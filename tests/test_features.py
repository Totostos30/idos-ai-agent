"""Island features in the core: clipboard history, notes and reminders, the island tool's reminder actions."""
import asyncio
import datetime
import itertools
import unittest
from unittest import mock

from core import bus
from core.db import DB
from core.features import clipboard as clipboard_mod
from core.features import load_features
from core.features import notes as notes_mod
from core.tools import island

NOW = datetime.datetime(2026, 11, 9, 14, 0)  # Monday


class FakeSettings:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, key, default=None):
        return self.values.get(key, default)


class FakeApp:
    def __init__(self, values=None):
        self.settings = FakeSettings(values)
        self.db = DB(":memory:")
        self.tts = self
        self.spoken = []
        self.features = []

    async def say(self, text, interrupt=True, wait=False):
        self.spoken.append(text)
        return True

    def background(self, coro, name):
        return asyncio.get_running_loop().create_task(coro, name=name)

    def feature(self, name):
        return next((f for f in self.features if f.name == name), None)


class BusCase(unittest.TestCase):
    def setUp(self):
        bus.reset()
        self.events = []
        bus.subscribe(lambda event, data, binary: self.events.append((event, data)))
        self.app = FakeApp({"clipboard.max_items": 10})

    def tearDown(self):
        bus.reset()
        self.app.db.close()


class ClipboardTest(BusCase):
    def setUp(self):
        super().setUp()
        self.clip = clipboard_mod.create(self.app)

    def test_history_dedupes_and_searches_cyrillic(self):
        with mock.patch.object(clipboard_mod.time, "time", side_effect=itertools.count(1000.0)):
            first = self.clip.add("Привет, Мир", "notepad.exe")
            self.clip.add("second")
            again = self.clip.add("Привет, Мир", "code.exe")
        self.assertEqual(first["id"], again["id"])
        items = self.clip.list()
        self.assertEqual([i["preview"] for i in items], ["Привет, Мир", "second"])
        self.assertEqual(items[0]["app"], "code.exe")
        self.assertEqual([i["preview"] for i in self.clip.list("мИр")], ["Привет, Мир"])
        self.assertEqual(self.clip.get(first["id"])["text"], "Привет, Мир")
        self.assertIsNone(self.clip.add("   "))
        self.assertEqual(self.events[0][0], "clipboard.added")

    def test_trim_keeps_pinned(self):
        with mock.patch.object(clipboard_mod.time, "time", side_effect=itertools.count(1000.0)):
            first = self.clip.add("pinned item")
            self.assertTrue(self.clip.pin(first["id"]))
            for n in range(15):
                self.clip.add(f"item {n}")
        items = self.clip.list(limit=100)
        self.assertEqual(len(items), 11)
        self.assertTrue(items[0]["pinned"])
        self.assertEqual(items[1]["preview"], "item 14")
        self.assertEqual(self.clip.clear(), 10)
        self.assertEqual([i["preview"] for i in self.clip.list()], ["pinned item"])

    def test_capture_skips_secrets_and_excluded_apps(self):
        self.app.settings.values["clipboard.skip_apps"] = ["KeePass.exe"]

        async def capture(process, text, secret=False):
            with mock.patch("core.win.foreground", return_value={"process": process}), \
                    mock.patch("core.win.get_clipboard", return_value=text), \
                    mock.patch.object(clipboard_mod, "excluded", return_value=secret):
                return await self.clip.capture()

        self.assertIsNone(asyncio.run(capture("keepass.exe", "password")))
        self.assertIsNone(asyncio.run(capture("chrome.exe", "hidden", secret=True)))
        self.assertEqual(asyncio.run(capture("chrome.exe", "hello"))["app"], "chrome.exe")
        self.assertEqual([i["preview"] for i in self.clip.list()], ["hello"])


