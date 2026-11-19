"""Connectors that need an API key. Each is fully implemented; without a key it
reports `configured: false` and the engine records the gap instead of inventing data."""
from datetime import datetime, timedelta, timezone

from .. import config, db
from .base import CONSENSUS, HISTORICAL, MARKET, OFFICIAL, Provider, ProviderError


class FRED(Provider):
    """FRED + ALFRED (real-time vintages). Docs: https://fred.stlouisfed.org/docs/api/fred/"""
    name = "FRED / ALFRED"
    category = HISTORICAL
    requires_key = True
    data_delay = "Updated shortly after official publication (minutes to hours); not a real-time feed"
    docs = "https://fred.stlouisfed.org/docs/api/fred/"
    notes = "Free key. 120 requests/minute. Release dates have no clock time; agency-standard times are applied."
    BASE = "https://api.stlouisfed.org/fred"

    def configured(self):
        return bool(config.FRED_API_KEY)

    def _q(self, path, ttl, **params):
        key = f"fred:{path}:{sorted(params.items())}"
        if ttl:
            c = db.cache_get(key, ttl)
            if c is not None:
                return c
        params.update(api_key=config.FRED_API_KEY, file_type="json")
        r = self.get(f"{self.BASE}/{path}", params=params)
        db.cache_put(key, r)
        return r

    def release_dates(self, release_id: int, days_back=400, days_fwd=120) -> list[str]:
        today = datetime.now(timezone.utc).date()
        r = self._q("release/dates", 6 * 3600, release_id=release_id,
                    realtime_start=str(today - timedelta(days=days_back)), realtime_end="9999-12-31",
                    include_release_dates_with_no_data="true", sort_order="asc", limit=1000)
        return [d["date"] for d in r.get("release_dates", [])
                if d["date"] <= str(today + timedelta(days=days_fwd))]

    def observations(self, series_id: str, start="2015-01-01", ttl=3 * 3600) -> list[dict]:
        r = self._q("series/observations", ttl, series_id=series_id, observation_start=start)
        out = []
        for o in r.get("observations", []):
            try:
                out.append({"date": o["date"], "value": float(o["value"])})
            except ValueError:
                continue  # "." = missing
        return out

    def first_releases(self, series_id: str, start="2010-01-01") -> list[dict]:
        """ALFRED initial-release values with the date each became public (look-ahead safe)."""
        r = self._q("series/observations", 24 * 3600, series_id=series_id, observation_start=start,
                    output_type=4, realtime_start="1776-07-04", realtime_end="9999-12-31")
        out = []
        for o in r.get("observations", []):
            try:
                out.append({"date": o["date"], "value": float(o["value"]), "public_on": o["realtime_start"]})
            except (ValueError, KeyError):
                continue
        return out

    def vintage_asof(self, series_id: str, asof: str, start="2018-01-01") -> list[dict]:
        """The series exactly as it was known on `asof` (no later revisions)."""
        r = self._q("series/observations", 24 * 3600, series_id=series_id, observation_start=start,
                    realtime_start=asof, realtime_end=asof)
        return [{"date": o["date"], "value": float(o["value"])} for o in r.get("observations", [])
                if o["value"] != "."]


class TwelveData(Provider):
    name = "Twelve Data (market prices)"
    category = MARKET
    requires_key = True
    data_delay = "Depends on plan: FX/metals near real-time; free tier is rate-limited (8/min, 800/day)"
    docs = "https://twelvedata.com/docs"
    notes = "Used for XAUUSD/EURUSD quotes, technical levels, event studies and post-release evaluation."
    BASE = "https://api.twelvedata.com"

    def configured(self):
        return bool(config.TWELVEDATA_API_KEY)

    def series(self, symbol: str, interval: str, start: datetime | None = None, end: datetime | None = None,
               outputsize: int = 500, ttl: int = 300) -> list[dict]:
        p = {"symbol": symbol, "interval": interval, "outputsize": outputsize, "timezone": "UTC",
             "order": "ASC", "apikey": config.TWELVEDATA_API_KEY}
        if start:
            p["start_date"] = start.strftime("%Y-%m-%d %H:%M:%S")
        if end:
            p["end_date"] = end.strftime("%Y-%m-%d %H:%M:%S")
        key = f"td:{symbol}:{interval}:{p.get('start_date')}:{p.get('end_date')}:{outputsize}"
        c = db.cache_get(key, ttl)
        if c is not None:
            return c
        r = self.get(f"{self.BASE}/time_series", params=p)
        if r.get("status") == "error":
            raise ProviderError(r.get("message", "Twelve Data error"))
        out = [{"t": v["datetime"], "o": float(v["open"]), "h": float(v["high"]), "l": float(v["low"]),
                "c": float(v["close"])} for v in r.get("values", [])]
        db.cache_put(key, out)
        return out

    def quote(self, symbol: str) -> dict:
        key = f"tdq:{symbol}"
        c = db.cache_get(key, 60)
        if c is not None:
            return c
        r = self.get(f"{self.BASE}/quote", params={"symbol": symbol, "apikey": config.TWELVEDATA_API_KEY})
        if r.get("status") == "error":
            raise ProviderError(r.get("message", "Twelve Data error"))
        q = {"symbol": symbol, "price": float(r["close"]), "change_pct": float(r.get("percent_change") or 0),
             "as_of": r.get("datetime"), "is_market_open": r.get("is_market_open")}
        db.cache_put(key, q)
        return q


class TradingEconomics(Provider):
    name = "Trading Economics (calendar + consensus)"
    category = CONSENSUS
    requires_key = True
    data_delay = "Calendar near real-time on paid plans"
    docs = "https://docs.tradingeconomics.com/economic_calendar/snapshot/"
    notes = "Paid. 'Forecast' = market consensus; 'TEForecast' = TE's own model (shown separately)."
    BASE = "https://api.tradingeconomics.com"

    def configured(self):
        return bool(config.TRADINGECONOMICS_API_KEY)

    def calendar(self, countries=("united states", "euro area"), days=10) -> list[dict]:
        d1 = datetime.now(timezone.utc).date() - timedelta(days=2)
        d2 = d1 + timedelta(days=days + 2)
        key = f"te:cal:{countries}:{d1}"
        c = db.cache_get(key, 15 * 60)
        if c is not None:
            return c
        r = self.get(f"{self.BASE}/calendar/country/{','.join(countries)}/{d1}/{d2}",
                     params={"c": config.TRADINGECONOMICS_API_KEY, "f": "json"})
        out = []
        for e in r:
            try:
                rel = datetime.fromisoformat(e["Date"]).replace(tzinfo=timezone.utc)
            except Exception:
                continue
            out.append({"title": e.get("Event", ""), "country": e.get("Country"), "release_utc": rel,
                        "importance": e.get("Importance"), "actual": e.get("Actual"),
                        "previous": e.get("Previous"), "revised": e.get("Revised"),
                        "consensus": e.get("Forecast"), "te_forecast": e.get("TEForecast"),
                        "reference": e.get("Reference"), "url": "https://tradingeconomics.com" + (e.get("URL") or "")})
        db.cache_put(key, out)
        return out
