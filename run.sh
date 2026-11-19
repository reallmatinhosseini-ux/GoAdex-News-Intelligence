#!/usr/bin/env bash
# Start the platform: API + web UI + background scheduler (one process).
set -e
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a

# Pick a Python >= 3.10. Login items (LaunchAgents) start with a minimal PATH, where `python3` is the
# macOS system 3.9 that cannot run this app — so search the usual install locations explicitly.
pick_python() {
  for p in "$PYTHON" python3.13 python3.12 python3.11 python3.10 \
           /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
           /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    [ -n "$p" ] || continue
    command -v "$p" >/dev/null 2>&1 || [ -x "$p" ] || continue
    "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null && { echo "$p"; return; }
  done
}
export PATH="/Library/Frameworks/Python.framework/Versions/Current/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
PY="$(pick_python)"
[ -n "$PY" ] || { echo "GoAdex needs Python 3.10 or newer (https://www.python.org/downloads/)." >&2; exit 1; }

# Install requirements only when something is missing (fast restarts, no network needed).
"$PY" -c 'import fastapi, uvicorn, httpx, numpy' 2>/dev/null || "$PY" -m pip install -q -r requirements.txt

# caffeinate -i: prevent idle sleep for as long as the server runs (stage jobs and the live price
# recorder stop if the Mac sleeps). Closing the lid on battery still sleeps the Mac.
if command -v caffeinate >/dev/null 2>&1; then
  exec caffeinate -i "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8600}"
fi
exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8600}"
