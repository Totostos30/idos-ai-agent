"""Self-test: `python -m core --selftest` prints a report (exit code 1 on errors); the UI calls `core.selftest`.

Checks Python and optional packages, the data folder, API keys (with full=True every key is tried with a real
request that does not spend chat quota), the voices, GigaSTT (a real short recognition session), the Whisper
fallback and every tool.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import tempfile

from . import config

# (module, what it gives, how to get it, Windows only)
PACKAGES = [
    ("playwright", "браузер агента", "pip install playwright", False),
    ("uiautomation", "элементы окон", "pip install uiautomation", True),
    ("winrt.windows.media.ocr", "распознавание текста на экране", "pip install winrt-Windows.Media.Ocr", True),
    ("mss", "быстрые скриншоты", "pip install mss", False),
    ("psutil", "процессы и сеть", "pip install psutil", False),
    ("pycaw", "громкость", "pip install pycaw comtypes", True),
    ("ddgs", "поиск в интернете без ключа", "pip install ddgs", False),
    ("PIL", "картинки и скриншоты", "pip install Pillow", False),
    ("markitdown", "чтение документов", "pip install markitdown[docx,pdf,pptx,xlsx]", False),
    ("torch", "голос Silero", "README, раздел «Голос»", False),
    ("scipy", "голос Silero (scipy)", "pip install scipy", False),
]
LEVELS = {"ok": "[ OK ]", "info": "[ -- ]", "warn": "[ !! ]", "error": "[FAIL]"}
TOOL_HINTS = {"mcp": "нет настроенных MCP-серверов (Настройки → MCP)"}


def item(name: str, level: str, detail: str) -> dict:
    return {"name": name, "level": level, "detail": detail}


def has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def environment_checks() -> list[dict]:
    out = []
    v = sys.version_info
    good = v >= (3, 11)
    out.append(item("Python", "ok" if good else "error",
                    f"{v.major}.{v.minor}.{v.micro}" + ("" if good else " — нужен Python 3.11 или новее")))
    for module, what, hint, windows_only in PACKAGES:
        if has_module(module):
            out.append(item(module, "ok", what))
        elif windows_only and not config.IS_WINDOWS:
            out.append(item(module, "info", f"{what}: только в Windows"))
        else:
            out.append(item(module, "warn", f"не установлен — отключено: {what} ({hint})"))
    try:
        config.ensure_dirs()
        with tempfile.NamedTemporaryFile(dir=config.DATA):
            pass
        out.append(item("Папка данных", "ok", str(config.DATA)))
    except OSError as exc:
        out.append(item("Папка данных", "error", f"{config.DATA}: {exc}"))
    return out


async def key_checks(app, full: bool) -> list[dict]:
    status = app.router.status()
    accounts = status.get("accounts", [])
    if not accounts:
        return [item("Ключи API", "error",
                     "нет ни одного ключа — добавьте ключи Groq в настройках (можно сразу несколько, по одному в строке)")]
    counts: dict[str, int] = {}
    for account in accounts:
        counts[account["provider"]] = counts.get(account["provider"], 0) + 1
    summary = ", ".join(f"{p}: {n}" for p, n in sorted(counts.items()))
    out = [item("Ключи API", "ok", f"{summary}; модель: {status.get('active')}")]
    to_test = []
    for account in accounts:
        name = f"Ключ {account.get('label') or account['id']} ({account.get('masked', '')})"
        if not account.get("enabled", True):
            out.append(item(name, "info", "выключен"))
        elif account.get("disabled"):
            out.append(item(name, "warn", account.get("disabled_reason") or "временно отключён"))
        elif full:
            to_test.append((name, account["id"]))
    results = await asyncio.gather(*(app.router.test_account(aid) for _, aid in to_test), return_exceptions=True)
    for (name, _), result in zip(to_test, results):
        if isinstance(result, BaseException):
            out.append(item(name, "warn", f"проверка не удалась: {result}"))
        else:
            out.append(item(name, "ok" if result.get("ok") else "warn",
                            f"{result.get('message', '')} ({result.get('ms', 0)} мс)"))
    return out


async def voice_checks(app, full: bool) -> list[dict]:
    out = []
    engines = app.tts.status().get("engines", [])
    ready = 0
    for engine in engines:
        reason = engine.get("reason") or ("недавно не сработал" if engine.get("failed") else "")
        ready += not reason
        out.append(item(f"Голос {engine['name']}", "warn" if reason else "ok", reason or "готов"))
    if not engines:
        out.append(item("Голос", "info", "озвучка выключена в настройках"))
    elif not ready:
        out.append(item("Голос", "warn", "ни один голос не готов — ответы будут только текстом"))
    if str(app.settings.get("voice.stt", "gigastt") or "") == "gigastt":
        probe = await (app.stt.ensure() if full else app.stt.probe())
        if probe.get("ok") and full:
            probe = await app.stt.probe(full=True)
        if probe.get("ok"):
            detail = "работает" + (f", частоты {probe['rates']}" if probe.get("rates") else "")
            if "latency_ms" in probe:
                detail += f", ответ за {probe['latency_ms']} мс"
            if probe.get("note"):
                detail += f" ({probe['note']})"
            out.append(item("GigaSTT", "ok", detail))
        else:
            out.append(item("GigaSTT", "warn", probe.get("reason") or "недоступен"))
    accounts = app.router.status().get("accounts", [])
    groq = any(a.get("provider") == "groq" and a.get("enabled", True) for a in accounts)
    out.append(item("Whisper (Groq)", "ok" if groq else "warn",
                    "запасное распознавание речи" if groq else "нужен ключ Groq для запасного распознавания"))
    return out


def tool_checks(app) -> list[dict]:
    out = []
    for tool in app.tools.tools.values():
        try:
            ok = bool(tool.available())
        except Exception:  # noqa: BLE001
            ok = False
        quiet = tool.name in TOOL_HINTS or not config.IS_WINDOWS
        level = "ok" if ok else ("info" if quiet else "warn")
        detail = "доступен" if ok else TOOL_HINTS.get(tool.name, "недоступен в этой системе")
        out.append(item(f"Инструмент {tool.name}", level, detail))
    return out


async def check_app(app, full: bool = False) -> dict:
    checks = await asyncio.to_thread(environment_checks)
    checks += await key_checks(app, full)
    checks += await voice_checks(app, full)
    checks += tool_checks(app)
    errors = sum(c["level"] == "error" for c in checks)
    warnings = sum(c["level"] == "warn" for c in checks)
    return {"ok": errors == 0, "errors": errors, "warnings": warnings, "checks": checks}


def format_report(report: dict) -> str:
    lines = [f"{LEVELS.get(c['level'], '[ ?? ]')} {c['name']}: {c['detail']}" for c in report["checks"]]
    lines += ["", f"Итог: ошибок {report['errors']}, предупреждений {report['warnings']}"]
    return "\n".join(lines)


def run_selftest(full: bool = True) -> int:
    from .app import App

    async def main() -> dict:
        app = App()
        try:
            return await check_app(app, full=full)
        finally:
            await app.close()

    report = asyncio.run(main())
    print(f"IDOS {config.VERSION} — самопроверка")
    print(format_report(report))
    return 1 if report["errors"] else 0
