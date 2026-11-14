"""Per-app volume mixer for the island panel (in the spirit of EarTrumpet): pycaw audio sessions on the
shared audio worker thread (COM must stay on one thread — see status.py). The island gets the master
volume and a list of app sessions; sliders call mixer.set, changes come back as mixer.changed."""
from __future__ import annotations

import asyncio
import logging

from ..tools import system as sysmod
from . import Feature

log = logging.getLogger("idos.mixer")


def _guarded(fn):
    try:
        return fn()
    except ImportError:
        raise
    except Exception:  # noqa: BLE001 - устройство могло смениться
        sysmod.reset_endpoints()
        raise


def _sessions_impl() -> dict:
    """Enumerate active audio sessions: {master: int, apps: [{pid, name, volume, mute}]}."""
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    speaker = AudioUtilities.GetSpeakers()
    if speaker is None:
        raise OSError("нет аудиоустройства")
    from comtypes import CLSCTX_ALL
    raw = getattr(speaker, "_dev", None) or speaker
    unknown = raw.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    ep = unknown.QueryInterface(IAudioEndpointVolume)
    out = {"master": round(ep.GetMasterVolumeLevelScalar() * 100), "apps": []}
    del unknown, ep
    for session in AudioUtilities.GetAllSessions():
        try:
            proc = session.Process
            name = None
            if proc is not None:
                try:
                    import psutil
                    name = psutil.Process(proc.pid).name()
                except Exception:  # noqa: BLE001 - процесс мог выйти
                    name = proc.name()
                pid = proc.pid
            else:
                pid, name = 0, session.DisplayName or "Системный звук"
            vol = session.SimpleAudioVolume
            out["apps"].append({"pid": pid, "name": name or "Приложение",
                                "volume": round(vol.GetMasterVolume() * 100),
                                "mute": bool(vol.GetMute())})
        except Exception:  # noqa: BLE001 - сессия исчезла между вызовами
            continue
    return out


def _set_impl(pid: int, volume: int | None, mute: bool | None) -> None:
    from pycaw.pycaw import AudioUtilities
    if pid in (0, None):
        ep = sysmod.endpoint_for("out")
        if volume is not None:
            ep.SetMasterVolumeLevelScalar(max(0, min(100, volume)) / 100, None)
        if mute is not None:
            ep.SetMute(1 if mute else 0, None)
        return
    for session in AudioUtilities.GetAllSessions():
        proc = session.Process
        if proc is not None and proc.pid == pid:
            vol = session.SimpleAudioVolume
            if volume is not None:
                vol.SetMasterVolume(max(0, min(100, volume)) / 100, None)
            if mute is not None:
                vol.SetMute(1 if mute else 0, None)
            return
    raise OSError("сессия не найдена")


def audio_call(fn):
    worker = sysmod.audio_worker()
    return worker.run_sync(fn, timeout=6)


class Mixer(Feature):
    name = "mixer"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.state: dict = {}

    def _poll(self) -> None:
        try:
            st = audio_call(lambda: _guarded(_sessions_impl))
        except ImportError:
            log.info("mixer: pycaw недоступен, микшер выключен")
            self.state = {"error": "pycaw недоступен"}
            self._stop = True
            return
        except Exception as exc:  # noqa: BLE001
            self.state = {"error": str(exc)}
            return
        if st and st != self.state:
            self.state = st
            self._changed()

    def _changed(self) -> None:
        from .. import bus
        bus.emit("mixer.changed", self.state)

    def _loop(self) -> None:
        import time
        self._stop = False
        while not getattr(self, "_stop", False):
            time.sleep(2.0)
            self._poll()

    async def start(self) -> None:
        try:
            self.spawn(asyncio.to_thread(self._loop), "poll")
        except Exception:  # noqa: BLE001
            log.debug("mixer poll failed", exc_info=True)

    def methods(self) -> dict:
        async def get_state() -> dict:
            return self.state or {}

        async def refresh() -> dict:
            self._poll()
            return self.state or {}

        async def set_volume(pid: int = 0, volume: int | None = None, mute: bool | None = None) -> dict:
            try:
                audio_call(lambda: _guarded(lambda: _set_impl(int(pid or 0), volume, mute)))
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}
            self._poll()
            return {"ok": True, "state": self.state}

        return {"mixer.state": get_state, "mixer.refresh": refresh, "mixer.set": set_volume}


def create(app) -> Mixer:
    return Mixer(app)
