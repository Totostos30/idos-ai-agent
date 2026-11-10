"""Capture: screenshots (screen, area, active window), OCR of an area, QR codes, colour picker with history and an
optional imgbb upload. Files go to settings capture.folder (default: data/captures)."""
from __future__ import annotations

import asyncio
import base64
import datetime as _dt
import io
import logging
import pathlib
import time

from .. import bus, config, llm
from . import Feature

log = logging.getLogger("idos.capture")
IMGBB = "https:" + "//api.imgbb" + ".com/1/upload"


def hex_color(rgb: tuple) -> str:
    r, g, b = (max(0, min(255, int(v))) for v in rgb[:3])
    return f"#{r:02X}{g:02X}{b:02X}"


def color_formats(hex_value: str) -> dict:
    """#RRGGBB → hex, rgb() and hsl() strings for copying."""
    h = hex_value.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    rf, gf, bf = r / 255, g / 255, b / 255
    hi, lo = max(rf, gf, bf), min(rf, gf, bf)
    light = (hi + lo) / 2
    if hi == lo:
        hue = sat = 0.0
    else:
        d = hi - lo
        sat = d / (2 - hi - lo) if light > 0.5 else d / (hi + lo)
        if hi == rf:
            hue = (gf - bf) / d + (6 if gf < bf else 0)
        elif hi == gf:
            hue = (bf - rf) / d + 2
        else:
            hue = (rf - gf) / d + 4
        hue *= 60
    return {"hex": "#" + h.upper(), "rgb": f"rgb({r}, {g}, {b})",
            "hsl": f"hsl({round(hue)}, {round(sat * 100)}%, {round(light * 100)}%)"}


def shot_name(kind: str = "shot", moment: _dt.datetime | None = None, ext: str = "png") -> str:
    moment = moment or _dt.datetime.now()
    return f"{kind}-{moment:%Y-%m-%d_%H-%M-%S}.{ext}"


def unique(path: pathlib.Path) -> pathlib.Path:
    if not path.exists():
        return path
    for n in range(2, 1000):
        candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{path.stem}-{int(time.time())}{path.suffix}")


def decode_qr(image_bytes: bytes) -> list[str]:
    """QR codes on a PNG/JPEG: OpenCV if installed, otherwise pyzbar; [] when neither is available."""
    try:
        import cv2
        import numpy as np

        img = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        detector = cv2.QRCodeDetector()
        try:
            ok, texts, _pts, _ = detector.detectAndDecodeMulti(img)
            found = [t for t in texts if t] if ok else []
        except Exception:  # noqa: BLE001 - old OpenCV without Multi
            text, _pts, _ = detector.detectAndDecode(img)
            found = [text] if text else []
        if found:
            return found
    except ImportError:
        pass
    try:
        from PIL import Image
        from pyzbar.pyzbar import decode

        return [r.data.decode("utf-8", "replace") for r in decode(Image.open(io.BytesIO(image_bytes)))]
    except ImportError:
        return []


