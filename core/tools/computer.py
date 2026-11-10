"""Mouse, keyboard and screen of the whole Windows desktop: screenshots described by the vision model with screen
coordinates, OCR to read or click any visible text, click/move/drag/scroll, typing and hotkeys.
Slamming the cursor into the top-left corner stops everything (failsafe).
"""
from __future__ import annotations

import asyncio
import io
from typing import Any

from .. import vision, win
from ..util import clip
from ..win import ocr, uia
from . import A, B, I, N, Result, S, Tool, ToolError, get_bool, get_float, get_int, get_str, need
from .windows import monitor_for, rect_xywh

BUTTONS = {"left": "left", "right": "right", "middle": "middle", "левая": "left", "правая": "right",
           "средняя": "middle"}


def grab(box: tuple[int, int, int, int]) -> bytes:
    from PIL import ImageGrab
    image = ImageGrab.grab(bbox=box, all_screens=True)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _primary(monitors: list[dict]) -> dict:
    return next((m for m in monitors if m.get("primary")), monitors[0])


async def area(args: dict) -> tuple[tuple[int, int, int, int], str]:
    """Capture box (left, top, right, bottom) in screen pixels and its label."""
    kind = get_str(args, "area").strip().lower()
    window = get_str(args, "window").strip()
    if kind in ("window", "окно") or window:
        info = await asyncio.to_thread(win.find_window, window) if window else await asyncio.to_thread(win.foreground)
        if not info:
            raise ToolError("нет активного окна")
        x, y, w, h = rect_xywh(info)
        return (x, y, x + w, y + h), f"окно «{clip(info.get('title', ''), 40)}»"
    if kind in ("all", "все", "всё"):
        vs = await asyncio.to_thread(win.virtual_screen)
        return (vs["x"], vs["y"], vs["x"] + vs["width"], vs["y"] + vs["height"]), "все мониторы"
    parts = [p for p in kind.replace(";", ",").replace(" ", ",").split(",") if p]
    if len(parts) == 4 and all(p.lstrip("-").isdigit() for p in parts):
        x, y, w, h = map(int, parts)
        return (x, y, x + w, y + h), "область"
    monitors = await asyncio.to_thread(win.monitors)
    index = get_int(args, "monitor", None)
    if index:
        if not 1 <= index <= len(monitors):
            raise ToolError(f"нет монитора {index}; всего {len(monitors)}")
        screen = monitors[index - 1]
    else:
        active = await asyncio.to_thread(win.foreground)
        screen = monitor_for(active, monitors) if active else _primary(monitors)
    return (screen["x"], screen["y"], screen["x"] + screen["width"], screen["y"] + screen["height"]), "экран"


async def capture(args: dict) -> tuple[bytes, tuple[int, int], str]:
    box, label = await area(args)
    return await asyncio.to_thread(grab, box), (box[0], box[1]), label


async def screen_lines(image: bytes, offset: tuple[int, int]) -> list:
    return ocr.shift(await ocr.recognize(image), *offset)


def _has_vision(ctx: Any) -> bool:
    return getattr(getattr(ctx, "app", None), "router", None) is not None


async def locate(ctx: Any, args: dict) -> tuple[int, int, str]:
    """Screen point of a target: exact text via OCR first, then the vision model."""
    target = need(args, "target", "текст на экране или описание элемента")
    image, offset, _label = await capture(args)
    if not get_bool(args, "visual"):
        try:
            hits = ocr.find_text(await screen_lines(image, offset), target)
        except ocr.OcrUnavailable:
            hits = []
        if hits:
            more = f"; совпадений {len(hits)}, взято первое" if len(hits) > 1 else ""
            return hits[0]["x"], hits[0]["y"], f"текст «{clip(hits[0]['text'], 60)}»{more}"
    if not _has_vision(ctx):
        raise ToolError(f"«{target}» не найден на экране")
    question = (f"Найди на снимке экрана: {target}. Ответь одной строкой: координаты центра (x, y) и что это. "
                "Если такого нет — ответь «нет».")
    text = await vision.describe(ctx.app, image, question, cancel=ctx.cancel, max_tokens=200, coords=True,
                                 offset=offset)
    match = vision.COORD_RE.search(text)
    if not match:
        raise ToolError(f"не нашёл «{target}»: {clip(text, 200)}")
    return int(match.group(1)), int(match.group(2)), clip(text, 160)


