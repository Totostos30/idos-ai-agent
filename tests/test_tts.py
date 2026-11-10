"""Speech output: text preparation, WAV helpers, engine chain, fallback, cache and interruption."""
from __future__ import annotations

import array
import asyncio
import math
import time
import unittest

from core.tts import TTS, clip_speech
from core.tts.engines import EngineError, build_engines
from core.tts.normalize import normalize, number_words, sentences, year_words
from core.tts.player import Audio, envelope, pcm_to_wav


class FakeSettings:
    def __init__(self, **values) -> None:
        self.values = {"voice.tts_engine": "silero", "voice.fallback": "windows", "voice.volume": 0.9,
                       "voice.max_speech_chars": 700}
        self.values.update(values)

    def get(self, key, default=None):
        return self.values.get(key, default)


def tone(seconds: float = 0.2, rate: int = 8000) -> bytes:
    samples = array.array("h", (int(8000 * math.sin(i / 5)) for i in range(int(seconds * rate))))
    return pcm_to_wav(samples.tobytes(), rate)


class FakeEngine:
    full_normalize = True

    def __init__(self, name: str, fail: bool = False) -> None:
        self.name, self.fail, self.calls = name, fail, []

    def available(self) -> str:
        return ""

    def warm(self) -> None:
        pass

    def synth(self, text: str) -> Audio:
        self.calls.append(text)
        if self.fail:
            raise EngineError("сломался")
        return Audio(tone(), "wav", self.name)


class FakePlayer:
    def __init__(self, hold: float = 0.0) -> None:
        self.played, self.hold = [], hold

    def play(self, audio, stop, volume=1.0) -> None:
        self.played.append((audio.engine, volume))
        if self.hold:
            stop.wait(self.hold)


def quiet(*_args) -> None:
    pass


class NormalizeTest(unittest.TestCase):
    def test_number_words(self):
        self.assertEqual(number_words(2026), "две тысячи двадцать шесть")
        self.assertEqual(number_words(1000), "тысяча")
        self.assertEqual(number_words(1_001_000), "один миллион одна тысяча")
        self.assertEqual(number_words(21, True), "двадцать одна")
        self.assertEqual(year_words(2026), "две тысячи двадцать шестого")

    def test_speakable_text(self):
        link = "https:" + "//example" + ".com/x"
        text = normalize(f"**Готово!** В 07:05 будет 15% и 3,5 °C, дата 08.11.2026. Открой Telegram и VS Code: {link}")
        for part in ("Готово!", "семь ноль пять", "пятнадцать процентов", "три целых пять десятых градуса",
                     "восьмое ноября две тысячи двадцать шестого года", "телеграм", "вэ эс код", "ссылка"):
            self.assertIn(part, text)
        self.assertNotRegex(text, r"[A-Za-z0-9*]")

    def test_code_acronyms_and_money(self):
        text = normalize("Смотри:\n```python\nprint(1)\n```\nAPI работает, Wi-Fi включён, 1 000 000 ₽, $20, -5 градусов")
        for part in ("код опущен", "эй пи ай", "вай фай", "один миллион рублей", "двадцать долларов", "минус пять"):
            self.assertIn(part, text)

    def test_light_mode_keeps_digits_for_windows_voices(self):
        text = normalize("Это 15 `код` 🙂 и C:\\Users\\Anton\\отчёт.docx", full=False)
        self.assertEqual(text, "Это 15 код и отчёт.docx.")

    def test_sentences_and_clip(self):
        chunks = sentences("Первое. " + "Слово " * 60 + ". Третье.", limit=100)
        self.assertEqual(chunks[0], "Первое.")
        self.assertTrue(all(len(chunk) <= 101 for chunk in chunks))
        self.assertEqual(clip_speech("Раз. Два. Три.", 10), "Раз. Два.")


class ManagerTest(unittest.TestCase):
    def test_fallback_cache_and_events(self):
        events = []
        broken, good, player = FakeEngine("silero", fail=True), FakeEngine("winrt"), FakePlayer()
        tts = TTS(FakeSettings(), engines=[broken, good], player=player, emit=lambda e, d: events.append((e, d)))

        async def scenario():
            self.assertTrue(await tts.say("Привет. Как дела?", wait=True))
            self.assertTrue(await tts.say("Привет. Как дела?", wait=True))

        asyncio.run(scenario())
        self.assertEqual([engine for engine, _ in player.played], ["winrt"] * 4)
        self.assertEqual(player.played[0][1], 0.9)
        self.assertEqual(len(good.calls), 2)
        self.assertEqual(len(broken.calls), 1)
        self.assertIn(("tts.error", {"engine": "silero", "message": "сломался"}), events)
        speaking = [d for e, d in events if e == "tts.state" and d["state"] == "speaking"]
        self.assertEqual(len(speaking), 4)
        self.assertEqual(speaking[0]["text"], "Привет.")
        self.assertTrue(speaking[0]["envelope"])
        self.assertEqual(events[-1], ("tts.state", {"state": "idle"}))

    def test_stop_interrupts_quickly(self):
        player = FakePlayer(hold=5.0)
        tts = TTS(FakeSettings(), engines=[FakeEngine("silero")], player=player, emit=quiet)

        async def scenario():
            await tts.say("Длинная фраза. Ещё одна.")
            await asyncio.sleep(0.3)
            self.assertTrue(tts.speaking)
            started = time.monotonic()
            await tts.stop()
            return time.monotonic() - started

        self.assertLess(asyncio.run(scenario()), 1.5)
        self.assertFalse(tts.speaking)
        self.assertEqual(len(player.played), 1)

    def test_new_reply_interrupts_previous(self):
        player = FakePlayer(hold=5.0)
        tts = TTS(FakeSettings(), engines=[FakeEngine("silero")], player=player, emit=quiet)

        async def scenario():
            await tts.say("Первый ответ.")
            await asyncio.sleep(0.2)
            await tts.say("Второй ответ.")
            await asyncio.sleep(0.2)
            await tts.stop()

        asyncio.run(scenario())
        self.assertEqual(len(player.played), 2)

    def test_off_empty_and_engine_chain(self):
        off = TTS(FakeSettings(**{"voice.tts_engine": "off"}), player=FakePlayer(), emit=quiet)
        self.assertFalse(asyncio.run(off.say("Привет")))
        tts = TTS(FakeSettings(), engines=[FakeEngine("silero")], player=FakePlayer(), emit=quiet)
        self.assertFalse(asyncio.run(tts.say("🙂 **")))
        self.assertEqual([e.name for e in build_engines(FakeSettings())], ["silero"])
        legacy = FakeSettings(**{"voice.tts_engine": "edge", "voice.fallback": "windows"})
        self.assertEqual([e.name for e in build_engines(legacy)], ["silero"])

    def test_wav_helpers(self):
        audio = Audio(tone(0.5), "wav")
        self.assertAlmostEqual(audio.duration, 0.5, places=2)
        levels = envelope(audio, fps=20)
        self.assertEqual(len(levels), 10)
        self.assertEqual(max(levels), 1.0)
        self.assertEqual(envelope(Audio(b"x" * 6000, "mp3"), fps=20), [0.6] * 20)


if __name__ == "__main__":
    unittest.main()
