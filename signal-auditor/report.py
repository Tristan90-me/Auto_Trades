"""Build the daily report from the journal."""
import json
import math
from collections import defaultdict
from datetime import datetime, timezone

import config
import db


def wilson(wins, n, z=1.96):
    """95% range for a true win rate, given wins out of n."""
    if n == 0:
        return 0.0, 0.0
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def stats(rows):
    s = {
        "signals": len(rows),
        "by_market": defaultdict(int),
        "pending": 0, "no_data": 0,
        "scored": 0, "first_wins": 0, "first_decided": 0,
        "outcomes": defaultdict(int),
        "pnl_gale": 0.0, "pnl_nogale": 0.0,
        "worst_loss": 0.0,
        "by_pair": defaultdict(lambda: {"n": 0, "first_wins": 0, "pnl_gale": 0.0}),
        "by_hour": defaultdict(lambda: {"n": 0, "first_wins": 0}),
    }
    for r in rows:
        s["by_market"][r["market"]] += 1
        if r["status"] == "pending":
            s["pending"] += 1
        if r["status"] == "no_data":
            s["no_data"] += 1
        if r["status"] != "scored":
            continue
        s["scored"] += 1
        s["outcomes"][r["outcome"]] += 1
        s["pnl_gale"] += r["pnl_gale"]
        s["pnl_nogale"] += r["pnl_nogale"]
        s["worst_loss"] = min(s["worst_loss"], r["pnl_gale"])
        first = json.loads(r["steps_json"])[0]["result"]
        won = first == "win"
        if first != "draw":
            s["first_decided"] += 1
            s["first_wins"] += won
        p = s["by_pair"][r["pair"]]
        p["n"] += 1
        p["first_wins"] += won
        p["pnl_gale"] += r["pnl_gale"]
        h = s["by_hour"][datetime.fromisoformat(json.loads(r["entries_json"])[0]).hour]
        h["n"] += 1
        h["first_wins"] += won
    return s


def _pct(x):
    return f"{x * 100:.1f}%"


def format_section(title, s):
    lines = [f"**{title}**", f"Signals received: {s['signals']}"]
    m = s["by_market"]
    lines.append(f"  real-market: {m.get('real', 0)} | OTC: {m.get('otc', 0)} | exotic/unverifiable: {m.get('unverifiable', 0)}")
    if s["pending"] or s["no_data"]:
        lines.append(f"  waiting for price data: {s['pending']} | no price data: {s['no_data']}")
    n = s["scored"]
    if n == 0:
        lines.append("No signals checked against real prices yet.")
        return "\n".join(lines)

    o = s["outcomes"]
    seq_wins = sum(v for k, v in o.items() if k.startswith("win"))
    lo, hi = wilson(s["first_wins"], s["first_decided"])
    breakeven = 1 / (1 + config.PAYOUT)
    lines += [
        f"Checked against real prices: {n}",
        f"  First-entry win rate: {_pct(s['first_wins'] / max(s['first_decided'], 1))} "
        f"(likely true range {_pct(lo)}-{_pct(hi)}; break-even is {_pct(breakeven)})",
        f"  'Win' incl. gales (what the channel advertises): {_pct(seq_wins / n)}",
        f"  Outcomes: first entry {o.get('win0', 0)}, 1st gale {o.get('win1', 0)}, "
        f"2nd gale {o.get('win2', 0)}, full loss {o.get('loss', 0)}, draws {sum(v for k, v in o.items() if k.startswith('draw'))}",
        f"Simulated P&L at ${config.STAKE:g} stake, {_pct(config.PAYOUT)} payout:",
        f"  following gales: ${s['pnl_gale']:+.2f}"
        + (f" (worst single signal ${s['worst_loss']:+.2f})" if s["worst_loss"] < 0 else ""),
        f"  first entry only: ${s['pnl_nogale']:+.2f}",
    ]
    pairs = sorted(s["by_pair"].items(), key=lambda kv: -kv[1]["n"])[:6]
    if pairs:
        lines.append("By pair (first-entry wins / signals, P&L with gales):")
        lines += [f"  {k}: {v['first_wins']}/{v['n']}, ${v['pnl_gale']:+.2f}" for k, v in pairs]
    return "\n".join(lines)


def analyst_notes(day_stats, all_stats):
    if not config.GEMINI_API_KEY or all_stats["scored"] == 0:
        return None
    from google import genai

    def plain(s):
        return json.loads(json.dumps(s, default=lambda o: dict(o)))

    prompt = (
        "You are auditing a Telegram binary-options signal channel for its follower. Below are statistics "
        "from checking its signals against real market prices. Write 4-6 short bullet points: what the "
        "numbers say about whether the channel has a real edge, any patterns by pair or UTC hour, and how "
        "much to trust them given the sample size. Be blunt and factual, no hype, no trading advice. "
        f"Break-even first-entry win rate at {config.PAYOUT:.0%} payout is {1 / (1 + config.PAYOUT):.1%}.\n\n"
        f"Today: {json.dumps(plain(day_stats))}\n\nAll time: {json.dumps(plain(all_stats))}"
    )
    try:
        client = genai.Client(api_key=config.GEMINI_API_KEY)
        return client.models.generate_content(model=config.GEMINI_MODEL, contents=prompt).text.strip()
    except Exception as e:
        return f"(Gemini notes unavailable: {e})"


def build(conn=None, days=1):
    conn = conn or db.connect()
    day = stats(db.since(conn, days))
    total = stats(db.all_signals(conn))
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    parts = [
        f"📊 Signal audit — {date} (UTC)",
        format_section(f"Last {days} day(s)", day),
        format_section("All time", total),
    ]
    notes = analyst_notes(day, total)
    if notes:
        parts.append("**Analyst notes (Gemini)**\n" + notes)
    parts.append("OTC prices are set by the broker, so OTC signals can't be checked independently.")
    return "\n\n".join(parts)
