"""apps, system and island tools without Windows: launching, window waiting, sound, power, settings, timers."""
from __future__ import annotations

import asyncio
import pathlib
import platform
import subprocess
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace
from unittest import mock

from core import bus, win
from core.tools import Context, ToolError, apps, build_registry, island, system

TG = r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Telegram.lnk"
CODE = r"C:\Users\anton\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Visual Studio Code\Visual Studio Code.lnk"
CALC = "shell:AppsFolder\\Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"
ITEMS = [
    {"name": "Telegram", "target": TG, "kind": "shortcut"},
    {"name": "Калькулятор", "target": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App", "kind": "store"},
    {"name": "Visual Studio Code", "target": CODE, "kind": "shortcut"},
    {"name": "Яндекс Музыка", "target": "Yandex.Music_vfvw9svesycw6!App", "kind": "store"},
    {"name": "Блокнот", "target": r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Accessories\Блокнот.lnk",
     "kind": "shortcut"},
]
NEW = {TG: {"hwnd": 2001, "title": "Telegram", "process": "Telegram.exe"},
       CALC: {"hwnd": 2002, "title": "Калькулятор", "process": "ApplicationFrameHost.exe"},
       CODE: {"hwnd": 2003, "title": "Добро пожаловать - Visual Studio Code", "process": "Code.exe"}}


class AppsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.windows = [{"hwnd": 1001, "title": "Без имени — Блокнот", "process": "notepad.exe"}]
        self.started: list[tuple] = []

        def fake_start(target: str, arguments: str = "") -> None:
            if target == "неведомая":
                raise OSError("not found")
            self.started.append((target, arguments))
            if target in NEW:
                self.windows.append(dict(NEW[target]))

        def fake_close(hwnd: int) -> None:
            self.windows[:] = [w for w in self.windows if w["hwnd"] != hwnd]

        self.close = mock.MagicMock(side_effect=fake_close)
        apps._cache.update(items=[], time=0.0)
        for patcher in (mock.patch.object(apps, "start", fake_start), mock.patch.object(apps, "WAIT", 0.6),
                        mock.patch.object(apps, "build_index", lambda: [dict(i) for i in ITEMS]),
                        mock.patch.multiple(win, list_windows=lambda: [dict(w) for w in self.windows], close=self.close)):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def tool(self, action: str, **args: object):
        return await apps.handler(action, args, Context())

    async def test_launch_by_spoken_name(self) -> None:
        self.assertEqual(await self.tool("launch", name="телеграм"), "запустил «Telegram» — окно 2001: «Telegram» (Telegram.exe)")
        self.assertEqual(self.started[-1], (TG, ""))
        self.assertIn("окно 2002", await self.tool("launch", name="Калькулятор"))
        self.assertEqual(self.started[-1][0], CALC)
        self.assertIn("«Visual Studio Code» — окно 2003", await self.tool("launch", name="вс код", args="--new-window"))
        self.assertEqual(self.started[-1], (CODE, "--new-window"))
        self.assertEqual(await self.tool("launch", name="блокнот"),
                         "«Блокнот» уже открыт — окно 1001: «Без имени — Блокнот» (notepad.exe)")
        self.assertEqual(await self.tool("launch", name="https://ya.ru"), "открыл https://ya.ru")
        with self.assertRaises(ToolError):
            await self.tool("launch", name="неведомая")

    async def test_search_list_close_kill_open(self) -> None:
        self.assertEqual(await self.tool("search", name="яндекс"), "Яндекс Музыка (Store)")
        self.assertEqual(await self.tool("list"), "notepad.exe: «Без имени — Блокнот» (1001)")
        self.assertEqual(await self.tool("close", name="блокнот"), "закрыл окон: 1 из 1")
        self.close.assert_called_once_with(1001)
        with self.assertRaises(ToolError):
            await self.tool("close", name="блокнот")
        self.windows.append({"hwnd": 1005, "title": "Документ", "process": "WINWORD.EXE"})
        done = subprocess.CompletedProcess([], 0, "SUCCESS: процесс завершён", "")
        with mock.patch.object(apps, "run_hidden", return_value=done) as run:
            self.assertEqual(await self.tool("kill", name="ворд"), "SUCCESS: процесс завершён")
        run.assert_called_once_with(["taskkill", "/F", "/T", "/IM", "WINWORD.EXE"], 20)
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "отчёт.txt"
            path.write_text("x", encoding="utf-8")
            self.assertTrue((await self.tool("open", path=str(path))).endswith("отчёт.txt"))
            self.assertTrue(self.started[-1][0].endswith("отчёт.txt"))
        self.assertEqual(await self.tool("open", path="ya.ru"), "открыл https://ya.ru")
        self.assertEqual(self.started[-1], ("https://ya.ru", ""))
        with self.assertRaises(ToolError):
            await self.tool("open", path="/нет/такого/файла.docx")
        self.assertEqual(await self.tool("refresh"), "программ в списке: 5")


class FakeVolume:
    def __init__(self) -> None:
        self.level, self.muted = 0.4, 0

    def GetMasterVolumeLevelScalar(self) -> float:
        return self.level

    def SetMasterVolumeLevelScalar(self, value: float, ctx: object) -> None:
        self.level = value

    def GetMute(self) -> int:
        return self.muted

    def SetMute(self, value: int, ctx: object) -> None:
        self.muted = value


class SystemTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.hotkey = mock.MagicMock()
        patcher = mock.patch.multiple(win, hotkey=self.hotkey, lock=mock.DEFAULT, set_clipboard=mock.DEFAULT,
                                      get_clipboard=lambda: "из буфера")
        self.win = patcher.start()
        self.addCleanup(patcher.stop)

    async def tool(self, action: str, **args: object):
        return await system.handler(action, args, Context())

    async def test_volume_and_media(self) -> None:
        with mock.patch.dict(sys.modules, {"pycaw": None, "pycaw.pycaw": None}):
            self.assertEqual(await self.tool("volume", level=50), "громкость ≈50%")
        self.hotkey.assert_any_call("volume_down", 50)
        self.hotkey.assert_called_with("volume_up", 25)
        volume = FakeVolume()
        package, module = types.ModuleType("pycaw"), types.ModuleType("pycaw.pycaw")
        module.AudioUtilities = SimpleNamespace(GetSpeakers=lambda: SimpleNamespace(EndpointVolume=volume))
        module.IAudioEndpointVolume = object
        with mock.patch.dict(sys.modules, {"pycaw": package, "pycaw.pycaw": module}):
            self.assertEqual(await self.tool("volume"), "громкость 40%")
            self.assertEqual(await self.tool("volume", change=-15), "громкость 25%")
            self.assertEqual(await self.tool("volume", mute=True), "громкость 25%, звук выключен")
            self.assertEqual(await self.tool("volume", level=70), "громкость 70%")
        self.assertEqual(await self.tool("media", command="дальше"), "следующий трек")
        self.hotkey.assert_called_with("media_next")
        self.assertEqual(await self.tool("media"), "пауза / воспроизведение")
        with self.assertRaises(ToolError):
            await self.tool("media", command="громче")

    async def test_brightness_power_clipboard(self) -> None:
        replies = iter([(0, "", ""), (0, "55\n", "")])
        with mock.patch.object(system, "powershell", side_effect=lambda script, timeout=30: next(replies)) as ps:
            self.assertEqual(await self.tool("brightness", level=55), "яркость 55%")
        self.assertIn("Brightness=[byte]55", ps.call_args_list[0].args[0])
        with mock.patch.object(system, "powershell", return_value=(1, "", "нет WMI")):
            with self.assertRaises(ToolError):
                await self.tool("brightness")
        with mock.patch.object(system, "run_hidden", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            self.assertEqual(await self.tool("shutdown", delay=60), "выключение через 60 с — отменить: system cancel_shutdown")
            run.assert_called_with(["shutdown", "/s", "/t", "60"], 20)
            await self.tool("restart")
            run.assert_called_with(["shutdown", "/r", "/t", "30"], 20)
        with mock.patch.object(system, "run_hidden", return_value=subprocess.CompletedProcess([], 1116, "", "")):
            self.assertEqual(await self.tool("cancel_shutdown"), "запланированного выключения нет")
        self.assertEqual(await self.tool("lock"), "компьютер заблокирован")
        self.win["lock"].assert_called_once_with()
        self.assertEqual(await self.tool("clipboard_get"), "из буфера")
        self.assertEqual(await self.tool("clipboard_set", text="привет"), "скопировал в буфер 6 симв.")
        self.win["set_clipboard"].assert_called_once_with("привет")

    def test_os_name_uses_build_number(self) -> None:
        with mock.patch.object(system.win, "IS_WINDOWS", True), \
                mock.patch.object(system.sys, "getwindowsversion", return_value=SimpleNamespace(build=22631)):
            self.assertEqual(system.os_name(), "Windows 11")
        with mock.patch.object(system.win, "IS_WINDOWS", True), \
                mock.patch.object(system.sys, "getwindowsversion", return_value=SimpleNamespace(build=19045)):
            self.assertEqual(system.os_name(), "Windows 10")
        with mock.patch.object(system.win, "IS_WINDOWS", False):
            self.assertEqual(system.os_name(), platform.system())

    async def test_info_processes_settings(self) -> None:
        csv_text = '"chrome.exe","1234","Console","1","250 000 K"\r\n"notepad.exe","4321","Console","1","12 345 K"\r\n'
        with mock.patch.object(system, "_psutil", lambda: None):
            info = await self.tool("info")
            self.assertIn("Компьютер:", info)
            self.assertRegex(info, r"Диск .+: свободно")
            with mock.patch.object(system, "run_hidden", return_value=subprocess.CompletedProcess([], 0, csv_text, "")):
                self.assertEqual((await self.tool("processes", limit=5)).splitlines()[0], "chrome.exe (PID 1234): 244.1 МБ")
                self.assertEqual(await self.tool("processes", name="note"), "notepad.exe (PID 4321): 12.1 МБ")
        with mock.patch.object(system, "start") as start:
            self.assertEqual(await self.tool("settings", page="Блютуз"), "открыл параметры: Блютуз")
            start.assert_called_with("ms-settings:bluetooth")
            await self.tool("settings", page="network-proxy")
            start.assert_called_with("ms-settings:network-proxy")
            await self.tool("settings")
            start.assert_called_with("ms-settings:")
            with self.assertRaises(ToolError):
                await self.tool("settings", page="что-то странное")
        if not win.IS_WINDOWS:
            with self.assertRaises(ToolError):
                await self.tool("theme", dark=True)
            with self.assertRaises(ToolError):
                await self.tool("radio", kind="блютуз", on=False)
        with self.assertRaises(ToolError):
            await self.tool("radio", kind="nfc")
        self.assertTrue(await self.tool("network"))

    def test_select_budget(self) -> None:
        with mock.patch.object(win, "IS_WINDOWS", True):
            reg = build_registry(None, ["computer", "windows", "apps", "files", "web", "system", "island", "shell"])
            picked = [s["function"]["name"] for s in reg.select("сделай погромче и поставь таймер на 5 минут", (), 1200)]
            self.assertIn("system", picked)
            self.assertIn("island", picked)
            self.assertEqual(picked[-1], "tools")
            only = reg.select("", ["shell"], 10)
            self.assertEqual([s["function"]["name"] for s in only], ["shell", "tools"])
            self.assertIn("computer — мышь", only[-1]["function"]["description"])
            everything = [s["function"]["name"] for s in reg.select("", (), 100000)]
            self.assertEqual(sorted(everything), sorted(reg.names()))

    def test_registry(self) -> None:
        with mock.patch.object(win, "IS_WINDOWS", True):
            reg = build_registry(None, ["apps", "system", "island"])
            self.assertEqual(sorted(reg.names()), ["apps", "island", "system"])
        self.assertEqual(reg.kind("system", "shutdown"), "danger")
        self.assertEqual(reg.kind("apps", "kill"), "danger")
        self.assertEqual(reg.kind("island", "timers"), "read")
        self.assertFalse(reg.needs_confirm("system", "shutdown", "full"))
        self.assertTrue(reg.needs_confirm("system", "shutdown", "danger"))
        self.assertEqual(reg.label("apps", "launch", {"name": "Telegram"}), "Запускаю Telegram")


class IslandTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.events: list[tuple] = []
        patcher = mock.patch.object(bus, "emit", side_effect=lambda event, data=None, binary=None:
                                    self.events.append((event, data)))
        patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self) -> None:
        for timer_id in list(island.TIMERS):
            island.cancel_timer(timer_id)

    def test_parse_duration(self) -> None:
        cases = {"5 минут": 300, "1 час 20 минут": 4800, "90 сек": 90, "1:30": 90, "1:30:00": 5400, "полчаса": 1800,
                 "2,5 мин": 150, "10 s": 10, "2h": 7200}
        for text, seconds in cases.items():
            self.assertEqual(island.parse_duration(text), seconds, text)
        self.assertIsNone(island.parse_duration("когда-нибудь"))
        self.assertEqual(island.clock(4800), "1:20:00")
        self.assertEqual(island.clock(59.6), "1:00")

    async def test_timer_lifecycle(self) -> None:
        said: list[str] = []

        async def say(text: str) -> None:
            said.append(text)

        ctx = Context(app=SimpleNamespace(tts=SimpleNamespace(say=say)))
        reply = await island.handler("timer", {"seconds": 1, "label": "чай"}, ctx)
        self.assertTrue(reply.startswith("таймер «чай» на 0:01 запущен (timer-"))
        self.assertEqual((self.events[-1][0], self.events[-1][1]["state"]), ("island.timer", "running"))
        self.assertIn("осталось 0:01", await island.handler("timers", {}, ctx))
        await asyncio.sleep(1.3)
        self.assertEqual(island.TIMERS, {})
        seen = [(event, data.get("state") or data.get("title")) for event, data in self.events]
        self.assertIn(("island.timer", "done"), seen)
        self.assertIn(("island.card", "Таймер"), seen)
        self.assertEqual(said, ["Таймер «чай»: время вышло."])
        self.assertEqual(await island.handler("timers", {}, ctx), "таймеров нет")

    async def test_cancel_and_cards(self) -> None:
        ctx = Context()
        await island.handler("timer", {"duration": "10 минут", "label": "паста"}, ctx)
        await island.handler("timer", {"duration": "1:30"}, ctx)
        with self.assertRaises(ToolError):
            await island.handler("cancel_timer", {}, ctx)
        self.assertEqual(await island.handler("cancel_timer", {"id": "паст"}, ctx), "таймер отменён")
        self.assertEqual(len(island.TIMERS), 1)
        self.assertEqual(await island.handler("cancel_timer", {"id": "all"}, ctx), "отменил таймеров: 1")
        self.assertIn(("island.timer", "cancelled"), [(e, d.get("state")) for e, d in self.events])
        with self.assertRaises(ToolError):
            await island.handler("timer", {"duration": "никогда"}, ctx)
        with self.assertRaises(ToolError):
            await island.handler("timer", {"seconds": 0.2}, ctx)
        reply = await island.handler("card", {"title": "Готово", "text": "Файл сохранён", "kind": "странный"}, ctx)
        self.assertEqual(reply, "показал карточку «Готово»")
        event, data = self.events[-1]
        self.assertEqual((event, data["title"], data["kind"], data["timeout"]), ("island.card", "Готово", "info", 8.0))
        await island.handler("clear", {}, ctx)
        self.assertEqual(self.events[-1], ("island.clear", {}))


if __name__ == "__main__":
    unittest.main()
