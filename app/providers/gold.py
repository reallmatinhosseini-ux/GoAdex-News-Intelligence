"""Market data for XAUUSD and EURUSD — no keys required.

- Live quotes: Swissquote public bid/ask feed (updates every few seconds). gold-api.com is a
  fallback for gold only.
- History: Dukascopy public datafeed, 1-minute BID candles per UTC day (LZMA .bi5 files).
  Completed days are immutable and cached permanently in SQLite.
"""
import lzma
import struct
import threading
import time
from datetime import date, datetime, timedelta, timezone

from .. import db
from .base import MARKET, Provider, ProviderError

INSTR = {
    "XAUUSD": {"sq": "XAU/USD", "duka": "XAUUSD", "scale": 1000.0, "dp": 2, "pip": None, "label": "Gold"},
    "EURUSD": {"sq": "EUR/USD", "duka": "EURUSD", "scale": 100000.0, "dp": 5, "pip": 0.0001, "label": "EUR/USD"},
}
_throttle = threading.Lock()
_last = [0.0]
_cool = [0.0]  # monotonic time until which the history feed is known to be rate-limiting us
MIN_INTERVAL = 4.0  # starting gap between history requests; adapts between FLOOR and CEIL
_gap = [MIN_INTERVAL]
FLOOR, CEIL = 2.5, 60.0


class LiveQuotes(Provider):
    name = "Live quotes (Swissquote bid/ask)"
    category = MARKET
    data_delay = "Real-time (seconds)"
    docs = "https://www.swissquote.com/"
    URL = "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/{}"

    def quote(self, inst: str) -> dict:
        ck = f"quote:{inst}"
        c = db.cache_get(ck, 3)
        if c:
            return c
        meta = INSTR[inst]
        d = self.get(self.URL.format(meta["sq"]), timeout=6)
        best = max(d, key=lambda p: p.get("ts", 0))
        prof = next((x for x in best["spreadProfilePrices"] if x["spreadProfile"] == "standard"),
                    best["spreadProfilePrices"][0])
        ts = datetime.fromtimestamp(best["ts"] / 1000, timezone.utc)
        dp = meta["dp"]
        q = {"inst": inst, "price": round(prof["bid"], dp), "bid": round(prof["bid"], dp), "ask": round(prof["ask"], dp),
             "mid": round((prof["bid"] + prof["ask"]) / 2, dp), "ts": best["ts"] / 1000, "as_of": db.iso(ts),
             "source": f"Swissquote live {meta['sq']} (bid)", "url": self.docs}
        db.cache_put(ck, q)
        return q


class GoldSpot(Provider):
    """Live gold: Swissquote bid/ask, gold-api.com fallback."""
    name = "Live gold price (gold-api.com fallback)"
    category = MARKET
    data_delay = "Real-time"
    docs = "https://gold-api.com/"

    def __init__(self, live: LiveQuotes):
        self.live = live

    def spot(self) -> dict:
        try:
            return self.live.quote("XAUUSD")
        except (ProviderError, KeyError, ValueError, TypeError):
            pass
        c = db.cache_get("gold:spot", 8)
        if c:
            return c
        r = self.get("https://api.gold-api.com/price/XAU")
        q = {"inst": "XAUUSD", "price": float(r["price"]), "as_of": r.get("updatedAt"),
             "source": "gold-api.com spot XAU/USD (mid)", "url": self.docs}
        db.cache_put("gold:spot", q)
        return q


class Dukascopy(Provider):
    name = "Dukascopy historical prices (1-minute)"
    category = MARKET
    data_delay = "Historical; current day available with ~1–2 h delay"
    docs = "https://www.dukascopy.com/swiss/english/marketwatch/historical/"
    BASE = "https://datafeed.dukascopy.com/datafeed"

    def _fetch_day(self, d: date, quick: bool, inst: str) -> list[tuple]:
        meta = INSTR[inst]
        url = f"{self.BASE}/{meta['duka']}/{d.year}/{d.month - 1:02d}/{d.day:02d}/BID_candles_min_1.bi5"
        last = None
        for attempt in range(1 if quick else 5):
            if time.monotonic() < _cool[0]:
                if quick:
                    raise ProviderError("price history feed is rate-limiting; skipped")
                time.sleep(_cool[0] - time.monotonic())
            with _throttle:
                wait = _gap[0] - (time.monotonic() - _last[0])
                if wait > 0:
                    time.sleep(wait)
                _last[0] = time.monotonic()
            try:
                raw = self.get(url, as_json=False, timeout=40, raw=True)
                _gap[0] = max(FLOOR, _gap[0] * 0.9)  # healthy: speed up gently
                break
            except ProviderError as e:
                if "HTTP 404" in str(e):
                    return []  # no file = no trading that day (weekend / holiday)
                last = e
                if "429" in str(e) or "503" in str(e) or "timed out" in str(e):
                    _gap[0] = min(CEIL, _gap[0] * 2)  # throttled: halve the request rate
                    # the feed is throttling: pause all history requests (shared) instead of retrying into it
                    _cool[0] = time.monotonic() + (300 if "429" in str(e) else 120)
                elif not quick:
                    time.sleep(3 * (attempt + 1))
        else:
            raise last
        if not raw:
            return []
        data = lzma.decompress(raw)
        base = datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()
        out, sc = [], meta["scale"]
        for i in range(len(data) // 24):
            t, o, c, lo, hi, vol = struct.unpack(">5if", data[i * 24:(i + 1) * 24])
            if vol <= 0 and o == c == lo == hi:
                continue  # flat filler minute (market closed)
            out.append((int(base + t), o / sc, hi / sc, lo / sc, c / sc))
        return out

    def day(self, d: date, quick: bool = False, inst: str = "XAUUSD") -> list[tuple]:
        """[(unix_ts, open, high, low, close)] for a UTC day."""
        key = d.isoformat() if inst == "XAUUSD" else f"{inst}:{d.isoformat()}"
        today = datetime.now(timezone.utc).date()
        row = db.row("SELECT data, fetched_at FROM gold_bars WHERE day=?", (key,))
        if row and (d < today - timedelta(days=1) or
                    (db.now_utc() - db.parse_iso(row["fetched_at"])).total_seconds() < 600):
            return [tuple(x) for x in db.jload(row["data"], [])]
        bars = self._fetch_day(d, quick, inst)
        with db.tx() as c:
            c.execute("INSERT OR REPLACE INTO gold_bars(day, data, fetched_at) VALUES(?,?,?)",
                      (key, db.jdump(bars), db.iso(db.now_utc())))
        return bars

    def bars(self, start: datetime, end: datetime, quick: bool = False, inst: str = "XAUUSD") -> list[tuple]:
        out, d = [], start.date()
        while d <= end.date():
            out += [b for b in self.day(d, quick, inst) if start.timestamp() <= b[0] <= end.timestamp()]
            d += timedelta(days=1)
        return out
