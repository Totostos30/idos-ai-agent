"""Ask the vision model about an image (screenshots, photos, image files)."""
from __future__ import annotations

import base64
import io
import re
from typing import Any

MAX_SIDE = 1600
DEFAULT_QUESTION = ("Опиши изображение для ассистента, который управляет компьютером: какие окна и "
                    "приложения видны, ключевые кнопки и поля, весь важный текст дословно.")
COORD_HINT = ("Если указываешь положение элемента, пиши координаты его центра строго в виде (x, y) — "
              "в пикселях этого изображения размером {w}×{h}.")
COORD_RE = re.compile(r"\((\d{1,4})\s*[,;]\s*(\d{1,4})\)")


def sniff(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/png"


def data_url(image: bytes, mime: str | None = None) -> str:
    return f"data:{mime or sniff(image)};base64,{base64.b64encode(image).decode('ascii')}"


def prepare(image: bytes, max_side: int = MAX_SIDE) -> tuple[bytes, str, float, tuple[int, int]]:
    """Downscale + JPEG so requests stay far below Groq's 4 MB base64 limit.

    Returns (bytes, mime, scale, (width, height)) where ``scale`` maps image pixels back to the original.
    """
    try:
        from PIL import Image
    except ImportError:
        return image, sniff(image), 1.0, (0, 0)
    with Image.open(io.BytesIO(image)) as img:
        img.load()
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        original = img.size
        scale = max_side / max(original)
        if scale < 1:
            img = img.resize((max(1, int(original[0] * scale)), max(1, int(original[1] * scale))), Image.LANCZOS)
        else:
            scale = 1.0
        out = io.BytesIO()
        img.save(out, "JPEG", quality=82, optimize=True)
        return out.getvalue(), "image/jpeg", 1.0 / scale, img.size


def rescale(text: str, scale: float, offset: tuple[int, int] = (0, 0)) -> str:
    """Convert "(x, y)" coordinates from the downscaled image back to screen pixels."""
    if scale == 1.0 and offset == (0, 0):
        return text

    def repl(match: re.Match) -> str:
        x = int(int(match.group(1)) * scale) + offset[0]
        y = int(int(match.group(2)) * scale) + offset[1]
        return f"({x}, {y})"

    return COORD_RE.sub(repl, text)


async def describe(app: Any, image: bytes, question: str = "", cancel: Any = None, max_tokens: int = 800,
                   max_side: int = MAX_SIDE, coords: bool = False, offset: tuple[int, int] = (0, 0)) -> str:
    data, mime, scale, size = prepare(image, max_side)
    prompt = question.strip() or DEFAULT_QUESTION
    if coords and size[0]:
        prompt += "\n" + COORD_HINT.format(w=size[0], h=size[1])
    done = await app.router.chat(
        [{"role": "user", "content": [{"type": "text", "text": prompt},
                                      {"type": "image_url", "image_url": {"url": data_url(data, mime)}}]}],
        stream=False, purpose="vision", max_tokens=max_tokens, cancel=cancel)
    text = (done.text or "").strip()
    return rescale(text, scale, offset) if coords else text


def thumbnail(image: bytes, width: int = 480, quality: int = 70) -> bytes:
    """Small JPEG preview for the step card in the UI; returns the input unchanged if Pillow is missing."""
    try:
        from PIL import Image
    except ImportError:
        return image
    try:
        with Image.open(io.BytesIO(image)) as source:
            img = source.convert("RGB")
        if img.width > width:
            img = img.resize((width, max(1, round(img.height * width / img.width))), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, "JPEG", quality=quality, optimize=True)
        return out.getvalue()
    except Exception:  # noqa: BLE001 - a broken preview must never break the tool
        return image
