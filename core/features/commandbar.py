"""Command bar (Alt+Space): one line that launches apps, counts, converts, starts timers, opens sites and settings,
searches the web or hands the request to the agent. Results are ranked by match quality and by how often the user
picked them (table `launcher`)."""
from __future__ import annotations

import ast
import logging
import math
import operator
import re
import time
import urllib.parse
from typing import Any

from .. import bus, config
from . import Feature

log = logging.getLogger("idos.commandbar")
LIMIT = 8

SETTINGS_PAGES = {
    "wi-fi": ("Wi‑Fi", "ms-settings:network-wifi"), "wifi": ("Wi‑Fi", "ms-settings:network-wifi"),
    "сеть": ("Сеть и интернет", "ms-settings:network"), "bluetooth": ("Bluetooth", "ms-settings:bluetooth"),
    "блютуз": ("Bluetooth", "ms-settings:bluetooth"), "звук": ("Звук", "ms-settings:sound"),
    "экран": ("Экран", "ms-settings:display"), "дисплей": ("Экран", "ms-settings:display"),
    "обновлен": ("Центр обновления", "ms-settings:windowsupdate"), "update": ("Центр обновления", "ms-settings:windowsupdate"),
    "приложен": ("Приложения", "ms-settings:appsfeatures"), "батаре": ("Питание и батарея", "ms-settings:batterysaver"),
    "питани": ("Питание и батарея", "ms-settings:powersleep"), "фон": ("Персонализация: фон", "ms-settings:personalization-background"),
    "тем": ("Темы", "ms-settings:themes"), "язык": ("Язык и регион", "ms-settings:regionlanguage"),
    "раскладк": ("Клавиатура", "ms-settings:keyboard"), "мыш": ("Мышь", "ms-settings:mousetouchpad"),
    "vpn": ("VPN", "ms-settings:network-vpn"), "прокси": ("Прокси", "ms-settings:network-proxy"),
    "уведомлен": ("Уведомления", "ms-settings:notifications"), "хранилищ": ("Память устройства", "ms-settings:storagesense"),
    "принтер": ("Принтеры", "ms-settings:printers"), "конфиденциальн": ("Конфиденциальность", "ms-settings:privacy"),
    "микрофон": ("Микрофон", "ms-settings:privacy-microphone"), "камер": ("Камера", "ms-settings:privacy-webcam"),
}
SYSTEM = {
    "заблокир": ("Заблокировать компьютер", "lock"), "lock": ("Заблокировать компьютер", "lock"),
    "диспетчер": ("Диспетчер задач", "taskmgr"), "проводник": ("Проводник", "explorer"),
    "корзин": ("Корзина", "shell:RecycleBinFolder"), "загрузк": ("Папка «Загрузки»", "shell:Downloads"),
    "панель управлен": ("Панель управления", "control"), "параметр": ("Параметры Windows", "ms-settings:"),
}

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow, ast.USub: operator.neg,
        ast.UAdd: operator.pos}
_FUNCS = {"sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan, "log": math.log10, "ln": math.log,
          "abs": abs, "round": round, "pi": math.pi, "e": math.e, "корень": math.sqrt}
CALC_CHARS = re.compile(r"^[\d\s+\-*/^%().,a-zа-я]+$", re.I)

