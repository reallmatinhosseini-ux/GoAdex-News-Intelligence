"""Research orchestration: builds versioned report snapshots for an event and records
why each revision changed. Reports are immutable once written."""
import traceback
from datetime import timedelta

from .. import catalog, config, db
from ..providers import registry as R
from ..providers.base import NotConfigured, ProviderError, fact
from . import quant, reasoning, regime as regime_mod

PRIMARY = ["XAUUSD", "EURUSD"]


def _latest_report(event_id: str) -> dict | None:
    r = db.row("SELECT * FROM reports WHERE event_id=? ORDER BY version DESC LIMIT 1", (event_id,))
    if r:
        r["body"] = db.jload(r["body"], {})
    return r


def _volatility(qs: dict, regime: dict, impact: str) -> dict:
    vix = regime.get("signals", {}).get("markets", {}).get("vix")
    med = qs.get("median_abs_move_15m_bps")
    if med is not None:
        lvl = "High" if med > 40 else "Elevated" if med > 20 else "Moderate"
        basis = f"Median absolute 15-min gold move across {qs.get('n_events')} past releases: {med}bp " \
                f"(80th pct {qs.get('p80_abs_move_15m_bps')}bp)."
    else:
        lvl = {"high": "Elevated", "medium": "Moderate", "low": "Low"}.get(impact, "Moderate")
        basis = "No event-study data yet — qualitative estimate from event tier."
    if vix and vix["value"] > 25:
        basis += f" VIX {vix['value']} indicates a stressed backdrop that tends to widen reactions."
    return {"level": lvl, "basis": basis}


def _data_quality(regime: dict, assess: dict, consensus) -> dict:
    gaps = list(regime.get("gaps", [])) + [g for a in assess.values() for g in a.get("gaps", [])]
    cov = min(a["coverage"] for a in assess.values()) if assess else 0
    grade = "Good" if cov > 0.7 and consensus else "Fair" if cov > 0.45 else "Limited"
    return {"grade": grade, "coverage": cov, "has_consensus": bool(consensus), "gaps": sorted(set(gaps))}


def _diff_reason(prev: dict | None, cur: dict) -> tuple[str | None, bool]:
    if not prev:
        return "Initial report", False
    pb = prev["body"].get("assets", {})
    changes, material = [], False
    for a in PRIMARY:
        p, c = pb.get(a, {}), cur["assets"].get(a, {})
        if not p or not c:
            continue
        if p.get("bias") != c.get("bias") or p.get("confidence") != c.get("confidence"):
            material = True
            pf = {f["name"]: f["signal"] for f in p.get("factors", [])}
            moved = [f"{f['name']} {pf.get(f['name'], 0):+.2f}→{f['signal']:+.2f}" for f in c.get("factors", [])
                     if abs(f["signal"] - pf.get(f["name"], 0)) >= 0.1]
            new = [f["name"] for f in c.get("factors", []) if f["name"] not in pf]
            changes.append(f"{a}: {p.get('bias')}/{p.get('confidence')} → {c.get('bias')}/{c.get('confidence')}"
                           + (f" — drivers: {', '.join(moved)}" if moved else "")
                           + (f"; new evidence: {', '.join(new)}" if new else ""))
        elif abs((p.get("score") or 0) - (c.get("score") or 0)) >= 0.1:
            changes.append(f"{a}: score {p.get('score'):+.2f}→{c.get('score'):+.2f} (conclusion unchanged)")
    if (prev["body"].get("consensus") or {}) != (cur.get("consensus") or {}):
        changes.append("Consensus inputs updated")
    return ("; ".join(changes) if changes else "Refreshed — no material change in evidence"), material


