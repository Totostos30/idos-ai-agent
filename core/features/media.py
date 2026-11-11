"""Now playing for the island: Windows media sessions (GSMTC) with cover art, accent colour and controls, synced lyrics
from LRCLIB and an audio-reactive equalizer from the speakers' loopback (PyAudioWPatch + numpy, optional)."""
from __future__ import annotations

import asyncio
import base64
import colorsys
import hashlib
import io
import logging
import re
import threading
import time
import urllib.parse
from typing import Any, Callable

from .. import bus, config, llm
from . import Feature

log = logging.getLogger("idos.media")
LRCLIB = "https:" + "//lrclib" + ".net/api"
HEADERS = {"User-Agent": "IDOS/2.0 (desktop assistant)"}
MEDIA_KEYS = {"play_pause": "media_play_pause", "play": "media_play_pause", "pause": "media_play_pause",
              "next": "media_next", "previous": "media_prev", "stop": "media_stop"}
APPS = (("spotify", "Spotify"), ("applemusic", "Apple Music"), ("zunemusic", "Медиаплеер"), ("vlc", "VLC"),
        ("aimp", "AIMP"), ("foobar", "foobar2000"), ("chrome", "Chrome"), ("msedge", "Edge"), ("firefox", "Firefox"),
        ("opera", "Opera"), ("telegram", "Telegram"), ("discord", "Discord"))
NOISE = re.compile(r"\s*[\(\[](?:official|lyric|lyrics|audio|video|visualizer|mv|hd|hq|4k|remaster|клип|премьера)"
                   r"[^\)\]]*[\)\]]", re.I)
LRC_STAMP = re.compile(r"\[(\d+):(\d{1,2}(?:[.:]\d{1,3})?)\]")


# Spoken player names -> pieces of the Windows media-session id (AUMID) and of the window title / process.
PLAYER_ALIASES = (
    (("яндекс музык", "yandex music", "yandex.music", "яндекс.музык", "я.музык"),
     ("yandex.music", "yandexmusic", "yandex music", "yandex.desktop.music", "яндекс музыка", "яндекс.музыка")),
    (("спотифай", "spotify"), ("spotify",)),
    (("apple music", "эпл мьюзик", "эппл мьюзик"), ("applemusic", "apple music")),
    (("медиаплеер", "media player", "проигрыватель windows", "groove"), ("zunemusic", "zunevideo", "media player")),
    (("vlc", "влц"), ("vlc",)), (("aimp", "аимп"), ("aimp",)), (("foobar", "фубар"), ("foobar",)),
    (("вк музык", "vk music", "вконтакте", "boom"), ("vk", "boom")),
    (("звук", "zvuk", "сбер звук"), ("zvuk",)), (("телеграм", "telegram"), ("telegram",)),
    (("дискорд", "discord"), ("discord",)), (("яндекс браузер", "yandex browser"), ("yandex.exe", "yandexbrowser")),
    (("хром", "chrome"), ("chrome",)), (("эдж", "edge"), ("msedge",)), (("файрфокс", "firefox"), ("firefox",)),
    (("опера", "opera"), ("opera",)),
)
BROWSERS = ("chrome", "msedge", "firefox", "opera", "yandex.exe", "yandexbrowser", "browser", "brave", "vivaldi")


def player_tokens(query: str) -> tuple[str, ...]:
    """What to look for in a session id / window for a spoken player name («Яндекс Музыку» -> yandex.music…)."""
    q = re.sub(r"\s+", " ", (query or "").casefold().replace("ё", "е")).strip()
    if not q:
        return ()
    for spoken, tokens in PLAYER_ALIASES:
        if any(word in q for word in spoken):
            return tokens
    plain = re.sub(r"\.exe$", "", q)
    return tuple(dict.fromkeys((plain, plain.replace(" ", ""))))


def session_matches(aumid: str, query: str) -> bool:
    low = (aumid or "").casefold()
    tokens = player_tokens(query)
    if not low or not tokens:
        return False
    if any(t in low for t in tokens):
        # «Яндекс Музыка» must not match the Yandex browser playing a series and vice versa.
        wants_browser = any(t in BROWSERS for t in tokens)
        return wants_browser or not any(b in low for b in BROWSERS)
    return False


