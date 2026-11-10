"""Documents (PDF, Word, Excel, PowerPoint, EPUB, HTML...) as Markdown via MarkItDown."""
from __future__ import annotations

import pathlib
import re
from typing import Any

from . import A, I, S, Tool, ToolError, get_int, need

DOC_SUFFIXES = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".epub", ".odt", ".ods", ".html", ".htm",
                ".msg", ".ipynb"}
LIMIT = 12000
_cache: dict[tuple[str, float], str] = {}


def chunk(text: str, start: int = 0, limit: int = LIMIT, header: str = "") -> str:
    start = max(0, int(start or 0))
    part = text[start:start + limit]
    rest = len(text) - start - len(part)
    out = (header + "\n" if header else "") + part
    if rest > 0:
        out += f"\n…[ещё {rest} символов — продолжите с start={start + len(part)}]"
    return out


def _markitdown(path: pathlib.Path) -> str:
    from markitdown import MarkItDown  # type: ignore

    try:
        converter = MarkItDown(enable_plugins=False)
    except TypeError:
        converter = MarkItDown()
    result = converter.convert(str(path))
    return getattr(result, "markdown", None) or getattr(result, "text_content", "") or ""


def _fallback(path: pathlib.Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader  # type: ignore

        return "\n\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
    if suffix == ".docx":
        import docx  # type: ignore

        return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
    if suffix in (".html", ".htm"):
        raw = path.read_text(encoding="utf-8", errors="replace")
        raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw))
    raise ToolError("для этого формата нужен пакет markitdown (установите зависимости через Setup.ps1)")


def convert(path: pathlib.Path) -> str:
    path = pathlib.Path(path)
    if not path.exists():
        raise ToolError(f"файл не найден: {path}")
    key = (str(path), path.stat().st_mtime)
    if key in _cache:
        return _cache[key]
    try:
        text = _markitdown(path)
    except ImportError:
        text = _fallback(path)
    except Exception as exc:  # noqa: BLE001 - try the small fallbacks before giving up
        try:
            text = _fallback(path)
        except Exception:  # noqa: BLE001
            raise ToolError(f"не удалось прочитать документ: {exc}") from exc
    text = (text or "").strip() or "(в документе нет текста — возможно, это скан; откройте его и используйте OCR)"
    if len(_cache) >= 12:
        _cache.pop(next(iter(_cache)))
    _cache[key] = text
    return text


def register(reg: Any, app: Any) -> None:
    def handler(action: str, args: dict, ctx: Any) -> str:
        from .files import is_secret, resolve

        path = resolve(need(args, "path"))
        if is_secret(path):
            raise ToolError("это файл с паролями/ключами — читать его запрещено")
        return chunk(convert(path), get_int(args, "start", 0) or 0, LIMIT, header=f"{path.name}:")

    reg.add(Tool("document", "Прочитать документ (PDF, Word, Excel, PowerPoint, EPUB, HTML) как Markdown; длинные — частями через start.",
                 {"read": A("Читаю документ {path}", "read")},
                 {"path": S("путь к файлу"), "start": I("с какого символа читать")}, handler, timeout=180))
