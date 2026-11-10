"""Provider catalogue, model aliases and rate-limit header parsing used by the router."""
from __future__ import annotations

import email.utils
import fnmatch
import json
import re
import time
from datetime import datetime
from typing import Any

# Every provider speaks the OpenAI chat-completions protocol (Anthropic and Gemini via their compatibility endpoints).
PROVIDERS: dict[str, dict[str, Any]] = {
    "groq": {
        "name": "Groq", "base": "https://api.groq.com/openai/v1", "keys_url": "https://console.groq.com/keys",
        "free": True, "max_tokens_param": "max_completion_tokens", "input_budget": 6400,
        "models": ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"],
        "vision": ["qwen/qwen3.8-27b"], "stt": "whisper-large-v3-turbo",
    },
    # OpenAI-compatible gateway; the free models cost x0 but the balance must not be empty.
    "anymodel": {
        "name": "AnyModel", "base": "https://anymodel.org/v1", "keys_url": "https://anymodel.org/app",
        "free": True, "input_budget": 20000, "balance_path": "/balance",
        "models": ["am/nemotron-3-ultra-550b-a55b", "am/nemotron-3-nano-omni-30b-a3b-reasoning"],
        "vision": ["am/nemotron-3-nano-omni-30b-a3b-reasoning"],
        "note": ("Один аккаунт и один ключ: несколько аккаунтов для обхода лимитов запрещены правилами AnyModel. "
                 "Включите для ключа Ghost Mode, чтобы запросы и скриншоты не сохранялись в истории. "
                 "Бесплатные модели работают, только если на балансе есть хоть немного токенов."),
    },
    "cerebras": {
        "name": "Cerebras", "base": "https://api.cerebras.ai/v1", "keys_url": "https://cloud.cerebras.ai",
        "free": True, "max_tokens_param": "max_completion_tokens", "input_budget": 24000,
        "models": ["gpt-oss-120b", "qwen-3.8-27b"],
    },
    "openrouter": {
        "name": "OpenRouter", "base": "https://openrouter.ai/api/v1", "keys_url": "https://openrouter.ai/settings/keys",
        "free": True, "headers": {"HTTP-Referer": "https://localhost/idos", "X-Title": "IDOS"},
        "models": ["openai/gpt-oss-120b:free"],
    },
    "gemini": {
        "name": "Google Gemini", "base": "https://generativelanguage.googleapis.com/v1beta/openai",
        "keys_url": "https://aistudio.google.com/apikey", "free": True,
        "models": ["gemini-2.5-flash"], "vision": ["gemini-2.5-flash"],
    },
    "mistral": {
        "name": "Mistral", "base": "https://api.mistral.ai/v1", "keys_url": "https://console.mistral.ai/api-keys",
        "paid": True, "models": ["mistral-large-latest", "mistral-medium-latest"], "vision": ["mistral-medium-latest"],
    },
    "openai": {
        "name": "OpenAI", "base": "https://api.openai.com/v1", "keys_url": "https://platform.openai.com/api-keys",
        "paid": True, "max_tokens_param": "max_completion_tokens", "models": ["gpt-5.1", "gpt-5-mini"],
        "vision": ["gpt-5.1", "gpt-5-mini"],
    },
    "anthropic": {
        "name": "Anthropic", "base": "https://api.anthropic.com/v1",
        "keys_url": "https://console.anthropic.com/settings/keys", "paid": True,
        "models": ["claude-sonnet-4-5", "claude-haiku-4-5"], "vision": ["claude-sonnet-4-5", "claude-haiku-4-5"],
    },
    "deepseek": {
        "name": "DeepSeek", "base": "https://api.deepseek.com/v1", "keys_url": "https://platform.deepseek.com/api_keys",
        "paid": True, "models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "xai": {
        "name": "xAI", "base": "https://api.x.ai/v1", "keys_url": "https://console.x.ai", "paid": True,
        "models": ["grok-4", "grok-4-fast"], "vision": ["grok-4"],
    },
    "ollama": {"name": "Ollama", "base": "http://127.0.0.1:11434/v1", "keys_url": "https://ollama.com/download",
               "no_key": True, "models": []},
    "lmstudio": {"name": "LM Studio", "base": "http://127.0.0.1:1234/v1", "keys_url": "https://lmstudio.ai",
                 "no_key": True, "models": []},
}

PAID_ORDER = ["openai", "anthropic", "mistral", "deepseek", "xai"]
PAID_WORDS = ("платн", "paid", "премиум", "дорог", "самую умн", "самая умн", "мощн")

# Order matters: the first entry whose word occurs in the request wins ("120b" must precede "20b").
ALIASES: list[tuple[tuple[str, ...], str]] = [
    (("омни", "omni", "немотрон нано", "nemotron nano"), "anymodel:am/nemotron-3-nano-omni-30b-a3b-reasoning"),
    (("немотрон", "nemotron", "ультра", "ultra", "anymodel", "энимодел", "эни модел"), "anymodel:am/nemotron-3-ultra-550b-a55b"),
    (("120b",), "groq:openai/gpt-oss-120b"),
    (("20b", "быстр", "fast", "легк", "лайт"), "groq:openai/gpt-oss-20b"),
    (("квен", "qwen", "зрени", "vision"), "groq:qwen/qwen3.8-27b"),
    (("церебрас", "cerebras"), "cerebras:gpt-oss-120b"),
    (("джемини", "гемини", "gemini"), "gemini:gemini-2.5-flash"),
    (("клод", "claude", "anthropic", "антропик"), "anthropic:"),
    (("openai", "опенаи", "оупенаи", "чатгпт", "chatgpt", "gpt-5", "гпт-5", "гпт 5"), "openai:"),
    (("мистрал", "mistral"), "mistral:mistral-large-latest"),
    (("дипсик", "deepseek"), "deepseek:deepseek-chat"),
    (("грок", "grok", "xai"), "xai:"),
    (("lm studio", "lmstudio"), "lmstudio:"),
    (("локальн", "ollama", "оллам"), "ollama:"),
    (("gpt", "гпт", "стандарт", "основн", "обычн", "по умолчанию", "дефолт"), "groq:openai/gpt-oss-120b"),
]

