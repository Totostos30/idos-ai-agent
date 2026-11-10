"""Island cards and timers: the agent shows information and countdowns on the Dynamic Island."""
from __future__ import annotations

import asyncio
import itertools
import logging
import re
import time
from typing import Any

from .. import bus
from ..util import clip
from . import A, B, N, S, Tool, ToolError, get_bool, get_float, get_str, need

log = logging.getLogger("idos.island")
KINDS = {"info", "success", "warning", "error", "alarm"}
UNITS = (("сек", 1), ("с", 1), ("s", 1), ("мин", 60), ("м", 60), ("m", 60), ("час", 3600), ("ч", 3600), ("h", 3600))
WORDS = {"полчаса": 1800, "пол часа": 1800, "полминуты": 30, "час": 3600, "минуту": 60, "минута": 60,
         "полтора часа": 5400}
TIMERS: dict[str, dict] = {}
_ids = itertools.count(1)


def parse_duration(text: str) -> float | None:
    """'5 минут', '1 час 20 мин', '90 сек', '1:30', 'полчаса' → seconds."""
    t = " ".join(text.lower().replace(",", ".").split())
    if not t:
        return None
    if t in WORDS:
        return float(WORDS[t])
    clock_match = re.fullmatch(r"(\d+):(\d{1,2})(?::(\d{1,2}))?", t)
    if clock_match:
        a, b, c = (int(x) if x else 0 for x in clock_match.groups())
        return float(a * 3600 + b * 60 + c if clock_match.group(3) else a * 60 + b)
    total, found = 0.0, False
    for number, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([a-zа-я]+)", t):
        mult = next((m for prefix, m in UNITS if unit.startswith(prefix)), None)
        if mult is not None:
            total += float(number) * mult
            found = True
    return total if found else None


