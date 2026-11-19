"""Email alerts: built correctly, safe headers, never sent when unconfigured, never break requests."""
from app import notify


class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        pass

    def login(self, user, pw):
        assert user == "sender@example.test" and pw == "abcdabcdabcdabcd"

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


def _configure(monkeypatch):
    monkeypatch.setenv("NOTIFY_EMAIL", "owner@example.test")
    monkeypatch.setenv("SMTP_USER", "sender@example.test")
    monkeypatch.setenv("SMTP_PASSWORD", "abcd abcd abcd abcd")  # Gmail shows app passwords with spaces
    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(notify, "desktop", lambda *a: None)
    monkeypatch.setattr(notify, "email_async", lambda s, b, reply_to=None: notify.send_email(s, b, reply_to))
    FakeSMTP.sent = []


def test_not_configured_sends_nothing(monkeypatch):
    for k in ("NOTIFY_EMAIL", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    assert notify.email_ready() is False
    assert notify.send_email("x", "y") is False


def test_investor_alert(monkeypatch):
    _configure(monkeypatch)
    notify.investor_interest({"name": "Ada\r\nBcc: evil@x.test", "email": "ada@fund.example", "organization": "Fund",
                              "role": "Partner", "investor_type": "vc", "ticket": "100k_500k", "message": "Hello"},
                             {"vc": "Venture capital"})
    m = FakeSMTP.sent[-1]
    assert m["To"] == "owner@example.test" and m["Reply-To"] == "ada@fund.example"
    assert "\n" not in m["Subject"] and m["Bcc"] is None  # header injection neutralised
    body = m.get_content()
    assert "Venture capital" in body and "$100k – $500k" in body and "Hello" in body


def test_waitlist_alert_and_smtp_failure_is_swallowed(monkeypatch):
    _configure(monkeypatch)
    notify.waitlist_signup("new@user.example", "gold", "home")
    assert "new@user.example" in FakeSMTP.sent[-1]["Subject"]

    class Boom(FakeSMTP):
        def login(self, *a):
            raise OSError("auth failed")
    monkeypatch.setattr(notify.smtplib, "SMTP", Boom)
    assert notify.send_email("x", "y") is False
