"""Entry point for coding-agent hooks: `python -m core.hook <source> <event> [--home DIR]`.

Claude Code passes the event JSON on stdin; Codex passes it as the last argument. The event is written to the
IDOS spool folder and shown on the island by core.features.hooks. Always exits 0 so it never blocks the agent."""
from __future__ import annotations

import json
import os
import sys


def main(argv: list[str]) -> int:
    args = list(argv)
    if "--home" in args:
        i = args.index("--home")
        if i + 1 < len(args):
            os.environ["IDOS_HOME"] = args[i + 1]
            del args[i:i + 2]
    source = args[0] if args else "agent"
    event = args[1] if len(args) > 1 else "notification"
    payload: dict = {}
    raw = args[2] if len(args) > 2 else ""
    if not raw and not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
        except (OSError, ValueError):
            raw = ""
    if raw.strip():
        try:
            value = json.loads(raw)
            payload = value if isinstance(value, dict) else {"message": str(value)}
        except ValueError:
            payload = {"message": raw.strip()[:500]}
    try:
        from .features.hooks import write_event
        from . import config

        write_event(source, event, payload, config.DATA / "hooks")
    except Exception:  # noqa: BLE001 - a hook must never fail the calling agent
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
