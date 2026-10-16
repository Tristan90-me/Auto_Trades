"""Settings, read from the .env file next to this script."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _opt(name, default=None):
    value = os.getenv(name, "").strip()
    return value if value else default


# Telegram (your own account, from my.telegram.org)
TG_API_ID = _opt("TG_API_ID")
TG_API_HASH = _opt("TG_API_HASH")
CHANNEL = _opt("CHANNEL")  # @username, t.me link, numeric id, or exact channel title

# Gemini (optional: used for odd message formats and the analyst notes)
GEMINI_API_KEY = _opt("GEMINI_API_KEY")
GEMINI_MODEL = _opt("GEMINI_MODEL", "gemini-2.5-flash")

# Simulation assumptions
STAKE = float(_opt("STAKE", "1"))
PAYOUT = float(_opt("PAYOUT", "0.85"))            # profit per $1 on a win
GALE_MULTIPLIER = float(_opt("GALE_MULTIPLIER", "2"))
DEFAULT_EXPIRY_MIN = int(_opt("DEFAULT_EXPIRY_MIN", "5"))

# Channel clock. Blank = auto-detect from when each message was posted.
SIGNAL_UTC_OFFSET = _opt("SIGNAL_UTC_OFFSET")
SIGNAL_UTC_OFFSET = int(SIGNAL_UTC_OFFSET) if SIGNAL_UTC_OFFSET is not None else None

# Daily report time, in UTC (HH:MM)
REPORT_TIME_UTC = _opt("REPORT_TIME_UTC", "22:00")

DB_PATH = BASE_DIR / "auditor.db"
SESSION_PATH = str(BASE_DIR / "auditor_session")


def require_telegram():
    missing = [n for n, v in (("TG_API_ID", TG_API_ID), ("TG_API_HASH", TG_API_HASH)) if not v]
    if missing:
        raise SystemExit(f"Missing in .env: {', '.join(missing)}. Copy .env.example to .env and fill it in.")
