"""Мок-ядро Эйдоса для превью дизайна без живого ядра.

Говорит по тому же протоколу, что и настоящее ядро: WebSocket ws://127.0.0.1:9877/ws,
запросы {id, method, params} → ответы {id, result}, события {event, data}.
Запуск: py -3 tools/mock_core.py [порт]
В браузере: index.html?view=app&port=9877&token=mock
"""
from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import sys
import threading
import time

HOST, PORT = "127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 9877

NOW = time.time()


def cfg() -> dict:
    return {
        "theme": {"preset": "dark", "accent": ""},
        "island": {"parts": {"load": True, "ticker": True, "event": True, "downloads": True, "sleep": True,
                            "w_days": True, "w_agenda": True, "w_sysmon": True, "w_downloads": True,
                            "w_mixer": True, "w_note": True, "w_health": True},
                   "hidden_tabs": [], "hidden_tools": [], "music_pill": "wide",
                   "monitor": 0, "offset_x": 0, "offset_y": 0, "scale": 1,
                   "opacity_collapsed": 1, "opacity_open": 1, "show_in_capture": True,
                   "waves": {"full_width_enabled": True, "full_width_duration_s": 3, "lip_breath_enabled": True}},
        "hotkeys": {"voice": "PageUp", "dictation": "Ctrl+Alt+D", "screenshot": "PrintScreen", "island": "Ctrl+Alt+I"},
        "voice": {"engine": "sapi", "voice_sapi": "Ирина", "rate": 1.0, "stt_url": "ws://127.0.0.1:9876/v1/ws", "lexicon": ""},
        "audio": {"silence_ms": 1300, "input_device": "Микрофон (USB Audio Device)"},
        "agent": {"auto_memory": True, "confirm_tools": "ask", "max_steps": 25},
        "models": {"stt": "groq:whisper-large-v3-turbo", "vision": ""},
        "web": {"engine": "duckduckgo"},
        "capture": {"fps": 30, "quality": 80},
        "weather": {"city": "Казань", "units": "metric"},
        "clipboard": {"max_items": 20},
        "memory": {"auto_save": True},
        "obs": {"host": "localhost", "port": 4455},
        "media": {},
        "security": {"allow_mic": True},
        "browser": {"default": "edge"},
        "alice": {"url": ""},
        "integrations": {},
        "mcp": {},
    }


