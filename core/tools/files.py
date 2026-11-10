"""Files and folders, with automatic backups before anything destructive."""
from __future__ import annotations

import asyncio
import datetime as dt
import fnmatch
import json
import mimetypes
import os
import pathlib
import shutil
import subprocess
import time
import uuid
from typing import Any

from .. import config, vision, win
from ..util import human_bytes
from . import A, B, I, S, Tool, ToolError, get_bool, get_int, get_str, need
from .document import DOC_SUFFIXES, LIMIT, chunk, convert

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
SECRET_WORDS = ("password", "passwd", "парол", "secret", "credential", "id_rsa", "id_ed25519", "wallet.dat")
SECRET_SUFFIXES = {".kdbx", ".pem", ".key", ".pfx", ".p12", ".ppk"}
SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "$recycle.bin", "system volume information",
             "appdata", "windows", "program files", "program files (x86)", "programdata", "target", ".cache"}
TEXT_SUFFIXES = {".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml", ".ini", ".cfg", ".toml", ".log", ".py",
                 ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".bat", ".cmd", ".ps1", ".rs", ".java", ".c", ".cpp",
                 ".h", ".cs", ".go", ".sql", ".srt"}
BOM_SUFFIXES = {".ps1", ".csv"}
CRLF_SUFFIXES = {".bat", ".cmd"}


def resolve(raw: str) -> pathlib.Path:
    text = os.path.expandvars(os.path.expanduser((raw or "").strip().strip('"').strip("'")))
    if not text:
        raise ToolError("нужен путь (path)")
    head, _, rest = text.replace("\\", "/").partition("/")
    key = head.strip().lower()
    if key in win.FOLDERS or key in win.FOLDER_ALIASES or key == "home":
        base = win.known_folder(key)
        return base / rest if rest else base
    if key in ("workspace", "рабочая папка"):
        return config.WORKSPACE / rest if rest else config.WORKSPACE
    path = pathlib.Path(text)
    return path if path.is_absolute() else config.WORKSPACE / path


def inside(path: pathlib.Path, base: pathlib.Path | str) -> bool:
    try:
        path.resolve().relative_to(pathlib.Path(base).resolve())
        return True
    except (ValueError, OSError):
        return False


def is_secret(path: pathlib.Path) -> bool:
    name = path.name.lower()
    if name == ".env" or name.startswith(".env.") or path.suffix.lower() in SECRET_SUFFIXES:
        return True
    if name == "keys.json" and inside(path, config.DATA):
        return True
    return any(word in name for word in SECRET_WORDS)


def is_protected(path: pathlib.Path) -> bool:
    if inside(path, config.ROOT):
        free = (config.WORKSPACE, config.TEMP, config.DATA / "skills", config.DATA / "captures", config.DATA / "notes")
        return not any(inside(path, item) for item in free)
    system = os.environ.get("SystemRoot") or ("C:\\Windows" if win.IS_WINDOWS else "/usr")
    return inside(path, system)


def _user_roots() -> list[pathlib.Path]:
    roots = [pathlib.Path.home()]
    for name in win.FOLDERS:
        try:
            roots.append(win.known_folder(name))
        except win.WinError:
            pass
    return roots


def _same(a: pathlib.Path, b: pathlib.Path) -> bool:
    return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))


