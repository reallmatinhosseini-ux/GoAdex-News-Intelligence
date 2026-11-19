"""Historical release instants and measured gold reactions (real Dukascopy 1-minute data)."""
import html
import math
import re
from datetime import datetime, timedelta, timezone
from statistics import pstdev

from .. import catalog, db
from ..providers import registry as R
from ..providers.base import ProviderError
from ..providers.official import et_to_utc

HORIZONS = [1, 5, 15, 30, 60]
BACKTEST_KEYS = ["US_FOMC", "US_CPI", "US_NFP", "US_PCE", "US_GDP", "US_PPI", "US_RETAIL", "US_FOMC_MINUTES"]


def _store(c, key, title, rel, source, url):
    c.execute("INSERT OR IGNORE INTO hist_events(id, catalog_key, title, release_utc, source, source_url) "
              "VALUES(?,?,?,?,?,?)", (f"{key}:{db.iso(rel)}", key, title, db.iso(rel), source, url))


def backfill() -> dict:
    """Collect every past release instant available from authorised schedules."""
    out = {}
    now = db.now_utc()
    try:
        n = 0
        with db.tx() as c:
            for e in R.fed_calendar.events():
                k = catalog.match_title(e["title"])
                if e["release_utc"] < now and k in ("US_FOMC", "US_FOMC_MINUTES"):
                    _store(c, k, "FOMC Rate Decision & Statement" if k == "US_FOMC" else "FOMC Minutes",
                           e["release_utc"], "Federal Reserve", e["url"])
                    n += 1
        out["fomc"] = n
    except ProviderError as e:
        out["fomc"] = f"error {e}"
    try:
        n = 0
        with db.tx() as c:
            for e in R.bea.events():
                k = catalog.match_title(e["title"])
                if k in ("US_PCE", "US_GDP") and e["release_utc"] < now:
                    _store(c, k, catalog.resolve(k)["name"] + " — " + e["title"].split(",")[-1].strip(),
                           e["release_utc"], "BEA", e["url"])
                    n += 1
        out["bea"] = n
    except ProviderError as e:
        out["bea"] = f"error {e}"
    if R.fred.configured():
        n = 0
        for key in BACKTEST_KEYS:
            d = catalog.resolve(key)
            rid = d.get("fred_release_id")
            if not rid:
                continue
            try:
                dates = R.fred.release_dates(rid, days_back=3300, days_fwd=0)
            except ProviderError:
                continue
            with db.tx() as c:
                for ds in dates:
                    rel = et_to_utc(datetime.fromisoformat(ds), d["et_time"])
                    if rel < now:
                        _store(c, key, d["name"], rel, f"FRED release {rid}",
                               f"https://fred.stlouisfed.org/releases/calendar?rid={rid}")
                        n += 1
        out["fred"] = n
    # Past releases that were on the live calendar (carry the consensus captured at the time)
    with db.tx() as c:
        for e in db.rows("SELECT * FROM events WHERE release_utc < ? AND catalog_key IN (%s)"
                         % ",".join("?" * len(BACKTEST_KEYS)), (db.iso(now), *BACKTEST_KEYS)):
            _store(c, e["catalog_key"], e["title"], db.parse_iso(e["release_utc"]), e["source"], e["source_url"])
    return out


def prefetch(release_isos: list[str], workers: int = 1, insts=("XAUUSD", "EURUSD")) -> int:
    """Download the Dukascopy day files needed for these releases (cached afterwards)."""
    have = {r["day"] for r in db.rows("SELECT day FROM gold_bars")}
    n = 0
    for iso in release_isos:
        T = db.parse_iso(iso)
        for inst in insts:
            for d in sorted({(T - timedelta(hours=7)).date(), T.date(), (T + timedelta(minutes=66)).date()}):
                key = d.isoformat() if inst == "XAUUSD" else f"{inst}:{d.isoformat()}"
                if key in have:
                    continue
                try:
                    R.duka.day(d, inst=inst)
                    have.add(key)
                    n += 1
                except Exception:
                    pass
    return n


