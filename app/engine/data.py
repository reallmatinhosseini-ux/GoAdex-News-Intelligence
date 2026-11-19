"""Data access layer for the engine: picks the best available authorised source for
each series, applies transforms, and returns values wrapped with provenance."""
from datetime import datetime, timedelta, timezone

from .. import db
from ..providers import registry as R
from ..providers.base import HISTORICAL, MARKET, OFFICIAL, ProviderError, fact

import contextvars

# Point-in-time context. When set (a datetime), every accessor below returns only data that had
# been PUBLISHED before that instant. Used for retrospective backtests: no look-ahead.
ASOF: contextvars.ContextVar = contextvars.ContextVar("asof", default=None)
# Publication lag (days after the observation date) used to decide what was public at ASOF.
# Monthly macro data is published ~2-6 weeks after the reference month; 45 days is conservative.
LAG_DAYS = {"ICSA": 6, "T5YIE": 1, "DFF": 1, "DGS2": 1, "DFEDTARU": 1, "VIXCLS": 1, "DTWEXBGS": 1}
DEFAULT_LAG = 45


def _visible(obs: list[dict], series_id: str) -> list[dict]:
    asof = ASOF.get()
    if not asof:
        return obs
    lag = timedelta(days=LAG_DAYS.get(series_id, DEFAULT_LAG))
    return [o for o in obs if datetime.fromisoformat(o["date"]).replace(tzinfo=timezone.utc) + lag <= asof]


def _before_day(rows: list[dict], key="date") -> list[dict]:
    asof = ASOF.get()
    if not asof:
        return rows
    d = asof.date().isoformat()
    return [r for r in rows if r[key] < d]


# FRED series -> BLS fallback (keyless) where an equivalent official series exists
BLS_EQUIV = {"CPIAUCSL": "CUSR0000SA0", "CPILFESL": "CUSR0000SA0L1E", "PAYEMS": "CES0000000001",
             "UNRATE": "LNS14000000", "CES0500000003": "CES0500000003", "PPIFIS": "WPSFD4",
             "CIVPART": "LNS11300000"}


def series(series_id: str, start="2016-01-01") -> tuple[list[dict], dict]:
    """Return (observations, provenance). Tries FRED, then BLS. Raises if neither works."""
    errors = []
    if R.fred.configured():
        try:
            obs = _visible(R.fred.observations(series_id, start=start), series_id)
            return obs, {"source": "FRED", "series": series_id,
                         "url": f"https://fred.stlouisfed.org/series/{series_id}"}
        except ProviderError as e:
            errors.append(f"FRED: {e}")
    if series_id in BLS_EQUIV:
        bid = BLS_EQUIV[series_id]
        try:
            obs = _visible(R.bls.series(sorted(set(BLS_EQUIV.values())), years=10).get(bid, []), series_id)
            if obs:
                return obs, {"source": "BLS", "series": bid,
                             "url": f"https://data.bls.gov/timeseries/{bid}"}
        except ProviderError as e:
            errors.append(f"BLS: {e}")
    raise ProviderError("; ".join(errors) or f"No configured source for {series_id} (needs FRED_API_KEY)")


def transform(obs: list[dict], how: str) -> list[dict]:
    out = []
    for i, o in enumerate(obs):
        v = None
        if how == "level":
            v = o["value"]
        elif how == "mom_pct" and i >= 1:
            v = (o["value"] / obs[i - 1]["value"] - 1) * 100
        elif how == "yoy_pct" and i >= 12:
            v = (o["value"] / obs[i - 12]["value"] - 1) * 100
        elif how == "change_k" and i >= 1:
            v = o["value"] - obs[i - 1]["value"]
        if v is not None:
            out.append({"date": o["date"], "value": round(v, 4)})
    return out


def latest(series_id: str, how="level", start="2016-01-01"):
    obs, prov = series(series_id, start)
    t = transform(obs, how)
    if not t:
        raise ProviderError(f"No data for {series_id}")
    return t, prov


def yields() -> tuple[list[dict], list[dict]]:
    """Nominal and real Treasury curves (official, end-of-day)."""
    end = ASOF.get()
    nom = _before_day(R.treasury.history("daily_treasury_yield_curve", months=5 if end else 4, end=end))
    try:
        real = _before_day(R.treasury.history("daily_treasury_real_yield_curve", months=5 if end else 4, end=end))
    except ProviderError:
        real = []
    return nom, real


def cot(asset: str) -> list[dict]:
    """CFTC positioning; Tuesday reports are public the following Friday (3-day lag)."""
    asof = ASOF.get()
    if not asof:
        return R.cftc.positioning(asset)
    return R.cftc.positioning(asset, 156, end=(asof - timedelta(days=3)).date().isoformat())


def ecb(key: str, n: int = 130) -> list[dict]:
    if not ASOF.get():
        return R.ecb.series(key, n)
    return _before_day(R.ecb.history(key))[-n:]


def fred_obs(series_id: str, start: str) -> list[dict]:
    return _visible(R.fred.observations(series_id, start=start), series_id)


def price_history(asset: str, interval="1day", outputsize=120):
    from ..catalog import ASSETS
    return R.prices.series(ASSETS[asset]["td_symbol"], interval, outputsize=outputsize, ttl=900)


def change(rows: list[dict], key: str, n: int):
    vals = [r[key] for r in rows if r.get(key) is not None]
    if len(vals) <= n:
        return None
    return vals[-1] - vals[-1 - n]


def provenance_fact(value, prov: dict, as_of: str, category=OFFICIAL, **kw):
    return fact(value, prov["source"] + " " + prov["series"], prov["url"], as_of, category, **kw)
