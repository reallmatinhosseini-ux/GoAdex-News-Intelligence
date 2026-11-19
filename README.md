# GoAdex News Intelligence

Pre-release news forecasting and backtesting for Gold (XAUUSD) and EUR/USD.

A one-page dashboard. It researches important upcoming economic news in the background and shows
which way gold (XAUUSD) and EUR/USD are expected to move. Every event has an **Evidence** button
that opens the full reasoning and sources.

## Run

```bash
cp .env.example .env    # optional: add keys
./run.sh                # http://127.0.0.1:8600
```

Nothing ever needs to be requested by hand. The server:

1. Finds upcoming releases automatically from the Fed, BEA and (with keys) FRED or Trading Economics calendars.
2. Researches each important event: first up to 7 days ahead, again at 24 h and 6 h before, and a final report at 75 minutes before release. That guarantees the analysis is ready at least 1 hour early.
3. Re-checks every 10 minutes during the final hour. A new version is written only when something changes, and it records why.
4. Pulls the actual figures from official sources once they are released.

## Prediction timeline (automatic, for XAUUSD and EURUSD)

| When | What happens |
|---|---|
| T-33 min | Evidence refreshed (macro, rates, dollar, positioning, consensus) |
| **T-30 min** | **Direction forecast** published: Bullish/Bearish, confidence, reasoning, for both assets |
| **T-2 min** | **Final targets** published from the live bid (seconds old) and volatility measured up to the last minute: price, target, move ($ for gold, pips for EURUSD), typical time to target, 80% range, confidence, data timestamp |
| T+20 s onward | Actual release figures fetched from official sources (shown only once published) |
| T+62 min | Both stages scored at 1, 5, 15, 30 and 60 minutes; stored separately |

Time-critical stage jobs run on their own worker thread, so long background work can never delay them.
Live prices: Swissquote bid/ask, recorded every 10 s (`app/recorder.py`); the page refreshes them every 5 s.

## How the numbers are calculated (`app/engine/model.py`)

- **Move size**: ridge regression of log(1+|30-min move|) on event type and pre-release volatility, trained
  walk-forward on earlier releases only. Used only if it beats the naive median out of sample.
- **Direction**: ridge logistic regression on point-in-time fundamentals + an analog model (15 most similar
  past releases). Published only if earlier out-of-sample calls beat 50% significantly (Wilson lower bound,
  90%) with Brier < 0.25; otherwise "Insufficient Evidence".
- **Target** = live price ± expected move. **Range** = 80th percentile of the move distribution (coverage
  measured historically). **Time to target** = median minutes past releases took to move that far.
- Every prediction carries its step-by-step calculation. If data is missing or stale: "Prediction Not Ready".

## What you see

- **Analysis Ready / Not Ready:** whether research for that event has finished.
- **Direction:** Bullish, Bearish or Neutral. If there isn't enough reliable data, it shows **Insufficient Evidence** instead of guessing (under 45% of the needed inputs, or fewer than two meaningful signals).
- **Evidence page:** summary, every piece of evidence with its source, possible outcomes, what would prove the call wrong, key numbers, economic backdrop, missing data, and update history.

## Data

These work with no keys: the Federal Reserve, BEA, BLS, US Treasury yields and CFTC positioning.

Optional keys add more:

| Key | Adds |
|---|---|
| `FRED_API_KEY` (free) | CPI, jobs report, PPI, retail sales and other release dates, plus more macro inputs |
| `TWELVEDATA_API_KEY` | Gold and EUR/USD prices, price levels, historical reaction patterns |
| `TRADINGECONOMICS_API_KEY` | Consensus forecasts and ECB/ISM schedules |
| `ANTHROPIC_API_KEY` | AI web research with verified sources, plus a written strategist view on the Evidence page |

Missing data is never filled in with estimates. It lowers confidence instead, and the Evidence page
lists it under "Missing data".
