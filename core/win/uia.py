"""UI Automation for any Windows app: a numbered snapshot of the interactive elements of a window or an open
menu, search by name, reading all text, and actions by ref (invoke/click/type/toggle/select/expand).
Uses the `uiautomation` package. Every function here must run on the single UIA worker thread, so call them
through `await uia.call(fn, ...)`.
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import time
from typing import Any, Callable

from ..util import Worker, clip
from . import IS_WINDOWS

log = logging.getLogger("idos.uia")

ROLES = {
    "ButtonControl": "кнопка", "SplitButtonControl": "кнопка-меню", "CheckBoxControl": "флажок",
    "RadioButtonControl": "переключатель", "ComboBoxControl": "список", "EditControl": "поле ввода",
    "DocumentControl": "документ", "HyperlinkControl": "ссылка", "ListItemControl": "элемент",
    "MenuItemControl": "пункт меню", "TabItemControl": "вкладка", "TreeItemControl": "узел",
    "SliderControl": "ползунок", "SpinnerControl": "счётчик", "DataItemControl": "строка",
    "HeaderItemControl": "столбец",
}
VALUED = {"EditControl", "ComboBoxControl", "DocumentControl", "SliderControl", "SpinnerControl"}
TEXTUAL = {"TextControl", "HyperlinkControl", "ButtonControl", "ListItemControl", "MenuItemControl",
           "TabItemControl", "TreeItemControl", "DataItemControl", "HeaderItemControl", "CheckBoxControl",
           "RadioButtonControl", "StatusBarControl"}
POPUP_CLASSES = {"#32768", "Xaml_WindowedPopupClass", "Popup", "PopupHost", "Net UI Tool Window", "DropDown"}
REFS: dict[int, dict] = {}
_auto: Any = None
_initializer: Any = None
_worker: Worker | None = None


class UiaError(RuntimeError):
    pass


def available() -> bool:
    return IS_WINDOWS and importlib.util.find_spec("uiautomation") is not None


def module() -> Any:
    global _auto
    if _auto is None:
        try:
            import uiautomation as auto
        except ImportError as exc:
            raise UiaError("нет пакета uiautomation — элементы окон недоступны") from exc
        _auto = auto
    return _auto


def _thread_init() -> None:
    global _initializer
    try:
        _initializer = module().UIAutomationInitializerInThread(debug=False)
    except Exception:  # noqa: BLE001 - reported on the first real call
        log.debug("UIA init failed", exc_info=True)


async def call(fn: Callable[..., Any], *args: Any, timeout: float = 30.0, **kwargs: Any) -> Any:
    """Run fn on the UIA thread. A modal dialog can block Invoke for good, so a stuck thread is abandoned."""
    global _worker
    if _worker is None:
        _worker = Worker("uia", init=_thread_init)
    worker = _worker
    try:
        return await asyncio.wait_for(worker.run(lambda: fn(*args, **kwargs)), timeout)
    except asyncio.TimeoutError:
        if _worker is worker:
            _worker = None
            REFS.clear()
        raise UiaError("приложение не ответило вовремя — возможно, открылся диалог; сделайте snapshot заново") from None


def _pattern(control: Any, name: str) -> Any:
    try:
        return control.GetPattern(getattr(module().PatternId, name))
    except Exception:  # noqa: BLE001
        return None


def _center(control: Any) -> tuple[int, int, int, int]:
    r = control.BoundingRectangle
    left, top, right, bottom = int(r.left), int(r.top), int(r.right), int(r.bottom)
    return (left + right) // 2, (top + bottom) // 2, right - left, bottom - top


def _value(control: Any) -> str:
    pattern = _pattern(control, "ValuePattern")
    try:
        return str(pattern.Value or "") if pattern is not None else ""
    except Exception:  # noqa: BLE001
        return ""


def _document_text(control: Any, limit: int = 6000) -> str:
    pattern = _pattern(control, "TextPattern")
    try:
        return str(pattern.DocumentRange.GetText(limit) or "") if pattern is not None else ""
    except Exception:  # noqa: BLE001
        return ""


def _state(control: Any, kind: str) -> str:
    try:
        if kind in ("CheckBoxControl", "RadioButtonControl", "ButtonControl"):
            pattern = _pattern(control, "TogglePattern")
            if pattern is not None:
                return {0: "выкл", 1: "вкл", 2: "частично"}.get(int(pattern.ToggleState), "")
        if kind in ("RadioButtonControl", "ListItemControl", "TabItemControl", "TreeItemControl", "DataItemControl"):
            pattern = _pattern(control, "SelectionItemPattern")
            if pattern is not None and pattern.IsSelected:
                return "выбрано"
        if kind in ("ComboBoxControl", "MenuItemControl", "TreeItemControl", "SplitButtonControl"):
            pattern = _pattern(control, "ExpandCollapsePattern")
            if pattern is not None:
                return {0: "свёрнуто", 1: "раскрыто"}.get(int(pattern.ExpandCollapseState), "")
    except Exception:  # noqa: BLE001
        pass
    return ""


def _info(control: Any, kind: str) -> dict:
    x, y, w, h = _center(control)
    return {"control": control, "kind": kind, "name": (control.Name or "").strip(), "id": control.AutomationId or "",
            "value": _value(control) if kind in VALUED else "", "state": _state(control, kind),
            "enabled": bool(control.IsEnabled), "x": x, "y": y, "w": w, "h": h}


def _norm(text: str) -> str:
    return text.lower().replace("ё", "е")


def _walk(root: Any, query: str = "", limit: int = 80, budget: float = 6.0,
          max_nodes: int = 5000) -> tuple[list[dict], bool]:
    started, items, needle = time.monotonic(), [], _norm(query.strip())
    for visited, (control, _depth) in enumerate(module().WalkControl(root, includeTop=False, maxDepth=30)):
        if visited >= max_nodes or time.monotonic() - started > budget:
            return items, True
        try:
            kind = control.ControlTypeName
            if kind not in ROLES or control.IsOffscreen:
                continue
            info = _info(control, kind)
            if info["w"] < 2 or info["h"] < 2:
                continue
            if needle and needle not in _norm(" ".join((info["name"], info["value"], info["id"]))):
                continue
        except Exception:  # noqa: BLE001 - elements disappear while we walk
            continue
        items.append(info)
        if len(items) >= limit:
            return items, True
    return items, False


def _window(hwnd: int | None) -> Any:
    auto = module()
    control = auto.ControlFromHandle(hwnd) if hwnd else auto.GetForegroundControl()
    if control is None:
        raise UiaError("окно не найдено")
    return control


def _roots(hwnd: int | None, surface: str) -> list:
    window = _window(hwnd)
    if surface != "menu":
        return [window]
    try:
        pid = window.ProcessId
    except Exception:  # noqa: BLE001
        pid = None
    popups = []
    for child in module().GetRootControl().GetChildren():
        try:
            if child.ClassName in POPUP_CLASSES or child.ControlTypeName == "MenuControl":
                if child.ClassName == "#32768" or pid is None or child.ProcessId == pid:
                    popups.append(child)
        except Exception:  # noqa: BLE001
            continue
    return popups + [window]


def format_item(ref: int, item: dict) -> str:
    name = item["name"] or item["id"] or "без названия"
    extra = []
    if item["value"] and item["value"] != item["name"]:
        extra.append(f"значение «{clip(item['value'], 50)}»")
    if item["state"]:
        extra.append(item["state"])
    if not item["enabled"]:
        extra.append("недоступно")
    role = ROLES.get(item["kind"], item["kind"].replace("Control", ""))
    return f"[{ref}] {role} «{clip(name, 70)}»" + (" — " + ", ".join(extra) if extra else "")


def snapshot(hwnd: int | None = None, surface: str = "window", query: str = "", limit: int = 80) -> str:
    items: list[dict] = []
    truncated = False
    for root in _roots(hwnd, surface):
        found, cut = _walk(root, query, limit - len(items))
        items += found
        truncated = truncated or cut
        if len(items) >= limit:
            break
    REFS.clear()
    REFS.update(enumerate(items, 1))
    if not items:
        if query:
            return f"ничего не нашёл по запросу «{query}»"
        return "у окна нет доступных элементов — попробуйте computer screenshot или ocr"
    lines = [format_item(ref, item) for ref, item in REFS.items()]
    if truncated:
        lines.append("… показаны не все элементы — уточните поиск через find")
    return "\n".join(lines)


def read_text(hwnd: int | None = None, limit: int = 6000) -> str:
    started, lines, size = time.monotonic(), [], 0
    for visited, (control, _depth) in enumerate(module().WalkControl(_window(hwnd), includeTop=True, maxDepth=30)):
        if visited >= 6000 or time.monotonic() - started > 8:
            lines.append("…")
            break
        try:
            kind = control.ControlTypeName
            if control.IsOffscreen:
                continue
            if kind in VALUED:
                text = _value(control) or (_document_text(control, limit) if kind == "DocumentControl" else "")
            else:
                text = (control.Name or "") if kind in TEXTUAL else ""
        except Exception:  # noqa: BLE001
            continue
        text = " ".join(text.split())
        if text and (not lines or lines[-1] != text):
            lines.append(text)
            size += len(text) + 1
            if size > limit:
                break
    return clip("\n".join(lines), limit) or "в окне нет читаемого текста"


def describe(control: Any) -> str:
    if control is None:
        return "ничего"
    kind = control.ControlTypeName
    info = _info(control, kind)
    return format_item(0, info)[4:] + f" в точке ({info['x']}, {info['y']})"


def at_point(x: int, y: int) -> str:
    return describe(module().ControlFromPoint(x, y))


def focused() -> str:
    return describe(module().GetFocusedControl())


def act(ref: int, action: str = "click", text: str = "", append: bool = False) -> str:  # noqa: C901
    from .. import win
    item = REFS.get(int(ref))
    if item is None:
        raise UiaError(f"элемента [{ref}] нет — сделайте snapshot окна заново")
    control = item["control"]
    name = item["name"] or item["id"] or f"[{ref}]"

    def mouse(button: str = "left", count: int = 1) -> str:
        x, y = item["x"], item["y"]
        try:
            cx, cy, w, h = _center(control)
            if w > 0 and h > 0:
                x, y = cx, cy
        except Exception:  # noqa: BLE001 - use coordinates from the snapshot
            pass
        win.click(x, y, button, count)
        return f"нажато мышью «{name}» в ({x}, {y})"

    if action == "right_click":
        return mouse("right")
    if action == "double_click":
        return mouse("left", 2)
    if action == "focus":
        control.SetFocus()
        return f"фокус на «{name}»"
    if action == "get":
        return _value(control) or _document_text(control, 4000) or item["name"] or "пусто"
    if action == "type":
        pattern = _pattern(control, "ValuePattern")
        if pattern is not None and not append and not getattr(pattern, "IsReadOnly", False):
            try:
                pattern.SetValue(text)
                return f"введено в «{name}»"
            except Exception:  # noqa: BLE001 - fall back to the keyboard
                log.debug("SetValue failed", exc_info=True)
        try:
            control.SetFocus()
        except Exception:  # noqa: BLE001
            mouse()
        if append:
            win.hotkey("ctrl+end")
        elif item["kind"] != "DocumentControl":
            win.hotkey("ctrl+a")
        win.type_text(text)
        return f"введено с клавиатуры в «{name}»"
    simple = {"toggle": ("TogglePattern", "Toggle"), "select": ("SelectionItemPattern", "Select"),
              "expand": ("ExpandCollapsePattern", "Expand"), "collapse": ("ExpandCollapsePattern", "Collapse"),
              "scroll": ("ScrollItemPattern", "ScrollIntoView")}
    if action in simple:
        pattern_name, method = simple[action]
        pattern = _pattern(control, pattern_name)
        if pattern is None:
            if action in ("toggle", "select"):
                return mouse()
            raise UiaError(f"«{name}» не поддерживает {action}")
        getattr(pattern, method)()
        return f"{action}: «{name}»"
    if action != "click":
        raise UiaError(f"неизвестное действие {action}")
    for pattern_name, method in (("InvokePattern", "Invoke"), ("TogglePattern", "Toggle"),
                                 ("SelectionItemPattern", "Select"), ("ExpandCollapsePattern", "Expand"),
                                 ("LegacyIAccessiblePattern", "DoDefaultAction")):
        pattern = _pattern(control, pattern_name)
        if pattern is None:
            continue
        try:
            getattr(pattern, method)()
            return f"нажато «{name}»"
        except Exception:  # noqa: BLE001 - try the next way
            log.debug("%s failed", pattern_name, exc_info=True)
    return mouse()
