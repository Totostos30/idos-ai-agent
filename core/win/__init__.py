"""Thin ctypes layer over the Win32 API: input, windows, folders, clipboard, layout, power.

Importing this module is safe on any OS; functions raise ``WinError`` when Windows is not available.
Coordinates are physical pixels: call ``set_dpi_aware()`` once at start-up.
"""
from __future__ import annotations

import ctypes
import os
import pathlib
import time
import uuid
from typing import Any

IS_WINDOWS = os.name == "nt"


class WinError(RuntimeError):
    pass


class FailsafeError(RuntimeError):
    """The user pushed the cursor into the top-left corner."""


if IS_WINDOWS:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
    ULONG_PTR = ctypes.c_size_t

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)]

    class SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                    ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                    ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]

    def _sig(dll: Any, name: str, restype: Any, *argtypes: Any) -> Any:
        fn = getattr(dll, name)
        fn.restype = restype
        fn.argtypes = list(argtypes)
        return fn

    HWND, BOOL, INT, UINT, DWORD = wintypes.HWND, wintypes.BOOL, ctypes.c_int, wintypes.UINT, wintypes.DWORD
    EnumWindowsProc = ctypes.WINFUNCTYPE(BOOL, HWND, wintypes.LPARAM)
    MonitorEnumProc = ctypes.WINFUNCTYPE(BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                                         wintypes.LPARAM)
    _SendInput = _sig(user32, "SendInput", UINT, UINT, ctypes.POINTER(INPUT), INT)
    _SetCursorPos = _sig(user32, "SetCursorPos", BOOL, INT, INT)
    _GetCursorPos = _sig(user32, "GetCursorPos", BOOL, ctypes.POINTER(wintypes.POINT))
    _GetSystemMetrics = _sig(user32, "GetSystemMetrics", INT, INT)
    _EnumWindows = _sig(user32, "EnumWindows", BOOL, EnumWindowsProc, wintypes.LPARAM)
    _GetAsyncKeyState = _sig(user32, "GetAsyncKeyState", ctypes.c_short, INT)
    _IsWindowVisible = _sig(user32, "IsWindowVisible", BOOL, HWND)
    _IsWindow = _sig(user32, "IsWindow", BOOL, HWND)
    _IsIconic = _sig(user32, "IsIconic", BOOL, HWND)
    _IsZoomed = _sig(user32, "IsZoomed", BOOL, HWND)
    _GetWindowTextLengthW = _sig(user32, "GetWindowTextLengthW", INT, HWND)
    _GetWindowTextW = _sig(user32, "GetWindowTextW", INT, HWND, wintypes.LPWSTR, INT)
    _GetClassNameW = _sig(user32, "GetClassNameW", INT, HWND, wintypes.LPWSTR, INT)
    _GetWindowThreadProcessId = _sig(user32, "GetWindowThreadProcessId", DWORD, HWND, ctypes.POINTER(DWORD))
    _GetWindowLongW = _sig(user32, "GetWindowLongW", ctypes.c_long, HWND, INT)
    _SetWindowLongW = _sig(user32, "SetWindowLongW", ctypes.c_long, HWND, INT, ctypes.c_long)
    _GetWindowRect = _sig(user32, "GetWindowRect", BOOL, HWND, ctypes.POINTER(wintypes.RECT))
    _GetForegroundWindow = _sig(user32, "GetForegroundWindow", HWND)
    _SetForegroundWindow = _sig(user32, "SetForegroundWindow", BOOL, HWND)
    _BringWindowToTop = _sig(user32, "BringWindowToTop", BOOL, HWND)
    _ShowWindow = _sig(user32, "ShowWindow", BOOL, HWND, INT)
    _SwitchToThisWindow = _sig(user32, "SwitchToThisWindow", None, HWND, BOOL)
    _AttachThreadInput = _sig(user32, "AttachThreadInput", BOOL, DWORD, DWORD, BOOL)
    _PostMessageW = _sig(user32, "PostMessageW", BOOL, HWND, UINT, wintypes.WPARAM, wintypes.LPARAM)
    _MoveWindow = _sig(user32, "MoveWindow", BOOL, HWND, INT, INT, INT, INT, BOOL)
    _MonitorFromWindow = _sig(user32, "MonitorFromWindow", wintypes.HMONITOR, HWND, DWORD)
    _GetMonitorInfoW = _sig(user32, "GetMonitorInfoW", BOOL, wintypes.HMONITOR, ctypes.POINTER(MONITORINFO))
    _EnumDisplayMonitors = _sig(user32, "EnumDisplayMonitors", BOOL, wintypes.HDC, ctypes.c_void_p,
                                MonitorEnumProc, wintypes.LPARAM)
    _GetKeyboardLayout = _sig(user32, "GetKeyboardLayout", ctypes.c_void_p, DWORD)
    _LoadKeyboardLayoutW = _sig(user32, "LoadKeyboardLayoutW", ctypes.c_void_p, wintypes.LPCWSTR, UINT)
    _VkKeyScanW = _sig(user32, "VkKeyScanW", ctypes.c_short, wintypes.WCHAR)
    _LockWorkStation = _sig(user32, "LockWorkStation", BOOL)
    _OpenClipboard = _sig(user32, "OpenClipboard", BOOL, HWND)
    _CloseClipboard = _sig(user32, "CloseClipboard", BOOL)
    _EmptyClipboard = _sig(user32, "EmptyClipboard", BOOL)
    _GetClipboardData = _sig(user32, "GetClipboardData", ctypes.c_void_p, UINT)
    _SetClipboardData = _sig(user32, "SetClipboardData", ctypes.c_void_p, UINT, ctypes.c_void_p)
    _GetClipboardSequenceNumber = _sig(user32, "GetClipboardSequenceNumber", DWORD)
    _GlobalAlloc = _sig(kernel32, "GlobalAlloc", ctypes.c_void_p, UINT, ctypes.c_size_t)
    _GlobalLock = _sig(kernel32, "GlobalLock", ctypes.c_void_p, ctypes.c_void_p)
    _GlobalUnlock = _sig(kernel32, "GlobalUnlock", BOOL, ctypes.c_void_p)
    _GetCurrentThreadId = _sig(kernel32, "GetCurrentThreadId", DWORD)
    _OpenProcess = _sig(kernel32, "OpenProcess", wintypes.HANDLE, DWORD, BOOL, DWORD)
    _CloseHandle = _sig(kernel32, "CloseHandle", BOOL, wintypes.HANDLE)
    _QueryFullProcessImageNameW = _sig(kernel32, "QueryFullProcessImageNameW", BOOL, wintypes.HANDLE, DWORD,
                                       wintypes.LPWSTR, ctypes.POINTER(DWORD))
    _GetSystemPowerStatus = _sig(kernel32, "GetSystemPowerStatus", BOOL, ctypes.POINTER(SYSTEM_POWER_STATUS))
    _SHGetKnownFolderPath = _sig(shell32, "SHGetKnownFolderPath", ctypes.c_long, ctypes.POINTER(GUID), DWORD,
                                 wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p))
    _CoTaskMemFree = _sig(ole32, "CoTaskMemFree", None, ctypes.c_void_p)
    _DwmGetWindowAttribute = _sig(dwmapi, "DwmGetWindowAttribute", ctypes.c_long, HWND, DWORD, ctypes.c_void_p,
                                  DWORD)

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x1, 0x2, 0x4
MOUSE_FLAGS = {"left": (0x2, 0x4), "right": (0x8, 0x10), "middle": (0x20, 0x40)}
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x800, 0x1000
SW_MAXIMIZE, SW_MINIMIZE, SW_RESTORE, SW_SHOW = 3, 6, 9, 5
WM_CLOSE, WM_INPUTLANGCHANGEREQUEST = 0x10, 0x50
GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE, WS_EX_TOPMOST = -20, 0x80, 0x08000000, 0x8
CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x2

