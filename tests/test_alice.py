"""Инструмент alice: композиция запроса, выбор ответа и пошаговый диалог на поддельной странице.

Живой Яндекс не нужен: страница-заглушка воспроизводит только то, на что опирается код, — поле ввода,
блоки ответа и кнопку «Стоп» во время генерации.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import tempfile
import unittest

TMP = pathlib.Path(tempfile.mkdtemp(prefix="idos-alice-"))
os.environ.setdefault("IDOS_HOME", str(TMP))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from core.tools import Context, ToolError, build_registry  # noqa: E402
from core.tools import alice  # noqa: E402
from unittest import mock  # noqa: E402


class FakeSettings:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key, default=None):
        return self.values.get(key, default)


class App:
    def __init__(self, **settings):
        self.settings = FakeSettings(settings)


class FakeNode:
    def __init__(self, text):
        self.text = text

    async def inner_text(self):
        return self.text


class FakeKeyboard:
    def __init__(self, page):
        self.page = page

    async def press(self, key):
        if key == "Enter":
            self.page.submitted = True

    async def type(self, text, **kwargs):
        self.page.typed = text


class FakeLocator:
    """Поле ввода, блоки ответа и кнопка «Стоп»: гаснет после первого опроса, как конец генерации."""

    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    @property
    def first(self):
        return self

    async def count(self):
        if "stop" in self.selector.lower():
            self.page.polls += 1
            return 1 if self.page.polls <= 1 else 0
        if self.selector == self.page.answer_selector:
            self.page.polls += 1
            return len(self.page.messages)
        return 1

    async def nth(self, index):
        return FakeNode(self.page.messages[index])

    async def wait_for(self, **kwargs):
        if self.selector != self.page.answer_selector and not self.page.has_field:
            raise RuntimeError("нет поля ввода")
        return None

    async def fill(self, value, **kwargs):
        self.page.typed = value

    async def click(self, **kwargs):
        return None


class FakePage:
    answer_selector = alice.DEFAULT_ANSWER

    def __init__(self, messages=None):
        self.messages = list(messages or [])
        self.typed = ""
        self.submitted = False
        self.polls = 0
        self.has_field = True
        self.url = alice.DEFAULT_URL
        self.keyboard = FakeKeyboard(self)

    def locator(self, selector):
        return FakeLocator(self, selector)

    async def evaluate(self, script, *args):
        return self.url

    async def bring_to_front(self):
        return None

    def is_closed(self):
        return False

    async def goto(self, url, **kwargs):
        self.url = url
        return None

    async def screenshot(self, **kwargs):
        return b"\xff\xd8\xff\x00"


class FakeContext:
    def __init__(self, page):
        self.page = page

    async def new_page(self):
        return self.page


class FakeManager:
    def __init__(self, page):
        self.page = page
        self.context = FakeContext(page)
        self.lock = asyncio.Lock()

    def pages(self):
        return [self.page]

    async def ensure(self):
        return None

    async def new_page(self):
        return self.page


def run(coro):
    return asyncio.run(coro)


class ComposeTest(unittest.TestCase):
    def test_home_address_is_used(self):
        question = "где ближайшая булочная?"
        plain = alice.build_query(App(), question)
        self.assertTrue(plain.startswith(question))
        self.assertIn("Отвечай максимально кратко и лаконично", plain)
        composed = alice.build_query(App(**{"alice.home_address": "Москва, Тверская 1"}), question)
        self.assertIn("Москва, Тверская 1", composed)
        self.assertIn(question, composed)
        self.assertTrue(composed.endswith("Отвечай максимально кратко и лаконично."))

    def test_brevity_suffix_is_always_appended(self):
        out = alice.build_query(App(), "погода")
        self.assertTrue(out.endswith("Отвечай максимально кратко и лаконично."))
        self.assertFalse(out.startswith("Пользователь живёт"))

    def test_pick_answer_prefers_the_question_echo(self):
        texts = ["Привет! Чем помочь?", "Скопировать", "Ближайшая булочная на Тверской, 300 метров."]
        self.assertEqual(alice.pick_answer(texts, "где ближайшая булочная?"),
                         "Ближайшая булочная на Тверской, 300 метров.")
        self.assertEqual(alice.pick_answer(["Спросила: где булочная? Ответ ниже...", "коротко"], "где булочная?"),
                         "Спросила: где булочная? Ответ ниже...")

    def test_pick_answer_skips_noise_only(self):
        self.assertEqual(alice.pick_answer(["Скопировать", "Источники"], "вопрос"), "")
        self.assertEqual(alice.pick_answer([], "вопрос"), "")

    def test_clean_answer_drops_service_lines(self):
        self.assertEqual(alice.clean_answer("Ответ\n\nСкопировать\n\nИсточники\n\nЕщё"), "Ответ")

    def test_selectors_fall_back_to_defaults(self):
        state = alice.Alice(FakeSettings(), FakeManager(FakePage()))
        self.assertEqual(state.answer_selector(), alice.DEFAULT_ANSWER)
        self.assertEqual(state.input_selector(), alice.DEFAULT_INPUT)
        custom = alice.Alice(FakeSettings({"alice.answer_selector": ".reply"}), FakeManager(FakePage()))
        self.assertEqual(custom.answer_selector(), ".reply")


class AskFlowTest(unittest.TestCase):
    def make(self, **settings):
        page = FakePage()
        state = alice.Alice(FakeSettings(settings), FakeManager(page))
        return state, page

    def test_ask_types_and_returns_answer(self):
        state, page = self.make()

        async def read_messages(p, selector):
            return ["Привет! Чем помочь?", "Ближайшая булочная на Тверской, 300 метров."]

        saved = alice.read_messages
        alice.read_messages = read_messages
        try:
            answer = run(state.ask("где ближайшая булочная?", timeout=15))
        finally:
            alice.read_messages = saved
        self.assertEqual(answer, "Ближайшая булочная на Тверской, 300 метров.")
        self.assertEqual(page.typed, "где ближайшая булочная?")
        self.assertTrue(page.submitted)

    def test_ask_trims_history_of_previous_answers(self):
        state, page = self.make()

        async def read_messages(p, selector):
            return ["Привет! Чем помочь?", "Старый ответ про погоду, довольно длинный.", "Новый ответ на вопрос."]

        saved = alice.read_messages
        alice.read_messages = read_messages
        try:
            answer = run(state.ask("вопрос", timeout=15))
        finally:
            alice.read_messages = saved
        self.assertIn(answer, ("Старый ответ про погоду, довольно длинный.", "Новый ответ на вопрос."))

    def test_ask_without_field_raises(self):
        state, page = self.make()
        page.has_field = False
        with self.assertRaises(ToolError):
            run(state.ask("вопрос", timeout=5))

    def test_ask_returns_empty_when_nothing_appears(self):
        state, page = self.make()

        async def no_messages(p, selector):
            return []

        saved = alice.read_messages
        alice.read_messages = no_messages
        try:
            self.assertEqual(run(state.ask("вопрос", timeout=6)), "")
        finally:
            alice.read_messages = saved


class HandlerTest(unittest.TestCase):
    def test_empty_question_is_rejected(self):
        async def call():
            return await alice.handler("ask", {"question": "  "}, Context(app=App(), cancel=asyncio.Event()))

        with self.assertRaises(ToolError):
            run(call())

    def test_ask_closes_the_browser_afterwards(self):
        closed = []

        class Manager:
            def __init__(self):
                self.lock = asyncio.Lock()

            async def close(self):
                closed.append(True)

        async def fake_ask(self, question, timeout):
            return "ответ"

        async def call():
            with mock.patch.object(alice, "get_manager", return_value=Manager()), \
                    mock.patch.object(alice.Alice, "ask", fake_ask):
                return await alice.handler("ask", {"question": "где булочная?"},
                                           Context(app=App(), cancel=asyncio.Event()))

        self.assertEqual(run(call()), "ответ")
        self.assertEqual(closed, [True])


class RegistryTest(unittest.TestCase):
    def test_registered_and_read_only(self):
        reg = build_registry(App(), ["alice"])
        self.assertIn("alice", reg.names())
        self.assertEqual(reg.kind("alice", "ask"), "read")
        self.assertFalse(reg.needs_confirm("alice", "ask", "danger"))

    def test_disabled_tool_is_hidden(self):
        reg = build_registry(App(**{"alice.enabled": False}), ["alice"])
        self.assertNotIn("alice", reg._available())

    def test_hint_pulls_the_tool_into_the_step(self):
        reg = build_registry(App(), ["alice"])
        names = {schema["function"]["name"] for schema in reg.select("спроси у алисы, где булочная")}
        self.assertIn("alice", names)

    def test_alice_by_name_is_sticky(self):
        from core.agent_text import wants_alice

        self.assertTrue(wants_alice("Алиса, где ближайшая булочная?"))
        self.assertTrue(wants_alice("спроси у нейро про пробки"))
        self.assertFalse(wants_alice("найди погоду в интернете"))


if __name__ == "__main__":
    unittest.main()
