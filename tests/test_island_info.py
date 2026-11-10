"""Island features: now playing — media sessions, covers and accent colour, lyrics, the agent's «media» tool."""
import asyncio
import io
import time
import unittest
from unittest import mock

from core import llm
from core.features import media as media_mod
from core.tools import HINTS, ToolError
from tests.test_features import BusCase

LYRICS = [{"t": 0.0, "text": "первая"}, {"t": 9.0, "text": "вторая"}, {"t": 30.0, "text": "третья"}]


class FakeTools:
    def __init__(self):
        self.tools = {}

    def add(self, tool):
        self.tools[tool.name] = tool
        return tool


def tool_handler(tools, name):
    tool = tools.tools[name]
    return getattr(tool, "handler", None) or next(v for v in vars(tool).values() if asyncio.iscoroutinefunction(v))


def red_png() -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (4, 4), (255, 0, 0)).save(out, "PNG")
    return out.getvalue()


class MediaHelpersTest(unittest.TestCase):
    def test_names_and_lrc(self):
        self.assertEqual(media_mod.app_name("SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"), "Spotify")
        self.assertEqual(media_mod.app_name("ru.yandex.desktop.music"), "Яндекс Музыка")
        self.assertEqual(media_mod.app_name("Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic"), "Медиаплеер")
        self.assertEqual(media_mod.app_name("C:\\Apps\\Player.exe"), "Player")
        self.assertEqual(media_mod.app_name(""), "Плеер")
        self.assertEqual(media_mod.clean_track("Кино - Группа крови (Official Video)", "КИНО"), ("Группа крови", "Кино"))
        self.assertEqual(media_mod.clean_track("Numb", "Linkin Park - Topic"), ("Numb", "Linkin Park"))
        self.assertEqual(media_mod.clean_track("Song [Lyrics]", "Artist"), ("Song", "Artist"))
        lines = media_mod.parse_lrc("[ar:Band]\n[offset:+500]\n[00:12.50]Первая\n[00:15.00][01:20.00]Припев\n[00:10.00]")
        self.assertEqual(lines, [{"t": 9.5, "text": ""}, {"t": 12.0, "text": "Первая"}, {"t": 14.5, "text": "Припев"},
                                 {"t": 79.5, "text": "Припев"}])
        self.assertEqual(media_mod.line_at(lines, 13), 1)
        self.assertEqual(media_mod.line_at(lines, 1), -1)
        self.assertEqual((media_mod.mmss(200), media_mod.mmss(3725)), ("3:20", "1:02:05"))

    def test_fetch_lyrics_falls_back_to_search(self):
        found = [{"plainLyrics": "только текст", "duration": 180}, {"syncedLyrics": "[00:01.00]далеко", "duration": 200},
                 {"syncedLyrics": "[00:02.00]близко", "duration": 181, "plainLyrics": "близко"}]
        fake = mock.AsyncMock(side_effect=[llm.HTTPError(404, "not found"), (found, {})])
        with mock.patch.object(media_mod.llm, "request_json_async", fake):
            result = asyncio.run(media_mod.fetch_lyrics("Песня (Official Audio)", "Группа", duration=180))
        self.assertTrue(result["synced"])
        self.assertEqual(result["lines"], [{"t": 2.0, "text": "близко"}])
        first, second = (call.args[0] for call in fake.call_args_list)
        self.assertIn("/get?", first)
        self.assertIn("duration=180", first)
        self.assertNotIn("Official", first)
        self.assertIn("/search?", second)


class MediaFeatureTest(BusCase):
    def setUp(self):
        super().setUp()
        self.calls = []
        cover = red_png()

        async def snapshot(want_cover=False):
            self.calls.append(want_cover)
            state = {"active": True, "playing": True, "title": "Song", "artist": "Band", "album": "", "app": "Spotify",
                     "position": 10.0, "duration": 200.0, "position_at": time.time(), "can_next": True, "can_prev": True}
            if want_cover:
                state["cover_bytes"] = cover
            return state

        self.snapshot = snapshot

    def test_refresh_cover_and_lyrics(self):
        self.app.settings.values["island.lyrics"] = True
        media = media_mod.create(self.app)
        media.snapshot = self.snapshot
        found = {"synced": True, "lines": LYRICS, "plain": "", "instrumental": False}

        async def scenario():
            with mock.patch.object(media_mod, "fetch_lyrics", mock.AsyncMock(return_value=found)) as fetch:
                self.assertTrue(await media.refresh())
                await asyncio.sleep(0.05)
                self.assertFalse(await media.refresh())
                again = await media.load_lyrics()
                return fetch.await_count, again

        fetched, again = asyncio.run(scenario())
        self.assertEqual(fetched, 1)
        self.assertEqual(self.calls, [False, True, False])
        state = media.state
        self.assertTrue(state["cover"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(state["accent"], "#ff0000")
        self.assertEqual(again["track_id"], state["track_id"])
        self.assertEqual(self.app.db.kv_get("lyrics:" + state["track_id"])["lines"][1]["text"], "вторая")
        self.assertIn("media.lyrics", [event for event, _ in self.events])

    def test_tool_and_controls(self):
        tools = FakeTools()
        self.app.tools = tools
        media = media_mod.create(self.app)
        handler = tool_handler(tools, "media")
        media.state = {"active": True, "playing": False, "title": "Song", "artist": "Band", "app": "Spotify",
                       "position": 10.0, "duration": 200.0, "position_at": time.time(), "track_id": "abc"}
        media.lyrics = {"synced": True, "lines": LYRICS, "plain": "", "track_id": "abc"}
        # The tool refreshes from Windows first; on a machine without a player that would wipe the fixture state.
        media.supported = lambda: False
        media.refresh = mock.AsyncMock(return_value=False)
        run = lambda action, args: asyncio.run(handler(action, args, None))  # noqa: E731
        self.assertEqual(run("now", {}), "на паузе в Spotify: Band — Song (0:10 из 3:20)")
        self.assertIn("▶ вторая", run("lyrics", {}))
        self.assertIn("третья", run("lyrics", {"full": True}))
        with mock.patch.object(media_mod.config, "IS_WINDOWS", False):
            with self.assertRaises(ToolError):
                run("control", {"command": "next"})
        with mock.patch.object(media_mod.config, "IS_WINDOWS", True), mock.patch("core.win.hotkey") as hotkey:
            self.assertEqual(run("control", {"command": "next"}), "следующий трек")
        hotkey.assert_called_once_with("media_next")
        with self.assertRaises(ToolError):
            run("control", {"command": "dance"})
        self.assertIn("песн", HINTS["media"])


if __name__ == "__main__":
    unittest.main()
