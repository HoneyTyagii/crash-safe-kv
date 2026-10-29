#!/usr/bin/env python3
"""Starter key-value store.

The command handling and output format are complete, but all data lives in a
Python dict. It is lost when the process exits. Your job is to replace the
storage layer with one that survives restarts and crashes.
"""
import argparse
import sys

MAX_KEY_BYTES = 256
MAX_VALUE_BYTES = 1024 * 1024


def valid_key(token):
    if not token or any(ch.isspace() for ch in token):
        return False
    return len(token.encode("utf-8")) <= MAX_KEY_BYTES


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args()
    del args

    store = {}

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
            store[parts[1]] = value
            print("OK", flush=True)

        elif cmd == "GET":
            if len(parts) != 2 or not valid_key(parts[1]):
                print("ERROR invalid key", flush=True)
                continue
            if parts[1] in store:
                print(f"VALUE {store[parts[1]]}", flush=True)
            else:
                print("NOT_FOUND", flush=True)

        elif cmd == "DELETE":
            if len(parts) != 2 or not valid_key(parts[1]):
                print("ERROR invalid key", flush=True)
                continue
            store.pop(parts[1], None)
            print("OK", flush=True)

        elif cmd == "SCAN":
            if len(parts) != 1:
                print("ERROR usage: SCAN", flush=True)
                continue
            for key in sorted(store):
                print(key, flush=True)
            print("END", flush=True)

        elif cmd == "COMPACT":
            if len(parts) != 1:
                print("ERROR usage: COMPACT", flush=True)
                continue
            print("OK", flush=True)

        elif cmd == "EXIT":
            return 0

        else:
            print("ERROR unknown command", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
