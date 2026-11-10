"""Small shared helpers: logging, text clipping, hidden subprocesses, single-thread workers."""
from __future__ import annotations

import asyncio
import concurrent.futures
import datetime as _dt
import logging
import logging.handlers
import os
import queue
import re
import subprocess
import sys
import threading
from typing import Any, Callable

from . import config

log = logging.getLogger("idos.util")
CREATE_NO_WINDOW = 0x08000000
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

_SECRET_RE = re.compile(
    r"(gsk_[A-Za-z0-9]{12,}|csk-[A-Za-z0-9]{12,}|sk-(?:or-|ant-|proj-)?[A-Za-z0-9_\-]{16,}|"
    r"AIza[0-9A-Za-z_\-]{20,}|xai-[A-Za-z0-9]{16,}|(?<=Bearer )[A-Za-z0-9._\-]{12,})")


def mask_secrets(text: str) -> str:
    return _SECRET_RE.sub(lambda m: m.group(0)[:6] + "…" + m.group(0)[-3:], text)


class SecretFilter(logging.Filter):
    """Never let an API key reach a log file."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        masked = mask_secrets(message)
        if masked != message:
            record.msg, record.args = masked, ()
        return True


def setup_logging(level: str = "INFO", filename: str = "core.log") -> None:
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    console.addFilter(SecretFilter())
    root.addHandler(console)
    try:
        config.LOGS.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(config.LOGS / filename, maxBytes=2_000_000, backupCount=3,
                                                       encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        handler.addFilter(SecretFilter())
        root.addHandler(handler)
    except OSError as exc:
        log.warning("file logging disabled: %s", exc)


def clip(text: Any, limit: int = 2000) -> str:
    text = "" if text is None else str(text)
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def clip_middle(text: Any, limit: int = 2000) -> str:
    text = "" if text is None else str(text)
    if len(text) <= limit:
        return text
    head = int(limit * 0.6)
    tail = max(0, limit - head - 20)
    return text[:head].rstrip() + f"\n…[пропущено {len(text) - head - tail} симв.]…\n" + (text[-tail:] if tail else "")


def human_bytes(size: float) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if abs(size) < 1024 or unit == "ГБ":
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ТБ"


def decode_output(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    for encoding in ("utf-8", "cp866", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def hidden_flags() -> int:
    return CREATE_NO_WINDOW if config.IS_WINDOWS else 0


def run_hidden(args: list[str] | str, timeout: float = 30, shell: bool = False, cwd: str | None = None,
               input_text: str | None = None) -> subprocess.CompletedProcess:
    """Run a process without flashing a console window; stdout/stderr are decoded text."""
    proc = subprocess.run(args, shell=shell, cwd=cwd, capture_output=True, timeout=timeout,
                          input=input_text.encode("utf-8") if input_text is not None else None,
                          creationflags=hidden_flags())
    return subprocess.CompletedProcess(proc.args, proc.returncode, decode_output(proc.stdout),
                                       decode_output(proc.stderr))


def powershell(script: str, timeout: float = 30) -> tuple[int, str, str]:
    prefix = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ProgressPreference='SilentlyContinue'; "
    exe = "powershell.exe" if config.IS_WINDOWS else "pwsh"
    try:
        proc = run_hidden([exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                           prefix + script], timeout=timeout)
    except FileNotFoundError:
        return 127, "", "PowerShell не найден"
    except subprocess.TimeoutExpired:
        return 124, "", f"тайм-аут {timeout:g} с"
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def now_text(moment: _dt.datetime | None = None) -> str:
    moment = moment or _dt.datetime.now()
    return f"{moment:%Y-%m-%d %H:%M}, {WEEKDAYS[moment.weekday()]}"


class Debouncer:
    """Call ``fn`` once, ``delay`` seconds after the last ``trigger()``."""

    def __init__(self, delay: float, fn: Callable[[], Any]) -> None:
        self.delay, self.fn = delay, fn
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def trigger(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.delay, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        try:
            self.fn()
        except Exception:  # noqa: BLE001
            log.exception("debounced call failed")

    def cancel(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


class Worker:
    """A dedicated daemon thread for objects that must stay on one thread (COM, UI Automation, Playwright)."""

    def __init__(self, name: str, init: Callable[[], Any] | None = None) -> None:
        self.name = name
        self._init = init
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        if self._init is not None:
            try:
                self._init()
            except Exception:  # noqa: BLE001
                log.exception("worker %s init failed", self.name)
        while True:
            fn, loop, fut = self._queue.get()
            try:
                result, error = fn(), None
            except BaseException as exc:  # noqa: BLE001 - delivered to the caller
                result, error = None, exc
            if loop is None:
                if error is None:
                    fut.set_result(result)
                else:
                    fut.set_exception(error)
                continue
            try:
                loop.call_soon_threadsafe(self._deliver, fut, result, error)
            except RuntimeError:  # the event loop is already closed
                pass

    @staticmethod
    def _deliver(fut: asyncio.Future, result: Any, error: BaseException | None) -> None:
        if fut.cancelled():
            return
        if error is None:
            fut.set_result(result)
        else:
            fut.set_exception(error)

    async def run(self, fn: Callable[[], Any]) -> Any:
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._queue.put((fn, loop, fut))
        return await fut

    def run_sync(self, fn: Callable[[], Any], timeout: float = 60) -> Any:
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self._queue.put((fn, None, fut))
        return fut.result(timeout)


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}
