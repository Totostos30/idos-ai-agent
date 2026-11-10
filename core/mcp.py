"""Minimal MCP client: stdio servers (newline-delimited JSON-RPC 2.0) and Streamable HTTP servers.

Servers are configured in settings ``mcp.servers`` using the familiar Claude Desktop shape::

    {"files": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:/Users"]},
     "remote": {"url": "https://example.com/mcp", "headers": {"Authorization": "Bearer ..."}}}
"""
from __future__ import annotations

import asyncio
import collections
import itertools
import json
import logging
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from typing import Any

from . import config
from .util import clip

log = logging.getLogger("idos.mcp")
PROTOCOL = "2025-06-18"
CREATE_NO_WINDOW = 0x08000000


class McpError(RuntimeError):
    pass


def render(result: dict[str, Any]) -> tuple[str, bool]:
    """Flatten a tools/call result into text for the model."""
    parts: list[str] = []
    for item in result.get("content") or []:
        kind = item.get("type")
        if kind == "text":
            parts.append(item.get("text", ""))
        elif kind == "resource":
            res = item.get("resource") or {}
            parts.append(res.get("text") or f"[ресурс {res.get('uri', '')}]")
        elif kind == "resource_link":
            parts.append(" ".join(p for p in ("[ссылка", item.get("name") or "", item.get("uri", "") + "]") if p))
        elif kind in ("image", "audio"):
            parts.append(f"[{kind} {item.get('mimeType', '')}]")
    if not any(parts) and result.get("structuredContent") is not None:
        parts.append(json.dumps(result["structuredContent"], ensure_ascii=False))
    return "\n".join(p for p in parts if p).strip() or "(пустой ответ)", not result.get("isError", False)


class Server:
    def __init__(self, name: str, timeout: float = 60) -> None:
        self.name = name
        self.timeout = float(timeout or 60)
        self.tools: list[dict[str, Any]] = []
        self.server_info: dict[str, Any] = {}
        self.protocol = PROTOCOL
        self._ids = itertools.count(1)
        self._start_lock: asyncio.Lock | None = None

    @property
    def running(self) -> bool:
        raise NotImplementedError

    async def _open(self) -> None:
        raise NotImplementedError

    async def request(self, method: str, params: dict | None = None, timeout: float | None = None) -> dict:
        raise NotImplementedError

    async def notify(self, method: str, params: dict | None = None) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError

    async def ensure(self) -> None:
        if self._start_lock is None:
            self._start_lock = asyncio.Lock()
        async with self._start_lock:
            if self.running:
                return
            await self._open()
            try:
                result = await self.request("initialize", {
                    "protocolVersion": PROTOCOL, "capabilities": {},
                    "clientInfo": {"name": "IDOS", "version": config.VERSION}})
                self.server_info = result.get("serverInfo") or {}
                self.protocol = result.get("protocolVersion") or PROTOCOL
                await self.notify("notifications/initialized")
                await self._load_tools()
            except BaseException:
                await self.close()
                raise

    async def _load_tools(self) -> None:
        tools: list[dict[str, Any]] = []
        cursor = None
        for _ in range(20):
            res = await self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(res.get("tools") or [])
            cursor = res.get("nextCursor")
            if not cursor:
                break
        self.tools = tools

    async def list_tools(self) -> list[dict[str, Any]]:
        await self.ensure()
        return self.tools

    async def call(self, tool: str, arguments: dict | None = None) -> tuple[str, bool]:
        await self.ensure()
        result = await self.request("tools/call", {"name": tool, "arguments": arguments or {}},
                                    timeout=max(self.timeout, 120))
        return render(result)


