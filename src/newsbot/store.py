"""既読管理（SQLite）。記事ごとに「ダイジェスト済み」「通知済み」を記録する。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id         TEXT PRIMARY KEY,
    feed_name  TEXT NOT NULL,
    feed_url   TEXT NOT NULL,
    category   TEXT NOT NULL DEFAULT '',
    title      TEXT NOT NULL,
    link       TEXT NOT NULL,
    summary    TEXT NOT NULL DEFAULT '',
    published  TEXT,              -- ISO 8601 (UTC)。不明なら NULL
    fetched_at TEXT NOT NULL,
    digested   INTEGER NOT NULL DEFAULT 0,
    alerted    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_items_digested ON items(digested);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def get_meta(con: sqlite3.Connection, key: str) -> str | None:
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, value))
    con.commit()


def feed_known(con: sqlite3.Connection, feed_url: str) -> bool:
    return con.execute("SELECT 1 FROM items WHERE feed_url=? LIMIT 1", (feed_url,)).fetchone() is not None


def insert_new(con: sqlite3.Connection, items: list[dict]) -> list[dict]:
    """未登録の記事だけを追加し、追加したものを返す。"""
    fetched = now_utc().isoformat()
    new = []
    for it in items:
        cur = con.execute(
            "INSERT OR IGNORE INTO items(id, feed_name, feed_url, category, title, link, summary, published, fetched_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (it["id"], it["feed_name"], it["feed_url"], it["category"], it["title"], it["link"],
             it["summary"], it["published"], fetched),
        )
        if cur.rowcount:
            new.append(it)
    con.commit()
    return new


def pending_digest(con: sqlite3.Connection, lookback_hours: float) -> list[sqlite3.Row]:
    """未ダイジェストの記事。古すぎるものは送らずに済みにする。"""
    cutoff = (now_utc() - timedelta(hours=lookback_hours)).isoformat()
    con.execute(
        "UPDATE items SET digested=1 WHERE digested=0 AND COALESCE(published, fetched_at) < ?", (cutoff,)
    )
    con.commit()
    return con.execute(
        "SELECT * FROM items WHERE digested=0 ORDER BY feed_name, COALESCE(published, fetched_at) DESC"
    ).fetchall()


def mark(con: sqlite3.Connection, column: str, ids: list[str]) -> None:
    assert column in ("digested", "alerted")
    con.executemany(f"UPDATE items SET {column}=1 WHERE id=?", [(i,) for i in ids])
    con.commit()


def prune(con: sqlite3.Connection, keep_days: int = 60) -> None:
    cutoff = (now_utc() - timedelta(days=keep_days)).isoformat()
    con.execute("DELETE FROM items WHERE fetched_at < ?", (cutoff,))
    con.commit()
