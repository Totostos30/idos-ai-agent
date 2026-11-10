"""Audio for TTS: a WAV/MP3 container, a loudness envelope for the island and a stoppable Windows player (MCI)."""
from __future__ import annotations

import array
import io
import logging
import os
import sys
import tempfile
import threading
import time
import uuid
import wave
from dataclasses import dataclass

log = logging.getLogger("idos.tts")
IS_WINDOWS = sys.platform == "win32"


@dataclass
class Audio:
    data: bytes
    fmt: str = "wav"  # wav | mp3
    engine: str = ""

    @property
    def duration(self) -> float:
        if self.fmt != "wav":
            return len(self.data) * 8 / 48000.0  # edge-tts sends 48 kbit/s mono MP3
        try:
            with wave.open(io.BytesIO(self.data)) as wav:
                return wav.getnframes() / float(wav.getframerate() or 1)
        except (wave.Error, EOFError):
            return 0.0


def pcm_to_wav(pcm: bytes, rate: int, channels: int = 1, width: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return buf.getvalue()


def envelope(audio: Audio, fps: int = 20) -> list[float]:
    """Relative loudness (0..1) per 1/fps s that drives the island's speaking animation; MP3 gets a flat line."""
    if audio.fmt != "wav":
        return [0.6] * max(1, int(audio.duration * fps))
    try:
        with wave.open(io.BytesIO(audio.data)) as wav:
            rate, channels, width = wav.getframerate(), wav.getnchannels(), wav.getsampwidth()
            frames = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError):
        return []
    if width != 2 or not rate:
        return []
    samples = array.array("h")
    samples.frombytes(frames[: len(frames) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    step = max(1, rate * channels // fps)
    stride = max(1, step // 200)
    levels = []
    for start in range(0, len(samples), step):
        window = samples[start:start + step:stride]
        if window:
            levels.append((sum(s * s for s in window) / len(window)) ** 0.5)
    peak = max(levels or [0.0]) or 1.0
    return [round(level / peak, 3) for level in levels]


def _mci(command: str, check: bool = True) -> str:
    import ctypes

    out = ctypes.create_unicode_buffer(256)
    code = ctypes.windll.winmm.mciSendStringW(command, out, 255, 0)
    if code and check:
        err = ctypes.create_unicode_buffer(256)
        ctypes.windll.winmm.mciGetErrorStringW(code, err, 255)
        raise RuntimeError(f"MCI: {err.value or code}")
    return out.value


class Player:
    """Blocking playback meant for a worker thread; `stop` interrupts it within ~50 ms."""

    def play(self, audio: Audio, stop: threading.Event, volume: float = 1.0) -> None:
        if not IS_WINDOWS:
            stop.wait(min(audio.duration, 30.0))
            return
        path = os.path.join(tempfile.gettempdir(), f"idos-tts-{uuid.uuid4().hex[:8]}.{audio.fmt}")
        with open(path, "wb") as fh:
            fh.write(audio.data)
        alias = "idos" + uuid.uuid4().hex[:8]
        try:
            _mci(f'open "{path}" type mpegvideo alias {alias}')
            _mci(f"setaudio {alias} volume to {int(max(0.0, min(1.0, volume)) * 1000)}", check=False)
            _mci(f"play {alias}")
            deadline = time.time() + audio.duration * 1.5 + 5
            time.sleep(0.05)
            while not stop.is_set() and time.time() < deadline:
                if _mci(f"status {alias} mode", check=False) not in ("playing", "seeking", "not ready"):
                    break
                time.sleep(0.04)
        finally:
            _mci(f"stop {alias}", check=False)
            _mci(f"close {alias}", check=False)
            try:
                os.remove(path)
            except OSError:
                log.debug("temp audio not removed: %s", path)
