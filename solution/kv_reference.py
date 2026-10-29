#!/usr/bin/env python3
"""Reference implementation for the crash-safe key-value store task.

Storage layout:
  snapshot.dat : magic + length + crc32 + JSON object of live keys
  wal.log      : sequence of [length:u32][crc32:u32][JSON payload]

A write is acknowledged only after the WAL record is fsynced. Recovery loads
the snapshot, replays complete WAL records, and truncates an incomplete or
corrupt tail before accepting new writes.
"""
import argparse
import json
import os
import struct
import sys
import zlib

WAL_NAME = "wal.log"
SNAP_NAME = "snapshot.dat"
SNAP_TMP = "snapshot.tmp"
SNAP_MAGIC = b"KVSNAP1\n"
MAX_KEY_BYTES = 256
MAX_VALUE_BYTES = 1024 * 1024
MAX_RECORD_BYTES = MAX_VALUE_BYTES + 4096
MAX_SNAPSHOT_BYTES = 512 * 1024 * 1024


def write_all(fd, data):
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def fsync_dir(path):
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def encode_record(op, key, value=None):
    obj = {"op": op, "key": key}
    if value is not None:
        obj["value"] = value
    payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    header = struct.pack(">II", len(payload), zlib.crc32(payload) & 0xFFFFFFFF)
    return header + payload


def apply_record(obj, store):
    if not isinstance(obj, dict):
        return
    key = obj.get("key")
    if not isinstance(key, str):
        return
    if obj.get("op") == "PUT":
        value = obj.get("value")
        if isinstance(value, str):
            store[key] = value
    elif obj.get("op") == "DELETE":
        store.pop(key, None)


def replay_wal(path, store):
    if not os.path.exists(path):
        return 0
    valid = 0
    with open(path, "rb") as fh:
        while True:
            header = fh.read(8)
            if len(header) != 8:
                break
            length, crc = struct.unpack(">II", header)
            if length > MAX_RECORD_BYTES:
                break
            payload = fh.read(length)
            if len(payload) != length:
                break
            if (zlib.crc32(payload) & 0xFFFFFFFF) != crc:
                break
            try:
                obj = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                break
            apply_record(obj, store)
            valid = fh.tell()
    return valid


def load_snapshot(path, store):
    try:
        with open(path, "rb") as fh:
            if fh.read(len(SNAP_MAGIC)) != SNAP_MAGIC:
                return
            header = fh.read(8)
            if len(header) != 8:
                return
            length, crc = struct.unpack(">II", header)
            if length > MAX_SNAPSHOT_BYTES:
                return
            payload = fh.read(length)
            if len(payload) != length:
                return
            if (zlib.crc32(payload) & 0xFFFFFFFF) != crc:
                return
            data = json.loads(payload.decode("utf-8"))
            if not isinstance(data, dict):
                return
            for key, value in data.items():
                if isinstance(key, str) and isinstance(value, str):
                    store[key] = value
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return


def write_snapshot(data_dir, store):
    payload = json.dumps(store, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    header = SNAP_MAGIC + struct.pack(">II", len(payload), zlib.crc32(payload) & 0xFFFFFFFF)
    tmp = os.path.join(data_dir, SNAP_TMP)
    final = os.path.join(data_dir, SNAP_NAME)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        write_all(fd, header + payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, final)
    fsync_dir(data_dir)


class Store:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.store = {}
        self.snapshot_path = os.path.join(data_dir, SNAP_NAME)
        self.wal_path = os.path.join(data_dir, WAL_NAME)

        leftover = os.path.join(data_dir, SNAP_TMP)
        if os.path.exists(leftover):
            try:
                os.unlink(leftover)
            except OSError:
                pass

        if os.path.exists(self.snapshot_path):
            load_snapshot(self.snapshot_path, self.store)

        valid = replay_wal(self.wal_path, self.store)
        self.wal_fd = os.open(self.wal_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        size = os.fstat(self.wal_fd).st_size
        if size != valid:
            os.ftruncate(self.wal_fd, valid)
            os.fsync(self.wal_fd)
        fsync_dir(data_dir)

    def put(self, key, value):
        write_all(self.wal_fd, encode_record("PUT", key, value))
        os.fsync(self.wal_fd)
        self.store[key] = value

    def delete(self, key):
        write_all(self.wal_fd, encode_record("DELETE", key))
        os.fsync(self.wal_fd)
        self.store.pop(key, None)

    def compact(self):
        write_snapshot(self.data_dir, self.store)
        os.ftruncate(self.wal_fd, 0)
        os.fsync(self.wal_fd)
        fsync_dir(self.data_dir)

    def close(self):
        try:
            os.close(self.wal_fd)
        except OSError:
            pass


def valid_key(token):
    if not token or any(ch.isspace() for ch in token):
        return False
    return len(token.encode("utf-8")) <= MAX_KEY_BYTES


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args()

    store = Store(args.data_dir)

    for raw in sys.stdin:
        line = raw.rstrip("\n").rstrip("\r")
        if not line.strip():
            continue
        parts = line.split(" ", 2)
        cmd = parts[0]

        if cmd == "PUT":
            if len(parts) < 2 or not valid_key(parts[1]):
                print("ERROR invalid key", flush=True)
                continue
            value = parts[2] if len(parts) == 3 else ""
            if len(value.encode("utf-8")) > MAX_VALUE_BYTES:
                print("ERROR value too large", flush=True)
                continue
            store.put(parts[1], value)
            print("OK", flush=True)

        elif cmd == "GET":
            if len(parts) != 2 or not valid_key(parts[1]):
                print("ERROR invalid key", flush=True)
                continue
            if parts[1] in store.store:
                print(f"VALUE {store.store[parts[1]]}", flush=True)
            else:
                print("NOT_FOUND", flush=True)

        elif cmd == "DELETE":
            if len(parts) != 2 or not valid_key(parts[1]):
                print("ERROR invalid key", flush=True)
                continue
            store.delete(parts[1])
            print("OK", flush=True)

        elif cmd == "SCAN":
            if len(parts) != 1:
                print("ERROR usage: SCAN", flush=True)
                continue
            for key in sorted(store.store):
                print(key, flush=True)
            print("END", flush=True)

        elif cmd == "COMPACT":
            if len(parts) != 1:
                print("ERROR usage: COMPACT", flush=True)
                continue
            store.compact()
            print("OK", flush=True)

        elif cmd == "EXIT":
            store.close()
            return 0

        else:
            print("ERROR unknown command", flush=True)

    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