def _close_at(by_min: dict, t: datetime):
    """Close of the 1-minute bar that starts at t-1min, i.e. the price at instant t."""
    b = by_min.get(int((t - timedelta(minutes=1)).timestamp()) // 60)
    return b[4] if b else None


TD_SYMBOL = {"XAUUSD": "XAU/USD", "EURUSD": "EUR/USD"}


def market_bars(inst: str, start: datetime, end: datetime) -> list[tuple]:
    """1-minute bars from the first source that can supply them: Dukascopy (keyless) → Twelve Data (key)."""
    errors = []
    try:
        b = R.duka.bars(start, end, inst=inst)
        if b:
            return b
    except ProviderError as e:
        errors.append(f"Dukascopy: {e}")
    if R.prices.configured():
        try:
            rows = R.prices.series(TD_SYMBOL[inst], "1min", start=start, end=end, outputsize=5000, ttl=3650 * 86400)
            out = []
            for r in rows:
                ts = datetime.strptime(r["t"][:16], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp()
                out.append((int(ts), r["o"], r["h"], r["l"], r["c"]))
            if out:
                return out
        except ProviderError as e:
            errors.append(f"Twelve Data: {e}")
    if errors:
        raise ProviderError("; ".join(errors))
    return []


def _rx_key(event_id: str, inst: str) -> str:
    return event_id if inst == "XAUUSD" else f"{event_id}|{inst}"


def reaction(event_id: str, release_utc: str, refresh: bool = False, inst: str = "XAUUSD") -> dict | None:
    """Measured reaction of `inst` around a release. None if price data is not available (yet).
    Recent releases use the live recorder (seconds-fresh); older ones use Dukascopy history."""
    key = _rx_key(event_id, inst)
    if not refresh:
        r = db.row("SELECT data FROM reactions WHERE event_id=?", (key,))
        if r:
            d = db.jload(r["data"])
            return d if d and not d.get("missing") else None
    T = db.parse_iso(release_utc)
    if T > db.now_utc() - timedelta(minutes=61):
        return None
    from .. import recorder
    src = "Dukascopy 1-minute BID candles"
    bars = []
    if T > db.now_utc() - timedelta(days=3) and recorder.coverage(inst, T - timedelta(minutes=30), T + timedelta(minutes=60)) >= 0.8:
        bars = recorder.bars(inst, T - timedelta(hours=7), T + timedelta(minutes=66))
        src = "Swissquote live ticks (recorded every 10 s)"
    if not bars:
        bars = market_bars(inst, T - timedelta(hours=7), T + timedelta(minutes=66))
    by_min = {b[0] // 60: b for b in bars}
    p_pre = _close_at(by_min, T)
    after = [b for b in bars if T.timestamp() <= b[0] < (T + timedelta(minutes=60)).timestamp()]
    if p_pre is None or len(after) < 30:
        if T < db.now_utc() - timedelta(days=2):
            with db.tx() as c:
                c.execute("INSERT OR REPLACE INTO reactions(event_id, data, built_at) VALUES(?,?,?)",
                          (key, db.jdump({"missing": True}), db.iso(db.now_utc())))
        return None
    pre = [b[4] for b in bars if (T - timedelta(hours=6)).timestamp() <= b[0] < (T - timedelta(minutes=61)).timestamp()]
    rets = [math.log(b / a) for a, b in zip(pre, pre[1:]) if a > 0]
    dp = 3 if inst == "XAUUSD" else 6
    out = {"inst": inst, "p_pre": p_pre, "p_asof": _close_at(by_min, T - timedelta(minutes=60)) or p_pre,
           "p_t30": _close_at(by_min, T - timedelta(minutes=30)), "p_t2": _close_at(by_min, T - timedelta(minutes=2)),
           "vol_pre_bp": round(pstdev(rets) * 1e4, 3) if len(rets) > 30 else None,
           "hi60": max(b[2] for b in after), "lo60": min(b[3] for b in after),
           "hi30": max(b[2] for b in after if b[0] < (T + timedelta(minutes=30)).timestamp()),
           "lo30": min(b[3] for b in after if b[0] < (T + timedelta(minutes=30)).timestamp()),
           "path": [[int((b[0] - T.timestamp()) // 60), round(b[4], dp), round(b[2], dp), round(b[3], dp)] for b in bars
                    if (T - timedelta(minutes=30)).timestamp() <= b[0] < (T + timedelta(minutes=61)).timestamp()],
           "source": f"{src} ({inst})"}
    for h in HORIZONS:
        p = _close_at(by_min, T + timedelta(minutes=h))
        out[f"p{h}"] = p
        out[f"m{h}"] = round(p - p_pre, dp + 2) if p else None
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO reactions(event_id, data, built_at) VALUES(?,?,?)",
                  (key, db.jdump(out), db.iso(db.now_utc())))
    return out


def released_values(key: str, release_utc: str) -> dict:
    """What was published at that release, from official sources (best effort, labelled)."""
    T = db.parse_iso(release_utc)
    ck = f"relvals:{key}:{release_utc}"
    c = db.cache_get(ck, 3650 * 86400)
    if c is not None:
        return c
    out = {}
    if key == "US_FOMC":
        url = f"https://www.federalreserve.gov/newsevents/pressreleases/monetary{T.strftime('%Y%m%d')}a.htm"
        try:
            page = R.fed_press.get(url, as_json=False, headers={"User-Agent": "Mozilla/5.0"})
            t = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", page)).split())
            m = re.search(r"decided to (raise|lower|maintain)[^.]{0,120}?target range for the federal funds rate[^.]*\.", t)
            if m:
                rng = re.search(r"(\d[\d\-/ ]*) to (\d[\d\-/ ]*) percent", m.group(0))
                out["actual"] = {"value": f"{m.group(1).capitalize()} · {rng.group(1).strip()}–{rng.group(2).strip()}%"
                                 if rng else m.group(1).capitalize(), "source": "Federal Reserve statement", "url": url}
                out["statement"] = m.group(0)
        except ProviderError:
            pass
    else:
        d = catalog.resolve(key)
        spec = (d.get("series") or {}).get("headline") or (d.get("series") or {}).get("core")
        if spec:
            from . import data
            try:
                obs, prov = data.series(spec[1])
                t = data.transform(obs, spec[2])
                # reference period = latest observation before the release month (monthly/quarterly data
                # describe an earlier period than the one they are published in)
                month_start = T.strftime("%Y-%m-01")
                cand = [o for o in t if o["date"] < month_start]
                if len(cand) >= 2:
                    out["actual"] = {"value": round(cand[-1]["value"], 3), "ref": cand[-1]["date"],
                                     "source": f"{prov['source']} {prov['series']} (current vintage)", "url": prov["url"]}
                    out["previous"] = {"value": round(cand[-2]["value"], 3), "ref": cand[-2]["date"],
                                       "source": f"{prov['source']} {prov['series']} (current vintage)", "url": prov["url"]}
            except ProviderError:
                pass
    live = db.row("SELECT consensus, previous, actual FROM events WHERE id=?", (f"{key}:{release_utc}",))
    if live:
        for fld in ("consensus", "previous", "actual"):
            v = (db.jload(live[fld]) or {})
            h = v.get("headline") or next(iter(v.values()), None) if v else None
            if h:
                out["forecast" if fld == "consensus" else fld] = h
    db.cache_put(ck, out)
    return out
