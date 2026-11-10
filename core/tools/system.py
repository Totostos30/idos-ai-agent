"""System: sound and media keys, brightness, power, clipboard, computer state, processes, Windows settings
pages, dark/light theme, Wi-Fi/Bluetooth radios, network status and the recycle bin."""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from typing import Any

from .. import win
from ..util import Worker, clip, human_bytes, powershell, run_hidden
from . import A, B, I, S, Tool, ToolError, get_bool, get_int, get_str, need
from .apps import start

log = logging.getLogger("idos.system")
MEDIA = {"play_pause": "media_play_pause", "play": "media_play_pause", "pause": "media_play_pause",
         "toggle": "media_play_pause", "пауза": "media_play_pause", "играть": "media_play_pause",
         "next": "media_next", "следующий": "media_next", "дальше": "media_next", "prev": "media_prev",
         "previous": "media_prev", "предыдущий": "media_prev", "назад": "media_prev", "stop": "media_stop",
         "стоп": "media_stop"}
MEDIA_ACTION = {"play": "play", "играть": "play", "pause": "pause", "пауза": "pause", "next": "next",
                "следующий": "next", "дальше": "next", "prev": "previous", "previous": "previous",
                "предыдущий": "previous", "назад": "previous", "stop": "stop", "стоп": "stop"}
_APP: Any = None
MEDIA_TEXT = {"media_play_pause": "пауза / воспроизведение", "media_next": "следующий трек",
              "media_prev": "предыдущий трек", "media_stop": "остановил воспроизведение"}
SETTINGS = {
    "bluetooth": "bluetooth", "блютуз": "bluetooth", "wifi": "network-wifi", "wi-fi": "network-wifi",
    "вайфай": "network-wifi", "сеть": "network", "network": "network", "vpn": "network-vpn", "впн": "network-vpn",
    "экран": "display", "дисплей": "display", "display": "display", "звук": "sound", "sound": "sound",
    "уведомления": "notifications", "notifications": "notifications", "питание": "powersleep",
    "power": "powersleep", "батарея": "batterysaver", "хранилище": "storagesense", "storage": "storagesense",
    "приложения": "appsfeatures", "apps": "appsfeatures", "автозагрузка": "startupapps",
    "startup": "startupapps", "по умолчанию": "defaultapps", "персонализация": "personalization",
    "фон": "personalization-background", "обои": "personalization-background", "цвета": "colors", "темы": "themes",
    "мышь": "mousetouchpad", "mouse": "mousetouchpad", "клавиатура": "typing", "язык": "regionlanguage",
    "language": "regionlanguage", "дата": "dateandtime", "время": "dateandtime", "обновления": "windowsupdate",
    "update": "windowsupdate", "конфиденциальность": "privacy", "privacy": "privacy",
    "микрофон": "privacy-microphone", "камера": "privacy-webcam", "принтеры": "printers",
    "printers": "printers", "о системе": "about", "about": "about", "учетные записи": "yourinfo",
    "игры": "gaming-gamebar", "специальные возможности": "easeofaccess", "буфер обмена": "clipboard",
    "многозадачность": "multitasking", "фокусировка": "focus", "ночной свет": "nightlight",
}
RADIO = {"bluetooth": "bluetooth", "bt": "bluetooth", "блютуз": "bluetooth", "блютус": "bluetooth", "wifi": "wifi",
         "wi-fi": "wifi", "вайфай": "wifi", "вай-фай": "wifi"}
POWER = {"hibernate": ["shutdown", "/h"], "logoff": ["shutdown", "/l"], "cancel_shutdown": ["shutdown", "/a"]}
POWER_TEXT = {"shutdown": "выключение через {d} с — отменить: system cancel_shutdown",
              "restart": "перезагрузка через {d} с — отменить: system cancel_shutdown",
              "hibernate": "перевожу в гибернацию", "logoff": "выхожу из системы", "cancel_shutdown": "выключение отменено"}
POWER_ACTIONS = ("lock", "sleep", "hibernate", "shutdown", "restart", "logoff", "cancel_shutdown")
_audio: Worker | None = None


