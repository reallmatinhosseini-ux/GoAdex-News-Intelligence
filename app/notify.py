"""Notifications: local macOS desktop alerts and owner email alerts.

Email is sent over SMTP with STARTTLS, in a background thread so web requests never wait on it.
Configure in .env (never commit credentials):
    NOTIFY_EMAIL   address that receives alerts
    SMTP_HOST      default smtp.gmail.com
    SMTP_PORT      default 587
    SMTP_USER      the sending account (e.g. your Gmail address)
    SMTP_PASSWORD  an app password for that account (Gmail: myaccount.google.com/apppasswords)
Disable desktop alerts with NOTIFY=0. Test email with:  python3 -m app.notify test
"""
import logging
import os
import platform
import smtplib
import ssl
import subprocess
import threading
from email.message import EmailMessage

log = logging.getLogger("notify")


def desktop(title: str, body: str):
    if os.environ.get("NOTIFY", "1") != "1" or platform.system() != "Darwin":
        return
    esc = lambda s: s.replace("\\\\", "\\\\\\\\").replace('"', '\\\\"')
    try:
        subprocess.run(["osascript", "-e", f'display notification "{esc(body)[:220]}" with title "GoAdex News Intelligence" subtitle "{esc(title)[:80]}" sound name "Glass"'],
                       timeout=5, check=False, capture_output=True)
    except Exception:
        pass


def email_config() -> dict:
    return {"to": os.environ.get("NOTIFY_EMAIL", "").strip(),
            "host": os.environ.get("SMTP_HOST", "smtp.gmail.com").strip(),
            "port": int(os.environ.get("SMTP_PORT", "587") or 587),
            "user": os.environ.get("SMTP_USER", "").strip(),
            "password": os.environ.get("SMTP_PASSWORD", "").replace(" ", "")}


def email_ready() -> bool:
    c = email_config()
    return bool(c["to"] and c["user"] and c["password"])


def _clean(s: str, n: int) -> str:
    return " ".join(str(s or "").split())[:n]  # no CR/LF → no header injection


def send_email(subject: str, body: str, reply_to: str | None = None) -> bool:
    """Send synchronously. Returns True on success; never raises."""
    c = email_config()
    if not email_ready():
        log.info("email alert skipped: NOTIFY_EMAIL / SMTP_USER / SMTP_PASSWORD not configured")
        return False
    msg = EmailMessage()
    msg["Subject"] = _clean(subject, 160)
    msg["From"] = f"GoAdex News Intelligence <{c['user']}>"
    msg["To"] = c["to"]
    if reply_to and "@" in reply_to:
        msg["Reply-To"] = _clean(reply_to, 200)
    msg.set_content(body)
    try:
        with smtplib.SMTP(c["host"], c["port"], timeout=20) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(c["user"], c["password"])
            s.send_message(msg)
        log.info("email alert sent: %s", msg["Subject"])
        return True
    except Exception as e:  # network / auth problems must never break the site
        log.warning("email alert failed: %s", e)
        return False


def email_async(subject: str, body: str, reply_to: str | None = None) -> None:
    threading.Thread(target=send_email, args=(subject, body, reply_to), daemon=True, name="email-alert").start()


def waitlist_signup(email: str, interest: str, source: str, wait: bool = False) -> bool | None:
    labels = {"both": "Gold & EUR/USD", "gold": "Gold", "eurusd": "EUR/USD"}
    desktop("New waitlist sign-up", email)
    return (send_email if wait else email_async)(f"GoAdex · New waitlist sign-up: {email}",
                f"Someone joined the GoAdex early-access waitlist.\n\n"
                f"Email:    {email}\nMarket:   {labels.get(interest, interest or '—')}\nSource:   {source or '—'}\n\n"
                f"See all sign-ups in the admin app → Waitlist & investors.\n", reply_to=email)


def investor_interest(d: dict, types: dict, wait: bool = False) -> bool | None:
    tickets = {"under_100k": "Under $100k", "100k_500k": "$100k – $500k", "500k_2m": "$500k – $2m",
               "2m_plus": "Over $2m", "undisclosed": "To be discussed", "": "Prefer not to say"}
    who = d.get("organization") or d.get("name")
    desktop("New investor interest", f"{d.get('name')} · {who}")
    return (send_email if wait else email_async)(f"GoAdex · Investor interest from {d.get('name')}" + (f" ({d.get('organization')})" if d.get("organization") else ""),
                "A new expression of interest was submitted on /investors.\n\n"
                f"Name:          {d.get('name')}\nEmail:         {d.get('email')}\n"
                f"Organization:  {d.get('organization') or '—'}\nRole:          {d.get('role') or '—'}\n"
                f"Type:          {types.get(d.get('investor_type'), d.get('investor_type'))}\n"
                f"Allocation:    {tickets.get(d.get('ticket') or '', d.get('ticket'))}\n\n"
                f"Message:\n{d.get('message') or '—'}\n\n"
                "Reply to this email to respond directly. All submissions: admin app → Waitlist & investors.\n",
                reply_to=d.get("email"))


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    if sys.argv[1:] == ["test"]:
        if not email_ready():
            print("Not configured. Set NOTIFY_EMAIL, SMTP_USER and SMTP_PASSWORD in .env, then run: "
                  "set -a; . ./.env; set +a; python3 -m app.notify test")
            sys.exit(1)
        ok = send_email("GoAdex · Test email alert", "Email alerts from GoAdex News Intelligence are working.\n")
        print("Sent ✓ — check your inbox." if ok else "Failed — see the warning above (check the app password).")
        sys.exit(0 if ok else 1)
    print("usage: python3 -m app.notify test")