class StdioServer(Server):
    def __init__(self, name: str, command: str, args: list | None = None, env: dict | None = None,
                 cwd: str | None = None, timeout: float = 60) -> None:
        super().__init__(name, timeout)
        if not command:
            raise McpError(f"у MCP-сервера {name} не указана команда (command)")
        self.command = command
        self.args = [str(a) for a in (args or [])]
        self.env = {str(k): str(v) for k, v in (env or {}).items()}
        self.cwd = cwd or None
        self.proc: asyncio.subprocess.Process | None = None
        self.pending: dict[Any, asyncio.Future] = {}
        self.stderr: collections.deque[str] = collections.deque(maxlen=20)
        self._tasks: list[asyncio.Task] = []
        self._alive = False
        self._write_lock: asyncio.Lock | None = None

    @property
    def running(self) -> bool:
        return self._alive and self.proc is not None and self.proc.returncode is None

    async def _open(self) -> None:
        exe = shutil.which(self.command) or self.command
        kwargs: dict[str, Any] = {"creationflags": CREATE_NO_WINDOW} if os.name == "nt" else {}
        try:
            self.proc = await asyncio.create_subprocess_exec(
                exe, *self.args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, env={**os.environ, **self.env}, cwd=self.cwd, limit=32 * 1024 * 1024,
                **kwargs)
        except OSError as exc:
            raise McpError(f"не удалось запустить MCP-сервер {self.name}: {exc}") from exc
        self._alive = True
        self._write_lock = asyncio.Lock()
        self._tasks = [asyncio.create_task(self._read()), asyncio.create_task(self._read_errors())]

    async def _read_errors(self) -> None:
        assert self.proc and self.proc.stderr
        while True:
            line = await self.proc.stderr.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                self.stderr.append(clip(text, 300))
                log.debug("mcp %s stderr: %s", self.name, text)

    async def _read(self) -> None:
        assert self.proc and self.proc.stdout
        try:
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    log.debug("mcp %s: not JSON: %s", self.name, clip(line.decode("utf-8", "replace"), 200))
                    continue
                if isinstance(msg, dict):
                    await self._dispatch(msg)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("mcp %s reader failed", self.name)
        finally:
            self._alive = False
            tail = " | ".join(self.stderr)
            for fut in self.pending.values():
                if not fut.done():
                    fut.set_exception(McpError(f"MCP-сервер {self.name} завершился" + (f": {clip(tail, 400)}" if tail else "")))
            self.pending.clear()

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        if "method" in msg:
            if "id" in msg:
                method = msg["method"]
                if method == "ping":
                    reply: dict[str, Any] = {"result": {}}
                elif method == "roots/list":
                    reply = {"result": {"roots": []}}
                else:
                    reply = {"error": {"code": -32601, "message": f"method {method} is not supported"}}
                await self._send({"jsonrpc": "2.0", "id": msg["id"], **reply})
            elif msg["method"] == "notifications/tools/list_changed":
                asyncio.create_task(self._reload())
            return
        fut = self.pending.pop(msg.get("id"), None)
        if fut is None or fut.done():
            return
        if "error" in msg:
            fut.set_exception(McpError(f"{self.name}: {(msg.get('error') or {}).get('message', 'ошибка')}"))
        else:
            fut.set_result(msg.get("result") or {})

    async def _reload(self) -> None:
        try:
            await self._load_tools()
        except McpError as exc:
            log.warning("mcp %s: tools reload failed: %s", self.name, exc)

    async def _send(self, payload: dict[str, Any]) -> None:
        if not self.proc or not self.proc.stdin or self.proc.returncode is not None or self._write_lock is None:
            raise McpError(f"MCP-сервер {self.name} не запущен")
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        async with self._write_lock:
            try:
                self.proc.stdin.write(data)
                await self.proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise McpError(f"MCP-сервер {self.name} закрыл соединение") from exc

    async def request(self, method: str, params: dict | None = None, timeout: float | None = None) -> dict:
        rid = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self.pending[rid] = fut
        try:
            await self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
            return await asyncio.wait_for(fut, timeout or self.timeout)
        except asyncio.TimeoutError as exc:
            raise McpError(f"{self.name}: нет ответа на {method}") from exc
        finally:
            self.pending.pop(rid, None)

    async def notify(self, method: str, params: dict | None = None) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})

    async def close(self) -> None:
        proc, self.proc = self.proc, None
        self._alive = False
        if proc is not None and proc.returncode is None:
            try:
                if proc.stdin:
                    proc.stdin.close()
                await asyncio.wait_for(proc.wait(), 2)
            except (asyncio.TimeoutError, ProcessLookupError, OSError):
                try:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True,
                                       creationflags=CREATE_NO_WINDOW, check=False)
                    else:
                        proc.kill()
                    await asyncio.wait_for(proc.wait(), 3)
                except (asyncio.TimeoutError, ProcessLookupError, OSError):
                    pass
        for task in self._tasks:
            task.cancel()
        self._tasks = []


