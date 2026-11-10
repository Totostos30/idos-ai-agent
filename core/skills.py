"""Skills: markdown playbooks the agent loads on demand.

Built-in skills live in ``skills/*.md``; skills created by the user or by the
agent itself live in ``data/skills/*.md`` and override built-ins with the same
name. A file may start with front matter::

    ---
    name: telegram
    title: Telegram
    description: Отправка сообщений и поиск чатов в Telegram Desktop
    ---
"""
from __future__ import annotations

import pathlib
import re
import time
from dataclasses import dataclass

from . import config

USER_SKILLS = config.DATA / "skills"
FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)
SAFE_NAME = re.compile(r"[^a-z0-9а-яё_-]+")


@dataclass
class Skill:
    name: str
    title: str
    description: str
    path: pathlib.Path
    builtin: bool


def _split(text: str) -> tuple[dict[str, str], str]:
    meta: dict[str, str] = {}
    match = FRONT.match(text)
    if not match:
        return meta, text
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip().lower()] = value.strip().strip('"').strip("'")
    return meta, text[match.end():]


def _parse(path: pathlib.Path, builtin: bool) -> Skill | None:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    meta, body = _split(text)
    description = meta.get("description", "")
    if not description:
        for line in body.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                description = line
                break
    name = (meta.get("name") or path.stem).strip().lower()
    return Skill(name=name, title=meta.get("title") or name, description=description[:160], path=path,
                 builtin=builtin)


def slug(name: str) -> str:
    return SAFE_NAME.sub("-", name.strip().lower()).strip("-")[:48] or "skill"


class Skills:
    def __init__(self, dirs: list[tuple[pathlib.Path, bool]] | None = None) -> None:
        self.dirs = dirs or [(config.SKILLS_DIR, True), (USER_SKILLS, False)]
        self._cache: tuple[float, dict[str, Skill]] = (0.0, {})

    def all(self) -> dict[str, Skill]:
        now = time.time()
        if now - self._cache[0] < 10 and self._cache[1]:
            return self._cache[1]
        found: dict[str, Skill] = {}
        for folder, builtin in self.dirs:
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.md")):
                if path.name.lower() in ("readme.md", "agents.md"):
                    continue
                skill = _parse(path, builtin)
                if skill:
                    found[skill.name] = skill
        self._cache = (now, found)
        return found

    def invalidate(self) -> None:
        self._cache = (0.0, {})

    def list(self) -> list[dict[str, object]]:
        return [{"name": s.name, "title": s.title, "description": s.description, "builtin": s.builtin}
                for s in sorted(self.all().values(), key=lambda s: s.name)]

    def find(self, name: str) -> Skill | None:
        skills = self.all()
        key = (name or "").strip().lower()
        if key in skills:
            return skills[key]
        for skill in skills.values():
            if key and (key in skill.name or key in skill.title.lower()):
                return skill
        return None

    def load(self, name: str) -> str:
        skill = self.find(name)
        if skill is None:
            names = ", ".join(sorted(self.all())) or "нет"
            raise KeyError(f"Навык «{name}» не найден. Доступны: {names}")
        _, body = _split(skill.path.read_text(encoding="utf-8-sig"))
        return body.strip()

    def prompt_list(self) -> str:
        lines = [f"- {s.name}: {s.description}" for s in sorted(self.all().values(), key=lambda s: s.name)]
        return "\n".join(lines) or "- (нет)"

    def save(self, name: str, description: str, body: str, title: str = "") -> pathlib.Path:
        USER_SKILLS.mkdir(parents=True, exist_ok=True)
        key = slug(name)
        path = USER_SKILLS / f"{key}.md"
        head = f"---\nname: {key}\ntitle: {title or name}\ndescription: {description.strip()[:160]}\n---\n\n"
        config.atomic_write(path, head + body.strip() + "\n")
        self.invalidate()
        return path

    def delete(self, name: str) -> bool:
        skill = self.find(name)
        if skill is None or skill.builtin:
            return False
        skill.path.unlink(missing_ok=True)
        self.invalidate()
        return True
