"""Hooks for coding agents (Claude Code, Codex CLI): they report "waiting for permission" and "done" to the island.

The agents run `python -m core.hook <source> <event>` (installed by hooks.install). The hook writes a JSON file into
data/hooks; this feature picks it up, shows a card and optionally speaks. No network port is needed."""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import sys
import time

from .. import bus, config
from . import Feature

log = logging.getLogger("idos.hooks")
SPOOL = config.DATA / "hooks"
POLL = 1.0
NAMES = {"claude": "Claude Code", "codex": "Codex"}
TEXTS = {"notification": "ждёт вашего ответа", "permission": "просит разрешение", "stop": "закончил работу",
         "agent-turn-complete": "закончил работу", "error": "сообщил об ошибке"}


def write_event(source: str, event: str, payload: dict | None = None, spool: pathlib.Path = SPOOL) -> pathlib.Path:
    spool.mkdir(parents=True, exist_ok=True)
    path = spool / f"{time.time_ns()}-{source}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"source": source, "event": event, "payload": payload or {}, "time": time.time()},
                              ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return path


def describe(item: dict) -> tuple[str, str, str]:
    """Hook event → (title, text, kind) for the island card."""
    source = str(item.get("source", "")).lower()
    event = str(item.get("event", "")).lower()
    payload = item.get("payload") or {}
    if isinstance(payload, dict):
        event = str(payload.get("type") or payload.get("hook_event_name") or event).lower()
        message = payload.get("message") or payload.get("last-assistant-message") or ""
    else:
        message = ""
    if "permission" in str(message).lower() or "permission" in event:
        event = "permission"
    key = next((k for k in TEXTS if k in event), "notification")
    title = NAMES.get(source, source.capitalize() or "Агент")
    text = str(message).strip()[:200] or TEXTS[key]
    kind = "warning" if key in ("permission", "notification") else "error" if key == "error" else "success"
    return title, text, kind


def hook_command(source: str, event: str) -> str:
    python = pathlib.Path(sys.executable)
    if python.name.lower() == "python.exe" and (python.parent / "pythonw.exe").exists():
        python = python.parent / "pythonw.exe"
    return f'"{python}" -m core.hook {source} {event} --home "{config.ROOT}"'


def install_claude(settings_path: pathlib.Path | None = None) -> pathlib.Path:
    path = settings_path or pathlib.Path.home() / ".claude" / "settings.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    hooks = data.setdefault("hooks", {})
    for event, name in (("Notification", "notification"), ("Stop", "stop")):
        groups = [g for g in hooks.get(event, []) if "core.hook" not in json.dumps(g)]
        groups.append({"hooks": [{"type": "command", "command": hook_command("claude", name)}]})
        hooks[event] = groups
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def install_codex(config_path: pathlib.Path | None = None) -> pathlib.Path:
    path = config_path or pathlib.Path.home() / ".codex" / "config.toml"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    python = hook_command("codex", "notify").split('"')[1]
    args = [python, "-m", "core.hook", "codex", "notify", "--home", str(config.ROOT)]
    notify = "notify = [" + ", ".join(json.dumps(a) for a in args) + "]"
    out, placed, in_table = [], False, False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            if not placed:
                out.append(notify)
                placed = True
            in_table = True
        if not in_table and stripped.startswith("notify"):
            continue
        out.append(line)
    if not placed:
        out.append(notify)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out).strip() + "\n", encoding="utf-8")
    return path


class Hooks(Feature):
    name = "hooks"

    def __init__(self, app, spool: pathlib.Path = SPOOL) -> None:
        super().__init__(app)
        self.spool = spool
        self.events: list[dict] = []

    def enabled(self, source: str) -> bool:
        return bool(self.settings.get({"claude": "integrations.claude_code", "codex": "integrations.codex"}.get(
            source, "integrations." + source), False))

    async def handle(self, item: dict) -> dict | None:
        if not self.enabled(str(item.get("source", ""))):
            return None
        title, text, kind = describe(item)
        event = {"title": title, "text": text, "kind": kind, "time": item.get("time", time.time())}
        self.events = ([event] + self.events)[:50]
        bus.emit("hooks.event", event)
        bus.card(title, text, kind=kind, icon="terminal", timeout=10 if kind != "warning" else 20)
        if self.settings.get("integrations.hooks_voice", True):
            await self.say(f"{title}: {text}" if len(text) < 90 else f"{title} {TEXTS['notification']}")
        return event

    async def poll(self) -> int:
        if not self.spool.is_dir():
            return 0
        count = 0
        for path in sorted(self.spool.glob("*.json")):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                item = None
            try:
                path.unlink()
            except OSError:
                pass
            if isinstance(item, dict) and time.time() - float(item.get("time", 0)) < 600:
                await self.handle(item)
                count += 1
        return count

    async def _loop(self) -> None:
        while True:
            try:
                await self.poll()
            except Exception:  # noqa: BLE001
                log.debug("hook poll failed", exc_info=True)
            await asyncio.sleep(POLL)

    async def start(self) -> None:
        self.spawn(self._loop(), "spool")

    def methods(self) -> dict:
        async def install(target: str = "claude") -> dict:
            fn = install_codex if target == "codex" else install_claude
            path = await asyncio.to_thread(fn)
            key = "integrations.codex" if target == "codex" else "integrations.claude_code"
            if hasattr(self.settings, "set"):
                self.settings.set(key, True)
            return {"ok": True, "path": str(path)}

        async def events() -> list:
            return self.events

        async def test(source: str = "claude") -> dict:
            return {"event": await self.handle({"source": source, "event": "stop", "time": time.time()})}

        return {"hooks.install": install, "hooks.events": events, "hooks.test": test}


def create(app) -> Hooks:
    return Hooks(app)
