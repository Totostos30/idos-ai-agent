"""PowerShell / cmd / Python with timeouts and kill-switch support."""
from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from typing import Any

from .. import config
from ..util import clip_middle, decode_output
from . import A, Aborted, B, I, Result, S, Tool, ToolError, get_bool, get_int, get_str, need

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
PS_PREFIX = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; $OutputEncoding=[System.Text.Encoding]::UTF8; "
             "$ProgressPreference='SilentlyContinue'; ")
OUT_LIMIT = 6000
# Background processes started by the agent: (Popen, start time). The kill switch stops those of the current run.
BACKGROUND: list[tuple[subprocess.Popen, float]] = []


def kill_background(since: float = 0.0) -> int:
    """Kill still-running background processes started at or after `since`; returns how many were killed."""
    killed = 0
    for item in list(BACKGROUND):
        proc, started = item
        if proc.poll() is not None:
            BACKGROUND.remove(item)
            continue
        if started >= since:
            try:
                _kill(proc)
                killed += 1
            except OSError:
                pass
            BACKGROUND.remove(item)
    return killed
ERR_LIMIT = 2500


def build(command: str, shell: str = "powershell") -> list[str]:
    if os.name != "nt":
        return ["bash", "-lc", command]
    if shell == "cmd":
        return ["cmd.exe", "/d", "/s", "/c", "chcp 65001>nul & " + command]
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
            PS_PREFIX + command]


def _kill(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True,
                       creationflags=CREATE_NO_WINDOW, check=False)
    else:
        proc.kill()


def execute(args: list[str], ctx: Any = None, timeout: float = 60, cwd: str | None = None,
            stdin: str | None = None) -> tuple[int | None, str, str, str]:
    """Run a process; returns (code, stdout, stderr, status) with status '', 'timeout' or 'cancelled'."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,  # noqa: S603 - the point of the tool
                            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL, cwd=cwd, env=env,
                            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
    deadline = time.time() + timeout
    data = stdin.encode("utf-8") if stdin is not None else None
    while True:
        try:
            out, err = proc.communicate(input=data, timeout=0.25)
            return proc.returncode, decode_output(out), decode_output(err), ""
        except subprocess.TimeoutExpired:
            data = None
            cancelled = bool(ctx is not None and getattr(ctx, "cancelled", False))
            if cancelled or time.time() > deadline:
                _kill(proc)
                try:
                    out, err = proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    out, err = b"", b""
                return None, decode_output(out), decode_output(err), "cancelled" if cancelled else "timeout"


def report(code: int | None, out: str, err: str, status: str, timeout: float) -> Result:
    if status == "cancelled":
        raise Aborted("остановлено пользователем")
    parts = [f"тайм-аут {int(timeout)} с — процесс остановлен" if status == "timeout" else f"код выхода: {code}"]
    if out.strip():
        parts.append("вывод:\n" + clip_middle(out.strip(), OUT_LIMIT))
    if err.strip():
        parts.append("ошибки:\n" + clip_middle(err.strip(), ERR_LIMIT))
    if not out.strip() and not err.strip():
        parts.append("(нет вывода)")
    return Result("\n".join(parts), ok=status == "" and code == 0)


def _cwd(args: dict) -> str:
    from .files import resolve

    raw = get_str(args, "cwd").strip()
    path = resolve(raw) if raw else config.WORKSPACE
    path.mkdir(parents=True, exist_ok=True) if path == config.WORKSPACE else None
    if not path.is_dir():
        raise ToolError(f"папка не найдена: {path}")
    return str(path)


def handler(action: str, args: dict, ctx: Any) -> Result:
    timeout = float(min(max(get_int(args, "timeout", 60) or 60, 1), 900))
    cwd = _cwd(args)
    if action == "python":
        code = need(args, "code")
        folder = config.TEMP / "py"
        folder.mkdir(parents=True, exist_ok=True)
        script = folder / f"snippet-{uuid.uuid4().hex[:8]}.py"
        script.write_text(code, encoding="utf-8")
        try:
            return report(*execute([sys.executable, "-X", "utf8", str(script)], ctx, timeout, cwd), timeout)
        finally:
            script.unlink(missing_ok=True)
    command = need(args, "command")
    argv = build(command, get_str(args, "shell", "powershell").lower())
    if get_bool(args, "background"):
        flags = (CREATE_NO_WINDOW | DETACHED_PROCESS) if os.name == "nt" else 0
        proc = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,  # noqa: S603
                                stdin=subprocess.DEVNULL, creationflags=flags)
        BACKGROUND[:] = [item for item in BACKGROUND if item[0].poll() is None]
        BACKGROUND.append((proc, time.time()))
        return Result(f"запущено в фоне, PID {proc.pid}")
    return report(*execute(argv, ctx, timeout, cwd), timeout)


def register(reg: Any, app: Any) -> None:
    reg.add(Tool(
        "shell",
        "Команды Windows: PowerShell (по умолчанию) или cmd; python — выполнить код Python. "
        "Для программ, которые должны продолжать работу, — background=true.",
        {"run": A("Выполняю команду {command}", "danger"), "python": A("Запускаю Python-код", "danger")},
        {"command": S("команда"), "shell": {"type": "string", "enum": ["powershell", "cmd"]},
         "code": S("код Python"), "cwd": S("рабочая папка"), "timeout": I("секунд, по умолчанию 60"),
         "background": B("запустить и не ждать")},
        handler, timeout=930))
