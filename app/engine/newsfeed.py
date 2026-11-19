"""News engine: collects market news, tags what each item affects, scores impact, links it to
calendar events — and fills in official actual values for released US indicators.

Relevance is rule-based and transparent (keyword families below); items that touch none of the
market-moving themes are dropped.
"""
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .. import db
from ..providers import registry as R
from ..providers.base import ProviderError
from ..providers.news import FEEDS

log = logging.getLogger("news")
ET_TZ = ZoneInfo("America/New_York")

TAGS = {
    "gold": (3, r"\bgold\b|bullion|\bxau|precious metal|\bsilver\b"),
    "central_bank": (3, r"\bfed\b|federal reserve|\bfomc\b|\becb\b|central bank|rate (?:cut|hike|decision)|interest rates?|monetary policy|"
                        r"\bminutes\b|powell|warsh|waller|jefferson|lagarde|bank of england|\bboe\b|bank of japan|\bboj\b|\bsnb\b|rba|pboc"),
    "data": (2, r"\bcpi\b|inflation (?:data|rate|report)|payrolls?|\bnfp\b|jobless|unemployment|\bgdp\b|retail sales|\bpmi\b|\bism\b|\bppi\b|"
                r"\bpce\b|jolts|durable goods|housing starts|building permits|consumer (?:confidence|sentiment)|industrial production|"
                r"trade (?:balance|deficit)|personal income|philly fed|empire state|claims"),
    "usd": (2, r"\bdollar\b|greenback|\bdxy\b|\busd\b|treasur|\byields?\b|\bbonds?\b|tariff|white house|shutdown|debt ceiling|\bfiscal\b"),
    "eur": (2, r"\beuros?\b|eurozone|euro area|\becb\b|lagarde|eur/usd|eurusd|\bgerman|\bbund\b|france|\bitaly|\beu\b"),
    "geopolitics": (2, r"\bwar\b|missile|\battack|sanction|ukraine|russia|israel|\biran|gaza|middle east|taiwan|ceasefire|nuclear|"
                       r"\bconflict|geopolit|military"),
    "risk": (1, r"\bstocks?\b|s&p|nasdaq|\bdow\b|equities|wall street|sell-?off|\brally\b|\bvix\b|volatility|recession|crisis|default|safe[- ]haven"),
    "oil": (1, r"\boil\b|crude|\bopec|brent|\bwti\b|diesel|natural gas"),
}
ASSET_OF = {"gold": ["XAUUSD"], "usd": ["XAUUSD", "EURUSD"], "eur": ["EURUSD"], "central_bank": ["XAUUSD", "EURUSD"],
            "data": ["XAUUSD", "EURUSD"], "geopolitics": ["XAUUSD"], "risk": ["XAUUSD"], "oil": ["XAUUSD"]}
BEA_KEEP = re.compile(r"gross domestic product|gdp|personal income|outlays|pce|trade in goods|international transactions", re.I)


def classify(title: str, summary: str, kind: str) -> dict:
    text = f"{title} {summary}".lower()
    tags = [t for t, (_, rx) in TAGS.items() if re.search(rx, text)]
    score = sum(TAGS[t][0] for t in tags)
    if tags and kind == "official":
        score += 2
    if re.search(r"\b(gold|xau)\b", title.lower()):
        score += 2
    impact = "high" if score >= 7 else "medium" if score >= 4 else "low"
    assets = sorted({a for t in tags for a in ASSET_OF[t]})
    return {"tags": tags, "score": score, "impact": impact, "assets": assets}


def _related(pub: datetime, tags: list, title: str) -> str | None:
    if "data" not in tags and "central_bank" not in tags:
        return None
    words = set(re.findall(r"[a-z]{4,}", title.lower()))
    best = None
    for e in db.rows("SELECT id, title, release_utc FROM events WHERE release_utc BETWEEN ? AND ?",
                     (db.iso(pub - timedelta(hours=6)), db.iso(pub + timedelta(hours=1)))):
        ew = set(re.findall(r"[a-z]{4,}", e["title"].lower()))
        overlap = len(words & ew)
        if overlap and (best is None or overlap > best[0]):
            best = (overlap, e["id"])
    return best[1] if best else None


