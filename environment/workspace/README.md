# Starter key-value store

`kv.py` is a working in-memory key-value store. It handles the command grammar,
output format, and error messages, but all data lives in a Python dict. The
process loses everything when it exits.

The task instruction has the full contract. This starter is deliberately not
durable. Replace the storage layer, keep the CLI behaviour, and make every
acknowledged `PUT` or `DELETE` survive a restart or a `SIGKILL`.

Run the visible smoke test from `/app`:

```bash
python3 test_kv.py
```

The smoke test only covers single-process behaviour. Durability, recovery, torn
tails, and compaction are checked separately.
