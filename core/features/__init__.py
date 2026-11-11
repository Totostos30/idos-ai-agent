"""Island features in the core: clipboard history, notes and reminders, media with lyrics, weather, status
(battery, network, VPN), command bar, capture (screenshots, OCR, QR, colour), screen recorder, downloads, hooks for
coding agents and OBS.

Every feature is a small class: optional background work started in `start()`, UI methods from `methods()` and
`close()`. A feature that cannot load (missing package, wrong OS) is skipped with a logged reason, so the core
always starts.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
from typing import Any, Callable

log = logging.getLogger("idos.features")

MODULES = ["clipboard", "notes", "media", "weather", "status", "commandbar", "capture", "recorder", "downloads",
           "hooks", "obs"]


class Feature:
    name = "feature"

    def __init__(self, app: Any) -> None:
        self.app = app
        self.settings = app.settings
        self.db = getattr(app, "db", None)
        self._tasks: list[asyncio.Task] = []
        self._unsubscribe: list[Callable[[], None]] = []

    async def start(self) -> None:
        """Start background work; called once the event loop runs (not in demo mode)."""

    def methods(self) -> dict[str, Callable[..., Any]]:
        return {}

    def spawn(self, coro, label: str) -> None:
        self._tasks = [task for task in self._tasks if not task.done()]
        self._tasks.append(self.app.background(coro, f"{self.name}-{label}"))

    async def say(self, text: str) -> None:
        say = getattr(getattr(self.app, "tts", None), "say", None)
        if say is None:
            return
        try:
            result = say(text)
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # noqa: BLE001 - voice is optional
            log.debug("%s: speech failed", self.name, exc_info=True)

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        for off in self._unsubscribe:
            off()
        self._unsubscribe.clear()


def load_features(app: Any, modules: list[str] | None = None) -> list[Feature]:
    features: list[Feature] = []
    for name in MODULES if modules is None else modules:
        try:
            module = importlib.import_module(f"{__name__}.{name}")
            features.append(module.create(app))
        except Exception as exc:  # noqa: BLE001 - one broken feature must not stop the core
            log.warning("feature %s unavailable: %s", name, exc)
    return features