UNITS: dict[str, tuple[str, float]] = {
    # length (metre)
    "мм": ("len", 0.001), "mm": ("len", 0.001), "см": ("len", 0.01), "cm": ("len", 0.01), "м": ("len", 1.0),
    "m": ("len", 1.0), "км": ("len", 1000.0), "km": ("len", 1000.0), "дюйм": ("len", 0.0254), "in": ("len", 0.0254),
    "фут": ("len", 0.3048), "ft": ("len", 0.3048), "миля": ("len", 1609.344), "мили": ("len", 1609.344),
    "mi": ("len", 1609.344),
    # mass (kilogram)
    "г": ("mass", 0.001), "g": ("mass", 0.001), "кг": ("mass", 1.0), "kg": ("mass", 1.0), "т": ("mass", 1000.0),
    "lb": ("mass", 0.45359237), "фунт": ("mass", 0.45359237), "oz": ("mass", 0.0283495), "унция": ("mass", 0.0283495),
    # data (byte)
    "б": ("data", 1.0), "кб": ("data", 1024.0), "kb": ("data", 1024.0), "мб": ("data", 1024.0 ** 2),
    "mb": ("data", 1024.0 ** 2), "гб": ("data", 1024.0 ** 3), "gb": ("data", 1024.0 ** 3), "тб": ("data", 1024.0 ** 4),
    "tb": ("data", 1024.0 ** 4),
    # time (second)
    "сек": ("time", 1.0), "s": ("time", 1.0), "мин": ("time", 60.0), "min": ("time", 60.0), "ч": ("time", 3600.0),
    "h": ("time", 3600.0), "дн": ("time", 86400.0), "день": ("time", 86400.0), "дней": ("time", 86400.0),
    "нед": ("time", 604800.0),
    # speed (m/s)
    "км/ч": ("speed", 1 / 3.6), "kmh": ("speed", 1 / 3.6), "м/с": ("speed", 1.0), "mph": ("speed", 0.44704),
}
TEMPS = {"c": "c", "с": "c", "°c": "c", "°с": "c", "цельс": "c", "f": "f", "°f": "f", "фаренг": "f", "k": "k",
         "кельв": "k"}
CONVERT_RE = re.compile(r"^\s*(-?\d+(?:[.,]\d+)?)\s*([^\s\d]+)\s+(?:в|to|in|->|=)\s+([^\s\d]+)\s*$", re.I)
URL_RE = re.compile(r"^(https?://\S+|(?:www\.)?[\w-]+(?:\.[\w-]+)*\.(?:ru|com|org|net|io|ai|dev|app|me|su|рф|info|tv|pro)"
                    r"(?:/\S*)?)$", re.I)
