import asyncio
import unittest
from unittest import mock
from types import SimpleNamespace

from core.tools import ToolError, voice_tool
from core.tts import TTS
from core.tts.engines import ENGINES, SETTING_FOR, catalog


class FakeSettings:
    def __init__(self):
        self.data = dict()

    def get(self, key, default=None):
        return self.data.get(key, default)

    def update(self, values):
        self.data.update(values)


class VoicePickerTest(unittest.TestCase):
    def test_catalog_lists_all_engines(self):
        items = catalog(FakeSettings())
        self.assertEqual([i["engine"] for i in items], ["silero"])
        silero = items[0]
        self.assertIn("aidar", [v["id"] for v in silero["voices"]])
        self.assertEqual(silero["setting"], "voice.silero_speaker")
        self.assertEqual(list(ENGINES), ["silero"])
        self.assertFalse(items[0]["installable"])

    def test_resolve_by_russian_name(self):
        items = catalog(FakeSettings())
        self.assertEqual(voice_tool.resolve(items, "", "Айдар"), ("silero", "aidar"))
        self.assertEqual(voice_tool.resolve(items, "", "байя"), ("silero", "baya"))
        with self.assertRaises(ToolError):
            voice_tool.resolve(items, "piper", "ирина")
        with self.assertRaises(ToolError):
            voice_tool.resolve(items, "", "Геннадий")

    def test_apply_saves_voice_without_sample(self):
        settings = FakeSettings()
        tts = TTS(settings, engines=[])
        result = asyncio.run(tts.apply("silero", "baya", 1.3, sample=False))
        self.assertTrue(result["ok"])
        self.assertEqual(settings.data["voice.tts_engine"], "silero")
        self.assertEqual(settings.data["voice." + SETTING_FOR["silero"]], "baya")
        self.assertEqual(settings.data["voice.rate"], 1.3)
        with self.assertRaises(ValueError):
            asyncio.run(tts.apply("nope", "", None, sample=False))

    def test_tool_set(self):
        settings = FakeSettings()
        tts = TTS(settings, engines=[])
        ctx = SimpleNamespace(app=SimpleNamespace(tts=tts))
        with mock.patch.object(ENGINES["silero"], "available", lambda self: ""):
            text = asyncio.run(voice_tool.handler("set", dict(voice="евгений"), ctx))
        self.assertIn("eugene", text)
        self.assertEqual(settings.data["voice.silero_speaker"], "eugene")


if __name__ == "__main__":
    unittest.main()
