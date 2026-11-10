"""Speech recognition in the core.

GigaSTT (local, streaming) does the everyday work: the UI streams the microphone straight to its WebSocket
(ready → configure → 16-bit PCM frames → partial/final → stop), exactly like the old IDOS UI. The core starts the
server, makes sure the port really belongs to GigaSTT, probes it end-to-end for the self-test and offers Groq Whisper
as the fallback (`transcribe`), fed with PCM that the UI recorded.
"""
from __future__ import annotations

import array
import asyncio
import base64
import json
import logging
import os
import pathlib
import socket
import subprocess
import sys
import time
import urllib.parse

from . import config
from .tts.player import pcm_to_wav
from .util import hidden_flags
from .wsproto import WebSocket, WSClosed, accept_key

log = logging.getLogger("idos.stt")
DEFAULT_URL = "ws:" + "//127.0.0.1:9876/v1/ws"
MAX_UPLOAD = 24 * 1024 * 1024  # Whisper accepts up to 25 MB


class STTError(RuntimeError):
    pass


def parse_url(url: str | None) -> tuple[str, int, str]:
    parts = urllib.parse.urlsplit(url or DEFAULT_URL)
    return parts.hostname or "127.0.0.1", parts.port or 9876, parts.path or "/v1/ws"


def port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


