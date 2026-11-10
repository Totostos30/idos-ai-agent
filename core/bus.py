"""In-process event bus. Core modules emit events; the WebSocket server forwards them to the UI."""
from __future__ import annotations

import asyncio
import itertools
import logging
import threading
from typing import Any, Callable

log = logging.getLogger("idos.bus")
Sink = Callable[[str, Any, "bytes | None"], None]

_sinks: list[Sink] = []
_handlers: dict[str, list[Callable[[Any], None]]] = {}
_loop: asyncio.AbstractEventLoop | None = None
_lock = threading.Lock()
_ids = itertools.count(1)


def attach(loop: asyncio.AbstractEventLoop | None) -> None:
    """Events emitted from other threads are marshalled onto this loop."""
    global _loop
    _loop = loop


def reset() -> None:
    global _loop
    with _lock:
        _sinks.clear()
        _handlers.clear()
    _loop = None


def subscribe(sink: Sink) -> Callable[[], None]:
    with _lock:
        _sinks.append(sink)

    def unsubscribe() -> None:
        with _lock:
            if sink in _sinks:
                _sinks.remove(sink)
    return unsubscribe


def on(event: str, fn: Callable[[Any], None]) -> Callable[[], None]:
    with _lock:
        _handlers.setdefault(event, []).append(fn)

    def unsubscribe() -> None:
        with _lock:
            if fn in _handlers.get(event, []):
                _handlers[event].remove(fn)
    return unsubscribe


def _on_loop(loop: asyncio.AbstractEventLoop) -> bool:
    try:
        return asyncio.get_running_loop() is loop
    except RuntimeError:
        return False


def emit(event: str, data: Any = None, binary: bytes | None = None) -> None:
    loop = _loop
    if loop is None or loop.is_closed() or _on_loop(loop):
        _dispatch(event, data, binary)
        return
    try:
        loop.call_soon_threadsafe(_dispatch, event, data, binary)
    except RuntimeError:
        _dispatch(event, data, binary)


def _dispatch(event: str, data: Any, binary: bytes | None) -> None:
    with _lock:
        sinks = list(_sinks)
        handlers = list(_handlers.get(event, ()))
    for sink in sinks:
        try:
            sink(event, data, binary)
        except Exception:  # noqa: BLE001
            log.exception("event sink failed for %s", event)
    for fn in handlers:
        try:
            fn(data)
        except Exception:  # noqa: BLE001
            log.exception("event handler failed for %s", event)


def card(title: str, text: str = "", kind: str = "info", actions: list[dict] | None = None, timeout: float = 6.0,
         icon: str = "", sticky: bool = False, card_id: str | None = None) -> str:
    """Show a notification card on the island. ``actions``: [{"id": "...", "label": "..."}]."""
    card_id = card_id or f"card-{next(_ids)}"
    emit("island.card", {"id": card_id, "title": title, "text": text, "kind": kind, "actions": actions or [],
                         "timeout": 0 if sticky else timeout, "icon": icon})
    return card_id


def unsubscribe(sink: Sink) -> None:
    with _lock:
        if sink in _sinks:
            _sinks.remove(sink)


# Older modules use ``from .bus import bus`` and call bus.emit / bus.subscribe / bus.unsubscribe / bus.card.
import sys as _sys  # noqa: E402

bus = _sys.modules[__name__]
