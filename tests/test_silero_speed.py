import unittest

import numpy as np

from core.tts import engines
from core.tts.engines import CHAINS, ENGINES, time_stretch, to_pcm16

SR = 24000


def tone(seconds=1.0, freq=220.0):
    t = np.arange(int(SR * seconds)) / SR
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def pitch(x):
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return np.argmax(spec) * SR / len(x)


class SileroSpeedTest(unittest.TestCase):
    def test_faster_and_slower_keep_pitch(self):
        x = tone(1.0)
        for speed in (1.5, 0.75, 2.0, 0.5):
            y = time_stretch(x, speed, SR)
            self.assertAlmostEqual(len(y) / len(x), 1 / speed, delta=0.03)
            self.assertAlmostEqual(pitch(y), 220.0, delta=8.0)
            self.assertLess(np.max(np.abs(y)), 1.01)

    def test_normal_speed_is_untouched(self):
        x = tone(0.5)
        self.assertTrue(np.array_equal(time_stretch(x, 1.0, SR), x))

    def test_pcm_uses_speed(self):
        x = tone(1.0)
        normal = to_pcm16(x, 1.0, SR)
        fast = to_pcm16(x, 1.6, SR)
        self.assertEqual(len(normal), len(x) * 2)
        self.assertAlmostEqual(len(fast) / len(normal), 1 / 1.6, delta=0.03)

    def test_only_silero(self):
        self.assertEqual(list(ENGINES), ["silero"])
        self.assertEqual(set(CHAINS), {"silero", "off"})
        self.assertEqual(engines.PACKAGES, dict())

    def test_synth_reads_rate(self):
        class Model:
            def apply_tts(self, **kw):
                return tone(1.0)

        class Settings:
            def __init__(self, rate):
                self.data = {"voice.rate": rate, "voice.sample_rate": SR}

            def get(self, key, default=None):
                return self.data.get(key, default)

        lens = []
        for rate in (1.0, 1.5):
            eng = ENGINES["silero"](Settings(rate))
            eng._load = lambda *a, **k: Model()
            eng.model = Model()
            audio = eng.synth("привет")
            lens.append(len(audio.data))
        self.assertLess(lens[1], lens[0] * 0.75)


if __name__ == "__main__":
    unittest.main()
