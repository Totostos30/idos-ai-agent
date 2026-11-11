"""Agent browser on Playwright: open pages or searches, a numbered snapshot of clickable elements (ref),
click/type by ref or visible label, tabs, scrolling, screenshots for the vision model, downloads.

Modes: browser.cdp_url -> attach to the user's running Edge/Chrome (started with --remote-debugging-port=9222,
keeps every login); otherwise a persistent IDOS profile (logins survive restarts) in system Edge/Chrome,
falling back to Playwright's own Chromium.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import pathlib
import re
import shutil
import urllib.parse
from typing import Any

from .. import config, vision
from ..util import clip, human_bytes
from . import A, B, I, N, Result, S, Tool, ToolError, get_bool, get_float, get_int, get_str, need
from .document import LIMIT, chunk
from .web import extract, first_line

log = logging.getLogger("idos.browser")
PROFILE = config.DATA / "browser-profile"
SNAPSHOT_JS = r'''(max) => {
  const sel = "a[href],button,input:not([type=hidden]),textarea,select,summary,[role=button],[role=link]," +
    "[role=tab],[role=menuitem],[role=checkbox],[role=radio],[role=option],[role=switch],[role=combobox]," +
    "[role=textbox],[contenteditable=''],[contenteditable=true],[onclick]";
  document.querySelectorAll("[data-idos-ref]").forEach(e => e.removeAttribute("data-idos-ref"));
  const found = [];
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2 || el.disabled) continue;
    const st = getComputedStyle(el);
    if (st.visibility === "hidden" || st.display === "none" || st.opacity === "0") continue;
    found.push({el, view: r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth});
    if (found.length >= 600) break;
  }
  found.sort((a, b) => (b.view ? 1 : 0) - (a.view ? 1 : 0));
  const clean = s => (s || "").replace(/\s+/g, " ").trim();
  return found.slice(0, max).map(({el, view}, i) => {
    const tag = el.tagName.toLowerCase();
    const field = tag === "input" || tag === "textarea" || tag === "select";
    let label = el.getAttribute("aria-label") || el.getAttribute("title") || "";
    if (!label && el.labels && el.labels.length) label = el.labels[0].innerText;
    const text = field ? (el.value || "") : (el.innerText || el.getAttribute("alt") || "");
    el.setAttribute("data-idos-ref", String(i + 1));
    return {ref: i + 1, tag, type: (el.getAttribute("type") || "").toLowerCase(), role: el.getAttribute("role") || "",
      label: clean(label).slice(0, 80), text: clean(text).slice(0, 80),
      placeholder: clean(el.getAttribute("placeholder")).slice(0, 60),
      href: tag === "a" ? (el.getAttribute("href") || "").slice(0, 100) : "", checked: !!el.checked, view};
  });
}'''


def downloads_dir() -> pathlib.Path:
    from .files import resolve
    return resolve("downloads")


def find_executable() -> str:
    for name in ("msedge", "chrome", "google-chrome", "chromium", "chromium-browser"):
        path = shutil.which(name)
        if path:
            return path
    if config.IS_WINDOWS:
        for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
            base = os.environ.get(env, "")
            for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
                if base and (pathlib.Path(base) / rel).exists():
                    return str(pathlib.Path(base) / rel)
    return ""


def to_url(target: str, search_url: str) -> str:
    raw = target.strip()
    if re.match(r"^[a-z][a-z0-9+.-]*:", raw, re.I) and not re.match(r"^[\w.-]+:\d", raw):
        return raw
    if re.match(r"^(localhost|\d+\.\d+\.\d+\.\d+)(:\d+)?(/|$)", raw):
        return "http://" + raw
    if " " not in raw and re.search(r"\.[a-zа-я]{2,}(/|:|\?|$)", raw, re.I):
        return "https://" + raw
    return search_url + urllib.parse.quote_plus(raw)


def safe_name(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(". ")[:180] or "download"


def filename_from(headers: dict, url: str) -> str:
    disposition = headers.get("content-disposition", "")
    match = re.search(r"filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)", disposition)
    name = urllib.parse.unquote(match.group(1)) if match else ""
    if not name:
        match = re.search(r'filename\s*=\s*"?([^";]+)"?', disposition)
        name = match.group(1) if match else ""
    if not name:
        name = urllib.parse.unquote(pathlib.PurePosixPath(urllib.parse.urlparse(url).path).name)
    return safe_name(name)


def unique(path: pathlib.Path) -> pathlib.Path:
    if not path.exists():
        return path
    for n in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise ToolError("слишком много файлов с таким именем")


def format_elements(items: list[dict]) -> str:
    lines = []
    for it in items:
        kind = it.get("role") or (f"{it['tag']}[{it['type']}]" if it.get("type") else it["tag"])
        name = it.get("label") or it.get("text") or it.get("placeholder") or it.get("href") or ""
        extra = []
        value = "" if it.get("type") in ("checkbox", "radio") else it.get("text") or ""
        if it["tag"] in ("input", "textarea", "select") and value and value != name:
            extra.append(f"значение «{clip(value, 40)}»")
        if it.get("placeholder") and it["placeholder"] != name:
            extra.append(f"подсказка «{it['placeholder']}»")
        if it.get("type") in ("checkbox", "radio"):
            extra.append("отмечено" if it.get("checked") else "не отмечено")
        if it.get("href") and it["href"] != name and not it["href"].startswith("javascript:"):
            extra.append(it["href"])
        if not it.get("view", True):
            extra.append("вне экрана")
        lines.append(f"[{it['ref']}] {kind} «{clip(name, 80)}»" + (" — " + ", ".join(extra) if extra else ""))
    return "\n".join(lines)


class BrowserManager:
    def __init__(self, settings: Any = None) -> None:
        self.settings = settings
        self.lock = asyncio.Lock()
        self.context: Any = None
        self.page: Any = None
        self.mode = ""
        self._pw: Any = None
        self._browser: Any = None

    def get(self, key: str, default: Any = None) -> Any:
        value = self.settings.get(key, default) if self.settings is not None else default
        return default if value in (None, "") else value

    def pages(self) -> list:
        return [p for p in (self.context.pages if self.context is not None else []) if not p.is_closed()]

    def _alive(self) -> bool:
        return self.context is not None and (self._browser is None or self._browser.is_connected())

    def _on_page(self, page: Any) -> None:
        self.page = page

    def _on_close(self, *_: Any) -> None:
        self.context = self.page = None

    async def ensure(self) -> None:
        if self._alive():
            return
        await self.close()
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise ToolError("браузер недоступен: не установлен пакет playwright") from exc
        self._pw = await async_playwright().start()
        try:
            cdp = str(self.get("browser.cdp_url", ""))
            await (self._connect(cdp) if cdp else self._launch())
        except BaseException:
            await self.close()
            raise
        self.context.on("page", self._on_page)
        self.context.on("close", self._on_close)
        pages = self.pages()
        self.page = pages[-1] if pages else await self.context.new_page()

    async def _connect(self, cdp: str) -> None:
        try:
            self._browser = await self._pw.chromium.connect_over_cdp(cdp, timeout=15000)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"нет связи с браузером {cdp}: {first_line(exc)}. Запустите Edge с ключом "
                            "--remote-debugging-port=9222") from exc
        contexts = self._browser.contexts
        self.context = contexts[0] if contexts else await self._browser.new_context()
        self.mode = "cdp"

    async def _launch(self) -> None:
        PROFILE.mkdir(parents=True, exist_ok=True)
        headless = bool(self.get("browser.headless", False))
        args = ["--disable-blink-features=AutomationControlled", "--no-first-run", "--no-default-browser-check"]
        if not config.IS_WINDOWS:
            args.append("--no-sandbox")
        common: dict[str, Any] = {"user_data_dir": str(PROFILE), "headless": headless, "args": args,
                                  "locale": "ru-RU", "accept_downloads": True}
        if headless:
            common["viewport"] = {"width": 1280, "height": 900}
        else:
            common["no_viewport"] = True
        attempts: list[tuple[str, dict]] = []
        exe = str(self.get("browser.executable", ""))
        if exe:
            attempts.append(("executable", {"executable_path": exe}))
        for name in dict.fromkeys([str(self.get("browser.channel", "")), "msedge", "chrome"]):
            if name:
                attempts.append((name, {"channel": name}))
        found = find_executable()
        if found and found != exe:
            attempts.append(("system", {"executable_path": found}))
        attempts.append(("chromium", {}))
        errors = []
        for label, extra in attempts:
            try:
                self.context = await self._pw.chromium.launch_persistent_context(**common, **extra)
                self.mode = label
                log.info("browser started: %s", label)
                return
            except Exception as exc:  # noqa: BLE001 - try the next browser
                errors.append(f"{label}: {first_line(exc)}")
        raise ToolError("не удалось запустить браузер — " + "; ".join(errors[-3:]))

    async def current(self) -> Any:
        await self.ensure()
        if self.page is None or self.page.is_closed():
            pages = self.pages()
            self.page = pages[-1] if pages else await self.context.new_page()
        return self.page

    async def close(self) -> None:
        context, browser, pw = self.context, self._browser, self._pw
        self.context = self.page = self._browser = self._pw = None
        self.mode = ""
        try:
            if browser is not None:
                await browser.close()  # CDP: only disconnects, the user's browser keeps running
            elif context is not None:
                await context.close()
        except Exception:  # noqa: BLE001
            log.debug("browser close failed", exc_info=True)
        try:
            if pw is not None:
                await pw.stop()
        except Exception:  # noqa: BLE001
            log.debug("playwright stop failed", exc_info=True)


async def page_text(page: Any) -> str:
    try:
        html = await page.content()
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"страница ещё грузится: {first_line(exc)}") from exc
    return extract(html)[1]


async def _title(page: Any) -> str:
    try:
        return await page.title()
    except Exception:  # noqa: BLE001
        return ""


async def snapshot(page: Any, limit: int = 40, text_chars: int = 900) -> str:
    try:
        items = await page.evaluate(SNAPSHOT_JS, max(5, min(150, limit)))
    except Exception as exc:  # noqa: BLE001 - navigation in progress
        log.debug("snapshot failed: %s", exc)
        items = []
    text = clip(await page_text(page), text_chars)
    return (f"{await _title(page) or 'без заголовка'}\n{page.url}\n\nЭлементы (ref для click/type):\n"
            f"{format_elements(items) or '— нет'}\n\nТекст (начало, целиком — read):\n{text or '—'}")


async def settle(page: Any) -> None:
    for state, timeout in (("domcontentloaded", 10000), ("networkidle", 2500)):
        try:
            await page.wait_for_load_state(state, timeout=timeout)
        except Exception:  # noqa: BLE001 - long-polling pages never go idle
            pass


async def locate(page: Any, args: dict, use_text: bool = True) -> Any:
    ref = get_str(args, "ref").strip().strip("[]")
    if ref:
        if not ref.isdigit():
            raise ToolError("ref — это номер из snapshot")
        loc = page.locator(f'[data-idos-ref="{ref}"]')
        if await loc.count():
            return loc.first
        raise ToolError(f"элемента [{ref}] нет — страница изменилась, сделайте snapshot")
    target = get_str(args, "target").strip() or (get_str(args, "text").strip() if use_text else "")
    if not target:
        raise ToolError("укажите ref из snapshot или target — подпись элемента")
    if target.startswith("css="):
        candidates = [page.locator(target[4:])]
    else:
        candidates = [page.get_by_role("button", name=target), page.get_by_role("link", name=target),
                      page.get_by_label(target), page.get_by_placeholder(target), page.get_by_text(target),
                      page.get_by_title(target)]
    for loc in candidates:
        try:
            if await loc.count():
                return loc.first
        except Exception:  # noqa: BLE001 - invalid selector and similar
            continue
    raise ToolError(f"на странице нет элемента «{target}» — посмотрите snapshot")


async def _after(m: BrowserManager, before: int, prefix: str) -> str:
    await asyncio.sleep(0.4)
    pages = m.pages()
    if len(pages) > before:
        m.page = pages[-1]
    page = await m.current()
    await settle(page)
    return prefix + "\n\n" + await snapshot(page, 30, 600)


async def _history(page: Any, action: str) -> str:
    try:
        await (page.go_back if action == "back" else page.go_forward)(wait_until="domcontentloaded", timeout=20000)
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"не получилось: {first_line(exc)}") from exc
    await settle(page)
    return await snapshot(page, 30, 600)


async def run(m: BrowserManager, action: str, args: dict, ctx: Any) -> Any:  # noqa: C901 - one branch per action
    if action == "close":
        await m.close()
        return "браузер закрыт"
    page = await m.current()
    before = len(m.pages())
    if action == "open":
        target = get_str(args, "url").strip() or need(args, "query", "адрес или что искать")
        url = to_url(target, str(m.get("browser.search_url", "https://duckduckgo.com/?q=")))
        if get_bool(args, "new_tab"):
            page = m.page = await m.context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"не удалось открыть {url}: {first_line(exc)}") from exc
        await settle(page)
        return await snapshot(page)
    if action == "snapshot":
        return await snapshot(page, 80, 1500)
    if action == "read":
        return chunk(await page_text(page), max(0, get_int(args, "start", 0) or 0), LIMIT)
    if action == "click":
        loc = await locate(page, args)
        try:
            await loc.click(timeout=15000)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"не удалось нажать: {first_line(exc)}") from exc
        return await _after(m, before, "нажато")
    if action == "type":
        text = get_str(args, "text")
        has_target = get_str(args, "ref").strip() or get_str(args, "target").strip()
        loc = await locate(page, args, use_text=False) if has_target else page.locator(":focus").first
        try:
            await loc.fill(text, timeout=10000)
        except Exception:  # noqa: BLE001 - not a plain field: click and type like a human
            try:
                await loc.click(timeout=10000)
                await page.keyboard.type(text, delay=10)
            except Exception as exc:  # noqa: BLE001
                raise ToolError(f"не удалось ввести текст: {first_line(exc)}") from exc
        if not get_bool(args, "submit"):
            return f"введено: {clip(text, 60)}"
        await page.keyboard.press("Enter")
        return await _after(m, before, "введено и отправлено")
    if action == "press":
        await page.keyboard.press(need(args, "key", "например Enter, Escape, Control+A"))
        return await _after(m, before, "нажато")
    if action == "scroll":
        direction = get_str(args, "direction", "down").lower()
        if direction in ("top", "bottom"):
            await page.evaluate("y => window.scrollTo(0, y)", 0 if direction == "top" else 10 ** 9)
        else:
            height = await page.evaluate("innerHeight") or 800
            sign = -1 if direction in ("up", "вверх") else 1
            await page.mouse.wheel(0, sign * height * 0.85 * (get_float(args, "amount", 1.0) or 1.0))
        await asyncio.sleep(0.4)
        return await snapshot(page, 30, 600)
    if action in ("back", "forward"):
        return await _history(page, action)
    if action == "tabs":
        lines = [f"{i}. {clip(await _title(p), 60) or 'без заголовка'} — {p.url}" + (" ← текущая" if p is page else "")
                 for i, p in enumerate(m.pages(), 1)]
        return "\n".join(lines) or "вкладок нет"
    if action in ("tab", "close_tab"):
        pages, index = m.pages(), get_int(args, "index", 0) or 0
        if action == "tab":
            if not 1 <= index <= len(pages):
                raise ToolError(f"нет вкладки {index}; всего {len(pages)}")
            m.page = pages[index - 1]
            await m.page.bring_to_front()
            return await snapshot(m.page, 30, 600)
        await (pages[index - 1] if 1 <= index <= len(pages) else page).close()
        rest = m.pages()
        m.page = rest[-1] if rest else None
        return "вкладка закрыта" + ("\n\n" + await snapshot(m.page, 20, 400) if rest else "; других вкладок нет")
    if action == "screenshot":
        data = await page.screenshot(type="jpeg", quality=75)
        preview = vision.data_url(vision.thumbnail(data), "image/jpeg")
        if getattr(getattr(ctx, "app", None), "router", None) is None:
            return Result("скриншот сделан (модель зрения недоступна)", image=preview)
        question = get_str(args, "text") or "Что на этой странице? Перечисли важные элементы и текст."
        return Result(await vision.describe(ctx.app, data, question, cancel=ctx.cancel), image=preview)
    if action == "wait":
        target = get_str(args, "target").strip() or get_str(args, "text").strip()
        seconds = max(0.5, min(60.0, get_float(args, "amount", 10.0) or 10.0))
        if not target:
            await asyncio.sleep(min(seconds, 30.0))
            return await snapshot(page, 20, 400)
        loc = page.locator(target[4:]) if target.startswith("css=") else page.get_by_text(target)
        try:
            await loc.first.wait_for(timeout=seconds * 1000)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"не дождался «{target}» за {seconds:g} с") from exc
        return "появилось\n\n" + await snapshot(page, 20, 400)
    if action == "download":
        folder = downloads_dir()
        folder.mkdir(parents=True, exist_ok=True)
        url = get_str(args, "url").strip()
        if url:
            response = await m.context.request.get(to_url(url, ""), timeout=180000)
            if not response.ok:
                raise ToolError(f"сервер ответил {response.status}")
            body = await response.body()
            dest = unique(folder / filename_from(response.headers, response.url))
            await asyncio.to_thread(dest.write_bytes, body)
        else:
            loc = await locate(page, args)
            async with page.expect_download(timeout=180000) as info:
                await loc.click()
            download = await info.value
            dest = unique(folder / safe_name(download.suggested_filename))
            await download.save_as(str(dest))
        return f"скачано: {dest} ({human_bytes(dest.stat().st_size)})"
    if action == "eval":
        value = await page.evaluate(need(args, "text", "JavaScript-выражение"))
        return clip(json.dumps(value, ensure_ascii=False, default=str), 3000)
    raise ToolError(f"неизвестное действие {action}")


_default: BrowserManager | None = None


def get_manager(app: Any) -> BrowserManager:
    global _default
    manager = getattr(app, "browser", None)
    if isinstance(manager, BrowserManager):
        return manager
    manager = BrowserManager(getattr(app, "settings", None))
    if app is not None:
        try:
            app.browser = manager
            return manager
        except AttributeError:
            pass
    _default = _default or manager
    return _default


async def handler(action: str, args: dict, ctx: Any) -> Any:
    manager = get_manager(getattr(ctx, "app", None))
    async with manager.lock:
        ctx.check()
        return await run(manager, action, args, ctx)


def register(reg: Any, app: Any) -> None:
    reg.add(Tool(
        "browser",
        "Браузер агента: сайты со входом, кнопками и формами. open — адрес или поисковый запрос; в ответе "
        "snapshot — элементы с номерами [ref]. click/type — по ref или target (подпись элемента; css=селектор); "
        "type: text, submit — нажать Enter. read — весь текст, screenshot — посмотреть глазами, "
        "tabs/tab/close_tab, back/forward, scroll, wait, download (url или target), eval — JavaScript. "
        "Просто прочитать страницу дешевле через web fetch.",
        {"open": A("Открываю {url}{query}", "read"), "snapshot": A("Смотрю страницу", "read"),
         "read": A("Читаю страницу", "read"), "click": A("Нажимаю {target}{text}{ref}"),
         "type": A("Ввожу «{text}»"), "press": A("Нажимаю клавишу {key}"), "scroll": A("Листаю страницу", "read"),
         "back": A("Назад", "read"), "forward": A("Вперёд", "read"), "tabs": A("Смотрю вкладки", "read"),
         "tab": A("Перехожу на вкладку {index}"), "close_tab": A("Закрываю вкладку"),
         "screenshot": A("Смотрю на страницу", "read"), "wait": A("Жду {target}{text}", "read"),
         "download": A("Скачиваю {url}{target}"), "eval": A("Выполняю скрипт на странице", "danger"),
         "close": A("Закрываю браузер")},
        {"url": S("адрес"), "query": S("что искать (open без url)"), "ref": S("номер элемента из snapshot"),
         "target": S("подпись или текст элемента, либо css=селектор"),
         "text": S("type: текст; screenshot: вопрос; eval: JavaScript"),
         "key": S("клавиша: Enter, Escape, Tab, Control+A…"), "direction": S("scroll: down, up, top, bottom"),
         "amount": N("scroll: сколько экранов; wait: секунд"), "index": I("номер вкладки"),
         "new_tab": B("open в новой вкладке"), "submit": B("type: нажать Enter"),
         "start": I("read: с какого символа")},
        handler, available=lambda: importlib.util.find_spec("playwright") is not None, timeout=200))
