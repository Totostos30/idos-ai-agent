"""Media commands go to the player the user named, never to whatever Windows considers current (e.g. a browser)."""
import asyncio
import unittest
from unittest import mock

from core.features import media as media_mod

YM = "A025C540.Yandex.Music_vfsxfs4r6dsvj!App"
BROWSER = "YandexBrowser"
CHROME = "Chrome"


class App:
    settings: dict = {}
    tools = None


class MatchTest(unittest.TestCase):
    def test_aliases(self):
        self.assertTrue(media_mod.session_matches(YM, "яндекс музыку"))
        self.assertTrue(media_mod.session_matches("ru.yandex.desktop.music", "Яндекс Музыка"))
        self.assertFalse(media_mod.session_matches(CHROME, "яндекс музыку"))
        self.assertFalse(media_mod.session_matches(BROWSER, "яндекс музыку"))
        self.assertTrue(media_mod.session_matches("SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify", "спотифай"))
        self.assertTrue(media_mod.session_matches("Chrome", "хром"))
        self.assertFalse(media_mod.session_matches("", "spotify"))


class ControlTest(unittest.TestCase):
    def make(self, result):
        feature = media_mod.Media(App())
        calls = []

        async def control(action, position=None, app=""):
            calls.append((action, app))
            return result(app)

        feature.session_control = control
        feature.spawn = lambda coro, name: coro.close()
        return feature, calls

    def test_named_player_uses_its_session(self):
        feature, calls = self.make(lambda app: app == "Яндекс Музыка")
        with mock.patch.object(media_mod.Media, "supported", staticmethod(lambda: True)):
            out = asyncio.run(feature.control("play", None, "Яндекс Музыка"))
        self.assertEqual(out["via"], "session")
        self.assertEqual(calls[0], ("play", "Яндекс Музыка"))

    def test_named_player_never_sends_global_media_keys(self):
        feature, _calls = self.make(lambda app: False)
        hotkey = mock.Mock()
        with mock.patch.object(media_mod.Media, "supported", staticmethod(lambda: True)), \
                mock.patch.object(media_mod.config, "IS_WINDOWS", True), \
                mock.patch.object(media_mod.Media, "_window_key", mock.AsyncMock(return_value=False)), \
                mock.patch.dict("sys.modules", {"core.win": mock.Mock(hotkey=hotkey)}):
            out = asyncio.run(feature.control("play", None, "Яндекс Музыка", wait=0))
        self.assertFalse(out["ok"])
        hotkey.assert_not_called()

    def test_waits_for_freshly_started_player(self):
        state = {"n": 0}

        def later(app):
            state["n"] += 1
            return state["n"] >= 3

        feature, calls = self.make(later)
        with mock.patch.object(media_mod.Media, "supported", staticmethod(lambda: True)), \
                mock.patch.object(media_mod.asyncio, "sleep", mock.AsyncMock()):
            out = asyncio.run(feature.control("play", None, "spotify", wait=5))
        self.assertTrue(out["ok"])
        self.assertEqual(len(calls), 3)


if __name__ == "__main__":
    unittest.main()
