"""Speech output with Silero, caches phrases, plays them sentence by sentence
and streams loudness to the island so the character talks in time with the voice."""
from __future__ import annotations

import asyncio
import collections
import importlib
import logging
import subprocess
import sys
import threading
import time

from .. import bus
from ..util import Worker
from .engines import Engine, EngineError, build_engines
from .normalize import clean, normalize, sentences
from .player import Audio, Player, envelope

__all__ = ["TTS", "Audio", "Engine", "EngineError", "Player", "clip_speech", "normalize", "sentences"]

log = logging.getLogger("idos.tts")
CACHE_SIZE = 64
RETRY_AFTER = 600.0  # a failed engine is skipped for 10 minutes
FPS = 20
VOICE_KEYS = ("silero_model", "silero_speaker", "sample_rate", "rate")
SAMPLE = "Привет! Я Эйдос. Теперь я говорю вот таким голосом."
SILENT = " .,!?…-—"


def clip_speech(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = max(text.rfind(mark, 0, limit) for mark in (". ", "! ", "? "))
    return text[:cut + 1] if cut > limit // 3 else text[:limit].rsplit(" ", 1)[0] + "…"


class TTS:
    def __init__(self, settings, engines: list[Engine] | None = None, player: Player | None = None, emit=None) -> None:
        self.settings = settings
        self._fixed = engines is not None
        self._engines = engines
        self.player = player or Player()
        self._emit = emit
        self.cache: collections.OrderedDict[tuple, Audio] = collections.OrderedDict()
        self.failed: dict[str, float] = {}
        self.reasons: dict[str, str] = {}
        self.synth_worker = Worker("tts")
        self.play_worker = Worker("tts-play")
        self.stop_event = threading.Event()
        self.task: asyncio.Future | None = None
        self.speaking = False
        self.last_engine = ""
        on_change = getattr(settings, "on_change", None)
        if callable(on_change):
            on_change(self._on_setting)

    def emit(self, event: str, data: dict) -> None:
        (self._emit or bus.emit)(event, data)

    def _on_setting(self, key: str, _value=None) -> None:
        if str(key).startswith("voice"):
            if not self._fixed:
                self._engines = None
            self.failed.clear()

    def engines(self) -> list[Engine]:
        if self._engines is None:
            self._engines = build_engines(self.settings)
        return self._engines

    def usable(self) -> list[Engine]:
        now, out = time.monotonic(), []
        for engine in self.engines():
            if now - self.failed.get(engine.name, -1e9) < RETRY_AFTER:
                continue
            reason = engine.available()
            if reason:
                if self.reasons.get(engine.name) != reason:
                    self.reasons[engine.name] = reason
                    log.info("tts engine %s unavailable: %s", engine.name, reason)
                continue
            out.append(engine)
        return out

    def status(self) -> dict:
        return {
            "speaking": self.speaking,
            "engine": self.last_engine,
            "engines": [{"name": e.name, "reason": e.available() or self.reasons.get(e.name, ""),
                         "failed": e.name in self.failed} for e in self.engines()],
        }

    def _voice(self) -> str:
        return "|".join(str(self.settings.get(f"voice.{key}", "")) for key in VOICE_KEYS)

    async def _synth(self, chunk: str) -> Audio | None:
        for engine in self.usable():
            text = normalize(chunk, 10_000, full=engine.full_normalize, lexicon=self.settings.get("voice.lexicon") or None)
            if not text.strip(SILENT):
                return None
            key = (engine.name, self._voice(), text)
            audio = self.cache.get(key)
            if audio is not None:
                self.cache.move_to_end(key)
                return audio
            try:
                audio = await self.synth_worker.run(lambda e=engine, t=text: e.synth(t))
            except Exception as exc:  # noqa: BLE001 - fall through to the next engine
                self.failed[engine.name] = time.monotonic()
                log.warning("tts engine %s failed: %s", engine.name, exc)
                self.emit("tts.error", {"engine": engine.name, "message": str(exc)[:300]})
                continue
            self.cache[key] = audio
            while len(self.cache) > CACHE_SIZE:
                self.cache.popitem(last=False)
            return audio
        return None

    async def say(self, text: str, interrupt: bool = True, wait: bool = False) -> bool:
        """Speak in the background (the caller is not blocked unless wait=True). False when nothing will be said."""
        limit = int(self.settings.get("voice.max_speech_chars", 700) or 700)
        chunks = [c for c in sentences(clip_speech(clean(text), limit)) if c.strip(SILENT)]
        if not chunks or not self.engines():
            return False
        if interrupt:
            await self.stop()
        elif self.task is not None and not self.task.done():
            await asyncio.wait([self.task])
        stop = threading.Event()
        self.stop_event = stop
        self.task = asyncio.ensure_future(self._run(chunks, stop))
        if wait:
            await asyncio.wait([self.task])
        return True

    async def _run(self, chunks: list[str], stop: threading.Event) -> None:
        volume = float(self.settings.get("voice.volume", 0.9) or 0.0)
        spoken = 0
        self.speaking = True
        pending: asyncio.Future | None = asyncio.ensure_future(self._synth(chunks[0]))
        try:
            for index in range(len(chunks)):
                audio = await pending
                pending = asyncio.ensure_future(self._synth(chunks[index + 1])) if index + 1 < len(chunks) else None
                if stop.is_set():
                    break
                if audio is None:
                    continue
                spoken += 1
                self.last_engine = audio.engine
                self.emit("tts.state", {"state": "speaking", "text": chunks[index], "index": index,
                                        "total": len(chunks), "engine": audio.engine, "fps": FPS,
                                        "duration": round(audio.duration, 2), "envelope": envelope(audio, FPS)})
                await self.play_worker.run(lambda a=audio: self.player.play(a, stop, volume))
                if stop.is_set():
                    break
            if not spoken and not stop.is_set():
                self.emit("tts.error", {"engine": "", "message": "ни один голосовой движок не сработал — проверьте настройки голоса"})
        except Exception:  # noqa: BLE001
            log.exception("tts playback failed")
        finally:
            self.speaking = False
            self.emit("tts.state", {"state": "idle"})

    async def stop(self) -> None:
        """Interrupt current speech: barge-in, the stop hotkey or a newer reply."""
        self.stop_event.set()
        task = self.task
        if task is not None and not task.done():
            done, _ = await asyncio.wait([task], timeout=2.0)
            if not done:
                task.cancel()

    def stop_now(self) -> None:
        """Thread-safe stop for hotkey handlers outside the event loop."""
        self.stop_event.set()

    async def warm(self) -> None:
        """Load the first available engine in the background so the first reply is not delayed."""
        for engine in self.usable():
            try:
                await self.synth_worker.run(engine.warm)
                return
            except Exception as exc:  # noqa: BLE001
                self.failed[engine.name] = time.monotonic()
                log.warning("tts warm-up of %s failed: %s", engine.name, exc)

    async def apply(self, engine: str = "", voice: str = "", rate=None, sample: bool = True, text: str = "") -> dict:
        """Switch engine / voice / speed, save it and (optionally) say a sample with the new voice."""
        from .engines import CHAINS, LEGACY, SETTING_FOR

        engine = (engine or str(self.settings.get("voice.tts_engine", "silero") or "silero")).strip().lower()
        if engine in LEGACY:
            engine = "silero"
        if engine not in CHAINS:
            raise ValueError(f"неизвестный движок {engine}: silero или off")
        values = dict()
        values["voice.tts_engine"] = engine
        if voice and engine in SETTING_FOR:
            values["voice." + SETTING_FOR[engine]] = str(voice)
        if rate is not None:
            values["voice.rate"] = round(max(0.5, min(2.0, float(rate))), 2)
        if engine != "off":
            await self.stop()
        self.settings.update(values)
        self._on_setting("voice")
        for key, value in values.items():
            self.emit("settings.changed", dict(key=key, value=value))
        result = dict(ok=True, engine=engine, values=values)
        if engine == "off" or not sample:
            return result
        first = next((e for e in self.engines() if e.name in CHAINS[engine]), None)
        reason = first.available() if first is not None else "движок недоступен"
        if reason:
            result.update(ok=False, error=reason)
            return result
        try:
            await self.synth_worker.run(first.warm)
        except Exception as exc:  # noqa: BLE001 - download / load errors go to the UI
            result.update(ok=False, error=f"не удалось загрузить голос: {str(exc)[:300]}")
            return result
        self.failed.pop(first.name, None)
        self.last_engine = ""
        await self.say(text or SAMPLE, wait=True)
        result["played_by"] = self.last_engine
        if self.last_engine and self.last_engine not in CHAINS[engine]:
            result.update(ok=False, error=f"выбранный голос не сработал, прозвучал запасной ({self.last_engine})"
                                             + (f": {self.reasons.get(first.name, '')}" if self.reasons.get(first.name) else ""))
        return result

    async def install(self, engine: str) -> dict:
        """pip install the engine's package into the core's own Python."""
        from .engines import PACKAGES

        package = PACKAGES.get(str(engine))
        if not package:
            return dict(ok=False, error=f"для {engine} нечего устанавливать")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = await asyncio.to_thread(
                subprocess.run, [sys.executable, "-m", "pip", "install", "--upgrade", package],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900, creationflags=flags)
        except subprocess.TimeoutExpired:
            return dict(ok=False, error="pip не уложился в 15 минут")
        importlib.invalidate_caches()
        self.reasons.clear()
        self._on_setting("voice")
        out = ((proc.stdout or "") + (proc.stderr or ""))[-1500:]
        if proc.returncode != 0:
            log.warning("pip install %s failed: %s", package, out[-500:])
            return dict(ok=False, error=f"pip не смог установить {package}", log=out)
        return dict(ok=True, package=package, log=out)

    async def close(self) -> None:
        await self.stop()
