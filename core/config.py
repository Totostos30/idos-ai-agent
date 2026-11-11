"""Paths, settings and runtime info for the IDOS core."""
from __future__ import annotations

import copy
import json
import os
import pathlib
import tempfile
import threading
from typing import Any, Callable

VERSION = "2.5.6"
IS_WINDOWS = os.name == "nt"


def _root() -> pathlib.Path:
    env = os.environ.get("IDOS_HOME", "").strip()
    if env:
        return pathlib.Path(env).resolve()
    return pathlib.Path(__file__).resolve().parents[1]


ROOT = _root()
CODE_ROOT = pathlib.Path(__file__).resolve().parents[1]  # встроенные навыки живут рядом с кодом, не в IDOS_HOME
DATA = ROOT / "data"
TEMP = ROOT / "Temp"
LOGS = ROOT / "logs"
MODELS = ROOT / "models"
TOOLS_DIR = ROOT / "tools"
SKILLS_DIR = CODE_ROOT / "skills"
RUNTIME = DATA / "runtime"
BACKUPS = TEMP / "backups"
WORKSPACE = DATA / "workspace"
SETTINGS_FILE = DATA / "settings.json"
RUNTIME_FILE = RUNTIME / "core.json"
ENV_FILE = ROOT / ".env"


def ensure_dirs() -> None:
    for d in (DATA, TEMP, LOGS, MODELS, RUNTIME, BACKUPS, WORKSPACE, DATA / "clipboard", DATA / "captures", DATA / "skills", DATA / "notes"):
        d.mkdir(parents=True, exist_ok=True)


