"""Локальный сервер дерева коммитов: раздаёт статику и на каждый запрос /commits.json пересобирает
историю прямо из git, поэтому новые коммиты видны в браузере сразу, без перезапуска.

Запуск:  py -3 serve.py [порт]   (по умолчанию 8787; каталог — родитель этого файла с commit-tree/).
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent  # репозиторий


def run_git(args: list[str]) -> str:
    completed = subprocess.run(
        ["git", "-c", "safe.directory=*", *args],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return completed.stdout or ""


def commits_json() -> str:
    refs: dict[str, list[str]] = {}
    branches: list[dict[str, str]] = []
    for line in run_git(["for-each-ref", "--format=%(refname:short)%09%(objectname)",
                         "refs/heads", "refs/remotes"]).splitlines():
        line = line.strip()
        if not line or "\t" not in line:
            continue
        name, tip = line.split("\t", 1)
        branches.append({"name": name, "tip": tip})
        refs.setdefault(tip, []).append(name)

    raw = run_git(["log", "--all", "--date=iso",
                   "--pretty=format:%H%x1f%h%x1f%P%x1f%an%x1f%ad%x1f%s%x1e"])
    commits = []
    for record in raw.split("\x1e"):
        record = record.strip()
        if not record:
            continue
        parts = record.split("\x1f", 5)
        if len(parts) < 6:
            continue
        hash_, short, parents, author, date, subject = parts
        commits.append({
            "hash": hash_,
            "short": short,
            "parents": parents.split() if parents else [],
            "author": author,
            "date": date,
            "subject": subject,
            "refs": refs.get(hash_, []),
        })

    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "repository": ROOT.name,
        "branches": sorted(branches, key=lambda branch: branch["name"]),
        "commits": commits,
    }
    return json.dumps(payload, ensure_ascii=False)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # тихо
        return

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/commits.json":
            body = commits_json().encode("utf-8")
            self._send(200, "application/json; charset=utf-8", body)
            return
        if path == "/":
            path = "/index.html"
        target = (HERE / path.lstrip("/")).resolve()
        if not str(target).startswith(str(HERE)) or not target.is_file():
            self._send(404, "text/plain; charset=utf-8", b"not found")
            return
        self._send(200, self._mime(target), target.read_bytes())

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _mime(target: pathlib.Path) -> str:
        return {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json; charset=utf-8",
        }.get(target.suffix, "application/octet-stream")


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"IDOS commit tree: http://127.0.0.1:{port}  (репозиторий {ROOT})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()