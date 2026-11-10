"""Tiny stdio MCP server used by tests/test_mcp.py."""
import json
import sys

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")


def send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


while True:
    line = sys.stdin.readline()
    if not line:
        break
    msg = json.loads(line)
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        sys.stderr.write("fake server starting\n")
        sys.stderr.flush()
        send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": msg["params"]["protocolVersion"],
                                                     "capabilities": {"tools": {}},
                                                     "serverInfo": {"name": "fake", "version": "1"}}})
    elif method == "notifications/initialized":
        send({"jsonrpc": "2.0", "id": "srv-1", "method": "ping"})
    elif method == "tools/list":
        if not (msg.get("params") or {}).get("cursor"):
            send({"jsonrpc": "2.0", "id": mid, "result": {"nextCursor": "p2", "tools": [
                {"name": "echo", "description": "Повторить текст",
                 "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}]}})
        else:
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
                {"name": "fail", "description": "Всегда ошибка", "inputSchema": {"type": "object", "properties": {}}}]}})
    elif method == "tools/call":
        name = msg["params"]["name"]
        if name == "echo":
            send({"jsonrpc": "2.0", "id": mid, "result": {"content": [
                {"type": "text", "text": "эхо: " + msg["params"]["arguments"]["text"]}]}})
        elif name == "fail":
            send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": "сломалось"}],
                                                     "isError": True}})
        elif name == "crash":
            sys.exit(3)
        else:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "Unknown tool: " + name}})
    elif "result" in msg and mid == "srv-1":
        sys.stderr.write("got pong\n")
        sys.stderr.flush()
