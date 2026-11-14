"""System monitor for the island: CPU / RAM / disk percentages, once every couple of seconds (psutil).
Also exposes system.power for the island's sleep timer (the same action the agent's tool uses)."""
from __future__ import annotations

import asyncio
import logging
import os
import time

from . import Feature

log = logging.getLogger("idos.sysmon")
POLL = 2.0


def _psutil():
    try:
        import psutil
    except ImportError:
        return None
    return psutil


def sample(psutil) -> dict:
    vm = psutil.virtual_memory()
    st = {"cpu": round(psutil.cpu_percent(interval=None)), "ram": round(vm.percent),
          "updated": time.time()}
    try:
        st["disk"] = round(psutil.disk_usage(os.environ.get("SystemDrive", "C:")).percent)
    except Exception:  # noqa: BLE001 - exotic mounts
        st["disk"] = None
    return st


class Sysmon(Feature):
    name = "sysmon"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.state: dict = {}

    async def _loop(self) -> None:
        psutil = _psutil()
        if psutil is None:
            log.info("sysmon: psutil недоступен, монитор выключен")
            return
        try:
            psutil.cpu_percent(interval=None)  # первый вызов лишь калибрует счётчик
        except Exception:  # noqa: BLE001
            return
        while True:
            await asyncio.sleep(POLL)
            try:
                self.state = await asyncio.to_thread(sample, psutil)
            except Exception:  # noqa: BLE001
                continue
            if self.settings.get("island.parts.load", False) or self.settings.get("island.parts.w_sysmon", False):
                from .. import bus
                bus.emit("sysmon.state", self.state)

    def methods(self) -> dict:
        async def get_state() -> dict:
            if not self.state:
                psutil = _psutil()
                if psutil is None:
                    return {"error": "psutil недоступен"}
                try:
                    self.state = await asyncio.to_thread(sample, psutil)
                except Exception as exc:  # noqa: BLE001
                    return {"error": str(exc)}
            return self.state

        async def power(action: str = "sleep") -> dict:
            from ..tools.system import power as run_power
            if action not in ("sleep", "hibernate", "shutdown", "restart", "lock"):
                return {"error": "неизвестное действие"}
            return {"ok": True, "text": await asyncio.to_thread(run_power, action, 0) if action != "lock"
                    else await asyncio.to_thread(run_power, "lock")}

        return {"sysmon.state": get_state, "system.power": power}


def create(app) -> Sysmon:
    return Sysmon(app)
