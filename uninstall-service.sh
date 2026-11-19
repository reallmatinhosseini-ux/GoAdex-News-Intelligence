#!/usr/bin/env bash
# Stop and remove the GoAdex background service and the Cloudflare tunnel service (if installed).
for L in com.goadex.newsintel com.goadex.tunnel; do
  PLIST="$HOME/Library/LaunchAgents/$L.plist"
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
done
echo "Removed the GoAdex background service and tunnel. (The DNS record and tunnel stay in your Cloudflare account.)"
