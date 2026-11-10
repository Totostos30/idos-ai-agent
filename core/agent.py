"""The agent loop: think -> call tools -> check -> answer (text + voice)."""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from . import prompts
from .bus import bus
from .llm import Cancelled
from .router import ContextTooLong, Exhausted, RouterError, ToolCallFailed, estimate_tokens
from .tools import Aborted, Context, Result
from .tools import META_TOOL
from .util import clip

TOOL_ALIASES = {
    "browser": "web", "browser_search": "web", "search": "web", "web_search": "web", "websearch": "web",
    "internet": "web", "google": "web", "yandex": "web", "open_url": "web", "fetch": "web",
    "python": "shell", "code_interpreter": "shell", "terminal": "shell", "powershell": "shell", "cmd": "shell",
    "window": "windows", "app": "apps", "file": "files", "remember": "memory",
}


def missing_tool_name(message: str, known: Any) -> str | None:
    """Name of a registered tool from a provider error like "attempted to call tool 'web' which was not in
    request.tools". Handles aliases and namespaced names (browser.search, web_search, functions.web)."""
    m = re.search(r"(?:call(?:ed)?|use[ds]?)\s+(?:the\s+)?(?:tool|function)\s+['\"`]([^'\"`]+)['\"`]", message or "", re.I)
    if not m:
        return None
    raw = m.group(1).strip().lower()
    if raw.startswith("functions."):
        raw = raw[len("functions."):]
    for cand in (raw, raw.split(".")[0], raw.split("_")[0], raw.split("__")[0]):
        cand = TOOL_ALIASES.get(cand, cand)
        if cand in known and cand != META_TOOL:
            return cand
    return None

log = logging.getLogger("agent")

STOP_RE = re.compile(r"^\s*(стоп|хватит|отмена|отмени|остановись|прекрати|stop|cancel)[\s.!]*$", re.I)
_NAMES = (r"(?:gemini|джемини|клод|claude|gpt|гпт|qwen|квен|deepseek|дипсик|mistral|мистрал|openai|чатгпт|"
          r"ollama|церебрас|cerebras|grok|грок)")
USER_MODEL_RE = re.compile(r"модел|нейросет|нейронк|" + _NAMES + r"|платн|\bllm\b", re.I)


def user_asked_model(text: str) -> bool:
    """Model changes are allowed only on the user's own initiative."""
    return bool(USER_MODEL_RE.search(text or ""))


MODEL_SWITCH_RE = re.compile(
    r"^\s*(?:(?:эйдос|айдос|идос|idos)[,\s]+)?(?:пожалуйста[,\s]+)?(?:"
    r"(?:переключи(?:сь)?|смени|поменяй)\b.{0,30}?(?:модел|" + _NAMES + r"|платн|быстр|стандарт|основн|локальн|умн)"
    r"|(?:включи|используй|выбери|поставь)\b.{0,30}?(?:модел|" + _NAMES + r"))", re.I)


# The model must not imitate the action journal: actions happen only through real tool calls.
FAKE_ACTIONS_RE = re.compile(r"\s*\[\s*(?:Действи[ея]|Actions?)\s*:[^\]\n]*\]?", re.I)
VERIFY_TOOLS = {"computer", "apps", "windows"}
FAKE_CALL_RE = re.compile(r"\b[a-z_]{2,20}\.[a-z_]{2,20}\b[^\n]{0,200}?→\s*(?:ok|ошибка)\b", re.I)
_LEAD = r"^\s*(?:(?:эйдос|айдос|идос|idos)[,\s]+)?(?:пожалуйста[,\s]+)?"
REMEMBER_RE = re.compile(_LEAD + r"(?:запомни|запиши в память|сохрани в память|заруби на носу)\b[,:\s]*(?:что\s+)?(.+)$", re.I | re.S)
COMMAND_RE = re.compile(_LEAD + r"(?:запомни|забудь|открой|запусти|включи|выключи|поставь|установи|удали|отправь|создай|"
                        r"закрой|нажми|скачай|сохрани|смени|поменяй|сделай|убавь|прибавь|сверни|разверни|напомни|"
                        r"вруби|выруби|переименуй|перемести|скопируй|вставь|напечатай|запиши)\b", re.I)
CLAIM_RE = re.compile(r"^\W*(?:запомнил|сохранил|записал|забыл|открыл|запустил|включил|выключил|поставил|установил|удалил|"
                      r"отправил|создал|переключил|сделал|выполнил|закрыл|нажал|напечатал|скачал|сменил|готово|сделано|"
                      r"включаю|открываю|запускаю|запоминаю)", re.I)


