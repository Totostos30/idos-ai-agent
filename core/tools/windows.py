"""Windows of any app: list, focus, minimize/maximize/restore, close, move or snap to part of a screen;
UI Automation snapshot of a window or an open menu with numbered elements, search by name, reading all text,
and click/type/toggle/select/expand by ref.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .. import win
from ..util import clip
from ..win import uia
from . import A, B, I, S, Tool, ToolError, get_bool, get_int, get_str, need

SNAPS = {"left": (0, 0, .5, 1), "right": (.5, 0, .5, 1), "top": (0, 0, 1, .5), "bottom": (0, .5, 1, .5),
         "full": (0, 0, 1, 1), "center": (.15, .1, .7, .8), "top_left": (0, 0, .5, .5), "top_right": (.5, 0, .5, .5),
         "bottom_left": (0, .5, .5, .5), "bottom_right": (.5, .5, .5, .5)}
PLACE_ALIASES = {"слева": "left", "влево": "left", "справа": "right", "вправо": "right", "сверху": "top",
                 "вверху": "top", "снизу": "bottom", "внизу": "bottom", "весь экран": "full",
                 "во весь экран": "full", "центр": "center", "по центру": "center"}
ELEMENT = {"click": "click", "right_click": "right_click", "double_click": "double_click", "type": "type",
           "toggle": "toggle", "select": "select", "expand": "expand", "collapse": "collapse",
           "focus_element": "focus", "get": "get", "scroll_to": "scroll"}
STATES = {"minimize": "свернул", "maximize": "развернул", "restore": "восстановил"}


def rect_xywh(info: dict) -> tuple[int, int, int, int]:
    r = info.get("rect") or {}
    if "width" in r:
        return int(r.get("x", 0)), int(r.get("y", 0)), int(r["width"]), int(r["height"])
    left, top = int(r.get("left", 0)), int(r.get("top", 0))
    return left, top, int(r.get("right", left)) - left, int(r.get("bottom", top)) - top


def describe_window(info: dict, active: int = 0) -> str:
    flags = [text for key, text in (("minimized", "свёрнуто"), ("maximized", "развёрнуто")) if info.get(key)]
    if active and info.get("hwnd") == active:
        flags.append("активно")
    return (f"{info.get('hwnd')}: «{clip(info.get('title', ''), 70)}» — {info.get('process', '')}"
            + (f" ({', '.join(flags)})" if flags else ""))


def monitor_for(info: dict, monitors: list[dict]) -> dict:
    x, y, w, h = rect_xywh(info)
    cx, cy = x + w // 2, y + h // 2
    for m in monitors:
        if m["x"] <= cx < m["x"] + m["width"] and m["y"] <= cy < m["y"] + m["height"]:
            return m
    return next((m for m in monitors if m.get("primary")), monitors[0])


async def target_window(args: dict, required: bool = False) -> dict:
    query = get_str(args, "window").strip()
    if query:
        return await asyncio.to_thread(win.find_window, query)
    if required:
        raise ToolError("укажите window — номер из list, название окна или имя процесса")
    info = await asyncio.to_thread(win.foreground)
    if not info:
        raise ToolError("нет активного окна")
    return info


def _surface(args: dict) -> str:
    return "menu" if get_str(args, "surface").strip().lower() in ("menu", "меню") else "window"


async def move(args: dict) -> str:
    info = await target_window(args)
    place = get_str(args, "place").strip().lower()
    place = PLACE_ALIASES.get(place, place)
    index = get_int(args, "monitor", None)
    x, y, w, h = rect_xywh(info)
    if place or index:
        monitors = await asyncio.to_thread(win.monitors)
        if index:
            if not 1 <= index <= len(monitors):
                raise ToolError(f"нет монитора {index}; всего {len(monitors)}")
            screen = monitors[index - 1]
        else:
            screen = monitor_for(info, monitors)
        if place and place not in SNAPS:
            raise ToolError("place: left, right, top, bottom, full, center, top_left, top_right, bottom_left, bottom_right")
        fx, fy, fw, fh = SNAPS[place or "center"]
        area = screen["work"]
        x, y = area["x"] + int(area["width"] * fx), area["y"] + int(area["height"] * fy)
        w, h = int(area["width"] * fw), int(area["height"] * fh)
    else:
        values = [get_int(args, key, None) for key in ("x", "y", "width", "height")]
        if all(v is None for v in values):
            raise ToolError("укажите place или x, y, width, height")
        x, y, w, h = [cur if v is None else v for v, cur in zip(values, (x, y, w, h))]
    await asyncio.to_thread(win.move_window, info["hwnd"], x, y, w, h)
    return f"окно «{clip(info.get('title', ''), 50)}»: ({x}, {y}), {w}×{h}"


async def run(action: str, args: dict) -> Any:  # noqa: C901 - one branch per action
    if action == "list":
        windows = await asyncio.to_thread(win.list_windows)
        active = (await asyncio.to_thread(win.foreground) or {}).get("hwnd", 0)
        return "\n".join(describe_window(w, active) for w in windows[:60]) or "открытых окон нет"
    if action == "active":
        info = await target_window({})
        x, y, w, h = rect_xywh(info)
        return f"{describe_window(info)}; позиция ({x}, {y}), размер {w}×{h}"
    if action in ("snapshot", "find"):
        info = await target_window(args)
        query = need(args, "name", "текст на элементе") if action == "find" else ""
        limit = 25 if query else max(10, min(150, get_int(args, "limit", 80) or 80))
        text = await uia.call(uia.snapshot, info["hwnd"], _surface(args), query, limit)
        return f"{describe_window(info)}\n{text}"
    if action == "read":
        info = await target_window(args)
        return f"{describe_window(info)}\n{await uia.call(uia.read_text, info['hwnd'])}"
    if action == "focused":
        return await uia.call(uia.focused, timeout=10)
    if action == "point":
        x, y = get_int(args, "x", None), get_int(args, "y", None)
        if x is None or y is None:
            raise ToolError("нужны x и y")
        return await uia.call(uia.at_point, x, y, timeout=10)
    if action in ELEMENT:
        ref = get_str(args, "ref").strip().strip("[]")
        if not ref.isdigit():
            raise ToolError("нужен ref — номер элемента из snapshot или find")
        result = await uia.call(uia.act, int(ref), ELEMENT[action], get_str(args, "text"), get_bool(args, "append"),
                                timeout=15)
        await asyncio.sleep(0.25)
        return result
    if action == "focus":
        info = await target_window(args, required=True)
        ok = await asyncio.to_thread(win.focus, info["hwnd"])
        return ("переключился на " if ok else "Windows не дал вывести окно вперёд: ") + describe_window(info)
    if action in STATES:
        info = await target_window(args)
        await asyncio.to_thread(win.show, info["hwnd"], action)
        return f"{STATES[action]} {describe_window(info)}"
    if action == "close":
        info = await target_window(args, required=True)
        await asyncio.to_thread(win.close, info["hwnd"])
        await asyncio.sleep(0.6)
        if await asyncio.to_thread(win.is_window, info["hwnd"]):
            return f"окно ещё открыто — возможно, спрашивает о сохранении: {describe_window(info)}"
        return f"закрыл {describe_window(info)}"
    if action == "move":
        return await move(args)
    raise ToolError(f"неизвестное действие {action}")


async def handler(action: str, args: dict, ctx: Any) -> Any:
    try:
        return await run(action, args)
    except (win.WinError, uia.UiaError) as exc:
        raise ToolError(str(exc)) from exc


def register(reg: Any, app: Any) -> None:
    reg.add(Tool(
        "windows",
        "Окна и элементы любых программ. list — окна; snapshot — элементы окна с номерами [ref] "
        "(surface=menu — открытое меню); find — элементы по названию; read — весь текст окна; "
        "click/right_click/double_click/type/toggle/select/expand/collapse/get — действие с элементом по ref; "
        "focus, minimize, maximize, restore, close, move (place: left/right/top/bottom/full/center или x, y, "
        "width, height; monitor). window — номер, название окна или процесс; без него — активное окно.",
        {"list": A("Смотрю список окон", "read"), "active": A("Смотрю активное окно", "read"),
         "snapshot": A("Смотрю элементы окна {window}", "read"), "find": A("Ищу «{name}» в окне {window}", "read"),
         "read": A("Читаю окно {window}", "read"), "focused": A("Смотрю, где фокус", "read"),
         "point": A("Смотрю, что в точке ({x}, {y})", "read"), "click": A("Нажимаю элемент {ref}"),
         "right_click": A("Правый клик по элементу {ref}"), "double_click": A("Двойной клик по элементу {ref}"),
         "type": A("Ввожу «{text}»"), "toggle": A("Переключаю элемент {ref}"), "select": A("Выбираю элемент {ref}"),
         "expand": A("Раскрываю элемент {ref}"), "collapse": A("Сворачиваю элемент {ref}"),
         "focus_element": A("Ставлю фокус на элемент {ref}"), "get": A("Читаю элемент {ref}", "read"),
         "scroll_to": A("Прокручиваю к элементу {ref}", "read"), "focus": A("Переключаюсь на окно {window}"),
         "minimize": A("Сворачиваю окно {window}"), "maximize": A("Разворачиваю окно {window}"),
         "restore": A("Восстанавливаю окно {window}"), "close": A("Закрываю окно {window}"),
         "move": A("Перемещаю окно {window}")},
        {"window": S("номер из list, название окна или процесс"), "ref": S("номер элемента из snapshot/find"),
         "name": S("find: текст на элементе"), "text": S("type: текст"), "append": B("type: дописать, а не заменить"),
         "surface": S("window | menu"), "place": S("move: left, right, top, bottom, full, center, top_left…"),
         "monitor": I("move: номер монитора"), "x": I("x"), "y": I("y"), "width": I("ширина"),
         "height": I("высота"), "limit": I("snapshot: сколько элементов")},
        handler, available=lambda: win.IS_WINDOWS, timeout=60))
