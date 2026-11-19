"""Full economic calendar (all impacts, all major currencies) with consensus forecasts.

Source: the public Forex Factory weekly calendar export (this week + next week).
It carries title, time, impact, forecast (consensus) and previous. It does NOT carry
actual values — those come from official sources (BLS / Fed / FRED / BEA) when released.
Fetched at most once an hour, as the feed requests.
"""
from datetime import datetime, timezone

from .. import db
from .base import CONSENSUS, Provider, ProviderError

# Forex Factory title -> (catalog key, component, is_headline)
FF_MAP = {
    "CPI m/m": ("US_CPI", "headline", True), "Core CPI m/m": ("US_CPI", "core", False),
    "CPI y/y": ("US_CPI", "headline_yoy", False), "Core CPI y/y": ("US_CPI", "core_yoy", False),
    "Non-Farm Employment Change": ("US_NFP", "headline", True), "Unemployment Rate": ("US_NFP", "unemployment", False),
    "Average Hourly Earnings m/m": ("US_NFP", "ahe", False),
    "Core PCE Price Index m/m": ("US_PCE", "core", True), "PCE Price Index m/m": ("US_PCE", "headline", False),
    "Core PCE Price Index y/y": ("US_PCE", "core_yoy", False),
    "Federal Funds Rate": ("US_FOMC", "upper_bound", True), "FOMC Statement": ("US_FOMC", "statement", False),
    "FOMC Press Conference": ("US_FOMC_PRESSER", "presser", True), "FOMC Meeting Minutes": ("US_FOMC_MINUTES", "minutes", True),
    "Advance GDP q/q": ("US_GDP", "headline", True), "Prelim GDP q/q": ("US_GDP", "headline", True),
    "Final GDP q/q": ("US_GDP", "headline", True),
    "ISM Manufacturing PMI": ("US_ISM_MFG", "headline", True), "ISM Services PMI": ("US_ISM_SVC", "headline", True),
    "Retail Sales m/m": ("US_RETAIL", "headline", True), "Core Retail Sales m/m": ("US_RETAIL", "core", False),
    "PPI m/m": ("US_PPI", "headline", True), "Core PPI m/m": ("US_PPI", "core", False),
    "Unemployment Claims": ("US_CLAIMS", "headline", True),
    "ADP Non-Farm Employment Change": ("US_ADP", "headline", True),
    "JOLTS Job Openings": ("US_JOLTS", "headline", True),
    "CB Consumer Confidence": ("US_CONF", "headline", True),
    "Prelim UoM Consumer Sentiment": ("US_UMICH", "sentiment", True), "Revised UoM Consumer Sentiment": ("US_UMICH", "sentiment", True),
    "Prelim UoM Inflation Expectations": ("US_UMICH", "inflation_1y", False), "Revised UoM Inflation Expectations": ("US_UMICH", "inflation_1y", False),
    "Main Refinancing Rate": ("EU_ECB", "headline", True), "ECB Press Conference": ("EU_ECB_PRESSER", "presser", True),
}

GOLD_WORDS = ("fed ", "fomc", "powell", "warsh", "cpi", "pce", "non-farm", "unemployment", "gdp", "ppi", "retail sales",
              "ism", "jolts", "claims", "treasury", "inflation", "interest rate", "ecb", "lagarde")


def gold_relevant(title: str, country: str, impact: str) -> bool:
    if country == "USD" and impact in ("High", "Medium"):
        return True
    t = " " + title.lower()
    return country in ("USD", "EUR") and impact == "High" or (country == "USD" and any(w in t for w in GOLD_WORDS)
                                                               and impact != "Holiday" and "speaks" not in t)


class ForexFactory(Provider):
    name = "Economic calendar (Forex Factory weekly feed)"
    category = CONSENSUS
    data_delay = "Refreshed hourly; consensus forecasts, no actual values"
    docs = "https://www.forexfactory.com/calendar"
    URLS = ["https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            "https://nfs.faireconomy.media/ff_calendar_nextweek.json"]

    def week(self) -> list[dict]:
        out = []
        for u in self.URLS:
            ck = f"ff:{u}"
            data = db.cache_get(ck, 3600)
            if data is None:
                try:
                    data = self.get(u, headers={"User-Agent": "Mozilla/5.0"})
                    db.cache_put(ck, data)
                except ProviderError:
                    data = db.cache_get_stale(ck)[0] or []  # next week's file appears later in the week
            for e in data:
                try:
                    rel = datetime.fromisoformat(e["date"]).astimezone(timezone.utc)
                except Exception:
                    continue
                out.append({"title": e["title"].strip(), "country": e["country"], "release_utc": rel,
                            "impact": e.get("impact", ""), "forecast": e.get("forecast", ""),
                            "previous": e.get("previous", "")})
        return out
