"""Durable job scheduler and worker.

- Jobs live in SQLite (survive restarts); `idem_key` is UNIQUE so the same stage
  for the same event can never be queued or processed twice.
- A planner job runs every 10 minutes: syncs calendars and enqueues the research
  timeline for each pipeline event. The analytical engine runs server-side,
  independent of any browser.
- Failures retry with exponential backoff, then go 'dead' and are surfaced.
- The T-60 deadline is checked and recorded honestly (met / missed / late discovery).
"""
import logging
import threading
import time
import traceback
from datetime import timedelta

from . import catalog, config, db
from .engine import backtest, discovery, features, quant, regime, report, stages
from .providers import registry as R
from .providers.base import NotConfigured, ProviderError

log = logging.getLogger("scheduler")
MAX_ATTEMPTS = 5


class Retry(Exception):
    """Not an error: reschedule (e.g. actual data not published yet)."""
    def __init__(self, delay_s: int, note: str = ""):
        self.delay_s, self.note = delay_s, note


def enqueue(kind: str, idem: str, run_at, event_id: str | None = None) -> bool:
    with db.tx() as c:
        cur = c.execute("INSERT OR IGNORE INTO jobs(idem_key, kind, event_id, run_at, created_at) VALUES(?,?,?,?,?)",
                        (idem, kind, event_id, db.iso(run_at), db.iso(db.now_utc())))
        return cur.rowcount > 0


# ----------------------------------------------------------------- planner
def plan():
    last = db.cache_get("last_sync", 25 * 60)
    if last is None:
        summary = discovery.sync()
        db.cache_put("last_sync", {"at": db.iso(db.now_utc()), "summary": summary})
    now = db.now_utc()
    evs = db.rows("SELECT * FROM events WHERE release_utc BETWEEN ? AND ? AND impact IN ('high','medium')",
                  (db.iso(now - timedelta(hours=2)), db.iso(now + timedelta(hours=config.STAGE_EARLY_HOURS + 24))))
    for ev in evs:
        if ev["catalog_key"] in (None, "US_FED_SPEECH") and ev["impact"] != "high":
            continue
        rel = db.parse_iso(ev["release_utc"])
        eid = ev["id"]
        to_rel = rel - now
        if to_rel > timedelta(0):
            if to_rel > timedelta(hours=config.STAGE_REFRESH_HOURS):
                enqueue("research_early", f"{eid}:early", max(now, rel - timedelta(hours=config.STAGE_EARLY_HOURS)), eid)
            if to_rel > timedelta(hours=config.STAGE_DAY_HOURS):
                enqueue("research_refresh", f"{eid}:day", rel - timedelta(hours=config.STAGE_DAY_HOURS), eid)
            if to_rel > timedelta(minutes=config.FINAL_REPORT_LEAD_MIN + 5):
                enqueue("research_refresh", f"{eid}:refresh", max(now, rel - timedelta(hours=config.STAGE_REFRESH_HOURS)), eid)
            enqueue("snapshot_previous", f"{eid}:prev", max(now, rel - timedelta(hours=3)), eid)
            enqueue("research_final", f"{eid}:final", max(now, rel - timedelta(minutes=config.FINAL_REPORT_LEAD_MIN)), eid)
            enqueue("deadline_check", f"{eid}:deadline", rel - timedelta(minutes=config.REPORT_DEADLINE_MIN), eid)
            if ev["impact"] == "high":
                m = config.REPORT_DEADLINE_MIN - config.MONITOR_INTERVAL_MIN
                while m >= 5:
                    enqueue("monitor", f"{eid}:mon:{m}", rel - timedelta(minutes=m), eid)
                    m -= config.MONITOR_INTERVAL_MIN
        if to_rel > timedelta(minutes=2) and ev["catalog_key"] not in ("US_FOMC_PRESSER",):
            # Two prediction stages (time-critical; run on the dedicated stage thread)
            if to_rel > timedelta(minutes=33):
                enqueue("stage_prep", f"{eid}:prep", rel - timedelta(minutes=33), eid)   # fresh evidence for T-30
            if to_rel > timedelta(minutes=30):
                enqueue("stage_t30", f"{eid}:t30", rel - timedelta(minutes=30), eid)
            if to_rel > timedelta(minutes=7):
                enqueue("stage_prep", f"{eid}:prep2", rel - timedelta(minutes=7), eid)
            enqueue("stage_t2", f"{eid}:t2", rel - timedelta(minutes=2), eid)
            enqueue("stage_score", f"{eid}:score", rel + timedelta(minutes=62), eid)
        if ev["catalog_key"] not in (None, "US_FED_SPEECH", "US_FOMC_PRESSER", "US_FOMC_MINUTES", "US_BEIGE"):
            enqueue("release_fetch", f"{eid}:actual", rel + timedelta(seconds=20), eid)
    # Weekly event-study builds (need FRED vintages + prices)
    if R.fred.configured() and R.prices.configured():
        wk = now.strftime("%G-W%V")
        for key, d in catalog.CATALOG.items():
            if d.get("tier") in (1, 2) and d.get("series") and "alias_of" not in d:
                enqueue("event_study", f"study:{key}:{wk}", now, key)
    enqueue("backtest", f"bt:{now.strftime('%Y%m%d%H')}", now)
    enqueue("live_hist", f"lh:{now.strftime('%Y%m%d%H')}:{now.minute // 20}", now)
    enqueue("plan", f"plan:{now.strftime('%Y%m%d%H')}:{now.minute // 10 + 1}", now + timedelta(minutes=10))