CFG = cfg()
CHATS = [
    {"id": "c1", "title": "Сводка по заметкам"},
    {"id": "c2", "title": "Скриншот с ошибкой"},
    {"id": "c3", "title": "Курс рубля"},
]
MESSAGES = {
    "c1": [
        {"role": "user", "text": "Собери сводку по моим заметкам за неделю"},
        {"role": "assistant", "text": "Готово. За неделю в памяти 12 записей: 6 про проект IDOS, 3 про план на отпуск, 2 про счета, 1 контакт. Главные темы — редизайн интерфейса и подготовка к отпуску в декабре.\n\nХотите развернуть какую-то тему подробнее?", "meta": {"model": "groq:openai/gpt-oss-120b", "ms": 4200}},
        {"role": "user", "text": "Да, про IDOS"},
        {"role": "assistant", "text": "Записи: 1) реorganize настроек — готово; 2) греческий слой — в работе; 3) голосовой ввод — тест на USB-микрофоне. Ближайший шаг — merge ветки редизайна.", "meta": {"model": "groq:openai/gpt-oss-120b", "ms": 3100}},
    ],
    "c2": [
        {"role": "user", "text": "[скриншот] Что за ошибка?"},
        {"role": "assistant", "text": "Это traceback Python: access violation в PortAudio. Советую общий бэкенд под блокировкой — у вас он уже стоит в core/audio_backend.py.", "meta": {"model": "groq:openai/gpt-oss-120b", "ms": 5200}},
    ],
    "c3": [
        {"role": "user", "text": "Курс рубля к доллару сегодня"},
        {"role": "assistant", "text": "Сегодня 92,4 ₽ за $. За неделю рубль укрепился на 0,8%.", "meta": {"model": "groq:openai/gpt-oss-120b", "ms": 2800}},
    ],
}
MEM = [
    {"id": "m1", "text": "Пользователь живёт в Казани", "kind": "факт", "tags": ["город"], "created": NOW - 86400 * 2},
    {"id": "m2", "text": "Проект IDOS: редизайн интерфейса на ветке re-design-ui", "kind": "проект", "tags": ["idos"], "created": NOW - 86400},
    {"id": "m3", "text": "Любит музыку во время работы — остров с плеером", "kind": "предпочтение", "tags": ["музыка"], "created": NOW - 3600 * 5},
    {"id": "m4", "text": "Голосовой ввод тестируется на USB-микрофоне", "kind": "проект", "tags": ["голос"], "created": NOW - 7200},
]
TOOLS = [
    {"name": "browser", "description": "Браузер Эйдоса: открывает сайты, читает страницы, ищет элементы", "available": True,
     "actions": {"open": {"kind": "act", "label": "Открыть URL"}, "read": {"kind": "act", "label": "Читать"}, "click": {"kind": "write", "label": "Клик"}}},
    {"name": "computer", "description": "Мышь, клавиатура, снимок и распознавание экрана", "available": True,
     "actions": {"screenshot": {"kind": "act"}, "click": {"kind": "write"}, "type": {"kind": "write"}}},
    {"name": "weather", "description": "Погода по городу", "available": True, "actions": {"now": {"kind": "act"}}},
    {"name": "alice", "description": "Алиса (Яндекс): спросить через браузер", "available": True, "actions": {"ask": {"kind": "act"}}},
    {"name": "files", "description": "Файлы рабочей папки: чтение и запись", "available": True,
     "actions": {"read": {"kind": "act"}, "write": {"kind": "write"}}},
    {"name": "notes", "description": "Заметки и напоминания", "available": False, "actions": {}},
]
SKILLS = [
    {"name": "site-status", "description": "Проверяет доступность сайтов и присылает сводку"},
    {"name": "morning-brief", "description": "Утренний брифинг: погода, курс, события календаря"},
]
# Ключи как в живом ядре: у первого лимиты известны, у второго — только у части моделей
# (воспроизводит случай «? / ?» для проверки компактной строки без полосы).
ACCOUNTS = [
    {"id": "a1", "provider": "groq", "masked": "gsk_...qOnp", "label": "Groq · ключ 1", "spec": "groq",
     "enabled": True, "disabled": False, "current": True, "source": "local",
     "buckets": {
         "openai/gpt-oss-120b": {"limit_requests": 1000, "remaining_requests": 996, "used_today": 4},
         "whisper-large-v3-turbo": {"limit_requests": 2000, "remaining_requests": 1999, "used_today": 1},
         "qwen/qwen3.8-27b": {"limit_requests": 1000, "remaining_requests": 999, "used_today": 1}}},
    {"id": "a2", "provider": "groq", "masked": "gsk_...cCA0", "label": "Groq · ключ 2", "spec": "groq",
     "enabled": True, "disabled": False, "current": False, "source": "local",
     "buckets": {
         "openai/gpt-oss-120b": {"limit_requests": 1000, "remaining_requests": 999, "used_today": 1},
         "whisper-large-v3-turbo": {"used_today": 0},
         "qwen/qwen3.8-27b": {"used_today": 0}}},
    {"id": "a3", "provider": "groq", "masked": "gsk_...xVMQ", "label": "Groq · ключ 3", "spec": "groq",
     "enabled": True, "disabled": False, "current": False, "source": "local",
     "buckets": {
         "openai/gpt-oss-120b": {"limit_requests": 1000, "remaining_requests": 999, "used_today": 1},
         "whisper-large-v3-turbo": {"used_today": 0},
         "qwen/qwen3.8-27b": {"used_today": 0}}},
]

STATE = {"mic": False, "cam": False, "volume": 0.4, "muted": False,
         "battery": {"percent": 76, "charging": False, "saver": False},
         "vpn": {"on": True, "name": "WireGuard"}}
MEDIA = {"playing": False, "title": "Kiasmos — Blurred", "artist": "Kiasmos", "cover": "", "position": 42, "duration": 214,
         "can_prev": True, "can_next": True, "accent": ""}