def clean_reply(text: str) -> str:
    """Remove action journals the model may have written into its answer by itself."""
    text = FAKE_ACTIONS_RE.sub("", text or "")
    return "\n".join(line for line in text.splitlines() if not FAKE_CALL_RE.search(line) or len(line) > 300).strip()


def real_actions(run: "Run") -> list[str]:
    return [a for a in run.actions if not a.startswith("tools ")]


def speech_part(text: str, limit: int = 700) -> str:
    """First paragraph of the answer, cleaned for text-to-speech."""
    text = (text or "").strip()
    if not text:
        return ""
    first = re.split(r"\n\s*\n", text, maxsplit=1)[0]
    first = " ".join(line.strip() for line in first.splitlines() if line.strip())
    first = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", first)
    first = re.sub(r"https?://\S+", "", first)
    first = re.sub(r"[*_`#>|]+", "", first)
    first = re.sub(r"^[-•\d.)\s]+", "", first)
    first = " ".join(first.split())
    if len(first) > limit:
        cut = first[:limit]
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        first = cut[:end + 1] if end > limit * 0.4 else cut.rsplit(" ", 1)[0] + "…"
    return first


def short_args(args: dict, limit: int = 90) -> str:
    parts = [f"{k}={clip(' '.join(str(v).split()), 32)}" for k, v in args.items() if k != "action" and v not in (None, "")]
    return clip(", ".join(parts), limit)


def public_args(args: dict) -> dict:
    return {k: (clip(str(v), 200) if isinstance(v, str) else v) for k, v in args.items()}


@dataclass
class Run:
    id: str
    chat_id: str
    text: str
    source: str = "text"
    images: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.time)
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    inbox: list[str] = field(default_factory=list)
    steps: int = 0
    history_len: int = 0
    actions: list[str] = field(default_factory=list)
    steer: list[str] = field(default_factory=list)
    enabled: set[str] = field(default_factory=set)
    spec: str = ""
    task: asyncio.Task | None = None
    retry_at: float = 0.0


