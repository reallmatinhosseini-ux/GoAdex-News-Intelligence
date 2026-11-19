"""Live price recorder: samples Swissquote bid/ask for XAUUSD and EURUSD every 10 seconds and
keeps 3 days of ticks. This gives the T-2 stage real volatility/momentum up to the last minute,
and lets predictions be scored minutes after a release (no dependency on delayed history files).
"""
import logging
import math
import threading
import time
from datetime import datetime, timedelta, timezone

from . import db
from .providers import registry as R

log = logging.getLogger("recorder")
INTERVAL = 10
INSTS = ["XAUUSD", "EURUSD"]


def _loop(stop: threading.Event):
    n = 0
    while not stop.is_set():
        for inst in INSTS:
            try:
                q = R.live.quote(inst)
                with db.tx() as c:
                    c.execute("INSERT OR IGNORE INTO live_ticks(inst, ts, bid, ask) VALUES(?,?,?,?)",
                              (inst, int(q["ts"]), q["bid"], q["ask"]))
            except Exception as e:
                log.debug("quote %s failed: %s", inst, e)
        n += 1
        if n % 360 == 0:  # hourly prune
            with db.tx() as c:
                c.execute("DELETE FROM live_ticks WHERE ts < ?", (int(time.time()) - 3 * 86400,))
        stop.wait(INTERVAL)


def start(stop: threading.Event):
    t = threading.Thread(target=_loop, args=(stop,), name="recorder", daemon=True)
    t.start()
    return t


def bars(inst: str, start: datetime, end: datetime) -> list[tuple]:
    """1-minute bars (ts, o, h, l, c) built from recorded bid ticks."""
    rows = db.rows("SELECT ts, bid FROM live_ticks WHERE inst=? AND ts>=? AND ts<=? ORDER BY ts",
                   (inst, int(start.timestamp()), int(end.timestamp())))
    out, cur = [], None
    for r in rows:
        m = r["ts"] // 60 * 60
        if cur is None or cur[0] != m:
            if cur:
                out.append(tuple(cur))
            cur = [m, r["bid"], r["bid"], r["bid"], r["bid"]]
        else:
            cur[2] = max(cur[2], r["bid"]); cur[3] = min(cur[3], r["bid"]); cur[4] = r["bid"]
    if cur:
        out.append(tuple(cur))
    return out


def coverage(inst: str, start: datetime, end: datetime) -> float:
    mins = max(1, int((end - start).total_seconds() // 60))
    return len(bars(inst, start, end)) / mins


def _history_bars(inst: str, start: datetime, end: datetime) -> list[tuple]:
    """Cached Dukascopy 1-minute bars (DB only, no network) to fill minutes before recording began."""
    out = []
    d = start.date()
    while d <= end.date():
        key = d.isoformat() if inst == "XAUUSD" else f"{inst}:{d.isoformat()}"
        r = db.row("SELECT data FROM gold_bars WHERE day=?", (key,))
        if r:
            out += [tuple(b) for b in db.jload(r["data"], []) if start.timestamp() <= b[0] <= end.timestamp()]
        d += timedelta(days=1)
    return out


def context(inst: str, now: datetime | None = None, hours: float = 3) -> dict | None:
    """Pre-release volatility (bp/min) and momentum up to the last recorded minute.
    Minutes before live recording began are filled from real Dukascopy 1-minute history (same BID basis)."""
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(hours=hours)
    live = bars(inst, start, now)
    if not live:
        return None
    have = {b[0] for b in live}
    first_live = live[0][0]
    hist = [b for b in _history_bars(inst, start, now) if b[0] < first_live and b[0] not in have]
    b = sorted(hist + live)
    if len(b) < 60:
        return None
    closes = [x[4] for x in b]
    rets = [math.log(c2 / c1) for c1, c2 in zip(closes, closes[1:]) if c1 > 0]
    return {"vol_bp": round((sum(r * r for r in rets) / len(rets)) ** 0.5 * 1e4, 4),
            "mom_bp": round(math.log(closes[-1] / closes[0]) * 1e4, 2),
            "data_until": db.iso(datetime.fromtimestamp(live[-1][0] + 60, timezone.utc)),
            "minutes": len(b), "live_minutes": len(live), "history_minutes": len(hist),
            "partial": len(b) < hours * 60 * 0.5,
            "source": "Swissquote live ticks (every 10 s)" + (" + Dukascopy 1-minute history" if hist else "")}
