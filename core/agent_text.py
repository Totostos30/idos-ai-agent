"""Чистые функции агента: распознавание команд, очистка ответа и подготовка текста для голоса.

Вынесено из `agent.py`, чтобы цикл агента оставался читаемым. Модуль не зависит от App,
базы и сети — только регулярные выражения и строки.
"""
from __future__ import annotations

import re
from typing import Any

from .tools import META_TOOL
from .util import clip

TOOL_ALIASES = {
    "browser": "web", "browser_search": "web", "search": "web", "web_search": "web", "websearch": "web",
    "internet": "web", "google": "web", "yandex": "web", "open_url": "web", "fetch": "web",
    "python": "shell", "code_interpreter": "shell", "terminal": "shell", "powershell": "shell", "cmd": "shell",
    "window": "windows", "app": "apps", "file": "files", "remember": "memory",
}


def missing_tool_name(message: str, known: Any) -> str | None:
    """Name of a registered tool from a provider error like "attempted to call tool 'web' which was not in
    request.tools". Handles aliases and namespaced names (browser.search, web_search, functions.web)."""
    m = re.search(r"(?:call(?:ed)?|use[ds]?)\s+(?:the\s+)?(?:tool|function)\s+['\"`]([^'\"`]+)['\"`]", message or "", re.I)
    if not m:
        return None
    raw = m.group(1).strip().lower()
    if raw.startswith("functions."):
        raw = raw[len("functions."):]
    for cand in (raw, raw.split(".")[0], raw.split("_")[0], raw.split("__")[0]):
        cand = TOOL_ALIASES.get(cand, cand)
        if cand in known and cand != META_TOOL:
            return cand
    return None


STOP_RE = re.compile(r"^\s*(стоп|хватит|отмена|отмени|остановись|прекрати|stop|cancel)[\s.!]*$", re.I)
_NAMES = (r"(?:gemini|джемини|клод|claude|gpt|гпт|qwen|квен|deepseek|дипсик|mistral|мистрал|openai|чатгпт|"
          r"ollama|церебрас|cerebras|grok|грок)")
USER_MODEL_RE = re.compile(r"модел|нейросет|нейронк|" + _NAMES + r"|платн|\bllm\b", re.I)


def user_asked_model(text: str) -> bool:
    """Model changes are allowed only on the user's own initiative."""
    return bool(USER_MODEL_RE.search(text or ""))


MODEL_SWITCH_RE = re.compile(
    r"^\s*(?:(?:эйдос|айдос|идос|idos)[,\s]+)?(?:пожалуйста[,\s]+)?(?:"
    r"(?:переключи(?:сь)?|смени|поменяй)\b.{0,30}?(?:модел|" + _NAMES + r"|платн|быстр|стандарт|основн|локальн|умн)"
    r"|(?:включи|используй|выбери|поставь)\b.{0,30}?(?:модел|" + _NAMES + r"))", re.I)


# The model must not imitate the action journal: actions happen only through real tool calls.
FAKE_ACTIONS_RE = re.compile(r"\s*\[\s*(?:Действи[ея]|Actions?)\s*:[^\]\n]*\]?", re.I)
VERIFY_TOOLS = {"computer", "apps", "windows"}
FAKE_CALL_RE = re.compile(r"\b[a-z_]{2,20}\.[a-z_]{2,20}\b[^\n]{0,200}?→\s*(?:ok|ошибка)\b", re.I)
_LEAD = r"^\s*(?:(?:эйдос|айдос|идос|idos)[,\s]+)?(?:пожалуйста[,\s]+)?"
REMEMBER_RE = re.compile(_LEAD + r"(?:запомни|запиши в память|сохрани в память|заруби на носу)\b[,:\s]*(?:что\s+)?(.+)$", re.I | re.S)
COMMAND_RE = re.compile(_LEAD + r"(?:запомни|забудь|открой|запусти|включи|выключи|поставь|установи|удали|отправь|создай|"
                        r"закрой|нажми|скачай|сохрани|смени|поменяй|сделай|убавь|прибавь|сверни|разверни|напомни|"
                        r"вруби|выруби|переименуй|перемести|скопируй|вставь|напечатай|запиши)\b", re.I)
CLAIM_RE = re.compile(r"^\W*(?:запомнил|сохранил|записал|забыл|открыл|запустил|включил|выключил|поставил|установил|удалил|"
                      r"отправил|создал|переключил|сделал|выполнил|закрыл|нажал|напечатал|скачал|сменил|готово|сделано|"
                      r"включаю|открываю|запускаю|запоминаю)", re.I)


def clean_reply(text: str) -> str:
    """Remove action journals the model may have written into its answer by itself."""
    text = FAKE_ACTIONS_RE.sub("", text or "")
    return "\n".join(line for line in text.splitlines() if not FAKE_CALL_RE.search(line) or len(line) > 300).strip()


def real_actions(run: Any) -> list[str]:
    return [a for a in run.actions if not a.startswith("tools ")]


def speech_part(text: str, limit: int = 700) -> str:
    """First paragraph of the answer, cleaned for text-to-speech."""
    text = (text or "").strip()
    if not text:
        return ""
    first = re.split(r"\n\s*\n", text, maxsplit=1)[0]
    first = " ".join(line.strip() for line in first.splitlines() if line.strip())
    first = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", first)
    first = re.sub(r"https?://\S+", "", first)
    first = re.sub(r"[*_`#>|]+", "", first)
    first = re.sub(r"^[-•\d.)\s]+", "", first)
    first = " ".join(first.split())
    if len(first) > limit:
        cut = first[:limit]
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        first = cut[:end + 1] if end > limit * 0.4 else cut.rsplit(" ", 1)[0] + "…"
    return first


def short_args(args: dict, limit: int = 90) -> str:
    parts = [f"{k}={clip(' '.join(str(v).split()), 32)}" for k, v in args.items() if k != "action" and v not in (None, "")]
    return clip(", ".join(parts), limit)


def public_args(args: dict) -> dict:
    return {k: (clip(str(v), 200) if isinstance(v, str) else v) for k, v in args.items()}
