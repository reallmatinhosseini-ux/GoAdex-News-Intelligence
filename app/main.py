"""HTTP API (two read-only endpoints) + the web page. Starts the background worker on boot.

There is nothing to trigger by hand: the scheduler discovers events and researches them
automatically; the page only displays results.
"""
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import auth, catalog, config, db, recorder, scheduler
from .engine import newsfeed
from .engine.report import summarize
from .providers import registry as R

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
WEB = config.ROOT / "web"
worker = scheduler.Worker(n_threads=2)


log = logging.getLogger("main")
_ready = False
UNAVAILABLE = "Sign-ups are temporarily unavailable. Please try again a little later."


def _init_storage():
    global _ready
    if _ready:
        return
    db.init()
    auth.init()
    try:
        R.register_all()
    except Exception as e:  # provider bookkeeping must never stop the site from serving
        log.warning("provider registration failed: %s", e)
    _ready = True


def _snapshot() -> dict:
    """Public figures published from the main (always-on) instance; used when running serverless."""
    import json
    try:
        return json.loads(config.SNAPSHOT_PATH.read_text())
    except Exception:
        return {}


@asynccontextmanager
async def lifespan(app):
    _init_storage()
    if not config.SERVERLESS:  # background engine needs a long-running process
        worker.start()
        recorder.start(worker.stop)
        newsfeed.start(worker.stop)
    yield
    worker.stop.set()


app = FastAPI(title="GoAdex News Intelligence", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

PUBLIC_API = {"/api/auth/login", "/api/auth/me", "/api/waitlist", "/api/public/spot", "/api/public/overview",
              "/api/public/metrics", "/api/public/calendar", "/api/investor-interest"}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
       "base-uri 'self'; form-action 'self'")


LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _via_local_proxy(request: Request) -> bool:
    """True when the request arrived through a proxy on this machine (cloudflared connects from loopback)."""
    return bool(request.client and request.client.host in LOOPBACK)


def _client_ip(request: Request) -> str:
    # Behind Cloudflare Tunnel every request comes from 127.0.0.1; the real visitor is in CF-Connecting-IP.
    # Trust that header only from the local proxy, so remote clients cannot spoof it.
    if _via_local_proxy(request):
        cf = request.headers.get("cf-connecting-ip", "").strip()
        if cf:
            return cf[:64]
    return request.client.host if request.client else "unknown"