_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|h|m|s)")


def split_spec(spec: str) -> tuple[str, str]:
    provider, _, model = (spec or "").partition(":")
    return provider.strip().lower(), model.strip()


def alias_spec(query: str) -> str | None:
    low = (query or "").lower().replace("ё", "е")
    for words, spec in ALIASES:
        if any(word in low for word in words):
            return spec
    return None


def wants_paid(query: str) -> bool:
    low = (query or "").lower().replace("ё", "е")
    return any(word in low for word in PAID_WORDS)


def parse_duration(value: Any) -> float | None:
    """'2m59.56s' -> 179.56, '7.66s' -> 7.66, '120ms' -> 0.12, '15' -> 15.0."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    found = _DURATION_RE.findall(text)
    if not found:
        return None
    scale = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
    return sum(float(number) * scale[unit] for number, unit in found)


def parse_reset(value: Any, now: float | None = None) -> float | None:
    """Seconds until a limit resets. Accepts durations, plain seconds, epoch seconds/ms and ISO timestamps."""
    if value is None or not str(value).strip():
        return None
    now = time.time() if now is None else now
    text = str(value).strip()
    if "-" in text and ("t" in text.lower() or ":" in text):
        try:
            return max(0.0, datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() - now)
        except ValueError:
            return None
    try:
        number = float(text)
    except ValueError:
        return parse_duration(text)
    if number > 1e12:
        return max(0.0, number / 1000.0 - now)
    if number > 1e9:
        return max(0.0, number - now)
    return max(0.0, number)


def parse_retry_after(headers: dict | None) -> float | None:
    value = (headers or {}).get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        return max(0.0, email.utils.parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return None



LIMIT_NAMES = {"TPD": "дневной лимит токенов", "RPD": "дневной лимит запросов",
               "TPM": "лимит токенов в минуту", "RPM": "лимит запросов в минуту"}
_KIND_RE = re.compile(r"\((RPM|RPD|TPM|TPD)\)", re.I)
_KIND_WORDS = (("tokens per day", "TPD"), ("requests per day", "RPD"),
               ("tokens per minute", "TPM"), ("requests per minute", "RPM"))
_AGAIN_RE = re.compile(r"try again in\s+([0-9hms.]+)", re.I)


def _limit_number(word: str, text: str) -> int | None:
    match = re.search(word + r"\s*:?\s*(\d[\d,]*)", text, re.I)
    return int(match.group(1).replace(",", "")) if match else None


def parse_rate_limit(message: str) -> dict:
    """Groq 429: 'on tokens per day (TPD): Limit 200000, Used 196543, Requested 7012. Please try again in 9m36.48s'."""
    text = message or ""
    kind = ""
    match = _KIND_RE.search(text)
    if match:
        kind = match.group(1).upper()
    else:
        low = text.lower()
        kind = next((code for words, code in _KIND_WORDS if words in low), "")
    again = _AGAIN_RE.search(text)
    return {"kind": kind, "limit": _limit_number("limit", text), "used": _limit_number("used", text),
            "requested": _limit_number("requested", text),
            "wait": parse_duration(again.group(1).rstrip(".")) if again else None}


def _thousands(value: int) -> str:
    return f"{int(value):,}".replace(",", " ")


def describe_rate_limit(info: dict) -> str:
    parts = [LIMIT_NAMES.get(info.get("kind") or "", "лимит запросов") + " (429)"]
    if info.get("used") is not None and info.get("limit"):
        parts.append(f"израсходовано {_thousands(info['used'])} из {_thousands(info['limit'])}")
    if info.get("requested"):
        parts.append(f"запрос {_thousands(info['requested'])}")
    return ", ".join(parts)


def human_seconds(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"{seconds} с"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} мин" + (f" {rest} с" if rest and minutes < 10 else "")
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours} ч" + (f" {minutes} мин" if minutes else "")
    return f"{hours // 24} дн"


def estimate_tokens(messages: list[dict] | None, tools: list[dict] | None = None) -> int:
    """Cheap estimate: characters / 3, +20 tokens per message; an image counts as ~2048 tokens."""
    chars = 0
    for message in messages or []:
        chars += 60
        content = message.get("content")
        if isinstance(content, str):
            chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                chars += 2048 * 3 if part.get("type") == "image_url" else len(str(part.get("text", "")))
        for call in message.get("tool_calls") or []:
            chars += len(json.dumps(call, ensure_ascii=False))
    if tools:
        chars += len(json.dumps(tools, ensure_ascii=False))
    return chars // 3 + 20


def params_for(table: dict | None, spec: str) -> dict:
    """Merge every params entry matching ``spec``: glob keys ("groq:qwen/*") or plain prefixes; longer keys win."""
    merged: dict = {}
    for key in sorted(table or {}, key=len):
        value = table[key]
        if not isinstance(value, dict):
            continue
        if (any(ch in key for ch in "*?[") and fnmatch.fnmatchcase(spec, key)) or spec.startswith(key.rstrip("*")):
            merged.update(value)
    return merged
