"""Quantitative layer: look-ahead-safe event study + walk-forward conditional model.

Pipeline (per catalog event × asset):
  1. Historical release instants from ALFRED first-release dates (public_on) + agency time.
  2. Surprise proxy known at release: first-release value minus the mean of the prior
     three first-release values (consensus history needs a licensed feed; when one is
     added, replace `surprise` with actual − consensus).
  3. Asset reaction over EVAL windows from 1-minute prices around the release instant.
  4. Conditional model P(up | surprise sign), Laplace-smoothed, fitted ONLY on prior
     releases (walk-forward). Scored with Brier vs. the 50% climatology baseline and
     a reliability table. Probabilities are shown only when the gate passes.
"""
import time
from datetime import datetime, timedelta
from statistics import mean, median

from .. import catalog, config, db
from ..providers import registry as R
from ..providers.base import ProviderError
from ..providers.official import et_to_utc
from . import data

WINDOWS = config.EVAL_WINDOWS_MIN


def _history(key: str) -> tuple[list[dict], str]:
    d = catalog.resolve(key)
    sid, how = None, None
    for name, spec in (d.get("series") or {}).items():
        if spec[0] == "FRED":
            sid, how = spec[1], spec[2]
            break
    if not sid or not R.fred.configured():
        raise ProviderError("Event study needs FRED_API_KEY (ALFRED first-release vintages)")
    fr = R.fred.first_releases(sid, start="2015-01-01")
    out = []
    for i in range(4, len(fr)):
        cur, prev = fr[i], fr[i - 1]
        if how == "mom_pct":
            val = (cur["value"] / prev["value"] - 1) * 100
            hist = [(fr[j]["value"] / fr[j - 1]["value"] - 1) * 100 for j in range(i - 3, i)]
        elif how == "change_k":
            val = cur["value"] - prev["value"]
            hist = [fr[j]["value"] - fr[j - 1]["value"] for j in range(i - 3, i)]
        else:
            val = cur["value"]
            hist = [fr[j]["value"] for j in range(i - 3, i)]
        try:
            rel = et_to_utc(datetime.fromisoformat(cur["public_on"]), d.get("et_time", "08:30"))
        except Exception:
            continue
        out.append({"ref": cur["date"], "release_utc": db.iso(rel), "value": round(val, 4),
                    "surprise": round((val - mean(hist)) * d.get("surprise_sign", 1), 4)})
    return out, sid


