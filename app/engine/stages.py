"""Two-stage pre-release forecasts for XAUUSD and EURUSD, with a research log.

T-33 / T-7  research refresh (fundamentals, regime, consensus) — engine/report.py
T-30        published forecast (scenario A–D or Insufficient Evidence)
T-2         final update from the freshest data, with an explicit "what changed since T-30"
T+62        both stages scored (live recorder; Dukascopy / Twelve Data history as fallbacks)

Rules: a stage that cannot run before its deadline is recorded as "missed" and never published
late; a published stage is immutable (stored once, with a SHA-256 fingerprint); every input
carries its source and timestamp, and stale inputs are rejected and reported.
"""
import hashlib
import logging
import math
import time
from datetime import timedelta

from .. import catalog, config, db, recorder
from ..providers import registry as R
from . import features, history, model

log = logging.getLogger("stages")
STAGE_MIN = {"t30": 30, "t2": 2}
GRACE_S = {"t30": 300, "t2": 90}
FRESH_VOL_MIN = {"t30": 10, "t2": 3}
QUOTE_MAX_AGE_S = 60
REPORT_MAX_AGE_MIN = {"t30": 45, "t2": 15}


def _check(logbook, source, ok, detail, as_of=None):
    logbook.append({"source": source, "ok": bool(ok), "detail": detail, "as_of": as_of, "checked_at": db.iso(db.now_utc())})


def _quote(asset, logbook):
    """Live quote with verified freshness; falls back across sources and logs every attempt."""
    for i in range(4):
        try:
            q = R.live.quote(asset)
            age = time.time() - q["ts"]
            if age <= QUOTE_MAX_AGE_S:
                _check(logbook, f"Swissquote live {asset}", True, f"bid {q['bid']} / ask {q['ask']}, {age:.0f}s old", q["as_of"])
                return q
            _check(logbook, f"Swissquote live {asset}", False, f"quote is {age:.0f}s old (stale, limit {QUOTE_MAX_AGE_S}s)", q["as_of"])
        except Exception as e:
            _check(logbook, f"Swissquote live {asset}", False, f"attempt {i + 1}: {str(e)[:120]}")
        time.sleep(1.5)
    if asset == "XAUUSD":
        try:
            q = R.gold_spot.spot()
            ok = q.get("as_of") and (db.now_utc() - db.parse_iso(q["as_of"])).total_seconds() <= 120
            _check(logbook, "gold-api.com spot (fallback)", ok, f"{q['price']}", q.get("as_of"))
            if ok:
                return q
        except Exception as e:
            _check(logbook, "gold-api.com spot (fallback)", False, str(e)[:120])
    return None


def _latest_report(event_id):
    rep = db.row("SELECT body, created_at FROM reports WHERE event_id=? ORDER BY version DESC LIMIT 1", (event_id,))
    return (db.jload(rep["body"], {}), rep["created_at"]) if rep else ({}, None)


def _volatility(asset, stage, logbook):
    ctx = recorder.context(asset, hours=features.VOL_HOURS)
    if ctx:
        age = (db.now_utc() - db.parse_iso(ctx["data_until"])).total_seconds() / 60
        if age <= FRESH_VOL_MIN[stage]:
            _check(logbook, f"Volatility/momentum {asset}", True,
                   f"{ctx['minutes']} one-minute bars ({ctx['source']}), vol {ctx['vol_bp']:.2f} bp/min"
                   + (" — partial window" if ctx.get("partial") else ""), ctx["data_until"])
            return ctx
        _check(logbook, f"Volatility/momentum {asset}", False, f"latest bar {age:.0f} min old (limit {FRESH_VOL_MIN[stage]} min)", ctx["data_until"])
        return None
    have = len(recorder.bars(asset, db.now_utc() - timedelta(hours=3), db.now_utc()))
    _check(logbook, f"Volatility/momentum {asset}", False,
           f"only {have} live minutes recorded in the last 3 h and no cached history to fill the gap (need 60)")
    return None


