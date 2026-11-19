"""Serverless (Vercel) mode: boots without a lifespan, never writes to the code directory,
serves pages from the snapshot, and refuses sign-ups it cannot durably deliver."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SCRIPT = r'''
import json, os
from fastapi.testclient import TestClient
import index                                     # Vercel entry point
c = TestClient(index.app)                        # no "with": lifespan does NOT run, like some serverless hosts
H = {"X-Requested-With": "fetch"}
out = {
  "home": c.get("/").status_code, "investors": c.get("/investors").status_code, "login": c.get("/login").status_code,
  "overview": c.get("/api/public/overview").json().get("snapshot"),
  "metrics": sorted(c.get("/api/public/metrics").json().get("assets", {})),
  "calendar": c.get("/api/public/calendar").status_code,
  "protected": c.get("/api/home").status_code,
  "waitlist_no_email": c.post("/api/waitlist", json={"email": "a@b.co"}, headers=H).status_code,
  "investor_no_email": c.post("/api/investor-interest", json={"name": "A B", "email": "a@b.co", "investor_type": "vc"}, headers=H).status_code,
  "db": str(__import__("app.config", fromlist=["x"]).DB_PATH),
}
print(json.dumps(out))
'''


def test_vercel_mode():
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SMTP_", "NOTIFY_EMAIL", "DB_PATH"))}
    env.update(VERCEL="1", NOTIFY="0", TMPDIR=tempfile.mkdtemp())
    before = {p for p in ROOT.rglob("*") if "__pycache__" not in p.parts and ".pytest_cache" not in p.parts and ".git" not in p.parts}
    r = subprocess.run([sys.executable, "-c", SCRIPT], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    import json
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["home"] == out["investors"] == out["login"] == 200
    assert out["overview"] is True and out["metrics"] == ["EURUSD", "XAUUSD"] and out["calendar"] == 200
    assert out["protected"] == 401
    assert out["waitlist_no_email"] == 503 and out["investor_no_email"] == 503   # never a silent, lost sign-up
    assert out["db"].startswith("/tmp/")
    after = {p for p in ROOT.rglob("*") if "__pycache__" not in p.parts and ".pytest_cache" not in p.parts and ".git" not in p.parts}
    assert after == before, f"serverless mode wrote into the code directory: {after - before}"
