"""Clipboard history: watches the Windows clipboard, skips password managers and excluded apps, keeps pinned items."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time

from .. import bus, config
from . import Feature

log = logging.getLogger("idos.clipboard")
MAX_TEXT = 100_000
PREVIEW = 400


def excluded() -> bool:
    """Password managers mark secrets with the ExcludeClipboardContentFromMonitorProcessing format."""
    import ctypes

    user32 = ctypes.windll.user32
    fmt = user32.RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing")
    return bool(fmt and user32.IsClipboardFormatAvailable(fmt))


def public(row: dict) -> dict:
    text = row.get("text", "")
    return {"id": row["id"], "kind": row.get("kind", "text"), "preview": text[:PREVIEW], "length": len(text),
            "lines": text.count("\n") + 1, "app": row.get("app", ""), "pinned": bool(row.get("pinned")),
            "created": row.get("created", 0)}


class Clipboard(Feature):
    name = "clipboard"

    def __init__(self, app) -> None:
        super().__init__(app)
        self._seq = 0
        self._own = ""  # text we put on the clipboard ourselves

    # ------------------------------------------------------------------ storage
    def add(self, text: str, source_app: str = "") -> dict | None:
        text = (text or "")[:MAX_TEXT]
        if not text.strip():
            return None
        digest = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()
        now = time.time()
        row = self.db.one("SELECT id FROM clipboard WHERE hash = ?", (digest,))
        if row:
            self.db.execute("UPDATE clipboard SET created = ?, app = ? WHERE id = ?", (now, source_app, row["id"]))
            item_id = row["id"]
        else:
            item_id = self.db.execute("INSERT INTO clipboard(kind, text, hash, app, created) VALUES ('text', ?, ?, ?, ?)",
                                      (text, digest, source_app, now))
        self.trim()
        item = self.get(item_id, full=False)
        if item:
            bus.emit("clipboard.added", item)
        return item

    def trim(self) -> None:
        limit = max(10, int(self.settings.get("clipboard.max_items", 400) or 400))
        self.db.execute("DELETE FROM clipboard WHERE pinned = 0 AND id NOT IN "
                        "(SELECT id FROM clipboard WHERE pinned = 0 ORDER BY created DESC LIMIT ?)", (limit,))

    def get(self, item_id: int, full: bool = True) -> dict | None:
        row = self.db.one("SELECT * FROM clipboard WHERE id = ?", (int(item_id),))
        if row is None:
            return None
        item = public(row)
        if full:
            item["text"] = row["text"]
        return item

    def list(self, query: str = "", limit: int = 50, offset: int = 0) -> list[dict]:
        q = (query or "").strip().lower()
        if not q:
            rows = self.db.query("SELECT * FROM clipboard ORDER BY pinned DESC, created DESC LIMIT ? OFFSET ?",
                                 (int(limit), int(offset)))
            return [public(r) for r in rows]
        rows = [r for r in self.db.query("SELECT * FROM clipboard ORDER BY pinned DESC, created DESC")
                if q in r["text"].lower()]  # Python lower(): SQLite LIKE ignores case only for ASCII
        return [public(r) for r in rows[int(offset):int(offset) + int(limit)]]

    def pin(self, item_id: int, pinned: bool = True) -> bool:
        return self.db.execute("UPDATE clipboard SET pinned = ? WHERE id = ?", (int(bool(pinned)), int(item_id))) > 0

    def delete(self, item_id: int) -> bool:
        return self.db.execute("DELETE FROM clipboard WHERE id = ?", (int(item_id),)) > 0

    def clear(self, keep_pinned: bool = True) -> int:
        return self.db.execute("DELETE FROM clipboard" + (" WHERE pinned = 0" if keep_pinned else ""))

    # ------------------------------------------------------------------ Windows
    async def start(self) -> None:
        if not config.IS_WINDOWS:
            return
        from .. import win

        self._seq = win.clipboard_sequence()
        self.spawn(self._watch(), "watch")

    async def _watch(self) -> None:
        from .. import win

        while True:
            await asyncio.sleep(0.5)
            try:
                seq = win.clipboard_sequence()
                if seq != self._seq:
                    self._seq = seq
                    await self.capture()
            except Exception:  # noqa: BLE001
                log.debug("clipboard watch failed", exc_info=True)

    async def capture(self) -> dict | None:
        from .. import win

        if not self.settings.get("clipboard.enabled", True) or await asyncio.to_thread(excluded):
            return None
        window = await asyncio.to_thread(win.foreground)
        process = str(window.get("process", ""))
        skip = {str(s).lower() for s in self.settings.get("clipboard.skip_apps", []) or []}
        if process.lower() in skip:
            return None
        text = await asyncio.to_thread(win.get_clipboard)
        own, self._own = self._own, ""
        if not text or text == own:
            return None
        return self.add(text, process)

    async def paste(self, item_id: int) -> dict:
        item = self.get(item_id)
        if item is None:
            raise ValueError("такой записи в истории нет")
        if not config.IS_WINDOWS:
            return {"ok": False, "reason": "вставка есть только в Windows"}
        from .. import win

        self._own = item["text"]
        await asyncio.to_thread(win.set_clipboard, item["text"])
        pasted = bool(self.settings.get("clipboard.paste_on_click", True))
        if pasted:
            await asyncio.sleep(0.05)
            await asyncio.to_thread(win.hotkey, "ctrl+v")
        self.db.execute("UPDATE clipboard SET created = ? WHERE id = ?", (time.time(), item["id"]))
        return {"ok": True, "pasted": pasted}

    async def paste_plain(self) -> dict:
        """Re-put the clipboard as plain text and paste it (drops formatting)."""
        if not config.IS_WINDOWS:
            return {"ok": False, "reason": "вставка есть только в Windows"}
        from .. import win

        text = await asyncio.to_thread(win.get_clipboard)
        if not text:
            return {"ok": False, "reason": "в буфере нет текста"}
        self._own = text
        await asyncio.to_thread(win.set_clipboard, text)
        await asyncio.to_thread(win.hotkey, "ctrl+v")
        return {"ok": True}

    def methods(self) -> dict:
        async def list_items(query: str = "", limit: int = 50, offset: int = 0) -> list:
            return self.list(query, limit, offset)

        async def get_item(item_id: int) -> dict | None:
            return self.get(item_id)

        async def paste(item_id: int) -> dict:
            return await self.paste(item_id)

        async def paste_plain() -> dict:
            return await self.paste_plain()

        async def pin(item_id: int, pinned: bool = True) -> dict:
            return {"ok": self.pin(item_id, pinned)}

        async def delete(item_id: int) -> dict:
            return {"ok": self.delete(item_id)}

        async def clear(keep_pinned: bool = True) -> dict:
            return {"deleted": self.clear(keep_pinned)}

        return {"clipboard.list": list_items, "clipboard.get": get_item, "clipboard.paste": paste,
                "clipboard.paste_plain": paste_plain, "clipboard.pin": pin, "clipboard.delete": delete,
                "clipboard.clear": clear}


def create(app) -> Clipboard:
    return Clipboard(app)
