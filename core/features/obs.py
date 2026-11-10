"""OBS Studio over obs-websocket v5 (OBS 28+): record/stream state on the island, start/stop, pause, scenes.
Uses the built-in RFC 6455 client (core.wsproto), so no extra package is needed."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import itertools
import json
import logging

from .. import bus, wsproto
from . import Feature

log = logging.getLogger("idos.obs")
EVENTS_OUTPUTS = 1 << 6  # EventSubscription.Outputs
EVENTS_SCENES = 1 << 2  # EventSubscription.Scenes
RETRY = 20


def auth_string(password: str, salt: str, challenge: str) -> str:
    """obs-websocket v5: base64(sha256(base64(sha256(password + salt)) + challenge))."""
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


def identify_message(hello: dict, password: str) -> dict:
    data: dict = {"rpcVersion": 1, "eventSubscriptions": EVENTS_OUTPUTS | EVENTS_SCENES}
    auth = (hello.get("d") or {}).get("authentication")
    if auth:
        data["authentication"] = auth_string(password, auth["salt"], auth["challenge"])
    return {"op": 1, "d": data}


class Obs(Feature):
    name = "obs"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.ws: wsproto.WebSocket | None = None
        self.pending: dict[str, asyncio.Future] = {}
        self.ids = itertools.count(1)
        self.state = {"connected": False, "recording": False, "paused": False, "streaming": False, "scene": ""}

    def cfg(self, key: str, default=None):
        return self.settings.get(f"integrations.obs.{key}", default)

    async def connect(self) -> None:
        port = int(self.cfg("port", 4455) or 4455)
        ws = await wsproto.connect(f"ws://127.0.0.1:{port}/", timeout=4)
        kind, raw = await asyncio.wait_for(ws.recv(), 5)
        hello = json.loads(raw)
        await ws.send_text(json.dumps(identify_message(hello, str(self.cfg("password", "") or ""))))
        kind, raw = await asyncio.wait_for(ws.recv(), 5)
        if json.loads(raw).get("op") != 2:
            await ws.close()
            raise ConnectionError("OBS отклонил подключение (проверьте пароль)")
        self.ws = ws
        self.state["connected"] = True

    async def request(self, request_type: str, data: dict | None = None, timeout: float = 6) -> dict:
        if self.ws is None:
            raise RuntimeError("OBS не подключён: включите WebSocket-сервер в OBS и интеграцию в настройках")
        rid = str(next(self.ids))
        fut = asyncio.get_running_loop().create_future()
        self.pending[rid] = fut
        await self.ws.send_text(json.dumps({"op": 6, "d": {"requestType": request_type, "requestId": rid,
                                                            "requestData": data or {}}}))
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self.pending.pop(rid, None)

    def on_message(self, msg: dict) -> None:
        op, d = msg.get("op"), msg.get("d") or {}
        if op == 7:
            fut = self.pending.get(str(d.get("requestId")))
            if fut and not fut.done():
                status = d.get("requestStatus") or {}
                if status.get("result"):
                    fut.set_result(d.get("responseData") or {})
                else:
                    fut.set_exception(RuntimeError(status.get("comment") or f"OBS: ошибка {status.get('code')}"))
            return
        if op != 5:
            return
        event, data = d.get("eventType"), d.get("eventData") or {}
        before = dict(self.state)
        if event == "RecordStateChanged":
            st = data.get("outputState", "")
            self.state["recording"] = st in ("OBS_WEBSOCKET_OUTPUT_STARTED", "OBS_WEBSOCKET_OUTPUT_PAUSED",
                                             "OBS_WEBSOCKET_OUTPUT_RESUMED")
            self.state["paused"] = st == "OBS_WEBSOCKET_OUTPUT_PAUSED"
            if st == "OBS_WEBSOCKET_OUTPUT_STOPPED" and data.get("outputPath"):
                bus.card("OBS", f"Запись сохранена: {data['outputPath']}", kind="success", icon="record")
        elif event == "StreamStateChanged":
            self.state["streaming"] = bool(data.get("outputActive"))
        elif event == "CurrentProgramSceneChanged":
            self.state["scene"] = data.get("sceneName", "")
        if self.state != before:
            bus.emit("obs.state", dict(self.state))

    async def sync(self) -> None:
        rec = await self.request("GetRecordStatus")
        stream = await self.request("GetStreamStatus")
        scene = await self.request("GetCurrentProgramScene")
        self.state.update({"recording": bool(rec.get("outputActive")), "paused": bool(rec.get("outputPaused")),
                           "streaming": bool(stream.get("outputActive")),
                           "scene": scene.get("currentProgramSceneName") or scene.get("sceneName", "")})
        bus.emit("obs.state", dict(self.state))

    async def _reader(self) -> None:
        while self.ws is not None:
            try:
                kind, raw = await self.ws.recv()
            except wsproto.WSClosed:
                break
            if kind == "text":
                try:
                    self.on_message(json.loads(raw))
                except ValueError:
                    continue
        self.ws = None
        self.state["connected"] = False
        for fut in self.pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("OBS отключился"))
        bus.emit("obs.state", dict(self.state))

    async def _loop(self) -> None:
        while True:
            if self.cfg("enabled", False) and self.ws is None:
                try:
                    await self.connect()
                    reader = asyncio.get_running_loop().create_task(self._reader())
                    await self.sync()
                    await reader
                except (OSError, ConnectionError, asyncio.TimeoutError, ValueError, RuntimeError) as exc:
                    log.debug("obs connect failed: %s", exc)
            await asyncio.sleep(RETRY)

    async def start(self) -> None:
        self.spawn(self._loop(), "link")

    async def close(self) -> None:
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:  # noqa: BLE001
                pass
            self.ws = None
        await super().close()

    def methods(self) -> dict:
        async def state() -> dict:
            return dict(self.state)

        async def record(command: str = "toggle") -> dict:
            name = {"start": "StartRecord", "stop": "StopRecord", "pause": "PauseRecord", "resume": "ResumeRecord",
                    "toggle": "ToggleRecord"}.get(command, "ToggleRecord")
            return await self.request(name)

        async def stream(command: str = "toggle") -> dict:
            return await self.request({"start": "StartStream", "stop": "StopStream"}.get(command, "ToggleStream"))

        async def scenes() -> list:
            data = await self.request("GetSceneList")
            return [s.get("sceneName") for s in reversed(data.get("scenes") or [])]

        async def set_scene(name: str) -> dict:
            return await self.request("SetCurrentProgramScene", {"sceneName": name})

        return {"obs.state": state, "obs.record": record, "obs.stream": stream, "obs.scenes": scenes,
                "obs.set_scene": set_scene}


def create(app) -> Obs:
    return Obs(app)
