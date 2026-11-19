"""Runtime configuration, read from environment variables (see .env.example)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


FRED_API_KEY = _env("FRED_API_KEY")
BLS_API_KEY = _env("BLS_API_KEY")
TWELVEDATA_API_KEY = _env("TWELVEDATA_API_KEY")
TRADINGECONOMICS_API_KEY = _env("TRADINGECONOMICS_API_KEY")
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = _env("ANTHROPIC_MODEL", "claude-opus-5-5")
LLM_WEB_RESEARCH = _env("LLM_WEB_RESEARCH", "1") == "1"

DEFAULT_TIMEZONE = _env("DEFAULT_TIMEZONE", "Asia/Dubai")
# Serverless hosts (Vercel / AWS Lambda) have a read-only code directory and no long-running process:
# the app then runs as the public website only (no recorder/scheduler) and keeps scratch data in /tmp.
SERVERLESS = bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
DB_PATH = ROOT / _env("DB_PATH", "/tmp/goadex/intel.db" if SERVERLESS else "data/intel.db")
SNAPSHOT_PATH = ROOT / "snapshot" / "public.json"

# Model / pipeline versioning — stored on every forecast so the archive is auditable.
ENGINE_VERSION = "3.0.0"

# Scheduling policy (minutes relative to release time)
STAGE_EARLY_HOURS = 168         # first research pass (up to 7 days ahead)
STAGE_DAY_HOURS = 24            # daily refresh
STAGE_REFRESH_HOURS = 6         # refresh pass
FINAL_REPORT_LEAD_MIN = 75      # build final report at T-75 ...
REPORT_DEADLINE_MIN = 60        # ... so it is guaranteed available by T-60
MONITOR_INTERVAL_MIN = 10       # T-60 .. T-2 monitoring cadence
EVAL_WINDOWS_MIN = [1, 5, 15, 60]

# Quant-model validation gate: probabilities are only shown when out-of-sample
# walk-forward evaluation has at least this many predictions AND beats the base rate.
MIN_OOS_SAMPLES = 30

USER_AGENT = "NewsIntel/1.0 (macro research platform; non-commercial)"
