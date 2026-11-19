"""News backtesting orchestration and statistics.

1. Feature store: for every historical release, point-in-time features (as of T-61 min) and the
   measured gold reaction (Dukascopy 1-minute data) — engine/features.py.
2. Walk-forward: each release is predicted by models trained only on earlier releases and gated
   only by earlier out-of-sample results — engine/model.py.
Live predictions (saved before the release) are scored the same way and reported separately.
"""
import logging
import os
import threading
from datetime import timedelta
from statistics import mean, median

from .. import catalog, config, db
from . import features, history, model

log = logging.getLogger("backtest")
START = "2019-01-01"  # macro regime rules need ~15 months of prior data (BLS history starts 2017)
_lock = threading.Lock()


def run(max_events: int | None = None) -> dict:
    if not _lock.acquire(blocking=False):
        return {"processed": 0, "candidates": 0, "busy": True}
    try:
        if db.cache_get("bt_lock", 900):
            return {"processed": 0, "candidates": 0, "busy": True}
        db.cache_put("bt_lock", {"pid": os.getpid()})
        return _run(max_events)
    finally:
        with db.tx() as c:
            c.execute("DELETE FROM cache WHERE key='bt_lock'")
        _lock.release()


def _run(max_events):
    if not db.cache_get("hist_backfill_v2", 12 * 3600):
        db.cache_put("hist_backfill_v2", history.backfill())
    if not db.cache_get("hist_backfill", 12 * 3600):
        db.cache_put("hist_backfill", history.backfill())
    keys = features.TYPES
    evs = db.rows("SELECT * FROM hist_events WHERE catalog_key IN (%s) AND release_utc >= ? AND release_utc < ? "
                  "AND id NOT IN (SELECT event_id FROM features WHERE version=?) "
                  "AND id NOT IN (SELECT event_id FROM reactions WHERE data LIKE '%%missing%%') ORDER BY release_utc DESC"
                  % ",".join("?" * len(keys)),
                  (*keys, START, db.iso(db.now_utc() - timedelta(hours=2)), features.FEATURE_VERSION))
    soon = {r["catalog_key"] for r in db.rows("SELECT catalog_key FROM events WHERE release_utc BETWEEN ? AND ?",
                                              (db.iso(db.now_utc()), db.iso(db.now_utc() + timedelta(days=3))))}
    evs.sort(key=lambda e: (e["catalog_key"] not in soon, -db.parse_iso(e["release_utc"]).timestamp()))
    chunk = evs[:max_events] if max_events else evs  # upcoming types first, then newest first
    history.prefetch([e["release_utc"] for e in chunk])
    done = 0
    for ev in chunk:
        db.cache_put("bt_lock", {"pid": os.getpid()})
        try:
            done += bool(features.build_historical(ev))
        except Exception as e:  # one bad event must not stop the run
            log.warning("features %s failed: %s", ev["id"], e)
    summary = model.walk_forward_all()
    db.cache_put("backtest_status", {"at": db.iso(db.now_utc()), "processed": done,
                                     "pending": max(0, len(evs) - len(chunk)), "summary": summary})
    return {"processed": done, "candidates": len(evs)}


# ------------------------------------------------------------------ statistics
def _rate(rows, key):
    vals = [o[key] for o in rows if o.get(key) is not None]
    k, n = sum(vals), len(vals)
    return {"correct": k, "incorrect": n - k, "n": n, "accuracy": round(k / n, 3) if n else None,
            "lower_bound": round(model.wilson_lb(k, n), 3) if n else None}


def _agg(rows):
    preds = [db.jload(r["pred"]) for r in rows]
    outs = [db.jload(r["outcome"]) for r in rows]
    ready = [(p, o) for p, o in zip(preds, outs) if p.get("status") == "ready"]
    ro = [o for _, o in ready]
    two = [(p, o) for p, o in ready if p.get("two_way")]
    return {"events": len(rows), "forecasts": len(ready),
            "insufficient": sum(1 for p in preds if p.get("status") in ("insufficient", "not_ready")),
            "missed": sum(1 for p in preds if p.get("status") == "missed"),
            "final": {**_rate(ro, "final_correct"), "inconclusive": sum(1 for o in ro if "final" in o.get("inconclusive", []))},
            "initial": {**_rate(ro, "initial_correct"), "inconclusive": sum(1 for o in ro if "initial" in o.get("inconclusive", []))},
            "scenario": _rate(ro, "scenario_correct"),
            "two_way": {"predicted": len(two), "occurred": sum(1 for _, o in two if o.get("two_way_occurred") == 1),
                        "order_correct": sum(1 for _, o in two if o.get("order_correct") == 1),
                        "reversal_calls": _rate(ro, "reversal_correct")},
            "scenarios_predicted": {k: sum(1 for p, _ in ready if p.get("scenario") == k) for k in "ABCD"},
            "shadow": {t: _rate([o.get("shadow", {}) for o in outs], t) for t in ("final", "initial", "reversal")}
            | {f"h{h}": _rate([o.get("shadow", {}) for o in outs], f"h{h}") for h in model.HORIZONS}}


def stats(asset: str = "XAUUSD", stage: str = "t30", source: str | None = None, key: str | None = None) -> dict:
    kinds = [f"{src}_{stage}:{asset}" for src in (("live", "retro") if not source else (source,))]
    sql = ("SELECT * FROM predictions WHERE outcome IS NOT NULL AND engine_version=? AND kind IN (%s)"
           % ",".join("?" * len(kinds)))
    args = [config.ENGINE_VERSION, *kinds]
    if key:
        sql += " AND catalog_key=?"
        args.append(key)
    rows = db.rows(sql, args)
    summ = db.row("SELECT value FROM model_state WHERE key=?", (f"summary:{asset}:{stage}",))
    out = {"asset": asset, "stage": stage, "engine_version": config.ENGINE_VERSION, "overall": _agg(rows),
           "by_source": {s_: _agg([r for r in rows if r["kind"].startswith(s_)]) for s_ in ("live", "retro")},
           "by_event": {}, "by_horizon": {}, "status": db.cache_get_stale("backtest_status")[0],
           "model": db.jload(summ["value"], {}) if summ else {},
           "versions": db.rows("SELECT engine_version, COUNT(*) n FROM predictions WHERE kind IN (%s) GROUP BY 1"
                               % ",".join("?" * len(kinds)), kinds)}
    for k in sorted({r["catalog_key"] for r in rows}):
        out["by_event"][k] = {"name": catalog.resolve(k).get("name", k), **_agg([r for r in rows if r["catalog_key"] == k])}
    for h in model.HORIZONS:
        res = [db.jload(r["outcome"])["horizons"].get(str(h), {}).get("correct") for r in rows]
        res = [x for x in res if x is not None]
        out["by_horizon"][f"{h}m"] = {"n": len(res), "accuracy": round(mean(res), 3) if res else None,
                                      "lower_bound": round(model.wilson_lb(sum(res), len(res)), 3) if res else None}
    out["first"] = min((r["release_utc"] for r in rows), default=None)
    out["last"] = max((r["release_utc"] for r in rows), default=None)
    return out