class Agent:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.run: Run | None = None
        self.confirms: dict[str, asyncio.Future] = {}
        self._last_tools: dict[str, set[str]] = {}
        self.state = "idle"
        self.label = ""
        self._system: tuple[str, str] = ("", "")
        self._journal: dict[str, list[str]] = {}
        self._speech: asyncio.Task | None = None

    # --- public API ---------------------------------------------------------------------------------

    @property
    def settings(self):
        return self.app.settings

    @property
    def db(self):
        return self.app.db

    def status(self) -> dict[str, Any]:
        run = self.run
        return {"state": self.state, "label": self.label, "busy": run is not None,
                "run": run.id if run else None, "chat_id": self.current_chat(),
                "steps": run.steps if run else 0, "confirms": list(self.confirms)}

    def current_chat(self) -> str:
        chat = self.db.kv_get("agent.current_chat")
        if chat and self.db.one("SELECT id FROM chats WHERE id=?", (chat,)):
            return chat
        return self.new_chat()

    def new_chat(self, title: str = "") -> str:
        chat_id = uuid.uuid4().hex[:12]
        now = time.time()
        self.db.execute("INSERT INTO chats(id, title, created, updated) VALUES(?,?,?,?)", (chat_id, title, now, now))
        self.db.kv_set("agent.current_chat", chat_id)
        bus.emit("agent.chats", {"current": chat_id})
        return chat_id

    def select_chat(self, chat_id: str) -> str:
        if not self.db.one("SELECT id FROM chats WHERE id=?", (chat_id,)):
            raise KeyError("чат не найден")
        self.db.kv_set("agent.current_chat", chat_id)
        return chat_id

    def chats(self, limit: int = 60) -> list[dict[str, Any]]:
        return self.db.query("SELECT id, title, created, updated FROM chats ORDER BY updated DESC LIMIT ?", (int(limit),))

    def messages(self, chat_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT id, role, content, meta, created FROM messages WHERE chat_id=? ORDER BY id DESC "
                             "LIMIT ?", (chat_id or self.current_chat(), int(limit)))
        rows.reverse()
        for row in rows:
            try:
                row["meta"] = json.loads(row["meta"] or "{}")
            except ValueError:
                row["meta"] = {}
        return rows

    def delete_chat(self, chat_id: str) -> None:
        self.db.execute("DELETE FROM messages WHERE chat_id=?", (chat_id,))
        self.db.execute("DELETE FROM chats WHERE id=?", (chat_id,))
        if self.db.kv_get("agent.current_chat") == chat_id:
            self.new_chat()

    def rename_chat(self, chat_id: str, title: str) -> None:
        self.db.execute("UPDATE chats SET title=? WHERE id=?", (clip(title.strip(), 80), chat_id))

    async def ask(self, text: str, chat_id: str | None = None, source: str = "text",
                  images: list[str] | None = None) -> dict[str, Any]:
        """Start a run in the background (or steer the running one) and return at once."""
        text = (text or "").strip()
        if not text and not images:
            raise ValueError("пустой запрос")
        engine = getattr(getattr(self.app, "scenarios", None), "engine", None)
        asking = engine.question() if engine is not None and hasattr(engine, "question") else None
        if asking and text:
            # Сценарий ждёт ответа (блок «Спросить пользователя»): фраза — это ответ, а не новая задача.
            if STOP_RE.match(text):
                engine.stop()
                return {"stopped": True, "scenario": True}
            engine.answer(text)
            bus.emit("scenario.answer", {"run": asking.get("run"), "var": asking.get("var"), "text": text,
                                         "source": source})
            return {"answered": True, "var": asking.get("var"), "run_id": self.run.id if self.run else None}
        if self.run is not None:
            if STOP_RE.match(text):
                self.stop()
                return {"stopped": True, "run_id": self.run.id if self.run else None}
            self.run.inbox.append(text)
            bus.emit("agent.steer", {"run": self.run.id, "text": text})
            return {"queued": True, "run_id": self.run.id, "chat_id": self.run.chat_id}
        run = Run(id=uuid.uuid4().hex[:10], chat_id=chat_id or self.current_chat(), text=text, source=source,
                  images=list(images or []))
        self.run = run
        run.task = asyncio.create_task(self._task(run))
        return {"run_id": run.id, "chat_id": run.chat_id}

    async def ask_wait(self, text: str, **kwargs: Any) -> dict[str, Any]:
        started = await self.ask(text, **kwargs)
        run = self.run
        if run is not None and run.task is not None and run.id == started.get("run_id"):
            return await run.task
        return started

    def stop(self) -> bool:
        run = self.run
        for fut in list(self.confirms.values()):
            if not fut.done():
                fut.set_result(False)
        if run is None:
            return False
        run.cancel.set()
        try:  # background programs started by this run (shell background=true) die with it
            from .tools.shell import kill_background

            killed = kill_background(run.started)
            if killed:
                log.info("stopped %d background process(es)", killed)
        except Exception:  # noqa: BLE001
            log.debug("kill_background failed", exc_info=True)
        bus.emit("agent.stopping", {"run": run.id})
        return True

    def confirm(self, step_id: str, approve: bool) -> bool:
        fut = self.confirms.get(step_id)
        if fut is None or fut.done():
            return False
        fut.set_result(bool(approve))
        return True

    # --- run ----------------------------------------------------------------------------------------

    def _set_state(self, state: str, label: str = "") -> None:
        self.state, self.label = state, label
        bus.emit("agent.state", {"state": state, "label": label, "run": self.run.id if self.run else None})

    async def _task(self, run: Run) -> dict[str, Any]:
        ok, final = True, ""
        try:
            final = await self._execute(run)
        except (Aborted, Cancelled, asyncio.CancelledError):
            ok, final = False, "Остановлено."
        except Exhausted as exc:
            run.retry_at = float(getattr(exc, "retry_at", 0.0) or 0.0)
            ok, final = False, f"Сейчас не могу ответить: лимиты всех моделей исчерпаны.\n\n{exc}"
        except ContextTooLong:
            ok, final = False, ("Запрос не помещается в лимиты модели даже после сжатия.\n\nНачните новый чат "
                                "или переключитесь на модель с большим лимитом токенов.")
        except RouterError as exc:
            ok, final = False, f"Ошибка модели.\n\n{exc}"
        except Exception as exc:  # noqa: BLE001
            log.exception("agent run failed")
            ok, final = False, f"Внутренняя ошибка агента.\n\n{type(exc).__name__}: {exc}"
        finally:
            self.run = None
            for key in list(self.confirms):
                self.confirms.pop(key, None)
            try:
                self._finish(run, final, ok)
            except Exception:  # noqa: BLE001 - the answer must not get lost because of the DB
                log.exception("saving the answer failed")
            self._set_state("idle")
        if final:
            # Speech runs on its own: ask_wait/scenarios get the answer at once, and "стоп" / a new question
            # interrupts the voice through tts.stop_now() instead of waiting for the end of the phrase.
            self._speech = asyncio.create_task(self._speak(final, run))
        return {"run_id": run.id, "chat_id": run.chat_id, "text": final, "ok": ok, "steps": run.steps}

    def _finish(self, run: Run, final: str, ok: bool) -> None:
        meta = {"actions": run.actions[-12:], "model": run.spec, "ok": ok, "ms": int((time.time() - run.started) * 1000)}
        if run.retry_at:
            meta["retry_at"] = run.retry_at
        self._save(run.chat_id, "assistant", final, meta)
        bus.emit("agent.message", {"run": run.id, "chat_id": run.chat_id, "role": "assistant", "text": final,
                                   "ok": ok, "meta": meta})

    def _save(self, chat_id: str, role: str, content: str, meta: dict | None = None) -> None:
        now = time.time()
        self.db.execute("INSERT INTO messages(chat_id, role, content, meta, created) VALUES(?,?,?,?,?)",
                        (chat_id, role, content, json.dumps(meta or {}, ensure_ascii=False), now))
        self.db.execute("UPDATE chats SET updated=?, title=CASE WHEN title IS NULL OR title='' THEN ? ELSE title END "
                        "WHERE id=?", (now, clip(content.replace("\n", " "), 60) if role == "user" else "", chat_id))

    def _system_message(self) -> dict[str, str]:
        skills = self.app.skills.prompt_list() if getattr(self.app, "skills", None) else ""
        name = str(self.settings.get("general.user_name", "") or "")
        key = skills + "\x00" + name
        if self._system[0] != key:
            self._system = (key, prompts.system_prompt(skills, name))
        return {"role": "system", "content": self._system[1]}

    def _history(self, chat_id: str) -> list[dict[str, Any]]:
        limit = int(self.settings.get("agent.history_messages", 24) or 24)
        out: list[dict[str, Any]] = []
        journal: list[str] = []
        for row in self.messages(chat_id, limit):
            if row["role"] not in ("user", "assistant"):
                continue
            content = row["content"] or ""
            if row["role"] == "assistant":
                # Never show the journal inside assistant text: the model starts copying it instead of calling tools.
                content = clean_reply(content) or "…"
                actions = (row.get("meta") or {}).get("actions") or []
                if actions:
                    journal.extend(actions[-6:])
            out.append({"role": row["role"], "content": clip(content, 1500)})
        while out and out[0]["role"] != "user":
            out.pop(0)
        self._journal[chat_id] = journal[-6:]
        return out

    def _context(self, run: Run) -> str:
        window = layout = ""
        winctx = getattr(self.app, "winctx", None)
        if winctx is not None:
            try:
                info = winctx.foreground() or {}
                if info.get("title") or info.get("process"):
                    window = clip(f"{info.get('title', '')} ({info.get('process', '')})", 120)
                layout = winctx.layout() or ""
            except Exception:  # noqa: BLE001
                log.debug("window context failed", exc_info=True)
        memory = []
        if getattr(self.app, "memory", None) is not None:
            try:
                memory = self.app.memory.context(run.text)
            except Exception:  # noqa: BLE001
                log.debug("memory context failed", exc_info=True)
        return prompts.context_block(window=window, layout=layout, model=str(self.settings.get("models.active", "")),
                                     autonomy=str(self.settings.get("agent.autonomy", "full")), memory=memory,
                                     source=run.source, extra=self._journal_lines(run.chat_id))

    def _journal_lines(self, chat_id: str) -> list[str]:
        items = self._journal.get(chat_id) or []
        if not items:
            return []
        return ["Журнал системы (ранее в этом чате реально выполнено; не переписывай его в ответ): "
                + "; ".join(clip(a, 80) for a in items)]

    async def _describe_images(self, run: Run) -> str:
        notes = []
        for index, url in enumerate(run.images[:3], 1):
            content = [{"type": "text", "text": "Опиши изображение для ассистента: весь текст дословно и важные "
                                                 f"детали. Вопрос пользователя: {run.text or '—'}"},
                       {"type": "image_url", "image_url": {"url": url}}]
            try:
                done = await self.app.router.chat([{"role": "user", "content": content}], stream=False,
                                                  purpose="vision", max_tokens=700, cancel=run.cancel)
                notes.append(f"[Изображение {index}: {clip(done.text, 1800)}]")
            except RouterError as exc:
                notes.append(f"[Изображение {index}: не удалось распознать ({clip(str(exc), 120)})]")
        return "\n".join(notes)

    async def _quick_model_switch(self, run: Run) -> str | None:
        if len(run.text) > 90 or not MODEL_SWITCH_RE.search(run.text):
            return None
        router = self.app.router
        try:
            spec = await router.resolve_model(run.text)
        except Exception:  # noqa: BLE001 - let the model handle unusual requests
            return None
        if not spec:
            return None
        router.set_active(spec)
        vision = bool(getattr(router, "vision_only", lambda _s: False)(spec))
        run.actions.append(f"model.{'vision' if vision else 'switch'} {spec} → ok")
        bus.emit("agent.step", {"id": uuid.uuid4().hex[:8], "run": run.id, "tool": "model", "action": "switch",
                                "label": f"{'Модель зрения' if vision else 'Модель'}: {spec}", "status": "ok", "preview": spec})
        if vision:
            active = router.settings.get("models.active")
            return (f"{spec.split(':', 1)[-1]} используется только для чтения изображений — назначил её моделью зрения."
                    f"\n\nДля разговора и инструментов остаётся `{active}`.")
        return f"Переключился на {spec.split(':', 1)[-1]}.\n\nАктивная модель: `{spec}`."

    async def _quick_scenario(self, run: Run) -> str:
        """Точное совпадение запускает схему напрямую; модель вызывается только для объявленных переменных."""
        scenarios = getattr(self.app, "scenarios", None)
        if scenarios is None or run.images or not run.text:
            return ""
        if not self.settings.get("scenarios.fast_path", True):
            return ""
        try:
            found = scenarios.match(run.text)
        except Exception:  # noqa: BLE001
            log.exception("scenario match failed")
            return ""
        if not found:
            return ""
        scn, variables = found
        run.spec = "сценарий"
        label = f"Сценарий «{scn.get('name', '')}»"
        self._set_state("acting", label)
        result = await scenarios.run(scn, variables, source="agent", cancel=run.cancel, request=run.text)
        run.actions.append(f"scenario.run name={scn.get('name', '')} → {'ok' if result['ok'] else result['text']}")
        if result.get("stopped"):
            raise Aborted("остановлено")
        if result["ok"]:
            return f"Готово: {label.lower()} — {result['steps']} шагов."
        return (f"{label} не выполнен: {result['text']}.\n\nОткройте его во вкладке «Сценарии» и проверьте "
                "элемент пипеткой (кнопка «Проверить» подсветит, что находится).")

    async def _execute(self, run: Run) -> str:
        settings = self.settings
        self._set_state("thinking", "Думаю…")
        history = self._history(run.chat_id)
        self._save(run.chat_id, "user", run.text or "(изображение)", {"source": run.source, "images": len(run.images)})
        bus.emit("agent.message", {"run": run.id, "chat_id": run.chat_id, "role": "user", "text": run.text,
                                   "meta": {"source": run.source}})
        quick = await self._quick_model_switch(run)
        if quick:
            return quick
        quick = await self._quick_scenario(run)
        if quick:
            return quick
        user_text = run.text
        if run.images:
            self._set_state("thinking", "Рассматриваю изображение…")
            user_text = (user_text + "\n\n" + await self._describe_images(run)).strip()
        messages: list[dict[str, Any]] = [self._system_message(), *history,
                                          {"role": "user", "content": user_text + self._context(run)}]
        run.history_len = len(history)
        previous = set(self._last_tools.get(run.chat_id, ()))
        self._last_tools[run.chat_id] = run.enabled
        max_steps = int(settings.get("agent.max_steps", 40) or 40)
        deadline = run.started + 60 * float(settings.get("agent.max_minutes", 15) or 15)
        result_chars = int(settings.get("agent.tool_result_chars", 3500) or 3500)
        max_out = int(settings.get("models.max_output_tokens", 1200) or 1200)
        fails = {"context": 0, "format": 0, "fake": 0, "enable": 0}
        repeats: dict[str, int] = {}
        while True:
            if run.cancel.is_set():
                raise Aborted("остановлено")
            while run.inbox:
                steer = run.inbox.pop(0)
                run.steer.append(steer)
                messages.append({"role": "user", "content": prompts.STEERING.format(text=steer)})
            over = run.steps >= max_steps or time.time() > deadline
            if over:
                messages.append({"role": "user", "content": prompts.STEP_LIMIT_HINT})
            step_tools = None if over else self._step_tools(run, previous, max_out)
            self._fit(messages, step_tools, max_out, run)
            bus.emit("agent.delta_reset", {"run": run.id})
            try:
                done = await self.app.router.chat(
                    messages, tools=step_tools, stream=True, cancel=run.cancel, purpose="agent", max_tokens=max_out,
                    on_text=lambda delta: bus.emit("agent.delta", {"run": run.id, "text": delta}),
                    on_reset=lambda: bus.emit("agent.delta_reset", {"run": run.id}))
            except ContextTooLong:
                fails["context"] += 1
                if fails["context"] > 3 or not self._compact(messages, run, fails["context"]):
                    raise
                continue
            except ToolCallFailed as exc:
                missing = missing_tool_name(str(exc), getattr(self.app.tools, "tools", {}))
                if missing and missing not in run.enabled and fails["enable"] < 4:
                    # the model called a real tool that was not in this step's list (e.g. 'web') -> plug it in and retry
                    fails["enable"] += 1
                    run.enabled.add(missing)
                    log.info("model called tool %r outside the step list - enabling it", missing)
                    messages.append({"role": "user", "content": prompts.TOOL_ENABLED_HINT.format(name=missing)})
                    continue
                fails["format"] += 1
                if fails["format"] > 2:
                    raise RouterError(f"модель несколько раз ошиблась в формате вызова инструмента: {exc}") from exc
                messages.append({"role": "user", "content": prompts.TOOL_FORMAT_HINT.format(error=clip(str(exc), 200))})
                continue
            run.spec = done.spec or run.spec
            calls = [] if over else list(done.tool_calls or [])
            if not calls:
                text = (done.text or "").strip()
                hint = None if over or not step_tools else self._fake_action_hint(run, text, fails["fake"])
                if hint:
                    fails["fake"] += 1
                    log.warning("model described an action instead of calling a tool: %s", clip(text, 160))
                    messages.append({"role": "assistant", "content": clean_reply(text) or "…"})
                    messages.append({"role": "user", "content": hint})
                    continue
                text = clean_reply(text)
                text = await self._remember_fallback(run, text, result_chars)
                return text or ("Готово." if run.steps else "Готово, но модель ничего не ответила.")
            messages.append(done.assistant_message())
            for call in calls:
                run.steps += 1
                result = await self._run_call(run, call, result_chars)
                key = f"{call.get('name')}|{call.get('arguments')}"
                repeats[key] = repeats.get(key, 0) + 1
                content = result.text
                if repeats[key] >= 3:
                    content += ("\n[Система: ты уже трижды вызвал это действие с теми же аргументами. Попробуй "
                                "другой способ или сообщи, что мешает.]")
                messages.append({"role": "tool", "tool_call_id": call.get("id") or "call", "content": content})

    def _fake_action_hint(self, run: Run, text: str, tries: int) -> str | None:
        """The answer pretends an action happened but no tool was called — ask the model to really call it."""
        if tries >= 2:
            return None
        if FAKE_ACTIONS_RE.search(text) or FAKE_CALL_RE.search(text):
            return prompts.FAKE_ACTION_HINT
        if tries == 0 and not real_actions(run) and CLAIM_RE.match(text) and COMMAND_RE.match(run.text or ""):
            return prompts.CLAIM_HINT
        return None

    async def _remember_fallback(self, run: Run, text: str, result_chars: int) -> str:
        """«Запомни …» must end in memory.save even if the model only said «запомнил»."""
        match = REMEMBER_RE.match(run.text or "")
        if not match or len(run.text) > 400 or getattr(self.app, "memory", None) is None:
            return text
        if any(a.startswith("memory.save") for a in run.actions) or "memory" not in getattr(self.app.tools, "tools", {}):
            return text
        fact = " ".join(match.group(1).split()).strip(" .!")
        if not fact:
            return text
        call = {"id": "auto-memory", "name": "memory",
                "arguments": json.dumps({"action": "save", "text": "Со слов пользователя: " + fact, "tags": "profile"},
                                        ensure_ascii=False)}
        run.steps += 1
        result = await self._run_call(run, call, result_chars)
        if not result.ok:
            return (text + "\n\n" if text else "") + "Не удалось сохранить в память: " + clip(result.text, 200)
        return text or "Запомнил."

    def _step_tools(self, run: Run, previous: set[str], max_out: int) -> list[dict]:
        registry = self.app.tools
        select = getattr(registry, "select", None)
        if select is None:
            return registry.schemas()
        budget = int(self.app.router.budget_tokens("agent", max_out))
        share = float(self.settings.get("agent.tools_share", 0.4) or 0.4)
        sticky = list(run.enabled) + sorted(previous - run.enabled)
        return select(" ".join([run.text, *run.steer]), sticky, max(700, int(budget * share)))

    def _enable_tools(self, run: Run, args: dict) -> Result:
        raw = args.get("names") or args.get("name") or []
        items = raw.replace(";", ",").split(",") if isinstance(raw, str) else list(raw)
        names = [str(n).strip() for n in items if str(n).strip()]
        registered = getattr(self.app.tools, "tools", {})
        known = [n for n in names if n in registered]
        run.enabled.update(known)
        run.actions.append("tools " + ", ".join(names))
        text = ("подключены: " + ", ".join(known)) if known else "ничего не подключено"
        missing = [n for n in names if n not in registered]
        if missing:
            text += "; нет таких: " + ", ".join(missing) + ". Есть: " + ", ".join(sorted(registered))
        return Result(text, ok=bool(known))

    async def _run_call(self, run: Run, call: dict, result_chars: int) -> Result:
        name = str(call.get("name") or "")
        raw = call.get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else dict(raw)
            if not isinstance(args, dict):
                raise ValueError("not an object")
        except (ValueError, TypeError):
            run.actions.append(f"{name} → ошибка аргументов")
            return Result(f"Ошибка: аргументы {name} — не JSON-объект. Пример: {{\"action\": \"...\"}}", ok=False)
        if name == META_TOOL:
            return self._enable_tools(run, args)
        tools = self.app.tools
        if name in getattr(tools, "tools", {}):
            run.enabled.add(name)
        action = str(args.get("action") or "")
        if name == "model" and action == "switch" and not user_asked_model(run.text):
            run.actions.append("model.switch → отклонено (пользователь не просил)")
            return Result("Отказ: модель меняется только по прямой просьбе пользователя. "
                          "Продолжай на текущей модели.", ok=False)
        label = tools.label(name, action, args)
        step_id = uuid.uuid4().hex[:8]
        step = {"id": step_id, "run": run.id, "tool": name, "action": action, "label": label,
                "args": public_args(args)}
        bus.emit("agent.step", {**step, "status": "running"})
        self._set_state("acting", label)
        if tools.needs_confirm(name, action, str(self.settings.get("agent.autonomy", "full"))):
            if not await self._confirm(run, step):
                bus.emit("agent.step", {**step, "status": "denied"})
                run.actions.append(f"{name}.{action} → отклонено")
                return Result(prompts.DENIED, ok=False)
            self._set_state("acting", label)
        ctx = Context(app=self.app, chat_id=run.chat_id, cancel=run.cancel, step_id=step_id, request=run.text)
        started = time.perf_counter()
        result = await tools.run(name, args, ctx)
        took = round((time.perf_counter() - started) * 1000, 1)
        text = clip(result.text, result_chars)
        if result.ok and name in VERIFY_TOOLS and tools.kind(name, action) != "read" \
                and self.settings.get("agent.verify_actions", True):
            seen = await self._observe()
            if seen:
                text += "\n" + seen
        bus.emit("agent.step", {**step, "status": "ok" if result.ok else "error", "preview": clip(result.text, 280),
                                "image": result.image, "ms": took})
        run.actions.append(f"{name}.{action} {short_args(args, 60)} → {'ok' if result.ok else 'ошибка'}".replace("  ", " "))
        try:
            self.db.log_action(run.chat_id, f"{name}.{action}", args, result.text, result.ok)
        except Exception:  # noqa: BLE001
            log.debug("log_action failed", exc_info=True)
        self._set_state("thinking", "Думаю…")
        return Result(text, ok=result.ok, data=result.data)

    async def _confirm(self, run: Run, step: dict) -> bool:
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.confirms[step["id"]] = fut
        bus.emit("agent.confirm", step)
        self._set_state("waiting", f"Подтвердите: {step['label']}")
        waiter = asyncio.ensure_future(run.cancel.wait())
        try:
            done, _ = await asyncio.wait({fut, waiter}, timeout=180, return_when=asyncio.FIRST_COMPLETED)
            if fut in done:
                return bool(fut.result())
            if run.cancel.is_set():
                raise Aborted("остановлено")
            return False
        finally:
            waiter.cancel()
            self.confirms.pop(step["id"], None)
            bus.emit("agent.confirm_done", {"id": step["id"]})

    # --- token budget -------------------------------------------------------------------------------

    def _fit(self, messages: list[dict], tools: list[dict] | None, max_out: int, run: Run) -> None:
        budget = self.app.router.budget_tokens("agent", max_out)

        def fits() -> bool:
            return estimate_tokens(messages, tools) <= budget

        if fits():
            return
        tool_rows = [m for m in messages if m.get("role") == "tool"]
        for message in tool_rows[:-2]:
            message["content"] = clip(str(message.get("content") or ""), 300)
        while not fits() and run.history_len > 0:
            del messages[1]
            run.history_len -= 1
        if fits():
            return
        self._collapse(messages, run)
        for limit in (1500, 700, 300):
            if fits():
                return
            for message in messages:
                if message.get("role") == "tool":
                    message["content"] = clip(str(message.get("content") or ""), limit)

    def _compact(self, messages: list[dict], run: Run, level: int) -> bool:
        """Called after the provider said the request is too large; shrink harder each time."""
        changed = False
        if run.history_len:
            del messages[1:1 + run.history_len]
            run.history_len = 0
            changed = True
        changed = self._collapse(messages, run) or changed
        limit = {1: 1200, 2: 500}.get(level, 200)
        for message in messages:
            if message.get("role") == "tool" and len(str(message.get("content") or "")) > limit:
                message["content"] = clip(str(message["content"]), limit)
                changed = True
        user = messages[1] if len(messages) > 1 else None
        if user and user.get("role") == "user" and isinstance(user.get("content"), str) and len(user["content"]) > 4000:
            user["content"] = clip(user["content"], 4000 if level < 3 else 1500)
            changed = True
        return changed or level < 3

    def _collapse(self, messages: list[dict], run: Run, keep: int = 1) -> bool:
        start = 2 + run.history_len
        groups: list[tuple[int, int]] = []
        index = start
        while index < len(messages):
            message = messages[index]
            if message.get("role") == "assistant" and message.get("tool_calls"):
                end = index + 1
                while end < len(messages) and messages[end].get("role") == "tool":
                    end += 1
                groups.append((index, end))
                index = end
            else:
                index += 1
        if len(groups) <= keep:
            return False
        old = groups[:-keep]
        notes = []
        for first, last in old:
            calls = messages[first].get("tool_calls") or []
            for call, result in zip(calls, messages[first + 1:last]):
                fn = call.get("function") or call
                notes.append(f"- {fn.get('name')} {clip(str(fn.get('arguments') or ''), 80)} → "
                             f"{clip(' '.join(str(result.get('content') or '').split()), 120)}")
        messages[old[0][0]:old[-1][1]] = [{"role": "user", "content": "Уже выполнено в этой задаче (кратко):\n"
                                                     + "\n".join(notes[-20:])}]
        return True

    async def _observe(self) -> str:
        """Cheap post-action check (no screenshot, no tokens for images): which window is really in front now.
        Lets the model notice that a click/launch did not do what it claimed before it answers \"готово\"."""
        try:
            from . import win

            if not getattr(win, "IS_WINDOWS", False):
                return ""
            await asyncio.sleep(0.35)  # let the window appear / take focus
            info = await asyncio.wait_for(asyncio.to_thread(win.foreground), 2)
        except Exception:  # noqa: BLE001
            return ""
        if not info:
            return ""
        title = clip(str(info.get("title") or "без заголовка"), 90)
        process = str(info.get("process") or "")
        return f"[проверка] сейчас активно окно: «{title}»" + (f" ({process})" if process else "")

    # --- voice --------------------------------------------------------------------------------------

    async def _speak(self, text: str, run: Run) -> None:
        mode = str(self.settings.get("voice.speak_replies", "always") or "always")
        if mode == "never" or (mode == "voice" and run.source != "voice"):
            return
        tts = getattr(self.app, "tts", None)
        if tts is None:
            return
        speech = speech_part(text, int(self.settings.get("voice.max_speech_chars", 700) or 700))
        if not speech:
            return
        try:
            result = tts.say(speech)
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001
            log.warning("tts failed", exc_info=True)
