"""Command bar, capture helpers, recorder command line, downloads watcher, coding-agent hooks and OBS auth."""
import asyncio
import base64
import hashlib
import json
import pathlib
import tempfile
import time
import unittest

from core import bus
from core.db import DB
from core.features import capture, commandbar, downloads, hooks, load_features, obs, recorder


class FakeSettings:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


class FakeAgent:
    def __init__(self):
        self.asked = []

    async def ask(self, text, source="text", **kw):
        self.asked.append((text, source))
        return {"ok": True}


class FakeApp:
    def __init__(self, values=None):
        self.settings = FakeSettings(values)
        self.db = DB(":memory:")
        self.tts = self
        self.spoken = []
        self.agent = FakeAgent()
        self.features = []

    async def say(self, text, interrupt=True, wait=False):
        self.spoken.append(text)
        return True

    def background(self, coro, name):
        return asyncio.get_running_loop().create_task(coro, name=name)


class Events:
    def __enter__(self):
        bus.reset()
        self.items = []
        bus.subscribe(lambda e, d, b: self.items.append((e, d)))
        return self

    def __exit__(self, *exc):
        bus.reset()

    def names(self):
        return [e for e, _ in self.items]


class CommandBarTest(unittest.TestCase):
    def test_calculator(self):
        self.assertEqual(commandbar.calculate("2+2*3"), 8)
        self.assertEqual(commandbar.calculate("15% от 300"), 45)
        self.assertEqual(commandbar.calculate("2^10"), 1024)
        self.assertEqual(commandbar.calculate("sqrt(16)"), 4)
        self.assertEqual(commandbar.calculate("1 200,5 / 2"), 600.25)
        self.assertIsNone(commandbar.calculate("привет"))
        self.assertIsNone(commandbar.calculate("2024"))
        self.assertIsNone(commandbar.calculate("__import__('os')"))
        self.assertIsNone(commandbar.calculate("9**99999"))
        self.assertEqual(commandbar.fmt_number(1234567), "1 234 567")
        self.assertEqual(commandbar.fmt_number(2.5), "2,5")

    def test_convert(self):
        value, unit = commandbar.convert("10 км в мили")
        self.assertAlmostEqual(value, 6.2137, places=3)
        value, unit = commandbar.convert("100 f в c")
        self.assertAlmostEqual(value, 37.78, places=1)
        self.assertEqual(unit, "°C")
        self.assertEqual(commandbar.convert("2 гб в мб")[0], 2048)
        self.assertIsNone(commandbar.convert("2 кг в км"))

    def test_query_and_ranking(self):
        async def run():
            app = FakeApp()
            bar = commandbar.CommandBar(app)
            items = await bar.query("2+2")
            self.assertEqual(items[0]["kind"], "calc")
            self.assertEqual(items[0]["action"], {"type": "copy", "text": "4"})
            items = await bar.query("таймер 5 мин")
            self.assertEqual(items[0]["kind"], "timer")
            items = await bar.query("ya.ru")
            self.assertEqual(items[0]["action"]["target"], "https://ya.ru")
            items = await bar.query("настрой bluetooth")
            self.assertIn("ms-settings:bluetooth", [i["action"].get("target") for i in items])
            items = await bar.query("что нового")
            self.assertEqual({i["kind"] for i in items}, {"ask", "web"})
            self.assertEqual(items[0]["kind"], "ask")
            for _ in range(5):
                bar.remember("web")
            items = await bar.query("что нового")
            self.assertEqual(items[0]["kind"], "web")  # picked often → ranked first
            with Events() as ev:
                result = await bar.run({"type": "ask", "text": "открой блокнот"}, "ask")
                await asyncio.sleep(0)
                self.assertTrue(result["ok"])
                self.assertIn("commandbar.ask", ev.names())
            self.assertEqual(app.agent.asked, [("открой блокнот", "commandbar")])
            recent = await bar.query("")
            self.assertEqual(recent[0]["title"], "открой блокнот")
            self.assertFalse((await bar.run({"type": "nope"}))["ok"])
        asyncio.run(run())


