"""Core guarantees: walk-forward uses only past data; validation gate; deadline honesty."""
import os, tempfile
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
from datetime import timedelta
from app import config, db
from app.engine import quant

db.init()


def _rows(n, pattern):
    return [{"release_utc": f"2020-01-{i%28+1:02d}T13:30:00Z", "surprise": 1 if i % 2 else -1,
             "reaction_bps": {"15": pattern(i)}} for i in range(n)]


def test_walk_forward_never_sees_the_future():
    # Reactions flip regime halfway: a look-ahead model would score perfectly on the 2nd half.
    rows = _rows(60, lambda i: (-5 if i % 2 else 5) if i < 30 else (5 if i % 2 else -5))
    m = quant.walk_forward(rows, min_train=12)
    assert m["n_oos"] == 48
    assert m["hit_rate"] < 0.75  # must suffer after the regime break


def test_gate_requires_samples_and_skill():
    m = quant.walk_forward(_rows(20, lambda i: -5 if i % 2 else 5), min_train=12)
    assert m["status"] == "experimental"  # only 8 OOS predictions < MIN_OOS_SAMPLES
    m = quant.walk_forward(_rows(80, lambda i: -5 if i % 2 else 5), min_train=12)
    assert m["status"] == "validated" and m["brier"] < m["brier_baseline"]


def test_deadline_missed_is_recorded():
    from app import scheduler
    rel = db.now_utc() + timedelta(minutes=30)
    eid = "US_CPI:test"
    with db.tx() as c:
        c.execute("INSERT INTO events(id, catalog_key, title, country, release_utc, discovered_at, late_discovery) "
                  "VALUES(?,?,?,?,?,?,1)", (eid, "US_CPI", "CPI", "US", db.iso(rel), db.iso(db.now_utc())))
    scheduler.deadline_check(eid)
    ev = db.row("SELECT * FROM events WHERE id=?", (eid,))
    assert ev["report_deadline_met"] == 0 and "Late discovery" in ev["report_deadline_note"]