def _date(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")


def _hidden(path: pathlib.Path) -> bool:
    if path.name.startswith(".") or path.name.lower() in ("desktop.ini", "thumbs.db"):
        return True
    try:
        return bool(getattr(path.stat(), "st_file_attributes", 0) & 2)
    except OSError:
        return True


def decode(data: bytes) -> str:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


# --- backups -----------------------------------------------------------------------------------------

def backup(path: pathlib.Path, move: bool = False) -> pathlib.Path:
    folder = config.BACKUPS / f"{dt.datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / path.name
    if move:
        shutil.move(str(path), str(dest))
    elif path.is_dir():
        shutil.copytree(path, dest)
    else:
        shutil.copy2(path, dest)
    (folder / "manifest.json").write_text(json.dumps({"original": str(path), "backup": str(dest), "time": time.time(),
                                                      "moved": move}, ensure_ascii=False), encoding="utf-8")
    _prune()
    return dest


def _prune(days: int = 30) -> None:
    limit = time.time() - days * 86400
    try:
        for item in config.BACKUPS.iterdir():
            if item.is_dir() and item.stat().st_mtime < limit:
                shutil.rmtree(item, ignore_errors=True)
    except OSError:
        pass


def restore(target: pathlib.Path) -> str:
    want = os.path.normcase(str(target))
    best: dict[str, Any] | None = None
    for manifest in config.BACKUPS.glob("*/manifest.json"):
        try:
            info = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if want in (os.path.normcase(info.get("original", "")), os.path.normcase(info.get("backup", ""))):
            if pathlib.Path(info["backup"]).exists() and (best is None or info["time"] > best["time"]):
                best = info
    if best is None:
        raise ToolError(f"резервная копия для {target} не найдена")
    original = pathlib.Path(best["original"])
    note = ""
    if original.exists():
        note = f"\nтекущая версия сохранена: {backup(original, move=True)}"
    original.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(best["backup"], str(original))
    return f"восстановлено: {original}{note}"


# --- operations ------------------------------------------------------------------------------------

def listing(path: pathlib.Path, show_all: bool = False, limit: int = 150) -> str:
    if not path.exists():
        raise ToolError(f"папка не найдена: {path}")
    if not path.is_dir():
        return info(path)
    try:
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError as exc:
        raise ToolError(f"нет доступа к {path}") from exc
    rows, dirs, files = [], 0, 0
    for item in entries:
        if not show_all and _hidden(item):
            continue
        if item.is_dir():
            dirs += 1
            rows.append(f"📁 {item.name}")
            continue
        files += 1
        try:
            stat = item.stat()
            rows.append(f"{item.name} — {human_bytes(stat.st_size)}, {_date(stat.st_mtime)}")
        except OSError:
            rows.append(item.name)
    more = len(rows) - limit
    return f"{path}\nпапок: {dirs}, файлов: {files}\n" + "\n".join(rows[:limit]) + (f"\n…и ещё {more}" if more > 0 else "")


def info(path: pathlib.Path) -> str:
    if not path.exists():
        raise ToolError(f"не найдено: {path}")
    stat = path.stat()
    if path.is_dir():
        try:
            size = f"{sum(1 for _ in path.iterdir())} элементов"
        except OSError:
            size = "?"
        kind = "папка"
    else:
        size = human_bytes(stat.st_size)
        kind = mimetypes.guess_type(path.name)[0] or path.suffix or "файл"
    return f"{path}\nтип: {kind}\nразмер: {size}\nизменён: {_date(stat.st_mtime)}\nсоздан: {_date(stat.st_ctime)}"


def read(path: pathlib.Path, start: int = 0) -> str:
    if is_secret(path):
        raise ToolError("это файл с паролями/ключами — читать его запрещено")
    if not path.exists():
        raise ToolError(f"файл не найден: {path}")
    if path.is_dir():
        return listing(path)
    size = path.stat().st_size
    if path.suffix.lower() in DOC_SUFFIXES:
        text = convert(path)
    else:
        if size > 20_000_000:
            raise ToolError(f"файл слишком большой для чтения ({human_bytes(size)})")
        data = path.read_bytes()
        if data[:2] not in (b"\xff\xfe", b"\xfe\xff") and b"\x00" in data[:8192]:
            raise ToolError("это двоичный файл; откройте его в приложении (files open)")
        text = decode(data)
    return chunk(text, start, LIMIT, header=f"{path} ({human_bytes(size)})")


def write(path: pathlib.Path, text: str, append: bool = False) -> str:
    if is_secret(path):
        raise ToolError("запись в файлы с секретами запрещена")
    if is_protected(path):
        raise ToolError("это системный файл или файл IDOS — менять его нельзя (свободная папка: data/workspace)")
    if path.is_dir():
        raise ToolError(f"{path} — это папка")
    path.parent.mkdir(parents=True, exist_ok=True)
    note = ""
    existed = path.exists()
    if existed and not append and path.stat().st_size > 0:
        note = f"\nпрежняя версия сохранена: {backup(path)}"
    suffix = path.suffix.lower()
    encoding = "utf-8-sig" if suffix in BOM_SUFFIXES and not (append and existed) else "utf-8"
    if suffix in CRLF_SUFFIXES:
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    with open(path, "a" if append else "w", encoding=encoding, newline="") as handle:
        handle.write(text)
    return f"{'дописан' if append else 'записан'}: {path} ({human_bytes(path.stat().st_size)}){note}"


def _destination(src: pathlib.Path, to: str, overwrite: bool) -> pathlib.Path:
    dest = resolve(to)
    if dest.exists() and dest.is_dir() and not _same(dest, src):
        dest = dest / src.name
    if is_protected(dest):
        raise ToolError(f"сюда писать нельзя: {dest}")
    if dest.exists():
        if not overwrite:
            raise ToolError(f"уже существует: {dest}. Укажите overwrite=true или другое имя")
        backup(dest, move=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest


def _existing(args: dict) -> pathlib.Path:
    path = resolve(need(args, "path"))
    if not path.exists():
        raise ToolError(f"не найдено: {path}")
    return path


def _guard_change(path: pathlib.Path) -> None:
    if is_protected(path) or is_secret(path):
        raise ToolError(f"этот файл менять нельзя: {path}")
    if path == pathlib.Path(path.anchor) or any(_same(path, root) for root in _user_roots()):
        raise ToolError("системные папки пользователя и корень диска целиком трогать нельзя")


def search(base: pathlib.Path, pattern: str, text: str = "", limit: int = 40, budget: float = 15.0) -> str:
    pat = (pattern or "").lower().strip() or "*"
    is_glob = any(ch in pat for ch in "*?[")
    needle = (text or "").lower()
    found: list[pathlib.Path] = []
    started, timed_out = time.time(), False
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith(".")]
        for name in files + ([] if needle else dirs):
            low = name.lower()
            if not (fnmatch.fnmatch(low, pat) if is_glob else pat in low):
                continue
            full = pathlib.Path(root) / name
            if needle:
                if full.suffix.lower() not in TEXT_SUFFIXES or is_secret(full):
                    continue
                try:
                    if full.stat().st_size > 2_000_000 or needle not in decode(full.read_bytes()).lower():
                        continue
                except OSError:
                    continue
            found.append(full)
            if len(found) >= limit:
                break
        if len(found) >= limit:
            break
        if time.time() - started > budget:
            timed_out = True
            break
    if not found:
        return f"ничего не найдено в {base}" + (" (поиск остановлен по времени)" if timed_out else "")
    rows = []
    for item in found:
        try:
            rows.append(f"{item} — {'папка' if item.is_dir() else human_bytes(item.stat().st_size)}")
        except OSError:
            rows.append(str(item))
    tail = "\n(поиск остановлен по времени — уточните папку)" if timed_out else ""
    return f"найдено {len(rows)}:\n" + "\n".join(rows) + tail


def open_path(path: pathlib.Path, app: str = "") -> str:
    if not path.exists():
        raise ToolError(f"не найдено: {path}")
    if app:
        subprocess.Popen([app, str(path)])  # noqa: S603 - user-requested program
    elif win.IS_WINDOWS:
        os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", str(path)])  # noqa: S603,S607
    return f"открыто: {path}"


def run_sync(action: str, args: dict) -> str:
    if action == "desktop":
        text = listing(win.known_folder("desktop"))
        public = pathlib.Path(os.environ.get("PUBLIC", "C:\\Users\\Public")) / "Desktop"
        if win.IS_WINDOWS and public.exists():
            shared = [p.name for p in public.iterdir() if not _hidden(p)]
            if shared:
                text += "\nобщие ярлыки: " + ", ".join(shared[:40])
        return text
    if action == "list":
        return listing(resolve(get_str(args, "path") or "desktop"), get_bool(args, "all"))
    if action == "read":
        return read(resolve(need(args, "path")), get_int(args, "start", 0) or 0)
    if action in ("write", "append"):
        return write(resolve(need(args, "path")), get_str(args, "text"), append=action == "append")
    if action == "mkdir":
        path = resolve(need(args, "path"))
        if is_protected(path):
            raise ToolError(f"сюда писать нельзя: {path}")
        path.mkdir(parents=True, exist_ok=True)
        return f"папка готова: {path}"
    if action == "copy":
        src = _existing(args)
        dest = _destination(src, need(args, "to"), get_bool(args, "overwrite"))
        if src.is_dir():
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
        return f"скопировано: {dest}"
    if action in ("move", "rename"):
        src = _existing(args)
        _guard_change(src)
        to = need(args, "to")
        if action == "rename" and "/" not in to and "\\" not in to:
            to = str(src.parent / to)
        dest = _destination(src, to, get_bool(args, "overwrite"))
        shutil.move(str(src), str(dest))
        return f"{'переименовано' if action == 'rename' else 'перемещено'}: {dest}"
    if action == "delete":
        path = _existing(args)
        _guard_change(path)
        dest = backup(path, move=True)
        return f"удалено: {path}\nрезервная копия: {dest} (вернуть: files restore)"
    if action == "restore":
        return restore(resolve(need(args, "path")))
    if action == "search":
        base = resolve(get_str(args, "path") or "home")
        return search(base, get_str(args, "pattern"), get_str(args, "text"))
    if action == "info":
        return info(_existing(args))
    if action == "open":
        return open_path(_existing(args), get_str(args, "app"))
    raise ToolError(f"неизвестное действие {action}")


async def handler(action: str, args: dict, ctx: Any) -> str:
    if action == "read":
        path = resolve(need(args, "path"))
        if path.suffix.lower() in IMAGE_SUFFIXES and path.exists():
            if is_secret(path):
                raise ToolError("читать этот файл запрещено")
            data = await asyncio.to_thread(path.read_bytes)
            question = get_str(args, "text") or "Опиши изображение подробно и перепиши весь текст на нём."
            return f"{path.name} (изображение):\n" + await vision.describe(ctx.app, data, question, cancel=ctx.cancel)
    return await asyncio.to_thread(run_sync, action, args)


def register(reg: Any, app: Any) -> None:
    reg.add(Tool(
        "files",
        "Файлы и папки. path: абсолютный, или desktop/…, documents/…, downloads/… (можно по-русски), "
        "относительный = data/workspace. Перед удалением и перезаписью делается копия (restore).",
        {"desktop": A("Смотрю рабочий стол", "read"), "list": A("Открываю папку {path}", "read"),
         "read": A("Читаю {path}", "read"), "write": A("Записываю {path}"), "append": A("Дописываю {path}"),
         "mkdir": A("Создаю папку {path}"), "copy": A("Копирую {path}"), "move": A("Перемещаю {path}", "danger"),
         "rename": A("Переименовываю {path}"), "delete": A("Удаляю {path}", "danger"),
         "restore": A("Восстанавливаю {path}"), "search": A("Ищу файлы {pattern}", "read"),
         "info": A("Свойства {path}", "read"), "open": A("Открываю {path}")},
        {"path": S("файл или папка"), "to": S("куда (copy/move) или новое имя (rename)"),
         "text": S("текст для write/append; для search — искать внутри файлов; для read картинки — вопрос"),
         "pattern": S("часть имени или маска, напр. *.pdf"), "start": I("с какого символа читать"),
         "overwrite": B("заменить существующий"), "app": S("чем открыть (open)"),
         "all": B("list: показать скрытые и системные")},
        handler, timeout=90))