class CaptureTest(unittest.TestCase):
    def test_colors(self):
        self.assertEqual(capture.hex_color((255, 128, 0)), "#FF8000")
        f = capture.color_formats("#ff0000")
        self.assertEqual(f, {"hex": "#FF0000", "rgb": "rgb(255, 0, 0)", "hsl": "hsl(0, 100%, 50%)"})
        self.assertEqual(capture.color_formats("#808080")["hsl"], "hsl(0, 0%, 50%)")

    def test_names(self):
        import datetime

        name = capture.shot_name("shot", datetime.datetime(2026, 11, 9, 4, 5, 6))
        self.assertEqual(name, "shot-2026-11-09_04-05-06.png")
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "a.png"
            self.assertEqual(capture.unique(p), p)
            p.write_bytes(b"x")
            self.assertEqual(capture.unique(p).name, "a-2.png")

    def test_color_history(self):
        app = FakeApp()
        cap = capture.Capture(app)
        for h in ("#000000", "#FFFFFF", "#000000"):
            app.db.execute("INSERT INTO colors(hex, created) VALUES (?, ?)", (h, time.time()))
        self.assertEqual([c["hex"] for c in cap.colors()], ["#000000", "#FFFFFF"])


class RecorderTest(unittest.TestCase):
    def test_command(self):
        out = pathlib.Path("C:/v/rec.mp4")
        cmd = recorder.build_command("ffmpeg", out, 30, "high", {"x": 10, "y": 20, "width": 801, "height": 601})
        self.assertIn("gdigrab", cmd)
        self.assertEqual(cmd[cmd.index("-video_size") + 1], "800x600")
        self.assertEqual(cmd[cmd.index("-crf") + 1], "20")
        self.assertIn("+faststart", cmd)
        self.assertEqual(cmd[-1], str(out))
        cmd = recorder.build_command("ffmpeg", out, 120, window="Блокнот", mic="Mic", encoder="h264_nvenc")
        self.assertEqual(cmd[cmd.index("-framerate") + 1], "60")
        self.assertIn("title=Блокнот", cmd)
        self.assertIn("audio=Mic", cmd)
        self.assertIn("h264_nvenc", cmd)
        gif = recorder.build_command("ffmpeg", pathlib.Path("a.gif"))
        self.assertNotIn("libx264", gif)

    def test_state_idle(self):
        state = recorder.Recorder(FakeApp()).state()
        self.assertFalse(state["recording"])


class DownloadsTest(unittest.TestCase):
    def test_finished(self):
        pending = {}
        before = {"old.zip": (10, 1.0)}
        after = {"old.zip": (10, 1.0), "new.zip.crdownload": (5, 2.0)}
        self.assertEqual(downloads.finished(before, after, pending), [])
        after = {"old.zip": (10, 1.0), "new.zip": (50, 3.0)}
        self.assertEqual(downloads.finished(before, after, pending), [])  # first sighting: wait one poll
        self.assertEqual(downloads.finished(before, after, pending), ["new.zip"])
        self.assertEqual(pending, {})

    def test_growing_file(self):
        pending = {}
        self.assertEqual(downloads.finished({}, {"a.bin": (1, 1)}, pending), [])
        self.assertEqual(downloads.finished({}, {"a.bin": (9, 1)}, pending), [])
        self.assertEqual(downloads.finished({}, {"a.bin": (9, 1)}, pending), ["a.bin"])

    def test_snapshot_and_card(self):
        with tempfile.TemporaryDirectory() as tmp, Events() as ev:
            path = pathlib.Path(tmp) / "file.txt"
            path.write_text("hello")
            self.assertIn("file.txt", downloads.snapshot(pathlib.Path(tmp)))
            item = downloads.Downloads(FakeApp()).announce(path)
            self.assertEqual(item["size"], 5)
            self.assertIn("island.card", ev.names())


