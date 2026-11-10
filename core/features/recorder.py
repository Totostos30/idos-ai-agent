"""Screen recorder: ffmpeg with gdigrab (whole desktop, a monitor, an area or a window), optional microphone,
mp4/mkv/gif, quality presets, NVENC when the GPU supports it. The island shows a red dot with the elapsed time."""
from __future__ import annotations

import asyncio
import logging
import pathlib
import shutil
import subprocess
import time

from .. import bus, config
from ..util import hidden_flags
from . import Feature
from .capture import shot_name, unique

log = logging.getLogger("idos.recorder")
QUALITY = {"low": 32, "normal": 26, "high": 20}


def find_ffmpeg() -> str:
    for candidate in (config.TOOLS_DIR / "ffmpeg" / "bin" / "ffmpeg.exe", config.TOOLS_DIR / "ffmpeg" / "ffmpeg.exe"):
        if candidate.exists():
            return str(candidate)
    return shutil.which("ffmpeg") or ""


def build_command(ffmpeg: str, out: pathlib.Path, fps: int = 30, quality: str = "normal", area: dict | None = None,
                  window: str = "", mic: str = "", encoder: str = "libx264") -> list[str]:
    """ffmpeg arguments. `area`: {x, y, width, height}; `window`: exact window title; `mic`: DirectShow device."""
    fps = max(5, min(60, int(fps or 30)))
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "warning", "-y", "-f", "gdigrab", "-framerate", str(fps),
           "-draw_mouse", "1"]
    if area and area.get("width") and area.get("height"):
        w, h = int(area["width"]) // 2 * 2, int(area["height"]) // 2 * 2
        cmd += ["-offset_x", str(int(area.get("x", 0))), "-offset_y", str(int(area.get("y", 0))),
                "-video_size", f"{w}x{h}"]
    cmd += ["-i", f"title={window}" if window else "desktop"]
    if mic:
        cmd += ["-f", "dshow", "-i", f"audio={mic}"]
    suffix = out.suffix.lower()
    if suffix == ".gif":
        cmd += ["-vf", f"fps={min(fps, 15)},scale='min(960,iw)':-2:flags=lanczos", "-loop", "0"]
    else:
        crf = QUALITY.get(quality, 26)
        cmd += ["-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2"]
        if encoder == "h264_nvenc":
            cmd += ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", str(crf)]
        else:
            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf)]
        cmd += ["-pix_fmt", "yuv420p"]
        if mic:
            cmd += ["-c:a", "aac", "-b:a", "160k"]
        if suffix == ".mp4":
            cmd += ["-movflags", "+faststart"]
    return cmd + [str(out)]


class Recorder(Feature):
    name = "recorder"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.proc: subprocess.Popen | None = None
        self.path: pathlib.Path | None = None
        self.started = 0.0
        self._encoder: str | None = None

    def folder(self) -> pathlib.Path:
        custom = (self.settings.get("capture.record_folder", "") or "").strip()
        if custom:
            path = pathlib.Path(custom).expanduser()
        elif config.IS_WINDOWS:
            from .. import win

            path = win.known_folder("videos") / "IDOS"
        else:
            path = config.DATA / "captures"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def recording(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def state(self) -> dict:
        return {"recording": self.recording(), "path": str(self.path or ""),
                "elapsed": round(time.time() - self.started, 1) if self.recording() else 0,
                "started_at": int(self.started * 1000) if self.recording() else 0, "ffmpeg": bool(find_ffmpeg())}

    def encoder(self, ffmpeg: str) -> str:
        if self._encoder is None:
            self._encoder = "libx264"
            try:
                out = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, timeout=10,
                                     creationflags=hidden_flags()).stdout.decode("utf-8", "replace")
                if "h264_nvenc" in out and self.settings.get("capture.record_gpu", True):
                    self._encoder = "h264_nvenc"
            except (OSError, subprocess.SubprocessError):
                pass
        return self._encoder

    async def start_recording(self, area: dict | None = None, window: str = "", fmt: str = "") -> dict:
        if self.recording():
            return self.state()
        if not config.IS_WINDOWS:
            raise RuntimeError("запись экрана работает только в Windows")
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            raise RuntimeError("не найден ffmpeg: запустите Setup.ps1 или положите ffmpeg.exe в tools\\ffmpeg\\bin")
        fmt = (fmt or self.settings.get("capture.record_format", "mp4") or "mp4").lower().lstrip(".")
        if fmt not in ("mp4", "mkv", "gif"):
            fmt = "mp4"
        mic = (self.settings.get("audio.input_device", "") or "") if self.settings.get("capture.record_mic") else ""
        self.path = unique(self.folder() / shot_name("rec", ext=fmt))
        encoder = await asyncio.to_thread(self.encoder, ffmpeg) if fmt != "gif" else "libx264"
        cmd = build_command(ffmpeg, self.path, self.settings.get("capture.record_fps", 30),
                            self.settings.get("capture.record_quality", "normal"), area, window, mic, encoder)
        log.info("recording: %s", " ".join(cmd[1:]))
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                     creationflags=hidden_flags())
        self.started = time.time()
        await asyncio.sleep(0.6)
        if self.proc.poll() is not None:
            err = (self.proc.stderr.read() if self.proc.stderr else b"").decode("utf-8", "replace")[-400:]
            self.proc = None
            raise RuntimeError(f"ffmpeg не запустился: {err.strip() or 'неизвестная ошибка'}")
        bus.emit("recorder.state", self.state())
        return self.state()

    async def stop_recording(self) -> dict:
        proc, path = self.proc, self.path
        if proc is None:
            return self.state()
        try:
            if proc.stdin:
                proc.stdin.write(b"q")
                proc.stdin.flush()
                proc.stdin.close()
        except OSError:
            pass
        try:
            await asyncio.to_thread(proc.wait, 15)
        except subprocess.TimeoutExpired:
            proc.kill()
        self.proc = None
        info = {"recording": False, "path": str(path or ""), "seconds": round(time.time() - self.started, 1)}
        bus.emit("recorder.state", info)
        if path and path.exists():
            bus.card("Запись экрана", f"{path.name} — {int(info['seconds'])} с", icon="record", card_id=f"shot:{path}",
                     actions=[{"id": "open", "label": "Открыть"}, {"id": "folder", "label": "В папке"}])
        return info

    async def toggle(self, **kwargs) -> dict:
        return await (self.stop_recording() if self.recording() else self.start_recording(**kwargs))

    async def close(self) -> None:
        if self.recording():
            await self.stop_recording()
        await super().close()

    def methods(self) -> dict:
        async def start(x: int = 0, y: int = 0, width: int = 0, height: int = 0, window: str = "",
                        format: str = "") -> dict:  # noqa: A002 - UI parameter name
            area = {"x": x, "y": y, "width": width, "height": height} if width and height else None
            return await self.start_recording(area, window, format)

        async def stop() -> dict:
            return await self.stop_recording()

        async def toggle() -> dict:
            return await self.toggle()

        async def state() -> dict:
            return self.state()

        return {"recorder.start": start, "recorder.stop": stop, "recorder.toggle": toggle, "recorder.state": state}


def create(app) -> Recorder:
    return Recorder(app)
