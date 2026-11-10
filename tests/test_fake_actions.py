"""Модель не должна «изображать» вызов инструмента текстом «[Действия: …]»."""
from __future__ import annotations

import unittest

from test_agent import App, FakeRouter, comp  # noqa: E402

from core import prompts
from core.agent import Agent, clean_reply
from core.tools import memory_tool


def make(script):
    router = FakeRouter(script)
    app = App(router)
    memory_tool.register(app.tools, app)
    return router, app, Agent(app)


def texts(call):
    return [str(m.get("content")) for m in call["messages"]]


class FakeActionTest(unittest.IsolatedAsyncioTestCase):
    async def test_fake_journal_triggers_real_call(self):
        router, app, agent = make([
            comp("Запомнил: ты в Петербурге. [Действия: memory.save tags=profile, text=СПб → ok]"),
            comp(calls=[("memory", {"action": "save", "text": "Пользователь в Санкт-Петербурге", "tags": "profile"})]),
            comp("Запомнил, ты в Санкт-Петербурге."),
        ])
        result = await agent.ask_wait("Запомни что я в Санкт-Петербурге")
        self.assertIn(prompts.FAKE_ACTION_HINT, texts(router.calls[1]))
        rows = app.memory.list(10)
        self.assertEqual(len(rows), 1)
        self.assertIn("Санкт-Петербурге", rows[0]["text"])
        self.assertNotIn("[Действия", result["text"])

    async def test_claim_without_call_falls_back_to_memory(self):
        router, app, agent = make([comp("Запомнил: ты в Питере."), comp("Запомнил: ты в Питере.")])
        result = await agent.ask_wait("Эйдос, запомни, что я в Санкт-Петербурге")
        self.assertIn(prompts.CLAIM_HINT, texts(router.calls[1]))
        rows = app.memory.list(10)
        self.assertEqual(len(rows), 1)
        self.assertIn("я в Санкт-Петербурге", rows[0]["text"])
        self.assertEqual(result["text"], "Запомнил: ты в Питере.")

    async def test_plain_answer_is_untouched(self):
        router, app, agent = make([comp("Всё хорошо, спасибо!")])
        result = await agent.ask_wait("как дела?")
        self.assertEqual(len(router.calls), 1)
        self.assertEqual(result["text"], "Всё хорошо, спасибо!")
        self.assertEqual(app.memory.list(10), [])

    async def test_history_has_no_journal_in_assistant_text(self):
        router, app, agent = make([comp(calls=[("demo", {"action": "echo", "text": "а"})]), comp("готово"), comp("да")])
        await agent.ask_wait("повтори а")
        await agent.ask_wait("ещё?")
        msgs = router.calls[2]["messages"]
        assistant = [m for m in msgs if m["role"] == "assistant"]
        self.assertTrue(all("→ ok" not in str(m["content"]) for m in assistant))
        self.assertIn("Журнал системы", msgs[-1]["content"])

    def test_clean_reply(self):
        self.assertEqual(clean_reply("Готово. [Действия: apps.launch name=x → ok]"), "Готово.")
        self.assertEqual(clean_reply("Сайт example.com работает"), "Сайт example.com работает")


if __name__ == "__main__":
    unittest.main()