def _psutil() -> Any:
    try:
        import psutil
    except ImportError:
        return None
    return psutil


def tri(args: dict, key: str) -> bool | None:
    return None if args.get(key) in (None, "") else get_bool(args, key)


def _com_init() -> None:
    try:
        import comtypes
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    except Exception:  # noqa: BLE001 - pycaw is optional
        log.debug("COM init skipped", exc_info=True)


def audio_worker() -> Worker:
    global _audio
    if _audio is None:
        _audio = Worker("audio", init=_com_init)
    return _audio


_ENDPOINTS: dict[str, Any] = {}


def endpoint_for(kind: str) -> Any:
    """IAudioEndpointVolume of the default speakers ("out") or microphone ("in").

    Created once on the audio thread and reused: creating and dropping COM objects every second fed the
    garbage collector with pointers that crashed the core when released. Call reset_endpoints() on errors."""
    ep = _ENDPOINTS.get(kind)
    if ep is not None:
        return ep
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    device = AudioUtilities.GetSpeakers() if kind == "out" else AudioUtilities.GetMicrophone()
    if device is None:
        raise OSError("нет аудиоустройства")
    raw = getattr(device, "_dev", None) or device  # pycaw 2024+ wraps IMMDevice in AudioDevice
    if not hasattr(raw, "Activate"):  # unusual wrapper: use what pycaw offers, without caching
        return device.EndpointVolume
    from comtypes import CLSCTX_ALL
    unknown = raw.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    ep = unknown.QueryInterface(IAudioEndpointVolume)
    del unknown, raw, device
    _ENDPOINTS[kind] = ep
    return ep


def reset_endpoints() -> None:
    _ENDPOINTS.clear()


def _endpoint() -> Any:
    return endpoint_for("out")


def volume_keys(level: int | None, change: int | None, mute: bool | None) -> str:
    if mute is not None:
        win.hotkey("volume_mute")
        return "переключил звук (без pycaw состояние не проверить)"
    if level is not None:
        level = max(0, min(100, level))
        win.hotkey("volume_down", 50)
        if level:
            win.hotkey("volume_up", round(level / 2))
        return f"громкость ≈{level}%"
    if change:
        win.hotkey("volume_up" if change > 0 else "volume_down", max(1, round(abs(change) / 2)))
        return f"громкость {'+' if change > 0 else '−'}{abs(change)}%"
    raise ToolError("без пакета pycaw текущую громкость не узнать")


def set_volume(level: int | None = None, change: int | None = None, mute: bool | None = None) -> str:
    try:
        endpoint = _endpoint()
    except ImportError:
        return volume_keys(level, change, mute)
    if mute is not None:
        endpoint.SetMute(int(mute), None)
    current = round(endpoint.GetMasterVolumeLevelScalar() * 100)
    target = level if level is not None else (current + change if change else None)
    if target is not None:
        current = max(0, min(100, target))
        endpoint.SetMasterVolumeLevelScalar(current / 100, None)
        if current and mute is None and endpoint.GetMute():
            endpoint.SetMute(0, None)
    return f"громкость {current}%" + (", звук выключен" if endpoint.GetMute() else "")


def brightness(level: int | None) -> str:
    if level is not None:
        level = max(0, min(100, level))
        code, _out, _err = powershell(
            "$m = Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods -ErrorAction Stop | "
            "Select-Object -First 1; Invoke-CimMethod -InputObject $m -MethodName WmiSetBrightness -Arguments "
            f"@{{Timeout=[uint32]1; Brightness=[byte]{level}}} -ErrorAction Stop | Out-Null", 20)
        if code != 0:
            raise ToolError("яркость этого экрана программно не меняется — так умеют встроенные экраны ноутбуков")
    code, out, _err = powershell("(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness "
                                 "-ErrorAction Stop | Select-Object -First 1).CurrentBrightness", 20)
    value = out.strip().splitlines()[-1].strip() if out.strip() else ""
    if code != 0 or not value.isdigit():
        raise ToolError("яркость недоступна: внешний монитор настраивается его собственными кнопками")
    return f"яркость {value}%"


