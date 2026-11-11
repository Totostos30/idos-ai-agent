"""HTTP + WebSocket server connecting the UI (and local hooks) to the core.

Protocol over WebSocket:
  UI -> core   {"id": 1, "method": "chat.send", "params": {...}}
  core -> UI   {"id": 1, "result": ...} | {"id": 1, "error": {"message": "..."}}
  core -> UI   {"event": "agent.delta", "data": {...}}
Binary frames (both directions) use wsproto.pack_binary: JSON header + payload.
"""
from __future__ import annotations

import asyncio
import hmac
import inspect
import json
import logging
import os
import secrets
import time
import urllib.parse
from typing import Any, Callable

from . import config
from .bus import bus
from .wsproto import WebSocket, WSClosed, handshake_response, pack_binary, unpack_binary

log = logging.getLogger("server")
BINARY_BACKLOG = 64
MAX_RPC_INFLIGHT = 64
MAX_RPC_TEXT_BYTES = 2 * 1024 * 1024
LOSSY_EVENTS = {"cursor", "media.levels", "mic.level", "media.position"}
REASONS = {200: "OK", 204: "No Content", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found",
           413: "Payload Too Large", 500: "Internal Server Error", 503: "Service Unavailable"}


class RpcError(Exception):
    """Error whose message is safe to show to the user."""


class Session:
    def __init__(self, ws: WebSocket, peer: str) -> None:
        self.ws = ws
        self.peer = peer
        self.id = secrets.token_hex(4)
        self.window = ""
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=5000)
        self.inflight = 0

    def push(self, kind: str, payload: Any, *, critical: bool = False) -> None:
        try:
            self.queue.put_nowait((kind, payload))
        except asyncio.QueueFull:
            if not critical:
                log.warning("session %s queue full, dropping event", self.id)
                return
            # Never discard an RPC reply just because a client stopped reading
            # high-frequency events. Prefer removing an older event first.
            kept: list[tuple[str, Any]] = []
            removed = False
            while True:
                try:
                    item = self.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if not removed and item[0] == "event":
                    removed = True
                    continue
                kept.append(item)
            for item in kept:
                self.queue.put_nowait(item)
            try:
                self.queue.put_nowait((kind, payload))
            except asyncio.QueueFull:
                log.error("session %s queue full of critical messages", self.id)
            if not removed:
                log.warning("session %s queue full, could not evict an event", self.id)

    async def sender(self) -> None:
        while True:
            kind, payload = await self.queue.get()
            if kind == "close":
                return
            try:
                if kind in {"text", "event"}:
                    await self.ws.send_text(payload)
                else:
                    await self.ws.send_binary(payload)
            except (WSClosed, ConnectionError, OSError):
                return


class CoreServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 9877, token: str | None = None,
                 write_runtime: bool = True) -> None:
        self.host = host
        self.port = port
        self.token = token or secrets.token_urlsafe(24)
        self.write_runtime_file = write_runtime
        self.sessions: set[Session] = set()
        # Strong references to running RPC handlers: asyncio keeps only weak ones, a bare create_task can be
        # garbage-collected mid-flight (the UI then waits for an answer that never comes).
        self._tasks: set[asyncio.Task] = set()
        self.methods: dict[str, Callable[..., Any]] = {}
        self.server: asyncio.base_events.Server | None = None
        self.started = time.time()
        self._stopping = False
        bus.subscribe(self._on_event)

    # ------------------------------------------------------------ registry
    def add_methods(self, mapping: dict[str, Callable[..., Any]]) -> None:
        self.methods.update(mapping)

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        try:
            self.server = await asyncio.start_server(self._handle, self.host, self.port, limit=2 ** 20)
        except OSError:
            log.warning("port %s busy, choosing a free one", self.port)
            self.server = await asyncio.start_server(self._handle, self.host, 0, limit=2 ** 20)
        self.port = self.server.sockets[0].getsockname()[1]
        if self.write_runtime_file:
            config.write_runtime({"port": self.port, "token": self.token, "pid": os.getpid(),
                                  "started": self.started, "version": config.VERSION})
        log.info("core listening on %s:%s", self.host, self.port)

    async def stop(self) -> None:
        self._stopping = True
        for session in list(self.sessions):
            await session.ws.close()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        bus.unsubscribe(self._on_event)
        if self.write_runtime_file:
            config.clear_runtime(os.getpid())

    # ------------------------------------------------------------ events
    def _on_event(self, event: str, data: Any, binary: bytes | None) -> None:
        if not self.sessions:
            return
        if binary is not None:
            frame = pack_binary({"event": event, "data": data}, binary)
            for session in list(self.sessions):
                # Audio/images for a stalled window: drop instead of piling up megabytes in the queue.
                if session.queue.qsize() > BINARY_BACKLOG:
                    continue
                session.push("binary", frame)
            return
        try:
            text = json.dumps({"event": event, "data": data}, ensure_ascii=False, default=str)
        except (TypeError, ValueError, RecursionError):
            log.warning("dropping unserialisable event %s", event, exc_info=True)
            return
        lossy = event in LOSSY_EVENTS
        for session in list(self.sessions):
            if lossy and session.queue.qsize() > 100:
                continue
            session.push("event", text)

    # ------------------------------------------------------------ http
    async def _respond(self, writer: asyncio.StreamWriter, status: int, payload: Any,
                       content_type: str = "application/json; charset=utf-8") -> None:
        if payload is None:
            body = b""
        elif isinstance(payload, (bytes, bytearray)):
            body = bytes(payload)
        else:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        head = (f"HTTP/1.1 {status} {REASONS.get(status, 'OK')}\r\nContent-Type: {content_type}\r\n"
                f"Content-Length: {len(body)}\r\nAccess-Control-Allow-Origin: *\r\n"
                "Access-Control-Allow-Headers: *\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n")
        writer.write(head.encode() + body)
        await writer.drain()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = str(writer.get_extra_info("peername"))
        upgraded = False
        try:
            line = await asyncio.wait_for(reader.readline(), 15)
            if not line:
                return
            try:
                request_method, target, _ = line.decode("latin-1").strip().split(" ", 2)
            except ValueError:
                return
            headers: dict[str, str] = {}
            while True:
                raw = await asyncio.wait_for(reader.readline(), 15)
                if raw in (b"\r\n", b"\n", b""):
                    break
                name, _, value = raw.decode("latin-1").partition(":")
                headers[name.strip().lower()] = value.strip()
            path, _, query = target.partition("?")
            params = {k: v[-1] for k, v in urllib.parse.parse_qs(query).items()}
            if request_method == "OPTIONS":
                await self._respond(writer, 204, None)
                return
            if path == "/health":
                await self._respond(writer, 200, {"ok": True, "version": config.VERSION,
                                                  "uptime": round(time.time() - self.started)})
                return
            token = params.get("token") or headers.get("x-idos-token", "")
            if not token or not hmac.compare_digest(token.encode(), self.token.encode()):
                await self._respond(writer, 401, {"error": "unauthorized"})
                return
            if path == "/ws" and headers.get("upgrade", "").lower() == "websocket":
                key = headers.get("sec-websocket-key")
                if not key:
                    await self._respond(writer, 400, {"error": "missing key"})
                    return
                writer.write(handshake_response(key))
                await writer.drain()
                upgraded = True
                await self._ws_session(WebSocket(reader, writer), peer)
                return
            await self._respond(writer, 404, {"error": "not found"})
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
            pass
        except Exception:  # noqa: BLE001
            log.exception("http handler failed")
            try:
                await self._respond(writer, 500, {"error": "internal error"})
            except Exception:  # noqa: BLE001
                pass
        finally:
            if not upgraded:
                try:
                    writer.close()
                except Exception:  # noqa: BLE001
                    pass

    # ------------------------------------------------------------ websocket
    async def _ws_session(self, ws: WebSocket, peer: str) -> None:
        session = Session(ws, peer)
        self.sessions.add(session)
        sender = asyncio.create_task(session.sender())
        session.push("text", json.dumps({"event": "hello", "data": {"version": config.VERSION,
                                                                     "session": session.id}}))
        try:
            while True:
                kind, payload = await ws.recv()
                binary: bytes | None = None
                if kind == "text":
                    if len(payload.encode("utf-8")) > MAX_RPC_TEXT_BYTES:
                        log.warning("session %s sent oversized RPC text frame", session.id)
                        continue
                    try:
                        message = json.loads(payload)
                    except ValueError:
                        continue
                else:
                    try:
                        message, binary = unpack_binary(payload)  # type: ignore[arg-type]
                    except ValueError:
                        continue
                if isinstance(message, dict):
                    if session.inflight >= MAX_RPC_INFLIGHT:
                        call_id = message.get("id")
                        if call_id is not None:
                            session.push("text", json.dumps({"id": call_id,
                                                               "error": {"message": "Слишком много одновременных запросов"}},
                                                              ensure_ascii=False), critical=True)
                        continue
                    session.inflight += 1
                    task = asyncio.create_task(self._dispatch(session, message, binary))
                    self._tasks.add(task)
                    def done(completed: asyncio.Task, current: Session = session) -> None:
                        current.inflight = max(0, current.inflight - 1)
                        self._tasks.discard(completed)
                    task.add_done_callback(done)
        except WSClosed:
            pass
        except Exception:  # noqa: BLE001
            log.exception("websocket session failed")
        finally:
            self.sessions.discard(session)
            session.push("close", None)
            await ws.close()
            try:
                await asyncio.wait_for(sender, 2)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                sender.cancel()
            bus.emit("ui.disconnected", {"session": session.id, "window": session.window})

    async def _dispatch(self, session: Session, message: dict, binary: bytes | None) -> None:
        call_id = message.get("id")
        name = str(message.get("method", ""))
        params = message.get("params") or {}
        if not isinstance(params, dict):
            params = {"value": params}
        if binary is not None:
            params["_binary"] = binary
        try:
            if name == "session.hello":
                session.window = str(params.get("window", ""))
                result: Any = {"ok": True, "session": session.id}
                bus.emit("ui.connected", {"session": session.id, "window": session.window})
            else:
                handler = self.methods.get(name)
                if handler is None:
                    raise RpcError(f"Неизвестный метод: {name}")
                result = await self.invoke(handler, params, session)
            reply: dict[str, Any] = {"id": call_id, "result": result}
        except RpcError as exc:
            reply = {"id": call_id, "error": {"message": str(exc)}}
        except Exception as exc:  # noqa: BLE001
            log.exception("rpc %s failed", name)
            reply = {"id": call_id, "error": {"message": f"{type(exc).__name__}: {exc}"}}
        if call_id is None:
            return
        try:
            text = json.dumps(reply, ensure_ascii=False, default=str)
        except (TypeError, ValueError) as exc:
            text = json.dumps({"id": call_id, "error": {"message": f"bad result: {exc}"}})
        session.push("text", text, critical=True)

    async def invoke(self, handler: Callable[..., Any], params: dict, session: Session | None = None) -> Any:
        kwargs = dict(params)
        try:
            sig = inspect.signature(handler)
        except (TypeError, ValueError):
            sig = None
        if sig is not None:
            if "_session" in sig.parameters:
                kwargs["_session"] = session
            if not any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values()):
                kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}
        if inspect.iscoroutinefunction(handler):
            return await handler(**kwargs)
        result = await asyncio.to_thread(handler, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result
