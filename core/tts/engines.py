"""TTS: only Silero (local neural Russian). Speech speed is applied with a pitch-preserving WSOLA time stretch,
because Silero itself has no speed parameter."""
from __future__ import annotations

import array
import asyncio
import io
import logging
import os
import pathlib
import tempfile
import threading
import urllib.request
import uuid
import wave

from .. import config
from ..util import powershell
from .player import Audio, pcm_to_wav

log = logging.getLogger("idos.tts")
SILERO_BASE = "https:" + "//models" + ".silero.ai/models/tts/ru/"
SILERO_SPEAKERS = ("aidar", "baya", "kseniya", "xenia", "eugene")


def _v(vid: str, name: str, gender: str, note: str = "") -> dict:
    return dict(id=vid, name=name, gender=gender, note=note)


SILERO_VOICES = [
    _v("xenia", "Ксения", "ж", "спокойный и чёткий"),
    _v("baya", "Байя", "ж", "мягкий и тёплый"),
    _v("kseniya", "Ксения 2", "ж", "выше и бодрее"),
    _v("aidar", "Айдар", "м", "ровный, низкий"),
    _v("eugene", "Евгений", "м", "живой, разговорный"),
]
PACKAGES: dict = dict()  # nothing to install: Silero needs torch + scipy only
SETTING_FOR = dict(silero="silero_speaker")
_SILERO_MODELS: dict = dict()
_SILERO_LOCK = threading.Lock()


class EngineError(RuntimeError):
    pass


MIN_SPEED, MAX_SPEED = 0.5, 2.0


def time_stretch(samples, speed: float, sr: int):
    """Change speech tempo without changing the pitch (WSOLA). speed 1.5 = 1.5x faster, 0.7 = slower."""
    import numpy as np

    x = np.asarray(samples, dtype=np.float32).reshape(-1)
    speed = max(MIN_SPEED, min(MAX_SPEED, float(speed or 1.0)))
    frame = max(64, int(sr * 0.032))
    frame -= frame % 2
    hop = frame // 2
    tol = max(16, int(sr * 0.008))
    if abs(speed - 1.0) < 0.02 or len(x) < frame * 2:
        return x
    window = np.hanning(frame).astype(np.float32)
    pad = np.concatenate([np.zeros(frame, np.float32), x, np.zeros(2 * frame + tol, np.float32)])
    n = len(pad)
    out = np.zeros(int(n / speed) + 3 * frame, np.float32)
    norm = np.zeros_like(out)
    pos_in, pos_out, target = 0.0, 0, None
    while True:
        nominal = int(round(pos_in))
        if nominal + frame + tol >= n or pos_out + frame > len(out):
            break
        if target is None:
            best = nominal
        else:  # the segment that continues the previous one most smoothly
            lo, hi = max(0, nominal - tol), min(n - frame, nominal + tol)
            corr = np.correlate(pad[lo:hi + frame], target, mode="valid")
            best = lo + int(np.argmax(corr))
        out[pos_out:pos_out + frame] += pad[best:best + frame] * window
        norm[pos_out:pos_out + frame] += window
        if best + hop + frame > n:
            break
        target = pad[best + hop:best + hop + frame]
        pos_in += hop * speed
        pos_out += hop
    mask = norm > 1e-3
    out[mask] /= norm[mask]
    start = int(round(frame / speed))
    return out[start:start + int(round(len(x) / speed))]


def to_pcm16(audio, speed: float, sr: int) -> bytes:
    """Silero tensor (or any float array) -> 16-bit PCM with the speech speed applied."""
    try:
        import numpy as np
    except ImportError:  # no numpy: keep the original speed
        import torch

        samples = (audio.clamp(-1, 1) * 32767).to(torch.int16).cpu()
        return array.array("h", samples.tolist()).tobytes()
    if hasattr(audio, "detach"):
        audio = audio.detach().cpu().numpy()
    x = time_stretch(audio, speed, sr)
    return (np.clip(x, -1.0, 1.0) * 32767).astype("<i2").tobytes()


