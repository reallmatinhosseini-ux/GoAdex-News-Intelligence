"""Directional scenario engine — XAUUSD and EURUSD, stages T-30 and T-2.

Measurable definitions (all relative to the price 1 minute before the release, p0):
  INITIAL move  = sign of the price change at T+5 min   (|change| < FLAT_BP → inconclusive)
  FINAL move    = sign of the price change at T+60 min  (|change| < FLAT_BP → inconclusive)
  Scenario A  bullish continuation : initial UP,   final UP
  Scenario B  bearish continuation : initial DOWN, final DOWN
  Scenario C  up first, then down  : initial UP,   final DOWN   (two-way)
  Scenario D  down first, then up  : initial DOWN, final UP     (two-way)

Three probability models, each = average of a ridge logistic regression on point-in-time
features and an analog model (15 most similar earlier releases):
  P(initial up), P(final up), P(reversal)
All are fitted walk-forward (only releases whose outcome was known ≥2 h earlier) and each is
published only while its own earlier out-of-sample record passes a validation gate:
  directional models: Wilson lower bound of accuracy (90%, one-sided) > 50% and Brier < 0.25
  reversal model:     Brier better than the base-rate forecast and lower bound of precision > base rate
A scenario is selected only from validated components; otherwise "Insufficient Evidence".
No LLM, no randomness — arithmetic on measured data.
"""
import math
from datetime import timedelta

import numpy as np

from .. import catalog, config, db
from .features import ASSETS, FEATURE_VERSION, TYPES

HORIZONS = [1, 5, 15, 30, 60]
INITIAL_H, FINAL_H = 5, 60
FLAT_BP = {"XAUUSD": 2.0, "EURUSD": 1.0}   # moves smaller than this are "flat" (inconclusive)
MIN_TRAIN = 40          # fit models once 40 earlier releases exist
MIN_OOS = 60
MIN_CONVICTION = 0.03
Z90 = 1.2816
K_ANALOG = 15
COLS = ["f_a", "f_usd", "f_lead", "f_priced", "f_pos", "mom", "lvol"]
COL_NAMES = {"f_a": "Real-yield trend (gold) / US–euro rate gap (EUR)", "f_usd": "Dollar trend",
             "f_lead": "Leading-indicator surprise risk", "f_priced": "What is priced in",
             "f_pos": "Speculative positioning", "mom": "Pre-release price momentum", "lvol": "Pre-release volatility"}
UNIT = {"XAUUSD": "$", "EURUSD": "pips"}
SCEN = {"A": "Bullish continuation", "B": "Bearish continuation", "C": "Up first, then down", "D": "Down first, then up"}


def to_units(asset, price, bp):
    d = price * bp / 1e4
    return d if asset == "XAUUSD" else d / 0.0001


def wilson_lb(k, n, z=Z90):
    if n == 0:
        return 0.0
    p = k / n
    return (p + z * z / (2 * n) - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n)


def logistic(X, y, lam=2.0, iters=40):
    b = np.zeros(X.shape[1])
    P = np.eye(X.shape[1]) * lam
    P[0, 0] = 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(X @ b, -30, 30)))
        H = X.T @ (X * (p * (1 - p))[:, None]) + P
        step = np.linalg.solve(H, X.T @ (y - p) - P @ b)
        b += step
        if np.max(np.abs(step)) < 1e-7:
            break
    return b


def _vec(x):
    return [x["f_a"], x["f_usd"], x["f_lead"], x["f_priced"], x["f_pos"], max(-1, min(1, x["mom_bp"] / 50)),
            math.log(max(x["vol_bp"], 1e-4))]


def labels(asset, y):
    """Outcome labels from the measured path; None = inconclusive."""
    flat = FLAT_BP[asset]
    i, f = y.get(f"bp{INITIAL_H}"), y.get(f"bp{FINAL_H}")
    ini = None if i is None or abs(i) < flat else (1 if i > 0 else 0)
    fin = None if f is None or abs(f) < flat else (1 if f > 0 else 0)
    rev = None if ini is None or fin is None else int(ini != fin)
    scen = None if rev is None else ("A" if ini and fin else "B" if not ini and not fin else "C" if ini else "D")
    return {"initial": ini, "final": fin, "reversal": rev, "scenario": scen}