def _history_check(key, logbook):
    n = db.row("SELECT COUNT(*) n FROM features WHERE version=? AND catalog_key=?", (features.FEATURE_VERSION, key))["n"]
    tot = db.row("SELECT COUNT(*) n FROM features WHERE version=?", (features.FEATURE_VERSION,))["n"]
    pending = db.row("SELECT COUNT(*) n FROM hist_events WHERE catalog_key=? AND id NOT IN (SELECT event_id FROM features WHERE version=?)",
                     (key, features.FEATURE_VERSION))["n"]
    duka = db.row("SELECT last_ok, last_error, last_error_at FROM provider_health WHERE provider LIKE 'Dukascopy%' "
                  "ORDER BY last_ok DESC LIMIT 1") or {}
    feed = "history feed healthy" if (duka.get("last_ok") or "") > (duka.get("last_error_at") or "") else \
        f"history feed failing: {(duka.get('last_error') or '')[:60]}"
    td = "Twelve Data fallback configured" if R.prices.configured() else "Twelve Data fallback not configured (TWELVEDATA_API_KEY)"
    _check(logbook, "Historical reactions", tot >= model.MIN_TRAIN,
           f"{tot} past releases measured ({n} of this type, {pending} of this type still loading); {feed}; {td}")


def _fundamentals(event_id, stage, logbook):
    body, created = _latest_report(event_id)
    if created:
        age = (db.now_utc() - db.parse_iso(created)).total_seconds() / 60
        if age > REPORT_MAX_AGE_MIN[stage]:
            _check(logbook, "Fundamental research", False, f"report {age:.0f} min old; refreshing now")
            try:
                from . import report
                report.build(event_id, "monitor")
                body, created = _latest_report(event_id)
            except Exception as e:
                _check(logbook, "Fundamental research refresh", False, str(e)[:150])
    reg = body.get("regime", {})
    if created:
        _check(logbook, "Fundamental research", True, f"report built {created}; regime: {reg.get('headline', '—')}", created)
        for gap in ((reg.get("gaps") or []) + (body.get("data_quality", {}).get("gaps") or []))[:5]:
            _check(logbook, "Missing input", False, gap)
        cons = body.get("consensus")
        _check(logbook, "Consensus forecast", bool(cons),
               ", ".join(f"{k}: {v.get('value')} ({v.get('source')})" for k, v in (cons or {}).items())
               or "no consensus published by the available sources")
    else:
        _check(logbook, "Fundamental research", False, "no report available")
    return body


def run(event_id: str, stage: str) -> dict:
    ev = db.row("SELECT * FROM events WHERE id=?", (event_id,))
    key = ev["catalog_key"]
    T = db.parse_iso(ev["release_utc"])
    due = T - timedelta(minutes=STAGE_MIN[stage])
    late = (db.now_utc() - due).total_seconds()
    if late > GRACE_S[stage] or db.now_utc() >= T:
        for asset in features.ASSETS:
            _store(event_id, stage, asset, key, ev["release_utc"], {
                "asset": asset, "stage": stage, "status": "missed", "model_version": config.ENGINE_VERSION,
                "reason": f"Missed: the system was asleep or offline at T-{STAGE_MIN[stage]} (job ran {int(late // 60)} min "
                          f"{int(late % 60)} s late). Nothing is published after the scheduled time."})
        return {"missed": True}
    logbook = []
    body = _fundamentals(event_id, stage, logbook)
    out = {}
    for asset in features.ASSETS:
        lb = list(logbook)
        q = _quote(asset, lb)
        ctx = _volatility(asset, stage, lb)
        _history_check(key, lb)
        a = (body.get("assets") or {}).get(asset)
        if key not in features.TYPES:
            f = {"status": "insufficient", "reason": f"No model for {catalog.resolve(key).get('name', key)} yet."}
        elif not a:
            f = {"status": "not_ready", "reason": "Fundamental analysis unavailable (see research log)."}
        elif not ctx:
            f = {"status": "not_ready", "reason": "Fresh pre-release volatility/momentum unavailable (see research log)."}
        else:
            x = {"type": key, **ctx, **features.factor_vector(asset, a)}
            f = model.live(asset, stage, x)
            f["features"] = {k: x.get(k) for k in ("vol_bp", "mom_bp", "f_a", "f_usd", "f_lead", "f_priced", "f_pos")}
        f.update(asset=asset, stage=stage, model_version=config.ENGINE_VERSION,
                 price=(q or {}).get("price"), price_source={k: (q or {}).get(k) for k in ("source", "as_of", "bid", "ask")},
                 research_log=lb, explanation=_explain(asset, a, f), consensus=db.jload(ev["consensus"]),
                 lean=_lean(a), data_until=(ctx or {}).get("data_until"))
        if stage == "t2":
            f["changes_since_t30"] = _changes(event_id, asset, f, a)
        _store(event_id, stage, asset, key, ev["release_utc"], f)
        out[asset] = f
    _notify(ev, stage, out)
    return {a: (f.get("scenario") or f.get("status")) for a, f in out.items()}