def app_name(aumid: str) -> str:
    low = (aumid or "").lower()
    if "yandex" in low:
        return "Яндекс Музыка" if "music" in low else "Яндекс Браузер"
    for key, label in APPS:
        if key in low:
            return label
    tail = re.split(r"[!\\/]", aumid or "")[-1]
    return re.sub(r"\.exe$", "", tail, flags=re.I) or "Плеер"


def clean_track(title: str, artist: str) -> tuple[str, str]:
    """Tidy names for the lyrics search: drop «(Official Video)», split «Artist - Song» titles from video sites."""
    title = NOISE.sub("", title or "").strip()
    artist = re.sub(r"\s*-\s*topic$", "", artist or "", flags=re.I).strip()
    if " - " in title:
        left, right = (part.strip() for part in title.split(" - ", 1))
        if not artist or left.lower().startswith(artist.lower()[:4]) or re.search(r"vevo|official|music", artist, re.I):
            artist, title = left, right
    return title, artist


def track_key(state: dict) -> str:
    raw = "|".join(str(state.get(k, "")) for k in ("app", "artist", "title", "album"))
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]


def parse_lrc(text: str) -> list[dict]:
    offset = 0.0
    match = re.search(r"\[offset:\s*([+-]?\d+)\]", text or "", re.I)
    if match:
        offset = int(match.group(1)) / 1000.0
    lines = []
    for raw in (text or "").splitlines():
        stamps = list(LRC_STAMP.finditer(raw))
        if not stamps:
            continue
        words = raw[stamps[-1].end():].strip()
        for stamp in stamps:
            seconds = int(stamp.group(1)) * 60 + float(stamp.group(2).replace(":", "."))
            lines.append({"t": round(max(0.0, seconds - offset), 2), "text": words})
    lines.sort(key=lambda line: line["t"])
    return lines


async def fetch_lyrics(title: str, artist: str, album: str = "", duration: float = 0) -> dict | None:
    title, artist = clean_track(title, artist)
    if not title:
        return None
    params = {"track_name": title, "artist_name": artist}
    if album:
        params["album_name"] = album
    if duration:
        params["duration"] = str(int(round(duration)))
    try:
        data, _ = await llm.request_json_async(f"{LRCLIB}/get?{urllib.parse.urlencode(params)}", None, HEADERS, "GET", 12)
    except llm.HTTPError as exc:
        if exc.status != 404:
            raise
        data = None
    if not data or not (data.get("syncedLyrics") or data.get("plainLyrics")):
        query = urllib.parse.urlencode({"track_name": title, "artist_name": artist})
        found, _ = await llm.request_json_async(f"{LRCLIB}/search?{query}", None, HEADERS, "GET", 12)
        candidates = [c for c in (found or []) if c.get("syncedLyrics") or c.get("plainLyrics")]
        candidates.sort(key=lambda c: (not c.get("syncedLyrics"), abs(float(c.get("duration") or 0) - duration)))
        data = candidates[0] if candidates else None
    if not data:
        return None
    synced = parse_lrc(data.get("syncedLyrics") or "")
    return {"synced": bool(synced), "lines": synced, "plain": data.get("plainLyrics") or "",
            "instrumental": bool(data.get("instrumental"))}


def accent_color(image: Any) -> str:
    best, best_score = (128, 128, 128), -1.0
    for r, g, b in image.resize((24, 24)).getdata():
        _, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        score = s * (0.35 + v) if v > 0.25 else 0.0
        if score > best_score:
            best, best_score = (r, g, b), score
    return "#%02x%02x%02x" % best


def cover_info(data: bytes) -> tuple[str, str]:
    """Cover bytes → (data URL, accent colour); scaled to 192 px when Pillow is present."""
    if not data:
        return "", ""
    mime, accent = ("image/png" if data[:4] == b"\x89PNG" else "image/jpeg"), ""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as original:
            image = original.convert("RGB")
        image.thumbnail((192, 192))
        out = io.BytesIO()
        image.save(out, "JPEG", quality=85)
        data, mime, accent = out.getvalue(), "image/jpeg", accent_color(image)
    except Exception:  # noqa: BLE001 - Pillow missing or odd image: keep the original bytes
        log.debug("cover processing failed", exc_info=True)
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii"), accent


