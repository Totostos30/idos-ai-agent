"""Google Calendar on the island: reads the calendar's secret iCal (ICS) URL — no OAuth needed.
Settings → Интеграции → «Адрес iCal (Google Календарь)». Parses upcoming VEVENTs and keeps them
in memory; the island shows the nearest event in the capsule and the agenda in the panel."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
import time
import urllib.request
from zoneinfo import ZoneInfo

from . import Feature

log = logging.getLogger("idos.calendar")
POLL = 900.0  # 15 минут — Календарь не меняется ежесекундно
UA = {"User-Agent": "IDOS/1.0 (island calendar)"}


def _unfold(text: str) -> list[str]:
    out: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and out:
            out[-1] += raw[1:]
        else:
            out.append(raw)
    return out


def _dt_param(line: str) -> tuple[str, str]:
    """'DTSTART;TZID=Europe/Moscow:20250101T183000' → ('Europe/Moscow', '20250101T183000')."""
    body = line.split(":", 1)[1] if ":" in line else ""
    head = line.split(":", 1)[0]
    m = re.search(r"TZID=([^;]+)", head)
    return (m.group(1).strip('"') if m else ""), body.strip()


def _parse_when(tz: str, value: str) -> float | None:
    try:
        if re.fullmatch(r"\d{8}", value):  # весь день
            return dt.datetime.strptime(value, "%Y%m%d").replace(tzinfo=ZoneInfo(tz or "UTC")).timestamp()
        if value.endswith("Z"):
            return dt.datetime.strptime(value[:-1], "%Y%m%dT%H%M%S").replace(
                tzinfo=dt.timezone.utc).timestamp()
        naive = dt.datetime.strptime(value, "%Y%m%dT%H%M%S")
        return naive.replace(tzinfo=ZoneInfo(tz) if tz else dt.timezone.utc).timestamp()
    except Exception:  # noqa: BLE001 - пропустить кривую запись
        return None


def parse_ics(text: str, now: float) -> list[dict]:
    events: list[dict] = []
    cur: dict | None = None
    for line in _unfold(text):
        upper = line.upper()
        if upper.startswith("BEGIN:VEVENT"):
            cur = {}
        elif upper.startswith("END:VEVENT"):
            if cur:
                events.append(cur)
            cur = None
        elif cur is not None:
            if upper.startswith("DTSTART"):
                cur["start"] = _dt_param(line)
            elif upper.startswith("DTEND"):
                cur["end"] = _dt_param(line)
            elif upper.startswith("SUMMARY"):
                cur["title"] = line.split(":", 1)[1].strip()
            elif upper.startswith("STATUS"):
                cur["status"] = line.split(":", 1)[1].strip().upper()
    out: list[dict] = []
    for ev in events:
        if ev.get("status") == "CANCELLED" or "start" not in ev:
            continue
        start = _parse_when(*ev["start"])
        if start is None:
            continue
        end = _parse_when(*ev["end"]) if "end" in ev else start + 3600
        if end < now - 300:  # закончившиеся не показываем
            continue
        out.append({"start": start, "end": end, "title": ev.get("title") or "Без названия"})
    out.sort(key=lambda e: e["start"])
    return out[:12]


def fetch_ics(url: str) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", "replace")


class Calendar(Feature):
    name = "calendar"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.events: list[dict] = []
        self.updated = 0.0
        self.error = ""

    def url(self) -> str:
        return (self.settings.get("integrations.calendar_url", "") or "").strip()

    async def refresh(self) -> None:
        url = self.url()
        if not url:
            return
        try:
            text = await asyncio.to_thread(fetch_ics, url)
            self.events = parse_ics(text, time.time())
            self.updated = time.time()
            self.error = ""
        except Exception as exc:  # noqa: BLE001 - сеть может быть недоступна
            self.error = str(exc)
            log.debug("calendar refresh: %s", exc)
            return
        from .. import bus
        bus.emit("calendar.changed", self.state())

    async def _loop(self) -> None:
        while True:
            if self.url():
                await self.refresh()
            await asyncio.sleep(POLL)

    def state(self) -> dict:
        return {"events": self.events, "updated": self.updated, "error": self.error,
                "configured": bool(self.url())}

    async def start(self) -> None:
        self.spawn(self._loop(), "poll")

    def methods(self) -> dict:
        async def get_state() -> dict:
            return self.state()

        async def refresh() -> dict:
            if not self.url():
                return {"error": "Адрес iCal не задан"}
            await self.refresh()
            return self.state()

        return {"calendar.state": get_state, "calendar.refresh": refresh}


def create(app) -> Calendar:
    return Calendar(app)