async def run(action: str, args: dict, ctx: Any) -> Any:  # noqa: C901 - one branch per action
    if action == "screenshot":
        image, offset, label = await capture(args)
        preview = vision.data_url(vision.thumbnail(image), "image/jpeg")
        if not _has_vision(ctx):
            return Result(f"{label}: снимок сделан, но модель зрения недоступна — используйте ocr", image=preview)
        text = await vision.describe(ctx.app, image, get_str(args, "text"), cancel=ctx.cancel, coords=True,
                                     offset=offset)
        return Result(f"{label}: {text}", image=preview)
    if action == "ocr":
        image, offset, label = await capture(args)
        try:
            lines = await screen_lines(image, offset)
        except ocr.OcrUnavailable as exc:
            raise ToolError(f"OCR недоступен: {exc}. Используйте screenshot") from exc
        if not lines:
            return f"{label}: текста не найдено"
        coords = get_bool(args, "coords")
        body = "\n".join(f"{ln.text} ({ln.x + ln.w // 2}, {ln.y + ln.h // 2})" if coords else ln.text for ln in lines)
        return clip(f"{label}:\n{body}", 6000)
    if action == "find":
        x, y, note = await locate(ctx, args)
        return f"({x}, {y}) — {note}"
    if action in ("click", "move"):
        x, y, note = get_int(args, "x", None), get_int(args, "y", None), ""
        if x is None or y is None:
            x, y, note = await locate(ctx, args)
        suffix = f" — {note}" if note else ""
        if action == "move":
            await asyncio.to_thread(win.move, x, y)
            return f"курсор в ({x}, {y}){suffix}"
        button = BUTTONS.get(get_str(args, "button", "left").strip().lower(), "left")
        count = max(1, min(3, get_int(args, "count", 1) or 1))
        await asyncio.to_thread(win.click, x, y, button, count)
        await asyncio.sleep(0.3)
        kind = {"right": "правый клик", "middle": "клик колесом"}.get(button, "двойной клик" if count == 2 else "клик")
        return f"{kind} в ({x}, {y}){suffix}"
    if action == "drag":
        x, y, x2, y2 = (get_int(args, key, None) for key in ("x", "y", "x2", "y2"))
        if None in (x, y, x2, y2):
            raise ToolError("нужны x, y (откуда) и x2, y2 (куда)")
        button = BUTTONS.get(get_str(args, "button", "left").strip().lower(), "left")
        await asyncio.to_thread(win.drag, x, y, x2, y2, button)
        return f"перетащил из ({x}, {y}) в ({x2}, {y2})"
    if action == "scroll":
        direction = get_str(args, "direction", "down").strip().lower() or "down"
        amount = max(1, min(50, get_int(args, "amount", 5) or 5))
        sign = 1 if direction in ("up", "вверх", "right", "вправо") else -1
        horizontal = direction in ("left", "right", "влево", "вправо")
        await asyncio.to_thread(win.scroll, sign * amount, get_int(args, "x", None), get_int(args, "y", None), horizontal)
        return f"прокрутил {direction} на {amount}"
    if action == "type":
        text = get_str(args, "text")
        if not text:
            raise ToolError("нужен text")
        await asyncio.to_thread(win.type_text, text)
        enter = get_bool(args, "enter")
        if enter:
            await asyncio.to_thread(win.hotkey, "enter")
        return f"напечатал {len(text)} симв." + (" и нажал Enter" if enter else "")
    if action == "keys":
        combo = need(args, "keys", "например ctrl+c, alt+tab, win+d")
        repeat = max(1, min(50, get_int(args, "repeat", 1) or 1))
        await asyncio.to_thread(win.hotkey, combo, repeat)
        return f"нажал {combo}" + (f" ×{repeat}" if repeat > 1 else "")
    if action == "cursor":
        x, y = await asyncio.to_thread(win.cursor_pos)
        try:
            what = await uia.call(uia.at_point, x, y, timeout=5)
        except Exception:  # noqa: BLE001 - UI Automation is optional here
            what = ""
        return f"курсор в ({x}, {y})" + (f": {what}" if what else "")
    if action == "screen":
        monitors = await asyncio.to_thread(win.monitors)
        lines = [f"{i}. {m['width']}×{m['height']} в ({m['x']}, {m['y']})" + (" — основной" if m.get("primary") else "")
                 for i, m in enumerate(monitors, 1)]
        return "Мониторы:\n" + "\n".join(lines)
    if action == "layout":
        lang = get_str(args, "lang").strip().lower()
        if lang:
            await asyncio.to_thread(win.switch_layout, lang)
        return f"раскладка: {await asyncio.to_thread(win.keyboard_layout)}"
    if action == "wait":
        seconds = max(0.2, min(30.0, get_float(args, "seconds", 1.0) or 1.0))
        await asyncio.sleep(seconds)
        return f"подождал {seconds:g} с"
    raise ToolError(f"неизвестное действие {action}")


