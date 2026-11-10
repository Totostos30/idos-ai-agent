"""MCP client: stdio fake server, Streamable HTTP fake server, the mcp tool."""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TMP = tempfile.mkdtemp(prefix="idos-mcp-")
os.environ.setdefault("IDOS_HOME", TMP)
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from core import config  # noqa: E402
from core.mcp import McpError, McpManager, render  # noqa: E402
from core.tools import Context, build_registry  # noqa: E402

FAKE = str(pathlib.Path(__file__).with_name("fake_mcp_server.py"))
SEEN_HEADERS: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        pass

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN_HEADERS.append({k.lower(): v for k, v in self.headers.items()})
        if "id" not in body:
            self._send(202, b"", "application/json")
            return
        if body["method"] == "initialize":
            payload = {"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": "2025-06-18",
                                                                     "serverInfo": {"name": "http-fake"}}}
            self._send(200, json.dumps(payload).encode(), "application/json", {"Mcp-Session-Id": "s-1"})
            return
        if self.headers.get("Mcp-Session-Id") != "s-1":
            self._send(400, b"no session", "text/plain")
            return
        if body["method"] == "tools/list":
            result = {"tools": [{"name": "time", "description": "Текущее время", "inputSchema": {"type": "object"}}]}
        else:
            result = {"content": [{"type": "text", "text": "12:00"}]}
        note = json.dumps({"jsonrpc": "2.0", "method": "notifications/progress", "params": {}})
        reply = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}, ensure_ascii=False)
        sse = f"event: message\ndata: {note}\n\nevent: message\ndata: {reply}\n\n".encode()
        self._send(200, sse, "text/event-stream")


class App:
    def __init__(self, servers: dict) -> None:
        self.settings = config.Settings(pathlib.Path(tempfile.mkdtemp(dir=TMP)) / "settings.json")
        self.settings.set("mcp.servers", servers)
        self.mcp = McpManager(self.settings)


class McpTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.url = f"http://127.0.0.1:{cls.httpd.server_address[1]}/mcp"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()

    async def asyncSetUp(self) -> None:
        self.app = App({"fake": {"command": sys.executable, "args": [FAKE]},
                        "web": {"url": self.url, "headers": {"Authorization": "Bearer t"}},
                        "off": {"command": "nothing", "enabled": False}})

    async def asyncTearDown(self) -> None:
        await self.app.mcp.close()

    def test_render(self) -> None:
        text, ok = render({"content": [{"type": "text", "text": "a"}, {"type": "image", "mimeType": "image/png"},
                                       {"type": "resource", "resource": {"uri": "file:///x", "text": "b"}}]})
        self.assertEqual(text, "a\n[image image/png]\nb")
        self.assertTrue(ok)
        self.assertEqual(render({"structuredContent": {"x": 1}, "isError": True}), ('{"x": 1}', False))

    async def test_stdio(self) -> None:
        manager = self.app.mcp
        self.assertEqual(sorted(manager.configs()), ["fake", "web"])
        tools = await manager.list_tools("fake")
        self.assertEqual([t["name"] for _, t in tools], ["echo", "fail"])
        self.assertTrue(manager.running("fake"))
        self.assertEqual(await manager.call("FAKE", "echo", {"text": "привет"}), ("эхо: привет", True))
        self.assertEqual(await manager.call("fake", "fail", {}), ("сломалось", False))
        with self.assertRaises(McpError) as caught:
            await manager.call("fake", "nope", {})
        self.assertIn("Unknown tool", str(caught.exception))
        server = manager.get("fake")
        self.assertIn("got pong", " ".join(server.stderr))
        with self.assertRaises(McpError) as caught:
            await manager.call("fake", "crash", {})
        self.assertIn("завершился", str(caught.exception))
        self.assertFalse(manager.running("fake"))
        self.assertEqual(await manager.call("fake", "echo", {"text": "снова"}), ("эхо: снова", True))
        with self.assertRaises(McpError):
            manager.get("missing")

    async def test_http(self) -> None:
        SEEN_HEADERS.clear()
        tools = await self.app.mcp.list_tools("web")
        self.assertEqual(tools[0][1]["name"], "time")
        self.assertEqual(await self.app.mcp.call("web", "time", {}), ("12:00", True))
        self.assertEqual(SEEN_HEADERS[0].get("authorization"), "Bearer t")
        self.assertNotIn("mcp-protocol-version", SEEN_HEADERS[0])
        self.assertEqual(SEEN_HEADERS[-1].get("mcp-protocol-version"), "2025-06-18")
        self.assertEqual(SEEN_HEADERS[-1].get("mcp-session-id"), "s-1")

    async def test_tool(self) -> None:
        reg = build_registry(self.app, ["mcp_tool"])
        self.assertEqual([s["function"]["name"] for s in reg.schemas()], ["mcp"])
        ctx = Context(app=self.app)
        out = await reg.run("mcp", {"action": "servers"}, ctx)
        self.assertIn("fake:", out.text)
        out = await reg.run("mcp", {"action": "tools"}, ctx)
        self.assertIn("fake → echo", out.text)
        self.assertIn("text*:string", out.text)
        self.assertIn("web → time", out.text)
        out = await reg.run("mcp", {"action": "call", "server": "fake", "tool": "echo",
                                    "arguments": '{"text": "json"}'}, ctx)
        self.assertEqual((out.text, out.ok), ("эхо: json", True))
        out = await reg.run("mcp", {"action": "call", "server": "zzz", "tool": "x"}, ctx)
        self.assertFalse(out.ok)
        self.assertIn("не настроен", out.text)
        self.assertEqual(reg.kind("mcp", "call"), "danger")
        empty = App({})
        self.assertEqual(build_registry(empty, ["mcp_tool"]).schemas(), [])


if __name__ == "__main__":
    unittest.main()
