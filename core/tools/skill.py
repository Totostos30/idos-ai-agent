"""Skills: step-by-step instructions the agent loads on demand and can write itself."""
from __future__ import annotations

from typing import Any

from . import A, S, Tool, ToolError, get_str, need


def handler(action: str, args: dict, ctx: Any) -> str:
    skills = ctx.app.skills
    try:
        if action == "list":
            return skills.prompt_list() or "навыков пока нет"
        if action == "load":
            return skills.load(need(args, "name"))
        if action == "save":
            path = skills.save(need(args, "name"), need(args, "description"), need(args, "text"), get_str(args, "title"))
            return f"навык сохранён: {path.stem}"
        if action == "delete":
            if skills.delete(need(args, "name")):
                return "навык удалён"
            return "удалять можно только свои навыки (data/skills)"
    except KeyError as exc:
        raise ToolError(str(exc.args[0]) if exc.args else "навык не найден") from exc
    raise ToolError(f"неизвестное действие {action}")


def register(reg: Any, app: Any) -> None:
    reg.add(Tool(
        "skill",
        "Навыки — пошаговые инструкции для приложений и задач. load перед незнакомой задачей; "
        "save — записать удачный способ на будущее.",
        {"list": A("Смотрю навыки", "read"), "load": A("Читаю навык {name}", "read"),
         "save": A("Сохраняю навык {name}"), "delete": A("Удаляю навык {name}")},
        {"name": S("имя навыка"), "description": S("одна строка: когда применять"),
         "text": S("шаги навыка (Markdown)")},
        handler, timeout=20))
