"""Internet without a browser: search, news, reading pages/PDF by URL, deep research via Groq browser_search."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

from .. import config
from ..util import clip
from . import A, I, S, Tool, ToolError, get_int, need
from .document import LIMIT, chunk

log = logging.getLogger("idos.web")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")
MAX_BYTES = 6_000_000
CACHE_SECONDS = 600
DDG_HTML = "https://html.duckduckgo.com/html/"
DEEP_CHAIN = ["groq:openai/gpt-oss-120b", "groq:openai/gpt-oss-20b"]
META_CHARSET = re.compile(rb"""<meta[^>]+charset=["']?([\w-]+)""", re.I)
_pages: dict[str, tuple[float, str]] = {}


def first_line(exc: BaseException) -> str:
    text = re.sub(r"\s+", " ", str(exc).strip().split("\n")[0])
    return clip(text, 300) or type(exc).__name__


class Extractor(HTMLParser):
    """Readability-lite: visible text with headings and lists, preferring <article>/<main>."""

    SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "canvas", "button", "select", "nav",
            "footer", "aside", "object"}
    BLOCK = {"p", "div", "section", "article", "main", "li", "tr", "br", "hr", "pre", "blockquote", "table", "ul",
             "ol", "dd", "dt", "figcaption", "header", "h1", "h2", "h3", "h4", "h5", "h6"}
    HEAD = {"h1": "# ", "h2": "## ", "h3": "### ", "h4": "#### "}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.title = ""
        self.description = ""
        self.parts: list[str] = []
        self.main: list[str] = []
        self._in_title = False
        self._main_depth = 0

    def _emit(self, text: str) -> None:
        self.parts.append(text)
        if self._main_depth:
            self.main.append(text)

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "meta":
            meta = {k: v or "" for k, v in attrs}
            if (meta.get("name") or meta.get("property") or "").lower() in ("description", "og:description"):
                self.description = self.description or meta.get("content", "")
            return
        if tag in self.SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        if tag == "title":
            self._in_title = True
        elif tag in ("article", "main"):
            self._main_depth += 1
        if tag in self.BLOCK:
            self._emit("\n")
        if tag in self.HEAD:
            self._emit(self.HEAD[tag])
        elif tag == "li":
            self._emit("• ")
        elif tag in ("td", "th"):
            self._emit(" | ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag == "title":
            self._in_title = False
        elif tag in ("article", "main"):
            self._main_depth = max(0, self._main_depth - 1)
        if tag in self.BLOCK:
            self._emit("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self.skip:
            self._emit(data)


def clean(text: str) -> str:
    lines = []
    for raw in text.split("\n"):
        line = re.sub(r"[ \t\r\f\v\u00a0\u200b]+", " ", raw).strip()
        line = re.sub(r"^(\|\s*)+", "", line)
        if line in ("•", "#", "##", "###", "####"):
            continue
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def extract(markup: str) -> tuple[str, str]:
    parser = Extractor()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # noqa: BLE001 - html.parser is lenient; a broken tail must not fail the read
        pass
    main = clean("".join(parser.main))
    body = main if len(main) >= 400 else clean("".join(parser.parts))
    description = " ".join(parser.description.split())
    if description and description[:60] not in body:
        body = f"{description}\n\n{body}".strip()
    return " ".join(parser.title.split()), body


def decode(body: bytes, charset: str = "") -> str:
    if not charset:
        match = META_CHARSET.search(body[:4096])
        charset = match.group(1).decode("ascii", "ignore") if match else ""
    for encoding in ([charset] if charset else []) + ["utf-8", "cp1251"]:
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def normalize_url(raw: str) -> str:
    url = raw.strip().strip("<>\"'")
    if re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        if not url.lower().startswith(("http://", "https://")):
            raise ToolError("поддерживаются только http(s)-ссылки; локальные файлы читайте через files")
        return url
    if not url or " " in url or "." not in url:
        raise ToolError("это не похоже на ссылку — используйте search")
    return "https://" + url


def fetch_url(url: str, timeout: float = 25) -> tuple[str, str, bytes, str]:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - the agent reads public pages
            return (resp.geturl(), resp.headers.get_content_type(), resp.read(MAX_BYTES),
                    resp.headers.get_content_charset() or "")
    except urllib.error.HTTPError as exc:
        hint = " — попробуйте browser open" if exc.code in (401, 403, 429, 503) else ""
        raise ToolError(f"сайт ответил {exc.code} {exc.reason}{hint}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ToolError(f"не удалось открыть {url}: {getattr(exc, 'reason', exc)}") from exc


def to_text(final: str, ctype: str, body: bytes, charset: str) -> str:
    if ctype == "application/pdf" or body[:5] == b"%PDF-":
        folder = config.TEMP / "web"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (hashlib.sha1(final.encode("utf-8")).hexdigest()[:12] + ".pdf")
        path.write_bytes(body)
        from .document import convert
        return f"PDF: {final}\n\n{convert(path)}"
    textual = ("application/json", "text/plain", "text/markdown", "text/csv", "text/xml", "application/xml",
               "application/rss+xml", "application/atom+xml")
    if ctype in textual or ctype.endswith("+json"):
        return f"{final}\n\n{decode(body, charset)}"
    if "html" in ctype or body.lstrip()[:1] == b"<":
        title, content = extract(decode(body, charset))
        if len(content) < 200:
            content += "\n\n(текста мало — страница, вероятно, строится скриптами; откройте её через browser open)"
        return "\n".join(p for p in (title, final) if p) + "\n\n" + content
    raise ToolError(f"по ссылке файл ({ctype}), а не страница; скачайте его через browser download")


def read_page(url: str, start: int = 0) -> str:
    url = normalize_url(url)
    now = time.time()
    cached = _pages.get(url)
    if cached and now - cached[0] < CACHE_SECONDS:
        text = cached[1]
    else:
        text = to_text(*fetch_url(url))
        _pages[url] = (now, text)
        while len(_pages) > 24:
            _pages.pop(next(iter(_pages)))
    return chunk(text, start, LIMIT)


# ---------------------------------------------------------------- search providers

def _http_json(url: str, payload: dict | None = None, headers: dict | None = None, timeout: float = 20) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    base = {"User-Agent": UA, "Accept": "application/json"}
    if data is not None:
        base["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers={**base, **(headers or {})},
                                 method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def search_tavily(query: str, limit: int, key: str) -> list[dict]:
    data = _http_json("https://api.tavily.com/search", {"query": query, "max_results": limit, "search_depth": "basic"},
                      {"Authorization": f"Bearer {key}"})
    return [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
            for r in data.get("results") or []]


def search_brave(query: str, limit: int, key: str) -> list[dict]:
    url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode({"q": query, "count": limit})
    data = _http_json(url, headers={"X-Subscription-Token": key})
    return [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": re.sub(r"<[^>]+>", "", r.get("description", ""))}
            for r in (data.get("web") or {}).get("results") or []]


def search_searxng(query: str, limit: int, base: str, news: bool = False) -> list[dict]:
    params = {"q": query, "format": "json", "language": "ru", "categories": "news" if news else "general"}
    data = _http_json(base.rstrip("/") + "/search?" + urllib.parse.urlencode(params))
    return [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", ""),
             "date": r.get("publishedDate") or ""} for r in (data.get("results") or [])[:limit]]


def search_ddgs(query: str, limit: int, region: str, news: bool = False) -> list[dict]:
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # older package name
        except ImportError as exc:
            raise ToolError("пакет ddgs не установлен") from exc
    client = DDGS(timeout=15)
    method = client.news if news else client.text
    try:
        rows = method(query, region=region, max_results=limit)
    except TypeError:
        rows = method(query, max_results=limit)
    return [{"title": r.get("title", ""), "url": r.get("href") or r.get("url", ""), "snippet": r.get("body", ""),
             "date": r.get("date", "") or "", "source": r.get("source", "") or ""} for r in rows or []]


class _DdgParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict] = []
        self._field = ""

    def handle_starttag(self, tag: str, attrs: list) -> None:
        attr = {k: v or "" for k, v in attrs}
        cls = attr.get("class", "")
        if tag == "a" and "result__a" in cls.split():
            self.results.append({"title": "", "url": ddg_target(attr.get("href", "")), "snippet": ""})
            self._field = "title"
        elif "result__snippet" in cls.split() and self.results:
            self._field = "snippet"

    def handle_endtag(self, tag: str) -> None:
        if tag in ("a", "td", "div"):
            self._field = ""

    def handle_data(self, data: str) -> None:
        if self._field and self.results:
            self.results[-1][self._field] += data


def ddg_target(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlparse(href)
    if parsed.path.startswith("/l/"):
        target = urllib.parse.parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return target
    return href


def search_ddg_html(query: str, limit: int, region: str) -> list[dict]:
    url = DDG_HTML + "?" + urllib.parse.urlencode({"q": query, "kl": region})
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9"})
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
        markup = resp.read(MAX_BYTES).decode("utf-8", errors="replace")
    parser = _DdgParser()
    parser.feed(markup)
    rows = [r for r in parser.results if r["url"].startswith("http") and "duckduckgo.com/y.js" not in r["url"]]
    return [{**r, "title": " ".join(r["title"].split()), "snippet": " ".join(r["snippet"].split())}
            for r in rows[:limit]]


def search(settings: Any, query: str, limit: int = 6, news: bool = False) -> tuple[str, list[dict]]:
    def get(key: str, default: Any = "") -> Any:
        value = settings.get(key, default) if settings is not None else default
        return default if value is None else value

    provider = str(get("web.search_provider", "auto") or "auto").lower()
    region = str(get("web.region", "ru-ru") or "ru-ru")
    tavily = str(get("web.tavily_key", "") or os.environ.get("TAVILY_API_KEY", ""))
    brave = str(get("web.brave_key", "") or os.environ.get("BRAVE_API_KEY", ""))
    searx = str(get("web.searxng_url", "") or "")
    order: list[tuple[str, Any]] = []
    if provider in ("tavily", "auto") and tavily and not news:
        order.append(("Tavily", lambda: search_tavily(query, limit, tavily)))
    if provider in ("brave", "auto") and brave and not news:
        order.append(("Brave", lambda: search_brave(query, limit, brave)))
    if provider in ("searxng", "auto") and searx:
        order.append(("SearXNG", lambda: search_searxng(query, limit, searx, news)))
    order.append(("DuckDuckGo", lambda: search_ddgs(query, limit, region, news)))
    if not news:
        order.append(("DuckDuckGo HTML", lambda: search_ddg_html(query, limit, region)))
    errors = []
    for name, run in order:
        try:
            rows = [r for r in run() if r.get("url")]
        except Exception as exc:  # noqa: BLE001 - try the next provider
            errors.append(f"{name}: {first_line(exc)}")
            continue
        if rows:
            return name, rows[:limit]
        errors.append(f"{name}: пусто")
    raise ToolError("поиск не дал результатов (" + "; ".join(errors) + ")")


def format_results(provider: str, rows: list[dict]) -> str:
    lines = []
    for index, row in enumerate(rows, 1):
        meta = " · ".join(p for p in (row.get("source", ""), (row.get("date") or "")[:10]) if p)
        title = clip(" ".join((row.get("title") or row["url"]).split()), 120)
        snippet = clip(" ".join((row.get("snippet") or "").split()), 300)
        lines.append(f"{index}. {title}" + (f" ({meta})" if meta else "") + f"\n   {row['url']}" +
                     (f"\n   {snippet}" if snippet else ""))
    return "\n".join(lines) + f"\n\n(источник: {provider}; подробнее — web fetch по ссылке)"


async def deep(app: Any, question: str, cancel: Any = None) -> str:
    router = getattr(app, "router", None)
    if router is None or not router.has_provider("groq"):
        raise ToolError("для глубокого поиска нужен ключ Groq")
    done = await router.chat(
        [{"role": "system", "content": "Найди ответ в интернете. Отвечай по-русски, по делу; в конце — список ссылок-источников."},
         {"role": "user", "content": question}],
        tools=[{"type": "browser_search"}], tool_choice="required", stream=False, chain=list(DEEP_CHAIN),
        purpose="agent", max_tokens=1500, cancel=cancel)
    return (done.text or "").strip()


async def handler(action: str, args: dict, ctx: Any) -> Any:
    settings = getattr(ctx.app, "settings", None)
    if action in ("search", "news"):
        query = need(args, "query", "что искать")
        limit = max(1, min(10, get_int(args, "limit", 6) or 6))
        provider, rows = await asyncio.to_thread(search, settings, query, limit, action == "news")
        return format_results(provider, rows)
    if action == "fetch":
        return await asyncio.to_thread(read_page, need(args, "url"), max(0, get_int(args, "start", 0) or 0))
    if action == "deep":
        query = need(args, "query", "вопрос")
        try:
            text = await deep(ctx.app, query, ctx.cancel)
            if text:
                return text
        except Exception as exc:  # noqa: BLE001 - fall back to a regular search
            log.info("deep search unavailable: %s", exc)
        ctx.check()
        provider, rows = await asyncio.to_thread(search, settings, query, 6, False)
        return "Глубокий поиск недоступен, обычные результаты:\n" + format_results(provider, rows)
    raise ToolError(f"неизвестное действие {action}")


def register(reg: Any, app: Any) -> None:
    reg.add(Tool(
        "web",
        "Интернет без браузера: search — поиск, news — новости, fetch — прочитать страницу или PDF по url "
        "(start — продолжить), deep — сложный вопрос: модель сама ищет и читает источники (дорого по лимитам). "
        "Если на сайте нужно нажимать или входить — browser.",
        {"search": A("Ищу в интернете: {query}", "read"), "news": A("Ищу новости: {query}", "read"),
         "fetch": A("Читаю {url}", "read"), "deep": A("Исследую: {query}", "read")},
        {"query": S("поисковый запрос"), "url": S("адрес страницы"), "limit": I("сколько результатов, до 10"),
         "start": I("с какого символа читать")},
        handler, timeout=150))
