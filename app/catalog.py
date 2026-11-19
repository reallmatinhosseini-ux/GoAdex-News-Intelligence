"""Catalogue of supported events: how to find them, which data describes them,
which components matter, and how a surprise transmits to markets.

`surprise_sign` is the *textbook prior* for a positive (hawkish/stronger) surprise:
+1 means "higher than expected => USD-positive / yields up". The reasoning engine
never applies it blindly: it is adjusted by regime, pricing, positioning and the
empirically measured event study (see engine/reasoning.py).
"""

# FRED release ids (https://fred.stlouisfed.org/releases) used for schedule discovery.
# BLS series ids are used as a keyless fallback for actual values.
CATALOG = {
    "US_NFP": {
        "name": "Non-Farm Payrolls (Employment Situation)", "country": "US", "category": "labor",
        "tier": 1, "et_time": "08:30", "fred_release_id": 50,
        "match": ["employment situation", "non-farm payroll", "nonfarm payroll"],
        "series": {"headline": ("FRED", "PAYEMS", "change_k"), "unemployment": ("FRED", "UNRATE", "level"),
                   "ahe": ("FRED", "CES0500000003", "mom_pct"), "participation": ("FRED", "CIVPART", "level")},
        "bls": {"headline": "CES0000000001", "unemployment": "LNS14000000", "ahe": "CES0500000003"},
        "components": ["Payrolls change", "Prior-month revisions", "Unemployment rate",
                       "Average hourly earnings m/m", "Participation rate"],
        "surprise_sign": +1, "leading": ["ICSA", "ADPMNUSNERSA", "JTSJOL"],
    },
    "US_UNRATE": {"alias_of": "US_NFP", "name": "Unemployment Rate"},
    "US_AHE": {"alias_of": "US_NFP", "name": "Average Hourly Earnings"},
    "US_CPI": {
        "name": "Consumer Price Index (CPI / Core CPI)", "country": "US", "category": "inflation",
        "tier": 1, "et_time": "08:30", "fred_release_id": 10,
        "match": ["consumer price index", " cpi"],
        "series": {"headline": ("FRED", "CPIAUCSL", "mom_pct"), "core": ("FRED", "CPILFESL", "mom_pct"),
                   "headline_yoy": ("FRED", "CPIAUCSL", "yoy_pct"), "core_yoy": ("FRED", "CPILFESL", "yoy_pct")},
        "bls": {"headline": "CUSR0000SA0", "core": "CUSR0000SA0L1E"},
        "components": ["Headline m/m", "Core m/m", "Core services ex-shelter", "Shelter", "Core goods",
                       "Headline y/y", "Core y/y"],
        "surprise_sign": +1, "leading": ["PPIFIS", "MICH", "T5YIE"],
    },
    "US_PCE": {
        "name": "Personal Income & Outlays (Core PCE)", "country": "US", "category": "inflation",
        "tier": 1, "et_time": "08:30", "fred_release_id": 54,
        "match": ["personal income and outlays", "pce price", "personal consumption expenditures"],
        "series": {"core": ("FRED", "PCEPILFE", "mom_pct"), "headline": ("FRED", "PCEPI", "mom_pct"),
                   "core_yoy": ("FRED", "PCEPILFE", "yoy_pct"), "spending": ("FRED", "PCE", "mom_pct")},
        "components": ["Core PCE m/m", "Headline PCE m/m", "Core PCE y/y", "Real spending", "Personal income"],
        "surprise_sign": +1, "leading": ["CPILFESL", "PPIFIS"],
    },
    "US_FOMC": {
        "name": "FOMC Rate Decision & Statement", "country": "US", "category": "central_bank",
        "tier": 1, "et_time": "14:00", "match": ["fomc meeting"],
        "series": {"upper_bound": ("FRED", "DFEDTARU", "level")},
        "components": ["Rate decision", "Statement language", "Dissents",
                       "Summary of Economic Projections / dot plot (quarterly meetings)", "Balance-sheet guidance"],
        "surprise_sign": +1, "leading": ["DGS2", "DFF"],
    },
    "US_FOMC_PRESSER": {
        "name": "FOMC Press Conference", "country": "US", "category": "central_bank",
        "tier": 1, "et_time": "14:30", "match": ["fomc press conference"],
        "components": ["Chair's characterisation of risks", "Forward guidance", "Answers on balance sheet"],
        "surprise_sign": +1, "leading": ["DGS2"],
    },
    "US_FOMC_MINUTES": {
        "name": "FOMC Minutes", "country": "US", "category": "central_bank",
        "tier": 2, "et_time": "14:00", "match": ["fomc minutes"],
        "components": ["Balance of views", "Discussion of future path"], "surprise_sign": +1, "leading": ["DGS2"],
    },
    "US_GDP": {
        "name": "Gross Domestic Product", "country": "US", "category": "growth",
        "tier": 2, "et_time": "08:30", "fred_release_id": 53, "match": ["gross domestic product"],
        "series": {"headline": ("FRED", "A191RL1Q225SBEA", "level")},
        "components": ["Real GDP q/q saar", "Final sales to private domestic purchasers", "GDP price index"],
        "surprise_sign": +1,
    },
    "US_ISM_MFG": {"name": "ISM Manufacturing PMI", "country": "US", "category": "growth", "tier": 2,
                   "et_time": "10:00", "match": ["ism manufacturing"], "te_only": True,
                   "components": ["Headline", "Prices paid", "New orders", "Employment"], "surprise_sign": +1},
    "US_ISM_SVC": {"name": "ISM Services PMI", "country": "US", "category": "growth", "tier": 2,
                   "et_time": "10:00", "match": ["ism services", "ism non-manufacturing"], "te_only": True,
                   "components": ["Headline", "Prices paid", "Employment"], "surprise_sign": +1},
    "US_RETAIL": {
        "name": "Retail Sales", "country": "US", "category": "consumption", "tier": 2, "et_time": "08:30",
        "fred_release_id": 9, "match": ["advance monthly sales for retail", "retail sales"],
        "series": {"headline": ("FRED", "RSAFS", "mom_pct")},
        "components": ["Headline m/m", "Ex-autos", "Control group"], "surprise_sign": +1,
    },
    "US_PPI": {
        "name": "Producer Price Index", "country": "US", "category": "inflation", "tier": 2, "et_time": "08:30",
        "fred_release_id": 46, "match": ["producer price index"],
        "series": {"headline": ("FRED", "PPIFIS", "mom_pct")}, "bls": {"headline": "WPSFD4"},
        "components": ["Final demand m/m", "Core m/m", "Components feeding core PCE"], "surprise_sign": +1,
    },
    "US_CLAIMS": {
        "name": "Initial Jobless Claims", "country": "US", "category": "labor", "tier": 3, "et_time": "08:30",
        "fred_release_id": 180, "match": ["unemployment insurance weekly claims", "jobless claims"],
        "series": {"headline": ("FRED", "ICSA", "level")},
        "components": ["Initial claims", "Continuing claims"], "surprise_sign": -1,  # higher claims = weaker
    },
    "US_ADP": {"name": "ADP Employment", "country": "US", "category": "labor", "tier": 3, "et_time": "08:15",
               "fred_release_id": 194, "match": ["adp national employment"],
               "series": {"headline": ("FRED", "ADPMNUSNERSA", "change_k")},
               "components": ["Private payrolls change", "Pay growth"], "surprise_sign": +1},
    "US_JOLTS": {"name": "JOLTS Job Openings", "country": "US", "category": "labor", "tier": 3, "et_time": "10:00",
                 "fred_release_id": 192, "match": ["job openings and labor turnover"],
                 "series": {"headline": ("FRED", "JTSJOL", "level")},
                 "components": ["Openings", "Quits rate", "Hires"], "surprise_sign": +1},
    "US_CONF": {"name": "Conference Board Consumer Confidence", "country": "US", "category": "sentiment",
                "tier": 3, "et_time": "10:00", "match": ["consumer confidence"], "te_only": True,
                "components": ["Headline", "Labor differential"], "surprise_sign": +1},
    "US_UMICH": {"name": "UMich Sentiment & Inflation Expectations", "country": "US", "category": "sentiment",
                 "tier": 3, "et_time": "10:00", "fred_release_id": 91, "match": ["surveys of consumers", "michigan"],
                 "series": {"inflation_1y": ("FRED", "MICH", "level"), "sentiment": ("FRED", "UMCSENT", "level")},
                 "components": ["1-yr inflation expectations", "5-10yr inflation expectations", "Sentiment"],
                 "surprise_sign": +1},
    "US_FED_SPEECH": {"name": "Federal Reserve Speech", "country": "US", "category": "central_bank_speech",
                      "tier": 3, "match": [], "components": ["Policy signal", "Data-dependence remarks"],
                      "surprise_sign": +1},
    "US_BEIGE": {"name": "Beige Book", "country": "US", "category": "central_bank", "tier": 3,
                 "et_time": "14:00", "match": ["beige book"], "components": ["Activity", "Prices", "Labor"],
                 "surprise_sign": +1},
    "EU_ECB": {"name": "ECB Monetary Policy Decision", "country": "EA", "category": "central_bank", "tier": 1,
               "match": ["ecb interest rate decision", "deposit facility rate"], "te_only": True,
               "components": ["Deposit rate", "Statement", "Staff projections"], "surprise_sign": -1},  # USD-relative
    "EU_ECB_PRESSER": {"name": "ECB Press Conference", "country": "EA", "category": "central_bank", "tier": 1,
                       "match": ["ecb press conference"], "te_only": True,
                       "components": ["President's guidance"], "surprise_sign": -1},
}