# ---------------------------------------------------------------------------- Windows media session (WinRT)
def _seconds(value: Any) -> float:
    if value is None:
        return 0.0
    if hasattr(value, "total_seconds"):
        return max(0.0, value.total_seconds())
    return max(0.0, float(getattr(value, "duration", value)) / 10_000_000)


async def _session(app: str = "") -> Any:
    """The session Windows considers current or, when a player is named, that player's own session (or None)."""
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager

    manager = await Manager.request_async()
    if not app:
        return manager.get_current_session()
    for session in list(manager.get_sessions() or []):
        if session_matches(session.source_app_user_model_id or "", app):
            return session
    return None


async def read_stream(reference: Any) -> bytes:
    from winrt.windows.storage.streams import Buffer, DataReader, InputStreamOptions

    stream = await reference.open_read_async()
    size = int(stream.size)
    if not 0 < size <= 4_000_000:
        return b""
    buffer = await stream.read_async(Buffer(size), size, InputStreamOptions.READ_AHEAD)
    try:
        return bytes(buffer)
    except TypeError:
        reader = DataReader.from_buffer(buffer)
        out = bytearray(buffer.length)
        reader.read_bytes(out)
        return bytes(out)


async def snapshot_async(want_cover: bool = False) -> dict:
    session = await _session()
    if session is None:
        return {"active": False}
    props = await session.try_get_media_properties_async()
    info = session.get_playback_info()
    timeline = session.get_timeline_properties()
    status = int(info.playback_status) if info is not None else 0
    position = _seconds(getattr(timeline, "position", None))
    duration = _seconds(getattr(timeline, "end_time", None))
    updated = time.time()
    try:
        stamp = timeline.last_updated_time.timestamp()
        if stamp > 946684800:
            updated = stamp
    except Exception:  # noqa: BLE001
        pass
    if status == 4 and duration:
        position = min(duration, position + max(0.0, time.time() - updated))
    controls = getattr(info, "controls", None)
    state = {"active": True, "playing": status == 4, "title": props.title or "",
             "artist": props.artist or props.album_artist or "", "album": props.album_title or "",
             "aumid": session.source_app_user_model_id or "", "position": round(position, 2),
             "duration": round(duration, 2), "position_at": time.time(),
             "can_next": bool(getattr(controls, "is_next_enabled", True)),
             "can_prev": bool(getattr(controls, "is_previous_enabled", True)),
             "can_seek": bool(getattr(controls, "is_playback_position_enabled", False))}
    state["app"] = app_name(state["aumid"])
    if want_cover and props.thumbnail is not None:
        try:
            state["cover_bytes"] = await read_stream(props.thumbnail)
        except Exception:  # noqa: BLE001
            log.debug("cover read failed", exc_info=True)
    return state


async def control_async(action: str, position: float | None = None, app: str = "") -> bool:
    session = await _session(app)
    if session is None:
        return False
    if action == "seek":
        if position is None:
            return False
        return bool(await session.try_change_playback_position_async(int(max(0.0, position) * 10_000_000)))
    calls = {"play_pause": session.try_toggle_play_pause_async, "play": session.try_play_async,
             "pause": session.try_pause_async, "next": session.try_skip_next_async,
             "previous": session.try_skip_previous_async, "stop": session.try_stop_async}
    call = calls.get(action)
    return bool(await call()) if call else False


