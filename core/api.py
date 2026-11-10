"""Methods the island UI calls over the core WebSocket ({id, method, params} → {id, result | error}).

CoreServer.invoke passes params as keyword arguments (unknown keys are dropped); binary frames arrive as `_binary`
(recorded audio). Every handler is async so it runs on the event loop next to the agent; blocking work goes to
threads explicitly. Secrets never leave the core: key lists are masked.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any, Callable

from . import bus, config, stt
from .keystore import KeyStore
from .tools import island

log = logging.getLogger("idos.api")

# Vocabulary hint for Whisper: names it would otherwise misspell.
STT_PROMPT = "Эйдос, IDOS, Groq, Telegram, Windows, YouTube, Chrome, Edge, Visual Studio Code, Discord, Steam."
PLAIN = (str, int, float, bool, type(None))


def public_key(entry: dict) -> dict:
    out = {k: v for k, v in entry.items() if k != "secret"}
    if "secret" in entry:
        out["masked"] = KeyStore.mask(str(entry["secret"]))
    return out


def plain(data: dict) -> dict:
    """Drop tasks/handles so the value is JSON-serialisable."""
    return {k: v for k, v in data.items() if isinstance(v, PLAIN) or isinstance(v, (list, dict))}


def tool_info(tool) -> dict:
    try:
        available = bool(tool.available())
    except Exception:  # noqa: BLE001
        available = False
    return {"name": tool.name, "description": tool.description, "available": available,
            "actions": {name: {"label": a.label, "kind": a.kind} for name, a in tool.actions.items()}}


def build_methods(app) -> dict[str, Callable[..., Any]]:
    agent, router, keys, settings = app.agent, app.router, app.keystore, app.settings
    methods: dict[str, Callable[..., Any]] = {}

    def method(name: str):
        def deco(fn):
            methods[name] = fn
            return fn
        return deco

    def keys_changed() -> dict:
        router.invalidate()
        state = router.status()
        bus.emit("router.status", state)
        return state

    # ------------------------------------------------------------------ core
    @method("core.ping")
    async def ping() -> dict:
        return {"pong": True, "version": config.VERSION, "demo": app.demo, "windows": config.IS_WINDOWS}

    @method("core.shutdown")
    async def shutdown() -> dict:
        asyncio.get_running_loop().call_later(0.2, app.shutdown)
        return {"ok": True}

    @method("core.selftest")
    async def selftest(full: bool = False) -> dict:
        from .selftest import check_app

        return await check_app(app, full=bool(full))

    # ------------------------------------------------------------------ agent
    @method("agent.ask")
    async def ask(text: str = "", chat_id: str | None = None, source: str = "text",
                  images: list | None = None) -> dict:
        app.tts.stop_now()  # a new question interrupts the spoken answer
        return await agent.ask(text, chat_id=chat_id, source=source, images=images)

    @method("agent.stop")
    async def stop() -> dict:
        app.tts.stop_now()
        return {"stopped": agent.stop()}

    @method("agent.confirm")
    async def confirm(step_id: str, approve: bool = False) -> dict:
        return {"ok": agent.confirm(step_id, bool(approve))}

    @method("agent.status")
    async def agent_status() -> dict:
        return agent.status()

    @method("agent.chats")
    async def chats(limit: int = 60) -> list:
        return agent.chats(int(limit))

    @method("agent.messages")
    async def messages(chat_id: str | None = None, limit: int = 200) -> list:
        return agent.messages(chat_id, int(limit))

    @method("agent.new_chat")
    async def new_chat(title: str = "") -> dict:
        return {"chat_id": agent.new_chat(title)}

    @method("agent.select_chat")
    async def select_chat(chat_id: str) -> dict:
        return {"chat_id": agent.select_chat(chat_id)}

    @method("agent.delete_chat")
    async def delete_chat(chat_id: str) -> dict:
        agent.delete_chat(chat_id)
        return {"ok": True}

    @method("agent.rename_chat")
    async def rename_chat(chat_id: str, title: str) -> dict:
        agent.rename_chat(chat_id, title)
        return {"ok": True}

    # ------------------------------------------------------------------ keys and models
    @method("keys.list")
    async def keys_list(provider: str | None = None) -> list:
        return [public_key(e) for e in keys.entries(provider)]

    @method("keys.add")
    async def keys_add(provider: str, secret: str, label: str = "") -> dict:
        item = keys.add(provider, secret, label)
        keys_changed()
        return {**public_key(item), "masked": KeyStore.mask(str(secret).strip())}

    @method("keys.add_many")
    async def keys_add_many(provider: str, text: str) -> dict:
        result = keys.add_many(provider, text)
        keys_changed()
        return result

    @method("keys.remove")
    async def keys_remove(key_id: str) -> dict:
        ok = keys.remove(key_id)
        keys_changed()
        if not ok:
            return {"ok": False, "error": "ключ не найден — обновите список"}
        return {"ok": True, "env": KeyStore.local_id(key_id).startswith("env-")}

    @method("keys.enable")
    async def keys_enable(key_id: str, enabled: bool = True) -> dict:
        ok = keys.set_enabled(key_id, bool(enabled))
        keys_changed()
        return {"ok": ok}

    @method("keys.label")
    async def keys_label(key_id: str, label: str = "") -> dict:
        ok = keys.set_label(key_id, label)
        keys_changed()
        return {"ok": ok}

    @method("scenarios.list")
    async def scenarios_list() -> list:
        return app.scenarios.store.list()

    @method("scenarios.blocks")
    async def scenarios_blocks() -> dict:
        return app.scenarios.blocks()

    @method("scenarios.get")
    async def scenarios_get(sid: str) -> dict:
        found = app.scenarios.store.get(sid)
        if not found:
            raise ValueError("сценарий не найден")
        return found

    @method("scenarios.save")
    async def scenarios_save(data: dict) -> dict:
        saved = app.scenarios.store.save(data)
        bus.emit("scenario.saved", {"id": saved["id"]})
        return saved

    @method("scenarios.delete")
    async def scenarios_delete(sid: str) -> dict:
        return {"ok": app.scenarios.store.delete(sid)}

    @method("scenarios.run")
    async def scenarios_run(sid: str = "", data: dict | None = None, arg: str = "", start_at: str = "",
                            request: str = "") -> dict:
        ref = app.scenarios.store.save(data) if data else sid
        return await app.scenarios.run(ref, {"arg": arg, "1": arg}, source="ui", start_at=start_at, request=request)

    @method("scenarios.answer")
    async def scenarios_answer(text: str = "", skip: bool = False) -> dict:
        ok = app.scenarios.engine.answer(text, skip=bool(skip))
        if ok:
            bus.emit("scenario.answer", {"text": text, "skip": bool(skip), "source": "ui"})
        return {"ok": ok}

    @method("scenarios.question")
    async def scenarios_question() -> dict:
        return {"question": app.scenarios.engine.question()}

    @method("scenarios.stop")
    async def scenarios_stop() -> dict:
        return {"ok": app.scenarios.engine.stop()}

    @method("scenarios.match")
    async def scenarios_match(text: str) -> dict:
        found = app.scenarios.match(text)
        return {"id": found[0]["id"], "name": found[0]["name"], "vars": found[1]} if found else {"id": None}

    @method("scenarios.pick")
    async def scenarios_pick(timeout: float = 60.0) -> dict:
        target = await app.scenarios.driver.pick(float(timeout))
        return {"target": target}

    @method("scenarios.probe")
    async def scenarios_probe(target: dict) -> dict:
        return await app.scenarios.driver.probe(target)

    @method("router.status")
    async def router_status() -> dict:
        return router.status()

    @method("router.providers")
    async def providers() -> dict:
        return router.providers()

    @method("router.enable")
    async def router_enable(account_id: str, enabled: bool = True) -> dict:
        router.enable(account_id, bool(enabled))
        return router.status()

    @method("router.test")
    async def router_test(account_id: str) -> dict:
        return await router.test_account(account_id)

    @method("router.selftest")
    async def router_selftest(spec: str) -> dict:
        return await router.selftest_model(spec)

    @method("router.set_active")
    async def set_active(spec: str) -> dict:
        chosen = router.set_active(spec)
        bus.emit("router.status", router.status())
        return {"active": settings.get("models.active"), "vision": settings.get("models.vision"),
                "vision_only": router.vision_only(chosen)}

    @method("router.choices")
    async def router_choices() -> dict:
        return {"force": router.forced(), "active": settings.get("models.active"), "choices": router.choices()}

    @method("router.force")
    async def router_force(spec: str = "") -> dict:
        try:
            chosen = router.set_force(spec)
        except Exception as exc:  # noqa: BLE001 - shown as a toast
            return {"ok": False, "error": str(exc), "force": router.forced()}
        return {"ok": True, "force": chosen}

    @method("router.models")
    async def models(provider: str) -> list:
        return await router.list_models(provider)

    @method("router.resolve")
    async def resolve(query: str) -> dict:
        return {"spec": await router.resolve_model(query)}

    # ------------------------------------------------------------------ settings
    @method("settings.get")
    async def settings_get(key: str = "") -> Any:
        return settings.get(key) if key else settings.snapshot()

    @method("settings.set")
    async def settings_set(key: str, value: Any = None) -> dict:
        settings.set(key, value)
        bus.emit("settings.changed", {"key": key, "value": value})
        return {"ok": True}

    @method("settings.update")
    async def settings_update(values: dict) -> dict:
        settings.update(values)
        for key, value in values.items():
            bus.emit("settings.changed", {"key": key, "value": value})
        return {"ok": True}

    @method("settings.reset")
    async def settings_reset(key: str) -> dict:
        settings.reset(key)
        value = settings.get(key)
        bus.emit("settings.changed", {"key": key, "value": value})
        return {"value": value}

    # ------------------------------------------------------------------ memory, skills, tools, MCP
    @method("memory.list")
    async def memory_list(limit: int = 100, offset: int = 0) -> dict:
        return {"items": app.memory.list(int(limit), int(offset)), "count": app.memory.count()}

    @method("memory.search")
    async def memory_search(query: str, limit: int = 10) -> list:
        return app.memory.search(query, int(limit))

    @method("memory.save")
    async def memory_save(text: str, tags: str = "") -> dict:
        return app.memory.save(text, tags, source="user")

    @method("memory.update")
    async def memory_update(mid: int, text: str | None = None, tags: str | None = None) -> dict:
        return {"ok": app.memory.update(int(mid), text, tags)}

    @method("memory.delete")
    async def memory_delete(mid: int) -> dict:
        return {"ok": app.memory.delete(int(mid))}

    @method("skills.list")
    async def skills_list() -> list:
        return app.skills.list()

    @method("skills.load")
    async def skills_load(name: str) -> dict:
        return {"name": name, "body": app.skills.load(name)}

    @method("skills.save")
    async def skills_save(name: str, description: str, body: str, title: str = "") -> dict:
        return {"path": str(app.skills.save(name, description, body, title))}

    @method("skills.delete")
    async def skills_delete(name: str) -> dict:
        return {"ok": app.skills.delete(name)}

    @method("tools.list")
    async def tools_list() -> list:
        return [tool_info(t) for t in app.tools.tools.values()]

    @method("mcp.servers")
    async def mcp_servers() -> list:
        hidden = ("env", "headers")
        return [{"name": name, "running": app.mcp.running(name), **{k: v for k, v in cfg.items() if k not in hidden}}
                for name, cfg in app.mcp.configs().items()]

    @method("mcp.tools")
    async def mcp_tools(name: str | None = None) -> list:
        return [{"server": server, "name": t.get("name"), "description": t.get("description", "")}
                for server, t in await app.mcp.list_tools(name)]

    # ------------------------------------------------------------------ voice and dictation
    @method("voice.status")
    async def voice_status(probe: bool = True) -> dict:
        info = {"tts": app.tts.status(), "stt_engine": settings.get("voice.stt", "gigastt")}
        if probe:
            info["stt"] = await app.stt.probe()
        return info

    @method("voice.say")
    async def voice_say(text: str, interrupt: bool = True) -> dict:
        return {"ok": await app.tts.say(text, interrupt=bool(interrupt))}

    @method("voice.stop")
    async def voice_stop() -> dict:
        await app.tts.stop()
        return {"ok": True}

    @method("voice.voices")
    async def voice_voices() -> dict:
        from .tts.engines import SETTING_FOR, catalog

        items = await asyncio.to_thread(catalog, settings)
        current = dict((key, settings.get("voice." + key, "")) for key in SETTING_FOR.values())
        return dict(engine=settings.get("voice.tts_engine", "silero"), rate=settings.get("voice.rate", 1.0),
                    speak_replies=settings.get("voice.speak_replies", "always"), current=current, engines=items,
                    lexicon=settings.get("voice.lexicon") or {})

    @method("voice.preview")
    async def voice_preview(text: str = "") -> dict:
        from .tts.normalize import normalize

        return dict(text=normalize(text, 10_000, full=True, lexicon=settings.get("voice.lexicon") or None))

    @method("voice.apply")
    async def voice_apply(engine: str = "", voice: str = "", rate: float | None = None, sample: bool = True,
                          text: str = "") -> dict:
        try:
            return await app.tts.apply(engine, voice, rate, bool(sample), text)
        except ValueError as exc:
            return dict(ok=False, error=str(exc))

    @method("voice.install")
    async def voice_install(engine: str) -> dict:
        return await app.tts.install(engine)

    @method("voice.stt_start")
    async def stt_start() -> dict:
        status = await app.stt.ensure()
        bus.emit("voice.stt", status)
        return status

    @method("voice.stt_probe")
    async def stt_probe(full: bool = True) -> dict:
        return await app.stt.probe(full=bool(full))

    @method("voice.transcribe")
    async def transcribe(sample_rate: int = 16000, language: str = "ru", audio_b64: str = "",
                         _binary: bytes | None = None) -> dict:
        audio = _binary if _binary is not None else base64.b64decode(audio_b64 or "")
        return {"text": await stt.transcribe(router, audio, int(sample_rate), language, STT_PROMPT)}

    @method("dictation.type")
    async def dictation_type(text: str, enter: bool = False) -> dict:
        text = (text or "").strip()
        if not text:
            return {"ok": False, "reason": "пустой текст"}
        if not config.IS_WINDOWS:
            return {"ok": False, "reason": "ввод текста в окна есть только в Windows"}
        from . import win

        if settings.get("dictation.type_into_window", True):
            await asyncio.to_thread(win.type_text, text + " ")
            if enter:
                await asyncio.to_thread(win.hotkey, "enter")
            return {"ok": True, "typed": len(text)}
        await asyncio.to_thread(win.set_clipboard, text)
        return {"ok": True, "copied": True}

    # ------------------------------------------------------------------ island
    @method("island.timers")
    async def timers() -> list:
        return [plain(t) for t in island.TIMERS.values()]

    @method("island.timer")
    async def timer(seconds: float = 0, duration: str = "", label: str = "") -> dict:
        secs = float(seconds or 0) or float(island.parse_duration(duration) or 0)
        if secs <= 0:
            raise ValueError("не понял длительность таймера")
        return plain(island.start_timer(secs, label or "Таймер", app))

    @method("island.cancel_timer")
    async def cancel_timer(timer_id: str) -> dict:
        return {"ok": island.cancel_timer(timer_id)}

    @method("island.action")
    async def island_action(card_id: str = "", action: str = "", data: Any = None) -> dict:
        bus.emit("island.action", {"card_id": card_id, "action": action, "data": data})
        return {"ok": True}

    # ------------------------------------------------------------------ demo and features
    if app.demo:
        @method("demo.play")
        async def demo_play(scene: str = "all") -> dict:
            from .demo import play

            app.background(play(app, scene), "demo")
            return {"ok": True}

    for feature in app.features:
        extra = getattr(feature, "methods", None)
        if callable(extra):
            methods.update(extra())
    return methods