class HooksTest(unittest.TestCase):
    def test_describe(self):
        self.assertEqual(hooks.describe({"source": "claude", "event": "stop"}),
                         ("Claude Code", "закончил работу", "success"))
        title, text, kind = hooks.describe({"source": "claude", "event": "notification",
                                            "payload": {"message": "Claude needs your permission to use Bash"}})
        self.assertEqual(kind, "warning")
        self.assertIn("permission", text)
        title, text, kind = hooks.describe({"source": "codex", "event": "notify",
                                            "payload": {"type": "agent-turn-complete"}})
        self.assertEqual((title, kind), ("Codex", "success"))

    def test_spool(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmp, Events() as ev:
                spool = pathlib.Path(tmp)
                app = FakeApp({"integrations.claude_code": True})
                feature = hooks.Hooks(app, spool)
                hooks.write_event("claude", "stop", {}, spool)
                hooks.write_event("codex", "notify", {}, spool)  # codex disabled → ignored
                self.assertEqual(await feature.poll(), 2)
                self.assertEqual(list(spool.glob("*.json")), [])
                self.assertEqual(len(feature.events), 1)
                self.assertIn("hooks.event", ev.names())
                self.assertTrue(app.spoken)
        asyncio.run(run())

    def test_cli(self):
        from core import config, hook

        with tempfile.TemporaryDirectory() as tmp:
            old = config.DATA
            try:
                config.DATA = pathlib.Path(tmp)
                self.assertEqual(hook.main(["codex", "notify", json.dumps({"type": "agent-turn-complete"})]), 0)
                files = list((pathlib.Path(tmp) / "hooks").glob("*.json"))
                self.assertEqual(len(files), 1)
                self.assertEqual(json.loads(files[0].read_text(encoding="utf-8"))["payload"]["type"],
                                 "agent-turn-complete")
            finally:
                config.DATA = old

    def test_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = pathlib.Path(tmp) / "settings.json"
            settings.write_text(json.dumps({"model": "x", "hooks": {"Stop": [{"hooks": [{"command": "other"}]}]}}))
            hooks.install_claude(settings)
            hooks.install_claude(settings)  # idempotent
            data = json.loads(settings.read_text(encoding="utf-8"))
            self.assertEqual(data["model"], "x")
            self.assertEqual(len(data["hooks"]["Stop"]), 2)
            self.assertIn("core.hook", json.dumps(data["hooks"]["Notification"]))
            toml = pathlib.Path(tmp) / "config.toml"
            toml.write_text('model = "o4"\nnotify = ["old"]\n[mcp_servers.x]\ncommand = "y"\n')
            hooks.install_codex(toml)
            text = toml.read_text(encoding="utf-8")
            self.assertNotIn('"old"', text)
            self.assertLess(text.index("notify"), text.index("[mcp_servers.x]"))
            self.assertIn('"core.hook"', text)


class ObsTest(unittest.TestCase):
    def test_auth(self):
        secret = base64.b64encode(hashlib.sha256(b"pwsalt").digest()).decode()
        expected = base64.b64encode(hashlib.sha256((secret + "ch").encode()).digest()).decode()
        self.assertEqual(obs.auth_string("pw", "salt", "ch"), expected)
        msg = obs.identify_message({"op": 0, "d": {"authentication": {"salt": "salt", "challenge": "ch"}}}, "pw")
        self.assertEqual(msg["d"]["authentication"], expected)
        self.assertNotIn("authentication", obs.identify_message({"op": 0, "d": {}}, "")["d"])

    def test_events(self):
        with Events() as ev:
            feature = obs.Obs(FakeApp())
            feature.on_message({"op": 5, "d": {"eventType": "RecordStateChanged",
                                               "eventData": {"outputState": "OBS_WEBSOCKET_OUTPUT_STARTED"}}})
            self.assertTrue(feature.state["recording"])
            feature.on_message({"op": 5, "d": {"eventType": "CurrentProgramSceneChanged",
                                               "eventData": {"sceneName": "Игра"}}})
            self.assertEqual(feature.state["scene"], "Игра")
            self.assertEqual(ev.names().count("obs.state"), 2)

    def test_not_connected(self):
        with self.assertRaises(RuntimeError):
            asyncio.run(obs.Obs(FakeApp()).request("GetVersion"))


class LoadAllTest(unittest.TestCase):
    def test_all_features_load(self):
        names = [f.name for f in load_features(FakeApp())]
        for name in ("commandbar", "capture", "recorder", "downloads", "hooks", "obs"):
            self.assertIn(name, names)


if __name__ == "__main__":
    unittest.main()
