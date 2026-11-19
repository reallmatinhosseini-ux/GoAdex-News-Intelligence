"""Admin authentication and the public waitlist.

- Only admin accounts exist; they are created from the command line (`python3 -m app.admin`),
  never through the website. There is no public sign-up.
- Passwords: scrypt (N=2^14, r=8, p=1) with a per-user random salt; compared in constant time.
- Sessions: random 256-bit token in an HttpOnly, SameSite=Strict cookie; only its SHA-256 is stored;
  7-day expiry; deleted on logout.
- Brute force: after 5 failed logins for an email or IP within 15 minutes, logins are refused
  for 15 minutes.
- CSRF: state-changing requests must be JSON with an X-Requested-With header (cross-site forms
  can't send either) on top of SameSite cookies.
"""
import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from . import db

SESSION_COOKIE = "goadex_session"
SESSION_DAYS = 7
MAX_FAILS, LOCK_MIN = 5, 15
EMAIL_RX = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[A-Za-z]{2,24}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS admins (email TEXT PRIMARY KEY, pw_hash TEXT NOT NULL, salt TEXT NOT NULL,
    created_at TEXT, last_login TEXT, disabled INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, email TEXT NOT NULL, created_at TEXT, expires_at TEXT,
    ip TEXT, user_agent TEXT);
CREATE TABLE IF NOT EXISTS login_attempts (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT, ip TEXT, ok INTEGER, at TEXT);
CREATE TABLE IF NOT EXISTS waitlist (email TEXT PRIMARY KEY, name TEXT, interest TEXT, note TEXT, created_at TEXT,
    ip_hash TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS investor_interest (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, email TEXT, organization TEXT,
    role TEXT, investor_type TEXT, ticket TEXT, message TEXT, created_at TEXT, ip_hash TEXT);
"""


def init():
    with db.tx() as c:
        c.executescript(SCHEMA)


def _hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32).hex()


def create_admin(email: str, password: str):
    email = email.strip().lower()
    if not EMAIL_RX.match(email):
        raise ValueError("invalid email")
    if len(password) < 12:
        raise ValueError("password must be at least 12 characters")
    salt = secrets.token_bytes(16)
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO admins(email, pw_hash, salt, created_at, disabled) VALUES(?,?,?,?,0)",
                  (email, _hash(password, salt), salt.hex(), db.iso(db.now_utc())))


def _locked(email: str, ip: str) -> bool:
    since = db.iso(db.now_utc() - timedelta(minutes=LOCK_MIN))
    r = db.row("SELECT SUM(email=?) e, SUM(ip=?) i FROM login_attempts WHERE ok=0 AND at>=?", (email, ip, since))
    return (r["e"] or 0) >= MAX_FAILS or (r["i"] or 0) >= MAX_FAILS * 2


def login(email: str, password: str, ip: str, ua: str) -> str | None:
    """Returns a session token, or None. Same generic failure for unknown email and wrong password."""
    email = (email or "").strip().lower()
    if _locked(email, ip):
        raise PermissionError(f"Too many attempts. Try again in {LOCK_MIN} minutes.")
    a = db.row("SELECT * FROM admins WHERE email=? AND disabled=0", (email,))
    # always run the hash so response time does not reveal whether the email exists
    ok = False
    salt = bytes.fromhex(a["salt"]) if a else b"\x00" * 16
    calc = _hash(password or "", salt)
    if a:
        ok = hmac.compare_digest(calc, a["pw_hash"])
    with db.tx() as c:
        c.execute("INSERT INTO login_attempts(email, ip, ok, at) VALUES(?,?,?,?)", (email, ip, int(ok), db.iso(db.now_utc())))
        if not ok:
            return None
        token = secrets.token_urlsafe(32)
        c.execute("INSERT INTO sessions(token_hash, email, created_at, expires_at, ip, user_agent) VALUES(?,?,?,?,?,?)",
                  (hashlib.sha256(token.encode()).hexdigest(), email, db.iso(db.now_utc()),
                   db.iso(db.now_utc() + timedelta(days=SESSION_DAYS)), ip, (ua or "")[:200]))
        c.execute("UPDATE admins SET last_login=? WHERE email=?", (db.iso(db.now_utc()), email))
    return token


def session_user(token: str | None) -> str | None:
    if not token:
        return None
    r = db.row("SELECT s.email, s.expires_at FROM sessions s JOIN admins a ON a.email=s.email AND a.disabled=0 "
               "WHERE s.token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
    if not r or r["expires_at"] < db.iso(db.now_utc()):
        return None
    return r["email"]


def logout(token: str | None):
    if token:
        with db.tx() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))


def join_waitlist(email: str, name: str, interest: str, note: str, ip: str, source: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_RX.match(email):
        raise ValueError("Please enter a valid email address.")
    ip_hash = hashlib.sha256(("waitlist:" + ip).encode()).hexdigest()[:16]  # rate limiting only; raw IP not stored
    recent = db.row("SELECT COUNT(*) n FROM waitlist WHERE ip_hash=? AND created_at>=?",
                    (ip_hash, db.iso(db.now_utc() - timedelta(hours=1))))["n"]
    if recent >= 5:
        raise PermissionError("Too many sign-ups from this connection. Please try again later.")
    with db.tx() as c:
        cur = c.execute("INSERT OR IGNORE INTO waitlist(email, name, interest, note, created_at, ip_hash, source) VALUES(?,?,?,?,?,?,?)",
                        (email, (name or "").strip()[:80], (interest or "")[:40], (note or "").strip()[:500],
                         db.iso(db.now_utc()), ip_hash, (source or "")[:60]))
    return "added" if cur.rowcount else "exists"


INVESTOR_TYPES = {"vc": "Venture capital", "institutional": "Institutional investor", "strategic": "Strategic partner",
                  "family_office": "Family office", "angel": "Angel investor", "other": "Other"}
TICKETS = {"", "under_100k", "100k_500k", "500k_2m", "2m_plus", "undisclosed"}


def submit_investor_interest(name: str, email: str, organization: str, role: str, investor_type: str,
                             ticket: str, message: str, ip: str) -> int:
    name, email = (name or "").strip(), (email or "").strip().lower()
    if not name or len(name) > 120:
        raise ValueError("Please enter your full name.")
    if not EMAIL_RX.match(email):
        raise ValueError("Please enter a valid email address.")
    if investor_type not in INVESTOR_TYPES:
        raise ValueError("Please choose the type of organization you represent.")
    if ticket not in TICKETS:
        raise ValueError("Please choose an indicative allocation range.")
    ip_hash = hashlib.sha256(("investor:" + ip).encode()).hexdigest()[:16]
    recent = db.row("SELECT COUNT(*) n FROM investor_interest WHERE ip_hash=? AND created_at>=?",
                    (ip_hash, db.iso(db.now_utc() - timedelta(hours=1))))["n"]
    if recent >= 3:
        raise PermissionError("Too many submissions from this connection. Please try again later.")
    with db.tx() as c:
        cur = c.execute("INSERT INTO investor_interest(name, email, organization, role, investor_type, ticket, message, "
                        "created_at, ip_hash) VALUES(?,?,?,?,?,?,?,?,?)",
                        (name, email, (organization or "").strip()[:160], (role or "").strip()[:120], investor_type,
                         ticket, (message or "").strip()[:2000], db.iso(db.now_utc()), ip_hash))
    return cur.lastrowid
