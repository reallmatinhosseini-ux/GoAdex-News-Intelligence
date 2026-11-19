"""Point-in-time feature store for XAUUSD and EURUSD, for the two prediction stages.

Stage "t30": everything known at T-31 minutes (direction forecast published at T-30).
Stage "t2":  everything known at T-3 minutes  (final targets published at T-2).
Macro/fundamental factors are taken as of T-31 (they change daily, not intraday).
Outcomes are the measured reactions after the release and are only used to train on
*earlier* releases.
"""
import math
from datetime import datetime, timedelta, timezone

from .. import db, recorder
from ..providers import registry as R
from ..providers.base import ProviderError
from . import data, history, reasoning, regime

TYPES = ["US_FOMC", "US_CPI", "US_NFP", "US_PCE", "US_GDP", "US_PPI", "US_RETAIL", "US_FOMC_MINUTES"]
ASSETS = ["XAUUSD", "EURUSD"]
FACTOR_COLS = {
    "XAUUSD": {"Real-yield trend": "f_a", "Dollar trend": "f_usd", "Surprise risk from leading indicators": "f_lead",
               "What is priced in": "f_priced", "Speculative positioning": "f_pos"},
    "EURUSD": {"US–euro rate gap": "f_a", "Dollar trend": "f_usd", "Surprise risk from leading indicators": "f_lead",
               "What is priced in": "f_priced", "Speculative positioning": "f_pos"},
}
STAGES = {"t30": 31, "t2": 3}  # minutes before release at which the stage's data ends
VOL_HOURS = 3
FEATURE_VERSION = 3


def _ctx(bars: list, end_ts: float) -> dict:
    """Volatility (bp/min), momentum (bp) and last price from 1-minute bars in the VOL_HOURS before end_ts."""
    pre = [b[4] for b in bars if end_ts - VOL_HOURS * 3600 <= b[0] < end_ts]
    if len(pre) < 60:
        return {}
    rets = [math.log(b / a) for a, b in zip(pre, pre[1:]) if a > 0]
    return {"vol_bp": round((sum(r * r for r in rets) / len(rets)) ** 0.5 * 1e4, 4),
            "mom_bp": round(math.log(pre[-1] / pre[0]) * 1e4, 2), "price": pre[-1]}


def factor_vector(asset: str, assess: dict) -> dict:
    v = {c: 0.0 for c in FACTOR_COLS[asset].values()}
    for f in assess.get("factors", []):
        if f["name"] in FACTOR_COLS[asset]:
            v[FACTOR_COLS[asset][f["name"]]] = float(f["signal"])
    return v


def _slim(assess):
    return [{k: f[k] for k in ("name", "signal", "weight", "evidence", "sources")} for f in assess.get("factors", [])]


def build_historical(ev: dict) -> dict | None:
    row = db.row("SELECT x, y FROM features WHERE event_id=? AND version=?", (ev["id"], FEATURE_VERSION))
    if row:
        return {"x": db.jload(row["x"]), "y": db.jload(row["y"])}
    T = db.parse_iso(ev["release_utc"])
    x = {"type": ev["catalog_key"]}
    y = {}
    for asset in ASSETS:
        rx = history.reaction(ev["id"], ev["release_utc"], refresh=True, inst=asset)
        if not rx:
            continue
        bars = history.market_bars(asset, T - timedelta(hours=VOL_HOURS + 1), T)
        stages = {st: _ctx(bars, (T - timedelta(minutes=m)).timestamp()) for st, m in STAGES.items()}
        if not all(stages.values()):
            continue
        x[asset] = {"stages": stages}
        p0 = rx["p_pre"]
        ya = {k: rx.get(k) for k in ("p_pre", "p_t30", "p_t2", "hi30", "lo30", "hi60", "lo60")}
        ya["path"] = rx.get("path")
        for h in history.HORIZONS:
            if rx.get(f"m{h}") is not None:
                ya[f"bp{h}"] = round(rx[f"m{h}"] / p0 * 1e4, 3)
                ya[f"d{h}"] = rx[f"m{h}"]
        y[asset] = ya
    if "XAUUSD" not in y:
        return None
    tok = data.ASOF.set(T - timedelta(minutes=31))
    try:
        reg = regime.compute()
        for asset in [a for a in ASSETS if a in x]:
            a = reasoning.assess(ev["catalog_key"], asset, reg, {}, None)
            x[asset]["f"] = factor_vector(asset, a)
            x[asset]["factors"] = _slim(a)
    finally:
        data.ASOF.reset(tok)
    x["regime"] = reg.get("headline")
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO features(event_id, catalog_key, release_utc, version, x, y, built_at) "
                  "VALUES(?,?,?,?,?,?,?)", (ev["id"], ev["catalog_key"], ev["release_utc"], FEATURE_VERSION,
                                             db.jdump(x), db.jdump(y), db.iso(db.now_utc())))
    return {"x": x, "y": y}


def live_context(asset: str, now: datetime | None = None, max_age_min: float = 5) -> dict | None:
    """Volatility/momentum from the live recorder, only if its data ends within `max_age_min` of now.
    (The Dukascopy history feed lags 1-2 h, so it can never meet the stage freshness rule.)"""
    now = now or datetime.now(timezone.utc)
    c = recorder.context(asset, now, hours=VOL_HOURS)
    if c and (now - db.parse_iso(c["data_until"])).total_seconds() <= max_age_min * 60:
        return c
    return None
