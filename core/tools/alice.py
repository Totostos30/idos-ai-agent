"""Алиса (Яндекс): вопрос в чат Alice AI в браузере. Открывает alice.yandex.ru в отдельной вкладке, печатает
запрос, ждёт конец генерации и возвращает последний ответ ассистента. Адрес и селекторы берутся из настроек
(alice.*), поэтому смену вёрстки лечит правка настроек, а не кода: без входа Алиса не отвечает, поэтому вкладка
нужна в любом случае, и availability не прячет инструмент от модели.
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import re
from typing import Any, Callable

from ..util import clip
from . import A, Result, S, Tool, ToolError, get_bool, get_float, need
from .browser import BrowserManager, get_manager, settle, snapshot

log = logging.getLogger("idos.alice")
DEFAULT_URL = "https://alice.yandex.ru/"
DEFAULT_INPUT = ("textarea, [contenteditable='true'], [role='textbox'], input[type='text'], "
                 "input[type='search'], input:not([type])")
DEFAULT_ANSWER = ("[data-testid='answer'], [class*='answer' i], [class*='response' i], "
                  "[class*='Markdown' i], [class*='markdown' i]")
STOP_BUTTON = "[data-testid*='stop' i], [class*='stop-button' i]"
MAX_QUERY = 1500
MAX_ANSWER = 4000
POLL_SECONDS = 1.2
IDLE_POLLS = 3  # столько подряд одинаковых опросов подряд считаем концом ответа
UI_NOISE = {"скопировать", "поделиться", "источники", "нравится", "не нравится", "ещё", "стоп", "остановить"}


def _first(get: Callable[[str], Any], key: str, default: str) -> str:
    value = get(key)
    return str(value).strip() if value not in (None, "") else default


def build_query(app: Any, question: str) -> str:
    """Дописать адрес пользователя (иначе Алиса не знает, где искать) и требование краткости."""
    settings = getattr(app, "settings", None)
    place = str(settings.get("alice.home_address", "") or "").strip() if settings is not None else ""
    question = " ".join(question.split())
    prefix = f"Пользователь живёт по адресу: {place}. " if place else ""
    return prefix + question + " Отвечай максимально кратко и лаконично."


def clean_answer(text: str) -> str:
    """Убрать служебные кнопки под ответом и схлопнуть пустые строки."""
    lines = []
    for raw in (text or "").splitlines():
        line = " ".join(raw.split())
        if line and line.lower() not in UI_NOISE:
            lines.append(line)
    return "\n".join(lines).strip()


def pick_answer(texts: list[str], query: str) -> str:
    """Блок, где повторяется вопрос, — это и есть ответ; иначе самый длинный непустой текст не-эхо."""
    cleaned = [clean_answer(t) for t in texts if isinstance(t, str)]
    cleaned = [t for t in cleaned if len(t) > 2]
    if not cleaned:
        return ""
    head = query[:40].lower()
    for text in cleaned:
        if head and head in text.lower():
            return text
    candidates = [t for t in cleaned if t != query]
    return max(candidates or cleaned, key=len)


async def read_messages(page: Any, selector: str) -> list[str]:
    """Тексты блоков ответа; пусто, если разметка изменилась или вкладку закрыли."""
    out: list[str] = []
    try:
        roots = page.locator(selector)
        for index in range(await roots.count()):
            text = await roots.nth(index).inner_text()
            if text and text.strip():
                out.append(text)
    except Exception as exc:  # noqa: BLE001 - вкладку закрыли или страница ещё грузится
        log.debug("alice: cannot read messages: %s", exc)
    return out


class Alice:
    """Своя вкладка Алисы и пошаговый диалог, чтобы не мешать вкладкам пользователя."""

    def __init__(self, settings: Any, manager: BrowserManager) -> None:
        self.settings = settings
        self.manager = manager
        self.page: Any = None
        self.last_url = ""

    def get(self, key: str, default: Any = None) -> Any:
        value = self.settings.get(key, default) if self.settings is not None else default
        return default if value is None else value

    def input_selector(self) -> str:
        return _first(self.get, "alice.input_selector", DEFAULT_INPUT)

    def answer_selector(self) -> str:
        return _first(self.get, "alice.answer_selector", DEFAULT_ANSWER)

    async def page_for(self) -> Any:
        """Открытая вкладка Алисы: переиспользуем существующую, иначе создаём новую."""
        url = _first(self.get, "alice.url", DEFAULT_URL)
        host = re.sub(r"^https?://", "", url).split("/")[0].lower()
        if self.page is not None and not self.page.is_closed():
            await self.page.bring_to_front()
            return self.page
        for candidate in self.manager.pages():
            try:
                current = (await candidate.evaluate("location.host")) or ""
            except Exception:  # noqa: BLE001 - вкладка закрывается прямо сейчас
                continue
            if host and str(current).lower() == host:
                self.page = candidate
                await candidate.bring_to_front()
                return candidate
        self.page = self.manager.page = await self.manager.context.new_page()
        await self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await settle(self.page)
        return self.page

    async def open(self) -> str:
        await self.manager.ensure()
        return await snapshot(await self.page_for(), 20, 900)

    async def ask(self, question: str, timeout: float = 90.0) -> str:
        await self.manager.ensure()
        page = await self.page_for()
        await self._new_chat(page)
        field = page.locator(self.input_selector()).first
        try:
            await field.wait_for(state="visible", timeout=20000)
        except Exception:  # noqa: BLE001
            raise ToolError("поле ввода Алисы не найдено — откройте вкладку и войдите в аккаунт Яндекса") from None
        try:
            await field.fill(question, timeout=10000)
        except Exception:  # noqa: BLE001 - не обычное поле: пишем посимвольно
            await field.click(timeout=10000)
            await page.keyboard.type(question, delay=8)
        before = len(await read_messages(page, self.answer_selector()))
        await page.keyboard.press("Enter")
        answer = await self._wait_answer(page, question, before, timeout)
        return answer or ""

    async def _new_chat(self, page: Any) -> None:
        """Новый чат при переходе в другую вкладку: иначе ответ ищется среди старых сообщений."""
        try:
            current = await page.evaluate("location.href")
        except Exception:  # noqa: BLE001
            return
        if self.last_url and current == self.last_url:
            return
        if self.last_url:
            await page.goto(_first(self.get, "alice.url", DEFAULT_URL), wait_until="domcontentloaded",
                            timeout=30000)
            await settle(page)
        self.last_url = current


    async def _wait_answer(self, page: Any, query: str, before: int, timeout: float) -> str:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(5.0, timeout)
        last, stable = "", 0
        while loop.time() < deadline:
            if page.is_closed():
                return last
            await asyncio.sleep(POLL_SECONDS)
            texts = await read_messages(page, self.answer_selector())
            answer = pick_answer(texts[before:] or texts, query)
            if answer and answer == last and not await self._generating(page):
                stable += 1
                if stable >= IDLE_POLLS:
                    return answer
            else:
                last, stable = answer, 0
        return last

    async def _generating(self, page: Any) -> bool:
        """Идёт ли ещё генерация: кнопка «Стоп» ещё видна. Не нашли — считаем, что ответ готов."""
        try:
            return await page.locator(STOP_BUTTON).count() > 0
        except Exception:  # noqa: BLE001
            return False


async def handler(action: str, args: dict, ctx: Any) -> Any:
    app = getattr(ctx, "app", None)
    settings = getattr(app, "settings", None)
    manager = get_manager(app)
    state = getattr(app, "alice", None)
    if not isinstance(state, Alice):
        state = Alice(settings, manager)
        if app is not None:
            app.alice = state
    async with manager.lock:
        ctx.check()
        if action == "open":
            return await state.open()
        try:
            question = build_query(app, need(args, "question", "что спросить у Алисы"))[:MAX_QUERY]
            answer = await state.ask(question, get_float(args, "timeout", 90.0) or 90.0)
            head = clip(answer or "", MAX_ANSWER) or "(Алиса не дала ответа)"
            if not get_bool(args, "screenshot"):
                return head
            return Result(head, image=await _preview(state.page))
        finally:
            await manager.close()


async def _preview(page: Any) -> str:
    from .. import vision

    try:
        data = await page.screenshot(type="jpeg", quality=75)
    except Exception:  # noqa: BLE001 - превью не должно ломать ответ
        return ""
    return vision.data_url(vision.thumbnail(data), "image/jpeg")


def _available(settings: Any) -> bool:
    if settings is not None and not settings.get("alice.enabled", True):
        return False
    return importlib.util.find_spec("playwright") is not None


def register(reg: Any, app: Any) -> None:
    settings = getattr(app, "settings", None)
    reg.add(Tool(
        "alice",
        "Алиса (Яндекс, чат Alice AI в браузере пользователя): ask — задать вопрос и вернуть ответ Алисы, "
        "open — открыть вкладку Алисы (войти или посмотреть состояние). Спрашивай, когда пользователь сам "
        "называет «Алису» или «Нейро»; вопрос формулируй кратко и по делу.",
        {"ask": A("Спрашиваю Алису: {question}", "read"), "open": A("Открываю Алису", "read")},
        {"question": S("вопрос для Алисы"), "timeout": S("сколько ждать ответа, секунд (по умолчанию 90)"),
         "screenshot": S("ask: true — вернуть и снимок чата")},
        handler, available=lambda: _available(settings), timeout=200))
