#!/bin/bash
set -euo pipefail
cp "$(dirname "$0")/kv_reference.py" /app/kv.py
chmod +x /app/kv.py
