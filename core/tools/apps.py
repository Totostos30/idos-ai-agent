"""Apps: find and launch anything from the Start menu (classic shortcuts and Store apps) by its name or by the
way people say it, list running apps, close or kill them, open files, folders and links in their default
programs. After a launch the new window is reported, so the agent can go on with the windows tool.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import re
import time
from typing import Any

from .. import win
from ..util import clip, powershell, run_hidden
from . import A, S, Tool, ToolError, get_str, need
from .files import resolve

SPOKEN = {
    "телеграм": "telegram", "телега": "telegram", "дискорд": "discord", "стим": "steam", "спотифай": "spotify",
    "ворд": "word", "эксель": "excel", "пауэрпоинт": "powerpoint", "паверпоинт": "powerpoint", "хром": "chrome",
    "гугл хром": "chrome", "яндекс браузер": "yandex", "эдж": "edge", "обс": "obs", "вс код": "visual studio code",
    "вскод": "visual studio code", "вижуал студио код": "visual studio code", "фотошоп": "photoshop", "зум": "zoom",
    "скайп": "skype", "вайбер": "viber", "ватсап": "whatsapp", "ноушн": "notion", "фигма": "figma", "блендер": "blender",
    "проводник": "explorer", "калькулятор": "calculator", "блокнот": "notepad", "параметры": "settings",
    "настройки": "settings", "диспетчер задач": "task manager", "терминал": "terminal",
    "командная строка": "command prompt", "пэйнт": "paint", "паинт": "paint", "почта": "mail",
    "магазин": "microsoft store", "панель управления": "control panel", "ножницы": "snipping tool",
    "тимс": "teams", "аутлук": "outlook", "файрфокс": "firefox", "опера": "opera", "торрент": "qbittorrent",
    "вконтакте": "vk", "обсидиан": "obsidian", "курсор": "cursor", "винрар": "winrar",
}
FALLBACK = {"calculator": "calc", "notepad": "notepad", "explorer": "explorer", "settings": "ms-settings:",
            "task manager": "taskmgr", "terminal": "wt", "command prompt": "cmd", "paint": "mspaint",
            "control panel": "control", "snipping tool": "snippingtool", "word": "winword", "excel": "excel",
            "powerpoint": "powerpnt", "edge": "msedge", "chrome": "chrome", "firefox": "firefox"}
TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l",
    "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya"})
SKIP = ("uninstall", "удал", "readme", "справка", "help", "documentation", "website", "release notes", "лицензи")
URL_LIKE = re.compile(r"^(www\.)?[\w-]+(\.[\w-]+)*\.(ru|com|org|net|io|ai|dev|app|me|info|su|by|kz|ua|co|tv|pro|xyz|"
                      r"online|site|tech|рф)(/\S*)?$", re.I)
WAIT = 8.0
_cache: dict[str, Any] = {"items": [], "time": 0.0}


def norm(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").replace("_", " ").replace("-", " ").split())


def start(target: str, arguments: str = "") -> None:
    """ShellExecute: shortcuts, files, folders, links, App Paths names (winword) and ms-settings: pages."""
    starter = getattr(os, "startfile", None)
    if starter is None:
        raise ToolError("запуск программ работает только в Windows")
    if arguments:
        starter(target, "open", arguments)
    else:
        starter(target)


def start_menu_dirs() -> list[pathlib.Path]:
    return [pathlib.Path(os.environ[env]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            for env in ("ProgramData", "APPDATA") if os.environ.get(env)]


def scan_shortcuts(dirs: list[pathlib.Path]) -> list[dict]:
    out = []
    for root in dirs:
        if root.is_dir():
            for path in root.rglob("*"):
                if path.suffix.lower() in (".lnk", ".url", ".appref-ms"):
                    out.append({"name": path.stem, "target": str(path), "kind": "shortcut"})
    return out


def scan_store_apps() -> list[dict]:
    code, out, _err = powershell("Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress", timeout=25)
    if code != 0 or not out:
        return []
    try:
        data = json.loads(out)
    except ValueError:
        return []
    rows = [data] if isinstance(data, dict) else data
    return [{"name": row["Name"], "target": row["AppID"], "kind": "store"} for row in rows
            if isinstance(row, dict) and row.get("Name") and row.get("AppID")]


def build_index() -> list[dict]:
    items, seen = [], set()
    for item in scan_shortcuts(start_menu_dirs()) + scan_store_apps():
        key = norm(item["name"])
        if key and key not in seen and not any(word in key for word in SKIP):
            seen.add(key)
            items.append(item)
    return items


async def index(refresh: bool = False) -> list[dict]:
    if refresh or not _cache["items"] or time.monotonic() - _cache["time"] > 600:
        _cache["items"] = await asyncio.to_thread(build_index)
        _cache["time"] = time.monotonic()
    return _cache["items"]


def variants(query: str) -> list[str]:
    q = norm(query)
    words = " ".join(SPOKEN.get(w, w) for w in q.split())
    return [v for v in dict.fromkeys([q, SPOKEN.get(q, ""), words, q.translate(TRANSLIT)]) if v]


def score(name: str, query: str) -> int:
    n = norm(name)
    words = n.split()
    if n == query:
        return 100
    if n.startswith(query):
        return 85
    if any(w.startswith(query) for w in words):
        return 70
    if query in n:
        return 60
    parts = query.split()
    if len(parts) > 1 and all(any(w.startswith(p) for w in words) for p in parts):
        return 50
    return 0


def match(items: list[dict], query: str, limit: int = 5) -> list[dict]:
    ranked = []
    for penalty, variant in enumerate(variants(query)):
        for item in items:
            value = score(item["name"], variant)
            if value:
                ranked.append((value - penalty, -len(item["name"]), item))
    ranked.sort(key=lambda r: (r[0], r[1]), reverse=True)
    out: list[dict] = []
    for _value, _length, item in ranked:
        if item not in out:
            out.append(item)
        if len(out) >= limit:
            break
    return out


def _process(w: dict) -> str:
    return norm(os.path.splitext(w.get("process") or "")[0])


def _matches(w: dict, hints: list[str]) -> bool:
    text = norm(f"{w.get('title', '')} {w.get('process', '')}")
    return any(h in text for h in hints)


async def wait_window(before: set[int], hints: list[str], timeout: float = 8.0) -> tuple[dict | None, bool]:
    """The window that appeared after a launch; (existing window, False) for single-instance apps."""
    deadline, first_fresh, windows = time.monotonic() + timeout, 0.0, []
    while time.monotonic() < deadline:
        await asyncio.sleep(0.4)
        try:
            windows = await asyncio.to_thread(win.list_windows)
        except Exception:  # noqa: BLE001 - window listing is best effort
            return None, False
        fresh = [w for w in windows if w["hwnd"] not in before]
        for w in fresh:
            if _matches(w, hints):
                return w, True
        if fresh:
            first_fresh = first_fresh or time.monotonic()
            if time.monotonic() - first_fresh > 2.0:
                return fresh[0], True
    return next((w for w in windows if _matches(w, hints)), None), False


async def launch(name: str, arguments: str = "") -> str:
    raw = name.strip().strip('"')
    try:
        before = {w["hwnd"] for w in await asyncio.to_thread(win.list_windows)}
    except Exception:  # noqa: BLE001
        before = set()
    target, label, kind = raw, raw, "raw"
    if "://" in raw or raw.lower().startswith(("ms-settings:", "shell:", "mailto:")):
        kind = "direct"
    elif any(sep in raw for sep in "/\\") and resolve(raw).exists():
        target, kind = str(resolve(raw)), "direct"
    else:
        found = match(await index(), raw, 1)
        if found:
            item = found[0]
            label, kind = item["name"], item["kind"]
            target = item["target"] if kind == "shortcut" else f"shell:AppsFolder\\{item['target']}"
        else:
            target = FALLBACK.get(next((v for v in variants(raw) if v in FALLBACK), ""), raw)
    try:
        await asyncio.to_thread(start, target, arguments)
    except OSError as exc:
        similar = ", ".join(i["name"] for i in match(await index(), raw, 3))
        raise ToolError(f"не нашёл программу «{name}»" + (f"; похожие: {similar}" if similar else "")) from exc
    if kind == "direct":
        return f"открыл {clip(target, 100)}"
    hints = [w for w in norm(label).split() if len(w) >= 3] + [v for v in variants(raw) if len(v) >= 3]
    if kind == "shortcut":
        hints.append(norm(pathlib.PureWindowsPath(target).stem))
    window, fresh = await wait_window(before, list(dict.fromkeys(hints)), WAIT)
    if window is None:
        return f"запустил «{label}», окно пока не появилось"
    where = f"окно {window['hwnd']}: «{clip(window['title'], 60)}» ({window['process']})"
    return f"запустил «{label}» — {where}" if fresh else f"«{label}» уже открыт — {where}"


def windows_of(windows: list[dict], name: str) -> list[dict]:
    names = variants(name)
    return [w for w in windows if any(v == _process(w) or v in norm(w.get("title", "")) or
                                      (len(v) >= 4 and v in _process(w)) for v in names)]


async def running() -> str:
    groups: dict[str, list[str]] = {}
    for w in await asyncio.to_thread(win.list_windows):
        groups.setdefault(w.get("process") or "?", []).append(f"«{clip(w['title'], 50)}» ({w['hwnd']})")
    return "\n".join(f"{proc}: {', '.join(titles)}" for proc, titles in groups.items()) or "программ с окнами нет"


async def close(name: str) -> str:
    targets = windows_of(await asyncio.to_thread(win.list_windows), name)
    if not targets:
        raise ToolError(f"не нашёл открытых окон «{name}»")
    for w in targets:
        await asyncio.to_thread(win.close, w["hwnd"])
    await asyncio.sleep(0.8)
    alive = {w["hwnd"] for w in await asyncio.to_thread(win.list_windows)}
    left = [w for w in targets if w["hwnd"] in alive]
    text = f"закрыл окон: {len(targets) - len(left)} из {len(targets)}"
    if left:
        text += f"; «{clip(left[0]['title'], 50)}» ещё открыто — возможно, спрашивает о сохранении (kill — принудительно)"
    return text


async def kill(name: str) -> str:
    name = name.strip()
    if not name.isdigit() and not name.lower().endswith(".exe"):
        procs = [w["process"] for w in windows_of(await asyncio.to_thread(win.list_windows), name) if w.get("process")]
        fallback = next((FALLBACK[v] for v in variants(name) if FALLBACK.get(v, "").isalnum()), name)
        name = procs[0] if procs else fallback + ".exe"
    args = ["taskkill", "/F", "/T"] + (["/PID", name] if name.isdigit() else ["/IM", name])
    proc = await asyncio.to_thread(run_hidden, args, 20)
    text = (proc.stdout or proc.stderr or "").strip()
    if proc.returncode != 0:
        raise ToolError(clip(text or f"не удалось завершить {name}", 300))
    return clip(text, 300) or f"завершил {name}"


async def open_target(raw: str) -> str:
    text = raw.strip().strip('"')
    if "://" in text or text.lower().startswith(("mailto:", "ms-settings:")):
        await asyncio.to_thread(start, text)
        return f"открыл {clip(text, 80)}"
    path = resolve(text)
    if not path.exists():
        if URL_LIKE.match(text):
            await asyncio.to_thread(start, "https://" + text)
            return "открыл " + "https:" + "//" + clip(text, 80)
        raise ToolError(f"нет такого файла или папки: {path}")
    await asyncio.to_thread(start, str(path))
    return f"открыл {path}"


async def handler(action: str, args: dict, ctx: Any) -> Any:
    try:
        if action == "launch":
            return await launch(need(args, "name", "название программы"), get_str(args, "args"))
        if action == "search":
            found = match(await index(), need(args, "name", "что искать"), 8)
            return "\n".join(f"{i['name']} ({'Store' if i['kind'] == 'store' else 'ярлык'})" for i in found) or "ничего не нашёл"
        if action == "list":
            return await running()
        if action == "close":
            return await close(need(args, "name", "программа или окно"))
        if action == "kill":
            return await kill(need(args, "name", "процесс или PID"))
        if action == "open":
            return await open_target(need(args, "path", "файл, папка или ссылка"))
        if action == "refresh":
            return f"программ в списке: {len(await index(refresh=True))}"
    except win.WinError as exc:
        raise ToolError(str(exc)) from exc
    raise ToolError(f"неизвестное действие {action}")


def register(reg: Any, app: Any) -> None:
    reg.add(Tool(
        "apps",
        "Программы. launch — запустить по названию (меню Пуск, Store, «телеграм», «вс код»), пути или ссылке — "
        "в ответе номер окна для windows; search — найти установленные; list — запущенные и их окна; "
        "close — закрыть окна программы; kill — завершить процесс; open — открыть файл, папку или "
        "ссылку программой по умолчанию; refresh — обновить список программ.",
        {"launch": A("Запускаю {name}"), "search": A("Ищу программу {name}", "read"),
         "list": A("Смотрю запущенные программы", "read"), "close": A("Закрываю {name}"),
         "kill": A("Завершаю процесс {name}", "danger"), "open": A("Открываю {path}"),
         "refresh": A("Обновляю список программ", "read")},
        {"name": S("программа, процесс, PID или путь"), "path": S("open: файл, папка или ссылка"),
         "args": S("launch: аргументы командной строки")},
        handler, available=lambda: win.IS_WINDOWS, timeout=60))
