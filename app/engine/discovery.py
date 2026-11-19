"""Calendar discovery: merges official schedules (+ Trading Economics when configured)
into the events table. Idempotent — event ids are stable."""
from datetime import datetime, timedelta, timezone

from .. import catalog, config, db
from ..providers import registry as R
from ..providers.base import ProviderError
from ..providers.official import et_to_utc
from . import importance


def _event_id(key: str | None, title: str, rel: datetime) -> str:
    base = key or "X_" + "".join(ch for ch in title.upper() if ch.isalnum())[:40]
    return f"{base}:{db.iso(rel)}"


def _upsert(c, *, key, title, country, rel: datetime, basis, source, url, extra_title=None,
            consensus=None, previous=None, actual=None):
    eid = _event_id(key, title if key in (None, "US_FED_SPEECH") else "", rel)
    now = db.now_utc()
    d = catalog.resolve(key) if key else {}
    imp, why = importance.classify(key, title)
    existing = c.execute("SELECT id, consensus, previous, actual FROM events WHERE id=?", (eid,)).fetchone()
    if existing:
        c.execute("UPDATE events SET title=?, impact=?, impact_reason=?, updated_at=?, "
                  "consensus=COALESCE(?, consensus), previous=COALESCE(?, previous), "
                  "actual=COALESCE(?, actual) WHERE id=?",
                  (extra_title or d.get("name") or title, imp, why, db.iso(now),
                   db.jdump(consensus) if consensus else None, db.jdump(previous) if previous else None,
                   db.jdump(actual) if actual else None, eid))
        return False
    late = int(rel - now < timedelta(minutes=config.REPORT_DEADLINE_MIN) and rel > now)
    c.execute("INSERT INTO events(id, catalog_key, title, country, category, release_utc, time_basis, impact, "
              "impact_reason, source, source_url, discovered_at, late_discovery, consensus, previous, actual, "
              "updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (eid, key, extra_title or d.get("name") or title, country, d.get("category"), db.iso(rel), basis,
               imp, why, source, url, db.iso(now), late, db.jdump(consensus) if consensus else None,
               db.jdump(previous) if previous else None, db.jdump(actual) if actual else None, db.iso(now)))
    return True


