"""Speech recognition: GigaSTT probe/start (fake server), foreign port detection and the Groq Whisper fallback."""
from __future__ import annotations

import array
import asyncio
import io
import json
import pathlib
import socket
import unittest
import wave
from unittest import mock

from core import stt
from core.wsproto import WebSocket, WSClosed, handshake_response


class FakeSettings:
    def __init__(self, url: str) -> None:
        self.values = {"voice.stt_url": url}

    def get(self, key, default=None):
        return self.values.get(key, default)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def url_for(port: int) -> str:
    return "ws:" + "//127.0.0.1:" + str(port) + "/v1/ws"


async def fake_gigastt(reader, writer, state: dict) -> None:
    """Speaks the GigaSTT protocol the old UI used: ready → configure → PCM → stop → partial/final."""
    try:
        head = await reader.readuntil(b"\r\n\r\n")
        key = next(line.split(":", 1)[1].strip() for line in head.decode().split("\r\n")
                   if line.lower().startswith("sec-websocket-key"))
        writer.write(handshake_response(key))
        await writer.drain()
        ws = WebSocket(reader, writer)
        await ws.send_text(json.dumps({"type": "ready", "supported_rates": [16000, 48000]}))
        while True:
            kind, data = await ws.recv()
            if kind == "binary":
                state["bytes"] += len(data)
                continue
            message = json.loads(data)
            state["messages"].append(message)
            if message.get("type") == "stop":
                await ws.send_text(json.dumps({"type": "partial", "text": "привет"}))
                await ws.send_text(json.dumps({"type": "final", "text": "привет мир"}))
    except (WSClosed, asyncio.IncompleteReadError, ConnectionError, StopIteration):
        pass
    finally:
        writer.close()


def new_state() -> dict:
    return {"bytes": 0, "messages": []}


class GigaSTTTest(unittest.TestCase):
    def test_probe_quick_and_full_roundtrip(self):
        state = new_state()

        async def scenario():
            server = await asyncio.start_server(lambda r, w: fake_gigastt(r, w, state), "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            client = stt.GigaSTT(FakeSettings(url_for(port)))
            quick = await client.probe()
            full = await client.probe(full=True)
            server.close()
            return quick, full

        quick, full = asyncio.run(scenario())
        self.assertTrue(quick["ok"])
        self.assertEqual(quick["rates"], [16000, 48000])
        self.assertTrue(full["ok"])
        self.assertTrue(full["final"])
        self.assertEqual(full["text"], "привет мир")
        self.assertEqual(state["messages"][0], {"type": "configure", "sample_rate": 16000, "endpoint_mode": "manual"})
        self.assertEqual(state["messages"][-1], {"type": "stop"})
        self.assertEqual(state["bytes"], 6 * 3200)

    def test_free_port_and_foreign_program(self):
        async def foreign(reader, writer):
            try:
                await reader.read(1024)
                writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
            except OSError:
                pass
            finally:
                writer.close()

        async def scenario():
            idle = await stt.GigaSTT(FakeSettings(url_for(free_port()))).probe()
            server = await asyncio.start_server(foreign, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            client = stt.GigaSTT(FakeSettings(url_for(port)))
            busy = await client.ensure(wait=1)
            server.close()
            return idle, busy, client

        idle, busy, client = asyncio.run(scenario())
        self.assertFalse(idle["running"])
        self.assertIn("свободен", idle["reason"])
        self.assertFalse(busy["ok"])
        self.assertTrue(busy["foreign"])
        self.assertIsNone(client.proc)

    def test_missing_binary_is_explained(self):
        client = stt.GigaSTT(FakeSettings(url_for(free_port())))
        result = asyncio.run(client.ensure(wait=1))
        self.assertFalse(result["ok"])
        self.assertIn("установите GigaSTT", result["reason"])
        self.assertIsNone(client.proc)

    def test_ensure_starts_server_when_port_is_free(self):
        port, state, servers, started = free_port(), new_state(), [], []

        class FakePopen:
            def __init__(self, args, **kwargs):
                started.append((args, kwargs))
                self.pid, self.returncode = 4242, None
                handler = lambda r, w: fake_gigastt(r, w, state)  # noqa: E731
                servers.append(asyncio.get_running_loop().create_task(
                    asyncio.start_server(handler, "127.0.0.1", port)))

            def poll(self):
                return self.returncode

            def terminate(self):
                self.returncode = 0

            def wait(self, timeout=None):
                return self.returncode

            def kill(self):
                self.returncode = -9

        client = stt.GigaSTT(FakeSettings(url_for(port)))

        async def scenario():
            exe = mock.PropertyMock(return_value=pathlib.Path(__file__))
            with mock.patch.object(stt.subprocess, "Popen", FakePopen), mock.patch.object(stt.GigaSTT, "exe", exe):
                result = await client.ensure(wait=10)
            (await servers[0]).close()
            return result

        result = asyncio.run(scenario())
        self.assertTrue(result["ok"])
        self.assertTrue(result["started"])
        args, kwargs = started[0]
        self.assertEqual(args[1:6], ["serve", "--host", "127.0.0.1", "--port", str(port)])
        self.assertIn("--model-dir", args)
        self.assertTrue(kwargs["env"]["GIGASTT_VAD_MODEL_DIR"].endswith("vad"))
        client.stop()
        self.assertIsNone(client.proc)


class WhisperFallbackTest(unittest.TestCase):
    def test_downsample_and_transcribe(self):
        class FakeRouter:
            def __init__(self):
                self.calls = []

            async def transcribe(self, wav, language="ru", prompt=""):
                self.calls.append((wav, language, prompt))
                return "  привет мир \n"

        router = FakeRouter()
        pcm = array.array("h", [300, 600, 900] * 1600).tobytes()  # 0.1 s at 48 kHz
        small, rate = stt.downsample(pcm, 48000)
        self.assertEqual(rate, 16000)
        self.assertEqual(len(small), 1600 * 2)
        self.assertEqual(array.array("h", small[:4]).tolist(), [600, 600])
        self.assertEqual(asyncio.run(stt.transcribe(router, pcm, 48000, prompt="IDOS")), "привет мир")
        wav, language, prompt = router.calls[0]
        with wave.open(io.BytesIO(wav)) as audio:
            self.assertEqual((audio.getframerate(), audio.getnframes()), (16000, 1600))
        self.assertEqual((language, prompt), ("ru", "IDOS"))
        with self.assertRaises(stt.STTError):
            asyncio.run(stt.transcribe(router, b"", 16000))


if __name__ == "__main__":
    unittest.main()