def power(action: str, delay: int = 30) -> str:
    if action == "lock":
        win.lock()
        return "компьютер заблокирован"
    if action == "sleep":
        powershell("Add-Type -AssemblyName System.Windows.Forms; "
                   "[System.Windows.Forms.Application]::SetSuspendState('Suspend', $false, $false) | Out-Null", 30)
        return "перевожу компьютер в сон"
    args = POWER.get(action) or ["shutdown", "/s" if action == "shutdown" else "/r", "/t", str(delay)]
    proc = run_hidden(args, 20)
    if proc.returncode != 0:
        if action == "cancel_shutdown":
            return "запланированного выключения нет"
        raise ToolError(clip((proc.stderr or proc.stdout or "").strip() or f"shutdown: код {proc.returncode}", 300))
    return POWER_TEXT[action].format(d=delay)


def _duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    return (f"{days} д " if days else "") + (f"{hours} ч " if hours or days else "") + f"{mins} мин"


def os_name() -> str:
    """Windows 11 still reports release "10" through platform.release(); the build number is reliable."""
    if not win.IS_WINDOWS:
        return platform.system()
    try:
        build = sys.getwindowsversion().build
    except AttributeError:
        return platform.system()
    return "Windows 11" if build >= 22000 else "Windows 10"


def system_info() -> str:
    lines = [f"Система: {os_name()} {platform.release()} ({platform.version()})", f"Компьютер: {platform.node()}"]
    ps = _psutil()
    if ps is not None:
        memory = ps.virtual_memory()
        lines += [f"Процессор: загрузка {ps.cpu_percent(interval=0.4):.0f}%, потоков {ps.cpu_count()}",
                  f"Память: занято {human_bytes(memory.used)} из {human_bytes(memory.total)} ({memory.percent:.0f}%)",
                  f"Без перезагрузки: {_duration(time.time() - ps.boot_time())}"]
    for drive in ([f"{letter}:\\" for letter in "CDEFGH"] if win.IS_WINDOWS else ["/"]):
        if os.path.exists(drive):
            usage = shutil.disk_usage(drive)
            lines.append(f"Диск {drive}: свободно {human_bytes(usage.free)} из {human_bytes(usage.total)}")
    if win.IS_WINDOWS:
        battery = win.battery()
        if battery.get("percent") is not None and not battery.get("no_battery"):
            state = "заряжается" if battery.get("charging") else (
                f"осталось {_duration(battery['seconds_left'])}" if battery.get("seconds_left") else "от батареи")
            lines.append(f"Батарея: {battery['percent']}%, {state}" + (", экономия заряда" if battery.get("saver") else ""))
    return "\n".join(lines)


def process_rows() -> list[dict]:
    ps = _psutil()
    if ps is not None:
        procs = list(ps.process_iter(["pid", "name", "memory_info"]))
        for proc in procs:
            try:
                proc.cpu_percent(None)
            except ps.Error:
                pass
        time.sleep(0.5)
        cores, rows = ps.cpu_count() or 1, []
        for proc in procs:
            try:
                memory = proc.info.get("memory_info")
                rows.append({"pid": proc.pid, "name": proc.info.get("name") or "?",
                             "memory": memory.rss if memory else 0, "cpu": proc.cpu_percent(None) / cores})
            except ps.Error:
                continue
        return rows
    try:
        out = run_hidden(["tasklist", "/FO", "CSV", "/NH"], 20).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise ToolError("список процессов недоступен: нет psutil и tasklist") from exc
    rows = []
    for row in csv.reader(io.StringIO(out)):
        if len(row) >= 5 and row[1].isdigit():
            rows.append({"pid": int(row[1]), "name": row[0], "memory": int(re.sub(r"\D", "", row[4]) or 0) * 1024,
                         "cpu": None})
    return rows


def processes(limit: int = 15, sort: str = "memory", name: str = "") -> str:
    rows = [r for r in process_rows() if name.lower() in r["name"].lower()]
    key = "cpu" if sort == "cpu" and any(r["cpu"] is not None for r in rows) else "memory"
    rows.sort(key=lambda r: r[key] or 0, reverse=True)
    return "\n".join(f"{r['name']} (PID {r['pid']}): {human_bytes(r['memory'])}" +
                     (f", CPU {r['cpu']:.0f}%" if r["cpu"] is not None else "") for r in rows[:limit]) or "процессов не найдено"