def _notify(ev, stage, out):
    from .. import notify
    def line(a, f):
        name = "Gold" if a == "XAUUSD" else "EUR/USD"
        if f.get("status") == "ready":
            return f"{name}: {'BUY' if f['final'] == 'UP' else 'SELL'} · {f['scenario_name']} ({f['confidence']})"
        return f"{name}: {'Insufficient Evidence' if f.get('status') == 'insufficient' else 'Not Ready'}"
    notify.desktop(f"{'T-30 forecast' if stage == 't30' else 'T-2 final update'} · {ev['title'].split(' — ')[0]}",
                   " | ".join(line(a, f) for a, f in out.items()))


def _lean(a):
    if not a:
        return None
    top = sorted(a.get("factors", []), key=lambda f: -abs(f["signal"] * f["weight"]))[:4]
    return {"score": a.get("score"), "bias": a.get("bias"),
            "reasons": [{"name": f["name"], "signal": f["signal"], "evidence": f["evidence"]} for f in top]}


def _explain(asset, a, f):
    """Plain-language fundamental explanation of the selected scenario (or of why none is selected)."""
    label = "Gold" if asset == "XAUUSD" else "EUR/USD"
    if not a:
        return f"No fundamental analysis is available for {label}."
    pos = [x for x in a.get("factors", []) if x["signal"] >= 0.1]
    neg = [x for x in a.get("factors", []) if x["signal"] <= -0.1]
    part = []
    if f.get("status") == "ready":
        part.append(f"Primary scenario: {f['scenario_name']} — first move {f['first']}, final {f['final']}.")
    if neg:
        part.append(f"Pointing {label} lower: " + "; ".join(x["evidence"].rstrip(".") for x in neg[:3]) + ".")
    if pos:
        part.append(f"Pointing {label} higher: " + "; ".join(x["evidence"].rstrip(".") for x in pos[:3]) + ".")
    s = f.get("analog_stats")
    if s:
        part.append(f"In the {s['n']} most similar past releases {label} moved up first {s['initial_up']} times and "
                    f"finished higher {s['final_up']} times; {s['reversals']} reversed after the first move.")
    return " ".join(part)


