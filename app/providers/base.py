"""Provider base: HTTP with timing, health bookkeeping and explicit capability metadata.

Every provider declares what it is (category), whether it is configured, and its
known data delay, so the UI never implies real-time data that is not real-time.
"""
import time

import httpx

from .. import config, db

# Data categories kept explicitly separate (spec §6)
OFFICIAL = "official_release"
CONSENSUS = "consensus_forecast"
HISTORICAL = "historical_macro"
MARKET = "market_price"
NEWS = "news_qualitative"
AI = "ai_interpretation"


class ProviderError(Exception):
    pass


class NotConfigured(ProviderError):
    pass


class Provider:
    name = "base"
    category = HISTORICAL
    requires_key = False
    data_delay = "unknown"
    docs = ""
    notes = ""

    def configured(self) -> bool:
        return True

    def info(self) -> dict:
        return {"name": self.name, "category": self.category, "requires_key": self.requires_key,
                "configured": self.configured(), "data_delay": self.data_delay, "docs": self.docs,
                "notes": self.notes}

    # -- HTTP --------------------------------------------------------------
    def get(self, url: str, params: dict | None = None, timeout: float = 20, as_json=True,
            headers: dict | None = None, raw: bool = False):
        if not self.configured():
            raise NotConfigured(f"{self.name} is not configured (API key missing)")
        h = {"User-Agent": config.USER_AGENT}
        if headers:
            h.update(headers)
        t0 = time.monotonic()
        try:
            r = httpx.get(url, params=params, timeout=timeout, headers=h, follow_redirects=True)
            ms = int((time.monotonic() - t0) * 1000)
            if r.status_code >= 400:
                raise ProviderError(f"HTTP {r.status_code}: {r.text[:200]}")
            out = r.content if raw else (r.json() if as_json else r.text)
            self._ok(ms)
            return out
        except NotConfigured:
            raise
        except Exception as e:  # network, parse, HTTP
            self._err(str(e)[:300])
            raise ProviderError(str(e)) from e

    def post(self, url: str, payload: dict, timeout: float = 20):
        h = {"User-Agent": config.USER_AGENT, "Content-Type": "application/json"}
        t0 = time.monotonic()
        try:
            r = httpx.post(url, json=payload, timeout=timeout, headers=h)
            ms = int((time.monotonic() - t0) * 1000)
            if r.status_code >= 400:
                raise ProviderError(f"HTTP {r.status_code}: {r.text[:200]}")
            self._ok(ms)
            return r.json()
        except Exception as e:
            self._err(str(e)[:300])
            raise ProviderError(str(e)) from e

    # -- health ------------------------------------------------------------
    def _upsert(self):
        with db.tx() as c:
            c.execute("INSERT OR IGNORE INTO provider_health(provider, configured, data_delay, notes) "
                      "VALUES(?,?,?,?)", (self.name, int(self.configured()), self.data_delay, self.notes))

    def _ok(self, ms: int):
        self._upsert()
        with db.tx() as c:
            c.execute("UPDATE provider_health SET last_ok=?, last_latency_ms=?, ok_count=ok_count+1, "
                      "configured=? WHERE provider=?",
                      (db.iso(db.now_utc()), ms, int(self.configured()), self.name))

    def _err(self, msg: str):
        self._upsert()
        with db.tx() as c:
            c.execute("UPDATE provider_health SET last_error=?, last_error_at=?, err_count=err_count+1 "
                      "WHERE provider=?", (msg, db.iso(db.now_utc()), self.name))

    def register(self):
        self._upsert()
        with db.tx() as c:
            c.execute("UPDATE provider_health SET configured=?, data_delay=?, notes=? WHERE provider=?",
                      (int(self.configured()), self.data_delay, self.notes, self.name))


def fact(value, source: str, url: str = "", ts: str | None = None, category: str = HISTORICAL, **extra):
    """Every displayed fact carries its source, timestamp and data category."""
    d = {"value": value, "source": source, "url": url, "as_of": ts or db.iso(db.now_utc()),
         "category": category}
    d.update(extra)
    return d
