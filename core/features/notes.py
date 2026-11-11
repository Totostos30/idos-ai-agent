"""Notes and reminders: quick notes in the island, reminders from Russian time phrases, repeats and snooze."""
from __future__ import annotations

import asyncio
import datetime
import logging
import re
import time

from .. import bus
from ..tools.island import parse_duration
from . import Feature

log = logging.getLogger("idos.notes")
timedelta = datetime.timedelta

NUM_WORDS = {"одну": 1, "одна": 1, "один": 1, "две": 2, "два": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6,
             "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "пятнадцать": 15, "двадцать": 20,
             "тридцать": 30, "сорок": 40, "пятьдесят": 50}
WEEKDAYS = [("понедельник", 0), ("вторник", 1), ("сред", 2), ("четверг", 3), ("пятниц", 4), ("суббот", 5),
            ("воскресен", 6)]
WEEKDAY_AT = ["в понедельник", "во вторник", "в среду", "в четверг", "в пятницу", "в субботу", "в воскресенье"]
MONTHS = {"янв": 1, "фев": 2, "мар": 3, "апр": 4, "май": 5, "мая": 5, "июн": 6, "июл": 7, "авг": 8, "сен": 9, "окт": 10,
          "ноя": 11, "дек": 12}
PARTS = {"утра": 9, "утром": 9, "дня": 13, "днем": 13, "вечера": 19, "вечером": 19, "ночи": 23, "ночью": 23}
DATE_RE = re.compile(r"(?<![\d:.])(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?(?![\d:.])")
MONTH_RE = re.compile(r"\b(\d{1,2})\s+(янв|фев|мар|апр|ма[йя]|июн|июл|авг|сен|окт|ноя|дек)[а-я]*(?:\s+(\d{4}))?")
CLOCK_RE = re.compile(r"(?<![\d.])(\d{1,2})[:.](\d{2})(?![\d.])")
HOUR_RE = re.compile(r"\b(?:в|к|на)\s+(\d{1,2})(?:\s*час[а-я]*)?(?![\d:.])")
PART_RE = re.compile(r"\b(утра|утром|дня|днем|вечера|вечером|ночи|ночью)\b")
SPAN_RE = re.compile(r"(\d+)?\s*(день|дня|дней|сутки|суток|неделю|недели|недель)")


def _numbers(t: str) -> str:
    t = re.sub(r"\b(" + "|".join(NUM_WORDS) + r")\b", lambda m: str(NUM_WORDS[m.group(1)]), t)
    return re.sub(r"\b([2-5]0) ([1-9])\b", lambda m: str(int(m.group(1)) + int(m.group(2))), t)


def _span(rest: str) -> float | None:
    m = SPAN_RE.fullmatch(rest.strip())
    if m:
        return float(int(m.group(1) or 1) * (604800 if m.group(2).startswith("недел") else 86400))
    return parse_duration(rest)


def _calendar_date(text: str, now: datetime.datetime) -> tuple[datetime.date, bool, int | None, str]:
    day, explicit, weekday = now.date(), False, None
    if "послезавтра" in text:
        day, explicit = day + timedelta(days=2), True
    elif "завтра" in text:
        day, explicit = day + timedelta(days=1), True
    elif "сегодня" in text:
        explicit = True
    for prefix, number in WEEKDAYS:
        if re.search(r"(?:^|\b(?:в|во)\s+)" + prefix, text):
            weekday, explicit = number, True
            break
    for regex in (DATE_RE, MONTH_RE):
        match = regex.search(text)
        if not match or (regex is DATE_RE and text[max(0, match.start() - 2):match.start()] in ("в ", "к ")):
            continue
        month = int(match.group(2)) if regex is DATE_RE else MONTHS[match.group(2)]
        year = int(match.group(3)) if match.group(3) else now.year
        year += 2000 if year < 100 else 0
        try:
            found = datetime.date(year, month, int(match.group(1)))
            if not match.group(3) and found < now.date():
                found = found.replace(year=year + 1)
        except ValueError:
            continue
        day, explicit, weekday = found, True, None
        text = text[:match.start()] + " " + text[match.end():]
        break
    return day, explicit, weekday, text


