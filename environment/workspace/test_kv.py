#!/usr/bin/env python3
"""Visible smoke test for the starter store.

This only covers single-process command handling. It does not test durability,
recovery, or compaction; those are part of the hidden verification.
"""
import os
import queue
import subprocess
import sys
import tempfile
import threading


def run(commands):
    data_dir = tempfile.mkdtemp(prefix="kv-visible-")
    proc = subprocess.Popen(
        [sys.executable, "-u", "kv.py", "--data-dir", data_dir],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    lines = queue.Queue()

    def reader():
        for line in proc.stdout:
            lines.put(line.rstrip("\n"))
        lines.put(None)

    threading.Thread(target=reader, daemon=True).start()
    out = []
    for command in commands:
        proc.stdin.write(command + "\n")
        proc.stdin.flush()
        if command == "SCAN":
            while True:
                line = lines.get(timeout=10)
                if line == "END":
                    break
                out.append(line)
        else:
            out.append(lines.get(timeout=10))
    proc.stdin.write("EXIT\n")
    proc.stdin.flush()
    proc.wait(timeout=10)
    return out


def main():
    out = run(
        [
            "PUT a 1",
            "GET a",
            "PUT a 2",
            "GET a",
            "PUT spaces hello world",
            "GET spaces",
            "DELETE a",
            "GET a",
            "PUT b 3",
            "PUT c 4",
            "SCAN",
        ]
    )
    expected = [
        "OK",
        "VALUE 1",
        "OK",
        "VALUE 2",
        "OK",
        "VALUE hello world",
        "OK",
        "NOT_FOUND",
        "OK",
        "OK",
        "b",
        "c",
        "spaces",
    ]
    if out != expected:
        print("FAIL")
        print("expected:", expected)
        print("got:     ", out)
        return 1
    print("PASS visible smoke test")
    return 0


if __name__ == "__main__":
    sys.exit(main())