def open_settings(page: str) -> str:
    key = " ".join(page.lower().replace("ё", "е").split())
    target = SETTINGS.get(key, key if re.fullmatch(r"[a-z0-9-]*", key) else None)
    if target is None:
        raise ToolError(f"не знаю раздел «{page}»; например: bluetooth, wifi, звук, экран, обновления, приложения")
    start(f"ms-settings:{target}")
    return "открыл параметры" + (f": {page}" if page else "")


def set_theme(dark: bool) -> str:
    try:
        import winreg
    except ImportError as exc:
        raise ToolError("тема меняется только в Windows") from exc
    path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
        for name in ("AppsUseLightTheme", "SystemUsesLightTheme"):
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 0 if dark else 1)
    import ctypes
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "ImmersiveColorSet", 0x0002, 2000, None)
    return "включил тёмную тему" if dark else "включил светлую тему"


async def radio(kind: str, on: bool | None) -> str:
    try:
        from winrt.windows.devices.radios import Radio, RadioKind, RadioState
    except ImportError as exc:
        raise ToolError("нет пакета winrt-Windows.Devices.Radios — откройте system settings page=bluetooth") from exc
    await Radio.request_access_async()
    want = RadioKind.BLUETOOTH if kind == "bluetooth" else RadioKind.WI_FI
    found = [r for r in await Radio.get_radios_async() if r.kind == want]
    if not found:
        raise ToolError("такого адаптера в компьютере нет")
    if on is not None:
        await found[0].set_state_async(RadioState.ON if on else RadioState.OFF)
    name = "Bluetooth" if kind == "bluetooth" else "Wi-Fi"
    return f"{name} " + ("включён" if found[0].state == RadioState.ON else "выключен")


def network() -> str:
    lines = []
    if win.IS_WINDOWS:
        fields: dict[str, str] = {}
        for line in run_hidden(["netsh", "wlan", "show", "interfaces"], 15).stdout.splitlines():
            key, sep, value = line.partition(":")
            if sep:
                fields.setdefault(key.strip().lower(), value.strip())
        if fields.get("ssid"):
            signal = fields.get("signal") or fields.get("сигнал") or ""
            lines.append(f"Wi-Fi: {fields['ssid']}" + (f", сигнал {signal}" if signal else ""))
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            lines.append(f"Локальный IP: {sock.getsockname()[0]}")
    except OSError:
        lines.append("Сети нет")
    return "\n".join(lines)