def parse_when(text: str, now: datetime.datetime | None = None) -> float | None:
    """Russian time phrases → timestamp: «через 20 минут», «завтра в 9», «в пятницу в 18:30», «25.12 в 10:00»,
    «3 декабря», «сегодня вечером», ISO «2026-11-09 10:00». None when nothing matches."""
    now = now or datetime.datetime.now()
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        return datetime.datetime.fromisoformat(raw).timestamp()
    except ValueError:
        pass
    t = _numbers(" ".join(raw.lower().replace("ё", "е").split()))
    m = re.match(r"(?:через|спустя)\s+(.+)", t)
    if m:
        seconds = _span(m.group(1))
        return now.timestamp() + seconds if seconds else None
    day, explicit, weekday, t = _calendar_date(t, now)
    hour = minute = None
    m = CLOCK_RE.search(t)
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        hour, minute = int(m.group(1)), int(m.group(2))
    else:
        m = HOUR_RE.search(t)
        if m and int(m.group(1)) < 24:
            hour, minute = int(m.group(1)), 0
    part = PART_RE.search(t)
    if hour is not None and part:
        word = part.group(1)
        if word in ("дня", "днем", "вечера", "вечером") and hour < 12:
            hour += 12
        elif word in ("утра", "ночи", "ночью") and hour == 12:
            hour = 0
    elif hour is None:
        if "полдень" in t:
            hour, minute = 12, 0
        elif "полночь" in t:
            hour, minute = 0, 0
            if not explicit:
                day += timedelta(days=1)
        elif part:
            hour, minute = PARTS[part.group(1)], 0
        elif explicit:
            hour, minute = 9, 0
        else:
            return None
    if weekday is not None:
        day = now.date() + timedelta(days=(weekday - now.weekday()) % 7)
    moment = datetime.datetime.combine(day, datetime.time(hour, minute))
    if moment <= now:
        if weekday is not None:
            moment += timedelta(days=7)
        elif not explicit:
            moment += timedelta(days=1)
    return moment.timestamp()


def parse_repeat(text: str) -> str:
    t = (text or "").lower().replace("ё", "е")
    if re.search(r"кажд\w*\s+час|ежечасно", t):
        return "hourly"
    if re.search(r"по будням|в будни|кажд\w*\s+будн", t):
        return "weekdays"
    if re.search(r"кажд\w*\s+(?:недел|понедельник|вторник|сред|четверг|пятниц|суббот|воскресен)|еженедельно", t):
        return "weekly"
    if re.search(r"кажд\w*\s+(?:день|утро|вечер)|ежедневно", t):
        return "daily"
    return ""


def next_due(due: float, repeat: str, now: float) -> float | None:
    if repeat == "hourly":
        while due <= now:
            due += 3600
        return due
    step = {"daily": 1, "weekly": 7, "weekdays": 1}.get(repeat)
    if not step:
        return None
    moment = datetime.datetime.fromtimestamp(due)
    while moment.timestamp() <= now or (repeat == "weekdays" and moment.weekday() >= 5):
        moment += timedelta(days=step)
    return moment.timestamp()


def format_due(ts: float, now: datetime.datetime | None = None) -> str:
    now = now or datetime.datetime.now()
    moment = datetime.datetime.fromtimestamp(ts)
    clock = moment.strftime("%H:%M")
    days = (moment.date() - now.date()).days
    if days == 0:
        return f"сегодня в {clock}"
    if days == 1:
        return f"завтра в {clock}"
    if days == 2:
        return f"послезавтра в {clock}"
    if 2 < days < 7:
        return f"{WEEKDAY_AT[moment.weekday()]} в {clock}"
    return f"{moment.strftime('%d.%m.%Y')} в {clock}"


def _note(row: dict) -> dict:
    return {"id": row["id"], "text": row["text"], "done": bool(row["done"]), "pinned": bool(row["pinned"]),
            "created": row["created"], "updated": row["updated"]}


def _reminder(row: dict) -> dict:
    return {"id": row["id"], "text": row["text"], "due": row["due"], "when": format_due(row["due"]),
            "repeat": row["repeat"], "kind": row["kind"], "done": bool(row["done"])}


