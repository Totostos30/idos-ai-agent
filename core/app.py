"""Wires the IDOS core together and runs it.

Settings, API keys, the model router, memory, skills, tools, voice output (Silero), speech recognition (GigaSTT),
the agent's browser and MCP servers live on one `App` object that tools receive as `ctx.app`; the WebSocket server
exposes them to the island UI through the methods in `api.py`.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal

from . import bus, config
from .agent import Agent
from .db import DB
from .features import load_features
from .keystore import KeyStore
from .mcp import McpManager
from .memory import Memory
from .router import Router
from .server import CoreServer
from .skills import Skills
from .stt import GigaSTT
from .tools import build_registry
from .tools.browser import BrowserManager
from .tts import TTS
from .util import install_asyncio_error_handler, setup_logging

log = logging.getLogger("idos.app")


def _log_failure(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        log.error("background task %s failed", task.get_name(), exc_info=task.exception())


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return True
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        pass
    if os.name == "nt":
        import ctypes

        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class WinContext:
    """Active window and keyboard layout for the agent prompt (Windows only)."""

    def foreground(self) -> dict:
        from . import win

        return win.foreground()

    def layout(self) -> str:
        from . import win

        return win.keyboard_layout()


class App:
    """Everything the agent, the tools and the UI share."""

    def __init__(self, settings=None, demo: bool = False, features: list[str] | None = None) -> None:
        config.ensure_dirs()
        self.demo = demo
        self.settings = settings or config.settings()
        self.db = DB()
        self.keystore = KeyStore()
        self.keystore.migrate_plaintext()
        self.router = Router(self.settings, self.keystore, bus.emit)
        self.memory = Memory(self.db)
        self.skills = Skills()
        self.mcp = McpManager(self.settings)
        self.browser = BrowserManager(self.settings)
        self.tts = TTS(self.settings)
        self.stt = GigaSTT(self.settings)
        self.winctx = WinContext() if config.IS_WINDOWS and not demo else None
        from .scenarios import Scenarios
        self.scenarios = Scenarios(self)
        self.tools = build_registry(self)
        self.features = load_features(self, features)
        self.agent = Agent(self)
        self.server: CoreServer | None = None
        self.stopping = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()

    def background(self, coro, name: str) -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(_log_failure)
        return task

    def feature(self, name: str):
        return next((f for f in self.features if f.name == name), None)

    async def start(self, port: int = 9877, host: str = "127.0.0.1", write_runtime: bool = True) -> None:
        bus.attach(asyncio.get_running_loop())
        if config.IS_WINDOWS:
            from . import win

            win.set_dpi_aware()
        from .api import build_methods

        self.server = CoreServer(host, port, write_runtime=write_runtime)
        self.server.add_methods(build_methods(self))
        await self.server.start()
        if not self.demo:
            for feature in self.features:
                try:
                    await feature.start()
                except Exception:  # noqa: BLE001 - a feature must not stop the core
                    log.warning("feature %s failed to start", feature.name, exc_info=True)
        if not self.demo:
            if str(self.settings.get("voice.stt", "gigastt") or "") == "gigastt":
                self.background(self._start_stt(), "stt-start")
            self.background(self.tts.warm(), "tts-warm")
        parent = int(os.environ.get("IDOS_PARENT_PID", "0") or 0)
        if parent:
            self.background(self._watch_parent(parent), "parent-watch")
        bus.emit("core.ready", {"port": self.server.port, "version": config.VERSION, "demo": self.demo})

    async def _start_stt(self) -> None:
        status = await self.stt.ensure()
        bus.emit("voice.stt", status)
        if not status.get("ok"):
            log.info("GigaSTT: %s", status.get("reason"))

    async def _watch_parent(self, pid: int) -> None:
        """Exit together with the Tauri shell that started the core."""
        misses = 0
        while not self.stopping.is_set():
            await asyncio.sleep(3)
            misses = 0 if process_alive(pid) else misses + 1
            if misses >= 3:
                log.info("parent process %s is gone, shutting down", pid)
                self.stopping.set()

    def shutdown(self) -> None:
        self.stopping.set()

    async def close(self) -> None:
        self.stopping.set()
        steps = [("agent", self.agent.stop), ("tts", self.tts.close), ("stt", self.stt.stop),
                 ("browser", self.browser.close), ("mcp", self.mcp.close)]
        steps += [(type(f).__name__, f.close) for f in self.features if hasattr(f, "close")]
        if self.server is not None:
            steps.append(("server", self.server.stop))
        steps.append(("db", self.db.close))
        for name, step in steps:
            try:
                result = step()
                if asyncio.iscoroutine(result):
                    await asyncio.wait_for(result, 10)
            except Exception:  # noqa: BLE001 - keep closing the rest
                log.warning("closing %s failed", name, exc_info=True)
        for task in list(self._tasks):
            task.cancel()
        bus.attach(None)


async def run(port: int = 9877, demo: bool = False) -> None:
    setup_logging(os.environ.get("IDOS_LOG_LEVEL", "INFO"))
    install_asyncio_error_handler(asyncio.get_running_loop())
    app = App(demo=demo)
    await app.start(port)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, app.shutdown)
        except (NotImplementedError, RuntimeError, ValueError):
            pass  # Windows: Ctrl+C arrives as KeyboardInterrupt
    try:
        await app.stopping.wait()
    finally:
        await app.close()
