"""Entry point: `python -m core` (started by the Tauri shell, or manually for debugging)."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys


def main() -> None:
    if sys.platform == "win32":
        # Must be set before comtypes is imported anywhere. In MTA a COM pointer may be released by the
        # garbage collector on any thread; in STA that crashed the core with an access violation.
        sys.coinit_flags = 0  # COINIT_MULTITHREADED
    parser = argparse.ArgumentParser(prog="python -m core", description="IDOS core service")
    parser.add_argument("--port", type=int, default=int(os.environ.get("IDOS_PORT", "9877")))
    parser.add_argument("--demo", action="store_true", help="демо-режим интерфейса без Windows API")
    parser.add_argument("--selftest", action="store_true", help="проверить модули и выйти")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    if args.selftest:
        from .selftest import run_selftest

        sys.exit(run_selftest())
    _crash_log()
    from .app import run

    import logging
    log = logging.getLogger("idos")
    try:
        asyncio.run(run(port=args.port, demo=args.demo))
    except KeyboardInterrupt:
        log.info("core stopped by Ctrl+C")
    except BaseException:
        log.critical("core crashed", exc_info=True)
        raise
    else:
        log.info("core stopped normally")


_CRASH_FILE = None


def _crash_log() -> None:
    """Write a stack trace of every thread to logs/crash.log if Python dies in native code
    (access violation in COM/torch/audio drivers) — such crashes leave nothing in core.log."""
    global _CRASH_FILE
    import faulthandler
    import logging
    import pathlib
    import threading
    import time

    try:
        from . import config
        logs = pathlib.Path(config.LOGS)
        logs.mkdir(parents=True, exist_ok=True)
        _CRASH_FILE = open(logs / "crash.log", "a", encoding="utf-8", buffering=1)
        _CRASH_FILE.write("\n=== core start %s pid %s ===\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), os.getpid()))
        faulthandler.enable(_CRASH_FILE, all_threads=True)
    except Exception:  # noqa: BLE001 - diagnostics must never stop the core
        faulthandler.enable()

    def thread_hook(hook_args) -> None:
        logging.getLogger("idos").error("thread %s crashed", getattr(hook_args.thread, "name", "?"),
                                        exc_info=(hook_args.exc_type, hook_args.exc_value, hook_args.exc_traceback))

    threading.excepthook = thread_hook


if __name__ == "__main__":
    main()