def build(event_id: str, stage: str) -> dict:
    ev = db.row("SELECT * FROM events WHERE id=?", (event_id,))
    if not ev:
        raise ValueError(f"unknown event {event_id}")
    key = ev["catalog_key"]
    d = catalog.resolve(key)
    now = db.now_utc()
    rel = db.parse_iso(ev["release_utc"])
    as_of = db.iso(now)
    reg = regime_mod.compute(force=stage in ("final", "refresh"))
    xm = reasoning.cross_market()
    consensus, previous = db.jload(ev["consensus"]), db.jload(ev["previous"])

    # Optional cited web research (consensus, analyst views, last-24h developments)
    web = None
    prev_rep = _latest_report(event_id)
    if R.claude.configured() and config.LLM_WEB_RESEARCH and stage in ("early", "refresh", "final"):
        try:
            web = R.claude.web_research(ev["title"], ev["release_utc"], as_of, d.get("components", []))
        except (ProviderError, NotConfigured) as e:
            web = {"error": str(e)}
    elif prev_rep:
        web = prev_rep["body"].get("web_research")
    if not consensus and web and web.get("consensus"):
        c0 = web["consensus"][0]
        consensus = {"headline": fact(c0.get("value"), f"{c0.get('source_name')} (AI-extracted, verify)",
                                      c0.get("source_url"), c0.get("published") or as_of, "consensus_ai_extracted",
                                      component=c0.get("component"))}

    assess = {a: reasoning.assess(key, a, reg, xm, consensus) for a in PRIMARY}
    gold = assess["XAUUSD"]
    lead_parts = [f for f in gold["factors"] if f["name"].startswith("Surprise risk")]
    scen = reasoning.scenarios(key, consensus, previous, gold["quant"],
                               {"text": lead_parts[0]["evidence"][:140] if lead_parts else None})
    tech = {a: reasoning.technical_levels(a) for a in PRIMARY}
    invalidation = []
    for a in PRIMARY:
        A = assess[a]
        if A["bias"].startswith("Bearish"):
            invalidation.append(f"{a}: a print on the dovish side of expectations with front-end yields falling "
                                f">5bp would invalidate the bearish bias")
        elif A["bias"].startswith("Bullish"):
            invalidation.append(f"{a}: a hawkish surprise with 2y yields rising >5bp would invalidate the bullish bias")
        if tech[a] and tech[a].get("available"):
            t = tech[a]
            invalidation.append(f"{a}: sustained trade beyond the 20-day range ({t['low_20d']}–{t['high_20d']}) "
                                f"against the bias signals the market is reading the release differently")
    body = {
        "event": {"id": ev["id"], "title": ev["title"], "country": ev["country"], "category": ev["category"],
                  "release_utc": ev["release_utc"], "time_basis": ev["time_basis"], "impact": ev["impact"],
                  "impact_reason": ev["impact_reason"], "components": d.get("components", []),
                  "source": ev["source"], "source_url": ev["source_url"]},
        "stage": stage, "as_of": as_of, "engine_version": config.ENGINE_VERSION,
        "minutes_to_release": round((rel - now).total_seconds() / 60),
        "late_discovery": bool(ev["late_discovery"]),
        "regime": reg, "cross_market": {k: v for k, v in xm.items() if k != "series"},
        "consensus": consensus, "previous": previous, "actual": db.jload(ev["actual"]),
        "assets": assess, "scenarios": scen, "technicals": tech,
        "volatility": _volatility(gold["quant"], reg, ev["impact"]),
        "invalidation": invalidation, "web_research": web,
        "data_quality": _data_quality(reg, assess, consensus),
        "lookahead_guard": "All inputs are observations published at or before as_of; vintages via ALFRED for history.",
    }
    body["summary"] = summarize(body)
    if stage == "post_release":
        body["post_release"] = post_release_analysis(ev, consensus, previous, gold["quant"])
    if R.claude.configured():
        try:
            body["narrative"] = R.claude.narrative(body)
        except (ProviderError, NotConfigured) as e:
            body["narrative"] = {"error": str(e)}
    reason, material = _diff_reason(prev_rep, body)
    version = (prev_rep["version"] + 1) if prev_rep else 1
    with db.tx() as c:
        c.execute("INSERT INTO reports(event_id, version, stage, created_at, engine_version, body, gold_bias, "
                  "eur_bias, confidence, revision_reason, material_change) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (event_id, version, stage, as_of, config.ENGINE_VERSION, db.jdump(body), gold["bias"],
                   assess["EURUSD"]["bias"], gold["confidence"], reason, int(material)))
        new_status = {"early": "researching", "refresh": "researching", "final": "report_ready",
                      "monitor": "report_ready", "post_release": "released"}[stage]
        c.execute("UPDATE events SET status=?, updated_at=? WHERE id=?", (new_status, as_of, event_id))
    return body


