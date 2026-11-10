"""API keys: data/keys.json (managed from the UI) plus .env / environment variables.

Several keys per provider are supported (GROQ_API_KEYS=key1,key2 or GROQ_API_KEY_1=…, GROQ_API_KEY_2=…).
The agent's file tools refuse to read .env and keys.json; util.SecretFilter masks keys in logs.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
import re
import threading
import time
import uuid
from typing import Any, Mapping

from . import config

log = logging.getLogger("idos.keystore")

ENV_NAMES: dict[str, tuple[str, ...]] = {
    "groq": ("GROQ_API_KEY", "GROQ_API_KEYS"),
    "anymodel": ("ANYMODEL_API_KEY",),
    "cerebras": ("CEREBRAS_API_KEY", "CEREBRAS_API_KEYS"),
    "openrouter": ("OPENROUTER_API_KEY", "OPENROUTER_API_KEYS"),
    "gemini": ("GEMINI_API_KEY", "GEMINI_API_KEYS", "GOOGLE_API_KEY"),
    "mistral": ("MISTRAL_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "xai": ("XAI_API_KEY",),
}
_SPLIT_RE = re.compile(r"[\s,;]+")


def split_keys(text: str) -> list[str]:
    return [part.strip().strip("'\"") for part in _SPLIT_RE.split(text or "") if len(part.strip().strip("'\"")) >= 8]


def parse_env_file(path: pathlib.Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.lower().startswith("export "):
            key = key[7:].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


class KeyStore:
    def __init__(self, path: pathlib.Path | str | None = None, env_file: pathlib.Path | str | None = None,
                 environ: Mapping[str, str] | None = None) -> None:
        self.path = pathlib.Path(path) if path else config.DATA / "keys.json"
        self.env_file = pathlib.Path(env_file) if env_file else config.ENV_FILE
        self.environ = os.environ if environ is None else environ
        self._lock = threading.RLock()

    @staticmethod
    def mask(secret: str) -> str:
        secret = secret or ""
        return secret[:4] + "…" + secret[-4:] if len(secret) > 10 else "…"

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return {"keys": [], "env": {}}
        except (OSError, ValueError) as exc:
            log.warning("keys.json unreadable: %s", exc)
            return {"keys": [], "env": {}}
        if not isinstance(data, dict):
            return {"keys": [], "env": {}}
        data.setdefault("keys", [])
        data.setdefault("env", {})
        return data

    def _save(self, data: dict[str, Any]) -> None:
        config.atomic_write(self.path, json.dumps(data, ensure_ascii=False, indent=2))

    def _env_values(self) -> dict[str, str]:
        values = parse_env_file(self.env_file)
        values.update({k: v for k, v in self.environ.items() if k.endswith(tuple("0123456789")) or "API_KEY" in k})
        return values

    def entries(self, provider: str | None = None) -> list[dict[str, Any]]:
        """All keys in a stable order: [{id, provider, secret, label, source, enabled}]."""
        with self._lock:
            data = self._load()
            values = self._env_values()
        overrides = data.get("env", {})
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for name, names in ENV_NAMES.items():
            secrets: list[str] = []
            for var in names:
                secrets += split_keys(values.get(var, ""))
            numbered = sorted((k for k in values if re.fullmatch(names[0] + r"_\d+", k)),
                              key=lambda k: int(k.rsplit("_", 1)[1]))
            for var in numbered:
                secrets += split_keys(values[var])
            for secret in secrets:
                if secret in seen:
                    continue
                seen.add(secret)
                key_id = "env-" + hashlib.sha1(secret.encode("utf-8")).hexdigest()[:8]
                over = overrides.get(key_id, {})
                if over.get("removed"):
                    seen.discard(secret)  # тот же ключ, добавленный вручную, должен показываться
                    continue
                out.append({"id": key_id, "provider": name, "secret": secret, "label": over.get("label", ""),
                            "source": "env", "enabled": bool(over.get("enabled", True))})
        for item in data.get("keys", []):
            secret = str(item.get("secret", ""))
            if not secret or secret in seen or item.get("provider") not in ENV_NAMES:
                continue
            seen.add(secret)
            out.append({"id": item.get("id") or "k" + hashlib.sha1(secret.encode()).hexdigest()[:6],
                        "provider": item["provider"], "secret": secret, "label": item.get("label", ""),
                        "source": "file", "enabled": bool(item.get("enabled", True))})
        order = list(ENV_NAMES)
        out.sort(key=lambda e: order.index(e["provider"]))
        return [e for e in out if provider is None or e["provider"] == provider]

    def add(self, provider: str, secret: str, label: str = "") -> dict[str, Any]:
        provider, secret = provider.strip().lower(), secret.strip().strip("'\"")
        if provider not in ENV_NAMES:
            raise ValueError(f"неизвестный провайдер «{provider}»")
        if len(secret) < 8 or any(ch.isspace() for ch in secret):
            raise ValueError("это не похоже на API-ключ")
        with self._lock:
            if any(e["secret"] == secret for e in self.entries()):
                raise ValueError("такой ключ уже добавлен")
            data = self._load()
            item = {"id": "k" + uuid.uuid4().hex[:6], "provider": provider, "secret": secret, "label": label.strip(),
                    "enabled": True, "added": time.time()}
            data["keys"].append(item)
            self._save(data)
        return {k: v for k, v in item.items() if k != "secret"}

    def add_many(self, provider: str, text: str) -> dict[str, int]:
        """Paste many keys at once (one per line or comma separated)."""
        added = skipped = 0
        for secret in split_keys(text):
            try:
                self.add(provider, secret)
                added += 1
            except ValueError:
                skipped += 1
        return {"added": added, "skipped": skipped}

    @staticmethod
    def local_id(key_id: str) -> str:
        """Router account ids look like "groq:k1a2b3c"; the store keeps only "k1a2b3c" / "env-..."."""
        key_id = str(key_id or "").strip()
        head, sep, tail = key_id.partition(":")
        return tail if sep and head in ENV_NAMES else key_id

    def remove(self, key_id: str) -> bool:
        key_id = self.local_id(key_id)
        with self._lock:
            data = self._load()
            if key_id.startswith("env-"):
                if not any(e["id"] == key_id for e in self.entries()):
                    return False
                data.setdefault("env", {}).setdefault(key_id, {})["removed"] = True
                self._save(data)
                return True
            before = len(data["keys"])
            data["keys"] = [k for k in data["keys"] if k.get("id") != key_id]
            if len(data["keys"]) == before:
                return False
            self._save(data)
            return True

    def _patch(self, key_id: str, **changes: Any) -> bool:
        key_id = self.local_id(key_id)
        with self._lock:
            data = self._load()
            if key_id.startswith("env-"):
                data["env"].setdefault(key_id, {}).update(changes)
                self._save(data)
                return True
            for item in data["keys"]:
                if item.get("id") == key_id:
                    item.update(changes)
                    self._save(data)
                    return True
        return False

    def set_enabled(self, key_id: str, enabled: bool) -> bool:
        return self._patch(key_id, enabled=bool(enabled))

    def set_label(self, key_id: str, label: str) -> bool:
        return self._patch(key_id, label=label.strip())
