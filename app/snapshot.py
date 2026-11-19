"""Publish the public website figures for serverless hosting (Vercel).

The full engine (price recorder, scheduler, research database) runs on an always-on machine.
This writes the same aggregate, forecast-free data the public endpoints serve — recorded prices,
calendar, counts and validation status — to snapshot/public.json, which the serverless site reads.

    python3 -m app.snapshot            # write snapshot/public.json
    ./publish-site.sh                  # write it, commit and push (Vercel redeploys automatically)
"""
import json
import sys
from datetime import timedelta

from . import config, db


def build() -> dict:
    from .main import public_calendar, public_metrics, public_overview
    db.init()
    now = db.now_utc()
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    snap = {"generated_at": db.iso(now), "overview": public_overview(), "metrics": public_metrics(), "calendar": {}}
    for w in (0, 1):
        snap["calendar"][(monday + timedelta(days=7 * w)).strftime("%Y-%m-%d")] = public_calendar(w)
    return snap


def write() -> str:
    if config.SERVERLESS:
        sys.exit("Run this on the main machine, not on the serverless host.")
    snap = build()
    config.SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.SNAPSHOT_PATH.write_text(json.dumps(snap, separators=(",", ":"), default=str))
    return str(config.SNAPSHOT_PATH)


if __name__ == "__main__":
    path = write()
    print(f"Snapshot written: {path}")