def deadline_check(eid: str):
    ev = db.row("SELECT * FROM events WHERE id=?", (eid,))
    deadline = db.parse_iso(ev["release_utc"]) - timedelta(minutes=config.REPORT_DEADLINE_MIN)
    rep = db.row("SELECT stage, created_at FROM reports WHERE event_id=? AND created_at<=? ORDER BY created_at DESC LIMIT 1",
                 (eid, db.iso(deadline)))
    final = rep and rep["stage"] in ("final", "monitor")
    if final:
        met, note = 1, f"Final report available at {rep['created_at']}"
    elif rep:
        met, note = 0, f"Only a {rep['stage']} report was available at T-60 (final report not completed in time)"
    elif ev["late_discovery"]:
        met, note = 0, "Late discovery: event was first seen less than 60 minutes before release"
    else:
        met, note = 0, "No report available at T-60 (job failure or provider outage)"
    with db.tx() as c:
        c.execute("UPDATE events SET report_deadline_met=?, report_deadline_note=? WHERE id=?", (met, note, eid))


def run_job(job: dict):
    k, eid = job["kind"], job["event_id"]
    if k == "plan":
        plan()
    elif k == "research_early":
        report.build(eid, "early")
    elif k == "research_refresh":
        report.build(eid, "refresh")
    elif k == "research_final":
        report.build(eid, "final")
    elif k == "monitor":
        report.build(eid, "monitor")
    elif k == "snapshot_previous":
        report.snapshot_previous(eid)
    elif k == "deadline_check":
        deadline_check(eid)
    elif k == "release_fetch":
        got = report.fetch_actual(eid)
        if not got:
            if job["attempts"] > 40:
                return  # give up quietly after ~2h; surfaced as missing actual
            raise Retry(30 if job["attempts"] < 20 else 300, "actual not yet available from authorised sources")
        enqueue("post_report", f"{eid}:post", db.now_utc(), eid)
    elif k == "post_report":
        report.build(eid, "post_release")
    elif k == "backtest":
        res = backtest.run(max_events=25)
        if res["candidates"] > res["processed"]:
            # keep going immediately while making progress; otherwise (feed blocked) retry in 10 min
            delay = timedelta(seconds=5) if res["processed"] else timedelta(minutes=10)
            enqueue("backtest", f"bt:cont:{db.iso(db.now_utc())}", db.now_utc() + delay)
    elif k == "live_hist":
        # keep today's real 1-minute history cached (fills volatility windows before live recording began)
        for inst in features.ASSETS:
            for d in {db.now_utc().date(), (db.now_utc() - timedelta(hours=4)).date()}:
                try:
                    R.duka.day(d, quick=True, inst=inst)
                except Exception:
                    pass
    elif k == "stage_prep":
        report.build(eid, "monitor")
    elif k in ("stage_t30", "stage_t2"):
        stages.run(eid, k.split("_")[1])
    elif k == "stage_score":
        if not stages.score(eid):
            if job["attempts"] > 30:
                return
            raise Retry(600, "price data for the release window not available yet")
    elif k == "event_study":
        for a in ("XAUUSD", "EURUSD"):
            quant.build_event_study(eid, a)
    else:
        raise ValueError(f"unknown job kind {k}")


