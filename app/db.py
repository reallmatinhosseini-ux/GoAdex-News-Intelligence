"""SQLite persistence. One file, WAL mode, safe for the API + worker threads."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,               -- stable: <catalog_key>:<release_utc_iso>
    catalog_key TEXT,                  -- NULL for uncatalogued calendar items
    title TEXT NOT NULL,
    country TEXT NOT NULL,
    category TEXT,
    release_utc TEXT NOT NULL,
    time_basis TEXT,                   -- 'published' | 'agency_standard' | 'approximate'
    impact TEXT,                       -- high | medium | low (contextual, see importance.py)
    impact_reason TEXT,
    source TEXT,                       -- provider that published the schedule
    source_url TEXT,
    discovered_at TEXT NOT NULL,
    late_discovery INTEGER DEFAULT 0,  -- discovered after T-60
    status TEXT DEFAULT 'scheduled',   -- scheduled | researching | report_ready | released | evaluated
    consensus TEXT,                    -- JSON {field: {value, source, ts}}
    previous TEXT,                     -- JSON
    actual TEXT,                       -- JSON
    report_deadline_met INTEGER,       -- NULL until deadline passes; 1/0 afterwards
    report_deadline_note TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_release ON events(release_utc);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    stage TEXT NOT NULL,               -- early | refresh | final | monitor | post_release
    created_at TEXT NOT NULL,
    engine_version TEXT,
    body TEXT NOT NULL,                -- full JSON report (evidence, scenarios, sources)
    gold_bias TEXT, eur_bias TEXT, confidence TEXT,
    revision_reason TEXT,              -- why the conclusion changed vs previous version
    material_change INTEGER DEFAULT 0,
    UNIQUE(event_id, version)
);

CREATE TABLE IF NOT EXISTS observations (  -- cached time series, source-tagged
    series TEXT NOT NULL, provider TEXT NOT NULL, date TEXT NOT NULL,
    value REAL, fetched_at TEXT NOT NULL,
    PRIMARY KEY(series, provider, date)
);

CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT, fetched_at TEXT);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idem_key TEXT UNIQUE NOT NULL,     -- prevents duplicate processing
    kind TEXT NOT NULL,
    event_id TEXT,
    run_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', -- pending | running | done | failed | dead
    attempts INTEGER DEFAULT 0,
    last_error TEXT,
    started_at TEXT, finished_at TEXT, duration_ms INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_jobs_due ON jobs(status, run_at);

CREATE TABLE IF NOT EXISTS provider_health (
    provider TEXT PRIMARY KEY, configured INTEGER, last_ok TEXT, last_error TEXT,
    last_error_at TEXT, last_latency_ms INTEGER, ok_count INTEGER DEFAULT 0,
    err_count INTEGER DEFAULT 0, data_delay TEXT, notes TEXT
);

CREATE TABLE IF NOT EXISTS evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL, report_id INTEGER NOT NULL, asset TEXT NOT NULL,
    window_min INTEGER NOT NULL, predicted TEXT, prob_up REAL,
    price_t0 REAL, price_t1 REAL, ret_bps REAL, realized TEXT,
    correct INTEGER,                   -- NULL for neutral/abstain
    vol_regime TEXT, category TEXT, created_at TEXT,
    UNIQUE(event_id, asset, window_min)
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, event_id TEXT, title TEXT,
    body TEXT, forecast_stage TEXT, created_at TEXT, read INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS alert_prefs (kind TEXT PRIMARY KEY, enabled INTEGER, channels TEXT);

CREATE TABLE IF NOT EXISTS gold_bars (day TEXT PRIMARY KEY, data TEXT, fetched_at TEXT);

CREATE TABLE IF NOT EXISTS calendar (           -- full economic calendar (all impacts)
    id TEXT PRIMARY KEY, title TEXT, country TEXT, release_utc TEXT, impact TEXT,
    forecast TEXT, previous TEXT, actual TEXT, gold_relevant INTEGER, catalog_key TEXT,
    event_id TEXT, source TEXT, fetched_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_cal_rel ON calendar(release_utc);

CREATE TABLE IF NOT EXISTS hist_events (        -- historical release instants for backtesting
    id TEXT PRIMARY KEY, catalog_key TEXT, title TEXT, release_utc TEXT, source TEXT, source_url TEXT
);

CREATE TABLE IF NOT EXISTS reactions (          -- measured gold reaction around a release (Dukascopy)
    event_id TEXT PRIMARY KEY, data TEXT, built_at TEXT
);

CREATE TABLE IF NOT EXISTS predictions (        -- one row per event: live (pre-release) or retrospective
    event_id TEXT NOT NULL, kind TEXT NOT NULL,  -- 'live' | 'retrospective'
    catalog_key TEXT, release_utc TEXT, made_at TEXT, as_of TEXT,
    pred TEXT, outcome TEXT, released TEXT, engine_version TEXT,
    direction TEXT, correct INTEGER, target_hit INTEGER,
    PRIMARY KEY(event_id, kind)
);

CREATE TABLE IF NOT EXISTS features (           -- point-in-time model inputs + measured outcome per release
    event_id TEXT PRIMARY KEY, catalog_key TEXT, release_utc TEXT, version INTEGER, x TEXT, y TEXT, built_at TEXT
);
CREATE TABLE IF NOT EXISTS live_ticks (inst TEXT, ts INTEGER, bid REAL, ask REAL, PRIMARY KEY(inst, ts));
CREATE TABLE IF NOT EXISTS model_state (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS research_feed (
    id TEXT PRIMARY KEY, kind TEXT, title TEXT, url TEXT, source TEXT,
    published_at TEXT, summary TEXT, fetched_at TEXT
);
"""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        _local.conn = c
    return c


@contextmanager
def tx():
    c = conn()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise


MIGRATIONS = [
    "ALTER TABLE research_feed ADD COLUMN tags TEXT", "ALTER TABLE research_feed ADD COLUMN score INTEGER",
    "ALTER TABLE research_feed ADD COLUMN impact TEXT", "ALTER TABLE research_feed ADD COLUMN assets TEXT",
    "ALTER TABLE research_feed ADD COLUMN related_event TEXT", "ALTER TABLE calendar ADD COLUMN actual_source TEXT",
    "CREATE INDEX IF NOT EXISTS ix_feed_pub ON research_feed(published_at)",
]


def init():
    with tx() as c:
        c.executescript(SCHEMA)
        for m in MIGRATIONS:
            try:
                c.execute(m)
            except sqlite3.OperationalError:
                pass  # already applied


def rows(sql: str, args=()) -> list[dict]:
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def row(sql: str, args=()) -> dict | None:
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None


def jdump(o) -> str:
    return json.dumps(o, default=str, separators=(",", ":"))


def jload(s, default=None):
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


# ---- small key/value cache with TTL (used to respect provider rate limits) ----
def cache_get(key: str, max_age_s: int):
    r = row("SELECT value, fetched_at FROM cache WHERE key=?", (key,))
    if not r:
        return None
    if (now_utc() - parse_iso(r["fetched_at"])).total_seconds() > max_age_s:
        return None
    return jload(r["value"])


def cache_get_stale(key: str):
    r = row("SELECT value, fetched_at FROM cache WHERE key=?", (key,))
    return (jload(r["value"]), r["fetched_at"]) if r else (None, None)


def cache_put(key: str, value):
    with tx() as c:
        c.execute("INSERT OR REPLACE INTO cache(key,value,fetched_at) VALUES(?,?,?)",
                  (key, jdump(value), iso(now_utc())))
