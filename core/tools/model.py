"""Model management by voice or text: current model, list, switch, remaining limits."""
from __future__ import annotations

from typing import Any

from ..router import RouterError
from . import A, S, Tool, ToolError, get_str, need


def _status_text(status: dict[str, Any]) -> str:
    lines = []
    for account in status.get("accounts", []):
        if account.get("disabled"):
            lines.append(f"{account['label']}: отключён ({account.get('disabled_reason') or 'ошибка'})")
            continue
        parts = []
        for model, bucket in (account.get("buckets") or {}).items():
            left, limit = bucket.get("remaining_requests"), bucket.get("limit_requests")
            tokens = bucket.get("remaining_tokens")
            text = f"{model}: запросов {left if left is not None else '?'}/{limit or '?'}"
            if tokens is not None:
                text += f", токенов/мин {tokens}"
            if bucket.get("cooldown_in"):
                text += f", пауза {bucket['cooldown_in']} с"
            parts.append(text + f", сегодня {bucket.get('used_today', 0)}")
        current = " (сейчас)" if account.get("current") else ""
        lines.append(f"{account['label']}{current}: " + ("; ".join(parts) or "ещё не использовался"))
    return "\n".join(lines) or "ключей нет — добавьте их в Настройки → Модели"


async def handler(action: str, args: dict, ctx: Any) -> str:
    router = ctx.app.router
    try:
        if action == "current":
            status = router.status()
            current = status.get("current")
            text = f"активная: {status.get('active')}"
            if current:
                text += f"\nпоследний ответ: {current['spec']} ({current['account']})"
            return text + "\nцепочка запасных: " + " → ".join(status.get("chain") or [])
        if action == "list":
            provider = get_str(args, "provider").strip().lower()
            if provider:
                models = await router.list_models(provider)
                return f"{provider}: " + (", ".join(models[:60]) or "нет моделей (нет ключа?)")
            lines = []
            for key, info in router.status().get("providers", {}).items():
                kind = "бесплатно" if info.get("free") else "платно" if info.get("paid") else "локально" if info.get("no_key") else ""
                mark = " ✓" if router.has_provider(key) else ""
                lines.append(f"{key}{mark} ({info.get('name')}, {kind}): {', '.join((info.get('models') or [])[:4])}")
            return "✓ — ключ добавлен\n" + "\n".join(lines)
        if action == "switch":
            spec = await router.resolve_model(need(args, "query"))
            router.set_active(spec)
            if getattr(router, "vision_only", lambda _s: False)(spec):
                return (f"{spec} — только для изображений: назначена моделью зрения; "
                        f"основная модель остаётся {router.settings.get('models.active')}")
            return f"переключено на {spec}"
        if action == "status":
            return _status_text(router.status())
    except RouterError as exc:
        raise ToolError(str(exc)) from exc
    raise ToolError(f"неизвестное действие {action}")


def register(reg: Any, app: Any) -> None:
    reg.add(Tool(
        "model",
        "Модели ИИ: какая сейчас, список, смена — только если пользователь сам попросил (query: «платная», «быстрая», «gemini», «claude», "
        "«provider:model»), остатки лимитов по ключам.",
        {"current": A("Смотрю модель", "read"), "list": A("Список моделей", "read"),
         "switch": A("Переключаю модель: {query}"), "status": A("Проверяю лимиты", "read")},
        {"query": S("какую модель включить"), "provider": S("groq, cerebras, openrouter, gemini, openai, anthropic…")},
        handler, timeout=40))
