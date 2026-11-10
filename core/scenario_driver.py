"""Драйвер сценариев: UIA-элементы, окна, пипетка, подсветка и выполнение действий.

Ядро сценариев (`scenarios.py`) отвечает за хранение и блок-схему, а этот модуль — за
взаимодействие с Windows. Все тяжёлые импорты (uia, win, tkinter) — внутри функций,
поэтому импорт модуля безопасен и на не-Windows.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import queue
import threading
import time
from typing import Any, Callable

from . import bus

log = logging.getLogger("idos.scenario_driver")


class StepError(RuntimeError):
    """Шаг не выполнен (элемент не найден, программа не запустилась…)."""


WINDOW_ACTIONS = {"minimize": "Свернуть", "maximize": "Развернуть", "restore": "Восстановить",
                  "close": "Закрыть", "kill": "Завершить принудительно"}
MEDIA_ACTIONS = {"play_pause": "media_play_pause", "next": "media_next", "previous": "media_prev",
                 "stop": "media_stop", "volume_up": "volume_up", "volume_down": "volume_down", "mute": "volume_mute"}


def target_label(target: Any) -> str:
    if not isinstance(target, dict):
        return "элемент не выбран"
    if target.get("label"):
        return str(target["label"])
    name = target.get("name") or target.get("id") or "элемент"
    proc = target.get("process") or ""
    return f"«{name}»" + (f" в {proc}" if proc else "")


CLICKABLE = {"ButtonControl", "SplitButtonControl", "CheckBoxControl", "RadioButtonControl", "ComboBoxControl",
             "EditControl", "HyperlinkControl", "ListItemControl", "MenuItemControl", "TabItemControl",
             "TreeItemControl", "DataItemControl", "SliderControl", "ImageControl"}


def _same(auto: Any, a: Any, b: Any) -> bool:
    fn = getattr(auto, "ControlsAreSame", None)
    try:
        if fn:
            return bool(fn(a, b))
        return a.GetRuntimeId() == b.GetRuntimeId()
    except Exception:  # noqa: BLE001
        return False


def _rect(control: Any) -> tuple[int, int, int, int]:
    r = control.BoundingRectangle
    return int(r.left), int(r.top), int(r.right), int(r.bottom)


def _clickable(auto: Any, control: Any) -> Any:
    """Под курсором часто текст или картинка внутри кнопки — поднимаемся до самой кнопки."""
    node = control
    for _ in range(4):
        try:
            if node.ControlTypeName in CLICKABLE and node.ControlTypeName != "ImageControl":
                return node
            parent = node.GetParentControl()
        except Exception:  # noqa: BLE001
            break
        if parent is None:
            break
        node = parent
    return control


def capture_point(x: int, y: int, with_path: bool = True) -> dict:
    """Описание элемента под точкой экрана. Вызывать на потоке UIA."""
    from .win import uia
    from . import win
    auto = uia.module()
    raw = auto.ControlFromPoint(x, y)
    if raw is None:
        raise StepError("под курсором нет элемента")
    control = _clickable(auto, raw)
    top = control.GetTopLevelControl() or control
    hwnd = int(getattr(top, "NativeWindowHandle", 0) or 0)
    pid = int(getattr(control, "ProcessId", 0) or 0)
    if pid == os.getpid():
        raise StepError("это окно самого Эйдоса")
    left, top_y, right, bottom = _rect(control)
    wl, wt, wr, wb = _rect(top)
    ww, wh = max(1, wr - wl), max(1, wb - wt)
    cx, cy = (left + right) // 2, (top_y + bottom) // 2
    kind = control.ControlTypeName
    info = {"process": win.process_name(pid) if pid else "", "title": (top.Name or "")[:120],
            "wclass": top.ClassName or "", "type": kind, "name": (control.Name or "").strip()[:120],
            "id": control.AutomationId or "", "cls": control.ClassName or "",
            "rx": round((cx - wl) / ww, 4), "ry": round((cy - wt) / wh, 4),
            "x": cx, "y": cy, "w": right - left, "h": bottom - top_y, "hwnd": hwnd,
            "rect": [left, top_y, right, bottom]}
    role = uia.ROLES.get(kind, kind.replace("Control", "").lower())
    info["label"] = f"{role} «{info['name'] or info['id'] or 'без названия'}»" + \
        (f" — {info['process']}" if info["process"] else "")
    if with_path:
        chain, node = [], control
        for _ in range(30):
            if _same(auto, node, top):
                break
            parent = node.GetParentControl()
            if parent is None:
                break
            index = 0
            try:
                same_kind = [c for c in parent.GetChildren() if c.ControlTypeName == node.ControlTypeName]
                index = next((i for i, c in enumerate(same_kind) if _same(auto, c, node)), 0)
            except Exception:  # noqa: BLE001
                pass
            chain.append({"type": node.ControlTypeName, "name": (node.Name or "")[:80], "id": node.AutomationId or "",
                          "index": index})
            node = parent
        info["path"] = list(reversed(chain))
        same = twins(auto, top, info)
        ordinal = next((i for i, c in enumerate(same, 1) if _same(auto, c, control)), 0)
        info["ref"] = {"runtime": _runtime(control), "ordinal": ordinal, "twins": len(same)}
        if len(same) > 1 and ordinal:
            info["label"] += f" · №{ordinal} из {len(same)} одинаковых"
    return info


def _find_window(target: dict) -> int:
    from . import win
    proc = (target.get("process") or "").lower()
    title = (target.get("title") or "").lower()
    best, best_score = 0, -1
    for w in win.list_windows():
        score = 0
        if proc:
            if (w.get("process") or "").lower() != proc:
                continue
            score += 2
        wt = (w.get("title") or "").lower()
        if title and wt == title:
            score += 3
        elif title and (title in wt or wt in title) and wt:
            score += 1
        if target.get("wclass") and w.get("class") == target.get("wclass"):
            score += 1
        if score > best_score:
            best, best_score = int(w["hwnd"]), score
    if best_score < 0 or (not proc and best_score < 1):
        return 0
    return best


def _runtime(control: Any) -> list[int]:
    try:
        return [int(v) for v in (control.GetRuntimeId() or [])]
    except Exception:  # noqa: BLE001
        return []


def _matches(control: Any, target: dict) -> bool:
    """Тот же ли это вид элемента: тип + AutomationId (если есть) или имя."""
    name = (target.get("name") or "").strip().lower()
    aid = target.get("id") or ""
    kind = target.get("type") or ""
    try:
        if kind and control.ControlTypeName != kind:
            return False
        if aid:
            return control.AutomationId == aid
        if name:
            return (control.Name or "").strip().lower() == name
    except Exception:  # noqa: BLE001
        return False
    return False


def twins(auto: Any, root: Any, target: dict, budget: float = 4.0, limit: int = 9000) -> list:
    """Все элементы окна, неотличимые от цели по типу/имени/id, в порядке обхода дерева UIA."""
    if not ((target.get("name") or "").strip() or target.get("id")):
        return []
    out: list = []
    started = time.monotonic()
    for visited, (control, _depth) in enumerate(auto.WalkControl(root, includeTop=False, maxDepth=40)):
        if visited > limit or time.monotonic() - started > budget:
            break
        if _matches(control, target):
            out.append(control)
    return out


def _follow_path(root: Any, path: list[dict]) -> Any:
    """Идёт от окна по сохранённому пути: тип + порядковый номер среди соседей того же типа."""
    if not path:
        return None
    node = root
    for step in path:
        kids = [c for c in node.GetChildren() if c.ControlTypeName == step.get("type")]
        index = int(step.get("index", 0))
        pick = None
        if index < len(kids) and (not step.get("id") or kids[index].AutomationId == step["id"]):
            pick = kids[index]
        if pick is None:
            same = [c for c in kids if (step.get("id") and c.AutomationId == step["id"])
                    or (not step.get("id") and step.get("name") and (c.Name or "") == step["name"])]
            if len(same) == 1:
                pick = same[0]
        if pick is None:
            return None
        node = pick
    return node


def resolve_in(auto: Any, root: Any, target: dict, allow_coords: bool = True) -> dict | None:
    """Находит выбранный пипеткой элемент внутри окна root.

    Порядок: ref (тот же элемент по RuntimeId) → путь в дереве окна → №N среди одинаковых → координаты.
    При восстановлении по пути изменяемое Name не считается обязательным идентификатором.
    Так при нескольких одинаковых кнопках нажимается именно выбранная, а не первая попавшаяся.
    """
    wl, wt, wr, wb = _rect(root)
    want_x = wl + target.get("rx", 0.5) * (wr - wl)
    want_y = wt + target.get("ry", 0.5) * (wb - wt)
    ref = target.get("ref") or {}
    keyed = bool((target.get("name") or "").strip() or target.get("id"))

    def visible(c: Any) -> bool:
        try:
            left, top_y, right, bottom = _rect(c)
            return right - left >= 2 and bottom - top_y >= 2
        except Exception:  # noqa: BLE001
            return False

    def dist(c: Any) -> float:
        left, top_y, right, bottom = _rect(c)
        return abs((left + right) / 2 - want_x) + abs((top_y + bottom) / 2 - want_y)

    found = twins(auto, root, target) if keyed else []
    chosen, how = None, ""
    rid = ref.get("runtime")
    if rid and found:
        chosen = next((c for c in found if _runtime(c) == list(rid)), None)
        how = "ref: тот же элемент"
    if chosen is None and target.get("path"):
        try:
            node = _follow_path(root, target["path"])
        except Exception:  # noqa: BLE001
            node = None
        if node is not None:
            try:
                if target.get("type") and node.ControlTypeName != target["type"]:
                    node = None
                elif target.get("id") and node.AutomationId != target["id"]:
                    node = None
            except Exception:  # noqa: BLE001 - the UI may replace controls during lookup
                node = None
        if node is not None:
            chosen, how = node, "ref: путь в окне"
    if chosen is None and found:
        n, k = int(ref.get("twins") or 0), int(ref.get("ordinal") or 0)
        if len(found) == 1:
            chosen, how = found[0], "имя/id"
        elif n == len(found) and 1 <= k <= n:
            chosen, how = found[k - 1], f"ref: №{k} из {n}"
        else:
            shown = [c for c in found if visible(c)] or found
            chosen = min(shown, key=dist)
            how = f"ближайший к месту выбора из {len(found)} одинаковых"
    if chosen is not None and visible(chosen):
        left, top_y, right, bottom = _rect(chosen)
        return {"x": (left + right) // 2, "y": (top_y + bottom) // 2, "control": chosen, "how": how,
                "rect": [left, top_y, right, bottom]}
    if allow_coords and "rx" in target and wr - wl > 50:
        return {"x": int(want_x), "y": int(want_y), "control": None, "how": "координаты в окне",
                "rect": [int(want_x) - 10, int(want_y) - 10, int(want_x) + 10, int(want_y) + 10]}
    return None


def resolve(target: dict, allow_coords: bool = True) -> dict | None:
    """Находит выбранный пипеткой элемент. Вызывать на потоке UIA."""
    from .win import uia
    auto = uia.module()
    hwnd = _find_window(target)
    if not hwnd:
        return None
    root = auto.ControlFromHandle(hwnd)
    if root is None:
        return None
    found = resolve_in(auto, root, target, allow_coords)
    if found is not None:
        found["hwnd"] = hwnd
    return found


def read_element(control: Any, property_name: str = "value") -> str:
    """Читает свойство контрола или весь текст его поддерева на UIA-потоке."""
    from .win import uia
    if property_name in ("all_text", "tree_text"):
        started, lines, size = time.monotonic(), [], 0
        previous = ""
        truncated = False
        for visited, (item, _depth) in enumerate(uia.module().WalkControl(control, includeTop=True, maxDepth=50)):
            if visited >= 5000 or time.monotonic() - started > 8:
                truncated = True
                break
            try:
                kind = str(getattr(item, "ControlTypeName", "") or "")
                value = uia._value(item)
                document = uia._document_text(item, 6000) if kind == "DocumentControl" else ""
                name = str(getattr(item, "Name", "") or "")
            except Exception:  # noqa: BLE001 - controls may disappear during the scan
                continue
            parts = [document] if document else [value] + ([name] if name and name != value else [])
            text = "\n".join(part for part in parts if part)
            text = "\n".join(part.strip() for part in str(text).splitlines() if part.strip())
            if not text or text == previous:
                continue
            previous = text
            remaining = 6000 - size
            if remaining <= 0:
                truncated = True
                break
            if len(text) > remaining:
                lines.append(text[:remaining])
                truncated = True
                break
            lines.append(text)
            size += len(text) + 1
        result = "\n".join(lines).strip()
        if truncated and len(result) < 6000:
            result = (result + "\n…")[:6000]
        return result
    kind = str(getattr(control, "ControlTypeName", "") or "")
    name = str(getattr(control, "Name", "") or "").strip()
    if property_name == "name":
        return name
    if property_name == "id":
        return str(getattr(control, "AutomationId", "") or "")
    if property_name == "value":
        return uia._value(control) or (uia._document_text(control, 6000) if kind == "DocumentControl" else "")
    if property_name == "text":
        return uia._document_text(control, 6000) or uia._value(control) or name
    if property_name == "state":
        return uia._state(control, kind)
    raise StepError(f"неизвестное свойство элемента: {property_name}")


def key_down(vk: int) -> bool:
    if os.name != "nt":
        return False
    import ctypes
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


class Highlighter:
    """Прозрачное окно поверх всего экрана с рамкой вокруг элемента (tkinter, без кликов)."""

    def __init__(self) -> None:
        self.q: queue.Queue = queue.Queue()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if os.name != "nt":
            return
        self.thread = threading.Thread(target=self._run, name="idos-pick", daemon=True)
        self.thread.start()

    def show(self, rect: list[int] | None, text: str = "") -> None:
        if self.thread:
            self.q.put(("rect", rect, text))

    def close(self) -> None:
        if self.thread:
            self.q.put(("stop", None, ""))

    def _run(self) -> None:  # pragma: no cover - нужен рабочий стол Windows
        try:
            import ctypes
            import tkinter as tk
            from . import win
            vs = win.virtual_screen()
            root = tk.Tk()
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            root.configure(bg="#ff00ff")
            root.attributes("-transparentcolor", "#ff00ff")
            root.geometry(f"{vs['width']}x{vs['height']}+{vs['x']}+{vs['y']}")
            canvas = tk.Canvas(root, bg="#ff00ff", highlightthickness=0)
            canvas.pack(fill="both", expand=True)
            root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
            style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, style | 0x80000 | 0x20 | 0x80 | 0x08000000)
            ox, oy = vs["x"], vs["y"]

            def poll() -> None:
                try:
                    while True:
                        cmd, rect, text = self.q.get_nowait()
                        if cmd == "stop":
                            root.destroy()
                            return
                        canvas.delete("all")
                        if rect:
                            l, t, r, b = rect[0] - ox - 3, rect[1] - oy - 3, rect[2] - ox + 3, rect[3] - oy + 3
                            canvas.create_rectangle(l, t, r, b, outline="#7c8cff", width=3)
                            ty = t - 26 if t > 30 else b + 6
                            label = canvas.create_text(l + 8, ty + 10, text=text, anchor="w", fill="#ffffff",
                                                       font=("Segoe UI", 10, "bold"))
                            box = canvas.bbox(label)
                            if box:
                                bg = canvas.create_rectangle(box[0] - 8, box[1] - 4, box[2] + 8, box[3] + 4,
                                                             fill="#1b1b22", outline="#7c8cff")
                                canvas.tag_lower(bg, label)
                except queue.Empty:
                    pass
                root.after(30, poll)

            root.after(30, poll)
            root.mainloop()
        except Exception:  # noqa: BLE001 - без подсветки пипетка всё равно работает
            log.debug("highlighter failed", exc_info=True)


def preview_png(rect: list[int], max_w: int = 220) -> str:
    try:
        from PIL import ImageGrab
        pad = 8
        box = (rect[0] - pad, rect[1] - pad, rect[2] + pad, rect[3] + pad)
        image = ImageGrab.grab(bbox=box, all_screens=True)
        if image.width > max_w:
            image = image.resize((max_w, max(1, int(image.height * max_w / image.width))))
        out = io.BytesIO()
        image.save(out, "PNG")
        return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()
    except Exception:  # noqa: BLE001
        return ""


class WinDriver:
    VK_CTRL, VK_ESC = 0x11, 0x1B

    def __init__(self, app: Any) -> None:
        self.app = app

    def check(self) -> None:
        from . import win
        win.failsafe()

    async def _uia(self, fn: Callable, *args: Any, timeout: float = 20.0) -> Any:
        from .win import uia
        return await uia.call(fn, *args, timeout=timeout)

    async def launch(self, app_name: str) -> str:
        from .tools import ToolError
        from .tools.apps import launch
        try:
            return await launch(app_name)
        except ToolError as exc:
            raise StepError(str(exc)) from exc

    async def focus(self, query: str) -> str:
        from . import win
        try:
            info = await asyncio.to_thread(win.find_window, query)
            await asyncio.to_thread(win.focus, int(info["hwnd"]))
        except Exception as exc:  # noqa: BLE001
            raise StepError(f"окно «{query}» не найдено") from exc
        return f"окно «{info.get('title', query)}»"

    async def find(self, target: dict, timeout: float, check: Callable[[], None]) -> dict | None:
        end = time.monotonic() + max(0.0, timeout)
        while True:
            check()
            try:
                found = await self._uia(resolve, target, timeout=10.0)
            except Exception:  # noqa: BLE001
                found = None
            if found and (found["control"] is not None or time.monotonic() >= end):
                return found
            if time.monotonic() >= end:
                return found
            await asyncio.sleep(0.4)

    async def click(self, target: dict, button: str, method: str, timeout: float, check: Callable[[], None]) -> str:
        from . import win
        found = await self.find(target, timeout, check)
        if not found:
            raise StepError(f"не нашёл {target_label(target)} — окно программы не открыто?")
        if method == "invoke" and found["control"] is not None:
            def invoke() -> bool:
                from .win import uia
                for pattern, call in (("InvokePattern", "Invoke"), ("TogglePattern", "Toggle"),
                                      ("SelectionItemPattern", "Select")):
                    p = uia._pattern(found["control"], pattern)  # noqa: SLF001
                    if p is not None:
                        getattr(p, call)()
                        return True
                return False
            if await self._uia(invoke):
                return f"нажато {target_label(target)} (без мыши, {found['how']})"
        try:
            await asyncio.to_thread(win.focus, found["hwnd"])
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(0.12)
        count = 2 if button == "double" else 1
        await asyncio.to_thread(win.click, found["x"], found["y"], "left" if button == "double" else button, count)
        return f"нажато {target_label(target)} ({found['how']})"

    async def read(self, target: dict, property_name: str, timeout: float, check: Callable[[], None]) -> str:
        found = await self.find(target, timeout, check)
        if not found or found.get("control") is None:
            raise StepError(f"не нашёл {target_label(target)}")
        try:
            return str(await self._uia(read_element, found["control"], property_name, timeout=10.0) or "")
        except Exception as exc:  # noqa: BLE001
            raise StepError(f"не удалось прочитать {target_label(target)}: {exc}") from exc

    async def type(self, text: str, target: dict | None, enter: bool, timeout: float, check: Callable[[], None]) -> str:
        from . import win
        if isinstance(target, dict) and (target.get("name") or target.get("id") or target.get("path")):
            await self.click(target, "left", "mouse", timeout, check)
            await asyncio.sleep(0.1)
        if text:
            await asyncio.to_thread(win.type_text, text)
        if enter:
            await asyncio.to_thread(win.hotkey, "enter")
        return f"введено «{text[:40]}»"

    async def hotkey(self, keys: str) -> str:
        from . import win
        try:
            await asyncio.to_thread(win.hotkey, keys)
        except Exception as exc:  # noqa: BLE001
            raise StepError(f"клавиши «{keys}»: {exc}") from exc
        return keys

    async def media(self, action: str) -> str:
        from . import win
        await asyncio.to_thread(win.hotkey, MEDIA_ACTIONS.get(action, "media_play_pause"))
        return action

    async def open(self, path: str) -> str:
        from .tools import ToolError
        from .tools.apps import open_target
        try:
            return await open_target(path)
        except ToolError as exc:
            raise StepError(str(exc)) from exc

    async def window(self, app_name: str, action: str) -> str:
        """Свернуть / развернуть / восстановить / закрыть / завершить программу (пусто — активное окно)."""
        from . import win
        from .tools import ToolError
        from .tools import apps
        try:
            if not app_name:
                info = await asyncio.to_thread(win.foreground)
                if not info or "idos" in str(info.get("process") or "").lower():
                    raise StepError("активное окно — сам Эйдос; укажите программу в блоке")
                if action == "kill":
                    return await apps.kill(str(info.get("pid") or ""))
                if action == "close":
                    await asyncio.to_thread(win.close, int(info["hwnd"]))
                else:
                    await asyncio.to_thread(win.show, int(info["hwnd"]), action)
                return f"{WINDOW_ACTIONS[action].lower()}: «{info.get('title', '')}»"
            if action == "close":
                return await apps.close(app_name)
            if action == "kill":
                return await apps.kill(app_name)
            targets = apps.windows_of(await asyncio.to_thread(win.list_windows), app_name)
            if not targets:
                raise StepError(f"не нашёл открытых окон «{app_name}»")
            for w in targets:
                await asyncio.to_thread(win.show, int(w["hwnd"]), action)
            return f"{WINDOW_ACTIONS[action].lower()}: «{app_name}», окон {len(targets)}"
        except ToolError as exc:
            raise StepError(str(exc)) from exc
        except win.WinError as exc:
            raise StepError(str(exc)) from exc

    async def say(self, text: str, wait: bool = False) -> str:
        tts = getattr(self.app, "tts", None)
        if tts and text:
            await tts.say(text, interrupt=True, wait=wait)
        return text

    async def pick(self, timeout: float = 60.0) -> dict | None:
        """Пипетка: наведите курсор на элемент и нажмите Ctrl; Esc — отмена."""
        from . import win
        hl = Highlighter()
        hl.start()
        last, info = None, None
        end = time.monotonic() + timeout
        try:
            while key_down(self.VK_CTRL):  # отпустите Ctrl, если он был зажат
                await asyncio.sleep(0.05)
            bus.emit("scenario.pick", {"state": "picking", "text": "Наведите на элемент и нажмите Ctrl · Esc — отмена"})
            while time.monotonic() < end:
                if key_down(self.VK_ESC):
                    return None
                pos = await asyncio.to_thread(win.cursor_pos)
                if pos != last:
                    last = pos
                    try:
                        info = await self._uia(capture_point, pos[0], pos[1], False, timeout=5.0)
                        hl.show(info["rect"], info["label"] + "  ·  Ctrl — выбрать, Esc — отмена")
                    except Exception as exc:  # noqa: BLE001
                        info = None
                        hl.show(None)
                        log.debug("hover: %s", exc)
                if info and key_down(self.VK_CTRL):
                    full = await self._uia(capture_point, pos[0], pos[1], True, timeout=10.0)
                    hl.show(None)
                    await asyncio.sleep(0.08)
                    full["preview"] = await asyncio.to_thread(preview_png, full["rect"])
                    return full
                await asyncio.sleep(0.07)
            return None
        finally:
            hl.close()
            bus.emit("scenario.pick", {"state": "done"})

    async def probe(self, target: dict) -> dict:
        found = await self.find(target, 2.0, lambda: None)
        if not found:
            return {"found": False, "text": f"не нашёл {target_label(target)}"}
        hl = Highlighter()
        hl.start()
        hl.show(found["rect"], f"нашёл: {target_label(target)} ({found['how']})")
        await asyncio.sleep(1.6)
        hl.close()
        return {"found": True, "how": found["how"], "x": found["x"], "y": found["y"],
                "text": f"нашёл {target_label(target)} — {found['how']}"}


class NoDriver:
    """Не-Windows / демо: сценарии можно редактировать, но действия не выполняются."""

    def __init__(self, app: Any = None) -> None:
        self.app = app

    async def _no(self, *_: Any, **__: Any) -> str:
        raise StepError("действия сценариев работают только в Windows")

    launch = focus = click = read = type = hotkey = media = open = window = _no

    async def say(self, text: str, wait: bool = False) -> str:
        return text

    async def find(self, *_: Any) -> None:
        return None

    async def pick(self, timeout: float = 60.0) -> None:
        raise StepError("пипетка работает только в Windows")

    async def probe(self, target: dict) -> dict:
        return {"found": False, "text": "проверка элемента работает только в Windows"}
