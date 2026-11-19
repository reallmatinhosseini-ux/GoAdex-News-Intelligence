"""Keyless official sources: Federal Reserve, BEA, BLS, US Treasury, CFTC.

All endpoints are public, documented feeds published by the agencies themselves.
"""
import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .. import config, db
from .base import HISTORICAL, MARKET, NEWS, OFFICIAL, Provider, ProviderError

ET_TZ = ZoneInfo("America/New_York")


def et_to_utc(d: datetime, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(d.year, d.month, d.day, h, m, tzinfo=ET_TZ).astimezone(timezone.utc)


def _parse_fed_time(t: str) -> str | None:
    m = re.match(r"\s*(\d{1,2}):(\d{2})\s*([ap])\.?m\.?", t or "", re.I)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3).lower()
    if ap == "p" and h != 12:
        h += 12
    if ap == "a" and h == 12:
        h = 0
    return f"{h:02d}:{mi:02d}"


class FedCalendar(Provider):
    name = "Federal Reserve calendar"
    category = OFFICIAL
    data_delay = "Schedule feed; updated by the Board as events are announced"
    docs = "https://www.federalreserve.gov/newsevents/calendar.htm"
    URL = "https://www.federalreserve.gov/json/calendar.json"

    def events(self) -> list[dict]:
        raw = self.get(self.URL, as_json=False)
        data = __import__("json").loads(raw.lstrip("﻿"))
        out = []
        for e in data.get("events", []):
            typ = e.get("type")
            title = html.unescape(e.get("title", "")).strip()
            if typ not in ("FOMC", "Speeches", "Testimony", "Beige"):
                continue
            hhmm = _parse_fed_time(e.get("time", ""))
            if not hhmm:
                continue
            y, mo = map(int, e["month"].split("-"))
            for d in str(e.get("days", "")).split(","):
                d = d.strip()
                if not d.isdigit():
                    continue
                rel = et_to_utc(datetime(y, mo, int(d)), hhmm)
                desc = re.sub(r"<[^>]+>", " ", html.unescape(html.unescape(e.get("description", ""))))
                out.append({"title": title, "type": typ, "release_utc": rel, "time_basis": "published",
                            "description": " ".join(desc.split()), "url": e.get("link") or self.docs})
        return out


class FedPress(Provider):
    name = "Federal Reserve press releases"
    category = OFFICIAL
    data_delay = "RSS, typically within minutes of publication"
    docs = "https://www.federalreserve.gov/feeds/feeds.htm"
    FEEDS = {"monetary": "https://www.federalreserve.gov/feeds/press_monetary.xml",
             "speeches": "https://www.federalreserve.gov/feeds/speeches.xml"}

    def items(self, feed="monetary") -> list[dict]:
        txt = self.get(self.FEEDS[feed], as_json=False).lstrip("﻿")
        root = ET.fromstring(txt)
        out = []
        for it in root.iter("item"):
            pub = it.findtext("pubDate") or ""
            try:
                pdt = datetime.strptime(pub.strip(), "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
            except Exception:
                pdt = None
            out.append({"title": (it.findtext("title") or "").strip(), "url": (it.findtext("link") or "").strip(),
                        "published_at": db.iso(pdt) if pdt else None,
                        "summary": re.sub(r"<[^>]+>", " ", it.findtext("description") or "").strip()[:400]})
        return out

    def statement_text(self, url: str) -> str:
        page = self.get(url, as_json=False)
        txt = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S)
        txt = re.sub(r"<[^>]+>", " ", txt)
        return " ".join(html.unescape(txt).split())