class Levels:
    """Speaker loopback → 12 log-spaced bands, about 20 times a second (PyAudioWPatch + numpy, both optional)."""
    BANDS = 12

    def __init__(self, active: Callable[[], bool]) -> None:
        self.active = active
        self.reason = ""
        self._stop = threading.Event()

    def start(self) -> bool:
        try:
            import numpy  # noqa: F401
            import pyaudiowpatch  # noqa: F401
        except ImportError as exc:
            self.reason = f"нет пакета {exc.name}"
            return False
        threading.Thread(target=self._run, name="media-levels", daemon=True).start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        import numpy as np
        import pyaudiowpatch as pyaudio

        audio = pyaudio.PyAudio()
        try:
            device = audio.get_default_wasapi_loopback()
            rate, channels, frames = int(device["defaultSampleRate"]), max(1, int(device["maxInputChannels"])), 1024
            stream = audio.open(format=pyaudio.paInt16, channels=channels, rate=rate, input=True,
                                input_device_index=device["index"], frames_per_buffer=frames)
        except Exception as exc:  # noqa: BLE001
            self.reason = f"нет захвата звука: {exc}"
            audio.terminate()
            return
        freqs = np.fft.rfftfreq(frames, 1 / rate)
        edges = np.geomspace(40, min(16000, rate / 2 - 1), self.BANDS + 1)
        masks = [(freqs >= lo) & (freqs < hi) for lo, hi in zip(edges[:-1], edges[1:])]
        window, peak, last = np.hanning(frames), 1e-3, 0.0
        try:
            while not self._stop.is_set():
                if not self.active():
                    time.sleep(0.25)
                    continue
                raw = stream.read(frames, exception_on_overflow=False)
                samples = np.frombuffer(raw, dtype=np.int16).reshape(-1, channels).mean(axis=1) / 32768.0
                if samples.size < frames or time.time() - last < 0.05:
                    continue
                spectrum = np.abs(np.fft.rfft(samples[:frames] * window))
                bands = np.array([spectrum[m].mean() if m.any() else 0.0 for m in masks])
                peak = max(peak * 0.995, float(bands.max()), 1e-3)
                last = time.time()
                bus.emit("media.levels", {"bands": [round(float(v), 3) for v in np.clip(np.sqrt(bands / peak), 0, 1)]})
        finally:
            stream.close()
            audio.terminate()


def mmss(seconds: float) -> str:
    seconds = int(max(0, seconds))
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


def position_now(state: dict) -> float:
    position = float(state.get("position") or 0)
    if state.get("playing"):
        position += max(0.0, time.time() - float(state.get("position_at") or time.time()))
    duration = float(state.get("duration") or 0)
    return min(position, duration) if duration else position


def line_at(lines: list[dict], position: float) -> int:
    index = -1
    for number, line in enumerate(lines):
        if line["t"] > position:
            break
        index = number
    return index