# ------------------------------------------------------------------ release handling
def _num(x):
    try:
        return float(str(x).replace("%", "").replace("K", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def fetch_actual(event_id: str) -> dict | None:
    """Retrieve the released figure from authorised sources only. Returns None if not yet out."""
    ev = db.row("SELECT * FROM events WHERE id=?", (event_id,))
    key = ev["catalog_key"]
    d = catalog.resolve(key)
    rel = db.parse_iso(ev["release_utc"])
    prev = db.jload(ev["previous"]) or {}
    out = {}
    if key in ("US_FOMC",):
        for it in R.fed_press.items("monetary"):
            if "FOMC statement" in it["title"] or "Federal Reserve issues FOMC statement" in it["title"]:
                if it["published_at"] and abs((db.parse_iso(it["published_at"]) - rel).total_seconds()) < 6 * 3600:
                    import re
                    txt = R.fed_press.statement_text(it["url"])
                    m = re.search(r"target range for the federal funds rate (?:at|to) (\d+(?:-\d/\d)?(?:\.\d+)?) to "
                                  r"(\d+(?:-\d/\d)?(?:\.\d+)?) percent", txt)
                    out["headline"] = fact(f"{m.group(1)}–{m.group(2)}%" if m else "see statement", "Federal Reserve",
                                           it["url"], it["published_at"], "official_release")
                    out["statement_excerpt"] = txt[txt.find("Recent indicators"):][:1200] if "Recent indicators" in txt else txt[:1200]
                    dissent = re.search(r"Voting against[^.]*\.", txt)
                    out["dissents"] = dissent.group(0) if dissent else "None reported"
                    break
    elif d.get("bls"):
        ids = list(d["bls"].values())
        try:
            data_ = R.bls.series_fresh(ids, years=2)
        except ProviderError:
            data_ = {}
        for comp, sid in d["bls"].items():
            obs = data_.get(sid, [])
            if len(obs) >= 2:
                last = obs[-1]
                # Only accept if this reference period is newer than what we had before release
                if prev.get(comp, {}).get("ref") and last["date"] <= prev[comp]["ref"]:
                    continue
                spec = d["series"].get(comp, ("", "", "level"))[2]
                from .data import transform
                val = transform(obs, spec)[-1]["value"] if spec != "level" else last["value"]
                out[comp] = fact(round(val, 3), f"BLS {sid}", f"https://data.bls.gov/timeseries/{sid}",
                                 db.iso(db.now_utc()), "official_release", ref=last["date"])
    elif d.get("series") and R.fred.configured():
        for comp, (src, sid, how) in d["series"].items():
            try:
                obs = R.fred.observations(sid, start="2023-01-01", ttl=0)
            except ProviderError:
                continue
            from .data import transform
            t = transform(obs, how)
            if t and (not prev.get(comp, {}).get("ref") or t[-1]["date"] > prev[comp]["ref"]):
                out[comp] = fact(t[-1]["value"], f"FRED {sid}", f"https://fred.stlouisfed.org/series/{sid}",
                                 db.iso(db.now_utc()), "official_release", ref=t[-1]["date"])
    if R.te.configured() and not out:
        for e in R.te.calendar():
            if abs((e["release_utc"] - rel).total_seconds()) < 3600 and catalog.match_title(" " + e["title"]) == key \
                    and e.get("actual") not in (None, ""):
                out["headline"] = fact(e["actual"], "Trading Economics", e["url"], db.iso(db.now_utc()),
                                       "official_release", revised_previous=e.get("revised"))
    if not out:
        return None
    with db.tx() as c:
        c.execute("UPDATE events SET actual=?, status='released', updated_at=? WHERE id=?",
                  (db.jdump(out), db.iso(db.now_utc()), event_id))
    cons = db.jload(ev["consensus"]) or {}
    a, cv = _num(out.get("headline", {}).get("value")), _num(cons.get("headline", {}).get("value"))
    msg = f"Actual {out.get('headline', {}).get('value')}" + (f" vs consensus {cv}" if cv is not None else "")
    from .. import notify
    notify.desktop(f"Released · {ev['title'].split(' — ')[0]}", msg)
    return out


def snapshot_previous(event_id: str):
    """Before release, record the latest known values (with reference period) as 'previous'."""
    ev = db.row("SELECT * FROM events WHERE id=?", (event_id,))
    d = catalog.resolve(ev["catalog_key"])
    prev = db.jload(ev["previous"]) or {}
    from . import data as data_mod
    for comp, (src, sid, how) in (d.get("series") or {}).items():
        if comp in prev and prev[comp].get("ref"):
            continue
        try:
            t, prov = data_mod.latest(sid, how)
            prev[comp] = data_mod.provenance_fact(t[-1]["value"], prov, t[-1]["date"], ref=t[-1]["date"])
        except ProviderError:
            continue
    if prev:
        with db.tx() as c:
            c.execute("UPDATE events SET previous=? WHERE id=?", (db.jdump(prev), event_id))


def post_release_analysis(ev: dict, consensus, previous, qs: dict) -> dict:
    actual = db.jload(ev["actual"]) or {}
    a = _num(actual.get("headline", {}).get("value"))
    c = _num((consensus or {}).get("headline", {}).get("value"))
    p = _num((previous or {}).get("headline", {}).get("value"))
    d = catalog.resolve(ev["catalog_key"])
    out = {"actual": actual, "vs_consensus": None, "vs_previous": None}
    if a is not None and c is not None:
        out["vs_consensus"] = round(a - c, 4)
    if a is not None and p is not None:
        out["vs_previous"] = round(a - p, 4)
    surprise = out["vs_consensus"] if out["vs_consensus"] is not None else out["vs_previous"]
    if surprise is not None:
        stronger = surprise * d.get("surprise_sign", 1) > 0
        side = qs.get("after_stronger" if stronger else "after_weaker")
        out["surprise_direction"] = "stronger/hotter" if stronger else "weaker/cooler"
        out["surprise_basis"] = "consensus" if out["vs_consensus"] is not None else "previous (no consensus)"
        out["textbook_gold"] = "down" if (stronger and d.get("country") == "US") else "up"
        if side:
            out["historical_conditional"] = side
            out["model_status"] = qs.get("status")
    return out


def _plain(f: dict) -> str:
    """One plain-English clause per evidence factor (no jargon, no scores)."""
    n, sig, ev = f["name"], f["signal"], f["evidence"]
    if n == "Real-yield trend":
        return "real yields have been rising, which weighs on gold" if sig < 0 else "real yields have been falling, which supports gold"
    if n == "Dollar trend":
        return "the dollar has been strengthening" if sig < 0 else "the dollar has been weakening"
    if n.startswith("Surprise risk"):
        hot = "stronger/hotter" in ev
        import re
        names = [re.sub(r"\s*\(.*?\)|\s+m/m", "", p.split(" +")[0].split(" -")[0]).strip()
                 for p in ev.split(":", 1)[-1].split(";")][:2]
        lead = f" ({', '.join(x for x in names if x)})" if names else ""
        return (f"related data released beforehand{lead} has been running hot, so a strong print is more likely"
                if hot else f"related data released beforehand{lead} has been soft, so a weak print is more likely")
    if n == "What is priced in":
        return "markets look under-prepared for a hawkish outcome" if sig < 0 else "markets look under-prepared for a dovish outcome"
    if n == "Speculative positioning":
        return "speculators are already heavily long gold, leaving room for a sell-off" if sig < 0 else "speculators hold few gold longs, leaving room to buy"
    if n == "Historical reaction pattern":
        return "gold has usually " + ("fallen" if sig < 0 else "risen") + " after similar releases"
    return n.lower()


def summarize(b: dict) -> str:
    """Short plain-language summary that always agrees with the final call."""
    g = b["assets"]["XAUUSD"]
    title = b["event"]["title"].split(" — ")[0]
    fs = {f["name"]: f for f in g["factors"]}
    p = b.get("prediction") or {}
    if p.get("status") in ("insufficient", "not_ready"):
        return f"No call for gold into {title}: {p.get('reason', 'not enough reliable evidence').rstrip('.')}."
    if p.get("status") == "ready":
        up = p["direction"] == "Bullish"
    elif g["bias"].startswith(("Bull", "Bear")):
        up = g["bias"].startswith("Bull")
    else:
        sc = g.get("score") or 0
        if abs(sc) >= 0.05:
            sup = [_plain(f) for f in sorted(g["factors"], key=lambda f: -abs(f["signal"] * f["weight"]))
                   if (f["signal"] > 0) == (sc > 0) and abs(f["signal"]) >= 0.1][:2]
            return (f"Gold leans slightly {'higher' if sc > 0 else 'lower'} into {title} (evidence score {sc:+.2f}, "
                    f"below the ±0.15 needed for a firm call)" + (" because " + " and ".join(sup) if sup else "") + ".")
        return f"No clear edge for gold into {title}; the evidence points both ways (score {sc:+.2f})."
    sup = [_plain(fs[n]) for n in g["supporting"][:2] if n in fs and (fs[n]["signal"] > 0) == up]
    opp = [_plain(fs[n]) for n in g["opposing"][:1] if n in fs]
    why = (" because " + " and ".join(sup)) if sup else ""
    risk = f" Main risk: {opp[0]}." if opp else ""
    return f"Gold leans {'higher' if up else 'lower'} into {title}{why}.{risk}"