async def handler(action: str, args: dict, ctx: Any) -> Any:
    try:
        if action == "volume":
            level, change, mute = get_int(args, "level"), get_int(args, "change"), tri(args, "mute")
            return await audio_worker().run(lambda: set_volume(level, change, mute))
        if action == "media":
            word = get_str(args, "command", "play_pause").strip().lower() or "play_pause"
            key = MEDIA.get(word)
            if key is None:
                raise ToolError("command: play_pause, next, prev или stop")
            player = get_str(args, "app").strip()
            feature = _APP.feature("media") if _APP is not None and hasattr(_APP, "feature") else None
            if feature is not None:
                command = MEDIA_ACTION.get(word, "play_pause")
                result = await feature.control(command, None, player)
                if not result.get("ok"):
                    if player:
                        raise ToolError(f"не нашёл плеер «{player}»: запусти его и повтори с тем же app")
                    raise ToolError("нет активного плеера")
                return MEDIA_TEXT[key] + (f" в «{player}»" if player else "")
            if player:
                raise ToolError("управление конкретным плеером недоступно; используй инструмент media")
            await asyncio.to_thread(win.hotkey, key)
            return MEDIA_TEXT[key]
        if action == "brightness":
            return await asyncio.to_thread(brightness, get_int(args, "level"))
        if action in POWER_ACTIONS:
            delay = get_int(args, "delay")
            return await asyncio.to_thread(power, action, 30 if delay is None else max(0, min(3600, delay)))
        if action == "clipboard_get":
            text = await asyncio.to_thread(win.get_clipboard)
            return clip(text, 4000) if text else "буфер обмена пуст"
        if action == "clipboard_set":
            text = need(args, "text", "что скопировать")
            await asyncio.to_thread(win.set_clipboard, text)
            return f"скопировал в буфер {len(text)} симв."
        if action == "info":
            return await asyncio.to_thread(system_info)
        if action == "processes":
            return await asyncio.to_thread(processes, max(1, min(50, get_int(args, "limit", 15) or 15)),
                                           get_str(args, "sort", "memory").lower(), get_str(args, "name"))
        if action == "settings":
            return await asyncio.to_thread(open_settings, get_str(args, "page"))
        if action == "theme":
            return await asyncio.to_thread(set_theme, get_bool(args, "dark", True))
        if action == "radio":
            kind = RADIO.get(get_str(args, "kind").strip().lower())
            if kind is None:
                raise ToolError("kind: bluetooth или wifi")
            return await radio(kind, tri(args, "on"))
        if action == "network":
            return await asyncio.to_thread(network)
        if action == "empty_recycle_bin":
            code, _out, err = await asyncio.to_thread(powershell, "Clear-RecycleBin -Force -ErrorAction SilentlyContinue", 90)
            if code != 0:
                raise ToolError(clip(err or "не удалось очистить корзину", 300))
            return "корзина очищена"
    except win.WinError as exc:
        raise ToolError(str(exc)) from exc
    raise ToolError(f"неизвестное действие {action}")


ACTIONS = {
    "volume": A("Меняю громкость"), "media": A("Управляю воспроизведением {command}"),
    "brightness": A("Меняю яркость"), "lock": A("Блокирую компьютер"),
    "sleep": A("Перевожу компьютер в сон", "danger"), "hibernate": A("Усыпляю компьютер (гибернация)", "danger"),
    "shutdown": A("Выключаю компьютер", "danger"), "restart": A("Перезагружаю компьютер", "danger"),
    "logoff": A("Выхожу из системы", "danger"), "cancel_shutdown": A("Отменяю выключение"),
    "clipboard_get": A("Читаю буфер обмена", "read"), "clipboard_set": A("Копирую в буфер обмена"),
    "info": A("Смотрю состояние компьютера", "read"), "processes": A("Смотрю процессы", "read"),
    "settings": A("Открываю параметры {page}"), "theme": A("Меняю тему Windows"),
    "radio": A("Переключаю {kind}"), "network": A("Смотрю сеть", "read"),
    "empty_recycle_bin": A("Очищаю корзину", "danger"),
}
PARAMS = {
    "level": I("volume / brightness: 0–100"), "change": I("volume: изменить на ±N"), "mute": B("volume: true — выключить звук"),
    "command": S("media: play_pause | play | pause | next | prev | stop"),
    "app": S("media: какой плеер (Яндекс Музыка, Spotify…) — обязательно, если пользователь его назвал"), "delay": I("shutdown / restart: через сколько секунд"),
    "text": S("clipboard_set: текст"), "page": S("settings: bluetooth, wifi, звук, экран, обновления, приложения…"),
    "dark": B("theme: true — тёмная, false — светлая"), "kind": S("radio: bluetooth | wifi"), "on": B("radio: включить"),
    "limit": I("processes: сколько показать"), "sort": S("processes: memory | cpu"), "name": S("processes: фильтр по имени"),
}


def register(reg: Any, app: Any) -> None:
    global _APP
    _APP = app
    reg.add(Tool(
        "system",
        "Компьютер: volume, media, brightness, lock, sleep, hibernate, shutdown / restart (delay), logoff, "
        "cancel_shutdown, clipboard_get / clipboard_set, info (ОС, память, диски, батарея), processes, settings "
        "(раздел параметров Windows), theme, radio (Wi-Fi / Bluetooth), network, empty_recycle_bin.",
        ACTIONS, PARAMS, handler, available=lambda: win.IS_WINDOWS, timeout=60))