class Media(Feature):
    name = "media"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.state: dict = {"active": False}
        self.lyrics: dict | None = None
        self.levels: Levels | None = None
        self.snapshot: Callable[..., Any] = snapshot_async
        self.session_control: Callable[..., Any] = control_async
        self._covers: dict[str, tuple[str, str]] = {}
        if getattr(app, "tools", None) is not None:
            register_tool(app.tools, self)

    @staticmethod
    def supported() -> bool:
        if not config.IS_WINDOWS:
            return False
        try:
            import winrt.windows.media.control  # noqa: F401
        except Exception:  # noqa: BLE001
            return False
        return True

    @staticmethod
    async def _run(coro: Any) -> Any:
        """WinRT calls run on a worker thread with its own event loop."""
        return await asyncio.to_thread(asyncio.run, coro)

    @staticmethod
    def signature(state: dict) -> tuple:
        return tuple(state.get(k) for k in ("active", "playing", "track_id", "app", "can_next", "can_prev", "can_seek"))

    async def refresh(self) -> bool:
        state = await self._run(self.snapshot(False))
        if state.get("active"):
            key = track_key(state)
            if key not in self._covers:
                full = await self._run(self.snapshot(True))
                self._covers[key] = await asyncio.to_thread(cover_info, full.get("cover_bytes") or b"")
                while len(self._covers) > 20:
                    self._covers.pop(next(iter(self._covers)))
            state["cover"], state["accent"] = self._covers[key]
            state["track_id"] = key
        changed = self.signature(state) != self.signature(self.state)
        new_track = state.get("track_id") != self.state.get("track_id")
        self.state = state
        if new_track and state.get("active") and self.settings.get("island.lyrics", False):
            self.spawn(self.load_lyrics(), "lyrics")
        return changed

    async def load_lyrics(self, force: bool = False) -> dict | None:
        state = self.state
        if not state.get("active") or not state.get("title"):
            return None
        key = state.get("track_id") or track_key(state)
        if self.lyrics and self.lyrics.get("track_id") == key and not force:
            return self.lyrics
        cached = None if force or self.db is None else self.db.kv_get("lyrics:" + key)
        if cached is None:
            try:
                cached = await fetch_lyrics(state["title"], state.get("artist", ""), state.get("album", ""),
                                            float(state.get("duration") or 0))
            except (llm.HTTPError, llm.NetworkError) as exc:
                log.info("lyrics unavailable: %s", exc)
                return None
            cached = cached or {"synced": False, "lines": [], "plain": "", "missing": True}
            if self.db is not None:
                self.db.kv_set("lyrics:" + key, cached)
        if (self.state.get("track_id") or key) != key:
            return None
        self.lyrics = {**cached, "track_id": key}
        bus.emit("media.lyrics", self.lyrics)
        return self.lyrics

    async def _session_try(self, action: str, position: float | None, app: str) -> bool:
        try:
            if app:
                return bool(await self._run(self.session_control(action, position, app)))
            return bool(await self._run(self.session_control(action, position)))
        except Exception:  # noqa: BLE001
            log.debug("media session control failed", exc_info=True)
            return False

    async def _window_key(self, action: str, app: str) -> bool:
        """Fallback for a named player without a media session yet: focus its window and press its own hotkey.
        Global media keys are never used here — Windows would send them to whatever played last (e.g. a browser)."""
        keys = {"play": "space", "pause": "space", "play_pause": "space"}
        if action not in keys or not config.IS_WINDOWS:
            return False
        from .. import win

        for query in (app, *player_tokens(app)):
            try:
                info = await asyncio.to_thread(win.find_window, query)
            except Exception:  # noqa: BLE001
                continue
            if any(b in str(info.get("process", "")).casefold() for b in BROWSERS) and \
                    not any(t in BROWSERS for t in player_tokens(app)):
                continue
            if await asyncio.to_thread(win.focus, info["hwnd"]):
                await asyncio.sleep(0.25)
                await asyncio.to_thread(win.hotkey, keys[action])
                return True
        return False

    async def control(self, action: str, position: float | None = None, app: str = "", wait: float | None = None) -> dict:
        action = {"toggle": "play_pause", "prev": "previous", "back": "previous", "skip": "next"}.get(action, action)
        if action not in MEDIA_KEYS and action != "seek":
            raise ValueError("command: play_pause, play, pause, next, previous, stop или seek")
        app = (app or "").strip()
        done, via = False, ""
        if app:
            # A just-launched player registers its media session a few seconds later: wait for it.
            deadline = time.monotonic() + (8.0 if wait is None else max(0.0, wait))
            while self.supported():
                done = await self._session_try(action, position, app)
                if done or time.monotonic() >= deadline:
                    break
                await asyncio.sleep(0.6)
            via = "session" if done else ""
            if not done and await self._window_key(action, app):
                done, via = True, "window"
            if done and self.supported():
                self.spawn(self._settle(), "settle")
            return {"ok": done, "via": via, "app": app}
        if self.supported():
            done = await self._session_try(action, position, "")
            via = "session" if done else ""
        if not done and action in MEDIA_KEYS and config.IS_WINDOWS:
            from .. import win

            await asyncio.to_thread(win.hotkey, MEDIA_KEYS[action])
            done, via = True, "keys"
        if done and self.supported():
            self.spawn(self._settle(), "settle")
        return {"ok": done, "via": via}

    async def _settle(self) -> None:
        await asyncio.sleep(0.4)
        try:
            await self.refresh()
        except Exception:  # noqa: BLE001
            log.debug("media refresh after control failed", exc_info=True)
        bus.emit("media.state", self.state)

    async def start(self) -> None:
        if not self.supported():
            log.info("media sessions unavailable (needs winrt-Windows.Media.Control); controls use media keys")
            return
        self.spawn(self._poll(), "poll")
        if self.settings.get("island.eq_edge", True):
            self.levels = Levels(lambda: bool(self.state.get("playing")))
            if not self.levels.start():
                log.info("equalizer off: %s", self.levels.reason)

    async def _poll(self) -> None:
        last = 0.0
        while True:
            try:
                changed = await self.refresh()
            except Exception:  # noqa: BLE001
                log.debug("media poll failed", exc_info=True)
                changed = False
            playing = bool(self.state.get("playing"))
            if changed or (playing and time.time() - last > 5):
                bus.emit("media.state", self.state)
                last = time.time()
            await asyncio.sleep(1.0 if playing else 2.5)

    async def close(self) -> None:
        if self.levels is not None:
            self.levels.stop()
        await super().close()

    def methods(self) -> dict:
        async def get_state() -> dict:
            return self.state

        async def control(action: str, position: float | None = None, app: str = "") -> dict:
            return await self.control(action, position, app)

        async def lyrics(force: bool = False) -> dict:
            return await self.load_lyrics(force) or {"synced": False, "lines": [], "plain": "", "missing": True}

        return {"media.state": get_state, "media.control": control, "media.lyrics": lyrics}


