"""Downloads: watches the Downloads folder and shows a card on the island when a file has finished downloading
(temporary .crdownload/.part/.tmp files are ignored until they are renamed). Actions: open, show in folder."""
from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import subprocess
import time

from .. import bus, config
from ..util import human_bytes
from . import Feature

log = logging.getLogger("idos.downloads")
PARTIAL = (".crdownload", ".part", ".partial", ".tmp", ".download", ".opdownload", ".!qb", ".aria2")
POLL = 2.0


def snapshot(folder: pathlib.Path) -> dict[str, tuple[int, float]]:
    out: dict[str, tuple[int, float]] = {}
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                try:
                    if entry.is_file():
                        st = entry.stat()
                        out[entry.name] = (st.st_size, st.st_mtime)
                except OSError:
                    continue
    except OSError:
        pass
    return out


def finished(before: dict, after: dict, pending: dict | None = None) -> list[str]:
    """Names of files that appeared since `before`, are not partial and kept their size between two polls.
    `pending` (name → size) carries files whose size is still changing."""
    pending = pending if pending is not None else {}
    done = []
    for name, (size, _mtime) in after.items():
        if name.lower().endswith(PARTIAL) or name.startswith("~$") or name.lower() == "desktop.ini":
            continue
        if name in before and name not in pending:
            continue
        if size > 0 and pending.get(name) == size:
            pending.pop(name, None)
            done.append(name)
        else:
            pending[name] = size
    for name in list(pending):
        if name not in after:
            pending.pop(name, None)
    return done


class Downloads(Feature):
    name = "downloads"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.items: list[dict] = []

    def folder(self) -> pathlib.Path:
        custom = (self.settings.get("integrations.downloads_folder", "") or "").strip()
        if custom:
            return pathlib.Path(custom).expanduser()
        if config.IS_WINDOWS:
            from .. import win

            return win.known_folder("downloads")
        return pathlib.Path.home() / "Downloads"

    def announce(self, path: pathlib.Path) -> dict:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        item = {"name": path.name, "path": str(path), "size": size, "size_text": human_bytes(size), "time": time.time()}
        self.items = ([item] + [i for i in self.items if i["path"] != item["path"]])[:30]
        bus.emit("downloads.done", item)
        bus.card("Загрузка завершена", f"{path.name} · {item['size_text']}", kind="success", icon="download",
                 actions=[{"id": "open", "label": "Открыть"}, {"id": "folder", "label": "В папке"}],
                 card_id=f"dl:{path}", timeout=8)
        return item

    async def _loop(self) -> None:
        folder = self.folder()
        before = await asyncio.to_thread(snapshot, folder)
        pending: dict = {}
        while True:
            await asyncio.sleep(POLL)
            if not self.settings.get("integrations.downloads", True):
                continue
            after = await asyncio.to_thread(snapshot, folder)
            for name in finished(before, after, pending):
                self.announce(folder / name)
            before = {k: v for k, v in after.items() if k not in pending}

    def on_action(self, data: dict) -> None:
        card, action = str((data or {}).get("card_id", "")), (data or {}).get("action")
        if not card.startswith("dl:") or not config.IS_WINDOWS:
            return
        path = card[3:]
        if action == "open":
            os.startfile(path)  # noqa: S606
        elif action == "folder":
            subprocess.Popen(["explorer", "/select,", path])  # noqa: S603,S607

    async def start(self) -> None:
        self._unsubscribe.append(bus.on("island.action", self.on_action))
        self.spawn(self._loop(), "watch")

    def methods(self) -> dict:
        async def recent() -> list:
            return self.items

        return {"downloads.recent": recent}


def create(app) -> Downloads:
    return Downloads(app)
