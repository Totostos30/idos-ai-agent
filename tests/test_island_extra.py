import asyncio
import unittest
from unittest import mock

from core import bus
from core.features import status


class FakeApp:
    pass


class IslandIndicatorsTest(unittest.TestCase):
    def make(self):
        with mock.patch.object(status.Feature, "__init__", lambda self, app: None):
            return status.Status(FakeApp())

    def test_volume_virtual_clamps(self):
        st = self.make()
        with mock.patch.object(status.config, "IS_WINDOWS", False):
            m = st.methods()
            self.assertEqual(asyncio.run(m["status.volume"](delta=30))["volume"], 80)
            self.assertEqual(asyncio.run(m["status.volume"](delta=90))["volume"], 100)
            self.assertEqual(asyncio.run(m["status.volume"](delta=-200))["volume"], 50)

    def test_fast_tick_emits_on_change_only(self):
        st = self.make()
        events = []
        with mock.patch.object(status, "keyboard_state", return_value={"caps": True, "layout": "RU"}), \
             mock.patch.object(status, "privacy_state", return_value={"mic": True}), \
             mock.patch.object(bus, "emit", lambda e, d=None, b=None: events.append(e)):
            st.fast_tick(0, idle=0)
            st.fast_tick(1, idle=0)
            st.fast_tick(5, idle=500)
        self.assertEqual(events, ["status.keyboard", "status.privacy", "status.away"])
        self.assertTrue(st.away)

    def test_non_windows_indicators_empty(self):
        with mock.patch.object(status.config, "IS_WINDOWS", False):
            self.assertEqual(status.keyboard_state(), {})
            self.assertEqual(status.privacy_state(), {})
            self.assertEqual(status.idle_seconds(), 0.0)


if __name__ == "__main__":
    unittest.main()
