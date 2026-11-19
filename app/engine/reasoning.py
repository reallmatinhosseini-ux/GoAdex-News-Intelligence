"""Layers B–E: event-specific analysis, expectations/surprise-risk, cross-market
confirmation and the final directional assessment.

Every factor is explicit: name, layer, signed signal for the asset (+ bullish),
weight, plain-language evidence and source facts. The score is a transparent
weighted average — not a probability.
"""
from statistics import mean, pstdev

from .. import catalog, db
from ..providers import registry as R
from ..providers.base import MARKET, ProviderError, fact
from . import data, quant

# Leading / expectation-shaping series per indicator: (series, transform, sign: + means "stronger/hotter")
LEADING = {
    "ICSA": ("level", -1, "Initial jobless claims (4w vs 13w avg)"),
    "ADPMNUSNERSA": ("change_k", +1, "ADP private payrolls"),
    "JTSJOL": ("level", +1, "JOLTS job openings"),
    "PPIFIS": ("mom_pct", +1, "PPI final demand m/m"),
    "MICH": ("level", +1, "UMich 1y inflation expectations"),
    "T5YIE": ("level", +1, "5y breakeven inflation"),
    "DGS2": ("level", +1, "2y Treasury yield"),
    "DFF": ("level", +1, "Effective fed funds"),
    "CPILFESL": ("mom_pct", +1, "Core CPI m/m (feeds core PCE)"),
}

ASSET_USD_SENS = {"XAUUSD": -1, "EURUSD": -1}