def collect() -> dict:
    kinds = {n: k for n, _, k in FEEDS}
    stats = {}
    now = db.now_utc()
    for name, url, kind in FEEDS:
        try:
            items = R.news.fetch(name, url)
        except (ProviderError, Exception) as e:
            stats[name] = f"error: {str(e)[:80]}"
            continue
        n = 0
        with db.tx() as c:
            for it in items:
                pub = it["published"] or now
                if pub < now - timedelta(days=4) or pub > now + timedelta(hours=1):
                    continue
                if name == "BEA" and not BEA_KEEP.search(it["title"]):
                    continue
                cl = classify(it["title"], it["summary"], kinds[name])
                # keep market-moving items: strong score, or geopolitics (safe-haven driver for gold)
                if cl["score"] < 3 and "geopolitics" not in cl["tags"]:
                    continue
                c.execute("INSERT OR IGNORE INTO research_feed(id, kind, title, url, source, published_at, summary, fetched_at, "
                          "tags, score, impact, assets, related_event) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (it["url"], kind, it["title"], it["url"], name, db.iso(pub), it["summary"], db.iso(now),
                           db.jdump(cl["tags"]), cl["score"], cl["impact"], db.jdump(cl["assets"]),
                           _related(pub, cl["tags"], it["title"])))
                n += 1
        stats[name] = f"ok ({len(items)} items, {n} relevant)"
    with db.tx() as c:
        c.execute("DELETE FROM research_feed WHERE published_at < ?", (db.iso(now - timedelta(days=14)),))
    db.cache_put("news_status", {"at": db.iso(now), "feeds": stats})
    return stats


# --------------------------------------------------------------------------- official actuals
# Calendar title → (FRED series, transform, unit, decimals, divisor)
ACTUALS = {
    "Retail Sales m/m": ("RSAFS", "mom", "%", 1, 1), "Core Retail Sales m/m": ("RSFSXMV", "mom", "%", 1, 1),
    "Unemployment Claims": ("ICSA", "level", "K", 0, 1000), "Non-Farm Employment Change": ("PAYEMS", "chg", "K", 0, 1),
    "Unemployment Rate": ("UNRATE", "level", "%", 1, 1), "Average Hourly Earnings m/m": ("CES0500000003", "mom", "%", 1, 1),
    "CPI m/m": ("CPIAUCSL", "mom", "%", 1, 1), "Core CPI m/m": ("CPILFESL", "mom", "%", 1, 1),
    "CPI y/y": ("CPIAUCNS", "yoy", "%", 1, 1), "Core CPI y/y": ("CPILFENS", "yoy", "%", 1, 1),
    "PPI m/m": ("PPIFIS", "mom", "%", 1, 1), "Core PPI m/m": ("PPICOR", "mom", "%", 1, 1),
    "Core PCE Price Index m/m": ("PCEPILFE", "mom", "%", 1, 1), "PCE Price Index m/m": ("PCEPI", "mom", "%", 1, 1),
    "Core PCE Price Index y/y": ("PCEPILFE", "yoy", "%", 1, 1), "Industrial Production m/m": ("INDPRO", "mom", "%", 1, 1),
    "Capacity Utilization Rate": ("TCU", "level", "%", 1, 1), "Building Permits": ("PERMIT", "level", "M", 2, 1000),
    "Housing Starts": ("HOUST", "level", "M", 2, 1000), "JOLTS Job Openings": ("JTSJOL", "level", "M", 2, 1000),
    "ADP Non-Farm Employment Change": ("ADPMNUSNERSA", "chg", "K", 0, 1), "Business Inventories m/m": ("BUSINV", "mom", "%", 1, 1),
    "Import Prices m/m": ("IR", "mom", "%", 1, 1), "Philly Fed Manufacturing Index": ("GACDFSA066MSFRBPHI", "level", "", 1, 1),
    "Empire State Manufacturing Index": ("GACDINA066MSFRBNY", "level", "", 1, 1),
    "Advance GDP q/q": ("A191RL1Q225SBEA", "level", "%", 1, 1), "Prelim GDP q/q": ("A191RL1Q225SBEA", "level", "%", 1, 1),
    "Final GDP q/q": ("A191RL1Q225SBEA", "level", "%", 1, 1), "Durable Goods Orders m/m": ("DGORDER", "mom", "%", 1, 1),
    "Core Durable Goods Orders m/m": ("ADXTNO", "mom", "%", 1, 1), "New Home Sales": ("HSN1F", "level", "M", 2, 1000),
    "Trade Balance": ("BOPGSTB", "level", "B", 1, 1000), "Federal Funds Rate": ("DFEDTARU", "level", "%", 2, 1),
}


