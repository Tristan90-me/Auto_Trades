"""Check signals against real 1-minute market prices and simulate the gale sequence."""
import json
from datetime import datetime, timedelta, timezone

import config
import db

MAJORS = {"EUR", "USD", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD"}


def forex_closed(t):
    """Spot forex is shut roughly Fri 21:00 UTC to Sun 22:00 UTC; Quotex shows only OTC then."""
    wd, h = t.weekday(), t.hour
    return wd == 5 or (wd == 4 and h >= 21) or (wd == 6 and h < 22)


def classify(pair, otc_flag, entry_utc):
    """real = we can check it against real prices; otc = broker-generated price;
    unverifiable = exotic pair that is almost always OTC on Quotex."""
    if otc_flag or forex_closed(entry_utc):
        return "otc"
    base, quote = pair.split("/")
    if base in MAJORS and quote in MAJORS:
        return "real"
    return "unverifiable"


def simulate(results, stake=None, payout=None, mult=None):
    """results: list of 'win' | 'loss' | 'draw' per step, in order.
    Returns (outcome, pnl_following_gales, pnl_first_entry_only)."""
    stake = config.STAKE if stake is None else stake
    payout = config.PAYOUT if payout is None else payout
    mult = config.GALE_MULTIPLIER if mult is None else mult

    first = results[0]
    pnl_nogale = stake * payout if first == "win" else (0.0 if first == "draw" else -stake)

    pnl = 0.0
    for k, r in enumerate(results):
        s = stake * mult ** k
        if r == "win":
            return f"win{k}", round(pnl + s * payout, 4), round(pnl_nogale, 4)
        if r == "draw":  # stake refunded; sequence ends
            return f"draw{k}", round(pnl, 4), round(pnl_nogale, 4)
        pnl -= s
    return "loss", round(pnl, 4), round(pnl_nogale, 4)


def _fetch_minutes(pair, start, end):
    import yfinance as yf

    ticker = pair.replace("/", "") + "=X"
    df = yf.Ticker(ticker).history(start=start, end=end, interval="1m")
    if df is None or df.empty:
        return None
    df.index = df.index.tz_convert("UTC")
    return df


def _price_at(df, t):
    """Price at the start of minute t: that candle's open, or the previous close if the candle is missing."""
    ts = t.replace(second=0, microsecond=0)
    if ts in df.index:
        return float(df.loc[ts, "Open"])
    before = df[df.index < ts]
    if not before.empty and (ts - before.index[-1]) <= timedelta(minutes=3):
        return float(before["Close"].iloc[-1])
    return None


def score_row(row, df):
    entries = [datetime.fromisoformat(t) for t in json.loads(row["entries_json"])]
    expiry = timedelta(minutes=row["expiry_min"])
    steps, results = [], []
    for t in entries:
        strike, exit_ = _price_at(df, t), _price_at(df, t + expiry)
        if strike is None or exit_ is None:
            return None
        if exit_ == strike:
            r = "draw"
        elif (exit_ > strike) == (row["direction"] == "CALL"):
            r = "win"
        else:
            r = "loss"
        steps.append({"at": t.isoformat(), "strike": strike, "exit": exit_, "result": r})
        results.append(r)
        if r != "loss":  # no further gale needed
            break
    outcome, pnl_gale, pnl_nogale = simulate(results)
    return steps, outcome, pnl_gale, pnl_nogale


def score_pending(conn=None):
    """Score every pending signal whose last expiry is far enough in the past. Returns count scored."""
    conn = conn or db.connect()
    now = datetime.now(timezone.utc)
    done = 0
    for row in db.pending(conn):
        entries = [datetime.fromisoformat(t) for t in json.loads(row["entries_json"])]
        last_exit = entries[-1] + timedelta(minutes=row["expiry_min"])
        if now - last_exit < timedelta(minutes=10):  # give the data feed time to catch up
            continue
        try:
            df = _fetch_minutes(row["pair"], entries[0] - timedelta(minutes=10), last_exit + timedelta(minutes=5))
        except Exception as e:
            print(f"[score] {row['pair']} fetch failed: {e}")
            df = None
        result = score_row(row, df) if df is not None else None
        if result is None:
            # If the free feed still has no candles a day later (or the signal is older
            # than its ~30-day history), it never will.
            if now - last_exit > timedelta(days=1):
                db.save_score(conn, row["id"], status="no_data")
            continue
        steps, outcome, pnl_gale, pnl_nogale = result
        db.save_score(conn, row["id"], status="scored", outcome=outcome, steps=steps,
                      pnl_gale=pnl_gale, pnl_nogale=pnl_nogale)
        done += 1
    return done
