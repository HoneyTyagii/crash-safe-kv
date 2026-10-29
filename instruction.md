# Crash-safe key-value store

Implement a durable key-value store at `/app/kv.py`. The starter in `/app` is a
working in-memory store. It handles the command grammar and output format, but
it loses all data when the process exits. Replace the storage layer and keep the
CLI behaviour.

## Invocation

```text
python3 /app/kv.py --data-dir /path/to/data
```

The program reads one command per line from standard input and writes one
response per line to standard output. Flush after every response. The data
directory may not exist yet; create it. All persistent state must live under
that directory.

## Data model

- A key is a non-empty UTF-8 string with no whitespace and at most 256 bytes.
- A value is a UTF-8 string with no newline and at most 1 MiB. An empty value is
  valid.
- A `PUT` value may contain spaces. The parser takes everything after the second
  space as the value.

## Commands

| Command | Response |
| - | - |
| `PUT key [value]` | `OK` |
| `GET key` | `VALUE <value>` or `NOT_FOUND` |
| `DELETE key` | `OK` |
| `SCAN` | live keys in sorted order, one per line, then `END` |
| `COMPACT` | `OK` |
| `EXIT` | exit with status 0 |

`GET` on an empty value must print `VALUE ` with a trailing space, not
`NOT_FOUND`. `DELETE` prints `OK` whether or not the key existed. `SCAN` returns
keys in lexicographic order by Unicode code point, with no duplicates.

An unknown command or an invalid key must print `ERROR <message>` and continue
processing later commands. A missing `--data-dir` must exit with status 2.

## Durability

A `PUT` or `DELETE` may print `OK` only after the change is durable across a
process crash. The verifier starts the process, sends commands, waits for each
`OK`, then may kill it with `SIGKILL` at a fixed point. After a restart, every
acknowledged operation must still be present. A write that was sent but never
acknowledged may be present or absent, but it must never appear partially.

Recovery must tolerate a torn or corrupt record at the end of the on-disk log.
Ignore the incomplete tail, keep every complete record before it, repair the
log, and accept new writes. The process may be killed at any point, including
during a large write or during compaction.

## Compaction

`COMPACT` must rewrite the on-disk representation so the data directory does not
grow without bound. It may run only when the command is issued; do not compact
automatically. Compaction must be durable and crash-safe: a kill during
`COMPACT` must not lose acknowledged data.

The verifier checks that after 1000 overwrites of a single key, a `COMPACT`, 50
more keys, and another `COMPACT`, the total size of the data directory is at
most 16 KiB, and every live key is still readable after a restart.

## Environment

- Python 3.12, standard library only. No third-party packages and no network.
- The starter is `/app/kv.py`. The visible smoke test is `/app/test_kv.py`.
- Keep the CLI contract exact. The hidden verifier drives the program as a
  subprocess and compares stdout line by line. It does not inspect your source
  or grade style.
- All hidden cases must pass for a score of 1.0. A single failure scores 0.0.
