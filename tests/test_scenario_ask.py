"""Блоки «Спросить пользователя» и «Свернуть / закрыть программу», роутинг ответа через агента."""
from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import tempfile
import types
import unittest

TMP = pathlib.Path(tempfile.mkdtemp(prefix="idos-ask-"))
os.environ.setdefault("IDOS_HOME", str(TMP))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from core import scenarios as sc  # noqa: E402
from core.scenarios import Scenarios, pick_choice, split_choices, var_name  # noqa: E402
from core.tools.scenario import parse_vars  # noqa: E402
from tests.test_scenarios import FakeDriver, chain  # noqa: E402


class WinFakeDriver(FakeDriver):
    async def window(self, app, action):
        if app == "нет":
            raise sc.StepError("не нашёл окон")
        self.calls.append(("window", app, action))
        return f"{action}: {app}"


class AskTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.dir = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        self.driver = WinFakeDriver()
        self.events: list[tuple[str, dict]] = []
        self.s = Scenarios(folder=self.dir, driver=self.driver)
        self.s.engine.emit = lambda e, d: self.events.append((e, d))
        self.s.engine.extractor = None

    async def _answer_when_asked(self, text, skip=False):
        for _ in range(200):
            if self.s.engine.question():
                return self.s.engine.answer(text, skip=skip)
            await asyncio.sleep(0.01)
        raise AssertionError("вопрос так и не задан")

    async def test_answer_goes_to_variable(self) -> None:
        scn = chain(("ask", {"question": "Что включить?", "var": "исполнитель", "timeout": 10}),
                    ("type", {"text": "{исполнитель}", "enter": True}), ("say", {"text": "Включаю {исполнитель}"}))
        run = asyncio.create_task(self.s.run(scn))
        self.assertTrue(await self._answer_when_asked("Кино"))
        result = await run
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.driver.calls[0], ("say", "Что включить?"))
        self.assertIn(("say", "Включаю Кино"), self.driver.calls)
        self.assertTrue(any(c[0] == "type" for c in self.driver.calls))
        asks = [d for e, d in self.events if e == "scenario.ask"]
        self.assertEqual(asks[0]["var"], "исполнитель")
        self.assertEqual(asks[-1]["state"], "done")
        self.assertIsNone(self.s.engine.question())

    async def test_text_mode_does_not_speak(self) -> None:
        scn = chain(("ask", {"question": "Город?", "var": "city", "mode": "text", "timeout": 10}),
                    ("say", {"text": "{city}"}))
        run = asyncio.create_task(self.s.run(scn))
        await self._answer_when_asked("Казань")
        await run
        self.assertEqual(self.driver.calls, [("say", "Казань")])

    async def test_prefilled_value_skips_question(self) -> None:
        scn = chain(("ask", {"question": "Город?", "var": "city"}), ("say", {"text": "{city}"}))
        result = await self.s.run(scn, {"city": "Москва"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.driver.calls, [("say", "Москва")])
        self.assertFalse([e for e, _ in self.events if e == "scenario.ask"])

    async def test_timeout_uses_default_or_fail_link(self) -> None:
        scn = chain(("ask", {"question": "?", "var": "x", "default": "джаз", "timeout": 5, "mode": "text"}),
                    ("say", {"text": "{x}"}))
        run = asyncio.create_task(self.s.run(scn))
        await self._answer_when_asked("", skip=True)
        self.assertTrue((await run)["ok"])
        self.assertEqual(self.driver.calls, [("say", "джаз")])
        self.driver.calls.clear()
        scn = {"id": "f", "name": "f", "nodes": [
            {"id": "start", "type": "start", "links": {"next": "a"}},
            {"id": "a", "type": "ask", "params": {"question": "?", "mode": "text", "timeout": 5},
             "links": {"fail": "b"}},
            {"id": "b", "type": "say", "params": {"text": "не дождался"}, "links": {}}]}
        run = asyncio.create_task(self.s.run(scn))
        await self._answer_when_asked("", skip=True)
        self.assertTrue((await run)["ok"])
        self.assertEqual(self.driver.calls, [("say", "не дождался")])

    async def test_choices(self) -> None:
        self.assertEqual(split_choices("рок, джаз; классика"), ["рок", "джаз", "классика"])
        ch = ["рок", "джаз", "классика"]
        self.assertEqual(pick_choice("давай джаз", ch), "джаз")
        self.assertEqual(pick_choice("второй", ch), "джаз")
        self.assertEqual(pick_choice("3", ch), "классика")
        self.assertIsNone(pick_choice("рэп пожалуй", ch))
        scn = chain(("ask", {"question": "?", "var": "g", "choices": "рок, джаз", "mode": "text", "timeout": 5}))
        run = asyncio.create_task(self.s.run(scn))
        await self._answer_when_asked("рэп")
        result = await run
        self.assertFalse(result["ok"])

    async def test_extractor_used_only_with_hint(self) -> None:
        seen = []

        async def fake(question, answer, hint):
            seen.append(hint)
            return "«07:00»"
        self.s.engine.extractor = fake
        scn = chain(("ask", {"question": "Когда?", "var": "t", "extract": "время ЧЧ:ММ", "mode": "text", "timeout": 5}),
                    ("say", {"text": "{t}"}))
        run = asyncio.create_task(self.s.run(scn))
        await self._answer_when_asked("в семь утра")
        await run
        self.assertEqual(self.driver.calls, [("say", "07:00")])
        self.assertEqual(seen, ["время ЧЧ:ММ"])

    async def test_stop_while_waiting(self) -> None:
        scn = chain(("ask", {"question": "?", "mode": "text", "timeout": 60}), ("say", {"text": "нет"}))
        run = asyncio.create_task(self.s.run(scn))
        for _ in range(200):
            if self.s.engine.question():
                break
            await asyncio.sleep(0.01)
        self.assertTrue(self.s.engine.stop())
        result = await asyncio.wait_for(run, 3)
        self.assertFalse(result["ok"])
        self.assertEqual(self.driver.calls, [])
        self.assertIsNone(self.s.engine.pending)

    async def test_agent_routes_reply_to_scenario(self) -> None:
        from core.agent import Agent
        scn = chain(("ask", {"question": "?", "var": "x", "mode": "text", "timeout": 10}), ("say", {"text": "{x}"}))
        run = asyncio.create_task(self.s.run(scn))
        for _ in range(200):
            if self.s.engine.question():
                break
            await asyncio.sleep(0.01)
        fake = types.SimpleNamespace(app=types.SimpleNamespace(scenarios=self.s), run=None)
        reply = await Agent.ask(fake, "бах", source="voice")
        self.assertTrue(reply["answered"])
        await run
        self.assertEqual(self.driver.calls, [("say", "бах")])

    async def test_window_block(self) -> None:
        scn = {"id": "w", "name": "w", "nodes": [
            {"id": "start", "type": "start", "links": {"next": "a"}},
            {"id": "a", "type": "window", "params": {"app": "Яндекс Музыка", "action": "minimize"}, "links": {"next": "b"}},
            {"id": "b", "type": "window", "params": {"app": "нет", "action": "close"}, "links": {"fail": "c"}},
            {"id": "c", "type": "window", "params": {"app": "", "action": "kill"}, "links": {}}]}
        result = await self.s.run(scn)
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.driver.calls, [("window", "Яндекс Музыка", "minimize"), ("window", "", "kill")])
        bad = await self.s.run(chain(("window", {"app": "x", "action": "explode"})))
        self.assertFalse(bad["ok"])
        self.assertIn("window", sc.Scenarios.blocks(self.s)["blocks"])

    def test_helpers(self) -> None:
        self.assertEqual(var_name(" мой город! "), "мой_город")
        self.assertEqual(var_name(""), "answer")
        self.assertEqual(parse_vars("город=Москва; время = 07:00"), {"город": "Москва", "время": "07:00"})
        self.assertEqual(parse_vars('{"x": "1"}'), {"x": "1"})
        self.assertEqual(parse_vars(None), {})
        scn = chain(("ask", {"question": "Город?", "var": "city", "choices": "а, б"}))
        self.assertEqual(self.s.questions(scn)[0]["var"], "city")
        self.assertIn("city", self.s.describe(scn))
        self.assertIn("{city}", sc.summary(scn["nodes"][1]))


class AnyModelHelpersTest(unittest.TestCase):
    def test_aliases_and_png(self) -> None:
        from core import providers
        from core.router import _square_png
        self.assertIn("nemotron-3-ultra", providers.alias_spec("переключись на немотрон") or "")
        self.assertIn("nano-omni", providers.alias_spec("омни") or "")
        self.assertTrue(_square_png((255, 0, 0)).startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
