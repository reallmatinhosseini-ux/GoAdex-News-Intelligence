"""Contextual impact classification: base tier, promoted/demoted by the current regime."""
from .. import catalog, db


def _regime_labels() -> list[str]:
    r = db.cache_get_stale("regime")[0] or {}
    return [l.lower() for l in r.get("labels", [])]


def classify(key: str | None, title: str) -> tuple[str, str]:
    if not key:
        return "low", "Uncatalogued event"
    d = catalog.resolve(key)
    tier = d.get("tier", 3)
    labels = " ".join(_regime_labels())
    cat = d.get("category", "")
    reasons = [f"Base tier {tier}"]
    boost = 0
    if cat == "inflation" and any(w in labels for w in ("persistent", "re-accelerat", "above target")):
        boost, _ = 1, reasons.append("inflation is the binding policy constraint in the current regime")
    if cat == "labor" and any(w in labels for w in ("softening", "deteriorating")):
        boost, _ = 1, reasons.append("labour-market deterioration raises the weight of jobs data")
    if cat == "central_bank" and "easing" in labels:
        reasons.append("market is pricing a policy change")
    if key == "US_FED_SPEECH":
        t = title.lower()
        if "chair" in t and "vice" not in t:
            return "high", "Fed Chair remarks can reset policy expectations"
        if "vice chair" in t:
            return "medium", "Vice Chair remarks"
        return "low", "Individual Governor/President remarks"
    eff = tier - boost
    lvl = "high" if eff <= 1 else "medium" if eff == 2 else "low"
    return lvl, "; ".join(reasons)


def reclassify_all():
    with db.tx() as c:
        for r in c.execute("SELECT id, catalog_key, title FROM events WHERE release_utc > ?",
                           (db.iso(db.now_utc()),)).fetchall():
            lvl, why = classify(r["catalog_key"], r["title"])
            c.execute("UPDATE events SET impact=?, impact_reason=? WHERE id=?", (lvl, why, r["id"]))
