"""Tool registry: schemas for the model, dispatch, confirmation policy and pill labels.

Every tool takes an ``action`` plus a few optional arguments. Keeping the number
of tools small (one schema per area) keeps the prompt cheap, which matters with
Groq's 8K tokens-per-minute free tier.
"""
from __future__ import annotations

import asyncio
import functools
import importlib
import inspect
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from ..util import clip

log = logging.getLogger("tools")

MODULES = ["computer", "windows", "apps", "files", "shell", "browser", "web", "memory_tool", "system", "island",
           "model", "skill", "mcp_tool", "document", "voice_tool", "scenario"]


class ToolError(Exception):
    """Expected failure; the message is shown to the model."""


class Aborted(Exception):
    """Stopped by the user (kill switch, failsafe corner, cancel)."""


@dataclass
class Result:
    text: str
    ok: bool = True
    data: Any = None
    image: str | None = None  # preview for the UI only, never sent to the text model


@dataclass
class Context:
    app: Any = None
    chat_id: str = ""
    cancel: asyncio.Event | None = None
    step_id: str = ""
    request: str = ""

    @property
    def cancelled(self) -> bool:
        return self.cancel is not None and self.cancel.is_set()

    def check(self) -> None:
        if self.cancelled:
            raise Aborted("остановлено пользователем")


@dataclass
class Action:
    label: str
    kind: str = "act"  # read | act | danger


def A(label: str, kind: str = "act") -> Action:  # noqa: N802 - short constructor used in tool tables
    return Action(label, kind)


class SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


@dataclass
class Tool:
    name: str
    description: str
    actions: dict[str, Action]
    params: dict[str, dict] = field(default_factory=dict)
    handler: Callable[[str, dict, Context], Any] | None = None
    available: Callable[[], bool] = lambda: True
    timeout: float = 120.0
    thread: Any = None  # util.Worker for sync handlers that need one dedicated thread (COM/UIA)

    def schema(self) -> dict:
        props: dict[str, Any] = {"action": {"type": "string", "enum": list(self.actions)}}
        props.update(self.params)
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": props, "required": ["action"]}}}


# --- schema helpers --------------------------------------------------------------------------------

def S(desc: str) -> dict:  # noqa: N802
    return {"type": "string", "description": desc}


def N(desc: str) -> dict:  # noqa: N802
    return {"type": "number", "description": desc}


def I(desc: str) -> dict:  # noqa: N802,E743
    return {"type": "integer", "description": desc}


def B(desc: str) -> dict:  # noqa: N802
    return {"type": "boolean", "description": desc}


# --- argument helpers ------------------------------------------------------------------------------

def get_str(args: dict, key: str, default: str = "") -> str:
    value = args.get(key)
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value)
    return str(value)


def need(args: dict, key: str, hint: str = "") -> str:
    value = get_str(args, key).strip()
    if not value:
        raise ToolError(f"нужен параметр {key}" + (f" ({hint})" if hint else ""))
    return value


def get_float(args: dict, key: str, default: float | None = None) -> float | None:
    value = args.get(key)
    if value is None or value == "":
        return default
    try:
        return float(str(value).replace(",", "."))
    except ValueError as exc:
        raise ToolError(f"параметр {key} должен быть числом") from exc


def get_int(args: dict, key: str, default: int | None = None) -> int | None:
    value = get_float(args, key, None)
    return default if value is None else int(round(value))


def get_bool(args: dict, key: str, default: bool = False) -> bool:
    value = args.get(key)
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on", "да", "вкл", "включить")


def short_value(value: Any, limit: int = 48) -> str:
    """Pill-friendly value: file name instead of a full path, host instead of a long URL."""
    text = " ".join(str(value).split())
    if text.startswith(("http://", "https://")):
        host = text.split("/")[2]
        return host[4:] if host.startswith("www.") else host
    looks_like_path = bool(re.match(r"^([a-zA-Z]:[\\/]|\\\\|/|~|%)", text)) or (" " not in text and ("\\" in text or "/" in text))
    if looks_like_path and len(text) > 24 and "\n" not in str(value):
        name = text.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        if name:
            return clip(name, limit)
    return clip(text, limit)


# --- registry ----------------------------------------------------------------------------------------

