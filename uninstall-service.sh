#!/usr/bin/env bash
PLIST="$HOME/Library/LaunchAgents/com.goadex.newsintel.plist"
launchctl unload "$PLIST" 2>/dev/null || true
rm -f "$PLIST"
echo "Removed the GoAdex News Intelligence background service."