# ----------------------------------------------------------------- fit + predict
def fit_predict(train, x, asset):
    """P(initial up), P(final up), P(reversal) from earlier releases only."""
    out = {"n_train": len(train)}
    X_all = np.array([[1.0] + _vec(e["x"]) for e in train])
    A = X_all[:, 1:]
    mu, sd = A.mean(0), A.std(0) + 1e-9
    xv = np.array(_vec(x))
    dist = np.sqrt((((A - mu) / sd - (xv - mu) / sd) ** 2).sum(1))
    dist = dist + np.array([0.0 if e["x"]["type"] == x["type"] else 1.0 for e in train])
    order = np.argsort(dist)
    for target in ("initial", "final", "reversal"):
        idx = [i for i, e in enumerate(train) if e["lab"][target] is not None]
        if len(idx) < MIN_TRAIN:
            continue
        X = X_all[idx]
        y = np.array([float(train[i]["lab"][target]) for i in idx])
        b = logistic(X, y)
        p_log = float(1 / (1 + math.exp(-float(np.array([1.0] + list(xv)) @ b))))
        near = [i for i in order if train[i]["lab"][target] is not None][:K_ANALOG]
        k = int(sum(train[i]["lab"][target] for i in near))
        p_knn = (k + 1) / (len(near) + 2)
        out[target] = {"p": (p_log + p_knn) / 2, "p_log": p_log, "p_knn": p_knn, "analog_k": k, "analog_n": len(near),
                       "base": float(y.mean()), "coef": dict(zip(["intercept"] + COLS, [float(v) for v in b]))}
    out["analogs"] = [train[i]["id"] for i in order[:K_ANALOG]]
    out["analog_stats"] = _analog_stats([train[i] for i in order[:K_ANALOG]])
    return out


def _analog_stats(rows):
    lab = [r["lab"] for r in rows]
    c = lambda key, v: sum(1 for l in lab if l[key] == v)
    return {"n": len(rows), "initial_up": c("initial", 1), "initial_down": c("initial", 0),
            "final_up": c("final", 1), "final_down": c("final", 0), "reversals": c("reversal", 1),
            "scenarios": {s: c("scenario", s) for s in "ABCD"}}


# ----------------------------------------------------------------- validation gates (earlier OOS only)
def gates(oos):
    g = {}
    for target in ("initial", "final"):
        rec = [o for o in oos if o.get(f"p_{target}") is not None and o[target] is not None]
        calls = [o for o in rec if abs(o[f"p_{target}"] - 0.5) >= MIN_CONVICTION]
        k = sum(1 for o in calls if (o[f"p_{target}"] > 0.5) == bool(o[target]))
        brier = float(np.mean([(o[f"p_{target}"] - o[target]) ** 2 for o in rec])) if rec else None
        lb = wilson_lb(k, len(calls))
        g[target] = {"n": len(calls), "correct": k, "acc": k / len(calls) if calls else None, "lb": lb, "brier": brier,
                     "valid": bool(len(calls) >= MIN_OOS and lb > 0.5 and brier is not None and brier < 0.25)}
    rec = [o for o in oos if o.get("p_reversal") is not None and o["reversal"] is not None]
    if rec:
        base = float(np.mean([o["reversal"] for o in rec]))
        brier = float(np.mean([(o["p_reversal"] - o["reversal"]) ** 2 for o in rec]))
        brier_base = float(np.mean([(base - o["reversal"]) ** 2 for o in rec]))
        flagged = [o for o in rec if o["p_reversal"] > 0.5]
        k = sum(o["reversal"] for o in flagged)
        lb = wilson_lb(k, len(flagged))
        g["reversal"] = {"n": len(rec), "base_rate": base, "brier": brier, "brier_base": brier_base,
                         "flagged": len(flagged), "flagged_correct": k, "precision": k / len(flagged) if flagged else None,
                         "lb": lb, "valid": bool(len(rec) >= MIN_OOS and len(flagged) >= 15 and brier < brier_base and lb > base)}
    else:
        g["reversal"] = {"n": 0, "valid": False}
    return g