def clip(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def _leading_signal(key: str) -> tuple[float | None, list[dict], list[str]]:
    """Direction of likely surprise from indicators known now (+ = stronger/hotter)."""
    d = catalog.resolve(key)
    parts, gaps = [], []
    for sid in d.get("leading", []):
        how, sign, label = LEADING.get(sid, ("level", 1, sid))
        try:
            if sid == "DGS2":
                nom, _ = data.yields()
                ch = data.change(nom, "BC_2YEAR", 20)
                z = clip((ch or 0) / 0.25)
                parts.append({"indicator": "2y Treasury, 20d change", "z": round(z, 2),
                              "fact": fact(round((ch or 0) * 100), "US Treasury (derived, bp)", R.treasury.docs,
                                           nom[-1]["date"], MARKET)})
                continue
            t, prov = data.latest(sid, how, start="2019-01-01")
            vals = [o["value"] for o in t]
            if sid == "ICSA" and len(vals) >= 13:
                recent, base = mean(vals[-4:]), mean(vals[-13:])
            else:
                if len(vals) < 7:
                    continue
                recent, base = mean(vals[-2:]), mean(vals[-8:-2])
            sd = pstdev(vals[-24:]) or 1e-9
            z = clip((recent - base) / sd / 1.5) * sign
            parts.append({"indicator": label, "z": round(z, 2),
                          "fact": data.provenance_fact(round(vals[-1], 3), prov, t[-1]["date"])})
        except ProviderError as e:
            gaps.append(f"{label}: {e}")
    if not parts:
        return None, parts, gaps
    return clip(mean(p["z"] for p in parts)), parts, gaps


def _corr(a, b):
    n = min(len(a), len(b))
    a, b = a[-n:], b[-n:]
    if n < 20:
        return None
    ma, mb = mean(a), mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
    return num / den if den else None


def cross_market() -> dict:
    """Layer D: gold vs real-yield / USD relationships and their stability."""
    out = {"gaps": []}
    try:
        _, real = data.yields()
        gold = data.price_history("XAUUSD", "1day", 300)
        eur = data.price_history("EURUSD", "1day", 300)
        g = {r["t"][:10]: r["c"] for r in gold}
        e = {r["t"][:10]: r["c"] for r in eur}
        ry = {r["date"]: r.get("TC_10YEAR") for r in real if r.get("TC_10YEAR") is not None}
        days = sorted(set(g) & set(ry))
        gr = [(g[days[i]] / g[days[i - 1]] - 1) for i in range(1, len(days))]
        rr = [(ry[days[i]] - ry[days[i - 1]]) for i in range(1, len(days))]
        c60, call = _corr(gr[-60:], rr[-60:]), _corr(gr, rr)
        de = sorted(set(g) & set(e))
        ge = [(g[de[i]] / g[de[i - 1]] - 1) for i in range(1, len(de))]
        ee = [(e[de[i]] / e[de[i - 1]] - 1) for i in range(1, len(de))]
        out["gold_vs_real10y"] = {"corr_60d": round(c60, 2) if c60 is not None else None,
                                  "corr_full": round(call, 2) if call is not None else None, "n": len(gr)}
        out["gold_vs_eurusd"] = {"corr_60d": round(_corr(ge[-60:], ee[-60:]) or 0, 2), "n": len(ge)}
        stable = c60 is not None and call is not None and (c60 < -0.15) == (call < -0.15)
        out["real_yield_link"] = ("intact" if c60 is not None and c60 < -0.15 else "weak/broken") + \
                                 ("" if stable else " (unstable vs longer history)")
        out["series"] = {"dates": days[-120:], "gold": [g[d] for d in days[-120:]],
                         "real10y": [ry[d] for d in days[-120:]],
                         "eurusd": [e.get(d) for d in days[-120:]]}
        out["source"] = "Twelve Data daily closes; US Treasury real yield curve"
    except ProviderError as ex:
        out["gaps"].append(f"Cross-market correlations need price data: {ex}")
    except Exception as ex:
        out["gaps"].append(f"Cross-market computation failed: {ex}")
    return out


def technical_levels(asset: str) -> dict | None:
    try:
        bars = data.price_history(asset, "1day", 60)
    except ProviderError as e:
        return {"available": False, "note": str(e)}
    if len(bars) < 21:
        return {"available": False, "note": "insufficient price history"}
    prev = bars[-2]
    pivot = (prev["h"] + prev["l"] + prev["c"]) / 3
    trs = [max(b["h"] - b["l"], abs(b["h"] - a["c"]), abs(b["l"] - a["c"])) for a, b in zip(bars[-15:], bars[-14:])]
    return {"available": True, "as_of": bars[-1]["t"], "last": bars[-1]["c"],
            "prev_high": prev["h"], "prev_low": prev["l"], "pivot": round(pivot, 5),
            "r1": round(2 * pivot - prev["l"], 5), "s1": round(2 * pivot - prev["h"], 5),
            "high_20d": max(b["h"] for b in bars[-20:]), "low_20d": min(b["l"] for b in bars[-20:]),
            "atr14": round(mean(trs), 5), "change_5d_pct": round((bars[-1]["c"] / bars[-6]["c"] - 1) * 100, 2),
            "source": "Twelve Data daily bars (levels are descriptive, not signals)"}


def assess(key: str, asset: str, regime: dict, xm: dict, consensus: dict | None) -> dict:
    """Layer E for one asset. Returns bias, score, confidence, supporting/opposing factors."""
    d = catalog.resolve(key)
    sens = ASSET_USD_SENS.get(asset, -1)
    factors, gaps = [], []
    sig = regime.get("signals", {})
    rates = sig.get("rates", {})

    # 1. Real-yield trend (gold-specific transmission), down-weighted if the link is broken
    if asset == "XAUUSD" and "real10y_chg_20d_bp" in rates:
        ch = rates["real10y_chg_20d_bp"]["value"]
        link = xm.get("real_yield_link", "unknown")
        w = 0.07 if "weak/broken" in link else 0.15
        factors.append({"name": "Real-yield trend", "layer": "D", "signal": round(clip(-ch / 25), 2), "weight": w,
                        "evidence": f"10y TIPS yield {ch:+d}bp over 20 sessions; gold–real-yield link {link}.",
                        "sources": [rates["real10y_chg_20d_bp"], rates.get("real10y")]})

    # 2. USD trend
    usd = sig.get("markets", {}).get("usd_broad")
    if usd and usd.get("chg_20") is not None:
        pct = usd["chg_20"] / usd["value"] * 100
        factors.append({"name": "Dollar trend", "layer": "D", "signal": round(clip(sens * pct / 2), 2), "weight": 0.1,
                        "evidence": f"Broad trade-weighted USD {pct:+.2f}% over ~20 sessions.", "sources": [usd]})
    else:
        try:
            ex = data.ecb("eurusd")
            eur_pct = (ex[-1]["value"] / ex[-21]["value"] - 1) * 100
            usd_pct = -eur_pct
            factors.append({"name": "Dollar trend", "layer": "D", "signal": round(clip(sens * usd_pct / 1.5), 2), "weight": 0.1,
                            "evidence": f"EUR/USD {eur_pct:+.2f}% over 20 sessions to {ex[-1]['value']:.4f} — the dollar has "
                                        f"{'strengthened' if usd_pct > 0 else 'weakened'} against the euro.",
                            "sources": [fact(ex[-1]["value"], "ECB EUR/USD reference rate", R.ecb.docs, ex[-1]["date"], MARKET)]})
        except (ProviderError, IndexError, KeyError) as e:
            gaps.append(f"Dollar trend: {e}")

    # 2b. Fed–ECB rate differential (the main EUR/USD driver)
    if asset == "EURUSD":
        try:
            ea = data.ecb("ea_2y")
            nom, _ = data.yields()
            us = {r["date"]: r.get("BC_2YEAR") for r in nom if r.get("BC_2YEAR") is not None}
            eam = {r["date"]: r["value"] for r in ea}
            days = sorted(set(us) & set(eam))
            spread = [(us[d_] - eam[d_]) * 100 for d_ in days]
            ch = spread[-1] - spread[-21]
            factors.append({"name": "US–euro rate gap", "layer": "D", "signal": round(clip(-ch / 20), 2), "weight": 0.2,
                            "evidence": f"US 2-year yield minus euro-area 2-year yield is {spread[-1]:.0f}bp, "
                                        f"{'widened' if ch > 0 else 'narrowed'} {abs(ch):.0f}bp over 20 sessions — "
                                        f"{'favouring the dollar' if ch > 0 else 'favouring the euro'}.",
                            "sources": [fact(round(eam[days[-1]], 3), "ECB euro-area 2y yield (AAA)", R.ecb.docs, days[-1], MARKET),
                                        fact(us[days[-1]], "US Treasury 2y", R.treasury.docs, days[-1], MARKET)]})
        except (ProviderError, IndexError, KeyError) as e:
            gaps.append(f"US–euro rate gap: {e}")

    # 3. Leading indicators -> surprise-risk direction (Layer C)
    lead, parts, lgaps = _leading_signal(key)
    gaps += lgaps
    if lead is not None:
        # Stronger/hotter US data is USD-positive; stronger euro-area data is USD-relative negative.
        s = clip(lead * sens * (1 if d["country"] == "US" else -1))
        factors.append({"name": "Surprise risk from leading indicators", "layer": "C", "signal": round(s, 2),
                        "weight": 0.2,
                        "evidence": ("Indicators known before the release lean "
                                     f"{'stronger/hotter' if lead > 0.1 else 'weaker/cooler' if lead < -0.1 else 'neutral'} "
                                     f"(composite {lead:+.2f}): " + "; ".join(f"{p['indicator']} {p['z']:+.2f}" for p in parts)),
                        "sources": [p["fact"] for p in parts]})

    # 4. Policy pricing vs regime (what is priced in)
    pp = rates.get("policy_pricing")
    if pp and d.get("category") in ("inflation", "labor", "central_bank", "growth"):
        infl = sig.get("inflation", {}).get("label", "")
        lab = sig.get("labor", {}).get("label", "")
        s, why = 0.0, ""
        if "easing" in pp["label"] and ("Persistent" in infl or "re-accel" in infl.lower()):
            s, why = sens * 0.6, "Easing is priced while inflation is sticky: hawkish repricing risk is asymmetric (USD-positive)."
        elif "easing" in pp["label"] and ("Deteriorating" in lab or "Softening" in lab):
            s, why = 0.0, "Easing priced and labour data soft: dovish outcome largely priced; reaction asymmetry unclear."
        elif "tightening" in pp["label"] and "Disinflation" in infl:
            s, why = -sens * 0.6, "Tightening priced despite disinflation: dovish repricing risk (USD-negative)."
        else:
            why = f"{pp['label']}; no clear asymmetry against the {infl or 'unknown'} / {lab or 'unknown'} backdrop."
        factors.append({"name": "What is priced in", "layer": "C", "signal": round(s, 2), "weight": 0.2,
                        "evidence": why, "sources": [pp["spread_2y_3m_bp"]]})

    # 5. Positioning asymmetry
    if asset == "XAUUSD" and sig.get("gold_positioning"):
        gp = sig["gold_positioning"]
        p = gp["percentile_3y"]
        s = -0.6 if p >= 80 else 0.6 if p <= 20 else (50 - p) / 100
        factors.append({"name": "Speculative positioning", "layer": "D", "signal": round(s, 2), "weight": 0.15,
                        "evidence": f"Non-commercial net long {gp['net_pct_oi']['value']}% of OI, {p}th pct of 3y range "
                                    f"({gp['label'].lower()}); crowded positions amplify adverse surprises.",
                        "sources": [gp["net_pct_oi"]]})

    if asset == "EURUSD":
        try:
            pos = data.cot("EURUSD")
            hist = [p_["net_pct_oi"] for p_ in pos if p_["net_pct_oi"] is not None]
            pct = round(sum(1 for h in hist if h <= hist[-1]) / len(hist) * 100)
            s = -0.6 if pct >= 80 else 0.6 if pct <= 20 else (50 - pct) / 100
            factors.append({"name": "Speculative positioning", "layer": "D", "signal": round(s, 2), "weight": 0.15,
                            "evidence": f"Speculators' net euro position is {hist[-1]:+.1f}% of open interest, "
                                        f"{pct}th percentile of 3 years — "
                                        + ("crowded long, so disappointments hit harder." if pct >= 80 else
                                           "light, leaving room to buy." if pct <= 20 else "not stretched either way."),
                            "sources": [fact(round(hist[-1], 1), "CFTC COT (euro FX futures)", R.cftc.docs, pos[-1]["date"], MARKET)]})
        except (ProviderError, IndexError, ZeroDivisionError) as e:
            gaps.append(f"EUR positioning: {e}")

    # 6. Empirical event study, conditional on the leading-indicator lean
    qs = {"status": "skipped_point_in_time"} if data.ASOF.get() else quant.summarize(key, asset)
    if qs.get("status") in ("validated", "experimental") and lead is not None and abs(lead) > 0.1:
        side = qs.get("after_stronger") if lead > 0 else qs.get("after_weaker")
        if side and side["n"] >= 5:
            s = clip((side["share_up"] - 0.5) * 2 * min(1, abs(lead) * 2))
            factors.append({"name": "Historical reaction pattern", "layer": "C", "signal": round(s, 2),
                            "weight": 0.2 if qs["status"] == "validated" else 0.08,
                            "evidence": f"After {'stronger' if lead > 0 else 'weaker'}-than-trend prints, {asset} rose in "
                                        f"{side['share_up']*100:.0f}% of {side['n']} cases (median {side['median_bps']:+.1f}bp, 15m). "
                                        f"Model status: {qs['status']}.",
                            "sources": [fact(qs["n_events"], "Event study (ALFRED + Twelve Data)", "", qs["built_at"], "historical_macro")]})

    # 7. Consensus vs previous (expected direction already known to market — informative for narrative)
    if consensus and consensus.get("headline"):
        gaps_note = None
    else:
        gaps.append("No licensed consensus feed: surprise is assessed against trend, not consensus")

    # ---- combine -------------------------------------------------------------
    W_ALL = 1.0 if asset == "XAUUSD" else 1.05  # all applicable factor weights, incl. paid-data ones
    used = [f for f in factors if f["weight"] > 0]
    wsum = sum(f["weight"] for f in used)
    score = sum(f["signal"] * f["weight"] for f in used) / wsum if wsum else 0.0
    coverage = min(1.0, wsum / W_ALL)
    nonzero = [f for f in used if abs(f["signal"]) >= 0.1]
    agree = (sum(f["weight"] for f in nonzero if (f["signal"] > 0) == (score > 0)) /
             sum(f["weight"] for f in nonzero)) if nonzero else 0
    if coverage < 0.45 or len(nonzero) < 2:
        bias = "Insufficient evidence"
    elif score > 0.15:
        bias = "Bullish bias"
    elif score < -0.15:
        bias = "Bearish bias"
    else:
        bias = "Neutral / no edge"
    validated = qs.get("status") == "validated"
    if bias.startswith(("Insufficient", "Neutral")):
        conf = "Low"
    elif abs(score) > 0.45 and agree > 0.75 and coverage > 0.7 and validated:
        conf = "High"
    elif abs(score) > 0.25 and agree > 0.6 and coverage > 0.45:
        conf = "Moderate"
    else:
        conf = "Low"
    supporting = [f for f in nonzero if (f["signal"] > 0) == (score > 0)]
    opposing = [f for f in nonzero if (f["signal"] > 0) != (score > 0)]
    probs = None
    if validated and qs["model"].get("calibration"):
        probs = {"note": "Pre-release direction depends on the unknown surprise; validated conditional model shown in scenarios."}
    return {"asset": asset, "bias": bias, "score": round(score, 3), "confidence": conf,
            "coverage": round(coverage, 2), "agreement": round(agree, 2), "factors": factors,
            "supporting": [f["name"] for f in sorted(supporting, key=lambda f: -abs(f["signal"] * f["weight"]))],
            "opposing": [f["name"] for f in sorted(opposing, key=lambda f: -abs(f["signal"] * f["weight"]))],
            "quant": qs, "probabilities": probs, "gaps": gaps,
            "transmission": catalog.TRANSMISSION.get(asset, {}).get("path"),
            "label": "Weighted evidence score (−1…+1). Not a calibrated probability."}


# ----------------------------------------------------------------- scenarios
def scenarios(key: str, consensus: dict | None, previous: dict | None, qs_gold: dict, lead_trend: dict) -> list[dict]:
    d = catalog.resolve(key)
    cat = d.get("category")
    cv = (consensus or {}).get("headline", {}).get("value")
    pv = (previous or {}).get("headline", {}).get("value")
    ref = f"consensus {cv}" if cv not in (None, "") else (f"recent trend ({lead_trend.get('text')})"
                                                           if lead_trend.get("text") else "expectations")
    mag = qs_gold.get("p80_abs_move_15m_bps")
    mag_txt = f" Historical 80th-pct 15-min gold move: {mag}bp." if mag else ""
    if cat == "inflation":
        hot, cool = (f"Core m/m prints above {ref}, led by services ex-shelter",
                     f"Core m/m prints below {ref} with broad-based softening")
        mixed = "Headline and core diverge (energy/food vs core), or a hot headline with benign core services"
    elif cat == "labor":
        hot, cool = (f"Payrolls above {ref} with steady/lower unemployment and firm wage growth",
                     f"Payrolls below {ref}, unemployment up, and/or large negative revisions")
        mixed = "Payrolls and unemployment send opposite signals, or revisions offset the headline; wages decide"
    elif cat == "central_bank":
        hot, cool = ("Decision/statement more hawkish than priced (fewer cuts signalled, higher dots, hawkish dissents)",
                     "Dovish tilt: cut or explicit easing bias, lower dots, emphasis on labour-market risks")
        mixed = "Statement and press conference diverge — initial move reverses during the Q&A"
    else:
        hot, cool = (f"Release stronger than {ref}", f"Release weaker than {ref}")
        mixed = "Composition contradicts the headline; market looks through to next tier-1 data"
    src_note = "Conditions are relative to " + ref + ("" if cv not in (None, "") else
                                                      " — no licensed consensus feed; treat thresholds as directional")
    us = d.get("country") == "US"
    s_bear = {"name": "Bearish gold", "condition": hot if us else cool,
              "chain": "Hawkish repricing → front-end yields ↑ → USD ↑ → XAUUSD ↓, EURUSD ↓",
              "assets": {"XAUUSD": "down", "EURUSD": "down", "US2Y": "up", "USD": "up"}}
    s_bull = {"name": "Bullish gold", "condition": cool if us else hot,
              "chain": "Dovish repricing → yields ↓ → USD ↓ → XAUUSD ↑, EURUSD ↑",
              "assets": {"XAUUSD": "up", "EURUSD": "up", "US2Y": "down", "USD": "down"}}
    s_mix = {"name": "Mixed / fade", "condition": mixed,
             "chain": "Initial spike driven by headline algos, then reversal as details are digested; "
                      "also likely when the outcome is already priced or positioning is crowded",
             "assets": {"XAUUSD": "two-way", "EURUSD": "two-way", "US2Y": "two-way", "USD": "two-way"}}
    for s in (s_bear, s_bull, s_mix):
        s["note"] = src_note + "." + mag_txt
    if qs_gold.get("after_stronger") and qs_gold.get("after_weaker"):
        st, wk = qs_gold["after_stronger"], qs_gold["after_weaker"]
        s_bear["history"] = f"After stronger-than-trend prints gold fell in {(1-st['share_up'])*100:.0f}% of {st['n']} cases"
        s_bull["history"] = f"After weaker-than-trend prints gold rose in {wk['share_up']*100:.0f}% of {wk['n']} cases"
        if qs_gold.get("status") == "validated":
            s_bear["conditional_prob_gold_down_15m"] = round(1 - st["share_up"], 2)
            s_bull["conditional_prob_gold_up_15m"] = round(wk["share_up"], 2)
    return [s_bear, s_bull, s_mix]
