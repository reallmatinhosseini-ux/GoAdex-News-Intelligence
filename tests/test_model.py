"""Scenario engine: labels, gates, honest abstention, scenario selection — on synthetic data."""
import os, random, tempfile
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "m.db")
from datetime import datetime, timedelta, timezone
from app import db
from app.engine import model

db.init()


def _events(n, mode, seed=1):
    """mode: 'random' (no signal), 'trend' (f_a predicts final), 'reversal' (f_a>0 → up first then down)."""
    rnd = random.Random(seed)
    out, t0 = [], datetime(2019, 1, 1, tzinfo=timezone.utc)
    for i in range(n):
        f = rnd.uniform(-1, 1)
        if mode == "random":
            b5, b60 = rnd.choice([-8, 8]), rnd.choice([-12, 12])
        elif mode == "trend":
            b5 = b60 = (10 if f > 0 else -10)
        else:  # reversal
            b5, b60 = (8, -12) if f > 0 else (-8, -12) if rnd.random() < 0.5 else (-8, 12)
        y = {"p_pre": 2000, "bp1": b5 / 2, "bp5": b5, "bp15": b5, "bp30": b60, "bp60": b60}
        x = {"type": "US_CPI", "vol_bp": 1 + rnd.random(), "mom_bp": 0, "f_a": f, "f_usd": 0, "f_lead": 0, "f_priced": 0, "f_pos": 0}
        out.append({"id": f"e{i}", "t": t0 + timedelta(days=7 * i), "x": x, "y": y, "lab": model.labels("XAUUSD", y)})
    return out


def _oos(E):
    oos = []
    for i, e in enumerate(E):
        if i >= model.MIN_TRAIN:
            oos.append(model._oos_rec(e, model.fit_predict(E[:i], e["x"], "XAUUSD")))
    return oos


def test_labels_and_scenarios():
    assert model.labels("XAUUSD", {"bp5": 5, "bp60": 6})["scenario"] == "A"
    assert model.labels("XAUUSD", {"bp5": 5, "bp60": -6})["scenario"] == "C"
    assert model.labels("XAUUSD", {"bp5": -5, "bp60": 6})["scenario"] == "D"
    assert model.labels("XAUUSD", {"bp5": 0.5, "bp60": 6})["scenario"] is None   # flat first move = inconclusive


def test_insufficient_without_history():
    E = _events(20, "trend")
    f = model.forecast("XAUUSD", "t30", E[-1]["x"], E[:-1], [])
    assert f["status"] == "insufficient" and "need 40" in f["reason"]


def test_random_market_gives_insufficient_evidence():
    E = _events(260, "random")
    f = model.forecast("XAUUSD", "t30", E[-1]["x"], E[:-1], _oos(E[:-1]))
    assert f["status"] == "insufficient"          # never forces a call on coin-flip data


def test_trend_signal_gives_continuation():
    E = _events(260, "trend")
    x = dict(E[-1]["x"], f_a=0.9)
    f = model.forecast("XAUUSD", "t30", x, E[:-1], _oos(E[:-1]))
    assert f["status"] == "ready" and f["scenario"] == "A" and f["primary"].startswith("BUY") and not f["two_way"]


def test_scoring_two_way_order():
    f = {"status": "ready", "scenario": "C", "first": "UP", "final": "DOWN", "two_way": True}
    sc = model.score("XAUUSD", f, {"p_pre": 2000, "bp5": 6, "bp60": -9})
    assert sc["scenario_correct"] == 1 and sc["order_correct"] == 1 and sc["final_correct"] == 1
    sc = model.score("XAUUSD", f, {"p_pre": 2000, "bp5": -6, "bp60": -9})
    assert sc["scenario_correct"] == 0 and sc["order_correct"] == 0 and sc["final_correct"] == 1