class Engine:
    name = "engine"
    full_normalize = False  # True: the engine cannot read digits or Latin, so normalize() spells them out

    def __init__(self, settings) -> None:
        self.settings = settings

    def get(self, key: str, default=None):
        return self.settings.get(f"voice.{key}", default)

    def available(self) -> str:
        """'' when usable, otherwise why not."""
        return ""

    def synth(self, text: str) -> Audio:  # runs in a worker thread
        raise NotImplementedError

    def warm(self) -> None:
        """Load models ahead of the first reply."""

    def voices(self) -> list[dict]:
        return []


class SileroEngine(Engine):
    name = "silero"
    full_normalize = True

    lock = _SILERO_LOCK  # engines are rebuilt on every voice change; the loaded model is shared

    def voices(self) -> list[dict]:
        return list(SILERO_VOICES)

    def available(self) -> str:
        try:
            import torch  # noqa: F401
            import scipy  # noqa: F401  (нужен моделям Silero)
        except ImportError:
            return "нет пакета torch или scipy — Silero недоступен (см. README, раздел «Голос»)"
        return ""

    def path(self) -> pathlib.Path:
        return config.MODELS / "silero" / f"{self.get('silero_model', 'v5_ru')}.pt"

    def _download(self, path: pathlib.Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".part")
        log.info("downloading Silero model %s", path.name)
        urllib.request.urlretrieve(SILERO_BASE + path.name, part)
        if part.stat().st_size < 1_000_000:
            part.unlink(missing_ok=True)
            raise EngineError("модель Silero скачалась не полностью")
        part.replace(path)

    def _load(self):
        import torch

        name = str(self.get("silero_model", "v5_ru"))
        key = (name, str(self.get("device", "cpu")))
        if key in _SILERO_MODELS:
            return _SILERO_MODELS[key]
        path = self.path()
        if not path.exists():
            self._download(path)
        torch.set_num_threads(max(1, (os.cpu_count() or 2) // 2))
        model = torch.package.PackageImporter(str(path)).load_pickle("tts_models", "model")
        model.to(torch.device(str(self.get("device", "cpu"))))
        _SILERO_MODELS.clear()
        _SILERO_MODELS[key] = model
        return model

    def warm(self) -> None:
        with self.lock:
            self._load()

    def synth(self, text: str) -> Audio:
        with self.lock:
            model = self._load()
            speakers = list(getattr(model, "speakers", None) or SILERO_SPEAKERS)
            speaker = str(self.get("silero_speaker", "xenia"))
            if speaker not in speakers:
                speaker = "xenia" if "xenia" in speakers else speakers[0]
            rate = int(self.get("sample_rate", 48000) or 48000)
            rate = rate if rate in (8000, 24000, 48000) else 48000
            audio = model.apply_tts(text=text, speaker=speaker, sample_rate=rate, put_accent=True, put_yo=True)
        pcm = to_pcm16(audio, float(self.get("rate", 1.0) or 1.0), rate)
        return Audio(pcm_to_wav(pcm, rate), "wav", self.name)


ENGINES = {"silero": SileroEngine}
CHAINS = {"silero": ["silero"], "off": []}
LEGACY = ("piper", "edge", "windows", "winrt", "sapi")  # removed engines; old settings fall back to Silero


def build_engines(settings) -> list[Engine]:
    """Silero only; 'off' disables speech."""
    if str(settings.get("voice.tts_engine", "silero") or "silero") == "off":
        return []
    return [SileroEngine(settings)]


ENGINE_INFO = [
    ("silero", "Silero", "локально, без интернета, естественный русский"),
]


def catalog(settings) -> list[dict]:
    """Every engine with its voices and why it is unavailable (for the Settings page and the voice tool)."""
    out = []
    for key, title, note in ENGINE_INFO:
        engines = [ENGINES[name](settings) for name in CHAINS[key]]
        reasons = [e.available() for e in engines]
        usable = [e for e, r in zip(engines, reasons) if not r]
        voices: list[dict] = []
        for engine in usable or engines[:1]:
            try:
                voices = engine.voices()
            except Exception:  # noqa: BLE001 - a voice list is a bonus
                log.debug("voices() failed for %s", engine.name, exc_info=True)
            if voices:
                break
        out.append(dict(engine=key, title=title, note=note, setting="voice." + SETTING_FOR[key],
                        available=bool(usable), reason="" if usable else reasons[0],
                        installable=key in PACKAGES, voices=voices))
    return out