CRITICAL = ("stage_t2", "stage_t30", "stage_prep", "release_fetch")


def _claim(critical: bool = False) -> dict | None:
    now = db.iso(db.now_utc())
    flt = ("AND kind IN (%s)" % ",".join(f"'{k}'" for k in CRITICAL)) if critical else ""
    with db.tx() as c:
        r = c.execute("SELECT * FROM jobs WHERE status IN ('pending','failed') AND run_at<=? " + flt +
                      " ORDER BY CASE kind WHEN 'stage_t2' THEN 0 WHEN 'stage_t30' THEN 1 WHEN 'release_fetch' THEN 2 "
                      "WHEN 'stage_prep' THEN 3 WHEN 'research_final' THEN 4 WHEN 'deadline_check' THEN 5 "
                      "WHEN 'monitor' THEN 6 ELSE 9 END, run_at LIMIT 1", (now,)).fetchone()
        if not r:
            return None
        cur = c.execute("UPDATE jobs SET status='running', started_at=?, attempts=attempts+1 WHERE id=? "
                        "AND status IN ('pending','failed')", (now, r["id"]))
        if cur.rowcount == 0:
            return None
        d = dict(r)
        d["attempts"] += 1
        return d


def _finish(job, status, err=None, run_at=None):
    t1 = db.now_utc()
    started = db.row("SELECT started_at FROM jobs WHERE id=?", (job["id"],))["started_at"]
    dur = int((t1 - db.parse_iso(started)).total_seconds() * 1000)
    with db.tx() as c:
        c.execute("UPDATE jobs SET status=?, last_error=?, finished_at=?, duration_ms=?, run_at=COALESCE(?, run_at) "
                  "WHERE id=?", (status, err, db.iso(t1), dur, run_at, job["id"]))


class Worker:
    def __init__(self, n_threads=2):
        self.stop = threading.Event()
        self.threads = []
        self.n = n_threads

    def start(self):
        db.init()
        with db.tx() as c:  # crash recovery: re-queue jobs left 'running'
            c.execute("UPDATE jobs SET status='pending' WHERE status='running'")
        enqueue("plan", f"plan:boot:{db.iso(db.now_utc())}", db.now_utc())
        for i in range(self.n):
            t = threading.Thread(target=self._loop, name=f"worker-{i}", daemon=True)
            t.start()
            self.threads.append(t)
        # Dedicated thread for time-critical stage jobs (T-30, T-2, release fetch): never queued behind long jobs.
        t = threading.Thread(target=self._loop, args=(True,), name="stage-worker", daemon=True)
        t.start()
        self.threads.append(t)

    def _loop(self, critical: bool = False):
        while not self.stop.is_set():
            job = None
            try:
                job = _claim(critical)
                if not job:
                    time.sleep(0.5 if critical else 3)
                    continue
                run_job(job)
                _finish(job, "done")
            except Retry as r:
                _finish(job, "pending", r.note, db.iso(db.now_utc() + timedelta(seconds=r.delay_s)))
            except NotConfigured as e:
                _finish(job, "done", f"skipped: {e}")
            except Exception as e:
                tb = traceback.format_exc(limit=3)
                log.error("job %s failed: %s", job and job["kind"], tb)
                if job:
                    if job["attempts"] >= MAX_ATTEMPTS:
                        _finish(job, "dead", f"{e}\n{tb}"[:1500])
                    else:
                        backoff = 60 * 2 ** (job["attempts"] - 1)
                        _finish(job, "failed", f"{e}"[:1500], db.iso(db.now_utc() + timedelta(seconds=backoff)))
                else:
                    time.sleep(5)


def status() -> dict:
    counts = {r["status"]: r["n"] for r in db.rows("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}
    recent = db.rows("SELECT kind, event_id, status, attempts, last_error, run_at, finished_at, duration_ms "
                     "FROM jobs ORDER BY COALESCE(finished_at, run_at) DESC LIMIT 40")
    upcoming = db.rows("SELECT kind, event_id, run_at FROM jobs WHERE status IN ('pending','failed') "
                       "ORDER BY run_at LIMIT 30")
    last_ok = db.row("SELECT MAX(finished_at) t FROM jobs WHERE status='done'")
    sync = db.cache_get_stale("last_sync")[0]
    return {"counts": counts, "recent": recent, "upcoming": upcoming,
            "last_successful_job": last_ok["t"] if last_ok else None, "last_calendar_sync": sync}
