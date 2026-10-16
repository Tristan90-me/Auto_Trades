"""Signal auditor: log a Telegram signal channel and score it against real prices.

    python main.py channels          list your chats, to find the channel name
    python main.py backfill 300      import the channel's last 300 messages
    python main.py run               listen live, score every 15 min, send the daily report
    python main.py score             score pending signals now
    python main.py report [days]     print the report (add --send to also send it to Saved Messages)
"""
import asyncio
import sys
from datetime import datetime, timezone

import config
import db
import report
import scorer
import signal_parser


def ingest(conn, channel_id, message):
    text = message.message or ""
    posted = message.date.astimezone(timezone.utc)
    claims = signal_parser.parse_results(text)
    if claims:
        db.insert_claims(conn, channel_id=channel_id, msg_id=message.id, posted_utc=posted, claims=claims)
        return False
    sig = signal_parser.parse(text)
    if sig is None:
        return False
    entries, offset = signal_parser.entry_times_utc(sig, posted, config.SIGNAL_UTC_OFFSET)
    if not entries:
        print(f"[skip] couldn't place entry time {sig.entry_hhmm} for message {message.id}")
        return False
    market = scorer.classify(sig.pair, sig.otc_flag, entries[0])
    added = db.insert_signal(conn, channel_id=channel_id, msg_id=message.id, posted_utc=posted,
                             raw_text=text, sig=sig, entries=entries, offset=offset, market=market)
    if added:
        print(f"[signal] {sig.pair} {sig.direction} {entries[0]:%Y-%m-%d %H:%M} UTC "
              f"(channel clock UTC{offset:+d}) {len(entries) - 1} gale(s) -> {market}")
    return added


def make_client():
    from telethon import TelegramClient

    config.require_telegram()
    return TelegramClient(config.SESSION_PATH, int(config.TG_API_ID), config.TG_API_HASH)


async def resolve_channel(client):
    if not config.CHANNEL:
        raise SystemExit("Set CHANNEL in .env (run `python main.py channels` to find it).")
    target = config.CHANNEL
    try:
        return await client.get_entity(int(target) if target.lstrip("-").isdigit() else target)
    except (ValueError, TypeError):
        async for d in client.iter_dialogs():
            if d.name == target:
                return d.entity
    raise SystemExit(f"Couldn't find channel '{target}'. Run `python main.py channels` and copy its name or id.")


async def cmd_channels(client):
    async for d in client.iter_dialogs():
        if d.is_channel:
            print(f"{d.id}\t{d.name}")


async def cmd_backfill(client, limit):
    conn = db.connect()
    channel = await resolve_channel(client)
    added = 0
    async for msg in client.iter_messages(channel, limit=limit):
        added += ingest(conn, channel.id, msg)
    print(f"Imported {added} new signal(s). Scoring...")
    print(f"Scored {await asyncio.to_thread(scorer.score_pending, conn)}.")


async def send_report(client, conn):
    text = await asyncio.to_thread(report.build, conn)
    await client.send_message("me", text)  # your Saved Messages
    db.set_meta(conn, "last_report_date", datetime.now(timezone.utc).date().isoformat())
    print("[report] sent to Saved Messages")


async def periodic(client, conn):
    hh, mm = map(int, config.REPORT_TIME_UTC.split(":"))
    while True:
        try:
            n = await asyncio.to_thread(scorer.score_pending, conn)
            if n:
                print(f"[score] scored {n} signal(s)")
            now = datetime.now(timezone.utc)
            due = (now.hour, now.minute) >= (hh, mm)
            if due and db.get_meta(conn, "last_report_date") != now.date().isoformat():
                await send_report(client, conn)
        except Exception as e:
            print(f"[periodic] error: {e}")
        await asyncio.sleep(15 * 60)


async def cmd_run(client):
    from telethon import events

    conn = db.connect()
    channel = await resolve_channel(client)

    @client.on(events.NewMessage(chats=channel))
    async def on_message(event):
        ingest(conn, channel.id, event.message)

    print(f"Listening to '{getattr(channel, 'title', channel.id)}'. Ctrl+C to stop.")
    asyncio.create_task(periodic(client, conn))
    await client.run_until_disconnected()


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows console vs emoji
    args = sys.argv[1:]
    cmd = args[0] if args else "help"

    if cmd == "score":
        print(f"Scored {scorer.score_pending()}.")
        return
    if cmd == "report":
        days = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
        if "--send" not in args:
            print(report.build(days=days))
            return

    if cmd not in {"channels", "backfill", "run", "report"}:
        print(__doc__)
        return

    async def go():
        client = make_client()
        await client.start()  # first time: asks for your phone number and login code
        async with client:
            await dispatch(client)

    async def dispatch(client):
        if cmd == "channels":
            await cmd_channels(client)
        elif cmd == "backfill":
            await cmd_backfill(client, int(args[1]) if len(args) > 1 else 300)
        elif cmd == "run":
            await cmd_run(client)
        elif cmd == "report":
            await send_report(client, db.connect())

    try:
        asyncio.run(go())
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