async def connect(url: str, timeout: float = 5.0) -> WebSocket:
    """Client-side WebSocket handshake (masked frames) on top of the core's own wsproto."""
    host, port, path = parse_url(url)
    query = urllib.parse.urlsplit(url).query
    target = path + ("?" + query if query else "")
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    writer.write((f"GET {target} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    await writer.drain()
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
        writer.close()
        raise STTError("сервер не принял WebSocket-подключение") from exc
    lines = head.decode("latin-1").split("\r\n")
    headers = {k.strip().lower(): v.strip() for k, _, v in (line.partition(":") for line in lines[1:] if ":" in line)}
    if " 101 " not in lines[0] + " " or headers.get("sec-websocket-accept") != accept_key(key):
        writer.close()
        raise STTError(f"ответ не похож на WebSocket: {lines[0][:80]}")
    return WebSocket(reader, writer, client_mode=True)


def downsample(pcm: bytes, rate: int, target: int = 16000) -> tuple[bytes, int]:
    """Average-decimate 16-bit mono PCM when rate is a multiple of target (48 kHz → 16 kHz) to keep uploads small."""
    if rate <= target or rate % target:
        return pcm, rate
    factor = rate // target
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    out = array.array("h", (sum(samples[i:i + factor]) // factor for i in range(0, len(samples) - factor + 1, factor)))
    if sys.byteorder == "big":
        out.byteswap()
    return out.tobytes(), target


async def transcribe(router, pcm: bytes, rate: int, language: str = "ru", prompt: str = "") -> str:
    """Groq Whisper fallback: PCM (or a ready WAV) from the UI → WAV → router.transcribe, which rotates keys."""
    if not pcm:
        raise STTError("пустая запись")
    if pcm[:4] == b"RIFF":
        wav = pcm
    else:
        small, small_rate = await asyncio.to_thread(downsample, pcm, int(rate or 16000))
        wav = pcm_to_wav(small, small_rate)
    if len(wav) > MAX_UPLOAD:
        raise STTError("запись слишком длинная — Whisper принимает до 25 МБ")
    return (await router.transcribe(wav, language=language, prompt=prompt)).strip()


class GigaSTT:
    """Starts and checks the local GigaSTT server; the UI streams audio to it directly."""

    def __init__(self, settings) -> None:
        self.settings = settings
        self.proc: subprocess.Popen | None = None
        self._log = None

    @property
    def url(self) -> str:
        return str(self.settings.get("voice.stt_url", DEFAULT_URL) or DEFAULT_URL)

    @property
    def exe(self) -> pathlib.Path:
        return config.TOOLS_DIR / "gigastt" / "bin" / ("gigastt.exe" if os.name == "nt" else "gigastt")

    @property
    def model_dir(self) -> pathlib.Path:
        return config.MODELS / "gigastt"

    def command(self) -> list[str]:
        host, port, _ = parse_url(self.url)
        return [str(self.exe), "serve", "--host", host, "--port", str(port), "--model-dir", str(self.model_dir),
                "--pool-size", "1"]

    def environment(self) -> dict:
        env = dict(os.environ)
        env.setdefault("GIGASTT_PUNCT_MODEL_DIR", str(self.model_dir / "punct"))
        env.setdefault("GIGASTT_VAD_MODEL_DIR", str(self.model_dir / "vad"))
        return env

    async def probe(self, full: bool = False, timeout: float = 5.0) -> dict:
        """Quick: connect and wait for 'ready'. Full: also stream 0.6 s of silence, send stop and await 'final'."""
        host, port, _ = parse_url(self.url)
        base = {"ok": False, "running": True, "url": self.url}
        if not await asyncio.to_thread(port_open, host, port):
            return {**base, "running": False, "reason": f"GigaSTT не запущен: порт {port} свободен"}
        try:
            ws = await connect(self.url, timeout)
        except (OSError, asyncio.TimeoutError, STTError) as exc:
            return {**base, "foreign": True, "reason": f"порт {port} занят другой программой: {exc or 'нет ответа'}"}
        try:
            kind, data = await asyncio.wait_for(ws.recv(), timeout)
            message = json.loads(data) if kind == "text" else {}
            if message.get("type") != "ready":
                return {**base, "foreign": True, "reason": f"на порту {port} отвечает не GigaSTT"}
            rates = [int(r) for r in message.get("supported_rates") or []]
            result = {**base, "ok": True, "rates": rates}
            if full:
                result.update(await self._roundtrip(ws, rates, timeout))
            return result
        except (asyncio.TimeoutError, WSClosed, ValueError) as exc:
            return {**base, "reason": f"GigaSTT не ответил: {exc or 'тайм-аут'}"}
        finally:
            await ws.close()

    async def _roundtrip(self, ws: WebSocket, rates: list[int], timeout: float) -> dict:
        rate = 16000 if not rates or 16000 in rates else rates[0]
        await ws.send_text(json.dumps({"type": "configure", "sample_rate": rate, "endpoint_mode": "manual"}))
        frame = bytes(2 * rate // 10)
        for _ in range(6):
            await ws.send_binary(frame)
        started = time.monotonic()
        await ws.send_text(json.dumps({"type": "stop"}))
        try:
            while True:
                kind, data = await asyncio.wait_for(ws.recv(), timeout)
                if kind != "text":
                    continue
                message = json.loads(data)
                if message.get("type") == "final":
                    return {"final": True, "text": message.get("text", ""),
                            "latency_ms": int((time.monotonic() - started) * 1000)}
                if message.get("type") == "error":
                    return {"ok": False, "reason": f"GigaSTT: {message.get('message') or message}"}
        except asyncio.TimeoutError:
            return {"final": False, "note": "после stop не пришёл final — проверьте версию GigaSTT"}

    async def ensure(self, wait: float = 25.0) -> dict:
        """Start GigaSTT unless it already answers. A foreign program on the port is reported, never killed."""
        status = await self.probe()
        if status["ok"] or status.get("foreign"):
            return status
        if not self.exe.exists():
            return {**status, "reason": f"нет {self.exe} — установите GigaSTT (README, раздел «Голос»)"}
        if self.proc is None or self.proc.poll() is not None:
            config.LOGS.mkdir(parents=True, exist_ok=True)
            if self._log is None:
                self._log = open(config.LOGS / "gigastt.log", "ab")  # noqa: SIM115 - stays open for the child
            self.proc = subprocess.Popen(self.command(), env=self.environment(), cwd=str(config.ROOT),
                                         stdout=self._log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                         creationflags=hidden_flags())
            log.info("started GigaSTT, pid %s", self.proc.pid)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            await asyncio.sleep(0.5)
            if self.proc.poll() is not None:
                return {"ok": False, "running": False, "url": self.url,
                        "reason": f"GigaSTT завершился с кодом {self.proc.returncode} — см. logs/gigastt.log"}
            status = await self.probe(timeout=2.0)
            if status["ok"]:
                return {**status, "started": True}
        return {"ok": False, "running": False, "starting": True, "url": self.url,
                "reason": "GigaSTT ещё загружается — при первом запуске он скачивает модель, подождите пару минут"}

    def stop(self) -> None:
        """Stop only the server this core started."""
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        if self._log is not None:
            self._log.close()
            self._log = None