DEFAULTS: dict[str, Any] = {
    "language": "ru",
    "user_name": "",
    "scenarios": {"fast_path": True},
    "agent": {
        "autonomy": "full",  # full | danger | all
        "max_steps": 40,
        "max_minutes": 15,
        "tools_share": 0.4,  # share of the input budget for tool descriptions
        "auto_send_voice": True,
        "history_messages": 24,
        "tool_result_chars": 3500,
        "failsafe_corner": True,
        "auto_memory_context": True,
        "narrate_actions": True,
    },
    "models": {
        "active": "groq:openai/gpt-oss-120b",
        # Only models the user chose. Nothing else is added automatically.
        "chain": [
            "groq:openai/gpt-oss-120b",
            # Reserve for gpt-oss-120b (used only when an AnyModel key is added).
            "anymodel:am/nemotron-3-ultra-550b-a55b",
        ],
        "fast": "groq:openai/gpt-oss-120b",
        "force": "",  # "" = Auto (активная + резервные), иначе только эта модель, без переключений
        "vision": "groq:qwen/qwen3.8-27b",
        # Models matching these substrings are used only to read images (Qwen ignores tool-use rules).
        "vision_only": ["qwen", "nano-omni"],
        # Extra image readers, only if the user adds them.
        "vision_chain": ["anymodel:am/nemotron-3-nano-omni-30b-a3b-reasoning"],
        "stt": "groq:whisper-large-v3-turbo",
        "max_output_tokens": 1200,
        # Cap on the context sent per step: Groq's daily token limit (TPD) runs out long before 1000 requests.
        "max_input_tokens": 5000,
        # auto: economy budget for Groq free tier, full budget for others | economy | full
        "context_mode": "auto",
        "max_input_tokens_full": 24000,
        "temperature": 0.3,
        "switch_before": {"requests": 20, "tokens": 1500},
        "max_wait_seconds": 70,
        "params": {
            # gpt-oss on Groq rejects reasoning_format (400) — use include_reasoning instead.
            "groq:openai/gpt-oss-*": {"reasoning_effort": "low", "include_reasoning": False},
            "groq:qwen/*": {"reasoning_effort": "none", "reasoning_format": "hidden"},
            "cerebras:gpt-oss-*": {"reasoning_effort": "low"},
        },
        "custom_providers": {},
        "paid_default": {},
    },
    "voice": {
        "tts_engine": "silero",  # silero | off
        "speak_replies": "always",  # always | voice (only after voice questions) | never
        "silero_model": "v5_ru",
        "silero_speaker": "xenia",  # xenia | baya | kseniya | aidar | eugene
        "sample_rate": 48000,
        "device": "cpu",
        "rate": 1.0,  # speech speed 0.5–2.0 (pitch-preserving)
        "volume": 0.9,
        "stt": "gigastt",  # gigastt | groq
        "stt_url": "ws://127.0.0.1:9876/v1/ws",
        "silence_ms": 1100,
        "max_speech_chars": 700, "lexicon": {},
    },
    "island": {
        "theme": "dark",  # dark | light | system
        "scale": 1.0,
        "opacity_collapsed": 1.0,
        "opacity_open": 0.97,
        "character": "liquid",
        "auto_hide": True,  # если ничего не происходит — уезжает вверх, остаётся полоска
        "auto_hide_delay": 1,  # idle скрывается через 1 секунду; музыка управляется отдельным таймером 3 секунды
        "auto_hide_music": True,  # сохраняется для совместимости; музыка теперь скрывается всегда через 3 секунды
        "peek_delay_ms": 2000,  # сколько держать курсор на полоске до появления островка
        "expand_delay_ms": 700,  # сколько держать курсор на островке, чтобы открылось большое окно
        "hide_for_apps": [],
        "hide_in_fullscreen": True,
        "through_mode": False,
        "show_in_capture": False,
        "monitor": 0,
        "offset_x": 0,
        "music_pill": "wide",  # normal | wide | mini
        "lyrics": True,
        "eq_edge": True,
        "no_activate": True,
        "open_on_hover": True,
        "hover_delay_ms": 280,
        "edge_open": True,
        "show_battery": True,
        "show_weather": True,
        "parts": {},  # части островка: {"clock": false, ...} — скрыть (список ключей — src/parts.js)
        "hidden_tabs": [],  # скрытые вкладки панели: now | tools | clip | agent
        "hidden_tools": [],  # скрытые кнопки на вкладке «Инструменты»
        "sounds": True,
        "waves": {
            "full_width_enabled": True,
            "full_width_duration_s": 3.0,
            "full_width_amplitude": 1.0,
            "full_width_density": 32,
            "full_width_offset_percent": -3,
            "full_width_front_width": 1.8,
            "full_width_pulse_hz": 1.0,
            "full_width_glow": 10,
            "color": "",
            "music_enabled": True,
            "music_gain": 1.0,
            "music_glow": 10,
            "music_peak_count": 32,
            "music_peak_width": 0.3,
            "music_attack": 0.55,
            "music_release": 0.18,
            "ambient_amplitude": 1.0,
            "ambient_speed": 1.0,
            "cursor_enabled": True,
            "cursor_strength": 11,
            "away_sag_enabled": True,
            "away_sag_strength": 7,
            "rain_enabled": True,
            "rain_interval_s": 4.0,
            "rain_drop_size": 1.9,
            "success_drop_size": 3.4,
            "lip_breath_enabled": True,
            "lip_breath_period_s": 2.4,
            "respect_reduced_motion": False,
        },
    },
    "hotkeys": {
        "toggle": "Ctrl+Alt+I",
        "voice": "PageUp",
        "command_bar": "Alt+Space",
        "chat": "Ctrl+Alt+A",
        "dictation": "Ctrl+Alt+D",
        "clipboard": "Ctrl+Alt+V",
        "shot_area": "Ctrl+Alt+S",
        "shot_screen": "Ctrl+Alt+Shift+S",
        "ocr": "Ctrl+Alt+O",
        "qr": "Ctrl+Alt+Q",
        "color": "Ctrl+Alt+C",
        "record": "Ctrl+Alt+R",
        "paste_plain": "Ctrl+Alt+Shift+V",
        "mic_mute": "Ctrl+Alt+M",
        "stop_agent": "Ctrl+Alt+X",
        "through_mode": "",
        "vpn": "",
    },
    "toggle_listens": True,
    "clipboard": {
        "enabled": True,
        "max_items": 400,
        "skip_apps": ["KeePass.exe", "KeePassXC.exe", "1Password.exe", "Bitwarden.exe"],
        "paste_on_click": True,
    },
    "weather": {"enabled": False, "city": "", "lat": None, "lon": None, "rain_warning": True, "provider": "auto",
                "yandex_key": ""},
    "capture": {
        "folder": "",
        "record_folder": "",
        "record_fps": 30,
        "record_quality": "normal",
        "record_format": "mp4",
        "record_mic": False,
        "imgbb_key": "",
    },
    "notifications": {"mirror": False, "skip_apps": []},
    "integrations": {
        "claude_code": False,
        "codex": False,
        "obs": {"enabled": False, "port": 4455, "password": ""},
        "vpn_ip_lookup": True,
        "downloads": True,
    },
    "web": {"search_provider": "auto", "searxng_url": "", "region": "ru-ru", "tavily_key": "", "brave_key": ""},
    "browser": {"channel": "msedge", "cdp_url": "", "headless": False, "executable": "",
                "search_url": "https://duckduckgo.com/?q="},
    "dictation": {"type_into_window": True},
    "audio": {"output_device": "", "input_device": ""},
    "mcp": {"servers": {}},
}


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict) and key not in ("params", "custom_providers", "servers"):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def atomic_write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