def _changes(event_id, asset, f2, a):
    r = db.row("SELECT pred FROM predictions WHERE event_id=? AND kind=?", (event_id, f"live_t30:{asset}"))
    if not r:
        return {"summary": "No T-30 forecast to compare with."}
    f1 = db.jload(r["pred"])
    ch = []
    if f1.get("status") != f2.get("status") or f1.get("scenario") != f2.get("scenario"):
        ch.append(f"Forecast changed from {f1.get('scenario_name') or f1.get('status')} to "
                  f"{f2.get('scenario_name') or f2.get('status')}.")
    p1, p2 = f1.get("probabilities") or {}, f2.get("probabilities") or {}
    for t in ("initial", "final", "reversal"):
        if t in p1 and t in p2 and abs(p1[t] - p2[t]) >= 0.03:
            ch.append(f"P({t}{' up' if t != 'reversal' else ''}) {p1[t] * 100:.0f}% → {p2[t] * 100:.0f}%.")
    x1, x2 = f1.get("features") or {}, f2.get("features") or {}
    if x1.get("mom_bp") is not None and x2.get("mom_bp") is not None and abs(x1["mom_bp"] - x2["mom_bp"]) >= 3:
        ch.append(f"Pre-release momentum {x1['mom_bp']:+.1f} → {x2['mom_bp']:+.1f} bp.")
    if x1.get("vol_bp") and x2.get("vol_bp") and abs(math.log(x2["vol_bp"] / x1["vol_bp"])) >= 0.15:
        ch.append(f"Volatility {x1['vol_bp']:.2f} → {x2['vol_bp']:.2f} bp/min.")
    l1, l2 = (f1.get("lean") or {}).get("score"), (a or {}).get("score")
    if l1 is not None and l2 is not None and abs(l1 - l2) >= 0.05:
        ch.append(f"Fundamental evidence score {l1:+.2f} → {l2:+.2f}.")
    if f1.get("price") and f2.get("price"):
        ch.append(f"Price moved {f2['price'] - f1['price']:+.{2 if asset == 'XAUUSD' else 5}f} since T-30.")
    return {"summary": " ".join(ch) if ch else "No material change since T-30.", "t30_published_at": f1.get("published_at")}


def _store(event_id, stage, asset, key, release, f):
    """Publish once. A stored stage is never overwritten (immutable record with a fingerprint)."""
    kind = f"live_{stage}:{asset}"
    if db.row("SELECT 1 FROM predictions WHERE event_id=? AND kind=?", (event_id, kind)):
        return False
    f["published_at"] = db.iso(db.now_utc())
    f["fingerprint"] = hashlib.sha256(db.jdump({k: v for k, v in f.items() if k != "fingerprint"}).encode()).hexdigest()
    with db.tx() as c:
        c.execute("INSERT INTO predictions(event_id, kind, catalog_key, release_utc, made_at, as_of, pred, outcome, released, "
                  "engine_version, direction, correct, target_hit) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (event_id, kind, key, release, f["published_at"], f.get("data_until") or f["published_at"], db.jdump(f),
                   None, None, config.ENGINE_VERSION, f.get("scenario") or f.get("status"), None, None))
    return True


def score(event_id: str) -> bool:
    rows = db.rows("SELECT * FROM predictions WHERE event_id=? AND kind LIKE 'live_%' AND outcome IS NULL "
                   "AND json_extract(pred, '$.status') != 'missed'", (event_id,))
    done = True
    for r in rows:
        asset = r["kind"].split(":")[1]
        rx = history.reaction(event_id, r["release_utc"], refresh=True, inst=asset)
        if not rx:
            done = False
            continue
        p0 = rx["p_pre"]
        y = {"p_pre": p0}
        for h in history.HORIZONS:
            if rx.get(f"m{h}") is not None:
                y[f"bp{h}"] = rx[f"m{h}"] / p0 * 1e4
        sc = model.score(asset, db.jload(r["pred"]), y)
        with db.tx() as c:  # the outcome is appended; the published forecast itself is never modified
            c.execute("UPDATE predictions SET outcome=?, released=?, correct=?, target_hit=? WHERE event_id=? AND kind=?",
                      (db.jdump(sc), db.jdump(history.released_values(r["catalog_key"], r["release_utc"])),
                       sc.get("final_correct"), sc.get("scenario_correct"), event_id, r["kind"]))
    if done:
        with db.tx() as c:
            c.execute("INSERT OR IGNORE INTO hist_events(id, catalog_key, title, release_utc, source, source_url) "
                      "SELECT id, catalog_key, title, release_utc, source, source_url FROM events WHERE id=?", (event_id,))
    return done