VK = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "return": 0x0D, "shift": 0x10, "ctrl": 0x11, "alt": 0x12,
    "pause": 0x13, "capslock": 0x14, "esc": 0x1B, "escape": 0x1B, "space": 0x20, "pageup": 0x21, "pagedown": 0x22,
    "end": 0x23, "home": 0x24, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "printscreen": 0x2C,
    "insert": 0x2D, "delete": 0x2E, "win": 0x5B, "apps": 0x5D, "numlock": 0x90, "scrolllock": 0x91,
    "volume_mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF, "media_next": 0xB0, "media_prev": 0xB1,
    "media_stop": 0xB2, "media_play_pause": 0xB3, "browser_back": 0xA6, "browser_forward": 0xA7,
}
VK.update({f"f{i}": 0x6F + i for i in range(1, 25)})
ALIASES = {"control": "ctrl", "ctl": "ctrl", "контрол": "ctrl", "windows": "win", "super": "win", "meta": "win",
           "cmd": "win", "del": "delete", "ins": "insert", "pgup": "pageup", "pgdn": "pagedown",
           "page_up": "pageup", "page_down": "pagedown", "ввод": "enter", "пробел": "space", "bksp": "backspace",
           "arrowleft": "left", "arrowright": "right", "arrowup": "up", "arrowdown": "down", "menu": "apps",
           "prtsc": "printscreen", "play": "media_play_pause", "pause_media": "media_play_pause",
           "next": "media_next", "prev": "media_prev", "mute": "volume_mute", "plus": "=", "minus": "-"}
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5C, 0x5D, 0x6F, 0x90}
FOLDERS = {"desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641", "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
           "downloads": "374DE290-123F-4565-9164-39C4925E467B", "music": "4BD8D571-6D19-48D3-BE97-422220080E43",
           "pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB", "videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC"}
