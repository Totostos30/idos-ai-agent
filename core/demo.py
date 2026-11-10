"""Демо-сцены для проверки интерфейса без Windows и ключей: музыка, текст песни, агент, карточки, таймер, запись."""
from __future__ import annotations

import asyncio
import math
import time
import uuid

from . import bus

COVER = ("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>"
         "<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'><stop offset='0' stop-color='%23ff7a59'/>"
         "<stop offset='1' stop-color='%23b4329c'/></linearGradient></defs><rect width='64' height='64' fill='url(%23g)'/>"
         "<circle cx='32' cy='32' r='14' fill='none' stroke='white' stroke-opacity='.7' stroke-width='3'/></svg>")

LYRICS = ["Город засыпает под неоном", "И над крышами тихо светает", "Мы ловим волну и всё звучит", "Как будто так было всегда"]


def media_state(playing: bool = True) -> dict:
    return {"active": True, "playing": playing, "track_id": "demo-1", "app": "Spotify", "title": "Неоновая ночь",
            "artist": "Эйдос Demo", "album": "Остров", "duration": 214.0, "cover": COVER, "accent": "#ff6a6a",
            "can_next": True, "can_prev": True, "can_seek": True}


async def music(seconds: float = 6.0) -> None:
    bus.emit("media.state", media_state(True))
    start = time.time()
    line = -1
    while time.time() - start < seconds:
        t = time.time() - start
        bands = [max(0.0, min(1.0, 0.45 + 0.4 * math.sin(t * 7 + i * 0.7) * math.cos(t * 3 + i))) for i in range(12)]
        bus.emit("media.levels", {"bands": bands})
        bus.emit("media.position", {"position": 40 + t})
        if int(t / 1.5) != line:
            line = int(t / 1.5)
            bus.emit("media.lyrics", {"line": LYRICS[line % len(LYRICS)], "index": line})
        await asyncio.sleep(0.05)


async def agent_run() -> None:
    run = uuid.uuid4().hex[:8]
    bus.emit("agent.state", {"state": "thinking", "label": "Думаю…", "run": run})
    await asyncio.sleep(0.8)
    for tool, action, label in (("web", "search", "Ищу погоду в Москве"), ("apps", "launch", "Открываю Блокнот")):
        sid = uuid.uuid4().hex[:8]
        bus.emit("agent.state", {"state": "acting", "label": label, "run": run})
        bus.emit("agent.step", {"id": sid, "run": run, "tool": tool, "action": action, "label": label, "status": "running"})
        await asyncio.sleep(0.9)
        bus.emit("agent.step", {"id": sid, "run": run, "tool": tool, "action": action, "label": label, "status": "ok", "ms": 900,
                                "preview": "готово"})
    bus.emit("agent.state", {"state": "answering", "label": "Отвечаю", "run": run})
    text = "В Москве сейчас +3°, облачно. Блокнот открыт — что записать?"
    for i in range(0, len(text), 6):
        bus.emit("agent.delta", {"run": run, "text": text[i:i + 6]})
        await asyncio.sleep(0.05)
    bus.emit("agent.message", {"run": run, "chat_id": "", "role": "assistant", "text": text, "ok": True, "meta": {"model": "demo"}})
    bus.emit("agent.state", {"state": "idle", "label": "", "run": run})


async def extras() -> None:
    bus.card("Напоминание", "Созвон с командой через 5 минут", "reminder",
             actions=[{"id": "snooze", "label": "Отложить"}, {"id": "done", "label": "Готово"}], timeout=8, icon="⏰")
    await asyncio.sleep(0.6)
    bus.emit("island.timer", {"id": "demo", "label": "Чай", "state": "running", "seconds": 180, "ends_at": time.time() + 180})
    await asyncio.sleep(0.6)
    bus.card("Скриншот сохранён", "Скопирован в буфер обмена", "ok", timeout=5, icon="📸")


async def play(app, scene: str = "all") -> None:
    scene = scene or "all"
    if scene in ("all", "music"):
        await music(6.0 if scene == "all" else 20.0)
    if scene in ("all", "agent"):
        await agent_run()
    if scene in ("all", "extras"):
        await extras()
    if scene == "all":
        await music(30.0)
