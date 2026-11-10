"""SQLite storage (WAL) for chats, memory, clipboard history, notes, reminders and the action log."""
from __future__ import annotations

import json
import logging
import pathlib
import sqlite3
import threading
import time
from typing import Any, Iterable

from . import config

log = logging.getLogger("idos.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS chats (id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', created REAL NOT NULL,
    updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL, role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '', meta TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS messages_chat ON messages(chat_id, id);
CREATE TABLE IF NOT EXISTS memory (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'agent', created REAL NOT NULL, updated REAL NOT NULL,
    hits INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS clipboard (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL DEFAULT 'text',
    text TEXT NOT NULL DEFAULT '', hash TEXT UNIQUE, app TEXT NOT NULL DEFAULT '', pinned INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0, pinned INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, due REAL NOT NULL,
    repeat TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT 'reminder', done INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS shelf (id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS colors (id INTEGER PRIMARY KEY AUTOINCREMENT, hex TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS launcher (key TEXT PRIMARY KEY, uses INTEGER NOT NULL DEFAULT 0,
    last REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS actions (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, tool TEXT, args TEXT,
    result TEXT, ok INTEGER, created REAL NOT NULL);
"""

FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(text, tags, content='memory', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memory BEGIN
    INSERT INTO memory_fts(rowid, text, tags) VALUES (new.id, new.text, new.tags); END;
CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memory BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, text, tags) VALUES ('delete', old.id, old.text, old.tags); END;
CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE OF text, tags ON memory BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, text, tags) VALUES ('delete', old.id, old.text, old.tags);
    INSERT INTO memory_fts(rowid, text, tags) VALUES (new.id, new.text, new.tags); END;
"""


class DB:
    def __init__(self, path: pathlib.Path | str | None = None) -> None:
        self.path = str(path) if path else str(config.DATA / "idos.db")
        if self.path != ":memory:":
            pathlib.Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=15)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            try:
                self.conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:
                pass
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.executescript(SCHEMA)
            try:
                self.conn.executescript(FTS)
                self.fts = True
            except sqlite3.OperationalError as exc:
                log.warning("FTS5 unavailable, memory search falls back to a scan: %s", exc)
                self.fts = False
            self.conn.commit()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        """Run a write statement: the new row id for INSERT/REPLACE, otherwise the number of affected rows."""
        with self.lock:
            cursor = self.conn.execute(sql, tuple(params))
            self.conn.commit()
            if sql.lstrip()[:7].upper().startswith(("INSERT", "REPLACE")):
                return int(cursor.lastrowid or 0)
            return int(cursor.rowcount)

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        with self.lock:
            self.conn.executemany(sql, [tuple(row) for row in rows])
            self.conn.commit()

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self.lock:
            return [dict(row) for row in self.conn.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        with self.lock:
            row = self.conn.execute(sql, tuple(params)).fetchone()
        return dict(row) if row is not None else None

    def kv_get(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM kv WHERE key = ?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            return row["value"]

    def kv_set(self, key: str, value: Any) -> None:
        self.execute("INSERT OR REPLACE INTO kv(key, value) VALUES (?, ?)", (key, json.dumps(value, ensure_ascii=False)))

    def log_action(self, chat_id: str, tool: str, args: Any, result: str, ok: bool) -> None:
        try:
            self.execute("INSERT INTO actions(chat_id, tool, args, result, ok, created) VALUES (?, ?, ?, ?, ?, ?)",
                         (chat_id, tool, json.dumps(args, ensure_ascii=False, default=str)[:4000], (result or "")[:4000],
                          int(bool(ok)), time.time()))
            row = self.one("SELECT MAX(id) AS id FROM actions")
            if row and row["id"] and row["id"] % 500 == 0:
                self.execute("DELETE FROM actions WHERE id <= ?", (row["id"] - 5000,))
        except sqlite3.Error as exc:
            log.warning("action log failed: %s", exc)

    def close(self) -> None:
        with self.lock:
            self.conn.close()


_db: DB | None = None


def db() -> DB:
    global _db
    if _db is None:
        _db = DB()
    return _db


def use(instance: DB) -> DB:
    global _db
    _db = instance
    return instance
