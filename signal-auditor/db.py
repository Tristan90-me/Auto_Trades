"""SQLite journal of every signal and how it scored."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY,
    channel_id INTEGER,
    msg_id INTEGER,
    posted_utc TEXT,
    raw_text TEXT,
    pair TEXT,
    direction TEXT,
    expiry_min INTEGER,
    entries_json TEXT,      -- [entry, gale1, gale2] as UTC ISO strings
    utc_offset INTEGER,     -- channel clock offset that was detected/used
    market TEXT,            -- real | otc | unverifiable
    parsed_by TEXT,
    status TEXT,            -- pending | scored | unverifiable | no_data
    outcome TEXT,           -- win0 | win1 | win2 | loss | draw0 | ...
    steps_json TEXT,        -- per-step strike/exit/result
    pnl_gale REAL,          -- simulated P&L following the gales
    pnl_nogale REAL,        -- simulated P&L taking only the first entry
    scored_at TEXT,
    UNIQUE(channel_id, msg_id)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def connect():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def insert_signal(conn, *, channel_id, msg_id, posted_utc, raw_text, sig, entries, offset, market):
    status = "pending" if market == "real" else "unverifiable"
    cur = conn.execute(
        """INSERT OR IGNORE INTO signals
           (channel_id, msg_id, posted_utc, raw_text, pair, direction, expiry_min,
            entries_json, utc_offset, market, parsed_by, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (channel_id, msg_id, posted_utc.isoformat(), raw_text, sig.pair, sig.direction,
         sig.expiry_min, json.dumps([t.isoformat() for t in entries]), offset, market,
         sig.parsed_by, status),
    )
    conn.commit()
    return cur.rowcount == 1


def pending(conn):
    return conn.execute("SELECT * FROM signals WHERE status='pending' ORDER BY posted_utc").fetchall()


def save_score(conn, row_id, *, status, outcome=None, steps=None, pnl_gale=None, pnl_nogale=None):
    conn.execute(
        """UPDATE signals SET status=?, outcome=?, steps_json=?, pnl_gale=?, pnl_nogale=?, scored_at=?
           WHERE id=?""",
        (status, outcome, json.dumps(steps) if steps is not None else None, pnl_gale, pnl_nogale,
         datetime.now(timezone.utc).isoformat(), row_id),
    )
    conn.commit()


def since(conn, days):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return conn.execute("SELECT * FROM signals WHERE posted_utc >= ? ORDER BY posted_utc", (cutoff,)).fetchall()


def all_signals(conn):
    return conn.execute("SELECT * FROM signals ORDER BY posted_utc").fetchall()


def get_meta(conn, key):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