TIMER_RE = re.compile(r"^\s*(?:таймер|timer)\s+(.+)$", re.I)
REMIND_RE = re.compile(r"^\s*(?:напомни|напоминание|remind)\s+(.+)$", re.I)
WEB_RE = re.compile(r"^\s*(?:\?|g\s|гугл\s|найди в интернете\s|search\s)\s*(.+)$", re.I)


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("слишком большая степень")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Name) and node.id in _FUNCS and not callable(_FUNCS[node.id]):
        return _FUNCS[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and callable(_FUNCS.get(node.func.id)):
        return _FUNCS[node.func.id](*[_eval(a) for a in node.args])
    raise ValueError("не выражение")


def calculate(text: str) -> float | None:
    """Safe arithmetic: '2+2*3', '15% от 300', '2^10', 'sqrt(16)', '1 200,5 / 2'. None if it is not math."""
    t = (text or "").strip().lstrip("=").strip().lower()
    if not t or not re.search(r"\d", t) or not CALC_CHARS.match(t.replace("от", "")):
        return None
    percent = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*%\s*(?:от|of)\s*(\d+(?:[.,]\d+)?)", t)
    if percent:
        return float(percent.group(1).replace(",", ".")) * float(percent.group(2).replace(",", ".")) / 100
    t = re.sub(r"(?<=\d)\s+(?=\d{3}\b)", "", t).replace(",", ".").replace("^", "**").replace("×", "*").replace("÷", "/")
    if not re.search(r"[-+*/%(]", t) and not re.match(r"^[a-zа-я]+\(", t):
        return None
    try:
        value = _eval(ast.parse(t, mode="eval"))
    except (SyntaxError, ValueError, TypeError, ZeroDivisionError, OverflowError):
        return None
    if isinstance(value, complex) or (isinstance(value, float) and not math.isfinite(value)):
        return None
    return float(value)


def fmt_number(value: float) -> str:
    if abs(value - round(value)) < 1e-9 and abs(value) < 1e15:
        return f"{int(round(value)):,}".replace(",", " ")
    text = f"{value:,.6f}".rstrip("0").rstrip(".")
    return text.replace(",", " ").replace(".", ",")


def _temp_unit(word: str) -> str | None:
    w = word.lower().strip("°")
    return next((v for k, v in TEMPS.items() if w == k.strip("°") or (len(k) > 2 and w.startswith(k))), None)


def convert(text: str) -> tuple[float, str] | None:
    """'10 км в мили', '100 f в c', '2 гб в мб' → (value, target unit)."""
    m = CONVERT_RE.match(text or "")
    if not m:
        return None
    value, src, dst = float(m.group(1).replace(",", ".")), m.group(2).lower(), m.group(3).lower()
    ts, td = _temp_unit(src), _temp_unit(dst)
    if ts and td:
        kelvin = {"c": value + 273.15, "f": (value - 32) * 5 / 9 + 273.15, "k": value}[ts]
        out = {"c": kelvin - 273.15, "f": (kelvin - 273.15) * 9 / 5 + 32, "k": kelvin}[td]
        return out, {"c": "°C", "f": "°F", "k": "K"}[td]
    a, b = UNITS.get(src.rstrip(".")), UNITS.get(dst.rstrip("."))
    if not a or not b or a[0] != b[0]:
        return None
    return value * a[1] / b[1], dst


def item(kind: str, title: str, subtitle: str = "", action: dict | None = None, score: int = 50, icon: str = "",
         key: str = "") -> dict:
    return {"kind": kind, "title": title, "subtitle": subtitle, "action": action or {}, "score": score, "icon": icon,
            "key": key or f"{kind}:{title}"}


class CommandBar(Feature):
    name = "commandbar"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.history: list[str] = []

    # ------------------------------------------------------------------ ranking
    def usage(self) -> dict[str, tuple[int, float]]:
        if self.db is None:
            return {}
        try:
            return {r["key"]: (r["uses"], r["last"]) for r in self.db.query("SELECT key, uses, last FROM launcher")}
        except Exception:  # noqa: BLE001
            return {}

    def remember(self, key: str) -> None:
        if self.db is None or not key:
            return
        self.db.execute("INSERT INTO launcher(key, uses, last) VALUES (?, 1, ?) ON CONFLICT(key) DO UPDATE SET "
                        "uses = uses + 1, last = excluded.last", (key, time.time()))

    def rank(self, items: list[dict]) -> list[dict]:
        usage, now = self.usage(), time.time()
        for it in items:
            uses, last = usage.get(it["key"], (0, 0.0))
            recent = 6 if now - last < 86400 else 0
            it["score"] += min(25, uses * 3) + recent
        out, seen = [], set()
        for it in sorted(items, key=lambda i: i["score"], reverse=True):
            if it["key"] not in seen:
                seen.add(it["key"])
                out.append(it)
        return out[:LIMIT]

    # ------------------------------------------------------------------ search
    async def apps(self, query: str) -> list[dict]:
        if not config.IS_WINDOWS:
            return []
        try:
            from ..tools import apps

            found = apps.match(await apps.index(), query, 5)
        except Exception:  # noqa: BLE001 - index unavailable
            log.debug("app index failed", exc_info=True)
            return []
        out = []
        for n, app_item in enumerate(found):
            out.append(item("app", app_item["name"], "Приложение", {"type": "launch", "name": app_item["name"]},
                            80 - n * 4, "app", f"app:{app_item['name']}"))
        return out

    async def query(self, text: str) -> list[dict]:
        q = (text or "").strip()
        if not q:
            return self.rank([item("recent", h, "Недавний запрос", {"type": "ask", "text": h}, 30, "history")
                              for h in reversed(self.history[-5:])])
        low = q.lower()
        items: list[dict] = []
        value = calculate(q)
        if value is not None:
            shown = fmt_number(value)
            items.append(item("calc", f"= {shown}", "Скопировать результат", {"type": "copy", "text": shown}, 99, "calc"))
        conv = convert(q)
        if conv is not None:
            shown = f"{fmt_number(conv[0])} {conv[1]}"
            items.append(item("convert", shown, "Конвертер — скопировать", {"type": "copy", "text": shown}, 98, "convert"))
        m = TIMER_RE.match(q)
        if m:
            items.append(item("timer", f"Таймер: {m.group(1)}", "Запустить на острове",
                              {"type": "timer", "duration": m.group(1)}, 97, "timer"))
        m = REMIND_RE.match(q)
        if m:
            items.append(item("remind", f"Напомнить: {m.group(1)}", "Создать напоминание",
                              {"type": "ask", "text": q}, 96, "bell"))
        m = WEB_RE.match(q)
        if m:
            items.append(item("web", f"Найти в интернете: {m.group(1)}", "Браузер", {"type": "web", "query": m.group(1)},
                              95, "globe"))
        if URL_RE.match(q):
            url = q if "://" in q else "https://" + q
            items.append(item("url", url, "Открыть сайт", {"type": "open", "target": url}, 94, "link", f"url:{url}"))
        for stem, (title, target) in SETTINGS_PAGES.items():
            if stem in low:
                items.append(item("settings", title, "Параметры Windows", {"type": "open", "target": target}, 72,
                                  "settings", f"settings:{target}"))
        for stem, (title, target) in SYSTEM.items():
            if stem in low:
                items.append(item("system", title, "Система", {"type": "system" if target == "lock" else "open",
                                                              "target": target}, 74, "system", f"system:{target}"))
        items += await self.apps(q)
        items.append(item("ask", f"Спросить Эйдос: {q}", "Агент выполнит или ответит", {"type": "ask", "text": q}, 40,
                          "spark", "ask"))
        items.append(item("web", f"Искать «{q}» в интернете", "Браузер", {"type": "web", "query": q}, 20, "globe",
                          "web"))
        return self.rank(items)

    # ------------------------------------------------------------------ run
    async def run(self, action: dict, key: str = "") -> dict:
        kind = (action or {}).get("type", "")
        self.remember(key)
        if kind == "copy":
            text = str(action.get("text", ""))
            if config.IS_WINDOWS:
                import asyncio

                from .. import win

                await asyncio.to_thread(win.set_clipboard, text)
            return {"ok": True, "copied": text}
        if kind == "timer":
            from ..tools import island

            secs = island.parse_duration(str(action.get("duration", "")))
            if not secs:
                return {"ok": False, "reason": "не понял длительность"}
            timer = island.start_timer(secs, "Таймер", self.app)
            return {"ok": True, "timer": timer["id"]}
        if kind == "ask":
            text = str(action.get("text", "")).strip()
            if text:
                self.history = [h for h in self.history if h != text][-30:] + [text]
                bus.emit("commandbar.ask", {"text": text})
                self.app.background(self.app.agent.ask(text, source="commandbar"), "commandbar-ask")
            return {"ok": True, "asked": text}
        if kind == "web":
            base = self.settings.get("browser.search_url", "https://duckduckgo.com/?q=") or "https://duckduckgo.com/?q="
            return await self._open(base + urllib.parse.quote_plus(str(action.get("query", ""))))
        if kind == "open":
            return await self._open(str(action.get("target", "")))
        if kind == "launch":
            from ..tools import apps

            return {"ok": True, "text": await apps.launch(str(action.get("name", "")))}
        if kind == "system" and action.get("target") == "lock" and config.IS_WINDOWS:
            import asyncio

            from .. import win

            await asyncio.to_thread(win.lock)
            return {"ok": True}
        return {"ok": False, "reason": f"неизвестное действие {kind!r}"}

    async def _open(self, target: str) -> dict:
        if not config.IS_WINDOWS:
            return {"ok": False, "reason": "открытие работает только в Windows", "target": target}
        import asyncio

        from ..tools import apps

        await asyncio.to_thread(apps.start, target)
        return {"ok": True, "target": target}

    def methods(self) -> dict:
        async def query(text: str = "") -> list:
            return await self.query(text)

        async def run(action: dict | None = None, key: str = "") -> dict:
            return await self.run(action or {}, key)

        async def open_bar() -> dict:
            bus.emit("commandbar.open", {})
            return {"ok": True}

        return {"commandbar.query": query, "commandbar.run": run, "commandbar.open": open_bar}


def create(app) -> CommandBar:
    return CommandBar(app)