class Settings:
    """JSON settings with defaults, dotted access and change listeners."""

    def __init__(self, path: pathlib.Path = SETTINGS_FILE):
        self.path = path
        self._lock = threading.RLock()
        self._listeners: list[Callable[[str, Any], None]] = []
        self._user: dict = {}
        self.data: dict = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self) -> None:
        with self._lock:
            try:
                self._user = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
                if not isinstance(self._user, dict):
                    self._user = {}
            except (OSError, ValueError):
                broken = self.path.with_suffix(".broken.json")
                try:
                    if self.path.exists():
                        os.replace(self.path, broken)
                except OSError:
                    pass
                self._user = {}
            self._migrate()
            self.data = deep_merge(DEFAULTS, self._user)

    def _migrate(self) -> None:
        """v2.2.9: drop the old automatic fallback chain (cerebras/20b/openrouter) — the pair gpt-oss-120b + qwen."""
        models = self._user.get("models")
        if not isinstance(models, dict) or int(models.get("defaults_rev") or 0) >= 5:
            return
        if int(models.get("defaults_rev") or 0) >= 4:
            self._migrate_v5(models)
            return
        if int(models.get("defaults_rev") or 0) < 3:
            models.pop("chain", None)
            if models.get("fast") == "groq:openai/gpt-oss-20b":
                models.pop("fast", None)
        # v2.5.0: the user chose Nemotron 3 Ultra / Nano Omni (AnyModel) as reserves for gpt-oss-120b / qwen.
        for key, spec in (("chain", "anymodel:am/nemotron-3-ultra-550b-a55b"), ("vision_chain", "anymodel:am/nemotron-3-nano-omni-30b-a3b-reasoning"),
                          ("vision_only", "nano-omni")):
            if isinstance(models.get(key), list) and spec not in models[key]:
                models[key].append(spec)
        models["defaults_rev"] = 4
        self._migrate_v5(models)

    def _migrate_v5(self, models: dict) -> None:
        """v2.5.4: gpt-oss on Groq does not accept reasoning_format — drop it from saved params."""
        params = models.get("params")
        if isinstance(params, dict):
            for key, value in params.items():
                if isinstance(value, dict) and "gpt-oss" in key and key.startswith("groq"):
                    if value.pop("reasoning_format", None) is not None:
                        value.setdefault("include_reasoning", False)
        models["defaults_rev"] = 5
        try:
            self.save()
        except OSError:
            pass

    def save(self) -> None:
        with self._lock:
            atomic_write(self.path, json.dumps(self._user, ensure_ascii=False, indent=2))

    def get(self, dotted: str, default: Any = None) -> Any:
        with self._lock:
            node: Any = self.data
            for part in dotted.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return copy.deepcopy(node)

    def set(self, dotted: str, value: Any, save: bool = True) -> None:
        with self._lock:
            parts = dotted.split(".")
            node = self._user
            for part in parts[:-1]:
                if not isinstance(node.get(part), dict):
                    node[part] = {}
                node = node[part]
            node[parts[-1]] = copy.deepcopy(value)
            self.data = deep_merge(DEFAULTS, self._user)
            if save:
                self.save()
        for fn in list(self._listeners):
            try:
                fn(dotted, value)
            except Exception:  # noqa: BLE001 - listeners must not break settings
                pass

    def update(self, values: dict[str, Any]) -> None:
        for key, value in values.items():
            self.set(key, value, save=False)
        self.save()

    def reset(self, dotted: str) -> None:
        with self._lock:
            parts = dotted.split(".")
            node = self._user
            for part in parts[:-1]:
                node = node.get(part, {})
                if not isinstance(node, dict):
                    return
            node.pop(parts[-1], None)
            self.data = deep_merge(DEFAULTS, self._user)
            self.save()
        for fn in list(self._listeners):
            fn(dotted, self.get(dotted))

    def on_change(self, fn: Callable[[str, Any], None]) -> None:
        self._listeners.append(fn)

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self.data)

    def public_snapshot(self) -> dict:
        """Return UI-safe settings without exposing stored credentials."""
        secret_names = ("key", "password", "secret", "token")

        def redact(value: Any) -> Any:
            if isinstance(value, dict):
                out = {}
                for name, item in value.items():
                    if any(part in str(name).lower() for part in secret_names):
                        out[name] = "__IDOS_SECRET_SET__" if item else ""
                    else:
                        out[name] = redact(item)
                return out
            if isinstance(value, list):
                return [redact(item) for item in value]
            return copy.deepcopy(value)

        with self._lock:
            return redact(self.data)


_settings: Settings | None = None


def settings() -> Settings:
    global _settings
    if _settings is None:
        ensure_dirs()
        _settings = Settings()
    return _settings


def write_runtime(info: dict) -> None:
    atomic_write(RUNTIME_FILE, json.dumps(info, ensure_ascii=False, indent=2))


def read_runtime() -> dict:
    try:
        return json.loads(RUNTIME_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def clear_runtime(pid: int) -> None:
    info = read_runtime()
    if info.get("pid") == pid:
        try:
            RUNTIME_FILE.unlink()
        except OSError:
            pass