def _conf(oos, target, conviction):
    rec = [o for o in oos if o.get(f"p_{target}") is not None and o[target] is not None
           and abs(o[f"p_{target}"] - 0.5) >= MIN_CONVICTION]
    sim = [o for o in rec if abs(o[f"p_{target}"] - 0.5) >= conviction * 0.8]
    pool, basis = (sim, "calls with at least this conviction") if len(sim) >= 20 else (rec, "calls")
    k = sum(1 for o in pool if (o[f"p_{target}"] > 0.5) == bool(o[target]))
    lb = wilson_lb(k, len(pool))
    return {"label": "High" if lb >= 0.58 else "Moderate" if lb >= 0.52 else "Low", "k": k, "n": len(pool),
            "lb": round(lb, 3), "basis": basis}


# ----------------------------------------------------------------- one forecast
def forecast(asset, stage, x, train, oos_before, assess=None) -> dict:
    name = catalog.resolve(x["type"]).get("name", x["type"])
    out = {"asset": asset, "stage": stage, "model_version": config.ENGINE_VERSION, "n_train": len(train),
           "definitions": {"initial": f"price at T+{INITIAL_H} min vs 1 min before release",
                           "final": f"price at T+{FINAL_H} min vs 1 min before release",
                           "flat": f"moves under {FLAT_BP[asset]} bp count as flat/inconclusive"}}
    if x.get("vol_bp") is None:
        return {**out, "status": "not_ready", "reason": "Fewer than 60 fresh one-minute prices in the last 3 hours, so pre-release volatility and momentum can't be measured."}
    if len(train) < MIN_TRAIN:
        return {**out, "status": "insufficient",
                "reason": f"Only {len(train)} past releases with measured {asset} reactions are available (need {MIN_TRAIN}); more history is loading."}
    fp = fit_predict(train, x, asset)
    g = gates(oos_before)
    out.update(validation=g, analog_stats=fp["analog_stats"], analogs=fp["analogs"])
    probs = {t: fp[t] for t in ("initial", "final", "reversal") if t in fp}
    out["probabilities"] = {t: round(v["p"], 3) for t, v in probs.items()}
    steps = [f"Trained on {fp['n_train']} earlier releases (walk-forward; no information after this point)."]
    for t, v in probs.items():
        steps.append(f"P({'reversal' if t == 'reversal' else t + ' move up'}) = {v['p'] * 100:.0f}% "
                     f"(fundamentals model {v['p_log'] * 100:.0f}%, {v['analog_n']} most similar releases {v['analog_k']}/{v['analog_n']}"
                     f"; base rate {v['base'] * 100:.0f}%).")
    A_ = fp["analog_stats"]
    steps.append(f"Most similar {A_['n']} past releases: first move up {A_['initial_up']}, down {A_['initial_down']}; "
                 f"final up {A_['final_up']}, down {A_['final_down']}; reversals {A_['reversals']} "
                 f"(A {A_['scenarios']['A']}, B {A_['scenarios']['B']}, C {A_['scenarios']['C']}, D {A_['scenarios']['D']}).")

    def call(t):
        if t not in probs:
            return None
        p = probs[t]["p"]
        if not g[t]["valid"] or abs(p - 0.5) < MIN_CONVICTION:
            return None
        return 1 if p > 0.5 else 0

    fin, ini = call("final"), call("initial")
    rev_ok = "reversal" in probs and g["reversal"]["valid"] and probs["reversal"]["p"] > 0.5
    for t in ("initial", "final"):
        gt = g[t]
        steps.append(f"{t.capitalize()}-direction model out of sample: " +
                     (f"{gt['correct']}/{gt['n']} right ({gt['acc'] * 100:.0f}%, lower bound {gt['lb'] * 100:.0f}%)" if gt["n"] else "no calls yet")
                     + (" — validated." if gt["valid"] else " — NOT validated (needs lower bound > 50% on ≥60 calls)."))
    gr = g["reversal"]
    steps.append("Reversal model out of sample: " + (
        f"Brier {gr['brier']:.3f} vs base rate {gr['brier_base']:.3f}; flagged reversals right {gr['flagged_correct']}/{gr['flagged']}"
        if gr.get("n") else "no history yet") + (" — validated." if gr["valid"] else " — NOT validated."))

    scenario, first, final = None, None, None
    if fin is not None:
        final = fin
        if rev_ok:
            first, scenario = 1 - fin, ("D" if fin else "C")
        else:
            first, scenario = (ini if ini is not None else fin), ("A" if fin else "B")
            if ini is not None and ini != fin:  # validated models disagree without a validated reversal → stay with final
                first = fin
    elif ini is not None and rev_ok:
        first, final, scenario = ini, 1 - ini, ("C" if ini else "D")

    out["steps"] = steps
    if scenario is None:
        why = []
        for t in ("final", "initial"):
            if t in probs and not g[t]["valid"]:
                why.append(f"the {t}-direction model has not beaten a coin flip out of sample "
                           f"({g[t]['correct']}/{g[t]['n']} right)" if g[t]["n"] else f"the {t}-direction model has no out-of-sample record yet")
            elif t in probs and abs(probs[t]["p"] - 0.5) < MIN_CONVICTION:
                why.append(f"P({t} up) is {probs[t]['p'] * 100:.0f}%, too close to 50%")
        out.update(status="insufficient", reason="Insufficient Evidence: " + ("; ".join(why) or "no validated component") + ".",
                   lean_initial=_lean(probs.get("initial")), lean_final=_lean(probs.get("final")))
        return out
    t_conf = "final" if final is not None and fin is not None else "initial"
    conf = _conf(oos_before, t_conf, abs(probs[t_conf]["p"] - 0.5))
    if scenario in "CD":
        rc = gr
        conf_rev = wilson_lb(rc["flagged_correct"], rc["flagged"])
        if conf_rev < 0.5 and conf["label"] != "Low":
            conf["label"] = {"High": "Moderate", "Moderate": "Low"}[conf["label"]]
    out.update(status="ready", scenario=scenario, scenario_name=SCEN[scenario],
               primary="BUY / Bullish" if final else "SELL / Bearish",
               two_way=scenario in "CD", first="UP" if first else "DOWN", final="UP" if final else "DOWN",
               confidence=conf["label"], confidence_detail=conf,
               confidence_note=f"Right on {conf['k']} of {conf['n']} earlier out-of-sample {conf['basis']} "
                               f"(lower bound {conf['lb'] * 100:.0f}%).",
               alternatives=_alternatives(probs, scenario))
    return out