async def handler(action: str, args: dict, ctx: Any) -> Any:
    try:
        return await run(action, args, ctx)
    except (win.WinError, uia.UiaError) as exc:
        raise ToolError(str(exc)) from exc


def register(reg: Any, app: Any) -> None:
    reg.add(Tool(
        "computer",
        "Мышь, клавиатура и экран Windows. screenshot — посмотреть на экран (координаты в ответе); ocr — прочитать "
        "текст с экрана; find — найти текст или элемент (target) и вернуть (x, y); click (x, y или target; "
        "button, count), move, drag (x, y → x2, y2), scroll (direction, amount), type — напечатать в активное "
        "окно (enter), keys — сочетание клавиш (ctrl+c, alt+tab, win+d), cursor, screen, layout (lang), wait. "
        "area: screen | window | all | x,y,w,h. Для обычных программ надёжнее windows snapshot/click.",
        {"screenshot": A("Смотрю на экран", "read"), "ocr": A("Читаю текст с экрана", "read"),
         "find": A("Ищу на экране «{target}»", "read"), "click": A("Кликаю {target}"),
         "move": A("Веду курсор {target}"), "drag": A("Перетаскиваю"), "scroll": A("Прокручиваю {direction}"),
         "type": A("Печатаю «{text}»"), "keys": A("Нажимаю {keys}"), "cursor": A("Смотрю на курсор", "read"),
         "screen": A("Смотрю мониторы", "read"), "layout": A("Раскладка {lang}"),
         "wait": A("Жду {seconds} с", "read")},
        {"x": I("x на экране"), "y": I("y на экране"), "x2": I("drag: куда x"), "y2": I("drag: куда y"),
         "target": S("текст на экране или описание элемента"), "visual": B("искать глазами, без OCR"),
         "button": S("left | right | middle"), "count": I("кликов (2 — двойной)"),
         "direction": S("down | up | left | right"), "amount": I("scroll: щелчков колеса"),
         "text": S("type: текст; screenshot: вопрос"), "enter": B("type: Enter после"),
         "keys": S("сочетание клавиш"), "repeat": I("keys: повторов"), "area": S("screen | window | all | x,y,w,h"),
         "monitor": I("номер монитора"), "window": S("какое окно снять"), "coords": B("ocr: с координатами"),
         "lang": S("ru | en"), "seconds": N("wait: секунд")},
        handler, available=lambda: win.IS_WINDOWS, timeout=120))