def clock(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    hours, rest = divmod(s, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


async def _finish(timer_id: str, app: Any) -> None:
    timer = TIMERS.get(timer_id)
    if timer is None:
        return
    try:
        await asyncio.sleep(max(0.0, timer["ends"] - time.time()))
    except asyncio.CancelledError:
        return
    TIMERS.pop(timer_id, None)
    bus.emit("island.timer", {"id": timer_id, "label": timer["label"], "state": "done"})
    bus.card("Таймер", f"{timer['label']} — время вышло", kind="alarm", sticky=True, icon="timer",
             actions=[{"id": "dismiss", "label": "Ок"}])
    say = getattr(getattr(app, "tts", None), "say", None)
    if say is not None:
        try:
            result = say(f"Таймер «{timer['label']}»: время вышло.")
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # noqa: BLE001 - speech is a bonus
            log.debug("timer speech failed", exc_info=True)


def start_timer(seconds: float, label: str, app: Any = None) -> dict:
    timer_id = f"timer-{next(_ids)}"
    timer = {"id": timer_id, "label": label, "seconds": seconds, "ends": time.time() + seconds}
    TIMERS[timer_id] = timer
    timer["task"] = asyncio.get_running_loop().create_task(_finish(timer_id, app))
    bus.emit("island.timer", {"id": timer_id, "label": label, "state": "running", "seconds": seconds,
                              "ends_at": int(timer["ends"] * 1000)})
    return timer


def cancel_timer(timer_id: str) -> bool:
    timer = TIMERS.pop(timer_id, None)
    if timer is None:
        return False
    timer["task"].cancel()
    bus.emit("island.timer", {"id": timer_id, "label": timer["label"], "state": "cancelled"})
    return True


NOTE_ACTIONS = ("remind", "reminders", "cancel_reminder", "note", "notes")
REPEATS = {"hourly": "каждый час", "daily": "каждый день", "weekly": "каждую неделю", "weekdays": "по будням"}


def _notes_action(action: str, args: dict, ctx: Any) -> str:
    app = getattr(ctx, "app", None)
    notes = app.feature("notes") if app is not None and hasattr(app, "feature") else None
    if notes is None:
        raise ToolError("заметки и напоминания сейчас недоступны")
    if action == "remind":
        try:
            item = notes.add_reminder(need(args, "text", "о чём напомнить"), get_str(args, "when"))
        except ValueError as exc:
            raise ToolError(str(exc)) from None
        repeat = REPEATS.get(item["repeat"], "")
        tail = f", {repeat}" if repeat else ""
        return f"напомню «{clip(item['text'], 80)}» {item['when']}{tail} (id {item['id']})"
    if action == "reminders":
        lines = []
        for item in notes.reminders():
            repeat = REPEATS.get(item["repeat"], "")
            tail = f" ({repeat})" if repeat else ""
            lines.append(f"{item['id']}: «{clip(item['text'], 80)}» — {item['when']}{tail}")
        return "\n".join(lines) or "напоминаний нет"
    if action == "cancel_reminder":
        key = get_str(args, "id").strip()
        items = notes.reminders()
        if key.lower() in ("all", "все", "всё"):
            for item in items:
                notes.cancel_reminder(item["id"])
            return f"отменил напоминаний: {len(items)}"
        target = next((i for i in items if key and str(i["id"]) == key), None)
        target = target or next((i for i in items if key and key.lower() in i["text"].lower()), None)
        if target is None and not key and len(items) == 1:
            target = items[0]
        if target is None:
            raise ToolError("не нашёл такое напоминание — посмотрите reminders" if items else "напоминаний нет")
        notes.cancel_reminder(target["id"])
        return f"напоминание «{clip(target['text'], 60)}» отменено"
    if action == "note":
        try:
            note = notes.add_note(need(args, "text", "текст заметки"))
        except ValueError as exc:
            raise ToolError(str(exc)) from None
        return f"заметка сохранена (id {note['id']})"
    items = notes.notes(include_done=False)
    return "\n".join(f"{n['id']}: {clip(n['text'], 120)}" for n in items) or "заметок нет"


async def handler(action: str, args: dict, ctx: Any) -> Any:
    if action == "card":
        title = need(args, "title", "заголовок")
        kind = get_str(args, "kind", "info").strip().lower()
        bus.card(title, get_str(args, "text"), kind=kind if kind in KINDS else "info",
                 timeout=get_float(args, "seconds", 8.0) or 8.0, icon=get_str(args, "icon"),
                 sticky=get_bool(args, "sticky"))
        return f"показал карточку «{clip(title, 60)}»"
    if action == "timer":
        seconds = get_float(args, "seconds", None)
        if seconds is None:
            text = need(args, "duration", "например «5 минут», «1:30» или seconds")
            seconds = parse_duration(text)
            if seconds is None:
                raise ToolError(f"не понял длительность «{text}»")
        if not 1 <= seconds <= 24 * 3600:
            raise ToolError("таймер: от 1 секунды до 24 часов")
        label = get_str(args, "label").strip() or clock(seconds)
        timer = start_timer(seconds, label, getattr(ctx, "app", None))
        return f"таймер «{label}» на {clock(seconds)} запущен ({timer['id']})"
    if action == "timers":
        now = time.time()
        return "\n".join(f"{t['id']}: «{t['label']}» — осталось {clock(t['ends'] - now)}"
                         for t in TIMERS.values()) or "таймеров нет"
    if action == "cancel_timer":
        key = get_str(args, "id").strip()
        if key.lower() in ("all", "все", "всё"):
            ids = list(TIMERS)
            for timer_id in ids:
                cancel_timer(timer_id)
            return f"отменил таймеров: {len(ids)}"
        if not key:
            if len(TIMERS) != 1:
                raise ToolError("укажите id таймера (timers) или all" if TIMERS else "таймеров нет")
            key = next(iter(TIMERS))
        if key not in TIMERS:
            key = next((t["id"] for t in TIMERS.values() if key.lower() in t["label"].lower()), key)
        if not cancel_timer(key):
            raise ToolError(f"нет таймера {key}")
        return "таймер отменён"
    if action in NOTE_ACTIONS:
        return _notes_action(action, args, ctx)
    if action == "clear":
        bus.emit("island.clear", {})
        return "убрал карточки с острова"
    raise ToolError(f"неизвестное действие {action}")


def register(reg: Any, app: Any) -> None:
    reg.add(Tool(
        "island",
        "Остров. card — показать карточку (title, text, kind info/success/warning/error, seconds, sticky); "
        "timer — таймер (duration «5 минут» или seconds, label), по окончании — карточка и голос; "
        "timers — список; cancel_timer (id, label или all); clear — убрать карточки; "
        "remind — напоминание на время (text — о чём, when — «через 20 минут», «завтра в 9», «в пятницу в 18:30», "
        "«каждый день в 8:00»), придёт карточкой и голосом; reminders — список; cancel_reminder (id, часть текста "
        "или all); note — заметка (text); notes — список заметок.",
        {"card": A("Показываю карточку «{title}»"), "timer": A("Ставлю таймер {duration}"),
         "timers": A("Смотрю таймеры", "read"), "cancel_timer": A("Отменяю таймер {id}"),
         "clear": A("Убираю карточки"), "remind": A("Ставлю напоминание {when}"),
         "reminders": A("Смотрю напоминания", "read"), "cancel_reminder": A("Отменяю напоминание {id}"),
         "note": A("Сохраняю заметку"), "notes": A("Смотрю заметки", "read")},
        {"title": S("card: заголовок"), "text": S("card: текст; remind: о чём напомнить; note: текст заметки"), "kind": S("info | success | warning | error"),
         "icon": S("card: имя иконки"), "sticky": B("card: не скрывать сама"),
         "seconds": N("card: сколько показывать; timer: длительность"),
         "duration": S("timer: «5 минут», «1 час 20 минут», «1:30»"),
         "label": S("timer: подпись, например «чай»"), "id": S("cancel_timer, cancel_reminder: id, часть подписи или all"),
         "when": S("remind: когда — «через 20 минут», «завтра в 9», «каждый день в 8:00»")},
        handler, timeout=30))
