"""Minimal RFC 6455 WebSocket implementation on top of asyncio streams.

Used for the UI connection (server side) and for small local clients such as
OBS. No third-party dependency, so the core starts even on a bare Python.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import struct
import urllib.parse

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WSClosed(Exception):
    pass


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


def _xor(data: bytes, mask: bytes) -> bytes:
    n = len(data)
    if not n:
        return data
    m = (mask * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "little") ^ int.from_bytes(m, "little")).to_bytes(n, "little")


class WebSocket:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 max_size: int = 64 * 1024 * 1024, client_mode: bool = False) -> None:
        self.reader = reader
        self.writer = writer
        self.max_size = max_size
        self.client_mode = client_mode
        self.closed = False
        self._send_lock = asyncio.Lock()

    async def _read_frame(self) -> tuple[bool, int, bytes]:
        head = await self.reader.readexactly(2)
        b1, b2 = head[0], head[1]
        fin = bool(b1 & 0x80)
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack(">H", await self.reader.readexactly(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", await self.reader.readexactly(8))[0]
        if length > self.max_size:
            raise WSClosed("frame too large")
        if opcode >= 0x8 and (length > 125 or not fin):
            raise WSClosed("bad control frame")
        if not self.client_mode and not masked:
            raise WSClosed("client frames must be masked")  # RFC 6455 §5.1
        mask = await self.reader.readexactly(4) if masked else b""
        payload = await self.reader.readexactly(length) if length else b""
        if masked:
            payload = _xor(payload, mask)
        return fin, opcode, payload

    async def recv(self) -> tuple[str, str | bytes]:
        """Return ("text", str) or ("binary", bytes)."""
        message = bytearray()
        first_opcode = None
        while True:
            try:
                fin, opcode, payload = await self._read_frame()
            except (asyncio.IncompleteReadError, ConnectionError) as exc:
                self.closed = True
                raise WSClosed(str(exc)) from exc
            if opcode == 0x8:
                try:
                    await self._send_frame(0x8, payload[:2])
                except Exception:  # noqa: BLE001
                    pass
                self.closed = True
                raise WSClosed("closed by peer")
            if opcode == 0x9:
                await self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode in (0x1, 0x2):
                first_opcode = opcode
                message = bytearray(payload)
            elif opcode == 0x0:
                message += payload
            else:
                raise WSClosed(f"bad opcode {opcode}")
            if len(message) > self.max_size:
                raise WSClosed("message too large")
            if fin:
                if first_opcode == 0x1:
                    return "text", message.decode("utf-8")
                return "binary", bytes(message)

    async def _send_frame(self, opcode: int, payload: bytes) -> None:
        header = bytearray([0x80 | opcode])
        n = len(payload)
        mask_bit = 0x80 if self.client_mode else 0
        if n < 126:
            header.append(mask_bit | n)
        elif n < 65536:
            header.append(mask_bit | 126)
            header += struct.pack(">H", n)
        else:
            header.append(mask_bit | 127)
            header += struct.pack(">Q", n)
        if self.client_mode:
            mask = os.urandom(4)
            header += mask
            payload = _xor(payload, mask)
        async with self._send_lock:
            if self.closed and opcode != 0x8:
                raise WSClosed("socket closed")
            self.writer.write(bytes(header) + payload)
            await self.writer.drain()

    async def send_text(self, text: str) -> None:
        await self._send_frame(0x1, text.encode("utf-8"))

    async def send_binary(self, data: bytes) -> None:
        await self._send_frame(0x2, data)

    async def close(self, code: int = 1000) -> None:
        if not self.closed:
            try:
                await self._send_frame(0x8, struct.pack(">H", code))
            except Exception:  # noqa: BLE001
                pass
        self.closed = True
        try:
            self.writer.close()
        except Exception:  # noqa: BLE001
            pass


def handshake_response(key: str) -> bytes:
    return (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept_key(key)}\r\n\r\n"
    ).encode()


async def connect(url: str, timeout: float = 5.0, headers: dict[str, str] | None = None) -> WebSocket:
    """Open a client connection to ws://host:port/path."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "ws":
        raise ValueError("only ws:// is supported")
    host = parts.hostname or "127.0.0.1"
    port = parts.port or 80
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    extra = "".join(f"{k}: {v}\r\n" for k, v in (headers or {}).items())
    request = (
        f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n{extra}\r\n"
    )
    writer.write(request.encode())
    await writer.drain()
    status = await asyncio.wait_for(reader.readline(), timeout)
    if b" 101 " not in status:
        writer.close()
        raise ConnectionError("handshake failed: " + status.decode("latin-1").strip())
    accept = ""
    while True:
        line = await asyncio.wait_for(reader.readline(), timeout)
        if line in (b"\r\n", b"\n", b""):
            break
        name, _, value = line.decode("latin-1").partition(":")
        if name.strip().lower() == "sec-websocket-accept":
            accept = value.strip()
    if accept != accept_key(key):
        writer.close()
        raise ConnectionError("bad Sec-WebSocket-Accept")
    return WebSocket(reader, writer, client_mode=True)


def pack_binary(header: dict, payload: bytes) -> bytes:
    """Binary frame layout: 4-byte big-endian header length, JSON header, payload."""
    raw = json.dumps(header, ensure_ascii=False).encode("utf-8")
    return struct.pack(">I", len(raw)) + raw + payload


def unpack_binary(data: bytes) -> tuple[dict, bytes]:
    if len(data) < 4:
        raise ValueError("short binary frame")
    n = struct.unpack(">I", data[:4])[0]
    if n > len(data) - 4:
        raise ValueError("bad binary header")
    header = json.loads(data[4:4 + n].decode("utf-8"))
    if not isinstance(header, dict):
        raise ValueError("bad binary header")
    return header, data[4 + n:]
