import asyncio
import unittest
from unittest import mock

from core.features import capture as capmod


def keys(seq):
    """seq: list of sets of pressed VK codes, one per poll; last state repeats."""
    state = {"i": 0}
    calls = {"n": 0}

    def key_down(vk):
        frame = seq[min(state["i"], len(seq) - 1)]
        calls["n"] += 1
        if vk == 0x11:  # Ctrl is checked once per poll after Esc
            state["i"] += 1
        return vk in frame
    return key_down


class ColorPickTest(unittest.TestCase):
    def setUp(self):
        self.cap = capmod.Capture.__new__(capmod.Capture)
        self.cap._picking = None
        self.events = []
        self.p = mock.patch.object(capmod.bus, "emit", lambda name, data=None: self.events.append((name, data)))
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def run_pick(self, seq, timeout=5.0):
        return asyncio.run(self.cap.wait_for_pick(keys(seq), lambda: (10, 20), lambda x, y: (255, 0, 128),
                                                  timeout=timeout, poll=0.001, preview_every=0.0))

    def test_waits_for_ctrl_release_then_press(self):
        # hotkey Ctrl still held -> released -> pressed: picks only on the fresh press
        res = self.run_pick([{0x11, 0x12}, {0x11}, set(), set(), {0x11}])
        self.assertEqual(res, (10, 20))
        self.assertTrue(any(n == "capture.color_pick" and d["state"] == "preview" and d["hex"].lower() == "#ff0080"
                            for n, d in self.events))

    def test_no_pick_while_only_hovering(self):
        res = self.run_pick([set()], timeout=3.0)
        self.assertIsNone(res)

    def test_escape_cancels(self):
        res = self.run_pick([set(), {0x1B}])
        self.assertIsNone(res)
        self.assertIsNone(self.cap._picking)

    def test_ctrl_with_alt_is_not_a_pick(self):
        res = self.run_pick([set(), {0x11, 0x12}], timeout=3.0)
        self.assertIsNone(res)


if __name__ == "__main__":
    unittest.main()