def _lean(v):
    if not v:
        return None
    return {"p_up": round(v["p"], 3), "label": "UP" if v["p"] > 0.5 else "DOWN"}


def _alternatives(probs, chosen):
    pi = probs.get("initial", {}).get("p", 0.5)
    pf = probs.get("final", {}).get("p", 0.5)
    pr = probs.get("reversal", {}).get("p", 0.5)
    est = {"A": pi * pf * (1 - pr), "B": (1 - pi) * (1 - pf) * (1 - pr), "C": pi * (1 - pf) * pr, "D": (1 - pi) * pf * pr}
    tot = sum(est.values()) or 1
    return [{"scenario": s, "name": SCEN[s], "relative_weight": round(v / tot, 2), "chosen": s == chosen}
            for s, v in sorted(est.items(), key=lambda kv: -kv[1])]


# ----------------------------------------------------------------- scoring
def score(asset, f, y):
    lab = labels(asset, y)
    p0 = y["p_pre"]
    out = {"p_pre": p0, "labels": lab, "horizons": {}, "inconclusive": []}
    fin = None if f.get("status") != "ready" else (1 if f["final"] == "UP" else 0)
    ini = None if f.get("status") != "ready" else (1 if f["first"] == "UP" else 0)
    for h in HORIZONS:
        bp = y.get(f"bp{h}")
        if bp is None:
            continue
        mv = "flat" if abs(bp) < FLAT_BP[asset] else ("up" if bp > 0 else "down")
        out["horizons"][str(h)] = {"bp": bp, "units": round(to_units(asset, p0, bp), 2), "direction": mv,
                                   "correct": None if fin is None or mv == "flat" else int((mv == "up") == bool(fin))}
    if f.get("status") == "ready":
        for k, pred, act in (("final", fin, lab["final"]), ("initial", ini, lab["initial"])):
            out[f"{k}_correct"] = None if act is None else int(pred == act)
            if act is None:
                out["inconclusive"].append(k)
        out["two_way_predicted"] = int(f["two_way"])
        out["two_way_occurred"] = lab["reversal"]
        out["reversal_correct"] = None if lab["reversal"] is None else int(bool(f["two_way"]) == bool(lab["reversal"]))
        out["scenario_correct"] = None if lab["scenario"] is None else int(lab["scenario"] == f["scenario"])
        # order check for predicted two-way moves: first leg in the predicted direction, then the reversal
        if f["two_way"] and lab["reversal"] is not None:
            out["order_correct"] = int(lab["initial"] == ini and lab["final"] == fin)
    out["actual_units_60"] = out["horizons"].get("60", {}).get("units")
    pr = f.get("probabilities") or {}
    if pr:  # unpublished "shadow" calls implied by the probabilities — reported separately, never as forecasts
        sh = {}
        for t in ("initial", "final"):
            if pr.get(t) is not None and lab[t] is not None and abs(pr[t] - 0.5) >= MIN_CONVICTION:
                sh[t] = int((pr[t] > 0.5) == bool(lab[t]))
        if pr.get("reversal") is not None and lab["reversal"] is not None:
            sh["reversal"] = int((pr["reversal"] > 0.5) == bool(lab["reversal"]))
        for h in HORIZONS:
            hz = out["horizons"].get(str(h))
            if pr.get("final") is not None and hz and hz["direction"] != "flat" and abs(pr["final"] - 0.5) >= MIN_CONVICTION:
                sh[f"h{h}"] = int((hz["direction"] == "up") == (pr["final"] > 0.5))
        out["shadow"] = sh
    return out


