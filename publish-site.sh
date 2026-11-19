#!/bin/bash
# Refresh the public figures on the hosted site: rebuild snapshot/public.json from this machine's
# database, commit it and push. Vercel redeploys automatically from GitHub.
set -e
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a
python3 -m app.snapshot
git add snapshot/public.json
git diff --cached --quiet && { echo "Snapshot unchanged."; exit 0; }
git commit -q -m "Update public site snapshot"
git push -q
echo "Published. Vercel will redeploy in about a minute."