class Registry:
    def __init__(self) -> None:
        self.tools: dict[str, Tool] = {}

    def add(self, tool: Tool) -> Tool:
        self.tools[tool.name] = tool
        return tool

    def names(self) -> list[str]:
        return list(self.tools)

    def schemas(self) -> list[dict]:
        out = []
        for tool in self.tools.values():
            try:
                if tool.available():
                    out.append(tool.schema())
            except Exception:  # noqa: BLE001
                log.debug("availability check failed for %s", tool.name, exc_info=True)
        return out

    def select(self, text: str, sticky: Any = (), limit_tokens: int = 2500) -> list[dict]:
        """Schemas for one agent step. Tools from `sticky` and tools whose HINTS occur in `text` always go in; the rest
        follow PRIORITY while they fit into `limit_tokens`; whatever is left is offered through the META_TOOL."""
        available = {}
        for name, tool in self.tools.items():
            try:
                if tool.available():
                    available[name] = tool
            except Exception:  # noqa: BLE001
                log.debug("availability check failed for %s", name, exc_info=True)
        low = " " + " ".join(str(text).lower().replace("ё", "е").split()) + " "
        wanted = [n for n in sticky if n in available]
        wanted += [n for n, hints in HINTS.items() if n in available and any(hint_in(h, low) for h in hints)]
        wanted = list(dict.fromkeys(wanted))
        out, picked, used = [], set(), 0
        for name in dict.fromkeys(wanted + [n for n in PRIORITY if n in available] + list(available)):
            schema = available[name].schema()
            cost = len(json.dumps(schema, ensure_ascii=False)) // 3 + 10
            if name in wanted or used + cost <= limit_tokens:
                out.append(schema)
                picked.add(name)
                used += cost
        rest = [n for n in available if n not in picked]
        if rest:
            out.append(self.meta_schema(rest))
        return out

    def meta_schema(self, names: list[str]) -> dict:
        listing = "; ".join(f"{n} — {SUMMARY.get(n) or self.tools[n].description[:60]}" for n in names)
        return {"type": "function", "function": {
            "name": META_TOOL,
            "description": "Подключить инструменты, которых нет в списке, — они появятся на следующем шаге: " + listing,
            "parameters": {"type": "object", "properties": {
                "names": {"type": "array", "items": {"type": "string"}, "description": "имена инструментов"}},
                "required": ["names"]}}}

    def kind(self, name: str, action: str) -> str:
        tool = self.tools.get(name)
        act = tool.actions.get(action) if tool else None
        return act.kind if act else "act"

    def label(self, name: str, action: str, args: dict) -> str:
        tool = self.tools.get(name)
        act = tool.actions.get(action) if tool else None
        if act is None:
            return f"{name}: {action}".strip(": ")
        values = SafeDict({k: short_value(v) for k, v in (args or {}).items() if v is not None})
        try:
            text = act.label.format_map(values)
        except (ValueError, IndexError, AttributeError):
            text = act.label
        text = text.replace("«»", "").replace("()", "")
        return " ".join(text.split()).strip(" :—-") or name

    def needs_confirm(self, name: str, action: str, autonomy: str) -> bool:
        kind = self.kind(name, action)
        if autonomy == "all":
            return kind != "read"
        if autonomy == "danger":
            return kind == "danger"
        return False

    async def run(self, name: str, args: dict, ctx: Context) -> Result:
        tool = self.tools.get(name)
        if tool is None:
            return Result(f"Ошибка: нет инструмента «{name}». Доступны: {', '.join(self.tools)}", ok=False)
        action = get_str(args, "action").strip().lower()
        if action not in tool.actions:
            if not action and len(tool.actions) == 1:
                action = next(iter(tool.actions))
            else:
                return Result(f"Ошибка: у {name} нет действия «{action}». Доступны: {', '.join(tool.actions)}",
                              ok=False)
        ctx.check()
        try:
            out = await self._call(tool, action, args, ctx)
        except Aborted:
            raise
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            return Result(f"Ошибка: тайм-аут {int(tool.timeout)} с", ok=False)
        except ToolError as exc:
            return Result(f"Ошибка: {exc}", ok=False)
        except (OSError, ValueError, KeyError, LookupError) as exc:
            return Result(f"Ошибка: {exc}", ok=False)
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "FailsafeError":  # cursor thrown into the top-left corner = emergency stop
                raise Aborted(str(exc)) from exc
            log.exception("tool %s.%s failed", name, action)
            return Result(f"Ошибка {type(exc).__name__}: {exc}", ok=False)
        if isinstance(out, Result):
            return out
        if out is None:
            return Result("готово")
        if isinstance(out, (dict, list)):
            return Result(json.dumps(out, ensure_ascii=False, default=str))
        return Result(str(out))

    async def _call(self, tool: Tool, action: str, args: dict, ctx: Context) -> Any:
        fn = tool.handler
        if fn is None:
            raise ToolError("инструмент не настроен")
        if inspect.iscoroutinefunction(fn):
            return await asyncio.wait_for(fn(action, args, ctx), tool.timeout)
        if tool.thread is not None:
            return await asyncio.wait_for(tool.thread.run(lambda: fn(action, args, ctx)), tool.timeout)
        return await asyncio.wait_for(asyncio.to_thread(fn, action, args, ctx), tool.timeout)


META_TOOL = "tools"
PRIORITY = ["computer", "apps", "windows", "web", "files", "system", "island", "voice", "memory", "browser", "shell", "document",
            "model", "skill", "mcp"]