TIMERS = []
# Погода: по умолчанию город не задан (как в живом ядре с включённым островом) —
# остров показывает чип «город?» и виджет-заглушку; weather.set_city «настраивает» её.
WEATHER = {"ok": False, "reason": "укажите город в настройках погоды"}
# после weather.set_city появляется и 5-дневный прогноз (days) — как в живом ядре
WEATHER_SET = {"ok": True, "temp": 9, "feels": 7, "wind": 4.2, "humidity": 82, "kind": "rain", "night": False,
               "text": "небольшой дождь", "city": "Казань", "icon": "🌧️",
               "days": [{"date": "2026-11-14", "min": 5, "max": 9, "kind": "rain", "symbol": "rain", "text": "дождь"},
                        {"date": "2026-11-15", "min": 3, "max": 7, "kind": "partly", "symbol": "partlycloudy_day", "text": "переменная облачность"},
                        {"date": "2026-11-16", "min": 2, "max": 6, "kind": "cloudy", "symbol": "cloudy", "text": "облачно"},
                        {"date": "2026-11-17", "min": 0, "max": 4, "kind": "snow", "symbol": "snow", "text": "снег"},
                        {"date": "2026-11-18", "min": -1, "max": 3, "kind": "partly", "symbol": "partlycloudy_day", "text": "переменная облачность"}]}
WEATHER.update(WEATHER_SET)
# Буфер обмена: пара записей с полными текстами для проверки кнопки «Копировать».
CLIP = [
    {"id": "c1", "kind": "text", "preview": "Пароль от Wi-Fi: otdelnyi-dom-5G", "length": 62, "pinned": True},
    {"id": "c2", "kind": "text", "preview": "Казань, ул. Баумана, 44, офис 307", "length": 45, "pinned": False},
]
CLIP_TEXT = {
    "c1": "Пароль от Wi-Fi: otdelnyi-dom-5G (гостевая сеть: dom-gost-2G, пароль тот же)",
    "c2": "Казань, ул. Баумана, 44, офис 307 — вход со двора, третий этаж, домофон 44К",
}

# Новые источники островка: монитор, календарь, загрузки, курс, микшер, заметки.
SYSMON = {"cpu": 37, "ram": 62, "disk": 8, "updated": 0.0}
CALENDAR = {"configured": True, "events": [
    {"start": NOW + 1500, "end": NOW + 3300, "title": "Планёрка"},
    {"start": NOW + 12600, "end": NOW + 16200, "title": "Спортзал"}], "updated": NOW, "error": ""}
DOWNLOADS = {"active": [
    {"name": "update.zip", "size": 734003200, "total": 1073741824, "progress": 0.683, "speed": 5242880, "eta": 103, "updated": NOW},
    {"name": "design.fig", "size": 15728640, "total": 0, "progress": None, "speed": 262144, "eta": None, "updated": NOW}],
    "recent": []}
TICKER = {"rates": {"usd": {"value": 92.4, "prev": 92.1, "diff": 0.3}, "eur": {"value": 99.8, "prev": 100.1, "diff": -0.3}},
          "updated": NOW, "error": ""}
MIXER = {"master": 62, "apps": [
    {"pid": 4012, "name": "Yandex.Music.exe", "volume": 40, "mute": False},
    {"pid": 9184, "name": "Discord.exe", "volume": 85, "mute": False},
    {"pid": 7300, "name": "chrome.exe", "volume": 55, "mute": True}]}
NOTES = [{"id": 1, "text": "идея: добавить тёмный смузи-режим", "done": False, "pinned": False, "created": NOW, "updated": NOW}]


