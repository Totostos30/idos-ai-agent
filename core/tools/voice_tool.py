"""Голос Эйдоса: какие есть голоса, смена голоса и скорости речи по просьбе пользователя."""
from __future__ import annotations

import asyncio
from typing import Any

from . import A, N, S, Tool, ToolError, get_float, get_str

ENGINES = ("silero", "off")
GENDER = {"м": "мужской", "ж": "женский"}


def _tts(ctx: Any):
    tts = getattr(getattr(ctx, "app", None), "tts", None)
    if tts is None:
        raise ToolError("голос сейчас недоступен")
    return tts


def _norm(text: str) -> str:
    return str(text or "").strip().lower().replace("ё", "е")


def resolve(items: list[dict], engine: str, voice: str) -> tuple[str, str]:
    """Find (engine, voice id) by id or Russian name: 'Айдар', 'aidar', 'Байя'…"""
    engine, wanted = _norm(engine), _norm(voice)
    if engine and engine not in ENGINES:
        raise ToolError("доступен только Silero: engine = silero или off")
    if engine == "off" or not wanted:
        return engine, ""
    for item in items:
        if engine and item["engine"] != engine:
            continue
        for v in item["voices"]:
            vid, name = _norm(v["id"]), _norm(v["name"])
            if wanted in (vid, name) or (len(wanted) > 3 and (wanted in name or wanted in vid)):
                return item["engine"], v["id"]
    known = ", ".join(f"{i['engine']}: " + " / ".join(v["name"] for v in i["voices"]) for i in items if i["voices"])
    raise ToolError(f"не знаю голос «{voice}». Есть — {known}")


def describe(items: list[dict], current_engine: str, current: dict) -> str:
    lines = []
    for item in items:
        chosen = current.get(item["setting"].split(".", 1)[1], "")
        head = f"{item['engine']} — {item['title']} ({item['note']})"
        if item["engine"] == current_engine:
            head += " [ВКЛЮЧЁН]"
        if not item["available"]:
            head += f" — недоступен: {item['reason']}"
        voices = [f"{v['id']} ({v['name']}, {GENDER.get(v['gender'], 'голос')})" + (" ← выбран" if v["id"] == chosen else "")
                  for v in item["voices"]]
        lines.append(head + ": " + (", ".join(voices) or "голос по умолчанию"))
    return "\n".join(lines)


async def handler(action: str, args: dict, ctx: Any) -> Any:
    from ..tts.engines import SETTING_FOR, catalog

    tts = _tts(ctx)
    settings = tts.settings
    if action == "list":
        items = await asyncio.to_thread(catalog, settings)
        current = dict((k, settings.get("voice." + k, "")) for k in SETTING_FOR.values())
        return describe(items, str(settings.get("voice.tts_engine", "silero")), current) + (
            f"\nскорость речи: {settings.get('voice.rate', 1.0)}")
    if action == "set":
        items = await asyncio.to_thread(catalog, settings)
        engine, voice = resolve(items, get_str(args, "engine"), get_str(args, "voice"))
        rate = get_float(args, "rate")
        if not engine and not voice and rate is None:
            raise ToolError("укажи voice (имя голоса), engine или rate")
        item = next((i for i in items if i["engine"] == engine), None)
        if item is not None and not item["available"]:
            raise ToolError(f"{item['title']} недоступен: {item['reason']}. Можно установить: action=install, engine={engine}"
                            if item["installable"] else f"{item['title']} недоступен: {item['reason']}")
        result = await tts.apply(engine, voice, rate, sample=False)
        if engine == "off":
            return "голосовые ответы выключены"
        parts = [f"движок {result['engine']}"]
        if voice:
            parts.append(f"голос {voice}")
        if rate is not None:
            parts.append(f"скорость {result['values'].get('voice.rate')}")
        return "готово: " + ", ".join(parts) + ". Ответь пользователю коротко — он сразу услышит новый голос."
    if action == "test":
        await tts.say(get_str(args, "text") or "Проверка голоса. Раз, два, три.", wait=True)
        return f"сказал фразу голосом {tts.last_engine or 'по умолчанию'}"
    if action in ("teach", "forget", "dictionary"):
        from ..tts.lexicon import parse_lexicon

        lex = parse_lexicon(settings.get("voice.lexicon") or {})
        if action == "dictionary":
            return "\n".join(f"{k} = {v}" for k, v in lex.items()) or "словарь произношения пуст"
        word = get_str(args, "word").strip()
        if not word:
            raise ToolError("укажи word — слово или сокращение")
        if action == "teach":
            say = get_str(args, "say").strip()
            if not say:
                raise ToolError("укажи say — как это произносить")
            lex = dict((k, v) for k, v in lex.items() if k.lower() != word.lower())
            lex[word] = say
            settings.set("voice.lexicon", lex)
            return f"запомнил: «{word}» читаю как «{say}»"
        kept = dict((k, v) for k, v in lex.items() if k.lower() != word.lower())
        settings.set("voice.lexicon", kept)
        return f"забыл «{word}»" if len(kept) < len(lex) else f"«{word}» не было в словаре"
    if action == "preview":
        from ..tts.normalize import normalize

        return "прочитаю так: " + normalize(get_str(args, "text"), 10_000, True, settings.get("voice.lexicon") or None)
    raise ToolError(f"неизвестное действие {action}")


ACTIONS = {
    "list": A("Смотрю, какие есть голоса", "read"), "set": A("Меняю голос {voice}"),
    "test": A("Проверяю голос", "read"), "teach": A("Запоминаю, как читать «{word}»"),
    "forget": A("Убираю «{word}» из словаря"), "dictionary": A("Смотрю словарь произношения", "read"),
    "preview": A("Проверяю, как прочитаю", "read"),
}
PARAMS = {
    "engine": S("set: silero | off (off — не говорить вслух)"),
    "voice": S("set: id или имя голоса Silero (xenia, baya, kseniya, aidar, eugene или Ксения, Байя, Айдар, Евгений)"),
    "rate": N("set: скорость речи 0.5–2 (1 — обычная)"), "text": S("test/preview: что сказать"),
    "word": S("teach/forget: слово, сокращение или аббревиатура как оно пишется (ГПУ, Kubernetes)"),
    "say": S("teach: как произносить русскими буквами (видеокарта, кубернетес)"),
}


def register(reg: Any, app: Any) -> None:
    reg.add(Tool(
        "voice",
        "Голос Эйдоса (только Silero): list (голоса), set (сменить голос / скорость / выключить; мужской или женский "
        "голос выбирай по list; «говори быстрее/медленнее» — rate ±0.2 от текущей), test, "
        "teach («произноси X как Y», «ты неправильно читаешь X» — запомнить произношение), forget, dictionary, "
        "preview (показать, как текст будет прочитан).",
        ACTIONS, PARAMS, handler, timeout=900))
