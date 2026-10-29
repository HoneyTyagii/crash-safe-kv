import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time

PROGRAM = os.environ.get("KV_PROGRAM", "/app/kv.py")
ENCODING = "utf-8"
TIMEOUT = 60

failures = []


class Session:
    def __init__(self, data_dir):
        self.proc = subprocess.Popen(
            [sys.executable, "-u", PROGRAM, "--data-dir", data_dir],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding=ENCODING,
            errors="replace",
            bufsize=1,
        )
        self.lines = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            for line in self.proc.stdout:
                self.lines.put(line.rstrip("\n"))
        except Exception:
            pass
        finally:
            self.lines.put(None)

    def read(self, timeout=TIMEOUT):
        try:
            item = self.lines.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(f"no response within {timeout}s")
        if item is None:
            code = self.proc.poll()
            try:
                err = self.proc.stderr.read()
            except Exception:
                err = ""
            raise RuntimeError(f"process ended with code {code}: {err[-500:]}")
        return item

    def send(self, line, timeout=TIMEOUT):
        if self.proc.poll() is not None:
            raise RuntimeError(f"process already exited with code {self.proc.poll()}")
        try:
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise RuntimeError(f"could not write command: {exc}")
        return self.read(timeout)

    def close(self):
        try:
            if self.proc.poll() is None:
                self.proc.stdin.write("EXIT\n")
                self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self._reap()

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(timeout=10)
        self._reap()

    def _reap(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.stdout.close()
        except Exception:
            pass
        try:
            self.proc.stderr.close()
        except Exception:
            pass


def expect(session, command, expected):
    got = session.send(command)
    if got != expected:
        raise AssertionError(f"{command!r} returned {got!r}, expected {expected!r}")


def scan(session):
    line = session.send("SCAN")
    keys = []
    while line != "END":
        keys.append(line)
        line = session.read()
        if len(keys) > 20000:
            raise AssertionError("SCAN did not terminate")
    return keys


def data_files(path):
    found = []
    for name in os.listdir(path):
        full = os.path.join(path, name)
        if os.path.isfile(full) and os.path.getsize(full) > 0:
            found.append(full)
    return found


def case_basic(tmp):
    s = Session(tmp)
    try:
        expect(s, "PUT a 1", "OK")
        expect(s, "GET a", "VALUE 1")
        expect(s, "PUT a 2", "OK")
        expect(s, "GET a", "VALUE 2")
        expect(s, "DELETE a", "OK")
        expect(s, "GET a", "NOT_FOUND")
        expect(s, "PUT b 3", "OK")
        expect(s, "PUT c 4", "OK")
        expect(s, "PUT d 5", "OK")
        expect(s, "DELETE c", "OK")
        if scan(s) != ["b", "d"]:
            raise AssertionError("SCAN returned wrong keys")
        expect(s, "PUT empty", "OK")
        expect(s, "GET empty", "VALUE ")
        expect(s, "PUT spaces hello world", "OK")
        expect(s, "GET spaces", "VALUE hello world")
        if not s.send("BOGUS").startswith("ERROR"):
            raise AssertionError("unknown command did not return ERROR")
        expect(s, "PUT after ok", "OK")
    finally:
        s.close()


def case_restart(tmp):
    s = Session(tmp)
    try:
        expect(s, "PUT persistent value", "OK")
        expect(s, "PUT second other", "OK")
        expect(s, "DELETE second", "OK")
    finally:
        s.close()

    s = Session(tmp)
    try:
        expect(s, "GET persistent", "VALUE value")
        expect(s, "GET second", "NOT_FOUND")
        if scan(s) != ["persistent"]:
            raise AssertionError("SCAN after restart returned wrong keys")
    finally:
        s.close()


def case_crash(tmp):
    s = Session(tmp)
    acknowledged = []
    try:
        for i in range(30):
            key = f"k{i:02d}"
            value = f"v{i}"
            expect(s, f"PUT {key} {value}", "OK")
            acknowledged.append((key, value))
    finally:
        s.kill()

    s = Session(tmp)
    try:
        for key, value in acknowledged:
            expect(s, f"GET {key}", f"VALUE {value}")
        expect(s, "PUT after-crash survived", "OK")
    finally:
        s.close()

    s = Session(tmp)
    try:
        expect(s, "GET after-crash", "VALUE survived")
    finally:
        s.close()


def case_crash_during_large_write(tmp):
    s = Session(tmp)
    big = "x" * (1024 * 1024)
    try:
        expect(s, "PUT base 1", "OK")

        def writer():
            try:
                s.proc.stdin.write("PUT big " + big + "\n")
                s.proc.stdin.flush()
            except Exception:
                pass

        thread = threading.Thread(target=writer)
        thread.start()
        time.sleep(0.01)
        s.kill()
        thread.join(timeout=5)
    finally:
        if s.proc.poll() is None:
            s.kill()

    s = Session(tmp)
    try:
        expect(s, "GET base", "VALUE 1")
        got = s.send("GET big")
        if got != "NOT_FOUND" and got != "VALUE " + big:
            raise AssertionError("GET big returned a partial value")
        expect(s, "PUT after-big survived", "OK")
    finally:
        s.close()

    s = Session(tmp)
    try:
        expect(s, "GET after-big", "VALUE survived")
    finally:
        s.close()


def case_torn_tail(tmp):
    s = Session(tmp)
    try:
        for i in range(10):
            expect(s, f"PUT k{i} v{i}", "OK")
    finally:
        s.close()

    files = data_files(tmp)
    if not files:
        raise AssertionError("no on-disk data after acknowledged writes")
    largest = max(files, key=os.path.getsize)
    with open(largest, "ab") as fh:
        fh.write(b"\x00\x00\x00\x00")

    s = Session(tmp)
    try:
        for i in range(10):
            expect(s, f"GET k{i}", f"VALUE v{i}")
        expect(s, "PUT after-torn survived", "OK")
    finally:
        s.close()

    s = Session(tmp)
    try:
        expect(s, "GET after-torn", "VALUE survived")
    finally:
        s.close()


def case_compaction(tmp):
    s = Session(tmp)
    try:
        for i in range(1000):
            expect(s, f"PUT hot value-{i}", "OK")
        expect(s, "COMPACT", "OK")
        expect(s, "GET hot", "VALUE value-999")
        for i in range(50):
            expect(s, f"PUT k{i:02d} v{i}", "OK")
        expect(s, "COMPACT", "OK")
    finally:
        s.close()

    total = sum(os.path.getsize(path) for path in data_files(tmp))
    if total > 16 * 1024:
        raise AssertionError(f"data directory is {total} bytes after compaction, expected <= 16384")

    s = Session(tmp)
    try:
        expect(s, "GET hot", "VALUE value-999")
        keys = scan(s)
        expected = ["hot"] + [f"k{i:02d}" for i in range(50)]
        if keys != expected:
            raise AssertionError(f"SCAN after compaction returned {len(keys)} keys, expected {len(expected)}")
    finally:
        s.close()


def case_scan_order(tmp):
    s = Session(tmp)
    try:
        for key in ["delta", "alpha", "charlie", "bravo", "alpha", "éclair"]:
            expect(s, f"PUT {key} {key}-value", "OK")
        expect(s, "DELETE bravo", "OK")
        expect(s, "PUT cafe café", "OK")
        expect(s, "GET cafe", "VALUE café")
        expected = ["alpha", "cafe", "charlie", "delta", "éclair"]
        if scan(s) != expected:
            raise AssertionError("SCAN did not return keys in sorted order")
    finally:
        s.close()

    s = Session(tmp)
    try:
        expected = ["alpha", "cafe", "charlie", "delta", "éclair"]
        if scan(s) != expected:
            raise AssertionError("SCAN order changed after restart")
        expect(s, "GET cafe", "VALUE café")
    finally:
        s.close()


def case_missing_dir(tmp):
    data_dir = os.path.join(tmp, "nested", "data")
    s = Session(data_dir)
    try:
        expect(s, "PUT a 1", "OK")
    finally:
        s.close()

    s = Session(data_dir)
    try:
        expect(s, "GET a", "VALUE 1")
    finally:
        s.close()


def case_usage(tmp):
    result = subprocess.run(
        [sys.executable, PROGRAM],
        capture_output=True,
        text=True,
        encoding=ENCODING,
        timeout=30,
    )
    if result.returncode != 2:
        raise AssertionError(f"missing --data-dir exited {result.returncode}, expected 2")


CASES = [
    ("basic", case_basic),
    ("restart", case_restart),
    ("crash_acknowledged", case_crash),
    ("crash_large_write", case_crash_during_large_write),
    ("torn_tail", case_torn_tail),
    ("compaction", case_compaction),
    ("scan_order", case_scan_order),
    ("missing_data_dir", case_missing_dir),
    ("usage_error", case_usage),
]


def main():
    if not os.path.isfile(PROGRAM):
        print(f"FAIL missing program: {PROGRAM}")
        return 1

    for name, func in CASES:
        tmp = tempfile.mkdtemp(prefix="kv-verify-")
        try:
            func(tmp)
            print(f"PASS {name}")
        except Exception as exc:
            print(f"FAIL {name}: {exc}")
            failures.append(name)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print(f"RESULT FAIL {len(failures)} of {len(CASES)} cases failed: {', '.join(failures)}")
        return 1
    print(f"RESULT PASS all {len(CASES)} cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