def sync() -> dict:
    """Pull every available schedule source. Returns a summary per source."""
    summary = {}
    horizon_lo = db.now_utc() - timedelta(days=45)
    horizon_hi = db.now_utc() + timedelta(days=75)

    # 1) Federal Reserve calendar (FOMC, press conferences, minutes, speeches, Beige Book)
    try:
        n = 0
        with db.tx() as c:
            for e in R.fed_calendar.events():
                if not (horizon_lo <= e["release_utc"] <= horizon_hi):
                    continue
                key = catalog.match_title(e["title"])
                if e["type"] == "Beige":
                    key = "US_BEIGE"
                if not key:
                    continue
                title = e["title"] if key == "US_FED_SPEECH" else None
                n += _upsert(c, key=key, title=e["title"], country="US", rel=e["release_utc"], basis="published",
                             source="Federal Reserve", url=e["url"],
                             extra_title=(f"{e['title']} — {e['description'][:80]}" if title else None))
        summary["fed"] = f"ok (+{n} new)"
    except ProviderError as ex:
        summary["fed"] = f"error: {ex}"

    # 2) BEA (GDP, Personal Income & Outlays / PCE)
    try:
        n = 0
        with db.tx() as c:
            for e in R.bea.events():
                if not (horizon_lo <= e["release_utc"] <= horizon_hi):
                    continue
                key = catalog.match_title(e["title"])
                if key not in ("US_GDP", "US_PCE"):
                    continue
                n += _upsert(c, key=key, title=e["title"], country="US", rel=e["release_utc"],
                             basis="published", source="BEA", url=e["url"],
                             extra_title=f"{catalog.resolve(key)['name']} — {e['title'].split(',')[-1].strip()}")
        summary["bea"] = f"ok (+{n} new)"
    except ProviderError as ex:
        summary["bea"] = f"error: {ex}"

    # 2b) Full calendar (all impacts) + consensus forecasts for catalogued events
    try:
        from ..providers.calendar_ff import FF_MAP, gold_relevant
        items = R.ff.week()
        grouped = {}
        now_s = db.iso(db.now_utc())
        with db.tx() as c:
            for e in items:
                m = FF_MAP.get(e["title"]) if e["country"] in ("USD", "EUR") else None
                if m and m[0].startswith("EU_") != (e["country"] == "EUR"):
                    m = None
                cid = f"FF:{e['country']}:{db.iso(e['release_utc'])}:{e['title']}"
                c.execute("INSERT OR REPLACE INTO calendar(id, title, country, release_utc, impact, forecast, previous, "
                          "actual, actual_source, gold_relevant, catalog_key, event_id, source, fetched_at) VALUES(?,?,?,?,?,?,?,"
                          "(SELECT actual FROM calendar WHERE id=?),(SELECT actual_source FROM calendar WHERE id=?),?,?,?,?,?)",
                          (cid, e["title"], e["country"], db.iso(e["release_utc"]), e["impact"], e["forecast"],
                           e["previous"], cid, cid, int(gold_relevant(e["title"], e["country"], e["impact"]) or bool(m)),
                           m[0] if m else None, f"{m[0]}:{db.iso(e['release_utc'])}" if m else None,
                           "Forex Factory", now_s))
                if m:
                    g = grouped.setdefault((m[0], e["release_utc"]), {"consensus": {}, "previous": {}, "title": e["title"]})
                    for fld, val in (("consensus", e["forecast"]), ("previous", e["previous"])):
                        if val:
                            g[fld][m[1]] = {"value": val, "source": "Forex Factory calendar", "url": R.ff.docs,
                                            "as_of": now_s, "category": "consensus_forecast" if fld == "consensus" else "official_release",
                                            "label": e["title"]}
                    if m[2]:
                        g["title"] = e["title"]
            n = 0
            for (key, rel), g in grouped.items():
                if not (horizon_lo <= rel <= horizon_hi):
                    continue
                n += _upsert(c, key=key, title=g["title"], country="EA" if key.startswith("EU_") else "US", rel=rel,
                             basis="published", source="Forex Factory calendar", url=R.ff.docs,
                             consensus=g["consensus"] or None, previous=g["previous"] or None)
        summary["calendar"] = f"ok ({len(items)} items, +{n} analysable events)"
    except ProviderError as ex:
        summary["calendar"] = f"error: {ex}"

    # 3) FRED release dates (BLS / Census / ADP / UMich schedules). Dates only -> agency standard time.
    if R.fred.configured():
        n, errs = 0, []
        for key, d in catalog.CATALOG.items():
            rid = d.get("fred_release_id")
            if not rid or key in ("US_GDP", "US_PCE"):
                continue
            try:
                dates = R.fred.release_dates(rid)
            except ProviderError as ex:
                errs.append(f"{key}: {ex}")
                continue
            with db.tx() as c:
                for ds in dates:
                    rel = et_to_utc(datetime.fromisoformat(ds), d["et_time"])
                    if horizon_lo <= rel <= horizon_hi:
                        n += _upsert(c, key=key, title=d["name"], country="US", rel=rel, basis="agency_standard",
                                     source=f"FRED release {rid}",
                                     url=f"https://fred.stlouisfed.org/releases/calendar?rid={rid}")
        summary["fred"] = f"ok (+{n} new)" + (f"; {len(errs)} errors" if errs else "")
    else:
        summary["fred"] = "not configured — BLS/Census release dates unavailable (set FRED_API_KEY)"

    # 4) Trading Economics: consensus/previous/actual + ECB/ISM/Conference Board
    if R.te.configured():
        try:
            n = 0
            with db.tx() as c:
                for e in R.te.calendar():
                    key = catalog.match_title(" " + e["title"])
                    if not key:
                        continue
                    ts = db.iso(db.now_utc())
                    mk = lambda v: {"headline": {"value": v, "source": "Trading Economics", "as_of": ts,
                                                 "url": e["url"], "category": "consensus_forecast"}} if v not in (None, "") else None
                    country = "EA" if (e["country"] or "").lower() == "euro area" else "US"
                    n += _upsert(c, key=key, title=e["title"], country=country, rel=e["release_utc"],
                                 basis="published", source="Trading Economics", url=e["url"],
                                 consensus=mk(e["consensus"]), previous=mk(e["previous"]),
                                 actual=mk(e["actual"]))
            summary["tradingeconomics"] = f"ok (+{n} new)"
        except ProviderError as ex:
            summary["tradingeconomics"] = f"error: {ex}"
    else:
        summary["tradingeconomics"] = "not configured — no consensus feed, no ECB/ISM schedule"

    # Collapse near-duplicate rows (same catalog key within 3h from different sources):
    # keep the 'published' time over 'agency_standard'.
    with db.tx() as c:
        rows = c.execute("SELECT id, catalog_key, release_utc, time_basis FROM events "
                         "WHERE catalog_key IS NOT NULL AND catalog_key!='US_FED_SPEECH' ORDER BY release_utc").fetchall()
        by_key = {}
        for r in rows:
            by_key.setdefault(r["catalog_key"], []).append(r)
        for lst in by_key.values():
            for a, b in zip(lst, lst[1:]):
                if abs((db.parse_iso(b["release_utc"]) - db.parse_iso(a["release_utc"])).total_seconds()) < 3 * 3600:
                    drop = a if a["time_basis"] == "agency_standard" else b
                    has_rep = c.execute("SELECT 1 FROM reports WHERE event_id=?", (drop["id"],)).fetchone()
                    if not has_rep:
                        c.execute("DELETE FROM events WHERE id=?", (drop["id"],))
    importance.reclassify_all()
    # The calendar's own impact rating can raise (never lower) an event's importance.
    with db.tx() as c:
        for lvl, ff in (("high", "High"), ("medium", "Medium")):
            c.execute("UPDATE events SET impact=?, impact_reason=COALESCE(impact_reason,'') || '; calendar rates it ' || ? "
                      "WHERE id IN (SELECT event_id FROM calendar WHERE impact=?) AND "
                      "(impact='low' OR (impact='medium' AND ?='high'))", (lvl, ff, ff, lvl))
    return summary
