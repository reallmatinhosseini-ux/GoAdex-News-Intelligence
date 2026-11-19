"""Auth & waitlist: protected APIs, login, lockout, CSRF, sessions, waitlist rules."""
import os
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "a.db")
os.environ["NOTIFY"] = "0"  # no desktop pop-ups during tests
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, db  # noqa: E402

db.init()
auth.init()
auth.create_admin("admin@example.test", "correct horse battery staple")
from app.main import app  # noqa: E402

H = {"X-Requested-With": "fetch"}


def client():
    return TestClient(app)


def test_data_apis_require_login():
    c = client()
    for p in ("/api/home", "/api/backtest", "/api/news", "/api/calendar", "/api/status", "/api/admin/waitlist", "/api/spot"):
        assert c.get(p).status_code == 401, p
    assert c.get("/app", follow_redirects=False).status_code == 303
    assert c.get("/").status_code == 200 and c.get("/login").status_code == 200
    assert c.get("/api/public/spot").status_code == 200
    ov = c.get("/api/public/overview")
    assert ov.status_code == 200 and set(ov.json()) >= {"series", "events", "upcoming", "stats", "as_of"}


def test_login_logout_and_session():
    c = client()
    bad = c.post("/api/auth/login", json={"email": "admin@example.test", "password": "wrong-password-123"}, headers=H)
    assert bad.status_code == 401
    r = c.post("/api/auth/login", json={"email": "ADMIN@example.test", "password": "correct horse battery staple"}, headers=H)
    cookie = r.headers["set-cookie"].lower()
    assert r.status_code == 200 and "httponly" in cookie and "samesite=strict" in cookie
    assert c.get("/api/auth/me").json()["email"] == "admin@example.test"
    assert c.get("/api/admin/waitlist").status_code == 200
    c.post("/api/auth/logout", json={}, headers=H)
    assert c.get("/api/admin/waitlist").status_code == 401


def test_csrf_requires_json_and_header():
    c = client()
    assert c.post("/api/auth/login", data={"email": "a", "password": "b"}).status_code == 400
    assert c.post("/api/auth/login", json={"email": "a", "password": "b"}).status_code == 400


def test_lockout_after_repeated_failures():
    c = client()
    for _ in range(auth.MAX_FAILS):
        c.post("/api/auth/login", json={"email": "victim@example.test", "password": "nope-nope-nope"}, headers=H)
    r = c.post("/api/auth/login", json={"email": "victim@example.test", "password": "anything-else"}, headers=H)
    assert r.status_code == 429


def test_waitlist_rules():
    c = client()
    assert c.post("/api/waitlist", json={"email": "not-an-email"}, headers=H).status_code == 400
    assert c.post("/api/waitlist", json={"email": "a@b.co", "interest": "gold"}, headers=H).json()["status"] == "added"
    assert c.post("/api/waitlist", json={"email": "A@B.co"}, headers=H).json()["status"] == "exists"
    assert c.post("/api/waitlist", json={"email": "bot@b.co", "website": "spam"}, headers=H).status_code == 200
    assert db.row("SELECT COUNT(*) n FROM waitlist WHERE email='bot@b.co'")["n"] == 0


def test_security_headers():
    r = client().get("/")
    assert r.headers["x-frame-options"] == "DENY" and "default-src 'self'" in r.headers["content-security-policy"]


def test_investor_interest_and_public_pages():
    c = client()
    for p in ("/investors", "/api/public/metrics", "/api/public/calendar"):
        assert c.get(p).status_code == 200, p
    assert c.get("/api/admin/investors").status_code == 401
    bad = c.post("/api/investor-interest", json={"name": "A", "email": "x@y.co", "investor_type": "nope"}, headers=H)
    assert bad.status_code == 400
    ok = c.post("/api/investor-interest", json={"name": "Ada Lovelace", "email": "ada@fund.example", "organization": "=cmd",
                                                "investor_type": "vc", "ticket": "100k_500k", "message": "Hi"}, headers=H)
    assert ok.status_code == 200
    assert c.post("/api/investor-interest", json={"name": "Bot", "email": "b@b.co", "investor_type": "vc", "website": "x"},
                  headers=H).status_code == 200
    assert db.row("SELECT COUNT(*) n FROM investor_interest WHERE email='b@b.co'")["n"] == 0
    c.post("/api/auth/login", json={"email": "admin@example.test", "password": "correct horse battery staple"}, headers=H)
    assert c.get("/api/admin/investors").json()["count"] == 1
    assert "'=cmd" in c.get("/api/admin/investors.csv").text  # formula injection neutralised
    m = c.get("/api/public/metrics").json()
    assert "pred" not in str(m) and set(m["assets"]) == {"XAUUSD", "EURUSD"}


def test_proxy_awareness_and_cors():
    from starlette.requests import Request as R
    from app import main
    def req(host, headers):
        return R({"type": "http", "method": "GET", "path": "/", "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
                  "client": (host, 1234), "scheme": "http", "server": ("x", 80), "query_string": b""})
    # CF-Connecting-IP is trusted only from the local tunnel, never from a remote client
    assert main._client_ip(req("127.0.0.1", {"cf-connecting-ip": "203.0.113.9"})) == "203.0.113.9"
    assert main._client_ip(req("198.51.100.7", {"cf-connecting-ip": "203.0.113.9"})) == "198.51.100.7"
    assert main._is_https(req("127.0.0.1", {"x-forwarded-proto": "https"})) is True
    assert main._is_https(req("198.51.100.7", {"x-forwarded-proto": "https"})) is False
    c = client()
    ok = c.options("/api/waitlist", headers={"Origin": "https://www.goadex.com", "Access-Control-Request-Method": "POST"})
    assert ok.status_code == 204 and ok.headers["access-control-allow-origin"] == "https://www.goadex.com"
    evil = c.options("/api/waitlist", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in evil.headers
    adm = c.options("/api/admin/waitlist", headers={"Origin": "https://www.goadex.com", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in adm.headers  # admin APIs are never cross-origin
    assert "access-control-allow-credentials" not in ok.headers


def test_icons_served_without_login():
    c = client()
    ico = c.get("/favicon.ico")
    assert ico.status_code == 200 and ico.headers["content-type"] == "image/x-icon"
    assert c.get("/apple-touch-icon.png").status_code == 200
    for p in ("/static/favicon.svg", "/static/site.webmanifest", "/static/icon-192.png"):
        assert c.get(p).status_code == 200, p
    assert '<link rel="icon" href="/static/favicon.svg"' in c.get("/").text