SUMMARY = {
    "computer": "мышь, клавиатура, снимок и распознавание экрана", "windows": "окна и их элементы: кнопки, поля, меню",
    "apps": "запуск и закрытие программ, открытие файлов и ссылок", "files": "файлы и папки",
    "document": "чтение PDF, DOCX, XLSX, PPTX", "shell": "команды PowerShell", "browser": "свой браузер: сайты, формы, вход",
    "web": "поиск в интернете и чтение страниц", "memory": "долговременная память о пользователе",
    "system": "громкость, яркость, питание, буфер обмена, Wi-Fi, Bluetooth, параметры Windows",
    "island": "карточки и таймеры на острове", "voice": "голос Эйдоса: выбор голоса и скорости речи", "model": "модели, ключи и лимиты API", "skill": "навыки-инструкции",
    "mcp": "внешние MCP-серверы",
}
# Lower-case stems (ё written as е) that make a tool part of the step even when it does not fit the budget.
SHORT_HINT = 4
_PREFIXES = r"(?:пере|при|раз|рас|по|за|на|вы|у|с|о)?"


@functools.lru_cache(maxsize=2048)
def _hint_re(hint: str) -> re.Pattern:
    return re.compile(r"(?<![0-9a-zа-я])" + _PREFIXES + re.escape(hint))


def hint_in(hint: str, low: str) -> bool:
    """Short hints (<= 4 chars: "сон", "диск", "темн") match only at the start of a word, optionally after a common
    prefix ("потемнее"), so "персонал" no longer pulls the system tool in. Longer hints ("громч" in "погромче")
    and hints that start with a non-letter (".txt") stay plain substrings."""
    if len(hint.strip()) > SHORT_HINT or not hint[:1].isalnum():
        return hint in low
    return _hint_re(hint).search(low) is not None


HINTS: dict[str, tuple[str, ...]] = {
    "computer": ("экран", "нажми", "нажать", "нажмите", "кликни", "клик", "мыш", "курсор", "напечатай", "введи", "впиши",
                 "клавиш", "сочетани", "скриншот", "снимок", "прокрут", "перетащ", "раскладк", "click", "screen"),
    "windows": ("окно", "окна", "окне", "окон", "сверни", "разверни", "на весь экран", "переключись на", "кнопк",
                "поле ", "меню", "window"),
    "apps": ("запусти", "открой", "закрой", "приложени", "программ", "процесс", "launch", "open"),
    "files": ("файл", "папк", "рабочий стол", "рабочем столе", "загрузки", "сохрани", "переименуй", "удали",
              "скопируй", "перемести", ".txt", ".docx", ".pdf", ".xlsx", "file", "folder"),
    "document": ("документ", "pdf", "docx", "xlsx", "pptx", "таблиц", "презентаци"),
    "shell": ("команд", "powershell", "терминал", "консол", "cmd", "скрипт", "winget", "pip ", "git ", "python", "реестр"),
    "browser": ("браузер", "сайт", "страниц", "вкладк", "войди", "логин", "авториз", "ссылк", "youtube", "ютуб", "http",
                "browser"),
    "web": ("интернет", "найди", "поищи", "погугли", "новост", "погод", "курс ", "что такое", "кто такой", "сколько стоит",
            "узнай", "актуальн", "search"),
    "memory": ("запомни", "помнишь", "забудь", "меня зовут", "предпочита", "remember"),
    "system": ("громк", "громч", "тише", "звук", "яркост", "выключи компьютер", "выключи пк", "перезагр", "спящ", "сон",
               "заблокируй", "буфер", "блютуз", "bluetooth", "wi-fi", "wifi", "вайфай", "сеть", "темн", "светл",
               "процессор", "оперативн", "диск ", "диска", "диске", "диску", "дисков", "батаре", "заряд", "параметры windows", "настройки windows",
               "корзин", "трек", "пауз", "музык", "volume"),
    "island": ("таймер", "засеки", "напомни", "напомин", "заметк", "карточк", "остров", "timer"),
    "voice": ("голос", "говори ", "озвуч", "скорость речи", "мужск", "женск", "silero", "быстрее", "медленнее", "voice"),
    "model": ("модел", "gpt", "llama", "qwen", "платн", "groq", "cerebras", "openrouter", "ключ", "лимит", "model"),
    "skill": ("навык", "скилл", "skill", "научись"),
    "mcp": ("mcp", "интеграци"),
}


def build_registry(app: Any = None, modules: list[str] | None = None) -> Registry:
    """Import tool modules; a module with missing optional dependencies is skipped, not fatal."""
    reg = Registry()
    for name in modules or MODULES:
        try:
            module = importlib.import_module(f"{__name__}.{name}")
            module.register(reg, app)
        except Exception as exc:  # noqa: BLE001
            log.warning("tool module %s unavailable: %s", name, exc)
    return reg


# places, "nearest ...", addresses -> web search; "my home/address/city" -> memory with the saved location
HINTS["web"] = HINTS.get("web", ()) + ("ближайш", "рядом", "поблизости", "неподал", "где находит", "где купить", "адрес ",
                                        "как добраться", "маршрут", "часы работы", "открыт ли", "открыто ли", "до скольки", "булочн", "пекарн", "кафе",
                                        "ресторан", "аптек", "магазин", "супермаркет", "отзывы", "near me", "nearby")
HINTS["memory"] = HINTS.get("memory", ()) + ("моему дому", "моего дома", "мой дом", "у меня дома", "от дома", "мой адрес",
                                              "моем городе", "моём городе", "где я живу", "мой город")