def _is_https(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    return _via_local_proxy(request) and request.headers.get("x-forwarded-proto", "").lower() == "https"


def _csp() -> str:
    if config.SERVERLESS and config.ADMIN_URL:
        return CSP.replace("connect-src 'self'", f"connect-src 'self' {config.ADMIN_URL}")
    return CSP


FORM_PATHS = {"/api/waitlist", "/api/investor-interest"}


def _cors_origin(request: Request) -> str | None:
    o = (request.headers.get("origin") or "").rstrip("/")
    return o if o and o in config.ALLOWED_ORIGINS and request.url.path in FORM_PATHS else None


@app.middleware("http")
async def guard(request: Request, call_next):
    if not _ready:
        _init_storage()
    origin = _cors_origin(request)
    if request.method == "OPTIONS" and origin:
        from fastapi.responses import Response
        return Response(status_code=204, headers={
            "Access-Control-Allow-Origin": origin, "Access-Control-Allow-Methods": "POST",
            "Access-Control-Allow-Headers": "Content-Type, X-Requested-With", "Access-Control-Max-Age": "600", "Vary": "Origin"})
    path = request.url.path
    if path.startswith("/api/"):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            # CSRF: cross-site forms can't send JSON with a custom header
            if request.headers.get("x-requested-with") != "fetch" or "application/json" not in request.headers.get("content-type", ""):
                return JSONResponse({"detail": "Bad request"}, status_code=400)
        if path not in PUBLIC_API and not auth.session_user(request.cookies.get(auth.SESSION_COOKIE)):
            return JSONResponse({"detail": "Login required"}, status_code=401)
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    if origin:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
    resp.headers["Content-Security-Policy"] = _csp()
    if path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def _stages(event_id: str, full: bool = False) -> dict:
    out = {"t30": {}, "t2": {}}
    for r in db.rows("SELECT kind, pred, outcome FROM predictions WHERE event_id=? AND kind LIKE 'live_%' AND engine_version=?",
                     (event_id, config.ENGINE_VERSION)):
        stage, asset = r["kind"][5:].split(":")
        p = db.jload(r["pred"])
        if not full:
            for k in ("factors", "steps", "validation", "analogs", "features", "research_log", "definitions"):
                p.pop(k, None)
        p["outcome"] = db.jload(r["outcome"])
        out[stage][asset] = p
    return out


def _card(e: dict, full: bool = False) -> dict:
    """Compact view of an event for the homepage."""
    rep = db.row("SELECT body, created_at, stage FROM reports WHERE event_id=? ORDER BY version DESC LIMIT 1",
                 (e["id"],))
    T = db.parse_iso(e["release_utc"])
    out = {"id": e["id"], "title": e["title"], "country": e["country"], "impact": e["impact"],
           "release_utc": e["release_utc"], "time_basis": e["time_basis"], "late_discovery": bool(e["late_discovery"]),
           "ready": False, "t30_at": db.iso(T - timedelta(minutes=30)), "t2_at": db.iso(T - timedelta(minutes=2)),
           "stages": _stages(e["id"], full), "consensus": db.jload(e["consensus"]), "actual": db.jload(e["actual"]),
           "modelled": e["catalog_key"] in __import__("app.engine.features", fromlist=["TYPES"]).TYPES}
    if rep:
        b = db.jload(rep["body"], {})
        out.update(ready=True, updated_at=rep["created_at"], summary=summarize(b) if b.get("assets") else None)
    return out


@app.get("/api/home")
def home():
    now = db.now_utc()
    # Only events the pipeline actually researches (minor Fed speeches are skipped).
    evs = db.rows("SELECT * FROM events WHERE release_utc > ? AND release_utc < ? AND impact IN ('high','medium') "
                  "AND NOT (catalog_key='US_FED_SPEECH' AND impact!='high') AND catalog_key != 'US_FOMC_PRESSER' ORDER BY release_utc LIMIT 24",
                  (db.iso(now - timedelta(minutes=30)), db.iso(now + timedelta(days=21))))
    reg = db.cache_get_stale("regime")[0] or {}
    s = reg.get("signals", {})
    backdrop = []
    if s.get("inflation"):
        backdrop.append({"label": "Core inflation", "value": f"{s['inflation']['core_cpi_yoy']['value']}%", "note": s["inflation"]["label"]})
    if s.get("labor"):
        backdrop.append({"label": "Unemployment", "value": f"{s['labor']['unemployment']['value']}%", "note": s["labor"]["label"]})
    r = s.get("rates", {})
    if r.get("us2y"):
        backdrop.append({"label": "US 2-year yield", "value": f"{r['us2y']['value']}%", "note": (r.get("policy_pricing") or {}).get("label", "")})
    if r.get("real10y"):
        backdrop.append({"label": "Real 10-year yield", "value": f"{r['real10y']['value']}%", "note": r.get("real_rate_label", "")})
    last = db.row("SELECT MAX(created_at) t FROM reports")
    spot = spot_api()
    cal = db.rows("SELECT c.*, e.actual AS ev_actual, e.impact AS ev_impact, "
                  "(SELECT 1 FROM reports r WHERE r.event_id=c.event_id LIMIT 1) AS analysed FROM calendar c "
                  "LEFT JOIN events e ON e.id=c.event_id WHERE c.release_utc >= ? AND c.release_utc < ? "
                  "ORDER BY c.release_utc, c.impact='High' DESC",
                  (db.iso(now - timedelta(days=now.weekday() + 1)), db.iso(now + timedelta(days=14))))
    for c in cal:
        a = db.jload(c.pop("ev_actual")) or {}
        from .providers.calendar_ff import FF_MAP
        m = FF_MAP.get(c["title"])
        if not c["actual"] and m and a.get(m[1]):
            c["actual"] = str(a[m[1]]["value"])
    return {"now": db.iso(now), "timezone": config.DEFAULT_TIMEZONE, "events": [_card(e) for e in evs],
            "backdrop": backdrop, "regime": reg.get("headline"), "last_analysis": last["t"] if last else None,
            "spot": spot, "calendar": cal}


@app.get("/api/spot")
def spot_api():
    out = {}
    for inst in ("XAUUSD", "EURUSD"):
        try:
            out[inst] = R.live.quote(inst)
        except Exception as e:
            out[inst] = R.gold_spot.spot() if inst == "XAUUSD" else {"error": str(e)}
    return out


@app.get("/api/calendar")
def calendar_api(start: str | None = None, days: int = 7):
    """Economic calendar for a date range: the full weekly calendar feed (all impacts, consensus)
    merged with official schedules (Fed, BEA, FRED release dates) for events the feed doesn't cover."""
    from .providers.calendar_ff import FF_MAP
    now = db.now_utc()
    if start:
        s0 = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    else:
        s0 = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    s1 = s0 + timedelta(days=max(1, min(days, 31)))
    rows = db.rows("SELECT c.*, e.actual AS ev_actual FROM calendar c LEFT JOIN events e ON e.id=c.event_id "
                   "WHERE c.release_utc >= ? AND c.release_utc < ? ORDER BY c.release_utc", (db.iso(s0), db.iso(s1)))
    out, linked = [], set()
    for c in rows:
        a = db.jload(c.pop("ev_actual")) or {}
        m = FF_MAP.get(c["title"])
        if not c["actual"] and m and a.get(m[1]):
            c["actual"] = str(a[m[1]]["value"])
        c["impact"] = (c["impact"] or "").lower()
        out.append(c)
        if c["event_id"]:
            linked.add(c["event_id"])
    # official-schedule events outside the feed's coverage (e.g. next month's CPI / NFP / FOMC)
    for e in db.rows("SELECT * FROM events WHERE release_utc >= ? AND release_utc < ? AND catalog_key IS NOT NULL "
                     "AND catalog_key != 'US_FED_SPEECH'", (db.iso(s0), db.iso(s1))):
        if e["id"] in linked:
            continue
        prev, act, cons = db.jload(e["previous"]) or {}, db.jload(e["actual"]) or {}, db.jload(e["consensus"]) or {}
        pick = lambda d: str(next(iter(d.values())).get("value")) if d else ""
        out.append({"id": e["id"], "title": e["title"], "country": "EUR" if e["country"] == "EA" else "USD",
                    "release_utc": e["release_utc"], "impact": e["impact"] or "low", "forecast": pick(cons),
                    "previous": pick(prev), "actual": pick(act), "gold_relevant": 1, "catalog_key": e["catalog_key"],
                    "event_id": e["id"], "source": e["source"]})
    out.sort(key=lambda r: r["release_utc"])
    stages = {}
    ids = sorted({r["event_id"] for r in out if r.get("event_id")})
    sql = ("SELECT event_id, kind, pred FROM predictions WHERE kind LIKE 'live_%' AND engine_version=? AND event_id IN ("
           + (",".join("?" * len(ids)) or "''") + ")")
    for r in db.rows(sql, (config.ENGINE_VERSION, *ids)):
        p = db.jload(r["pred"])
        st, a = r["kind"][5:].split(":")
        stages.setdefault(r["event_id"], {}).setdefault(st, {})[a] = {
            k: p.get(k) for k in ("status", "scenario", "scenario_name", "first", "final", "two_way", "confidence")}
    analysed = {r["event_id"] for r in db.rows("SELECT DISTINCT event_id FROM reports")}
    for r in out:
        eid = r.get("event_id")
        r["stages"] = stages.get(eid) if eid else None
        r["analysed"] = bool(eid and eid in analysed)
    return {"start": db.iso(s0), "end": db.iso(s1), "rows": out, "timezone": config.DEFAULT_TIMEZONE,
            "coverage": "Full calendar (all impacts, consensus) covers this week and next; later weeks show official schedules only."}


@app.get("/api/news")
def news_api(tag: str | None = None, q: str | None = None, hours: int = 72, impact: str | None = None, limit: int = 300):
    now = db.now_utc()
    sql, args = "SELECT * FROM research_feed WHERE published_at >= ? AND tags IS NOT NULL", [db.iso(now - timedelta(hours=max(1, min(hours, 336))))]
    if tag:
        sql += " AND tags LIKE ?"
        args.append(f'%"{tag}"%')
    if impact:
        sql += " AND impact=?"
        args.append(impact)
    if q:
        sql += " AND (title LIKE ? OR summary LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    rows = db.rows(sql + " ORDER BY published_at DESC LIMIT ?", (*args, min(limit, 500)))
    titles = {}
    for r in rows:
        r["tags"], r["assets"] = db.jload(r["tags"], []), db.jload(r["assets"], [])
        if r["related_event"]:
            if r["related_event"] not in titles:
                ev = db.row("SELECT title FROM events WHERE id=?", (r["related_event"],))
                titles[r["related_event"]] = ev["title"] if ev else None
            r["related_title"] = titles[r["related_event"]]
    st = db.cache_get_stale("news_status")[0] or {}
    return {"items": rows, "status": st, "now": db.iso(now)}


@app.get("/api/status")
def status_api():
    """Readiness and health: history, model validation, live recorder, feeds, schedule."""
    import time as _t
    from .engine import features as F
    types = F.TYPES
    ph = ",".join("?" * len(types))
    total = db.row(f"SELECT COUNT(*) n FROM hist_events WHERE release_utc >= '2019-01-01' AND release_utc < ? AND catalog_key IN ({ph})",
                   (db.iso(db.now_utc()), *types))["n"]
    loaded = db.row("SELECT COUNT(*) n FROM features WHERE version=?", (F.FEATURE_VERSION,))["n"]
    by_type = {r["catalog_key"]: r["n"] for r in db.rows("SELECT catalog_key, COUNT(*) n FROM features WHERE version=? GROUP BY 1",
                                                          (F.FEATURE_VERSION,))}
    models = {}
    for a in F.ASSETS:
        for st in ("t30", "t2"):
            sm = db.row("SELECT value FROM model_state WHERE key=?", (f"summary:{a}:{st}",))
            g = (db.jload(sm["value"], {}) if sm else {}).get("gates", {})
            models[f"{a}:{st}"] = {t: {"valid": (g.get(t) or {}).get("valid", False), "n": (g.get(t) or {}).get("n", 0)}
                                   for t in ("final", "initial", "reversal")}
    now = int(_t.time())
    rec = {}
    for a in F.ASSETS:
        ts = [r["ts"] for r in db.rows("SELECT ts FROM live_ticks WHERE inst=? AND ts >= ? ORDER BY ts", (a, now - 86400))]
        gaps = [{"from": db.iso(datetime.fromtimestamp(x, timezone.utc)), "minutes": round((y - x) / 60)}
                for x, y in zip(ts, ts[1:]) if y - x > 180]
        rec[a] = {"last_tick_age_s": (now - ts[-1]) if ts else None, "minutes_3h": len({t // 60 for t in ts if t >= now - 10800}),
                  "gaps_24h": gaps[-5:], "gap_minutes_24h": sum(g["minutes"] for g in gaps)}
    feeds = db.rows("SELECT provider, configured, last_ok, last_error, last_error_at, ok_count, err_count FROM provider_health "
                    "WHERE provider IN ('Live quotes (Swissquote bid/ask)', 'Dukascopy historical prices (1-minute)', 'FRED / ALFRED', "
                    "'Economic calendar (Forex Factory weekly feed)', 'BLS Public Data API', 'US Treasury yield curve', 'ECB Data Portal', "
                    "'CFTC Commitments of Traders', 'Twelve Data (market prices)')")
    nxt = db.row("SELECT kind, event_id, run_at FROM jobs WHERE kind IN ('stage_t30','stage_t2') AND status='pending' ORDER BY run_at LIMIT 1")
    last = db.row("SELECT MAX(finished_at) t FROM jobs WHERE status='done'")
    from .providers.gold import _gap
    return {"history": {"loaded": loaded, "total": total, "by_type": by_type, "pace_s": round(_gap[0], 1)},
            "models": models, "recorder": rec, "feeds": feeds, "next_stage": nxt, "last_job": last["t"] if last else None,
            "awake_guard": True}


@app.get("/api/backtest")
def backtest_list(asset: str = "XAUUSD", stage: str = "t30", source: str | None = None, key: str | None = None):
    from .engine import backtest
    kinds = [f"{src}_{stage}:{asset}" for src in (("live", "retro") if not source else (source,))]
    sql = ("SELECT p.event_id, p.kind, p.catalog_key, p.release_utc, p.direction, p.correct, p.target_hit, p.pred, "
           "p.outcome, COALESCE(h.title, e.title) AS title FROM predictions p LEFT JOIN hist_events h ON h.id=p.event_id "
           "LEFT JOIN events e ON e.id=p.event_id WHERE p.outcome IS NOT NULL AND p.engine_version=? AND p.kind IN (%s)"
           % ",".join("?" * len(kinds)))
    args = [config.ENGINE_VERSION, *kinds]
    if key:
        sql += " AND p.catalog_key=?"
        args.append(key)
    rows = []
    for r in db.rows(sql + " ORDER BY p.release_utc DESC LIMIT 1000", args):
        p, o = db.jload(r.pop("pred")), db.jload(r.pop("outcome"))
        r.update(status=p.get("status"), scenario=p.get("scenario"), scenario_name=p.get("scenario_name"),
                 first=p.get("first"), final=p.get("final"), confidence=p.get("confidence"), reason=p.get("reason"),
                 actual_scenario=(o.get("labels") or {}).get("scenario"), final_correct=o.get("final_correct"),
                 initial_correct=o.get("initial_correct"), scenario_correct=o.get("scenario_correct"),
                 inconclusive=o.get("inconclusive"), move60=o.get("actual_units_60"),
                 move5=(o.get("horizons") or {}).get("5", {}).get("units"),
                 p_final=(p.get("probabilities") or {}).get("final"), p_initial=(p.get("probabilities") or {}).get("initial"),
                 p_reversal=(p.get("probabilities") or {}).get("reversal"), shadow=o.get("shadow"))
        rows.append(r)
    names = {k: catalog.resolve(k).get("name", k) for k in
             {r["catalog_key"] for r in db.rows("SELECT DISTINCT catalog_key FROM predictions")}}
    return {"stats": backtest.stats(asset, stage, source, key), "events": rows, "types": names}


@app.get("/api/backtest/{event_id:path}")
def backtest_detail(event_id: str, asset: str = "XAUUSD", stage: str = "t30"):
    rows = db.rows("SELECT * FROM predictions WHERE event_id=? AND kind IN (?,?) AND outcome IS NOT NULL "
                   "ORDER BY kind LIKE 'live%' DESC", (event_id, f"live_{stage}:{asset}", f"retro_{stage}:{asset}"))
    if not rows:
        raise HTTPException(404)
    r = rows[0]
    h = db.row("SELECT * FROM hist_events WHERE id=?", (event_id,)) or db.row("SELECT * FROM events WHERE id=?", (event_id,)) or {}
    rx = db.row("SELECT data FROM reactions WHERE event_id=?", (event_id if asset == "XAUUSD" else f"{event_id}|{asset}",))
    return {"event_id": event_id, "kind": r["kind"], "asset": asset, "stage": stage, "catalog_key": r["catalog_key"],
            "name": catalog.resolve(r["catalog_key"]).get("name"), "title": h.get("title"),
            "release_utc": r["release_utc"], "source": h.get("source"), "source_url": h.get("source_url"),
            "made_at": r["made_at"], "as_of": r["as_of"], "pred": db.jload(r["pred"]), "outcome": db.jload(r["outcome"]),
            "released": db.jload(r["released"]) or {}, "reaction": db.jload(rx["data"]) if rx else None,
            "engine_version": r["engine_version"]}


def _with_summary(b):
    b["summary"] = summarize(b)
    return b


@app.get("/api/events/{event_id:path}")
def event_detail(event_id: str):
    ev = db.row("SELECT * FROM events WHERE id=?", (event_id,))
    if not ev:
        raise HTTPException(404)
    latest = db.row("SELECT body, version, created_at FROM reports WHERE event_id=? ORDER BY version DESC LIMIT 1",
                    (event_id,))
    card = _card(ev, full=True)
    return {"event": card, "source": ev["source"], "source_url": ev["source_url"],
            "deadline_met": ev["report_deadline_met"], "deadline_note": ev["report_deadline_note"],
            "previous": db.jload(ev["previous"]), "actual": db.jload(ev["actual"]),
            "report": _with_summary(db.jload(latest["body"])) if latest else None,
            "history": db.rows("SELECT version, stage, created_at, gold_bias, confidence, revision_reason, "
                               "material_change FROM reports WHERE event_id=? ORDER BY version DESC", (event_id,))}


app.mount("/static", StaticFiles(directory=WEB), name="static")


def _page(name: str) -> HTMLResponse:
    v = int(max((WEB / f).stat().st_mtime for f in ("app.js", "styles.css", "landing.js", "landing.css", "login.js")
                if (WEB / f).exists()))
    html = (WEB / name).read_text()
    for f in ("app.js", "styles.css", "landing.js", "landing.css", "login.js", "investors.js", "site.js"):
        html = html.replace(f"/static/{f}", f"/static/{f}?v={v}")
    if config.SERVERLESS and config.ADMIN_URL:
        html = html.replace("<head>", f'<head>\n  <meta name="goadex-api" content="{config.ADMIN_URL}">', 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    from fastapi.responses import FileResponse
    return FileResponse(WEB / "favicon.ico", media_type="image/x-icon", headers={"Cache-Control": "public, max-age=604800"})


@app.get("/apple-touch-icon.png", include_in_schema=False)
@app.get("/apple-touch-icon-precomposed.png", include_in_schema=False)
def apple_icon():
    from fastapi.responses import FileResponse
    return FileResponse(WEB / "apple-touch-icon.png", media_type="image/png", headers={"Cache-Control": "public, max-age=604800"})


@app.get("/")
def landing():
    return _page("landing.html")


@app.get("/investors")
def investors_page():
    return _page("investors.html")


@app.get("/login")
def login_page(request: Request):
    if config.SERVERLESS and config.ADMIN_URL:
        return RedirectResponse(config.ADMIN_URL + "/login", status_code=307)
    if auth.session_user(request.cookies.get(auth.SESSION_COOKIE)):
        return RedirectResponse("/app", status_code=303)
    return _page("login.html")


@app.get("/app")
def app_page(request: Request):
    if config.SERVERLESS and config.ADMIN_URL:
        return RedirectResponse(config.ADMIN_URL + "/app", status_code=307)
    if not auth.session_user(request.cookies.get(auth.SESSION_COOKIE)):
        return RedirectResponse("/login", status_code=303)
    return _page("index.html")


@app.post("/api/auth/login")
def api_login(request: Request, email: str = Body(...), password: str = Body(...)):
    try:
        token = auth.login(email, password, _client_ip(request), request.headers.get("user-agent", ""))
    except PermissionError as e:
        return JSONResponse({"detail": str(e)}, status_code=429)
    if not token:
        return JSONResponse({"detail": "Incorrect email or password."}, status_code=401)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(auth.SESSION_COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True, samesite="strict",
                    secure=_is_https(request), path="/")
    return resp


@app.post("/api/auth/logout")
def api_logout(request: Request):
    auth.logout(request.cookies.get(auth.SESSION_COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.SESSION_COOKIE, path="/")
    return resp


@app.get("/api/auth/me")
def api_me(request: Request):
    return {"email": auth.session_user(request.cookies.get(auth.SESSION_COOKIE))}


@app.post("/api/waitlist")
def api_waitlist(request: Request, email: str = Body(...), name: str = Body(""), interest: str = Body(""),
                 note: str = Body(""), website: str = Body(""), source: str = Body("")):
    if website:  # honeypot field: real visitors never fill it
        return {"ok": True, "status": "added"}
    if config.SERVERLESS:
        from . import notify
        if not notify.email_ready():
            return JSONResponse({"detail": UNAVAILABLE}, status_code=503)
    try:
        st = auth.join_waitlist(email, name, interest, note, _client_ip(request), source)
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    except PermissionError as e:
        return JSONResponse({"detail": str(e)}, status_code=429)
    if st == "added":
        from . import notify
        if config.SERVERLESS:  # no persistent disk: the email is the durable record, so it must go out
            if not notify.waitlist_signup(email.strip().lower(), interest, source, wait=True):
                with db.tx() as c:  # not delivered: forget it so a retry is treated as new
                    c.execute("DELETE FROM waitlist WHERE email=?", (email.strip().lower(),))
                return JSONResponse({"detail": UNAVAILABLE}, status_code=503)
        else:
            notify.waitlist_signup(email.strip().lower(), interest, source)
    return {"ok": True, "status": st}


@app.get("/api/public/spot")
def public_spot():
    out = {}
    for inst in ("XAUUSD", "EURUSD"):
        try:
            q = R.live.quote(inst)
            out[inst] = {k: q[k] for k in ("price", "bid", "ask", "as_of")}
        except Exception:
            out[inst] = None
    return out


@app.get("/api/public/overview")
def public_overview():
    """Public, forecast-free facts for the website: recorded prices (12 h), releases inside that window,
    upcoming release names/times and real counts. No predictions or evidence are exposed."""
    now = db.now_utc()
    if config.SERVERLESS:
        snap = _snapshot().get("overview") or {}
        cut, now_s = int((now - timedelta(hours=12)).timestamp()), db.iso(now)
        return {"series": {k: [p for p in v if p[0] >= cut] for k, v in (snap.get("series") or {}).items()},
                "events": [e for e in snap.get("events", []) if e["release_utc"] >= db.iso(now - timedelta(hours=12))],
                "upcoming": [e for e in snap.get("upcoming", []) if e["release_utc"] > now_s],
                "stats": snap.get("stats", {}), "window": [db.iso(now - timedelta(hours=12)), now_s],
                "as_of": snap.get("as_of", now_s), "snapshot": True}
    start = now - timedelta(hours=12)
    since = int(start.timestamp())
    series = {}
    for inst in ("XAUUSD", "EURUSD"):
        rows = db.rows("SELECT ts, bid, ask FROM live_ticks WHERE inst=? AND ts>=? ORDER BY ts", (inst, since))
        step = max(1, len(rows) // 420)
        series[inst] = [[r["ts"], round((r["bid"] + r["ask"]) / 2, 5)] for r in rows[::step]]
    events = db.rows(
        "SELECT title, country, release_utc, impact, actual, forecast FROM calendar WHERE release_utc>=? AND release_utc<=? "
        "AND lower(impact) IN ('high','medium') AND country IN ('USD','EUR') ORDER BY release_utc", (db.iso(start), db.iso(now)))
    upcoming = db.rows(
        "SELECT title, country, release_utc, impact FROM calendar WHERE gold_relevant=1 AND release_utc>? "
        "ORDER BY release_utc LIMIT 6", (db.iso(now),))

    def n(sql):
        r = db.row(sql)
        return int(r["n"]) if r else 0
    stats = {
        "releases": n("SELECT COUNT(*) n FROM hist_events"),
        "reactions": n("SELECT COUNT(*) n FROM reactions"),
        "news": n("SELECT COUNT(*) n FROM research_feed"),
        "ticks": n("SELECT COUNT(*) n FROM live_ticks"),
        "calendar": n("SELECT COUNT(*) n FROM calendar"),
    }
    return {"series": series, "events": events, "upcoming": upcoming, "stats": stats,
            "window": [db.iso(start), db.iso(now)], "as_of": db.iso(now)}


PUBLIC_CAL_FIELDS = ("title", "country", "release_utc", "impact", "forecast", "previous", "actual")


@app.get("/api/public/calendar")
def public_calendar(week: int = 0):
    """Public economic calendar (this week = 0, next week = 1): schedule and consensus data only."""
    now = db.now_utc()
    s0 = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    s0 += timedelta(days=7 * max(-4, min(1, week)))
    if config.SERVERLESS:
        cal = (_snapshot().get("calendar") or {}).get(s0.strftime("%Y-%m-%d"))
        return cal or {"start": db.iso(s0), "end": db.iso(s0 + timedelta(days=7)), "rows": [], "as_of": db.iso(now)}
    from .engine.features import TYPES
    d = calendar_api(start=s0.strftime("%Y-%m-%dT%H:%M:%S"), days=7)
    rows = [{k: r.get(k) for k in PUBLIC_CAL_FIELDS} | {"tracked": r.get("catalog_key") in TYPES}
            for r in d["rows"]]
    return {"start": d["start"], "end": d["end"], "rows": rows, "as_of": db.iso(now)}


@app.get("/api/public/metrics")
def public_metrics():
    """Aggregate, out-of-sample validation status. No individual forecasts are exposed."""
    if config.SERVERLESS and _snapshot().get("metrics"):
        return _snapshot()["metrics"]
    from .engine import backtest, model
    out = {"gate": {"min_calls": model.MIN_OOS, "lower_bound": 0.5, "brier": 0.25}, "assets": {}}
    for a in ("XAUUSD", "EURUSD"):
        for st in ("t30", "t2"):
            s = backtest.stats(a, st)
            o = s["overall"]
            out["assets"].setdefault(a, {})[st] = {
                "replayed": o["events"], "published": o["forecasts"], "withheld": o["insufficient"],
                "published_final": o["final"], "shadow": {k: o["shadow"].get(k) for k in ("final", "initial", "reversal")},
                "shadow_horizons": {h: o["shadow"].get(f"h{h}") for h in (1, 5, 15, 30, 60)},
                "first": s["first"], "last": s["last"]}
    out["engine_version"] = config.ENGINE_VERSION
    out["as_of"] = db.iso(db.now_utc())
    return out


@app.post("/api/investor-interest")
def api_investor_interest(request: Request, name: str = Body(""), email: str = Body(""), organization: str = Body(""),
                          role: str = Body(""), investor_type: str = Body(""), ticket: str = Body(""),
                          message: str = Body(""), website: str = Body("")):
    if website:  # honeypot
        return {"ok": True}
    if config.SERVERLESS:
        from . import notify
        if not notify.email_ready():
            return JSONResponse({"detail": "Submissions are temporarily unavailable. Please try again a little later."}, status_code=503)
    try:
        auth.submit_investor_interest(name, email, organization, role, investor_type, ticket, message, _client_ip(request))
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    except PermissionError as e:
        return JSONResponse({"detail": str(e)}, status_code=429)
    from . import notify
    sent = notify.investor_interest({"name": name.strip(), "email": email.strip().lower(), "organization": organization.strip(),
                                     "role": role.strip(), "investor_type": investor_type, "ticket": ticket,
                                     "message": message.strip()[:2000]}, auth.INVESTOR_TYPES, wait=config.SERVERLESS)
    if config.SERVERLESS and not sent:
        return JSONResponse({"detail": "Submissions are temporarily unavailable. Please try again a little later."}, status_code=503)
    return {"ok": True}


INV_COLS = ("name", "email", "organization", "role", "investor_type", "ticket", "message", "created_at")


@app.get("/api/admin/investors")
def admin_investors():
    from . import notify
    rows = db.rows(f"SELECT {', '.join(INV_COLS)} FROM investor_interest ORDER BY created_at DESC")
    return {"count": len(rows), "rows": rows, "types": auth.INVESTOR_TYPES, "email_alerts": notify.email_ready()}


@app.get("/api/admin/investors.csv")
def admin_investors_csv():
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(INV_COLS)
    for r in db.rows(f"SELECT {', '.join(INV_COLS)} FROM investor_interest ORDER BY created_at"):
        w.writerow([("'" + str(r[k])) if str(r[k] or "")[:1] in ("=", "+", "-", "@") else r[k] for k in INV_COLS])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=goadex-investor-interest.csv"})


@app.get("/api/admin/waitlist")
def admin_waitlist():
    rows = db.rows("SELECT email, name, interest, note, created_at, source FROM waitlist ORDER BY created_at DESC")
    return {"count": len(rows), "rows": rows,
            "last_7d": sum(1 for r in rows if r["created_at"] >= db.iso(db.now_utc() - timedelta(days=7)))}


@app.get("/api/admin/waitlist.csv")
def admin_waitlist_csv():
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["email", "name", "interest", "note", "joined_utc", "source"])
    for r in db.rows("SELECT email, name, interest, note, created_at, source FROM waitlist ORDER BY created_at"):
        # neutralise spreadsheet formula injection
        w.writerow([("'" + str(v)) if str(v)[:1] in ("=", "+", "-", "@") else v for v in
                    (r["email"], r["name"], r["interest"], r["note"], r["created_at"], r["source"])])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=goadex-waitlist.csv"})