DONE_TEXT = {"play_pause": "пауза / воспроизведение", "play": "воспроизведение", "pause": "пауза", "next": "следующий трек",
             "previous": "предыдущий трек", "stop": "остановил", "seek": "перемотал"}


def register_tool(tools, feature: Media) -> None:
    from ..tools import HINTS, SUMMARY, A, B, N, S, Tool, ToolError, get_bool, get_float, get_str
    from ..util import clip

    async def handler(action: str, args: dict, ctx) -> str:
        if action == "control":
            command = get_str(args, "command", "play_pause").strip().lower() or "play_pause"
            try:
                result = await feature.control(command, get_float(args, "position"), get_str(args, "app"))
            except ValueError as exc:
                raise ToolError(str(exc)) from None
            if not result["ok"]:
                if result.get("app"):
                    raise ToolError(f"не нашёл плеер «{result['app']}»: приложение не запущено или ещё не готово. "
                                    "Запусти его (apps), подожди пару секунд и повтори control с тем же app; "
                                    "можно нажать кнопку воспроизведения в его окне")
                raise ToolError("нет активного плеера или управление недоступно")
            where = f" в «{result['app']}»" if result.get("app") else ""
            return DONE_TEXT.get(command, "готово") + where
        if feature.supported():
            try:
                await feature.refresh()
            except Exception:  # noqa: BLE001
                log.debug("media refresh failed", exc_info=True)
        state = feature.state
        if not state.get("active"):
            if feature.supported():
                return "сейчас ничего не играет"
            return "сведения о треке недоступны: нужны Windows и пакет winrt-Windows.Media.Control"
        if action == "now":
            mode = "играет" if state.get("playing") else "на паузе"
            where = f" в {state['app']}" if state.get("app") else ""
            who = f"{state['artist']} — " if state.get("artist") else ""
            spot = f" ({mmss(position_now(state))} из {mmss(state['duration'])})" if state.get("duration") else ""
            return f"{mode}{where}: {who}{state.get('title') or 'без названия'}{spot}"
        lyrics = await feature.load_lyrics()
        if not lyrics or lyrics.get("missing"):
            raise ToolError("текст этой песни не найден")
        lines = lyrics.get("lines") or []
        if get_bool(args, "full") or not lines:
            return clip(lyrics.get("plain") or "\n".join(line["text"] for line in lines), 4000)
        index = max(0, line_at(lines, position_now(state)))
        part = lines[max(0, index - 2): index + 6]
        return "\n".join(("▶ " if line is lines[index] else "  ") + line["text"] for line in part)

    tools.add(Tool("media", "Музыка и видео в плеерах Windows. now — что играет (трек, исполнитель, приложение, позиция); "
                   "control — управление (command: play_pause, play, pause, next, previous, stop, seek; position — "
                   "секунды для seek; app — в каком плеере. ВАЖНО: если пользователь назвал приложение («включи "
                   "Яндекс Музыку», «пауза в Spotify») — всегда передавай app, иначе команда уйдёт в текущий "
                   "плеер Windows, например в видео в браузере. Для «запусти X и включи музыку» — сначала apps, "
                   "затем media control command=play app=X); lyrics — текст песни с LRCLIB (по умолчанию текущие строки, full=true — весь).",
                   {"now": A("Смотрю, что играет", "read"), "control": A("Управляю плеером: {command}"),
                    "lyrics": A("Ищу текст песни", "read")},
                   {"command": S("control: play_pause, play, pause, next, previous, stop, seek"),
                    "position": N("seek: позиция в секундах"),
                    "app": S("control: плеер, которым управлять (Яндекс Музыка, Spotify, VLC…); пусто — текущий"), "full": B("lyrics: весь текст")}, handler, timeout=30))
    HINTS["media"] = ("музык", "песн", "трек", "играет", "плеер", "spotify", "исполнител", "перемотай")
    SUMMARY["media"] = "что играет, управление плеером, текст песни"


def create(app) -> Media:
    return Media(app)