FOLDER_ALIASES = {"рабочий стол": "desktop", "стол": "desktop", "документы": "documents", "загрузки": "downloads",
                  "музыка": "music", "изображения": "pictures", "картинки": "pictures", "видео": "videos",
                  "дом": "home", "профиль": "home"}


def require() -> None:
    if not IS_WINDOWS:
        raise WinError("это действие работает только в Windows")


def set_dpi_aware() -> None:
    if not IS_WINDOWS:
        return
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()


# --- mouse and keyboard ------------------------------------------------------------------------------

def cursor_pos() -> tuple[int, int]:
    require()
    point = wintypes.POINT()
    _GetCursorPos(ctypes.byref(point))
    return point.x, point.y


VK_SHIFT, VK_CONTROL, VK_MENU, VK_ESCAPE, VK_LBUTTON, VK_RBUTTON = 0x10, 0x11, 0x12, 0x1B, 0x01, 0x02


def key_down(vk: int) -> bool:
    """True, если клавиша (или кнопка мыши) сейчас нажата."""
    require()
    return bool(_GetAsyncKeyState(int(vk)) & 0x8000)


def failsafe() -> None:
    """Abort automation when the user slams the cursor into the top-left corner."""
    if IS_WINDOWS:
        x, y = cursor_pos()
        if x <= 1 and y <= 1:
            raise FailsafeError("курсор в левом верхнем углу — аварийная остановка")


def _send(*inputs: Any) -> None:
    array = (INPUT * len(inputs))(*inputs)
    if _SendInput(len(inputs), array, ctypes.sizeof(INPUT)) != len(inputs):
        raise WinError(f"SendInput отклонён (ошибка {ctypes.get_last_error()}); возможно, окно запущено "
                       "от администратора")


def _mouse(flags: int, data: int = 0) -> Any:
    item = INPUT(type=INPUT_MOUSE)
    item.u.mi = MOUSEINPUT(0, 0, data & 0xFFFFFFFF, flags, 0, 0)
    return item


def _key(vk: int = 0, scan: int = 0, flags: int = 0) -> Any:
    item = INPUT(type=INPUT_KEYBOARD)
    item.u.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    return item


def screen_size() -> tuple[int, int]:
    require()
    return _GetSystemMetrics(0), _GetSystemMetrics(1)


def virtual_screen() -> dict[str, int]:
    require()
    return {"x": _GetSystemMetrics(76), "y": _GetSystemMetrics(77), "width": _GetSystemMetrics(78),
            "height": _GetSystemMetrics(79)}


def move(x: int, y: int) -> None:
    require()
    failsafe()
    _SetCursorPos(int(x), int(y))


def click(x: int | None = None, y: int | None = None, button: str = "left", count: int = 1) -> None:
    require()
    failsafe()
    if x is not None and y is not None:
        _SetCursorPos(int(x), int(y))
        time.sleep(0.03)
    down, up = MOUSE_FLAGS.get(button, MOUSE_FLAGS["left"])
    for index in range(max(1, count)):
        _send(_mouse(down), _mouse(up))
        if index + 1 < count:
            time.sleep(0.06)


def drag(x1: int, y1: int, x2: int, y2: int, button: str = "left", steps: int = 20) -> None:
    require()
    failsafe()
    down, up = MOUSE_FLAGS.get(button, MOUSE_FLAGS["left"])
    _SetCursorPos(int(x1), int(y1))
    time.sleep(0.05)
    _send(_mouse(down))
    for step in range(1, steps + 1):
        _SetCursorPos(int(x1 + (x2 - x1) * step / steps), int(y1 + (y2 - y1) * step / steps))
        time.sleep(0.012)
    _send(_mouse(up))