class BEASchedule(Provider):
    name = "BEA release schedule"
    category = OFFICIAL
    data_delay = "Official ICS schedule"
    docs = "https://www.bea.gov/news/schedule"
    URL = "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics"

    def events(self) -> list[dict]:
        txt = self.get(self.URL, as_json=False, headers={"User-Agent": "Mozilla/5.0 (research)"})
        txt = re.sub(r"\r?\n[ \t]", "", txt)  # unfold ICS lines
        out = []
        for block in txt.split("BEGIN:VEVENT")[1:]:
            summ = re.search(r"SUMMARY:(.*)", block)
            dt = re.search(r"DTSTART[^:]*:(\d{8}T\d{6}Z?)", block)
            if not summ or not dt:
                continue
            s = dt.group(1)
            rel = datetime.strptime(s[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
            out.append({"title": summ.group(1).replace("\\,", ",").strip(), "release_utc": rel,
                        "time_basis": "published", "url": self.docs})
        return out


class BLS(Provider):
    name = "BLS Public Data API"
    category = OFFICIAL
    data_delay = "Official values available at release time (08:30 ET)"
    docs = "https://www.bls.gov/developers/"
    notes = "v1 works without a key (25 queries/day); set BLS_API_KEY for v2 (500/day)."

    def series(self, ids: list[str], years: int = 3) -> dict[str, list[dict]]:
        key = f"bls:{','.join(sorted(ids))}:{years}"
        cached = db.cache_get(key, 12 * 3600)  # v1 allows 25 queries/day: one bundled query per 12 h
        if cached is not None:
            return cached
        return self.series_fresh(ids, years)

    def series_fresh(self, ids, years=3):
        y = datetime.now().year
        payload = {"seriesid": ids, "startyear": str(y - years + 1), "endyear": str(y)}
        url = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
        if config.BLS_API_KEY:
            payload["registrationkey"] = config.BLS_API_KEY
            url = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
        r = self.post(url, payload)
        if r.get("status") != "REQUEST_SUCCEEDED":
            raise ProviderError("; ".join(r.get("message", [])) or "BLS request failed")
        out = {}
        for s in r["Results"]["series"]:
            obs = []
            for d in s["data"]:
                if not d["period"].startswith("M") or d["period"] == "M13":
                    continue
                try:
                    obs.append({"date": f"{d['year']}-{d['period'][1:]}-01", "value": float(d["value"])})
                except ValueError:
                    continue
            out[s["seriesID"]] = sorted(obs, key=lambda o: o["date"])
        db.cache_put(f"bls:{','.join(sorted(ids))}:{years}", out)
        return out


class Treasury(Provider):
    name = "US Treasury yield curve"
    category = MARKET
    data_delay = "End-of-day official par yields (published after the close)"
    docs = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates"
    BASE = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
    NS = {"a": "http://www.w3.org/2005/Atom",
          "m": "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata",
          "d": "http://schemas.microsoft.com/ado/2007/08/dataservices"}

    def _month(self, kind: str, yyyymm: str) -> list[dict]:
        key = f"tsy:{kind}:{yyyymm}"
        cur = datetime.now(timezone.utc).strftime("%Y%m")
        cached = db.cache_get(key, 3600 if yyyymm == cur else 3650 * 86400)
        if cached is not None:
            return cached
        txt = self.get(self.BASE, params={"data": kind, "field_tdr_date_value_month": yyyymm},
                       as_json=False, headers={"User-Agent": "Mozilla/5.0 (research)"})
        root = ET.fromstring(txt.encode("utf-8"))
        out = []
        for p in root.iter(f"{{{self.NS['m']}}}properties"):
            rec = {}
            for ch in p:
                tag = ch.tag.split("}")[1]
                rec[tag] = ch.text
            date = (rec.get("NEW_DATE") or rec.get("NEW_DATE") or "")[:10]
            if date:
                out.append({"date": date, **{k: float(v) for k, v in rec.items()
                                             if k.startswith(("BC_", "TC_")) and v not in (None, "")}})
        out.sort(key=lambda r: r["date"])
        db.cache_put(key, out)
        return out

    def history(self, kind="daily_treasury_yield_curve", months=4, end: datetime | None = None) -> list[dict]:
        now = end or datetime.now(timezone.utc)
        rows = []
        for i in range(months - 1, -1, -1):
            d = (now.replace(day=1) - timedelta(days=31 * i)).strftime("%Y%m")
            try:
                rows += self._month(kind, d)
            except ProviderError:
                if i == 0:
                    raise
        seen, out = set(), []
        for r in rows:
            if r["date"] not in seen:
                seen.add(r["date"])
                out.append(r)
        return out


class CFTC(Provider):
    name = "CFTC Commitments of Traders"
    category = MARKET
    data_delay = "Weekly; Tuesday positions published Friday 15:30 ET"
    docs = "https://publicreporting.cftc.gov/"
    URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
    CODES = {"XAUUSD": "088691", "EURUSD": "099741"}

    def positioning(self, asset: str, weeks: int = 156, end: str | None = None) -> list[dict]:
        """Latest `weeks` reports, or the `weeks` reports up to `end` (YYYY-MM-DD) for point-in-time use."""
        code = self.CODES[asset]
        key = f"cot:{code}:{weeks}:{end}"
        cached = db.cache_get(key, 3650 * 86400 if end else 12 * 3600)
        if cached is not None:
            return cached
        params = {"cftc_contract_market_code": code, "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": weeks}
        if end:
            params["$where"] = f"report_date_as_yyyy_mm_dd <= '{end}T00:00:00'"
        data = self.get(self.URL, params=params)
        out = []
        for r in data:
            try:
                oi = float(r["open_interest_all"])
                net = float(r["noncomm_positions_long_all"]) - float(r["noncomm_positions_short_all"])
                out.append({"date": r["report_date_as_yyyy_mm_dd"][:10], "net_noncomm": net, "oi": oi,
                            "net_pct_oi": net / oi * 100 if oi else None})
            except (KeyError, ValueError):
                continue
        out.sort(key=lambda r: r["date"])
        db.cache_put(key, out)
        return out


class ECB(Provider):
    """ECB Data Portal (official, keyless): EUR/USD reference rate, euro-area yields, policy rate."""
    name = "ECB Data Portal"
    category = OFFICIAL
    data_delay = "Daily; EUR/USD reference rate published ~16:00 CET"
    docs = "https://data.ecb.europa.eu/help/api/overview"
    BASE = "https://data-api.ecb.europa.eu/service/data"
    SERIES = {
        "eurusd": "EXR/D.USD.EUR.SP00.A",
        "ea_2y": "YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_2Y",
        "deposit_rate": "FM/D.U2.EUR.4F.KR.DFR.LEV",
    }

    def history(self, key: str, start: str = "2016-01-01") -> list[dict]:
        ck = f"ecbh:{key}:{start}"
        cached = db.cache_get(ck, 6 * 3600)
        if cached is not None:
            return cached
        d = self.get(f"{self.BASE}/{self.SERIES[key]}", params={"startPeriod": start, "format": "jsondata"}, timeout=60)
        obs = list(d["dataSets"][0]["series"].values())[0]["observations"]
        dates = d["structure"]["dimensions"]["observation"][0]["values"]
        out = sorted(({"date": dates[int(i)]["id"], "value": float(v[0])} for i, v in obs.items() if v and v[0] is not None),
                     key=lambda r: r["date"])
        db.cache_put(ck, out)
        return out

    def series(self, key: str, n: int = 130) -> list[dict]:
        ck = f"ecb:{key}:{n}"
        cached = db.cache_get(ck, 3 * 3600)
        if cached is not None:
            return cached
        d = self.get(f"{self.BASE}/{self.SERIES[key]}", params={"lastNObservations": n, "format": "jsondata"})
        obs = list(d["dataSets"][0]["series"].values())[0]["observations"]
        dates = d["structure"]["dimensions"]["observation"][0]["values"]
        out = sorted(({"date": dates[int(i)]["id"], "value": float(v[0])} for i, v in obs.items() if v and v[0] is not None),
                     key=lambda r: r["date"])
        db.cache_put(ck, out)
        return out
