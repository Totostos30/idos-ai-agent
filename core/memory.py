"""Long-term memory about the user (SQLite FTS5 with a Python fallback)."""
from __future__ import annotations

import re
import time
from typing import Any

from . import db as dbmod
from .util import clip

WORD = re.compile(r"[0-9A-Za-zА-Яа-яЁё_]+")
STOP = set("""и в во не что он на я с со как а то все всё она так его но да ты к у же вы за бы по только ее её мне
было вот от меня еще ещё нет о из ему теперь когда даже ну ли если уже или ни быть был него до вас
нибудь уж вам ведь там потом себя ничего ей может они тут где есть надо ней для мы тебя их чем была
сам чтоб без чего раз тоже себе под будет ж тогда кто этот того потому этого какой ним здесь этом
один мой тем чтобы нее сейчас были куда зачем всех можно при об хоть после над больше тот через
эти нас про всего них какая много эту моя свою этой перед том нельзя такой им более всегда всю между
моё мои твой какие какое скажи расскажи пожалуйста помнишь знаешь это там the a an to of and or is are in
on for it me my you your please what who""".split())


def terms(text: str) -> list[str]:
    out: list[str] = []
    for word in WORD.findall((text or "").lower()):
        if len(word) > 1 and word not in STOP and word not in out:
            out.append(word)
    return out[:12]


def stem(word: str) -> str:
    """Very small Russian-friendly stemmer: cut the ending, keep at least 4 letters."""
    return word[:max(4, len(word) - 2)] if len(word) > 5 else word


def fts_query(text: str) -> str:
    return " OR ".join(f'"{stem(w)}"*' for w in terms(text))


class Memory:
    def __init__(self, database: dbmod.DB | None = None) -> None:
        self._db = database

    @property
    def db(self) -> dbmod.DB:
        return self._db or dbmod.db()

    def save(self, text: str, tags: str = "", source: str = "agent") -> dict[str, Any]:
        text = " ".join((text or "").split())
        if not text:
            raise ValueError("пустой текст для памяти")
        now = time.time()
        row = self.db.one("SELECT id, tags FROM memory WHERE text=?", (text,))
        if row:
            self.db.execute("UPDATE memory SET updated=?, tags=? WHERE id=?", (now, tags or row["tags"], row["id"]))
            return {"id": row["id"], "text": text, "duplicate": True}
        mid = self.db.execute("INSERT INTO memory(text, tags, source, created, updated) VALUES(?,?,?,?,?)",
                              (text, tags, source, now, now))
        return {"id": mid, "text": text}

    def update(self, mid: int, text: str | None = None, tags: str | None = None) -> bool:
        row = self.db.one("SELECT text, tags FROM memory WHERE id=?", (int(mid),))
        if not row:
            return False
        self.db.execute("UPDATE memory SET text=?, tags=?, updated=? WHERE id=?",
                        (" ".join((text or row["text"]).split()), row["tags"] if tags is None else tags, time.time(),
                         int(mid)))
        return True

    def delete(self, mid: int) -> bool:
        return self.db.execute("DELETE FROM memory WHERE id=?", (int(mid),)) > 0

    def list(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        return self.db.query("SELECT id, text, tags, source, created, updated FROM memory ORDER BY updated DESC "
                             "LIMIT ? OFFSET ?", (int(limit), int(offset)))

    def count(self) -> int:
        row = self.db.one("SELECT COUNT(*) AS n FROM memory")
        return int(row["n"]) if row else 0

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        if not terms(query):
            return []
        if getattr(self.db, "fts", False):
            try:
                return self.db.query(
                    "SELECT m.id, m.text, m.tags, m.created, m.updated FROM memory_fts "
                    "JOIN memory m ON m.id = memory_fts.rowid WHERE memory_fts MATCH ? "
                    "ORDER BY bm25(memory_fts) LIMIT ?", (fts_query(query), int(limit)))
            except Exception:  # noqa: BLE001 - malformed FTS query, fall back to scanning
                pass
        return self._scan(query, limit)

    def _scan(self, query: str, limit: int) -> list[dict[str, Any]]:
        stems = [stem(w) for w in terms(query)]
        scored = []
        for row in self.db.query("SELECT id, text, tags, created, updated FROM memory ORDER BY updated DESC LIMIT 5000"):
            hay = (row["text"] + " " + (row["tags"] or "")).lower()
            score = sum(1 for s in stems if s in hay)
            if score:
                scored.append((score, row["updated"] or 0, row))
        scored.sort(key=lambda item: (-item[0], -item[1]))
        return [row for _, _, row in scored[:limit]]

    def profile(self, limit: int = 6) -> list[str]:
        rows = self.db.query("SELECT text FROM memory WHERE tags LIKE '%profile%' ORDER BY updated DESC LIMIT ?",
                             (int(limit),))
        return [clip(row["text"], 160) for row in rows]

    def context(self, query: str, limit: int = 3) -> list[str]:
        """Short facts for the per-turn context block: profile facts plus the best matches."""
        items = self.profile()
        for row in self.search(query, limit):
            text = clip(row["text"], 160)
            if text not in items:
                items.append(text)
        return items[:8]
