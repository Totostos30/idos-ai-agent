"""Status for the island: battery (warnings, charging animation), network (online, Wi-Fi name and signal), VPN
detection by network adapters and an optional public IP lookup."""
from __future__ import annotations

import asyncio
import logging
import re
import socket
import subprocess
import threading
import time

from .. import bus, config, llm
from ..util import decode_output, hidden_flags
from . import Feature

log = logging.getLogger("idos.status")
VPN_HINTS = ("vpn", "wireguard", "wintun", "tun", "tap-", "openvpn", "outline", "amnezia", "proton", "nord", "warp",
             "cloudflare", "zerotier", "tailscale", "hiddify", "v2ray", "xray", "sing-box", "singbox", "clash", "happ",
             "nekoray", "hamachi", "radmin", "fortinet", "anyconnect", "globalprotect")
NOT_VPN = ("teredo", "isatap", "6to4", "loopback", "pseudo", "vethernet", "virtualbox", "vmware", "bluetooth")
IP_LOOKUP = "https:" + "//ipwho" + ".is/"
POLL = 15


def find_vpn(stats: dict, addrs: dict) -> str:
    """Name of an active VPN adapter (by its name and an assigned IP address), or ""."""
    for name, stat in stats.items():
        low = name.lower()
        if not getattr(stat, "isup", False) or any(bad in low for bad in NOT_VPN):
            continue
        if not any(hint in low for hint in VPN_HINTS):
            continue
        if any(getattr(a, "family", None) in (socket.AF_INET, socket.AF_INET6) for a in addrs.get(name, [])):
            return name
    return ""


def parse_wifi(text: str) -> dict:
    """`netsh wlan show interfaces` output (English or Russian Windows) → SSID and signal."""
    ssid = re.search(r"^\s*SSID\s*:\s*(.+?)\s*$", text or "", re.M)
    signal = re.search(r"^\s*(?:Signal|Сигнал)\s*:\s*(\d+)\s*%", text or "", re.M)
    state = re.search(r"^\s*(?:State|Состояние)\s*:\s*(.+?)\s*$", text or "", re.M)
    connected = bool(ssid) and (state is None or state.group(1).lower() in ("connected", "подключено"))
    return {"ssid": ssid.group(1) if connected else "", "signal": int(signal.group(1)) if connected and signal else None}


def wifi_info() -> dict:
    try:
        result = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True, timeout=5,
                                creationflags=hidden_flags())
    except (OSError, subprocess.SubprocessError):
        return {"ssid": "", "signal": None}
    return parse_wifi(decode_output(result.stdout))


def online(timeout: float = 1.5) -> bool:
    for host in ("1.1.1.1", "8.8.8.8"):
        try:
            with socket.create_connection((host, 53), timeout):
                return True
        except OSError:
            continue
    return False