class Capture(Feature):
    name = "capture"

    def folder(self) -> pathlib.Path:
        custom = (self.settings.get("capture.folder", "") or "").strip()
        path = pathlib.Path(custom).expanduser() if custom else config.DATA / "captures"
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ------------------------------------------------------------------ grabbing
    @staticmethod
    def _grab(box: tuple[int, int, int, int] | None) -> bytes:
        from PIL import ImageGrab

        image = ImageGrab.grab(bbox=box, all_screens=True)
        out = io.BytesIO()
        image.save(out, "PNG", optimize=False)
        return out.getvalue()

    async def grab(self, mode: str = "screen", x: int = 0, y: int = 0, width: int = 0, height: int = 0) -> bytes:
        box = None
        if mode == "area":
            if width <= 0 or height <= 0:
                raise ValueError("нужна область: x, y, width, height")
            box = (int(x), int(y), int(x + width), int(y + height))
        elif mode == "window" and config.IS_WINDOWS:
            from .. import win

            r = win.window_rect(win.foreground()["hwnd"])
            box = (r["x"], r["y"], r["x"] + r["width"], r["y"] + r["height"])
        elif mode == "monitor" and config.IS_WINDOWS:
            from .. import win

            mons = win.monitors()
            m = mons[min(max(0, int(x)), len(mons) - 1)] if mons else None
            if m:
                box = (m["x"], m["y"], m["x"] + m["width"], m["y"] + m["height"])
        bus.emit("capture.flash", {"mode": mode})
        return await asyncio.to_thread(self._grab, box)

    async def screenshot(self, mode: str = "screen", copy: bool = True, **area) -> dict:
        data = await self.grab(mode, **area)
        path = unique(self.folder() / shot_name("shot"))
        await asyncio.to_thread(path.write_bytes, data)
        if copy and config.IS_WINDOWS:
            await asyncio.to_thread(self.copy_image, data)
        thumb = ""
        try:
            from ..vision import thumbnail

            thumb = "data:image/jpeg;base64," + base64.b64encode(thumbnail(data, 360)).decode()
        except Exception:  # noqa: BLE001
            pass
        info = {"path": str(path), "size": len(data), "thumb": thumb, "copied": bool(copy and config.IS_WINDOWS)}
        bus.emit("capture.saved", info)
        bus.card("Снимок экрана", path.name + (" — скопирован" if info["copied"] else ""), icon="camera",
                 actions=[{"id": "open", "label": "Открыть"}, {"id": "folder", "label": "В папке"}],
                 card_id=f"shot:{path}")
        return info

    @staticmethod
    def copy_image(data: bytes) -> None:
        """PNG → CF_DIB on the Windows clipboard."""
        import ctypes

        from PIL import Image

        image = Image.open(io.BytesIO(data)).convert("RGB")
        out = io.BytesIO()
        image.save(out, "BMP")
        dib = out.getvalue()[14:]
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        handle = kernel32.GlobalAlloc(0x0002, len(dib))
        ptr = kernel32.GlobalLock(handle)
        ctypes.memmove(ptr, dib, len(dib))
        kernel32.GlobalUnlock(handle)
        for _ in range(20):
            if user32.OpenClipboard(None):
                break
            time.sleep(0.03)
        try:
            user32.EmptyClipboard()
            user32.SetClipboardData(8, handle)  # CF_DIB
        finally:
            user32.CloseClipboard()

    async def ocr(self, lang: str = "ru", copy: bool = True, **area) -> dict:
        from ..win import ocr

        data = await self.grab("area" if area.get("width") else "screen", **area)
        lines = await ocr.recognize(data, lang)
        text = "\n".join(line.text for line in lines)
        if copy and text and config.IS_WINDOWS:
            from .. import win

            await asyncio.to_thread(win.set_clipboard, text)
        bus.card("Текст с экрана", (text[:140] + "…") if len(text) > 140 else (text or "текст не найден"), icon="text")
        return {"text": text, "lines": len(lines), "copied": bool(copy and text and config.IS_WINDOWS)}

    async def qr(self, **area) -> dict:
        data = await self.grab("area" if area.get("width") else "screen", **area)
        codes = await asyncio.to_thread(decode_qr, data)
        if codes:
            actions = [{"id": "copy", "label": "Скопировать"}]
            if codes[0].startswith(("http://", "https://")):
                actions.insert(0, {"id": "open", "label": "Открыть"})
            bus.card("QR-код", codes[0][:200], icon="qr", actions=actions, card_id="qr:" + codes[0][:200])
        else:
            bus.card("QR-код", "на экране не найден", kind="warning", icon="qr")
        return {"codes": codes}

    _picking: "asyncio.Event | None" = None

    @staticmethod
    def _grab_pixel(x: int, y: int) -> tuple:
        from PIL import ImageGrab

        return ImageGrab.grab(bbox=(x, y, x + 1, y + 1), all_screens=True).getpixel((0, 0))

    async def wait_for_pick(self, key_down, cursor, pixel, timeout: float = 30.0, poll: float = 0.03,
                            preview_every: float = 0.12) -> tuple[int, int] | None:
        """Ждёт Ctrl: сначала отпускания (если он ещё зажат горячей клавишей), потом нажатия.

        Пока ждёт — шлёт предпросмотр цвета под курсором. Esc, повторный запуск или таймаут — отмена (None)."""
        CTRL, ALT, SHIFT, ESC = 0x11, 0x12, 0x10, 0x1B
        cancel = asyncio.Event()
        self._picking = cancel
        loop = asyncio.get_running_loop()
        until = loop.time() + max(3.0, float(timeout))
        released = False
        last_preview, last_hex = 0.0, ""
        try:
            while loop.time() < until and not cancel.is_set():
                if key_down(ESC):
                    return None
                ctrl = key_down(CTRL)
                if not ctrl:
                    released = True
                elif released and not key_down(ALT) and not key_down(SHIFT):
                    return cursor()
                now = loop.time()
                if now - last_preview >= preview_every:
                    last_preview = now
                    try:
                        x, y = cursor()
                        hx = hex_color(await asyncio.to_thread(pixel, x, y))
                    except Exception:  # noqa: BLE001 - предпросмотр необязателен
                        hx = ""
                    if hx and hx != last_hex:
                        last_hex = hx
                        bus.emit("capture.color_pick", {"state": "preview", "hex": hx, "x": x, "y": y})
                await asyncio.sleep(poll)
            return None
        finally:
            if self._picking is cancel:
                self._picking = None

    async def pick_color(self, x: int | None = None, y: int | None = None, wait: bool = True,
                         timeout: float = 30.0) -> dict:
        if not config.IS_WINDOWS:
            raise RuntimeError("пипетка работает только в Windows")
        from .. import win

        if x is None or y is None:
            if not wait:
                x, y = win.cursor_pos()
            else:
                if self._picking is not None:  # повторное нажатие выключает пипетку
                    self._picking.set()
                    return {"cancelled": True}
                bus.emit("capture.color_pick", {"state": "armed", "timeout": timeout})
                point = await self.wait_for_pick(win.key_down, win.cursor_pos, self._grab_pixel, timeout)
                if point is None:
                    bus.emit("capture.color_pick", {"state": "cancel"})
                    return {"cancelled": True}
                x, y = point
                bus.emit("capture.color_pick", {"state": "done"})
        pixel = await asyncio.to_thread(self._grab_pixel, int(x), int(y))
        value = color_formats(hex_color(pixel))
        await asyncio.to_thread(win.set_clipboard, value["hex"])
        if self.db is not None:
            self.db.execute("INSERT INTO colors(hex, created) VALUES (?, ?)", (value["hex"], time.time()))
        bus.emit("capture.color", value)
        return value

    def colors(self, limit: int = 24) -> list[dict]:
        if self.db is None:
            return []
        rows = self.db.query("SELECT hex FROM colors ORDER BY id DESC LIMIT ?", (int(limit),))
        seen, out = set(), []
        for row in rows:
            if row["hex"] not in seen:
                seen.add(row["hex"])
                out.append(color_formats(row["hex"]))
        return out

    def recent(self, limit: int = 20) -> list[dict]:
        files = sorted((p for p in self.folder().glob("*") if p.suffix.lower() in (".png", ".jpg", ".mp4", ".mkv", ".gif")),
                       key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
        return [{"path": str(p), "name": p.name, "size": p.stat().st_size, "time": p.stat().st_mtime} for p in files]

    async def upload(self, path: str) -> dict:
        key = (self.settings.get("capture.imgbb_key", "") or "").strip()
        if not key:
            raise RuntimeError("укажите ключ imgbb в настройках (capture.imgbb_key)")
        data = await asyncio.to_thread(pathlib.Path(path).read_bytes)
        import urllib.parse

        body = urllib.parse.urlencode({"key": key, "image": base64.b64encode(data).decode()}).encode()
        result, _ = await llm.request_json_async(IMGBB, body, {"Content-Type": "application/x-www-form-urlencoded",
                                                              "User-Agent": "IDOS/2.0"}, "POST", 30)
        url = ((result or {}).get("data") or {}).get("url", "")
        if url and config.IS_WINDOWS:
            from .. import win

            await asyncio.to_thread(win.set_clipboard, url)
        return {"url": url}

    def on_action(self, data: dict) -> None:
        card, action = str((data or {}).get("card_id", "")), (data or {}).get("action")
        if not config.IS_WINDOWS or not card.startswith(("shot:", "qr:")):
            return
        import os
        import subprocess

        value = card.split(":", 1)[1]
        if card.startswith("shot:"):
            if action == "open":
                os.startfile(value)  # noqa: S606
            elif action == "folder":
                subprocess.Popen(["explorer", "/select,", value])  # noqa: S603,S607
        elif action == "open":
            os.startfile(value)  # noqa: S606
        elif action == "copy":
            from .. import win

            win.set_clipboard(value)

    async def start(self) -> None:
        self._unsubscribe.append(bus.on("island.action", self.on_action))

    def methods(self) -> dict:
        async def screenshot(mode: str = "screen", copy: bool = True, x: int = 0, y: int = 0, width: int = 0,
                             height: int = 0) -> dict:
            return await self.screenshot(mode, bool(copy), x=int(x), y=int(y), width=int(width), height=int(height))

        async def ocr(lang: str = "ru", x: int = 0, y: int = 0, width: int = 0, height: int = 0) -> dict:
            return await self.ocr(lang, x=int(x), y=int(y), width=int(width), height=int(height))

        async def qr(x: int = 0, y: int = 0, width: int = 0, height: int = 0) -> dict:
            return await self.qr(x=int(x), y=int(y), width=int(width), height=int(height))

        async def color(x: int | None = None, y: int | None = None, wait: bool = True, timeout: float = 30.0) -> dict:
            return await self.pick_color(x, y, bool(wait), float(timeout))

        async def colors(limit: int = 24) -> list:
            return self.colors(int(limit))

        async def recent(limit: int = 20) -> list:
            return self.recent(int(limit))

        async def upload(path: str) -> dict:
            return await self.upload(path)

        return {"capture.screenshot": screenshot, "capture.ocr": ocr, "capture.qr": qr, "capture.color": color,
                "capture.colors": colors, "capture.recent": recent, "capture.upload": upload}


def create(app) -> Capture:
    return Capture(app)
