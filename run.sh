#!/usr/bin/env bash
# Start the platform: API + web UI + background scheduler (one process).
set -e
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a
python3 -m pip install -q -r requirements.txt
# caffeinate -i: prevent idle sleep for as long as the server runs (stage jobs and the live price
# recorder stop if the Mac sleeps). Closing the lid on battery still sleeps the Mac.
if command -v caffeinate >/dev/null 2>&1; then
  exec caffeinate -i python3 -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8600}"
fi
exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8600}"
