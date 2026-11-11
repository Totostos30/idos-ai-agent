"""HTTP plumbing for OpenAI-compatible APIs: urllib requests, SSE streaming and completion parsing."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import socket
import ssl
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator

log = logging.getLogger("idos.llm")
_TAG_RE = re.compile(r"<[^>]+>")


class HTTPError(Exception):
    def __init__(self, status: int, message: str, headers: dict | None = None, body: str = "") -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status, self.message, self.headers, self.body = int(status), message, headers or {}, body


class NetworkError(Exception):
    pass


class Cancelled(Exception):
    pass


def headers_dict(headers: Any) -> dict[str, str]:
    if not headers:
        return {}
    try:
        return {str(k).lower(): str(v) for k, v in headers.items()}
    except AttributeError:
        return {}


def error_message(body: str, status: int) -> str:
    text = (body or "").strip()
    try:
        data = json.loads(text) if text else None
    except ValueError:
        data = None
    if isinstance(data, list) and data:
        data = data[0]
    if isinstance(data, dict):
        err = data.get("error", data)
        if isinstance(err, dict):
            message = str(err.get("message") or err.get("msg") or err.get("detail") or "").strip()
            code = str(err.get("code") or err.get("type") or "").strip()
            if message:
                return message if not code or code in message else f"{message} [{code}]"
        elif isinstance(err, str) and err.strip():
            return err.strip()
        detail = data.get("detail") or data.get("message")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    if text.startswith("<"):
        text = " ".join(_TAG_RE.sub(" ", text).split())
    return text[:300] or f"HTTP {status}"


def open_request(url: str, data: bytes | None = None, headers: dict | None = None, method: str | None = None,
                 timeout: float = 60.0):
    request = urllib.request.Request(url, data=data, headers=headers or {},
                                     method=method or ("POST" if data is not None else "GET"))
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        raise HTTPError(exc.code, error_message(body, exc.code), headers_dict(exc.headers), body) from None
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, ssl.SSLError, OSError) as exc:
        reason = getattr(exc, "reason", None) or exc
        raise NetworkError(str(reason) or exc.__class__.__name__) from None


def request_raw(url: str, data: bytes | None = None, headers: dict | None = None, method: str | None = None,
                timeout: float = 60.0) -> tuple[bytes, dict]:
    response = open_request(url, data, headers, method, timeout)
    with response:
        try:
            return response.read(), headers_dict(response.headers)
        except (socket.timeout, TimeoutError, ConnectionError, OSError) as exc:
            raise NetworkError(str(exc) or exc.__class__.__name__) from None


def request_json(url: str, payload: Any = None, headers: dict | None = None, method: str | None = None,
                 timeout: float = 60.0) -> tuple[Any, dict]:
    merged = {"Accept": "application/json", **(headers or {})}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        merged.setdefault("Content-Type", "application/json")
    raw, response_headers = request_raw(url, data, merged, method, timeout)
    try:
        return (json.loads(raw.decode("utf-8")) if raw.strip() else {}), response_headers
    except ValueError:
        raise HTTPError(502, f"неверный JSON в ответе: {raw[:160]!r}", response_headers) from None


async def request_json_async(url: str, payload: Any = None, headers: dict | None = None, method: str | None = None,
                             timeout: float = 60.0) -> tuple[Any, dict]:
    return await asyncio.to_thread(request_json, url, payload, headers, method, timeout)


def multipart(fields: dict[str, Any], files: dict[str, tuple[str, bytes, str]]) -> tuple[bytes, str]:
    boundary = "----idos" + uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts += [f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n".encode(),
                  str(value).encode("utf-8"), b"\r\n"]
    for name, (filename, content, mime) in files.items():
        parts += [f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\n"
                  f"Content-Type: {mime}\r\n\r\n".encode(), content, b"\r\n"]
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def iter_sse(stream: Iterable[bytes]) -> Iterator[str]:
    """Yield the data payload of each server-sent event; stops at [DONE]."""
    lines: list[str] = []
    for raw in stream:
        line = raw.decode("utf-8", "replace").rstrip("\r\n") if isinstance(raw, bytes) else raw.rstrip("\r\n")
        if not line:
            if lines:
                payload, lines = "\n".join(lines), []
                if payload.strip() == "[DONE]":
                    return
                yield payload
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            value = line[5:]
            lines.append(value[1:] if value.startswith(" ") else value)
    if lines:
        payload = "\n".join(lines)
        if payload.strip() != "[DONE]":
            yield payload


def stream_error(chunk: dict) -> HTTPError:
    err = chunk.get("error")
    if isinstance(err, dict):
        message = str(err.get("message") or json.dumps(err, ensure_ascii=False))
        code = str(err.get("code") or err.get("type") or "")
        status = err.get("status_code") or err.get("status")
        try:
            status = int(status)
        except (TypeError, ValueError):
            status = 429 if "rate" in code.lower() or "exhaust" in str(status).lower() else 400
        text = message if not code or code in message else f"{message} [{code}]"
        return HTTPError(status, text, {}, json.dumps(chunk, ensure_ascii=False)[:4000])
    return HTTPError(400, str(err), {}, "")


@dataclass
class Completion:
    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)  # [{"id", "name", "arguments": json-string}]
    finish_reason: str = ""
    usage: dict = field(default_factory=dict)
    model: str = ""
    spec: str = ""
    provider: str = ""
    account: str = ""
    headers: dict = field(default_factory=dict)

    def assistant_message(self) -> dict:
        message: dict[str, Any] = {"role": "assistant", "content": self.text or ""}
        if self.tool_calls:
            message["content"] = self.text or None
            message["tool_calls"] = [{"id": call["id"], "type": "function",
                                      "function": {"name": call["name"], "arguments": call.get("arguments") or "{}"}}
                                     for call in self.tool_calls]
        return message


def _partial_tag(data: str, tag: str) -> int:
    """Length of the longest tail of ``data`` that may be the start of ``tag`` split across chunks."""
    for size in range(min(len(tag) - 1, len(data)), 0, -1):
        if data.endswith(tag[:size]):
            return size
    return 0


def _content_parts(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [part.get("text", "") for part in content if isinstance(part, dict)]
    return []


class StreamAccumulator:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.calls: dict[int, dict[str, str]] = {}
        self.finish = ""
        self.usage: dict = {}
        self.model = ""
        self.thinking = False
        self._carry = ""

    def feed(self, chunk: dict) -> str:
        if chunk.get("model"):
            self.model = str(chunk["model"])
        usage = chunk.get("usage") or (chunk.get("x_groq") or {}).get("usage")
        if isinstance(usage, dict) and usage:
            self.usage = usage
        out: list[str] = []
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or choice.get("message") or {}
            out.extend(_content_parts(delta.get("content")))
            for call in delta.get("tool_calls") or []:
                index = call.get("index")
                index = len(self.calls) if not isinstance(index, int) else index
                slot = self.calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                if call.get("id"):
                    slot["id"] = str(call["id"])
                function = call.get("function") or {}
                if function.get("name") and not slot["name"]:
                    slot["name"] = str(function["name"])
                arguments = function.get("arguments")
                if isinstance(arguments, dict):
                    slot["arguments"] = json.dumps(arguments, ensure_ascii=False)
                elif arguments:
                    slot["arguments"] += str(arguments)
            if choice.get("finish_reason"):
                self.finish = str(choice["finish_reason"])
        text = self._unthink("".join(out))
        if text:
            self.parts.append(text)
        return text

    def _unthink(self, text: str) -> str:
        """Reasoning models (Nemotron) may put their thoughts into <think>…</think>: never show or speak them."""
        if not text and not self._carry:
            return ""
        data, self._carry = self._carry + text, ""
        shown: list[str] = []
        while data:
            tag = "</think>" if self.thinking else "<think>"
            at = data.find(tag)
            if at < 0:
                keep = _partial_tag(data, tag)
                if not self.thinking:
                    shown.append(data[:len(data) - keep])
                self._carry = data[len(data) - keep:] if keep else ""
                break
            if not self.thinking:
                shown.append(data[:at])
            data = data[at + len(tag):]
            if self.thinking:
                data = data.lstrip()
            self.thinking = not self.thinking
        return "".join(shown)

    def result(self) -> Completion:
        if self._carry and not self.thinking:
            self.parts.append(self._carry)
        self._carry = ""
        calls = [{"id": slot["id"] or "call_" + uuid.uuid4().hex[:12], "name": slot["name"],
                  "arguments": slot["arguments"] or "{}"}
                 for _, slot in sorted(self.calls.items()) if slot["name"]]
        return Completion(text="".join(self.parts), tool_calls=calls, finish_reason=self.finish, usage=self.usage,
                          model=self.model)


def parse_completion(data: Any) -> Completion:
    if not isinstance(data, dict):
        raise HTTPError(502, "пустой ответ модели")
    if data.get("error"):
        raise stream_error(data)
    accumulator = StreamAccumulator()
    accumulator.feed({"model": data.get("model", ""), "usage": data.get("usage"),
                      "choices": [{"delta": c.get("message") or {}, "finish_reason": c.get("finish_reason")}
                                  for c in data.get("choices") or []]})
    return accumulator.result()


def _is_set(cancel: Any) -> bool:
    return bool(cancel is not None and cancel.is_set())


async def stream_chat(url: str, headers: dict, payload: dict, timeout: float = 120.0,
                      on_text: Callable[[str], Any] | None = None, cancel: Any = None) -> tuple[Completion, dict]:
    """POST with stream=true; text deltas go to ``on_text`` on the event loop. ``cancel``: asyncio/threading Event."""
    loop = asyncio.get_running_loop()
    holder: dict[str, Any] = {}

    def deliver(text: str) -> None:
        if on_text is not None:
            try:
                on_text(text)
            except Exception:  # noqa: BLE001
                log.exception("on_text failed")

    def work() -> tuple[Completion, dict]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        response = open_request(url, body, {**headers, "Accept": "text/event-stream"}, "POST", timeout)
        holder["response"] = response
        response_headers = headers_dict(response.headers)
        accumulator = StreamAccumulator()
        try:
            with response:
                if "text/event-stream" not in response_headers.get("content-type", ""):
                    completion = parse_completion(json.loads(response.read().decode("utf-8") or "{}"))
                    if completion.text:
                        loop.call_soon_threadsafe(deliver, completion.text)
                    return completion, response_headers
                for data in iter_sse(response):
                    if _is_set(cancel):
                        raise Cancelled()
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    if isinstance(chunk, dict) and chunk.get("error"):
                        raise stream_error(chunk)
                    text = accumulator.feed(chunk)
                    if text:
                        loop.call_soon_threadsafe(deliver, text)
        except (Cancelled, HTTPError):
            raise
        except ValueError as exc:
            raise HTTPError(502, f"неверный ответ: {exc}", response_headers) from None
        except (socket.timeout, TimeoutError, ConnectionError, OSError) as exc:
            if _is_set(cancel):
                raise Cancelled() from None
            raise NetworkError(f"поток прерван: {exc}") from None
        return accumulator.result(), response_headers

    task = asyncio.ensure_future(asyncio.to_thread(work))
    while True:
        done, _ = await asyncio.wait({task}, timeout=0.2)
        if done:
            return task.result()
        if _is_set(cancel):
            response = holder.get("response")
            if response is not None:
                try:
                    response.close()
                except Exception:  # noqa: BLE001
                    pass
            task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
            raise Cancelled()