class Notes(Feature):
    name = "notes"

    # ------------------------------------------------------------------ notes
    def notes(self, include_done: bool = True) -> list[dict]:
        where = "" if include_done else " WHERE done = 0"
        rows = self.db.query("SELECT * FROM notes" + where + " ORDER BY pinned DESC, done ASC, updated DESC")
        return [_note(r) for r in rows]

    def add_note(self, text: str) -> dict:
        text = (text or "").strip()
        if not text:
            raise ValueError("пустая заметка")
        now = time.time()
        note_id = self.db.execute("INSERT INTO notes(text, created, updated) VALUES (?, ?, ?)", (text, now, now))
        self._changed("notes")
        return _note(self.db.one("SELECT * FROM notes WHERE id = ?", (note_id,)))

    def update_note(self, note_id: int, text: str | None = None, done: bool | None = None,
                    pinned: bool | None = None) -> bool:
        fields, params = [], []
        if text is not None:
            if not text.strip():
                raise ValueError("пустая заметка")
            fields.append("text = ?")
            params.append(text.strip())
        for column, value in (("done", done), ("pinned", pinned)):
            if value is not None:
                fields.append(f"{column} = ?")
                params.append(int(bool(value)))
        if not fields:
            return False
        params += [time.time(), int(note_id)]
        ok = self.db.execute(f"UPDATE notes SET {', '.join(fields)}, updated = ? WHERE id = ?", params) > 0
        self._changed("notes")
        return ok

    def delete_note(self, note_id: int) -> bool:
        ok = self.db.execute("DELETE FROM notes WHERE id = ?", (int(note_id),)) > 0
        self._changed("notes")
        return ok

    # ------------------------------------------------------------------ reminders
    def reminders(self, include_done: bool = False) -> list[dict]:
        where = "" if include_done else " WHERE done = 0"
        rows = self.db.query("SELECT * FROM reminders" + where + " ORDER BY done ASC, due ASC")
        return [_reminder(r) for r in rows]

    def add_reminder(self, text: str, when: str = "", due: float | None = None, repeat: str = "") -> dict:
        text = (text or "").strip()
        if not text:
            raise ValueError("о чём напомнить? Текст пустой")
        moment = float(due) if due else parse_when(when or text)
        if moment is None:
            raise ValueError("не понял, когда напомнить — например: «через 20 минут», «завтра в 9», «в пятницу в 18:30»")
        if moment < time.time() - 60:
            raise ValueError(f"это время уже прошло ({format_due(moment)})")
        if repeat not in ("hourly", "daily", "weekly", "weekdays"):
            repeat = parse_repeat(when or text)
        reminder_id = self.db.execute("INSERT INTO reminders(text, due, repeat, created) VALUES (?, ?, ?, ?)",
                                      (text, moment, repeat, time.time()))
        self._changed("reminders")
        return _reminder(self.db.one("SELECT * FROM reminders WHERE id = ?", (reminder_id,)))

    def cancel_reminder(self, reminder_id: int) -> bool:
        ok = self.db.execute("DELETE FROM reminders WHERE id = ?", (int(reminder_id),)) > 0
        self._changed("reminders")
        return ok

    def snooze(self, reminder_id: int, minutes: float = 10) -> bool:
        ok = self.db.execute("UPDATE reminders SET due = ?, done = 0 WHERE id = ?",
                             (time.time() + float(minutes) * 60, int(reminder_id))) > 0
        self._changed("reminders")
        return ok

    async def fire_due(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        rows = self.db.query("SELECT * FROM reminders WHERE done = 0 AND due <= ? ORDER BY due", (now,))
        for row in rows:
            following = next_due(row["due"], row["repeat"], now) if row["repeat"] else None
            if following:
                self.db.execute("UPDATE reminders SET due = ? WHERE id = ?", (following, row["id"]))
            else:
                self.db.execute("UPDATE reminders SET done = 1 WHERE id = ?", (row["id"],))
            bus.card("Напоминание", row["text"], kind="alarm", sticky=True, icon="bell", card_id=f"reminder-{row['id']}",
                     actions=[{"id": "done", "label": "Готово"}, {"id": "snooze", "label": "Через 10 мин"}])
            await self.say(f"Напоминание: {row['text']}")
        if rows:
            self._changed("reminders")
        return len(rows)

    def on_action(self, data) -> None:
        data = data or {}
        card_id = str(data.get("card_id", ""))
        if not card_id.startswith("reminder-"):
            return
        try:
            reminder_id = int(card_id.split("-", 1)[1])
        except ValueError:
            return
        if data.get("action") == "snooze":
            self.snooze(reminder_id, 10)

    async def start(self) -> None:
        self._unsubscribe.append(bus.on("island.action", self.on_action))
        self.spawn(self._loop(), "loop")

    async def _loop(self) -> None:
        while True:
            try:
                await self.fire_due()
            except Exception:  # noqa: BLE001
                log.warning("reminders check failed", exc_info=True)
            await asyncio.sleep(5)

    def _changed(self, what: str) -> None:
        items = self.notes() if what == "notes" else self.reminders()
        bus.emit(f"{what}.changed", {"items": items})

    def methods(self) -> dict:
        async def notes_list(include_done: bool = True) -> list:
            return self.notes(include_done)

        async def notes_add(text: str) -> dict:
            return self.add_note(text)

        async def notes_update(note_id: int, text: str | None = None, done: bool | None = None,
                               pinned: bool | None = None) -> dict:
            return {"ok": self.update_note(note_id, text, done, pinned)}

        async def notes_delete(note_id: int) -> dict:
            return {"ok": self.delete_note(note_id)}

        async def reminders_list(include_done: bool = False) -> list:
            return self.reminders(include_done)

        async def reminders_add(text: str, when: str = "", due: float | None = None, repeat: str = "") -> dict:
            return self.add_reminder(text, when, due, repeat)

        async def reminders_cancel(reminder_id: int) -> dict:
            return {"ok": self.cancel_reminder(reminder_id)}

        async def reminders_snooze(reminder_id: int, minutes: float = 10) -> dict:
            return {"ok": self.snooze(reminder_id, minutes)}

        async def reminders_parse(when: str) -> dict:
            due = parse_when(when)
            return {"due": due, "when": format_due(due) if due else "", "repeat": parse_repeat(when)}

        return {"notes.list": notes_list, "notes.add": notes_add, "notes.update": notes_update,
                "notes.delete": notes_delete, "reminders.list": reminders_list, "reminders.add": reminders_add,
                "reminders.cancel": reminders_cancel, "reminders.snooze": reminders_snooze,
                "reminders.parse": reminders_parse}


def create(app) -> Notes:
    return Notes(app)
