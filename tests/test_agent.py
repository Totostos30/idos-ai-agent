"""Agent loop tests with a scripted fake router (no network)."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import pathlib
import sys
import tempfile
import unittest

TMP = pathlib.Path(tempfile.mkdtemp(prefix="idos-agent-"))
os.environ.setdefault("IDOS_HOME", str(TMP))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from core import config, prompts  # noqa: E402
from core import db as dbmod  # noqa: E402
from core.agent import MODEL_SWITCH_RE, Agent, speech_part  # noqa: E402
from core.bus import bus  # noqa: E402
from core.llm import Cancelled, Completion  # noqa: E402
from core.memory import Memory, fts_query  # noqa: E402
from core.router import ContextTooLong, ToolCallFailed  # noqa: E402
from core.skills import Skills  # noqa: E402
from core.tools import A, Registry, S, Tool, ToolError  # noqa: E402

WAIT = object()


def comp(text: str = "", calls: list | None = None, spec: str = "groq:openai/gpt-oss-120b") -> Completion:
    tool_calls = []
    for index, (name, args) in enumerate(calls or []):
        raw = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
        tool_calls.append({"id": f"call_{index}_{name}", "name": name, "arguments": raw})
    return Completion(text=text, tool_calls=tool_calls, spec=spec)


class FakeRouter:
    def __init__(self, script: list, budget: int = 6000) -> None:
        self.script = list(script)
        self.calls: list[dict] = []
        self.budget = budget
        self.active = None

    def budget_tokens(self, purpose: str = "agent", max_output: int = 1200) -> int:
        return self.budget

    async def chat(self, messages, *, tools=None, stream=True, on_text=None, on_reset=None, cancel=None, chain=None,
                   purpose="agent", max_tokens=None, temperature=None, extra=None, tool_choice=None):
        self.calls.append({"messages": copy.deepcopy(messages), "tools": tools, "purpose": purpose})
        item = self.script.pop(0) if self.script else comp("конец")
        if item is WAIT:
            await cancel.wait()
            raise Cancelled()
        if callable(item):
            item = item(messages, tools)
        if isinstance(item, BaseException):
            raise item
        if on_text and item.text:
            on_text(item.text)
        return item

    async def resolve_model(self, query: str) -> str:
        return "groq:openai/gpt-oss-20b"

    def set_active(self, spec: str) -> None:
        self.active = spec


class FakeTTS:
    def __init__(self) -> None:
        self.said: list[str] = []

    def say(self, text: str) -> None:
        self.said.append(text)


class App:
    counter = 0

    def __init__(self, router: FakeRouter, autonomy: str = "full") -> None:
        App.counter += 1
        self.settings = config.Settings(TMP / f"settings-{App.counter}.json")
        self.settings.set("agent.autonomy", autonomy)
        self.router = router
        self.db = dbmod.DB(str(TMP / f"db-{App.counter}.sqlite"))
        self.memory = Memory(self.db)
        skills_dir = TMP / "skills"
        skills_dir.mkdir(exist_ok=True)
        (skills_dir / "telegram.md").write_text(
            "---\nname: telegram\ndescription: Сообщения в Telegram\n---\nОткрой Telegram и найди чат.", encoding="utf-8")
        self.skills = Skills([(skills_dir, True)])
        self.tts = FakeTTS()
        self.tools = Registry()
        self.log: list[tuple[str, dict]] = []

        def handler(action: str, args: dict, ctx) -> str:
            self.log.append((action, dict(args)))
            if action == "fail":
                raise ToolError("не вышло")
            if action == "big":
                return "x" * 20000
            return f"сделано: {args.get('text', '')}"

        self.tools.add(Tool("demo", "Демо", {"echo": A("Повторяю {text}", "read"), "fail": A("Ломаю"),
                                         "big": A("Много", "read"), "wipe": A("Стираю {text}", "danger")},
                            {"text": S("текст")}, handler))


def tool_messages(call: dict) -> list[dict]:
    return [m for m in call["messages"] if m.get("role") == "tool"]


class AgentTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.events: list[tuple[str, object]] = []
        self.sink = lambda event, data, binary: self.events.append((event, data))
        bus.subscribe(self.sink)

    def tearDown(self) -> None:
        bus.unsubscribe(self.sink)

    def names(self) -> list[str]:
        return [e for e, _ in self.events]

    async def test_tool_flow_speech_and_events(self) -> None:
        router = FakeRouter([comp(calls=[("demo", {"action": "echo", "text": "привет"})]),
                             comp("Готово, повторил **привет**.\n\nПодробности: [ссылка](https://x.y)")])
        app = App(router)
        agent = Agent(app)
        result = await agent.ask_wait("повтори привет")
        self.assertTrue(result["ok"])
        self.assertEqual(app.log, [("echo", {"action": "echo", "text": "привет"})])
        self.assertEqual(app.tts.said, ["Готово, повторил привет."])
        second = router.calls[1]["messages"]
        self.assertEqual(second[0]["role"], "system")
        self.assertIn("telegram: Сообщения в Telegram", second[0]["content"])
        self.assertIn("[Контекст]", second[1]["content"])
        self.assertEqual(tool_messages(router.calls[1])[0]["content"], "сделано: привет")
        steps = [d for e, d in self.events if e == "agent.step"]
        self.assertEqual([s["status"] for s in steps], ["running", "ok"])
        self.assertEqual(steps[0]["label"], "Повторяю привет")
        self.assertIn("agent.message", self.names())
        self.assertEqual(agent.state, "idle")
        # system prompt is byte-stable between steps (prompt caching)
        self.assertEqual(router.calls[0]["messages"][0], second[0])
        # follow-up in the same chat sees the previous answer with its actions
        router.script = [comp("да")]
        await agent.ask_wait("а ещё?")
        history = router.calls[2]["messages"]
        self.assertEqual(history[1]["content"], "повтори привет")
        self.assertNotIn("[Действия", history[2]["content"])
        self.assertIn("demo.echo text=привет → ok", history[-1]["content"])  # journal is system context
        self.assertEqual(len(agent.messages()), 4)

    async def test_confirmation_denied_and_approved(self) -> None:
        for approve in (False, True):
            router = FakeRouter([comp(calls=[("demo", {"action": "wipe", "text": "всё"})]), comp("ок")])
            app = App(router, autonomy="danger")
            agent = Agent(app)
            await agent.ask("сотри всё")
            for _ in range(200):
                if agent.confirms:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(agent.state, "waiting")
            step_id = next(iter(agent.confirms))
            self.assertTrue(agent.confirm(step_id, approve))
            while agent.run is not None:
                await asyncio.sleep(0.01)
            if approve:
                self.assertEqual(app.log[0][0], "wipe")
            else:
                self.assertEqual(app.log, [])
                self.assertEqual(tool_messages(router.calls[1])[0]["content"], prompts.DENIED)

    async def test_read_actions_skip_confirmation_in_danger_mode(self) -> None:
        router = FakeRouter([comp(calls=[("demo", {"action": "echo", "text": "a"})]), comp("ок")])
        app = App(router, autonomy="danger")
        await Agent(app).ask_wait("x")
        self.assertEqual(len(app.log), 1)

    async def test_tool_error_and_bad_json(self) -> None:
        router = FakeRouter([comp(calls=[("demo", {"action": "fail"}), ("demo", "{oops"), ("nope", {"action": "x"}),
                                         ("demo", {"action": "zzz"})]), comp("не вышло")])
        app = App(router)
        result = await Agent(app).ask_wait("сломай")
        contents = [m["content"] for m in tool_messages(router.calls[1])]
        self.assertEqual(contents[0], "Ошибка: не вышло")
        self.assertIn("не JSON", contents[1])
        self.assertIn("нет инструмента «nope»", contents[2])
        self.assertIn("нет действия «zzz»", contents[3])
        self.assertEqual(result["text"], "не вышло")

    async def test_tool_call_failed_gets_hint(self) -> None:
        router = FakeRouter([ToolCallFailed("tool_use_failed", "{}"), comp("ответ")])
        result = await Agent(App(router)).ask_wait("x")
        self.assertEqual(result["text"], "ответ")
        self.assertIn("оформлен неверно", router.calls[1]["messages"][-1]["content"])

    async def test_context_too_long_compacts_and_retries(self) -> None:
        router = FakeRouter([comp(calls=[("demo", {"action": "big"})]), ContextTooLong("too big"), comp("готово")],
                            budget=100000)
        result = await Agent(App(router)).ask_wait("много")
        self.assertEqual(result["text"], "готово")
        self.assertEqual(len(tool_messages(router.calls[1])[0]["content"]), 3500)
        self.assertLessEqual(len(tool_messages(router.calls[2])[0]["content"]), 1200)

    async def test_budget_drops_old_history(self) -> None:
        router = FakeRouter([comp("раз")] * 1, budget=100000)
        app = App(router)
        agent = Agent(app)
        chat = agent.current_chat()
        for index in range(10):
            agent._save(chat, "user", f"вопрос {index} " + "слово " * 150)
            agent._save(chat, "assistant", f"ответ {index} " + "слово " * 150)
        router.budget = 1800
        router.script = [comp("ок")]
        await agent.ask_wait("новый вопрос")
        sent = router.calls[-1]["messages"]
        self.assertLess(len(sent), 22)
        self.assertIn("новый вопрос", sent[-1]["content"])
        self.assertEqual(sent[0]["role"], "system")

    async def test_step_limit_forces_final_answer(self) -> None:
        loop_call = comp(calls=[("demo", {"action": "echo", "text": "a"})])
        router = FakeRouter([loop_call, loop_call, comp("итог")])
        app = App(router)
        app.settings.set("agent.max_steps", 2)
        result = await Agent(app).ask_wait("цикл")
        self.assertEqual(len(app.log), 2)
        self.assertIsNone(router.calls[2]["tools"])
        self.assertEqual(router.calls[2]["messages"][-1]["content"], prompts.STEP_LIMIT_HINT)
        self.assertEqual(result["text"], "итог")

    async def test_repeated_action_hint(self) -> None:
        same = comp(calls=[("demo", {"action": "echo", "text": "a"})])
        router = FakeRouter([same, same, same, comp("стоп")])
        await Agent(App(router)).ask_wait("повторяй")
        self.assertIn("трижды", tool_messages(router.calls[3])[-1]["content"])

    async def test_stop_and_steering(self) -> None:
        router = FakeRouter([WAIT])
        app = App(router)
        agent = Agent(app)
        await agent.ask("долгая задача")
        queued = await agent.ask("и ещё вот это")
        self.assertTrue(queued["queued"])
        for _ in range(100):
            if router.calls:
                break
            await asyncio.sleep(0.01)
        self.assertIn("и ещё вот это", router.calls[0]["messages"][-1]["content"])
        stopped = await agent.ask("стоп")
        self.assertTrue(stopped["stopped"])
        for _ in range(100):
            if agent.run is None:
                break
            await asyncio.sleep(0.01)
        self.assertIsNone(agent.run)
        self.assertEqual(agent.messages()[-1]["content"], "Остановлено.")

    async def test_quick_model_switch(self) -> None:
        router = FakeRouter([])
        result = await Agent(App(router)).ask_wait("Переключись на быструю модель")
        self.assertEqual(router.active, "groq:openai/gpt-oss-20b")
        self.assertEqual(router.calls, [])
        self.assertIn("gpt-oss-20b", result["text"])

    async def test_image_is_described_by_vision_model(self) -> None:
        router = FakeRouter([comp("на картинке кот"), comp("Это кот.")])
        result = await Agent(App(router)).ask_wait("что тут?", images=["data:image/png;base64,AAAA"])
        self.assertEqual(router.calls[0]["purpose"], "vision")
        self.assertIn("[Изображение 1: на картинке кот]", router.calls[1]["messages"][-1]["content"])
        self.assertEqual(result["text"], "Это кот.")


class HelpersTest(unittest.TestCase):
    def test_model_switch_regex(self) -> None:
        for text in ("переключись на gemini", "используй модель gpt", "Эйдос, смени модель на платную",
                     "переключись на платную"):
            self.assertTrue(MODEL_SWITCH_RE.search(text), text)
        for text in ("включи быструю музыку", "переключи трек", "смени обои", "открой сайт gpt"):
            self.assertFalse(MODEL_SWITCH_RE.search(text), text)

    def test_speech_part(self) -> None:
        self.assertEqual(speech_part("**Привет**, Антон! [тут](https://a.b)\n\n- пункт"), "Привет, Антон! тут")
        long = "Первое предложение. " * 20
        self.assertTrue(speech_part(long, 100).endswith("."))
        self.assertLessEqual(len(speech_part(long, 100)), 100)

    def test_memory(self) -> None:
        database = dbmod.DB(str(TMP / "memory.sqlite"))
        memory = Memory(database)
        memory.save("Пользователя зовут Антон", tags="profile")
        memory.save("Любимая музыка — джаз и лофай")
        memory.save("Рабочие проекты лежат в D:\\Projects")
        self.assertTrue(memory.save("Любимая музыка — джаз и лофай")["duplicate"])
        self.assertEqual(memory.count(), 3)
        self.assertIn("джаз", memory.search("какую музыку я люблю?")[0]["text"])
        self.assertIn("Projects", memory.search("ГДЕ МОИ ПРОЕКТЫ")[0]["text"])
        self.assertIn("джаз", memory._scan("музыка", 3)[0]["text"])
        context = memory.context("проекты")
        self.assertEqual(context[0], "Пользователя зовут Антон")
        self.assertEqual(len(context), 2)
        self.assertEqual(memory.search("и в на"), [])
        self.assertEqual(fts_query('проекты "xy'), '"проек"* OR "xy"*')
        first = memory.list()[0]["id"]
        self.assertTrue(memory.delete(first))
        self.assertEqual(memory.count(), 2)

    def test_skills(self) -> None:
        folder = TMP / "skills-test"
        folder.mkdir(exist_ok=True)
        (folder / "excel.md").write_text("# Excel\nРабота с таблицами\n\nШаг 1", encoding="utf-8")
        user = TMP / "skills-user"
        skills = Skills([(folder, True), (user, False)])
        self.assertEqual(skills.list()[0]["description"], "Работа с таблицами")
        self.assertIn("Шаг 1", skills.load("EXCEL"))
        with self.assertRaises(KeyError):
            skills.load("nothing")
        import core.skills as skills_mod
        old = skills_mod.USER_SKILLS
        skills_mod.USER_SKILLS = user
        try:
            path = skills.save("Отчёт по почте", "Как собрать отчёт", "1. Открой почту")
        finally:
            skills_mod.USER_SKILLS = old
        self.assertTrue(path.exists())
        self.assertIn("отчёт-по-почте: Как собрать отчёт", skills.prompt_list())
        self.assertTrue(skills.delete("отчёт-по-почте"))
        self.assertFalse(skills.delete("excel"))


if __name__ == "__main__":
    unittest.main()
