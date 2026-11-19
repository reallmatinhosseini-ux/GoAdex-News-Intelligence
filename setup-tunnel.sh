#!/bin/bash
# Publish the GoAdex app running on this Mac at https://app.goadex.com through Cloudflare Tunnel.
# Run once from Terminal:  ./setup-tunnel.sh
# Re-running is safe. Remove everything with ./uninstall-service.sh
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
HOST="${APP_HOST:-app.goadex.com}"
NAME="goadex"
CF="$HOME/.cloudflared"
PORT="${PORT:-8600}"

say() { printf "\n\033[1m▸ %s\033[0m\n" "$1"; }
die() { printf "\n\033[31m✗ %s\033[0m\n" "$1"; exit 1; }

case "$DIR" in
  *scratch-workspaces*)
    printf "\n\033[33m! This project is in a temporary session folder that is deleted with its Claude session.\n"
    printf "  Move it to a permanent folder before relying on it 24/7.\033[0m\n"
    read -r -p "  Continue anyway? [y/N] " a; [ "$a" = "y" ] || exit 1 ;;
esac

say "Checking admin accounts"
[ -f data/intel.db ] || die "No database yet. Start the app once with ./run.sh, then run this again."
ACTIVE=$(sqlite3 data/intel.db "SELECT email FROM admins WHERE disabled=0" 2>/dev/null || true)
echo "$ACTIVE" | grep -qx "dev-admin@goadex.local" && die "The test admin dev-admin@goadex.local is still enabled and its password is not private.
  1) Create your own:  python3 -m app.admin create you@example.com
  2) Disable the test: python3 -m app.admin disable dev-admin@goadex.local
  Then run ./setup-tunnel.sh again."
[ -n "$ACTIVE" ] || die "No admin account exists. Create one:  python3 -m app.admin create you@example.com"
echo "Admins: $(echo $ACTIVE | tr '\n' ' ')"

say "Installing cloudflared (Cloudflare's official tunnel client)"
command -v cloudflared >/dev/null || brew install cloudflared
cloudflared --version

say "Signing in to Cloudflare (a browser window opens — choose goadex.com and authorise)"
[ -f "$CF/cert.pem" ] || cloudflared tunnel login

say "Creating the tunnel '$NAME'"
cloudflared tunnel info "$NAME" >/dev/null 2>&1 || cloudflared tunnel create "$NAME"
TID=$(cloudflared tunnel list -o json | python3 -c "import sys,json; print(next(t['id'] for t in json.load(sys.stdin) if t['name']=='$NAME'))")

say "Pointing $HOST at the tunnel"
cloudflared tunnel route dns "$NAME" "$HOST" || echo "(DNS record already exists — fine)"

cat > "$CF/$NAME.yml" <<YML
tunnel: $TID
credentials-file: $CF/$TID.json
ingress:
  - hostname: $HOST
    service: http://127.0.0.1:$PORT
  - service: http_status:404
YML
echo "Config: $CF/$NAME.yml"

say "Starting the app and the tunnel automatically at login"
./install-service.sh
PLIST="$HOME/Library/LaunchAgents/com.goadex.tunnel.plist"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.goadex.tunnel</string>
  <key>ProgramArguments</key><array><string>$(command -v cloudflared)</string><string>tunnel</string><string>--config</string><string>$CF/$NAME.yml</string><string>run</string><string>$NAME</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/data/tunnel.log</string>
  <key>StandardErrorPath</key><string>$DIR/data/tunnel.log</string>
</dict></plist>
PL
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

say "Checking https://$HOST"
for i in $(seq 1 20); do
  code=$(curl -s -o /dev/null -w "%{http_code}" -m 10 "https://$HOST/api/public/spot" || true)
  [ "$code" = "200" ] && { printf "\n\033[32m✓ Live: https://%s/login\033[0m\n" "$HOST"; exit 0; }
  sleep 5
done
echo "Not reachable yet (DNS can take a few minutes). Check: tail -f data/tunnel.log"
