"""Text recognition on screenshots: Windows.Media.Ocr (built into Windows 10/11, offline) through the winrt
packages, with Tesseract (pytesseract) as a fallback. Returns lines with word boxes in image pixels, plus a
phrase search that gives the exact center to click.
"""
from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass, field

log = logging.getLogger("idos.ocr")
Word = tuple[str, int, int, int, int]  # text, x, y, width, height


class OcrUnavailable(RuntimeError):
    pass


@dataclass
class Line:
    text: str
    x: int
    y: int
    w: int
    h: int
    words: list[Word] = field(default_factory=list)


def _box(words: list[Word]) -> tuple[int, int, int, int]:
    x0, y0 = min(w[1] for w in words), min(w[2] for w in words)
    x1, y1 = max(w[1] + w[3] for w in words), max(w[2] + w[4] for w in words)
    return x0, y0, x1 - x0, y1 - y0


def make_line(text: str, words: list[Word]) -> Line:
    return Line(text, *_box(words), words=words)


async def _winrt(image: bytes, lang: str) -> list[Line]:
    try:
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream
    except ImportError as exc:
        raise OcrUnavailable("нет пакетов winrt для Windows OCR") from exc
    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(image)
    await writer.store_async()
    writer.detach_stream()
    stream.seek(0)
    bitmap = await (await BitmapDecoder.create_async(stream)).get_software_bitmap_async()
    engine = None
    for tag in dict.fromkeys([lang, "ru", "en-US"]):
        try:
            if OcrEngine.is_language_supported(Language(tag)):
                engine = OcrEngine.try_create_from_language(Language(tag))
                break
        except Exception:  # noqa: BLE001
            continue
    engine = engine or OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise OcrUnavailable("в Windows не установлен пакет распознавания текста для языка")
    result = await engine.recognize_async(bitmap)
    lines = []
    for line in result.lines:
        words = [(w.text, int(w.bounding_rect.x), int(w.bounding_rect.y), int(w.bounding_rect.width),
                  int(w.bounding_rect.height)) for w in line.words]
        if words:
            lines.append(make_line(line.text, words))
    return lines


def _tesseract(image: bytes, lang: str) -> list[Line]:
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise OcrUnavailable("нет pytesseract") from exc
    with Image.open(io.BytesIO(image)) as img:
        try:
            data = pytesseract.image_to_data(img, lang="rus+eng" if lang.startswith("ru") else "eng",
                                             output_type=pytesseract.Output.DICT)
        except pytesseract.TesseractNotFoundError as exc:
            raise OcrUnavailable("не установлен Tesseract") from exc
    groups: dict[tuple, list[Word]] = {}
    for i, text in enumerate(data["text"]):
        if not str(text).strip() or float(data["conf"][i]) < 30:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        groups.setdefault(key, []).append((str(text), int(data["left"][i]), int(data["top"][i]),
                                           int(data["width"][i]), int(data["height"][i])))
    return [make_line(" ".join(w[0] for w in words), words) for words in groups.values()]


async def recognize(image: bytes, lang: str = "ru") -> list[Line]:
    errors = []
    try:
        return await _winrt(image, lang)
    except OcrUnavailable as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Windows OCR: {exc}")
    try:
        return await asyncio.to_thread(_tesseract, image, lang)
    except OcrUnavailable as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Tesseract: {exc}")
    raise OcrUnavailable("; ".join(errors))


def _norm(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split())


def find_text(lines: list[Line], query: str, limit: int = 10) -> list[dict]:
    """Lines containing the phrase (case and ё-insensitive) with the box and center of the matching words."""
    needle = _norm(query)
    if not needle:
        return []
    span, hits = len(needle.split()) + 1, []
    for line in lines:
        normed = [_norm(w[0]) for w in line.words]
        box = None
        for size in range(1, min(len(normed), span) + 1):
            for i in range(len(normed) - size + 1):
                if needle in " ".join(normed[i:i + size]):
                    box = _box(line.words[i:i + size])
                    break
            if box:
                break
        if box is None and needle in _norm(line.text):
            box = (line.x, line.y, line.w, line.h)
        if box:
            hits.append({"text": line.text, "x": box[0] + box[2] // 2, "y": box[1] + box[3] // 2, "box": box})
            if len(hits) >= limit:
                break
    return hits


def shift(lines: list[Line], dx: int, dy: int, scale: float = 1.0) -> list[Line]:
    """Map boxes from image pixels to screen pixels (image scale, then the capture offset)."""
    def tr(word: Word) -> Word:
        return (word[0], int(word[1] * scale) + dx, int(word[2] * scale) + dy, int(word[3] * scale), int(word[4] * scale))
    return [make_line(line.text, [tr(w) for w in line.words]) for line in lines]
