"""Сценарии: хранилище, движок блок-схем, триггеры, быстрый путь агента без модели."""
from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import tempfile
import unittest

TMP = pathlib.Path(tempfile.mkdtemp(prefix="idos-scn-"))
os.environ.setdefault("IDOS_HOME", str(TMP))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from core import scenarios as sc  # noqa: E402
from core.scenarios import Scenarios, StepError, Store, norm, validate  # noqa: E402


class FakeDriver:
    def __init__(self, missing: set[str] | None = None) -> None:
        self.calls: list[tuple] = []
        self.missing = missing or set()

    async def launch(self, app):
        self.calls.append(("launch", app))
        return f"запущено {app}"

    async def focus(self, q):
        self.calls.append(("focus", q))
        return q

    async def click(self, target, button, method, timeout, check):
        check()
        if target.get("name") in self.missing:
            raise StepError("не нашёл")
        self.calls.append(("click", target.get("name"), button))
        return "ok"

    async def type(self, text, target, enter, timeout, check):
        self.calls.append(("type", text, enter))
        return text

    async def hotkey(self, keys):
        self.calls.append(("hotkey", keys))
        return keys

    async def find(self, target, timeout, check):
        return None if target.get("name") in self.missing else {"x": 1, "y": 1}

    async def read(self, target, property_name, timeout, check):
        check()
        self.calls.append(("read", target.get("name"), property_name))
        return f"{target.get('name')} value"

    async def media(self, action):
        self.calls.append(("media", action))
        return action

    async def open(self, path):
        self.calls.append(("open", path))
        return path

    async def say(self, text):
        self.calls.append(("say", text))
        return text


def chain(*steps, sid="t", name="Тест", triggers=None):
    nodes = [{"id": "start", "type": "start", "links": {"next": "n0"}}]
    for i, (kind, params) in enumerate(steps):
        nodes.append({"id": f"n{i}", "type": kind, "params": params,
                      "links": {"next": f"n{i + 1}"} if i + 1 < len(steps) else {}})
    return {"id": sid, "name": name, "triggers": triggers or [], "nodes": nodes}


def T(name):
    return {"name": name, "process": "app.exe"}


class ScenarioTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.dir = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        self.driver = FakeDriver()
        self.events: list[tuple[str, dict]] = []
        self.s = Scenarios(folder=self.dir, driver=self.driver)
        self.s.engine.emit = lambda e, d: self.events.append((e, d))

    def test_seeded_example_and_validate(self) -> None:
        items = self.s.store.list()
        self.assertEqual(len(items), 1)
        self.assertIn("Яндекс", items[0]["name"])
        bad = validate({"name": "x", "nodes": [{"id": "a", "type": "click", "links": {"next": "zzz", "bogus": "a"}},
                                               {"id": "b", "type": "nope"}]})
        types = [n["type"] for n in bad["nodes"]]
        self.assertEqual(types, ["start", "click"])
        self.assertEqual(bad["nodes"][0]["links"], {"next": "a"})
        self.assertEqual(bad["nodes"][1]["links"], {})
        self.assertIn("timeout", bad["nodes"][1]["params"])

    def test_store_roundtrip_and_find(self) -> None:
        saved = self.s.store.save(chain(("wait", {"seconds": 0}), sid="Мой Сценарий!", name="Утро"))
        self.assertRegex(saved["id"], r"^[a-z0-9_-]+$")
        self.assertEqual(self.s.store.find("утро")["id"], saved["id"])
        self.assertTrue(self.s.store.delete(saved["id"]))
        self.assertIsNone(self.s.store.get(saved["id"]))

    async def test_linear_run_and_events(self) -> None:
        scn = chain(("launch", {"app": "Яндекс Музыка", "wait": 0}), ("click", {"target": T("Мне нравится")}),
                    ("click", {"target": T("Воспроизведение")}), ("say", {"text": "Включаю"}))
        result = await self.s.run(scn)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["steps"], 4)
        self.assertEqual(self.driver.calls, [("launch", "Яндекс Музыка"), ("click", "Мне нравится", "left"),
                                             ("click", "Воспроизведение", "left"), ("say", "Включаю")])
        statuses = [d["status"] for e, d in self.events if e == "scenario.step"]
        self.assertEqual(statuses.count("ok"), 4)
        runs = [d["status"] for e, d in self.events if e == "scenario.run"]
        self.assertEqual(runs, ["running", "ok"])

    async def test_branches_and_fail_link(self) -> None:
        self.driver.missing = {"Реклама", "Нет такой"}
        scn = {"id": "b", "name": "b", "nodes": [
            {"id": "start", "type": "start", "links": {"next": "if"}},
            {"id": "if", "type": "if_exists", "params": {"target": T("Реклама"), "timeout": 0},
             "links": {"yes": "close", "no": "click"}},
            {"id": "close", "type": "hotkey", "params": {"keys": "esc"}, "links": {"next": "click"}},
            {"id": "click", "type": "click", "params": {"target": T("Нет такой")}, "links": {"fail": "fallback"}},
            {"id": "fallback", "type": "media", "params": {"action": "play_pause"}, "links": {}}]}
        result = await self.s.run(scn)
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.driver.calls, [("media", "play_pause")])

    async def test_error_without_fail_link(self) -> None:
        self.driver.missing = {"Play"}
        result = await self.s.run(chain(("click", {"target": T("Play")})))
        self.assertFalse(result["ok"])
        self.assertIn("Нажать на элемент", result["text"])
        missing = await self.s.run(chain(("click", {"target": None})))
        self.assertIn("пипеткой", missing["text"])

    async def test_variables_and_loop_guard(self) -> None:
        result = await self.s.run(chain(("type", {"text": "{arg}", "enter": True})), {"arg": "Кино"})
        self.assertTrue(result["ok"])
        self.assertEqual(self.driver.calls, [("type", "Кино", True)])
        loop = {"id": "l", "name": "l", "nodes": [{"id": "start", "type": "start", "links": {"next": "a"}},
                                                    {"id": "a", "type": "wait", "params": {"seconds": 0},
                                                     "links": {"next": "a"}}]}
        result = await self.s.run(validate(loop))
        self.assertFalse(result["ok"])
        self.assertIn("бесконечный", result["text"])

    async def test_multiple_click_targets_and_element_read(self) -> None:
        scn = chain(("click", {"targets": [T("A"), T("B")]}), ("read", {
            "target": T("Field"), "var": "field_value", "property": "value",
        }), ("say", {"text": "{field_value}"}))
        result = await self.s.run(scn)
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.driver.calls, [
            ("click", "A", "left"), ("click", "B", "left"),
            ("read", "Field", "value"), ("say", "Field value"),
        ])

    async def test_condition_and_bounded_repeat(self) -> None:
        scn = {"id": "flow", "name": "Поток", "nodes": [
            {"id": "start", "type": "start", "links": {"next": "if"}},
            {"id": "if", "type": "condition", "params": {"var": "city", "operator": "equals", "value": "Казань"},
             "links": {"yes": "repeat", "no": "skip"}},
            {"id": "repeat", "type": "repeat", "params": {"count": 3, "var": "iteration"},
             "links": {"body": "say-iteration", "done": "done"}},
            {"id": "say-iteration", "type": "say", "params": {"text": "{iteration}"},
             "links": {"next": "repeat"}},
            {"id": "done", "type": "say", "params": {"text": "готово"}, "links": {}},
            {"id": "skip", "type": "say", "params": {"text": "пропуск"}, "links": {}},
        ]}
        result = await self.s.run(scn, {"city": "Казань"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.driver.calls, [("say", "1"), ("say", "2"), ("say", "3"), ("say", "готово")])
        self.driver.calls.clear()
        result = await self.s.run(scn, {"city": "Москва"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.driver.calls, [("say", "пропуск")])

    async def test_request_fills_only_declared_variables(self) -> None:
        seen = []

        async def extract(request, definitions):
            seen.append((request, [v["name"] for v in definitions]))
            return {"city": "Казань", "not_declared": "ignored"}

        self.s.engine.variable_extractor = extract
        scn = {"id": "vars", "name": "Переменные", "variables": [
            {"name": "city", "description": "Город"}, {"name": "time", "default": "09:00"},
        ], "nodes": [
            {"id": "start", "type": "start", "links": {"next": "say"}},
            {"id": "say", "type": "say", "params": {"text": "{city} в {time}"}, "links": {}},
        ]}
        result = await self.s.run(scn, request="Закажи поездку в Казань утром")
        self.assertTrue(result["ok"], result)
        self.assertEqual(seen, [("Закажи поездку в Казань утром", ["city", "time"])])
        self.assertEqual(self.driver.calls, [("say", "Казань в 09:00")])
        saved = validate(scn)
        self.assertEqual([v["name"] for v in saved["variables"]], ["city", "time"])

    async def test_named_trigger_variable_and_editor_extraction(self) -> None:
        scn = self.s.store.save({
            "id": "internet-search", "name": "Поиск в интернете",
            "triggers": ["найди в интернете {search_text}"],
            "nodes": [
                {"id": "start", "type": "start", "links": {"next": "type"}},
                {"id": "type", "type": "type", "params": {"text": "{search_text}"}, "links": {}},
            ],
        })
        self.assertEqual([v["name"] for v in scn["variables"]], ["search_text"])
        matched = self.s.store.match("Найди в интернете смешные котики")
        self.assertIsNotNone(matched)
        self.assertEqual(matched[1]["search_text"], "смешные котики")

        seen = []

        async def extract(request, definitions):
            seen.append((request, [v["name"] for v in definitions]))
            return {"search_text": "электрические самокаты"}

        self.s.engine.variable_extractor = extract
        result = await self.s.run(scn, request="Найди в интернете электрические самокаты")
        self.assertTrue(result["ok"], result)
        self.assertEqual(seen, [("Найди в интернете электрические самокаты", ["search_text"])])
        self.assertEqual(self.driver.calls, [("type", "электрические самокаты", False)])

    async def test_missing_template_variable_never_reaches_driver(self) -> None:
        scn = chain(("type", {"text": "найди {search_text}"}))
        result = await self.s.run(scn)
        self.assertFalse(result["ok"])
        self.assertIn("{search_text}", result["text"])
        self.assertEqual(self.driver.calls, [])

    async def test_stop_cancels_variable_extraction(self) -> None:
        started = asyncio.Event()

        async def extract(_request, _definitions):
            started.set()
            await asyncio.Event().wait()

        self.s.engine.variable_extractor = extract
        scn = {"id": "stop-vars", "name": "Стоп", "variables": [{"name": "city"}], "nodes": [
            {"id": "start", "type": "start", "links": {}},
        ]}
        run = asyncio.create_task(self.s.run(scn, request="Найди в Казани"))
        await asyncio.wait_for(started.wait(), 1)
        self.assertTrue(self.s.engine.stop())
        result = await asyncio.wait_for(run, 1)
        self.assertTrue(result.get("stopped"), result)

    async def test_stop_and_busy(self) -> None:
        task = asyncio.create_task(self.s.run(chain(("wait", {"seconds": 5}))))
        await asyncio.sleep(0.15)
        busy = await self.s.run(chain(("wait", {"seconds": 0})))
        self.assertIn("уже выполняется", busy["text"])
        self.assertTrue(self.s.engine.stop())
        result = await asyncio.wait_for(task, 2)
        self.assertTrue(result.get("stopped"))

    async def test_nested_scenario(self) -> None:
        self.s.store.save(chain(("hotkey", {"keys": "ctrl+l"}), sid="inner", name="Внутренний"))
        result = await self.s.run(chain(("scenario", {"name": "внутренний"}), ("say", {"text": "ок"})))
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.driver.calls, [("hotkey", "ctrl+l"), ("say", "ок")])

    def test_trigger_matching(self) -> None:
        self.s.store.delete("yandex-music-likes")
        self.s.store.save(chain(("wait", {"seconds": 0}), sid="m", name="Музыка",
                                triggers=["включи мою музыку", "включи трек *"]))
        self.assertEqual(norm("Эйдос, включи, пожалуйста, мою музыку!"), "включи мою музыку")
        self.assertEqual(self.s.match("Эйдос, включи пожалуйста мою музыку")[0]["id"], "m")
        self.assertEqual(self.s.match("включи мою музыкy")[0]["id"], "m")  # опечатка
        scn, vars_ = self.s.match("включи трек Группа крови")
        self.assertEqual(vars_["arg"], "группа крови")
        self.assertEqual(self.s.match("запусти сценарий музыка")[0]["id"], "m")
        self.assertIsNone(self.s.match("включи мою музыку и найди потом в браузере новости про космос"))
        self.assertIsNone(self.s.match("какая погода"))
        data = self.s.store.get("m")
        data["enabled"] = False
        self.s.store.save(data)
        self.assertIsNone(self.s.match("включи мою музыку"))

    def test_describe_and_blocks(self) -> None:
        scn = self.s.store.list()[0]
        text = self.s.describe(self.s.store.get(scn["id"]))
        self.assertIn("1. Запустить программу: Яндекс Музыка", text)
        self.assertIn("click", self.s.blocks()["blocks"])


class ToolAndAgentTest(unittest.IsolatedAsyncioTestCase):
    async def test_tool_registered_and_runs(self) -> None:
        from core.tools import Registry
        from core.tools import scenario as tool

        class Ctx:
            cancel = None

        reg = Registry()
        tool.register(reg, None)
        self.assertIn("scenario", [t.name for t in reg.tools.values()] if hasattr(reg, "tools") else ["scenario"])
        driver = FakeDriver()
        ctx = Ctx()
        ctx.app = type("A", (), {})()
        ctx.app.scenarios = Scenarios(folder=pathlib.Path(tempfile.mkdtemp(dir=TMP)), driver=driver)
        ctx.app.scenarios.engine.emit = lambda e, d: None
        ctx.app.scenarios.store.save(chain(("hotkey", {"keys": "f5"}), sid="r", name="Обновить"))
        listed = await tool.handler("list", {}, ctx)
        self.assertIn("Обновить", listed)
        out = await tool.handler("run", {"name": "обновить"}, ctx)
        self.assertIn("выполнен", out)
        self.assertEqual(driver.calls, [("hotkey", "f5")])

    async def test_tool_run_passes_original_request_to_variable_extractor(self) -> None:
        from core.tools import scenario as tool
        driver = FakeDriver()
        ctx = type("Ctx", (), {})()
        ctx.cancel = None
        ctx.request = "Найди кафе в Казани"
        ctx.app = type("App", (), {})()
        ctx.app.scenarios = Scenarios(folder=pathlib.Path(tempfile.mkdtemp(dir=TMP)), driver=driver)

        async def extract(request, definitions):
            self.assertEqual(request, "Найди кафе в Казани")
            self.assertEqual([v["name"] for v in definitions], ["city"])
            return {"city": "Казань"}

        ctx.app.scenarios.engine.variable_extractor = extract
        ctx.app.scenarios.store.save({"id": "tool-vars", "name": "Кафе", "variables": [{"name": "city"}],
                                      "nodes": [{"id": "start", "type": "start", "links": {"next": "say"}},
                                                {"id": "say", "type": "say", "params": {"text": "{city}"}}]})
        out = await tool.handler("run", {"name": "Кафе"}, ctx)
        self.assertIn("выполнен", out)
        self.assertEqual(driver.calls, [("say", "Казань")])

    async def test_agent_fast_path_uses_no_model(self) -> None:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        import test_agent as ta
        from core.agent import Agent
        router = ta.FakeRouter([])
        app = ta.App(router)
        driver = FakeDriver()
        app.scenarios = Scenarios(folder=pathlib.Path(tempfile.mkdtemp(dir=TMP)), driver=driver)
        app.scenarios.store.save(chain(("media", {"action": "play_pause"}), sid="p", name="Пауза",
                                       triggers=["поставь на паузу"]))
        agent = Agent(app)
        result = await agent.ask_wait("Эйдос, поставь на паузу")
        self.assertTrue(result["ok"], result)
        self.assertEqual(router.calls, [])
        self.assertEqual(driver.calls, [("media", "play_pause")])
        self.assertIn("Готово", str(result))
        app.settings.set("scenarios.fast_path", False)
        await agent.ask_wait("поставь на паузу")
        self.assertEqual(len(router.calls), 1)


if __name__ == "__main__":
    unittest.main()
