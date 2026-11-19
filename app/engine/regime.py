"""Layer A — macro regime detection from observable, sourced data.

Each signal returns a value, a rule-based label, the rule used, and provenance.
Missing sources are recorded as gaps rather than filled in.
"""
from statistics import mean

from .. import db
from ..providers import registry as R
from ..providers.base import HISTORICAL, MARKET, OFFICIAL, ProviderError, fact
from . import data


def _ann3m(levels: list[dict]):
    if len(levels) < 4:
        return None
    return ((levels[-1]["value"] / levels[-4]["value"]) ** 4 - 1) * 100


def compute(force=False) -> dict:
    asof = data.ASOF.get()
    if asof:
        force = True  # point-in-time runs never read or write the live cache
    if not force:
        c = db.cache_get("regime", 3600)
        if c and c.get("degraded"):
            c = db.cache_get("regime", 300)  # retry soon after a provider outage
        if c:
            return c
    now = db.iso(asof or db.now_utc())
    signals, gaps, labels = {}, [], []

    # --- Inflation ---------------------------------------------------------
    try:
        obs, prov = data.series("CPILFESL")
        yoy = data.transform(obs, "yoy_pct")[-1]["value"]
        a3 = _ann3m(obs)
        if a3 is not None and yoy > 3.0 and a3 >= yoy - 0.2:
            lab = "Persistent inflation"
        elif a3 is not None and a3 > yoy + 0.3:
            lab = "Inflation re-accelerating"
        elif a3 is not None and a3 < yoy - 0.3:
            lab = "Disinflation"
        else:
            lab = "Inflation stable" + (" above target" if yoy > 2.5 else " near target")
        signals["inflation"] = {
            "label": lab, "core_cpi_yoy": data.provenance_fact(round(yoy, 2), prov, obs[-1]["date"]),
            "core_cpi_3m_ann": data.provenance_fact(round(a3, 2) if a3 else None, prov, obs[-1]["date"]),
            "rule": "3m annualised core CPI vs y/y: >+0.3pp re-accelerating, <-0.3pp disinflation; y/y>3 & 3m≥y/y persistent",
            "reference_period": obs[-1]["date"]}
        labels.append(lab)
    except ProviderError as e:
        gaps.append(f"Inflation regime: {e}")

    # --- Labour ------------------------------------------------------------
    try:
        obs, prov = data.series("UNRATE")
        v = [o["value"] for o in obs]
        if len(v) >= 15:
            ma3 = [mean(v[i - 2:i + 1]) for i in range(2, len(v))]
            sahm = ma3[-1] - min(ma3[-13:-1])
            lab = ("Deteriorating labour market" if sahm >= 0.5 else
                   "Softening labour market" if sahm >= 0.3 else
                   "Strong labour market" if v[-1] < 4.2 and sahm < 0.15 else "Stable labour market")
            sig = {"label": lab, "unemployment": data.provenance_fact(v[-1], prov, obs[-1]["date"]),
                   "sahm_gap": data.provenance_fact(round(sahm, 2), prov, obs[-1]["date"], derived=True),
                   "rule": "Sahm-style gap: 3m avg UR minus prior-12m low of 3m avg (≥0.5 deteriorating, ≥0.3 softening)",
                   "reference_period": obs[-1]["date"]}
            try:
                pobs, pprov = data.series("PAYEMS")
                ch = data.transform(pobs, "change_k")
                sig["payrolls_3m_avg_k"] = data.provenance_fact(round(mean(c["value"] for c in ch[-3:]), 1),
                                                                pprov, pobs[-1]["date"], derived=True)
            except ProviderError as e:
                gaps.append(f"Payrolls: {e}")
            signals["labor"] = sig
            labels.append(lab)
    except ProviderError as e:
        gaps.append(f"Labour regime: {e}")

    # --- Rates, policy pricing, real yields (US Treasury, keyless) ----------
    try:
        nom, real = data.yields()
        last = nom[-1]
        y2, y10, b3 = last.get("BC_2YEAR"), last.get("BC_10YEAR"), last.get("BC_3MONTH")
        url = R.treasury.docs
        rates = {"as_of": last["date"],
                 "us2y": fact(y2, "US Treasury par curve", url, last["date"], MARKET),
                 "us10y": fact(y10, "US Treasury par curve", url, last["date"], MARKET),
                 "curve_2s10s_bp": fact(round((y10 - y2) * 100), "US Treasury (derived)", url, last["date"], MARKET),
                 "us2y_chg_20d_bp": fact(round((data.change(nom, "BC_2YEAR", 20) or 0) * 100), "US Treasury (derived)",
                                         url, last["date"], MARKET)}
        spread = (y2 - b3) * 100 if (y2 and b3) else None
        if spread is not None:
            pol = ("Market pricing easing" if spread < -25 else "Market pricing tightening" if spread > 25
                   else "Market pricing broadly on hold")
            rates["policy_pricing"] = {"label": pol,
                                       "spread_2y_3m_bp": fact(round(spread), "US Treasury (derived)", url, last["date"], MARKET),
                                       "rule": "2y minus 3m bill: < -25bp easing priced, > +25bp tightening priced (proxy for OIS/fed funds futures)"}
            labels.append(pol)
        if real:
            r10 = real[-1].get("TC_10YEAR")
            ch = data.change(real, "TC_10YEAR", 20)
            rates["real10y"] = fact(r10, "US Treasury real yield curve", url, real[-1]["date"], MARKET)
            rates["real10y_chg_20d_bp"] = fact(round((ch or 0) * 100), "US Treasury (derived)", url, real[-1]["date"], MARKET)
            rlab = "Restrictive real rates" if r10 and r10 > 1.5 else "Moderate real rates" if r10 and r10 > 0.5 else "Low real rates"
            rates["real_rate_label"] = rlab
            labels.append(rlab)
        signals["rates"] = rates
    except ProviderError as e:
        gaps.append(f"Treasury yields: {e}")

    # --- Fed policy rate & risk sentiment (FRED) -----------------------------
    if R.fred.configured():
        for sid, name in (("DFEDTARU", "fed_funds_upper"), ("VIXCLS", "vix"), ("DTWEXBGS", "usd_broad"),
                          ("T5YIE", "breakeven5y")):
            try:
                obs = data.fred_obs(sid, start="2016-01-01")
                f = fact(obs[-1]["value"], f"FRED {sid}", f"https://fred.stlouisfed.org/series/{sid}",
                         obs[-1]["date"], MARKET if sid != "DFEDTARU" else OFFICIAL)
                if len(obs) > 21:
                    f["chg_20"] = round(obs[-1]["value"] - obs[-21]["value"], 3)
                signals.setdefault("markets", {})[name] = f
            except ProviderError as e:
                gaps.append(f"{sid}: {e}")
        vix = signals.get("markets", {}).get("vix")
        if vix:
            risk = "Risk-off" if vix["value"] > 25 else "Risk-on" if vix["value"] < 15 else "Neutral risk sentiment"
            signals["risk"] = {"label": risk, "rule": "VIX >25 risk-off, <15 risk-on"}
            labels.append(risk)
    else:
        gaps.append("Fed funds target, VIX, broad USD index, breakevens: need FRED_API_KEY")

    # --- Positioning (CFTC, keyless) ---------------------------------------
    try:
        pos = data.cot("XAUUSD")
        hist = [p["net_pct_oi"] for p in pos if p["net_pct_oi"] is not None]
        cur = hist[-1]
        pct = sum(1 for h in hist if h <= cur) / len(hist) * 100
        signals["gold_positioning"] = {
            "net_pct_oi": fact(round(cur, 1), "CFTC COT (non-commercial, COMEX gold)", R.cftc.docs, pos[-1]["date"], MARKET),
            "percentile_3y": round(pct), "label": "Crowded long" if pct >= 80 else "Light positioning" if pct <= 20 else "Neutral positioning",
            "rule": "Speculative net long as % of open interest vs 3-year history"}
    except (ProviderError, IndexError, ZeroDivisionError) as e:
        gaps.append(f"Gold positioning: {e}")

    degraded = any("need" not in g and "Needs" not in g for g in gaps)
    out = {"as_of": now, "labels": labels, "signals": signals, "gaps": gaps, "degraded": degraded,
           "headline": " · ".join(labels[:4]) if labels else "Insufficient data to classify regime"}
    if not asof:
        db.cache_put("regime", out)
    return out