# ----------------------------------------------------------------- datasets & walk-forward
PRIORITY = ["US_FOMC", "US_NFP", "US_CPI", "US_PCE", "US_FOMC_MINUTES", "US_GDP", "US_PPI", "US_RETAIL"]


def dataset(asset, stage):
    """One sample per release instant: simultaneous releases share one market reaction, so counting
    them separately would double-count outcomes. The highest-priority event type represents the instant."""
    out = []
    rows = db.rows("SELECT event_id, catalog_key, release_utc, x, y FROM features WHERE version=? ORDER BY release_utc",
                   (FEATURE_VERSION,))
    best = {}
    for r in rows:
        k = r["release_utc"]
        rank = PRIORITY.index(r["catalog_key"]) if r["catalog_key"] in PRIORITY else 99
        if k not in best or rank < best[k][0]:
            best[k] = (rank, r)
    for r in [v[1] for v in sorted(best.values(), key=lambda v: v[1]["release_utc"])]:
        X, Y = db.jload(r["x"]), db.jload(r["y"])
        xa, ya = X.get(asset), Y.get(asset)
        if not xa or not ya or "f" not in xa or ya.get(f"bp{FINAL_H}") is None:
            continue
        ctx = xa["stages"][stage]
        out.append({"id": r["event_id"], "key": r["catalog_key"], "t": db.parse_iso(r["release_utc"]), "release": r["release_utc"],
                    "x": {"type": X["type"], **ctx, **xa["f"]}, "y": ya, "lab": labels(asset, ya),
                    "factors": xa.get("factors", []), "regime": X.get("regime")})
    return out


def _oos_rec(e, fp):
    r = {"t": db.iso(e["t"]), **e["lab"], "x": _vec(e["x"])}
    for t in ("initial", "final", "reversal"):
        r[f"p_{t}"] = fp[t]["p"] if t in fp else None
    return r