def scroll(clicks: int, x: int | None = None, y: int | None = None, horizontal: bool = False) -> None:
    require()
    failsafe()
    if x is not None and y is not None:
        _SetCursorPos(int(x), int(y))
    _send(_mouse(MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL, int(clicks) * 120))


def type_text(text: str, delay: float = 0.004) -> None:
    """Type any Unicode text regardless of the active keyboard layout."""
    require()
    failsafe()
    for char in text.replace("\r\n", "\n"):
        if char == "\n":
            _send(_key(VK["enter"]), _key(VK["enter"], flags=KEYEVENTF_KEYUP))
        elif char == "\t":
            _send(_key(VK["tab"]), _key(VK["tab"], flags=KEYEVENTF_KEYUP))
        else:
            data = char.encode("utf-16-le")
            for i in range(0, len(data), 2):
                unit = int.from_bytes(data[i:i + 2], "little")
                _send(_key(0, unit, KEYEVENTF_UNICODE), _key(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
        if delay:
            time.sleep(delay)


def vk_code(name: str) -> int:
    key = name.strip().lower()
    key = ALIASES.get(key, key)
    if key in VK:
        return VK[key]
    if len(key) == 1:
        char = key.upper()
        if "A" <= char <= "Z" or "0" <= char <= "9":
            return ord(char)
        cyr = "ФИСВУАПРШОЛДЬТЩЗЙКЫЕГМЦЧНЯ"
        if char in cyr:  # Russian letter -> the physical key in the ЙЦУКЕН layout
            return ord("A") + cyr.index(char)
        if IS_WINDOWS:
            code = _VkKeyScanW(key)
            if code != -1:
                return code & 0xFF
    raise WinError(f"неизвестная клавиша «{name}»")


def parse_keys(combo: str) -> list[int]:
    parts = [p for p in combo.replace(" ", "").replace("++", "+plus").split("+") if p]
    if not parts:
        raise WinError("пустое сочетание клавиш")
    return [vk_code(p) for p in parts]


def hotkey(combo: str, repeat: int = 1) -> None:
    """Press a combination like "ctrl+shift+esc" or a single key like "enter"."""
    require()
    failsafe()
    codes = parse_keys(combo)
    for _ in range(max(1, repeat)):
        downs = [_key(c, 0, KEYEVENTF_EXTENDEDKEY if c in EXTENDED else 0) for c in codes]
        ups = [_key(c, 0, KEYEVENTF_KEYUP | (KEYEVENTF_EXTENDEDKEY if c in EXTENDED else 0)) for c in reversed(codes)]
        _send(*downs)
        time.sleep(0.03)
        _send(*ups)
        time.sleep(0.03)


# --- windows -----------------------------------------------------------------------------------------

def _text(fn: Any, hwnd: int, size: int = 512) -> str:
    buf = ctypes.create_unicode_buffer(size)
    fn(hwnd, buf, size)
    return buf.value


def process_name(pid: int) -> str:
    try:
        import psutil  # type: ignore

        return psutil.Process(pid).name()
    except Exception:  # noqa: BLE001
        pass
    if not IS_WINDOWS:
        return ""
    handle = _OpenProcess(0x1000, False, pid)
    if not handle:
        return ""
    try:
        size = DWORD(1024)
        buf = ctypes.create_unicode_buffer(1024)
        if _QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return pathlib.PureWindowsPath(buf.value).name
        return ""
    finally:
        _CloseHandle(handle)


def window_pid(hwnd: int) -> int:
    pid = DWORD()
    _GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def window_rect(hwnd: int) -> dict[str, int]:
    rect = wintypes.RECT()
    _GetWindowRect(hwnd, ctypes.byref(rect))
    return {"x": rect.left, "y": rect.top, "width": rect.right - rect.left, "height": rect.bottom - rect.top}


def _cloaked(hwnd: int) -> bool:
    value = ctypes.c_int(0)
    try:
        _DwmGetWindowAttribute(hwnd, 14, ctypes.byref(value), ctypes.sizeof(value))
    except OSError:
        return False
    return value.value != 0


def window_info(hwnd: int) -> dict[str, Any]:
    pid = window_pid(hwnd)
    return {"hwnd": int(hwnd), "title": _text(_GetWindowTextW, hwnd), "class": _text(_GetClassNameW, hwnd, 256),
            "pid": pid, "process": process_name(pid), "minimized": bool(_IsIconic(hwnd)),
            "maximized": bool(_IsZoomed(hwnd)), "rect": window_rect(hwnd)}


def list_windows() -> list[dict[str, Any]]:
    require()
    handles: list[int] = []

    def callback(hwnd: int, _lparam: int) -> bool:
        if _IsWindowVisible(hwnd) and _GetWindowTextLengthW(hwnd) > 0:
            if not (_GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW) and not _cloaked(hwnd):
                handles.append(hwnd)
        return True

    _EnumWindows(EnumWindowsProc(callback), 0)
    out = []
    for hwnd in handles:
        info = window_info(hwnd)
        if info["class"] in ("Progman", "WorkerW", "Shell_TrayWnd") or info["title"] in ("IDOS", "IDOS overlay"):
            continue
        out.append(info)
    return out


def foreground() -> dict[str, Any]:
    require()
    hwnd = _GetForegroundWindow()
    return window_info(hwnd) if hwnd else {}


def find_window(query: str | int) -> dict[str, Any]:
    windows = list_windows()
    if isinstance(query, int) or str(query).isdigit():
        for info in windows:
            if info["hwnd"] == int(query):
                return info
    q = str(query).casefold().strip()
    q_exe = q[:-4] if q.endswith(".exe") else q
    for test in (lambda w: w["process"].casefold().removesuffix(".exe") == q_exe,
                 lambda w: w["title"].casefold() == q,
                 lambda w: q in w["title"].casefold(),
                 lambda w: q_exe in w["process"].casefold()):
        for info in windows:
            if test(info):
                return info
    raise WinError(f"окно «{query}» не найдено")


def focus(hwnd: int) -> bool:
    require()
    if _IsIconic(hwnd):
        _ShowWindow(hwnd, SW_RESTORE)
    fg = _GetForegroundWindow()
    current = _GetCurrentThreadId()
    fg_thread = _GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(fg_thread and fg_thread != current and _AttachThreadInput(current, fg_thread, True))
    try:
        _BringWindowToTop(hwnd)
        _SetForegroundWindow(hwnd)
    finally:
        if attached:
            _AttachThreadInput(current, fg_thread, False)
    if _GetForegroundWindow() != hwnd:
        _SwitchToThisWindow(hwnd, True)
        time.sleep(0.05)
    if _GetForegroundWindow() != hwnd:
        _send(_key(VK["alt"]))
        _SetForegroundWindow(hwnd)
        _send(_key(VK["alt"], flags=KEYEVENTF_KEYUP))
    time.sleep(0.05)
    return _GetForegroundWindow() == hwnd


def show(hwnd: int, state: str) -> None:
    require()
    _ShowWindow(hwnd, {"minimize": SW_MINIMIZE, "maximize": SW_MAXIMIZE, "restore": SW_RESTORE}.get(state, SW_SHOW))


def close(hwnd: int) -> None:
    require()
    _PostMessageW(hwnd, WM_CLOSE, 0, 0)


def move_window(hwnd: int, x: int, y: int, width: int, height: int) -> None:
    require()
    if _IsZoomed(hwnd) or _IsIconic(hwnd):
        _ShowWindow(hwnd, SW_RESTORE)
    _MoveWindow(hwnd, int(x), int(y), int(width), int(height), True)


def is_window(hwnd: int) -> bool:
    return IS_WINDOWS and bool(_IsWindow(hwnd))


def set_no_activate(hwnd: int, enabled: bool = True) -> None:
    require()
    style = _GetWindowLongW(hwnd, GWL_EXSTYLE)
    style = (style | WS_EX_NOACTIVATE) if enabled else (style & ~WS_EX_NOACTIVATE)
    _SetWindowLongW(hwnd, GWL_EXSTYLE, style)


def monitors() -> list[dict[str, Any]]:
    require()
    out: list[dict[str, Any]] = []

    def callback(handle: Any, _hdc: Any, _rect: Any, _lparam: Any) -> bool:
        info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        _GetMonitorInfoW(handle, ctypes.byref(info))
        r, w = info.rcMonitor, info.rcWork
        out.append({"x": r.left, "y": r.top, "width": r.right - r.left, "height": r.bottom - r.top,
                    "work": {"x": w.left, "y": w.top, "width": w.right - w.left, "height": w.bottom - w.top},
                    "primary": bool(info.dwFlags & 1)})
        return True

    _EnumDisplayMonitors(None, None, MonitorEnumProc(callback), 0)
    return out


def is_fullscreen() -> bool:
    """True when the foreground window covers its whole monitor (games, video, presentations)."""
    if not IS_WINDOWS:
        return False
    hwnd = _GetForegroundWindow()
    if not hwnd or _text(_GetClassNameW, hwnd, 256) in ("Progman", "WorkerW"):
        return False
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    if not _GetMonitorInfoW(_MonitorFromWindow(hwnd, 2), ctypes.byref(info)):
        return False
    rect = window_rect(hwnd)
    m = info.rcMonitor
    return rect["x"] <= m.left and rect["y"] <= m.top and rect["x"] + rect["width"] >= m.right \
        and rect["y"] + rect["height"] >= m.bottom


# --- keyboard layout -----------------------------------------------------------------------------------

LANGS = {0x419: "ru", 0x409: "en", 0x809: "en", 0x422: "uk", 0x423: "be", 0x43F: "kk"}


def keyboard_layout() -> str:
    if not IS_WINDOWS:
        return ""
    hwnd = _GetForegroundWindow()
    thread = _GetWindowThreadProcessId(hwnd, None) if hwnd else 0
    hkl = _GetKeyboardLayout(thread) or 0
    lang = int(hkl) & 0xFFFF
    return LANGS.get(lang, hex(lang))


def switch_layout(lang: str) -> None:
    require()
    code = {"ru": "00000419", "en": "00000409", "uk": "00000422"}.get(lang.lower()[:2])
    if not code:
        raise WinError("поддерживаются раскладки ru, en, uk")
    hkl = _LoadKeyboardLayoutW(code, 1)
    _PostMessageW(_GetForegroundWindow(), WM_INPUTLANGCHANGEREQUEST, 0, hkl or 0)


# --- folders, clipboard, power -----------------------------------------------------------------------

def known_folder(name: str) -> pathlib.Path:
    key = FOLDER_ALIASES.get(name.strip().lower(), name.strip().lower())
    if key == "home":
        return pathlib.Path.home()
    if key not in FOLDERS:
        raise WinError(f"неизвестная папка «{name}». Доступны: {', '.join(['home', *FOLDERS])}")
    if not IS_WINDOWS:
        return pathlib.Path.home() / key.capitalize()
    value = uuid.UUID(FOLDERS[key])
    guid = GUID(value.time_low, value.time_mid, value.time_hi_version, (ctypes.c_ubyte * 8)(*value.bytes[8:]))
    path = ctypes.c_wchar_p()
    if _SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(path)) != 0:
        return pathlib.Path.home() / key.capitalize()
    try:
        return pathlib.Path(path.value)
    finally:
        _CoTaskMemFree(path)


def _open_clipboard() -> None:
    for _ in range(20):
        if _OpenClipboard(None):
            return
        time.sleep(0.03)
    raise WinError("буфер обмена занят другим приложением")


def get_clipboard() -> str:
    require()
    _open_clipboard()
    try:
        handle = _GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        pointer = _GlobalLock(handle)
        try:
            return ctypes.wstring_at(pointer) if pointer else ""
        finally:
            _GlobalUnlock(handle)
    finally:
        _CloseClipboard()


def set_clipboard(text: str) -> None:
    require()
    data = (text or "").encode("utf-16-le") + b"\x00\x00"
    _open_clipboard()
    try:
        _EmptyClipboard()
        handle = _GlobalAlloc(GMEM_MOVEABLE, len(data))
        pointer = _GlobalLock(handle)
        ctypes.memmove(pointer, data, len(data))
        _GlobalUnlock(handle)
        if not _SetClipboardData(CF_UNICODETEXT, handle):
            raise WinError("не удалось записать буфер обмена")
    finally:
        _CloseClipboard()


def clipboard_sequence() -> int:
    return int(_GetClipboardSequenceNumber()) if IS_WINDOWS else 0


def lock() -> None:
    require()
    _LockWorkStation()


def battery() -> dict[str, Any]:
    require()
    status = SYSTEM_POWER_STATUS()
    _GetSystemPowerStatus(ctypes.byref(status))
    percent = None if status.BatteryLifePercent == 255 else int(status.BatteryLifePercent)
    seconds = None if status.BatteryLifeTime in (0xFFFFFFFF, -1) else int(status.BatteryLifeTime)
    return {"percent": percent, "charging": status.ACLineStatus == 1, "no_battery": bool(status.BatteryFlag & 128),
            "saver": bool(status.SystemStatusFlag & 1), "seconds_left": seconds}
