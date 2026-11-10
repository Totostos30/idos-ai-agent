"""Инструмент scenario: готовые сценарии пользователя (блок-схемы). Один вызов вместо многих шагов модели."""
from __future__ import annotations

import re
from typing import Any

from . import A, S, Tool, ToolError, get_str


def parse_vars(raw: Any) -> dict[str, str]:
    """vars: {"город": "Москва"} или строка «город=Москва; время=7:00»."""
    from ..scenarios import var_name
    if isinstance(raw, dict):
        items = raw.items()
    elif isinstance(raw, str) and raw.strip():
        text = raw.strip()
        if text.startswith("{"):
            import json
            try:
                data = json.loads(text)
            except ValueError:
                data = None
            if isinstance(data, dict):
                return parse_vars(data)
        items = [part.split("=", 1) for part in re.split(r"[;\n]+", text) if "=" in part]
    else:
        return {}
    return {var_name(k): str(v).strip() for k, v in items if str(v).strip()}


async def handler(action: str, args: dict, ctx: Any) -> Any:
    app = getattr(ctx, "app", None)
    scenarios = getattr(app, "scenarios", None)
    if scenarios is None:
        raise ToolError("сценарии недоступны")
    if action == "list":
        items = [s for s in scenarios.store.list() if s.get("enabled", True)]
        if not items:
            return "сценариев пока нет — пользователь создаёт их во вкладке «Сценарии»"
        return "\n".join(f"{s['name']} — фразы: {', '.join(s['triggers']) or 'нет'}"
                         + (f" — {s['description'][:120]}" if s.get("description") else "") for s in items)
    name = get_str(args, "name").strip()
    if not name:
        raise ToolError("укажите name — название сценария")
    scn = scenarios.store.find(name)
    if not scn:
        names = ", ".join(s["name"] for s in scenarios.store.list()) or "нет"
        raise ToolError(f"сценарий «{name}» не найден; есть: {names}")
    if action == "show":
        return f"«{scn['name']}»:\n" + scenarios.describe(scn)
    if action == "run":
        arg = get_str(args, "arg").strip()
        variables = {"arg": arg, "1": arg}
        variables.update(parse_vars(args.get("vars")))
        result = await scenarios.run(scn, variables, source="agent", cancel=getattr(ctx, "cancel", None),
                                     request=getattr(ctx, "request", ""))
        if not result["ok"]:
            raise ToolError(f"сценарий «{scn['name']}»: {result['text']}")
        return f"сценарий «{scn['name']}» выполнен: {result['steps']} шагов за {result['ms'] / 1000:.1f} с"
    raise ToolError(f"неизвестное действие {action}")


def register(reg: Any, app: Any) -> None:
    reg.add(Tool(
        "scenario",
        "Готовые сценарии пользователя (записанные блок-схемы действий). Если задача совпадает со сценарием — "
        "запусти его: это один шаг вместо многих. list — список; show — шаги и переменные; run — выполнить "
        "(name, arg — подстановка для «*» в фразе, vars — значения переменных «имя=значение; …»). "
        "Объявленные переменные, не переданные в vars, извлекаются из исходного запроса пользователя. "
        "Если в сценарии есть блоки «Спросить пользователя» и пользователь уже назвал значение — "
        "передай его в vars, тогда вопрос не задаётся; иначе сценарий сам спросит во время выполнения.",
        {"list": A("Смотрю сценарии", "read"), "show": A("Смотрю сценарий {name}", "read"),
         "run": A("Запускаю сценарий {name}")},
        {"name": S("название сценария"), "arg": S("run: значение для {arg} в сценарии"),
         "vars": S("run: переменные для блоков «Спросить»: город=Москва; время=07:00")},
        handler, timeout=600))