def _fmt(v, unit, dp):
    return f"{v:.{dp}f}{unit}"


def _actual_for(title: str, release: datetime):
    """Value first published on the release date (FRED vintage as of that day). None if not yet published."""
    sid, how, unit, dp, div = ACTUALS[title]
    day = release.astimezone(ET_TZ).date().isoformat()
    vint = R.fred._q("series/vintagedates", 600, series_id=sid, realtime_start=day)
    if day not in vint.get("vintage_dates", []):
        return None
    obs = R.fred._q("series/observations", 3650 * 86400, series_id=sid, realtime_start=day, realtime_end=day,
                    observation_start=(release - timedelta(days=800)).date().isoformat())
    vals = [(o["date"], float(o["value"])) for o in obs.get("observations", []) if o["value"] != "."]
    if len(vals) < 13 and how == "yoy":
        return None
    if len(vals) < 2:
        return None
    last = vals[-1][1]
    if how == "level":
        v = last / div
    elif how == "chg":
        v = (last - vals[-2][1]) / div
    elif how == "mom":
        v = (last / vals[-2][1] - 1) * 100
    else:
        v = (last / vals[-13][1] - 1) * 100
    return {"value": _fmt(v, unit, dp), "ref": vals[-1][0], "series": sid,
            "source": f"FRED {sid} (as first published {day})", "url": f"https://fred.stlouisfed.org/series/{sid}"}


def fill_actuals() -> int:
    if not R.fred.configured():
        return 0
    now = db.now_utc()
    n = 0
    for r in db.rows("SELECT * FROM calendar WHERE country='USD' AND (actual IS NULL OR actual='') AND release_utc BETWEEN ? AND ?",
                     (db.iso(now - timedelta(days=8)), db.iso(now - timedelta(minutes=3)))):
        if r["title"] not in ACTUALS:
            continue
        try:
            a = _actual_for(r["title"], db.parse_iso(r["release_utc"]))
        except (ProviderError, Exception) as e:
            log.debug("actual %s: %s", r["title"], e)
            continue
        if not a:
            continue
        with db.tx() as c:
            c.execute("UPDATE calendar SET actual=?, actual_source=? WHERE id=?", (a["value"], db.jdump(a), r["id"]))
            fc = f" vs forecast {r['forecast']}" if r["forecast"] else ""
            pv = f", previous {r['previous']}" if r["previous"] else ""
            title = f"US {r['title']}: {a['value']}{fc}{pv}"
            c.execute("INSERT OR IGNORE INTO research_feed(id, kind, title, url, source, published_at, summary, fetched_at, tags, "
                      "score, impact, assets, related_event) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (f"actual:{r['id']}", "data", title, a["url"], "Official data (FRED)", r["release_utc"],
                       f"Reference period {a['ref'][:7]} · {a['source']}", db.iso(now), db.jdump(["data", "usd"]),
                       9 if r["impact"] == "High" else 6 if r["impact"] == "Medium" else 4,
                       "high" if r["impact"] == "High" else "medium" if r["impact"] == "Medium" else "low",
                       db.jdump(["XAUUSD", "EURUSD"]), r["event_id"]))
        n += 1
    return n


def _loop(stop: threading.Event):
    i = 0
    while not stop.is_set():
        try:
            collect()
            if i % 3 == 0:
                fill_actuals()
        except Exception as e:
            log.warning("news loop: %s", e)
        i += 1
        stop.wait(180)


def start(stop: threading.Event):
    t = threading.Thread(target=_loop, args=(stop,), name="news", daemon=True)
    t.start()
    return t
