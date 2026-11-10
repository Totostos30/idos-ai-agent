"""Bridge to external MCP servers configured in settings (mcp.servers)."""
from __future__ import annotations

import json
from typing import Any

from ..mcp import McpError
from ..util import clip
from . import A, Result, S, Tool, ToolError, get_str, need


def schema_hint(schema: dict | None) -> str:
    props = (schema or {}).get("properties") or {}
    required = set((schema or {}).get("required") or [])
    parts = []
    for key, spec in list(props.items())[:12]:
        kind = spec.get("type", "") if isinstance(spec, dict) else ""
        if isinstance(kind, list):
            kind = "/".join(str(k) for k in kind)
        parts.append(f"{key}{'*' if key in required else ''}:{kind or 'any'}")
    return ", ".join(parts) or "без параметров"


def _manager(ctx: Any) -> Any:
    manager = getattr(ctx.app, "mcp", None)
    if manager is None:
        raise ToolError("MCP не инициализирован")
    return manager


async def handler(action: str, args: dict, ctx: Any) -> Any:
    manager = _manager(ctx)
    try:
        if action == "servers":
            configs = manager.configs()
            if not configs:
                return "MCP-серверы не настроены (Настройки → Интеграции → MCP)"
            lines = []
            for name, cfg in configs.items():
                target = cfg.get("url") or " ".join([cfg.get("command", ""), *map(str, cfg.get("args") or [])])
                lines.append(f"{name}: {clip(target, 90)} ({'запущен' if manager.running(name) else 'не запущен'})")
            return "\n".join(lines)
        if action == "tools":
            items = await manager.list_tools(get_str(args, "server").strip() or None)
            if not items:
                return "инструментов нет"
            return "\n".join(f"{server} → {tool.get('name')}: {clip(tool.get('description') or '', 160)} "
                             f"| параметры: {schema_hint(tool.get('inputSchema'))}" for server, tool in items)
        if action == "call":
            arguments = args.get("arguments") or {}
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments) if arguments.strip() else {}
                except ValueError as exc:
                    raise ToolError("arguments должны быть JSON-объектом") from exc
            if not isinstance(arguments, dict):
                raise ToolError("arguments должны быть объектом")
            text, ok = await manager.call(need(args, "server"), need(args, "tool"), arguments)
            return Result(clip(text, 8000), ok=ok)
    except McpError as exc:
        raise ToolError(str(exc)) from exc
    raise ToolError(f"неизвестное действие {action}")


def register(reg: Any, app: Any) -> None:
    def available() -> bool:
        settings = getattr(app, "settings", None)
        return bool(settings and settings.get("mcp.servers"))

    reg.add(Tool(
        "mcp",
        "Внешние MCP-серверы (подключённые приложения). Сначала tools, затем call с arguments по схеме.",
        {"servers": A("Смотрю MCP-серверы", "read"), "tools": A("Смотрю инструменты MCP {server}", "read"),
         "call": A("MCP {server}: {tool}", "danger")},
        {"server": S("имя сервера"), "tool": S("имя инструмента"),
         "arguments": {"type": "object", "description": "аргументы инструмента"}},
        handler, available=available, timeout=200))