def _reaction(asset: str, release_iso: str) -> dict | None:
    rel = db.parse_iso(release_iso)
    key = f"react:{asset}:{release_iso}"
    c = db.cache_get(key, 3650 * 86400)
    if c is not None:
        return c or None
    sym = catalog.ASSETS[asset]["td_symbol"]
    bars = R.prices.series(sym, "1min", start=rel - timedelta(minutes=5), end=rel + timedelta(minutes=max(WINDOWS) + 2),
                           outputsize=200, ttl=3650 * 86400)
    if not bars:
        db.cache_put(key, {})
        return None
    by_t = {b["t"][:16]: b for b in bars}
    t0 = (rel - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")
    if t0 not in by_t:
        db.cache_put(key, {})
        return None
    p0 = by_t[t0]["c"]
    out = {"p0": p0}
    for w in WINDOWS:
        tw = (rel + timedelta(minutes=w - 1)).strftime("%Y-%m-%d %H:%M")
        if tw in by_t:
            out[str(w)] = round((by_t[tw]["c"] / p0 - 1) * 1e4, 2)  # bps
    db.cache_put(key, out)
    return out


def build_event_study(key: str, asset: str, max_events=36, throttle_s=8.0) -> dict:
    """Expensive (one price call per release); run from the scheduler, result cached 7 days."""
    hist, sid = _history(key)
    hist = hist[-max_events:]
    rows = []
    for h in hist:
        if db.parse_iso(h["release_utc"]) > db.now_utc() - timedelta(minutes=70):
            continue
        cached = db.cache_get(f"react:{asset}:{h['release_utc']}", 3650 * 86400)
        try:
            r = _reaction(asset, h["release_utc"])
        except ProviderError as e:
            if "credits" in str(e).lower() or "limit" in str(e).lower():
                time.sleep(61)
                continue
            r = None
        if cached is None:
            time.sleep(throttle_s)  # free-tier friendly
        if r:
            rows.append({**h, "reaction_bps": r})
    res = {"key": key, "asset": asset, "series": sid, "n": len(rows), "rows": rows,
           "built_at": db.iso(db.now_utc()),
           "method": "Surprise proxy = first release minus mean of prior 3 first releases (ALFRED); "
                     "reaction = 1-min close vs. close at T-1min."}
    res["model"] = walk_forward(rows)
    db.cache_put(f"study:{key}:{asset}", res)
    return res


def walk_forward(rows: list[dict], window="15", min_train=12) -> dict:
    """Strict time-ordered evaluation. Each prediction uses only earlier releases."""
    rows = [r for r in rows if r["reaction_bps"].get(window) is not None and r["surprise"] != 0]
    preds = []
    for i in range(min_train, len(rows)):
        train, test = rows[:i], rows[i]
        sgn = 1 if test["surprise"] > 0 else -1
        same = [r for r in train if (r["surprise"] > 0) == (sgn > 0)]
        ups = sum(1 for r in same if r["reaction_bps"][window] > 0)
        p_up = (ups + 1) / (len(same) + 2)  # Laplace
        y = 1 if test["reaction_bps"][window] > 0 else 0
        preds.append({"release": test["release_utc"], "p_up": round(p_up, 3), "y": y})
    if not preds:
        return {"status": "insufficient_data", "n_oos": 0, "window_min": int(window),
                "note": f"Need > {min_train} historical releases with price reactions"}
    brier = mean((p["p_up"] - p["y"]) ** 2 for p in preds)
    base = mean((0.5 - p["y"]) ** 2 for p in preds)
    hits = [((p["p_up"] > 0.5) == (p["y"] == 1)) for p in preds if p["p_up"] != 0.5]
    bins = []
    for lo in (0.0, 0.4, 0.5, 0.6):
        hi = lo + (0.4 if lo == 0 else 0.1) if lo < 0.6 else 1.01
        b = [p for p in preds if lo <= p["p_up"] < hi]
        if b:
            bins.append({"range": f"{lo:.1f}-{min(hi,1):.1f}", "n": len(b),
                         "mean_pred": round(mean(p["p_up"] for p in b), 3),
                         "observed": round(mean(p["y"] for p in b), 3)})
    validated = len(preds) >= config.MIN_OOS_SAMPLES and brier < base
    return {"status": "validated" if validated else "experimental", "n_oos": len(preds),
            "window_min": int(window), "brier": round(brier, 4), "brier_baseline": round(base, 4),
            "hit_rate": round(mean(hits), 3) if hits else None, "calibration": bins,
            "gate": f"n_oos ≥ {config.MIN_OOS_SAMPLES} and Brier < baseline"}


def summarize(key: str, asset: str) -> dict:
    """Read the cached study and produce what the report needs (never triggers price calls)."""
    st = db.cache_get_stale(f"study:{key}:{asset}")[0]
    if not st:
        return {"status": "not_built", "note": "Event study not built yet (needs FRED + price keys; built by the scheduler)."}
    rows = st["rows"]
    out = {"status": st["model"]["status"], "n_events": st["n"], "built_at": st["built_at"],
           "method": st["method"], "model": st["model"]}
    for w in ("15", "60"):
        moves = [abs(r["reaction_bps"][w]) for r in rows if r["reaction_bps"].get(w) is not None]
        if moves:
            out[f"median_abs_move_{w}m_bps"] = round(median(moves), 1)
            out[f"p80_abs_move_{w}m_bps"] = round(sorted(moves)[int(len(moves) * 0.8) - 1], 1)
    pos = [r for r in rows if r["surprise"] > 0 and r["reaction_bps"].get("15") is not None]
    neg = [r for r in rows if r["surprise"] < 0 and r["reaction_bps"].get("15") is not None]
    if pos:
        out["after_stronger"] = {"n": len(pos), "share_up": round(mean(r["reaction_bps"]["15"] > 0 for r in pos), 2),
                                 "median_bps": round(median(r["reaction_bps"]["15"] for r in pos), 1)}
    if neg:
        out["after_weaker"] = {"n": len(neg), "share_up": round(mean(r["reaction_bps"]["15"] > 0 for r in neg), 2),
                               "median_bps": round(median(r["reaction_bps"]["15"] for r in neg), 1)}
    out["recent"] = rows[-8:]
    return out
