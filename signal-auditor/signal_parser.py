"""Turn a channel message into a structured signal."""
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import config

DIRECTIONS = {
    "CALL": "CALL", "UP": "CALL", "BUY": "CALL", "COMPRA": "CALL", "HIGHER": "CALL",
    "PUT": "PUT", "DOWN": "PUT", "SELL": "PUT", "VENDA": "PUT", "LOWER": "PUT",
}
_DIR_WORDS = "|".join(DIRECTIONS)

# e.g. "USD/ARS;01:00;PUT", "EUR/USD-OTC 14:05 CALL", "GBPJPY, 09:30, DOWN"
MAIN_RE = re.compile(
    r"\b([A-Z]{3})\s*/?\s*([A-Z]{3})(?:[\s\-_]*\(?OTC\)?)?\s*[;,|\s]\s*(\d{1,2}:\d{2})\s*[;,|\s]\s*"
    rf"({_DIR_WORDS})\b",
    re.IGNORECASE,
)
EXPIRY_RE = re.compile(r"(\d{1,2})\s*-?\s*min(?:ute)?s?\b|\bM(\d{1,2})\b", re.IGNORECASE)
GALE_RE = re.compile(r"GALE\D{0,20}?(\d{1,2}:\d{2})", re.IGNORECASE)
LOOKS_LIKE_SIGNAL_RE = re.compile(rf"\b({_DIR_WORDS})\b", re.IGNORECASE)


@dataclass
class Signal:
    pair: str            # "USD/ARS"
    direction: str       # "CALL" or "PUT"
    entry_hhmm: str      # entry time as written, in the channel's clock
    expiry_min: int
    gales: int = 0       # each gale enters when the previous trade expires
    otc_flag: bool = False
    parsed_by: str = "regex"


def parse_regex(text):
    m = MAIN_RE.search(text)
    if not m:
        return None
    base, quote, hhmm, word = m.groups()
    exp = EXPIRY_RE.search(text)
    expiry = int(exp.group(1) or exp.group(2)) if exp else config.DEFAULT_EXPIRY_MIN
    return Signal(
        pair=f"{base.upper()}/{quote.upper()}",
        direction=DIRECTIONS[word.upper()],
        entry_hhmm=hhmm.zfill(5),
        expiry_min=expiry,
        gales=len(GALE_RE.findall(text)),
        otc_flag="OTC" in text.upper(),
    )


def parse_gemini(text):
    """Fallback for formats the regex doesn't know. Returns None if not a signal."""
    if not config.GEMINI_API_KEY or not LOOKS_LIKE_SIGNAL_RE.search(text):
        return None
    from google import genai

    client = genai.Client(api_key=config.GEMINI_API_KEY)
    prompt = (
        "Extract a binary-options trading signal from this Telegram message. "
        'Reply with JSON only: {"is_signal": bool, "pair": "AAA/BBB", "direction": "CALL"|"PUT", '
        '"entry_time": "HH:MM", "expiry_minutes": int, "gale_count": int, "otc": bool}. '
        "If it is not a new trade signal (e.g. a result post or advert), set is_signal to false.\n\n"
        f"Message:\n{text}"
    )
    try:
        resp = client.models.generate_content(
            model=config.GEMINI_MODEL, contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        data = json.loads(resp.text)
    except Exception as e:  # rate limit, bad JSON, network
        print(f"[gemini] parse failed: {e}")
        return None
    if not data.get("is_signal"):
        return None
    try:
        return Signal(
            pair=data["pair"].upper().replace("-", "/"),
            direction=DIRECTIONS.get(str(data["direction"]).upper(), "CALL"),
            entry_hhmm=data["entry_time"].zfill(5),
            expiry_min=int(data.get("expiry_minutes") or config.DEFAULT_EXPIRY_MIN),
            gales=int(data.get("gale_count") or 0),
            otc_flag=bool(data.get("otc")),
            parsed_by="gemini",
        )
    except (KeyError, TypeError, ValueError):
        return None


def parse(text):
    if not text:
        return None
    return parse_regex(text) or parse_gemini(text)


def _local(day, hhmm, offset_h):
    h, m = map(int, hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m, tzinfo=timezone(timedelta(hours=offset_h)))


def resolve_entry_utc(hhmm, posted_utc, fixed_offset=None):
    """Work out the real UTC entry time.

    Signals are posted shortly before entry, so with no fixed offset we pick the
    whole-hour UTC offset that puts entry 0-58 minutes after the post.
    Returns (entry_utc, offset_hours).
    """
    if fixed_offset is not None:
        best = None
        for d in (-1, 0, 1):
            t = _local(posted_utc.date() + timedelta(days=d), hhmm, fixed_offset).astimezone(timezone.utc)
            if best is None or abs((t - posted_utc).total_seconds()) < abs((best - posted_utc).total_seconds()):
                best = t
        return best, fixed_offset
    for off in range(-12, 15):
        for d in (-1, 0, 1):
            t = _local(posted_utc.date() + timedelta(days=d), hhmm, off).astimezone(timezone.utc)
            if -120 <= (t - posted_utc).total_seconds() < 3480:
                return t, off
    return None, None


def entry_times_utc(sig, posted_utc, fixed_offset=None):
    """[entry, gale1, gale2, ...] in UTC, or [] if the time can't be resolved."""
    entry, off = resolve_entry_utc(sig.entry_hhmm, posted_utc, fixed_offset)
    if entry is None:
        return [], None
    step = timedelta(minutes=sig.expiry_min)
    return [entry + k * step for k in range(sig.gales + 1)], off
