"""Long-term memory tool (facts about the user, preferences, agreements)."""
from __future__ import annotations

from typing import Any

from . import A, I, S, Tool, ToolError, get_int, get_str, need


def handler(action: str, args: dict, ctx: Any) -> str:
    memory = ctx.app.memory
    if action == "save":
        row = memory.save(need(args, "text"), get_str(args, "tags").strip(), source="agent")
        return f"запомнил (#{row['id']})" + (", такое уже было" if row.get("duplicate") else "")
    if action == "search":
        rows = memory.search(need(args, "query"), get_int(args, "limit", 6) or 6)
        return "\n".join(f"#{r['id']}: {r['text']}" for r in rows) or "ничего не нашёл"
    if action == "list":
        rows = memory.list(get_int(args, "limit", 30) or 30)
        lines = [f"#{r['id']}{' [' + r['tags'] + ']' if r['tags'] else ''}: {r['text']}" for r in rows]
        return f"всего записей: {memory.count()}\n" + "\n".join(lines)
    if action == "delete":
        mid = get_int(args, "id")
        if mid is None:
            rows = memory.search(need(args, "query", "укажите id или query"), 1)
            if not rows:
                raise ToolError("не нашёл такую запись")
            mid = rows[0]["id"]
        if not memory.delete(mid):
            raise ToolError(f"записи #{mid} нет")
        return f"забыл запись #{mid}"
    raise ToolError(f"неизвестное действие {action}")


def register(reg: Any, app: Any) -> None:
    from . import HINTS

    HINTS["memory"] = tuple(dict.fromkeys(HINTS.get("memory", ()) + (
        "запомн", "запомин", "помнишь", "вспомн", "забудь", "забыть", "памят", "запиши себе",
        "что ты знаешь обо мне", "remember")))
    reg.add(Tool(
        "memory",
        "Долгая память: факты о пользователе, предпочтения, договорённости. tags=profile — самое важное "
        "о человеке (всегда в контексте).",
        {"save": A("Запоминаю"), "search": A("Вспоминаю {query}", "read"), "list": A("Смотрю память", "read"),
         "delete": A("Забываю запись")},
        {"text": S("что запомнить"), "query": S("что найти"), "tags": S("теги через запятую"),
         "id": I("номер записи"), "limit": I("сколько показать")},
        handler, timeout=20))