def handle(method: str, params: dict):
    if method == "session.hello":
        return {}
    if method == "settings.get":
        return CFG
    if method == "settings.set" or method == "settings.update":
        payload = params.get("values") if method == "settings.update" else {params.get("key"): params.get("value")}
        for path, value in (payload or {}).items():
            parts = path.split(".")
            node = CFG
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value
        return {"ok": True}
    if method == "settings.reset":
        return {"ok": True}
    if method == "agent.chats":
        return CHATS
    if method == "agent.messages":
        return MESSAGES.get(params.get("chat_id"), [])
    if method == "agent.new_chat":
        CHATS.insert(0, {"id": "c%d" % (len(CHATS) + 1), "title": "Новый диалог"})
        return {"chat_id": CHATS[0]["id"]}
    if method == "agent.select_chat" or method == "agent.rename_chat" or method == "agent.delete_chat":
        return {"ok": True}
    if method == "memory.list" or method == "memory.search":
        q = (params.get("query") or "").lower()
        items = [m for m in MEM if not q or q in m["text"].lower()] if q else MEM
        return {"items": items[: params.get("limit", 50)]}
    if method == "memory.save":
        MEM.insert(0, {"id": "m%d" % (len(MEM) + 10), "text": params.get("text", ""), "kind": "факт", "created": NOW})
        return {"ok": True}
    if method == "memory.delete":
        return {"ok": True}
    if method == "tools.list":
        return TOOLS
    if method == "skills.list":
        return SKILLS
    if method == "skills.delete":
        return {"ok": True}
    if method == "mcp.servers":
        return [{"name": "filesystem", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem"], "running": True}]
    if method == "router.status":
        return {"active": "groq:openai/gpt-oss-120b", "chain": ["groq:llama-3.3-70b", "openrouter:anthropic/claude-3.5-haiku"],
                "vision": "groq:meta-llama/llama-4-scout", "vision_chain": [],
                "providers": {"groq": {"name": "Groq", "note": "быстрые ответы, щедрые лимиты"},
                              "openrouter": {"name": "OpenRouter", "note": "много моделей, платно"}},
                "accounts": ACCOUNTS}
    if method == "router.choices":
        return {"active": "groq:openai/gpt-oss-120b", "force": "",
                "choices": [
                    {"spec": "groq:openai/gpt-oss-120b", "model": "openai/gpt-oss-120b", "provider": "groq", "ready": True},
                    {"spec": "groq:openai/gpt-oss-20b", "model": "openai/gpt-oss-20b", "provider": "groq", "ready": True},
                    {"spec": "groq:qwen/qwen3.8-27b", "model": "qwen/qwen3.8-27b", "provider": "groq", "ready": True},
                    {"spec": "openrouter:anthropic/claude-3.5-haiku", "model": "anthropic/claude-3.5-haiku", "provider": "openrouter", "ready": False}]}
    if method == "keys.list":
        return ACCOUNTS
    if method == "keys.add_many" or method == "keys.add" or method == "keys.enable" or method == "keys.remove" or method == "router.set_active" or method == "router.force":
        return {"ok": True}
    if method == "router.test" or method == "router.selftest" or method == "core.selftest":
        return {"active": "groq:openai/gpt-oss-120b", "chain": ["groq:llama-3.3-70b"], "vision": "groq:meta-llama/llama-4-scout",
                "vision_chain": [], "ok": True, "message": "groq:openai/gpt-oss-120b · 0.9 с",
                "checks": [{"ok": True, "name": "основная модель", "ms": 900, "detail": "ответ 42 токена"}]}
    if method == "voice.voices":
        return {"engines": [
            {"engine": "sapi", "title": "SAPI Windows", "note": "локальные голоса", "available": True,
             "voices": [{"id": "Irina", "name": "Ирина", "gender": "ж", "note": "по умолчанию"}, {"id": "Pavel", "name": "Павел", "gender": "м", "note": ""}]},
            {"engine": "edge", "title": "Edge TTS", "note": "онлайн-голоса", "available": True,
             "voices": [{"id": "svetlana", "name": "Светлана", "gender": "ж", "note": "живая интонация"}]}],
            "current": {"engine_sapi": "Irina", "engine_edge": "", "rate": 1.0}}
    if method == "voice.apply" or method == "voice.install":
        return {"ok": True}
    if method == "themes.list":
        return []
    if method == "themes.save":
        return {"slug": "mock", "saved": True}
    if method == "themes.delete":
        return {"deleted": True}
    if method == "scenarios.list":
        return [{"id": "sc1", "name": "Утренний брифинг", "desc": "Погода и курс по утрам", "triggers": ["08:00"], "vars": []},
                {"id": "sc2", "name": "Разбор скриншота", "desc": "Что на экране и почему", "triggers": [], "vars": []}]
    if method == "scenarios.get":
        return {"id": params.get("id", "sc1"), "name": "Утренний брифинг", "triggers": ["08:00"], "desc": "Погода и курс по утрам",
                "nodes": [{"id": "n1", "type": "start", "x": 60, "y": 60, "params": {}, "links": {"next": "n2"}},
                          {"id": "n2", "type": "launch", "x": 60, "y": 180, "params": {"app": "Яндекс Музыка", "wait": "3"}, "links": {"next": "n3"}},
                          {"id": "n3", "type": "end", "x": 60, "y": 300, "params": {}, "links": {}}], "vars": []}
    if method == "scenarios.save" or method == "scenarios.delete" or method == "scenarios.run" or method == "scenarios.stop" or method == "scenarios.answer" or method == "scenarios.question" or method == "scenarios.pick" or method == "scenarios.probe":
        return {"ok": True}
    if method == "scenarios.blocks":
        def block(title, outs=("next",), params=None):
            return {"title": title, "outs": list(outs), "params": params or {}}
        return {"blocks": {
            "start": block("Старт"), "end": block("Конец"),
            "launch": block("Запустить программу", params={"app": "", "wait": ""}),
            "window": block("Окно", params={"title": ""}),
            "focus": block("Фокус окна", params={"title": ""}),
            "click": block("Клик", params={"targets": []}),
            "type": block("Ввести текст", params={"text": ""}),
            "read": block("Прочитать", params={}),
            "ask": block("Спросить", params={"text": ""}),
            "say": block("Сказать", params={"text": ""}),
            "condition": block("Условие", outs=("yes", "no"), params={"expr": ""}),
            "if_exists": block("Если существует", outs=("yes", "no"), params={"target": ""}),
            "repeat": block("Повторить", outs=("next", "break"), params={"count": ""}),
            "hotkey": block("Горячая клавиша", params={"keys": ""}),
            "wait": block("Подождать", params={"seconds": ""}),
            "wait_for": block("Дождаться", params={"target": ""}),
            "media": block("Медиа", params={}),
            "open": block("Открыть", params={"url": ""}),
            "scenario": block("Сценарий", params={"id": ""}),
        }, "outs": {"next": "далее", "yes": "да", "no": "нет", "break": "прервать"}}
    # остров
    if method == "status.state":
        return STATE
    if method == "status.volume" or method == "status.mic_mute":
        return {"ok": True}
    if method == "island.timers":
        return TIMERS
    if method == "island.action" or method == "island.cancel_timer":
        return {"ok": True}
    if method == "media.state":
        return MEDIA
    if method == "media.control":
        MEDIA["playing"] = not MEDIA.get("playing", False)
        MEDIA["active"] = True
        return {"ok": True, "playing": MEDIA["playing"]}
    if method == "weather.state":
        return WEATHER
    if method == "weather.set_city":
        WEATHER.clear()
        WEATHER.clear()
        WEATHER.update(WEATHER_SET)
        WEATHER["city"] = (params or {}).get("city") or "Казань"
        return dict(WEATHER)
    if method == "sysmon.state":
        return SYSMON
    if method == "calendar.state":
        return CALENDAR
    if method == "calendar.refresh":
        return CALENDAR
    if method == "downloads.state":
        return DOWNLOADS
    if method == "ticker.state":
        return TICKER
    if method == "mixer.state":
        return MIXER
    if method == "mixer.set":
        return {"ok": True}
    if method == "system.power":
        return {"ok": True, "text": "перевожу компьютер в сон"}
    if method == "notes.list":
        return NOTES
    if method == "notes.add":
        NOTES.insert(0, {"id": len(NOTES) + 1, "text": params.get("text", ""), "done": False, "pinned": False,
                         "created": time.time(), "updated": time.time()})
        return NOTES[0]
    if method == "clipboard.list":
        return {"items": CLIP}
    if method == "clipboard.get":
        cid = (params or {}).get("item_id")
        return {"text": CLIP_TEXT.get(cid, "")}
    if method == "clipboard.paste" or method == "clipboard.paste_plain" or method == "clipboard.pin" or method == "clipboard.delete":
        return {"ok": True}
    if method == "recorder.state":
        return {"recording": False}
    if method == "recorder.stop" or method == "recorder.toggle":
        return {"ok": True}
    if method == "commandbar.query":
        return [{"icon": "app", "title": "Блокнот", "subtitle": "запуск"},
                {"icon": "app", "title": "Калькулятор", "subtitle": "запуск"}]
    if method == "commandbar.run" or method == "capture.color" or method == "capture.screenshot":
        return {"ok": True}
    if method == "obs.record" or method == "obs.stream" or method == "obs.set_scene":
        return {"ok": True}
    if method == "voice.stop" or method == "agent.ask" or method == "agent.stop" or method == "agent.confirm":
        return {"ok": True}
    return {"ok": True}


class WSServer:
    def __init__(self, host: str, port: int):
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind((host, port))
        self.srv.listen(8)
        self.clients: list[socket.socket] = []

    def accept(self) -> socket.socket | None:
        client, _ = self.srv.accept()
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = client.recv(4096)
            if not chunk:
                client.close()
                return None
            data += chunk
        key = ""
        for line in data.decode("latin-1").split("\r\n"):
            if line.lower().startswith("sec-websocket-key:"):
                key = line.split(":", 1)[1].strip()
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        client.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
        self.clients.append(client)
        print("client connected")
        return client

    @staticmethod
    def send(sock: socket.socket, text: str, opcode: int = 1) -> None:
        payload = text.encode("utf-8")
        header = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header.append(n)
        elif n < 65536:
            header.append(126)
            header += struct.pack(">H", n)
        else:
            header.append(127)
            header += struct.pack(">Q", n)
        sock.sendall(bytes(header) + payload)

    @staticmethod
    def recv_exact(sock: socket.socket, n: int) -> bytes | None:
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    @classmethod
    def recv(cls, sock: socket.socket) -> str | None:
        header = cls.recv_exact(sock, 2)
        if not header:
            return None
        opcode = header[0] & 0x0F
        masked = header[1] & 0x80
        n = header[1] & 0x7F
        if n == 126:
            ext = cls.recv_exact(sock, 2)
            if ext is None:
                return None
            n = struct.unpack(">H", ext)[0]
        elif n == 127:
            ext = cls.recv_exact(sock, 8)
            if ext is None:
                return None
            n = struct.unpack(">Q", ext)[0]
        mask = cls.recv_exact(sock, 4) if masked else b"\x00\x00\x00\x00"
        if mask is None:
            return None
        payload = cls.recv_exact(sock, n) if n else b""
        if payload is None:
            return None
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if opcode == 8:
            return None
        if opcode == 9:  # ping → настоящий pong тем же телом
            cls.send(sock, "", opcode=10)
            return ""
        return payload.decode("utf-8")

    def drop(self, sock: socket.socket) -> None:
        if sock in self.clients:
            self.clients.remove(sock)
        try:
            sock.close()
        except OSError:
            pass


import threading  # noqa: E402

server = WSServer(HOST, PORT)
lock = threading.Lock()
print(f"мок-ядро Эйдоса: ws://{HOST}:{PORT}/ws — жду клиентов")


def serve_client(sock: socket.socket) -> None:
    while True:
        try:
            text = server.recv(sock)
        except OSError:
            break
        if text is None:
            break
        if not text:
            continue
        try:
            msg = json.loads(text)
            result = handle(str(msg.get("method")), msg.get("params") or {})
            with lock:
                server.send(sock, json.dumps({"id": msg.get("id"), "result": result}, ensure_ascii=False))
        except Exception as exc:
            import sys as _sys
            print("ошибка обработки", msg.get("method") if isinstance(msg, dict) else "?", repr(exc), file=_sys.stderr, flush=True)
    server.drop(sock)


try:
    while True:
        client = server.accept()
        if client is not None:
            threading.Thread(target=serve_client, args=(client,), daemon=True).start()
except KeyboardInterrupt:
    pass
