"""The whole core over its real WebSocket: App(demo) + CoreServer + api methods, including a binary audio frame."""
from __future__ import annotations

import asyncio
import itertools
import json
import unittest

from core import bus, stt
from core.app import App
from core.wsproto import pack_binary


class RpcClient:
    def __init__(self, ws) -> None:
        self.ws = ws
        self.ids = itertools.count(1)
        self.events: list[dict] = []

    async def call(self, method: str, params: dict | None = None, binary: bytes | None = None):
        mid = next(self.ids)
        message = {"id": mid, "method": method, "params": params or {}}
        if binary is None:
            await self.ws.send_text(json.dumps(message, ensure_ascii=False))
        else:
            await self.ws.send_binary(pack_binary(message, binary))
        while True:
            kind, data = await asyncio.wait_for(self.ws.recv(), 10)
            if kind != "text":
                continue
            reply = json.loads(data)
            if "event" in reply:
                self.events.append(reply)
                continue
            if reply.get("id") == mid:
                if reply.get("error"):
                    raise RuntimeError(reply["error"].get("message", "error"))
                return reply.get("result")


class AppRpcTest(unittest.TestCase):
    def tearDown(self) -> None:
        bus.attach(None)

    def test_core_methods_over_websocket(self):
        async def scenario():
            app = App(demo=True)
            await app.start(port=0, write_runtime=False)
            url = "ws:" + "//127.0.0.1:" + str(app.server.port) + "/ws?token=" + app.server.token
            ws = await stt.connect(url)
            rpc = RpcClient(ws)
            out = {}
            try:
                out["ping"] = await rpc.call("core.ping")
                out["added"] = await rpc.call("keys.add", {"provider": "groq", "secret": "gsk_test_key_app_0001",
                                                          "label": "основной"})
                out["many"] = await rpc.call("keys.add_many", {"provider": "groq",
                                                              "text": "gsk_test_key_app_0002\ngsk_test_key_app_0003"})
                out["keys"] = await rpc.call("keys.list", {"provider": "groq"})
                out["router"] = await rpc.call("router.status")
                await rpc.call("settings.set", {"key": "voice.speak_replies", "value": "voice"})
                out["setting"] = await rpc.call("settings.get", {"key": "voice.speak_replies"})
                await rpc.call("settings.reset", {"key": "voice.speak_replies"})
                out["tools"] = await rpc.call("tools.list")
                out["saved"] = await rpc.call("memory.save", {"text": "Антон любит тёмную тему", "tags": "ui"})
                out["found"] = await rpc.call("memory.search", {"query": "тёмная тема"})
                out["timer"] = await rpc.call("island.timer", {"duration": "5 минут", "label": "чай"})
                out["timers"] = await rpc.call("island.timers")
                out["cancel"] = await rpc.call("island.cancel_timer", {"timer_id": out["timer"]["id"]})

                async def fake_transcribe(wav, language="ru", prompt=""):
                    return f" распознано {len(wav)} байт "

                app.router.transcribe = fake_transcribe
                out["stt"] = await rpc.call("voice.transcribe", {"sample_rate": 48000}, binary=bytes(4800 * 2))
                out["voice"] = await rpc.call("voice.status", {"probe": False})
                out["chats"] = await rpc.call("agent.chats")
                try:
                    await rpc.call("no.such.method")
                    out["unknown"] = None
                except RuntimeError as exc:
                    out["unknown"] = str(exc)
                for entry in out["keys"]:
                    await rpc.call("keys.remove", {"key_id": entry["id"]})
                out["left"] = await rpc.call("keys.list", {"provider": "groq"})
            finally:
                await ws.close()
                await app.close()
            return out, rpc.events

        out, events = asyncio.run(scenario())
        self.assertTrue(out["ping"]["pong"])
        self.assertTrue(out["ping"]["demo"])
        self.assertNotIn("secret", out["added"])
        self.assertIn("masked", out["added"])
        self.assertEqual(out["many"]["added"], 2)
        self.assertEqual(len(out["keys"]), 3)
        dumped = json.dumps([out["added"], out["keys"], out["router"], events], ensure_ascii=False)
        for suffix in ("0001", "0002", "0003"):
            self.assertNotIn("gsk_test_key_app_" + suffix, dumped)
        self.assertEqual(out["setting"], "voice")
        names = {t["name"] for t in out["tools"]}
        self.assertTrue({"files", "memory", "island", "web"} <= names, names)
        self.assertTrue(all("available" in t and "actions" in t for t in out["tools"]))
        self.assertTrue(out["found"])
        self.assertEqual((out["timer"]["label"], float(out["timer"]["seconds"])), ("чай", 300.0))
        self.assertNotIn("task", out["timer"])
        self.assertIn(out["timer"]["id"], [t["id"] for t in out["timers"]])
        self.assertTrue(out["cancel"]["ok"])
        self.assertEqual(out["stt"]["text"], "распознано 3244 байт")
        self.assertIn("engines", out["voice"]["tts"])
        self.assertIsInstance(out["chats"], list)
        self.assertTrue(out["unknown"])
        self.assertEqual(out["left"], [])
        self.assertTrue(any(e["event"] == "island.timer" for e in events))


if __name__ == "__main__":
    unittest.main()