# Events that warrant the full pre-news pipeline (tier 1 always; tier 2 when context promotes them).
PIPELINE_TIERS = {1, 2}

ASSETS = {
    "XAUUSD": {"label": "Gold", "primary": True, "td_symbol": "XAU/USD"},
    "EURUSD": {"label": "EUR/USD", "primary": False, "td_symbol": "EUR/USD"},
    "USDJPY": {"label": "USD/JPY", "td_symbol": "USD/JPY"},
    "GBPUSD": {"label": "GBP/USD", "td_symbol": "GBP/USD"},
    "US500": {"label": "S&P 500 (SPY proxy)", "td_symbol": "SPY"},
    "NAS100": {"label": "Nasdaq 100 (QQQ proxy)", "td_symbol": "QQQ"},
}

# How a USD-positive / yields-up shock typically transmits, per asset (prior, not law).
TRANSMISSION = {
    "XAUUSD": {"usd": -1, "real_yield": -1, "risk_off": +1,
               "path": "Surprise → policy expectations → real yields & USD → gold (inverse)"},
    "EURUSD": {"usd": -1, "real_yield": 0, "risk_off": -0.3,
               "path": "Surprise → Fed-ECB rate differential → USD → EUR/USD (inverse)"},
    "USDJPY": {"usd": +1, "real_yield": +0.5, "risk_off": -0.5, "path": "US yields → UST-JGB spread → USD/JPY"},
    "GBPUSD": {"usd": -1, "real_yield": 0, "risk_off": -0.3, "path": "Fed-BoE differential → GBP/USD"},
    "US500": {"usd": 0, "real_yield": -0.6, "risk_off": -1, "path": "Discount rates vs growth news → equities"},
    "NAS100": {"usd": 0, "real_yield": -0.9, "risk_off": -1, "path": "Long-duration growth equities ↔ real yields"},
}


def resolve(key: str) -> dict:
    d = CATALOG.get(key, {})
    if "alias_of" in d:
        base = dict(CATALOG[d["alias_of"]])
        base["name"] = d["name"]
        return base
    return d


def match_title(title: str) -> str | None:
    t = " " + title.lower()
    for key, d in CATALOG.items():
        if "alias_of" in d:
            continue
        for m in d.get("match", []):
            if m in t:
                return key
    if "speech" in t or "discussion" in t:
        return "US_FED_SPEECH"
    return None