class WhenTest(unittest.TestCase):
    def at(self, text):
        ts = notes_mod.parse_when(text, NOW)
        return None if ts is None else datetime.datetime.fromtimestamp(ts)

    def test_phrases(self):
        d = datetime.datetime
        cases = {
            "через 20 минут": d(2026, 11, 9, 14, 20), "через двадцать пять минут": d(2026, 11, 9, 14, 25),
            "через полчаса": d(2026, 11, 9, 14, 30), "через 3 дня": d(2026, 11, 12, 14, 0),
            "через неделю": d(2026, 11, 16, 14, 0), "завтра в 9": d(2026, 11, 10, 9, 0),
            "завтра утром": d(2026, 11, 10, 9, 0), "послезавтра в 10:15": d(2026, 11, 11, 10, 15),
            "в 18:30": d(2026, 11, 9, 18, 30), "в 13:00": d(2026, 11, 10, 13, 0), "в 7 вечера": d(2026, 11, 9, 19, 0),
            "в 3 часа дня": d(2026, 11, 9, 15, 0), "сегодня вечером": d(2026, 11, 9, 19, 0),
            "в пятницу в 18:30": d(2026, 11, 13, 18, 30), "в понедельник": d(2026, 11, 16, 9, 0),
            "во вторник": d(2026, 11, 10, 9, 0), "25.12 в 10:00": d(2026, 12, 25, 10, 0), "3 декабря": d(2026, 12, 3, 9, 0),
            "1 января 2027 в 0:05": d(2027, 1, 1, 0, 5), "01.02": d(2027, 2, 1, 9, 0), "в 18.30": d(2026, 11, 9, 18, 30),
            "в 12 ночи": d(2026, 11, 10, 0, 0), "в полдень": d(2026, 11, 10, 12, 0),
            "2026-11-20 08:00": d(2026, 11, 20, 8, 0),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.at(text), expected)
        self.assertIsNone(self.at("когда-нибудь"))
        self.assertIsNone(self.at(""))

    def test_repeat_next_due_and_format(self):
        self.assertEqual(notes_mod.parse_repeat("каждый день в 8:00"), "daily")
        self.assertEqual(notes_mod.parse_repeat("по будням в 9"), "weekdays")
        self.assertEqual(notes_mod.parse_repeat("каждую пятницу"), "weekly")
        self.assertEqual(notes_mod.parse_repeat("каждый час"), "hourly")
        self.assertEqual(notes_mod.parse_repeat("завтра"), "")
        friday = datetime.datetime(2026, 11, 13, 9, 0).timestamp()
        later = friday + 1
        self.assertEqual(datetime.datetime.fromtimestamp(notes_mod.next_due(friday, "weekdays", later)),
                         datetime.datetime(2026, 11, 16, 9, 0))
        self.assertEqual(datetime.datetime.fromtimestamp(notes_mod.next_due(friday, "daily", later)),
                         datetime.datetime(2026, 11, 14, 9, 0))
        self.assertEqual(notes_mod.next_due(friday, "hourly", later), friday + 3600)
        self.assertIsNone(notes_mod.next_due(friday, "", later))
        fmt = notes_mod.format_due
        self.assertEqual(fmt(datetime.datetime(2026, 11, 9, 18, 30).timestamp(), NOW), "сегодня в 18:30")
        self.assertEqual(fmt(datetime.datetime(2026, 11, 10, 9, 0).timestamp(), NOW), "завтра в 09:00")
        self.assertEqual(fmt(datetime.datetime(2026, 11, 13, 18, 30).timestamp(), NOW), "в пятницу в 18:30")
        self.assertEqual(fmt(datetime.datetime(2026, 12, 25, 10, 0).timestamp(), NOW), "25.12.2026 в 10:00")


class NotesTest(BusCase):
    def setUp(self):
        super().setUp()
        self.notes = notes_mod.create(self.app)
        self.app.features = [self.notes]

    def test_notes(self):
        with mock.patch.object(notes_mod.time, "time", side_effect=itertools.count(1000.0)):
            first = self.notes.add_note("купить молоко")
            second = self.notes.add_note("идея: тёмная тема")
            self.assertTrue(self.notes.update_note(first["id"], done=True))
            self.assertTrue(self.notes.update_note(second["id"], pinned=True))
        self.assertEqual([n["text"] for n in self.notes.notes()], ["идея: тёмная тема", "купить молоко"])
        self.assertEqual([n["text"] for n in self.notes.notes(include_done=False)], ["идея: тёмная тема"])
        self.assertTrue(self.notes.delete_note(first["id"]))
        with self.assertRaises(ValueError):
            self.notes.add_note("  ")
        self.assertTrue(any(event == "notes.changed" for event, _ in self.events))

    def test_reminders_fire_repeat_and_snooze(self):
        once = self.notes.add_reminder("позвонить маме", "через 10 минут")
        daily = self.notes.add_reminder("выпить таблетку", "каждый день в 8:00")
        self.assertEqual(daily["repeat"], "daily")
        self.assertIn("в 08:00", daily["when"])
        for text, when in (("что-то", "когда-нибудь"), ("", "через 5 минут")):
            with self.assertRaises(ValueError):
                self.notes.add_reminder(text, when)
        self.assertEqual(asyncio.run(self.notes.fire_due(now=min(once["due"], daily["due"]) - 1)), 0)
        self.assertEqual(asyncio.run(self.notes.fire_due(now=max(once["due"], daily["due"]) + 1)), 2)
        self.assertEqual(sorted(self.app.spoken), ["Напоминание: выпить таблетку", "Напоминание: позвонить маме"])
        left = self.notes.reminders()
        self.assertEqual([r["text"] for r in left], ["выпить таблетку"])
        self.assertGreater(left[0]["due"], daily["due"])
        cards = [data for event, data in self.events if event == "island.card"]
        self.assertEqual(len(cards), 2)
        card = next(c for c in cards if c["text"] == "позвонить маме")
        self.assertEqual((card["kind"], card["timeout"]), ("alarm", 0))
        self.assertEqual([a["id"] for a in card["actions"]], ["done", "snooze"])
        self.notes.on_action({"card_id": card["id"], "action": "snooze"})
        self.assertEqual(len(self.notes.reminders()), 2)

    def test_island_tool_actions(self):
        ctx = mock.Mock(app=self.app)
        run = lambda action, args: asyncio.run(island.handler(action, args, ctx))  # noqa: E731
        self.assertIn("напомню «полить цветы»", run("remind", {"text": "полить цветы", "when": "через 2 часа"}))
        self.assertIn("каждый день", run("remind", {"text": "зарядка", "when": "каждый день в 7:30"}))
        self.assertIn("полить цветы", run("reminders", {}))
        self.assertIn("отменено", run("cancel_reminder", {"id": "цвет"}))
        self.assertIn("отменил напоминаний: 1", run("cancel_reminder", {"id": "все"}))
        self.assertEqual(run("reminders", {}), "напоминаний нет")
        self.assertIn("заметка сохранена", run("note", {"text": "код от домофона 1234"}))
        self.assertIn("домофона", run("notes", {}))

    def test_load_features_skips_broken(self):
        with self.assertLogs("idos.features", "WARNING"):
            loaded = load_features(self.app, ["clipboard", "notes", "no_such_feature"])
        self.assertEqual([f.name for f in loaded], ["clipboard", "notes"])


if __name__ == "__main__":
    unittest.main()