def walk_forward(asset, stage):
    from . import history
    E = dataset(asset, stage)
    oos, rows = [], []
    kind = f"retro_{stage}:{asset}"
    for e in E:
        cutoff = e["t"] - timedelta(hours=2)
        train = [z for z in E if z["t"] <= cutoff]
        before = [o for o in oos if db.parse_iso(o["t"]) <= cutoff]
        f = forecast(asset, stage, e["x"], train, before)
        f["factors"], f["regime"] = e["factors"], e["regime"]
        f["published_at"] = db.iso(e["t"] - timedelta(minutes=30 if stage == "t30" else 2))
        if len(train) >= MIN_TRAIN:
            oos.append(_oos_rec(e, fit_predict(train, e["x"], asset)))
        sc = score(asset, f, e["y"])
        rows.append((e["id"], kind, e["key"], e["release"], db.iso(db.now_utc()),
                     db.iso(e["t"] - timedelta(minutes=31 if stage == "t30" else 3)), db.jdump(f), db.jdump(sc),
                     db.jdump(history.released_values(e["key"], e["release"])), config.ENGINE_VERSION,
                     f.get("scenario") if f.get("status") == "ready" else f.get("status"),
                     sc.get("final_correct"), sc.get("scenario_correct")))
    summary = {"n_events": len(E), "oos": len(oos), "gates": gates(oos), "factor_value": factor_value(oos),
               "updated": db.iso(db.now_utc()), "label_rates": _label_rates(E)}
    with db.tx() as c:
        c.execute("DELETE FROM predictions WHERE kind=?", (kind,))
        c.executemany("INSERT OR REPLACE INTO predictions(event_id, kind, catalog_key, release_utc, made_at, as_of, pred, "
                      "outcome, released, engine_version, direction, correct, target_hit) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        for k_, v_ in ((f"oos:{asset}:{stage}", oos), (f"summary:{asset}:{stage}", summary)):
            c.execute("INSERT OR REPLACE INTO model_state(key, value, updated_at) VALUES(?,?,?)", (k_, db.jdump(v_), db.iso(db.now_utc())))
    return summary


def _label_rates(E):
    labs = [e["lab"] for e in E]
    n = len(labs) or 1
    return {"initial_up": round(sum(1 for l in labs if l["initial"] == 1) / n, 3),
            "final_up": round(sum(1 for l in labs if l["final"] == 1) / n, 3),
            "reversal": round(sum(1 for l in labs if l["reversal"] == 1) / max(1, sum(1 for l in labs if l["reversal"] is not None)), 3),
            "inconclusive_final": round(sum(1 for l in labs if l["final"] is None) / n, 3)}


def factor_value(oos):
    """Out-of-sample predictive value of each input on its own: does its sign predict the final move?"""
    out = []
    for j, col in enumerate(COLS[:-1]):  # volatility has no direction
        rec = [(o["x"][j], o["final"]) for o in oos if o.get("final") is not None and abs(o["x"][j]) >= 0.1]
        k = sum(1 for v, y in rec if (v > 0) == bool(y))
        n = len(rec)
        acc = k / n if n else None
        lb = wilson_lb(max(k, n - k), n) if n else 0
        out.append({"factor": COL_NAMES[col], "n": n, "accuracy": round(acc, 3) if acc is not None else None,
                    "lower_bound": round(lb, 3), "useful": bool(n >= 40 and lb > 0.5),
                    "direction": None if acc is None else ("as expected" if acc >= 0.5 else "inverted")})
    return out


def walk_forward_all():
    return {f"{a}:{s}": walk_forward(a, s) for a in ASSETS for s in ("t30", "t2")}


def live(asset, stage, x):
    E = dataset(asset, stage)
    cutoff = db.now_utc() - timedelta(hours=2)
    train = [e for e in E if e["t"] <= cutoff]
    st = db.row("SELECT value FROM model_state WHERE key=?", (f"oos:{asset}:{stage}",))
    return forecast(asset, stage, x, train, db.jload(st["value"], []) if st else [])