class HttpServer(Server):
    def __init__(self, name: str, url: str, headers: dict | None = None, timeout: float = 60) -> None:
        super().__init__(name, timeout)
        self.url = url
        self.headers = {str(k): str(v) for k, v in (headers or {}).items()}
        self.session = ""
        self._ok = False

    @property
    def running(self) -> bool:
        return self._ok

    async def _open(self) -> None:
        self._ok = True
        self.session = ""

    def _post(self, payload: dict[str, Any], timeout: float) -> dict | None:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "User-Agent": f"IDOS/{config.VERSION}", **self.headers}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        if payload.get("method") != "initialize":
            headers["MCP-Protocol-Version"] = self.protocol
        req = urllib.request.Request(self.url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - user-configured server
                session = resp.headers.get("Mcp-Session-Id")
                if session:
                    self.session = session
                ctype = resp.headers.get("Content-Type", "")
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            if exc.code == 404 and self.session:
                self._ok = False
            raise McpError(f"{self.name}: HTTP {exc.code} {detail}".strip()) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise McpError(f"{self.name}: нет связи ({exc})") from exc
        if "id" not in payload or not body.strip():
            return None
        if "text/event-stream" in ctype:
            for block in body.replace("\r\n", "\n").split("\n\n"):
                data = "\n".join(line[5:].lstrip() for line in block.split("\n") if line.startswith("data:"))
                if not data:
                    continue
                try:
                    msg = json.loads(data)
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get("id") == payload["id"]:
                    return msg
            raise McpError(f"{self.name}: в ответе нет результата")
        try:
            return json.loads(body)
        except ValueError as exc:
            raise McpError(f"{self.name}: непонятный ответ сервера") from exc

    async def request(self, method: str, params: dict | None = None, timeout: float | None = None) -> dict:
        payload = {"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params or {}}
        msg = await asyncio.to_thread(self._post, payload, timeout or self.timeout)
        if not msg:
            raise McpError(f"{self.name}: пустой ответ на {method}")
        if "error" in msg:
            raise McpError(f"{self.name}: {(msg.get('error') or {}).get('message', 'ошибка')}")
        return msg.get("result") or {}

    async def notify(self, method: str, params: dict | None = None) -> None:
        await asyncio.to_thread(self._post, {"jsonrpc": "2.0", "method": method,
                                             **({"params": params} if params else {})}, 15)

    async def close(self) -> None:
        self._ok = False
        self.session = ""


class McpManager:
    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.servers: dict[str, Server] = {}
        self._signatures: dict[str, str] = {}

    def configs(self) -> dict[str, dict[str, Any]]:
        raw = self.settings.get("mcp.servers", {}) or {}
        return {name: cfg for name, cfg in raw.items() if isinstance(cfg, dict) and cfg.get("enabled", True)}

    def get(self, name: str) -> Server:
        configs = self.configs()
        key = name if name in configs else next((n for n in configs if n.lower() == name.strip().lower()), None)
        if key is None:
            raise McpError(f"MCP-сервер «{name}» не настроен. Есть: {', '.join(configs) or 'нет'}")
        cfg = configs[key]
        signature = json.dumps(cfg, sort_keys=True, ensure_ascii=False)
        server = self.servers.get(key)
        if server is None or self._signatures.get(key) != signature:
            if server is not None:
                asyncio.get_running_loop().create_task(server.close())
            timeout = cfg.get("timeout", 60)
            if cfg.get("url"):
                server = HttpServer(key, cfg["url"], cfg.get("headers"), timeout)
            else:
                server = StdioServer(key, cfg.get("command", ""), cfg.get("args"), cfg.get("env"), cfg.get("cwd"), timeout)
            self.servers[key] = server
            self._signatures[key] = signature
        return server

    def running(self, name: str) -> bool:
        server = self.servers.get(name)
        return bool(server and server.running)

    async def list_tools(self, name: str | None = None) -> list[tuple[str, dict[str, Any]]]:
        out: list[tuple[str, dict[str, Any]]] = []
        for key in ([name] if name else list(self.configs())):
            try:
                server = self.get(key)
                out.extend((server.name, tool) for tool in await server.list_tools())
            except McpError as exc:
                if name:
                    raise
                log.warning("mcp %s unavailable: %s", key, exc)
        return out

    async def call(self, name: str, tool: str, arguments: dict | None = None) -> tuple[str, bool]:
        return await self.get(name).call(tool, arguments)

    async def close(self) -> None:
        for server in list(self.servers.values()):
            await server.close()
        self.servers.clear()
        self._signatures.clear()