def signature(state: dict) -> tuple:
    battery, wifi = state.get("battery") or {}, state.get("wifi") or {}
    vpn, ip = state.get("vpn") or {}, state.get("ip") or {}
    signal = wifi.get("signal")
    return (battery.get("percent"), battery.get("charging"), battery.get("saver"), state.get("online"),
            wifi.get("ssid"), None if signal is None else signal // 10, vpn.get("on"), vpn.get("name"), ip.get("ip"))


# ---- быстрые индикаторы пилюли (как в Island): раскладка, Caps Lock, микрофон/камера, «вас нет» ----
LANGS = {0x0419: "RU", 0x0409: "EN", 0x0809: "EN", 0x0422: "UA", 0x0423: "BY", 0x043F: "KZ", 0x0407: "DE",
         0x040C: "FR", 0x0410: "IT", 0x0C0A: "ES", 0x0415: "PL", 0x041F: "TR", 0x0411: "JA", 0x0804: "ZH", 0x0412: "KO"}
AWAY_AFTER = 120
FAST_POLL = 0.6


def keyboard_state() -> dict:
    if not config.IS_WINDOWS:
        return {}
    import ctypes
    user32 = ctypes.windll.user32
    caps = bool(user32.GetKeyState(0x14) & 1)
    thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    lang = user32.GetKeyboardLayout(thread) & 0xFFFF
    return {"caps": caps, "layout": LANGS.get(lang, f"{lang:04X}")}


def idle_seconds() -> float:
    if not config.IS_WINDOWS:
        return 0.0
    import ctypes

    class LastInput(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    info = LastInput()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000


def _in_use(kind: str) -> str | None:
    """Какое приложение сейчас держит микрофон/камеру (журнал разрешений Windows 10/11)."""
    import winreg
    base = "Software\\Microsoft\\Windows\\CurrentVersion\\CapabilityAccessManager\\ConsentStore\\" + kind

    def scan(path: str) -> str | None:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            return None
        with key:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(key, i)
                except OSError:
                    return None
                i += 1
                if name == "NonPackaged":
                    found = scan(path + "\\" + name)
                    if found:
                        return found
                    continue
                try:
                    with winreg.OpenKey(key, name) as sub:
                        stop = winreg.QueryValueEx(sub, "LastUsedTimeStop")[0]
                        start = winreg.QueryValueEx(sub, "LastUsedTimeStart")[0]
                except OSError:
                    continue
                if start and not stop:
                    return name.replace("#", "\\").split("\\")[-1].removesuffix(".exe").split("_")[0] or name

    return scan(base)


def privacy_state() -> dict:
    if not config.IS_WINDOWS:
        return {}
    mic, cam = _in_use("microphone"), _in_use("webcam")
    return {"mic": bool(mic), "mic_app": mic or "", "camera": bool(cam), "camera_app": cam or "", "mic_muted": mic_muted()}


def _on_audio_thread(fn):
    """Run a pycaw/COM call on the single audio thread.

    COM objects must be created, used and released on the same initialised thread. Earlier they were made in
    random asyncio.to_thread workers and released later from other threads, which crashed Python with
    "COM method call without VTable" / "access violation" inside comtypes."""
    from ..tools import system as sysmod
    worker = sysmod.audio_worker()
    if threading.current_thread() is worker._thread:
        return fn()
    return worker.run_sync(fn, timeout=10)


def _mic_endpoint():
    from ..tools import system as sysmod
    return sysmod.endpoint_for("in")


def _guarded(fn):
    from ..tools import system as sysmod
    try:
        return fn()
    except ImportError:
        raise
    except Exception:
        sysmod.reset_endpoints()  # default device changed or was unplugged
        raise


def _mic_muted_impl() -> bool:
    return _guarded(lambda: bool(_mic_endpoint().GetMute()))


def mic_muted() -> bool:
    try:
        return bool(_on_audio_thread(_mic_muted_impl))
    except Exception:  # noqa: BLE001 - pycaw is optional
        return False


def _toggle_mic_impl() -> bool:
    def run() -> bool:
        ep = _mic_endpoint()
        ep.SetMute(0 if ep.GetMute() else 1, None)
        return bool(ep.GetMute())
    return _guarded(run)


def toggle_mic_mute() -> bool | None:
    try:
        return _on_audio_thread(_toggle_mic_impl)
    except Exception:  # noqa: BLE001
        log.debug("mic mute failed", exc_info=True)
        return None


def _change_volume_impl(delta: int) -> int | None:
    from ..tools import system as sysmod
    try:
        ep = sysmod._endpoint()
    except ImportError:
        sysmod.volume_keys(None, delta, None)
        return None
    def run() -> int:
        cur = max(0, min(100, round(ep.GetMasterVolumeLevelScalar() * 100) + delta))
        ep.SetMasterVolumeLevelScalar(cur / 100, None)
        if cur and ep.GetMute():
            ep.SetMute(0, None)
        return cur
    return _guarded(run)


def change_volume(delta: int) -> int | None:
    return _on_audio_thread(lambda: _change_volume_impl(delta))


class Status(Feature):
    name = "status"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.state: dict = {}
        self._ip_checked = 0.0
        self._warned_at = 101
        self.kbd: dict = {}
        self.privacy: dict = {}
        self.away = False
        self.volume = 50  # виртуальная громкость для демо и не-Windows

    async def collect(self) -> dict:
        state = {"battery": None, "online": await asyncio.to_thread(online), "wifi": {"ssid": "", "signal": None},
                 "vpn": {"on": False, "name": ""}, "ip": self.state.get("ip"), "updated": time.time()}
        if config.IS_WINDOWS:
            from .. import win

            try:
                state["battery"] = await asyncio.to_thread(win.battery)
            except Exception:  # noqa: BLE001
                log.debug("battery status failed", exc_info=True)
            state["wifi"] = await asyncio.to_thread(wifi_info)
        try:
            import psutil

            name = find_vpn(psutil.net_if_stats(), psutil.net_if_addrs())
            state["vpn"] = {"on": bool(name), "name": name}
        except Exception:  # noqa: BLE001 - psutil missing or adapters unreadable
            log.debug("vpn check failed", exc_info=True)
        vpn_changed = bool(self.state) and state["vpn"]["on"] != (self.state.get("vpn") or {}).get("on")
        if self.settings.get("integrations.vpn_ip_lookup", False) and state["online"] and (
                vpn_changed or time.time() - self._ip_checked > 600):
            state["ip"] = await self.lookup_ip()
        return state

    async def lookup_ip(self) -> dict | None:
        self._ip_checked = time.time()
        try:
            data, _ = await llm.request_json_async(IP_LOOKUP, None, {"User-Agent": "IDOS/2.0"}, "GET", 8)
        except (llm.HTTPError, llm.NetworkError):
            return None
        if not isinstance(data, dict) or data.get("success") is False:
            return None
        return {"ip": data.get("ip", ""), "country": data.get("country", ""), "code": data.get("country_code", ""),
                "flag": (data.get("flag") or {}).get("emoji", "")}

    async def refresh(self) -> dict:
        before, state = self.state, await self.collect()
        self.state = state
        if signature(state) != signature(before):
            bus.emit("status.state", state)
        if before and state["vpn"]["on"] != (before.get("vpn") or {}).get("on"):
            text = f"подключён: {state['vpn']['name']}" if state["vpn"]["on"] else "отключён"
            bus.card("VPN", text, kind="info", icon="shield", timeout=4)
        self.battery_events(before.get("battery") if before else None, state.get("battery"))
        return state

    def battery_events(self, before: dict | None, after: dict | None) -> None:
        if not after or after.get("no_battery") or after.get("percent") is None:
            return
        if before and not before.get("charging") and after.get("charging"):
            bus.emit("status.charging", {"percent": after["percent"]})
        if after.get("charging"):
            self._warned_at = 101
            return
        for level in (10, 20):
            if after["percent"] <= level < self._warned_at:
                self._warned_at = level
                bus.card("Батарея", f"Осталось {after['percent']}% — подключите зарядку",
                         kind="error" if level == 10 else "warning", icon="battery", timeout=10)
                break

    async def start(self) -> None:
        self.spawn(self._loop(), "loop")
        self.spawn(self._fast_loop(), "fast")

    def fast_tick(self, tick: int, idle: float | None = None) -> None:
        kbd = keyboard_state()
        if kbd != self.kbd:
            self.kbd = kbd
            bus.emit("status.keyboard", kbd)
        if tick % 5 == 0:
            priv = privacy_state()
            if priv != self.privacy:
                self.privacy = priv
                bus.emit("status.privacy", priv)
        away = (idle_seconds() if idle is None else idle) >= AWAY_AFTER
        if away != self.away:
            self.away = away
            bus.emit("status.away", {"away": away})

    async def _fast_loop(self) -> None:
        tick = 0
        while True:
            try:
                await asyncio.to_thread(self.fast_tick, tick)
            except Exception:  # noqa: BLE001
                log.debug("fast status failed", exc_info=True)
            tick += 1
            await asyncio.sleep(FAST_POLL)

    async def _loop(self) -> None:
        while True:
            try:
                await self.refresh()
            except Exception:  # noqa: BLE001
                log.warning("status refresh failed", exc_info=True)
            await asyncio.sleep(POLL)

    def methods(self) -> dict:
        async def get_state() -> dict:
            return self.state or await self.refresh()

        async def refresh() -> dict:
            return await self.refresh()

        async def volume(delta: int = 0) -> dict:
            delta = max(-50, min(50, int(delta)))
            if not config.IS_WINDOWS:
                self.volume = max(0, min(100, self.volume + delta))
                return {"volume": self.volume}
            return {"volume": await asyncio.to_thread(change_volume, delta)}

        async def mic_mute() -> dict:
            muted = await asyncio.to_thread(toggle_mic_mute) if config.IS_WINDOWS else None
            self.privacy = {**self.privacy, "mic_muted": bool(muted)}
            bus.emit("status.privacy", self.privacy)
            return {"muted": muted}

        async def indicators() -> dict:
            return {"keyboard": self.kbd, "privacy": self.privacy, "away": self.away}

        return {"status.state": get_state, "status.refresh": refresh, "status.volume": volume,
                "status.mic_mute": mic_mute, "status.indicators": indicators}


def create(app) -> Status:
    return Status(app)
