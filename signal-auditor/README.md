# Signal Auditor

Logs every signal from a Telegram channel and checks it against real market prices, with
the gales included. Sends you a daily report in your Telegram **Saved Messages**.
It never places trades and never touches a broker.

## Setup (once)

1. Open a terminal in this folder and run:

   ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and fill in `TG_API_ID`, `TG_API_HASH` and (optionally)
   `GEMINI_API_KEY`. Don't share `.env` with anyone, including in chat.

3. Find the channel and log in:

   ```
   python main.py channels
   ```

   The first time, it asks for your phone number and the login code Telegram sends you.
   It then lists your channels. Copy the channel's name or id into `CHANNEL=` in `.env`.

## Use

```
python main.py backfill 500    # import the last 500 messages and score what it can
python main.py report 30       # print a report for the last 30 days
python main.py run             # keep running: logs new signals, scores every 15 min, daily report
```

Leave `run` going in a terminal window. The PC must be on and awake for it to log signals.

## What the report means

- **real-market**: a major forex pair while the real market is open. Checked against free
  Yahoo Finance 1-minute prices. Quotex's own quotes differ slightly, so treat single
  results as approximate and the totals over weeks as the real signal.
- **OTC / exotic**: prices Quotex generates itself (weekends, and pairs like USD/ARS).
  These are logged but can't be checked independently.
- **First-entry win rate vs. break-even**: at an 85% payout you need to win more than 54.1%
  of single trades to make money. The "likely true range" shows how sure that number is;
  with few signals it is very wide.
- **'Win' incl. gales**: the number channels advertise. It can be high while the
  P&L line is negative, because one full loss (-$7 at $1) wipes out many small wins.

Free 1-minute price history only goes back about 30 days, so older backfilled signals show
as "no price data".
