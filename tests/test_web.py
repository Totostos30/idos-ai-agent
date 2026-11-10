"""Web tool tests: page extraction, encodings, search providers and the deep-search fallback (local servers only)."""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TMP = tempfile.mkdtemp(prefix="idos-web-")
os.environ.setdefault("IDOS_HOME", TMP)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
for _name in ("TAVILY_API_KEY", "BRAVE_API_KEY"):
    os.environ.pop(_name, None)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from core.llm import Completion  # noqa: E402
from core.router import RouterError  # noqa: E402
from core.tools import Context, ToolError  # noqa: E402
from core.tools import web  # noqa: E402

ARTICLE = "<p>" + "Это длинный абзац статьи о погоде в Москве. " * 12 + "</p>"
PAGE = ("<html><head><meta charset='windows-1251'><title>Погода — новости</title>"
        "<meta name='description' content='Краткое описание страницы'></head><body>"
        "<nav>Меню Главная Контакты</nav><article><h2>Прогноз</h2><ul><li>Понедельник: +3</li>"
        "<li>Вторник: дождь</li></ul>" + ARTICLE + "</article><script>var x = 1;</script>"
        "<footer>Подвал</footer></body></html>").encode("cp1251")
DDG = ("<div class='result'><a class='result__a' "
       "href='//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=1'>Пример <b>А</b></a>"
       "<a class='result__snippet' href='#'>Сниппет   первый</a></div>"
       "<div class='result'><a class='result__a' href='https://duckduckgo.com/y.js?ad=1'>Реклама</a></div>"
       "<div class='result'><a class='result__a' href='https://example.org/b'>Второй</a>"
       "<a class='result__snippet' href='#'>Ещё</a></div>")
SERVER = None
BASE = ""
SAVED: dict = {}


class Handler(BaseHTTPRequestHandler):
    ddg_queries: list = []

    def log_message(self, *args):
        pass

    def send(self, status, body, ctype):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(url.query)
        if url.path == "/page.html":
            self.send(200, PAGE, "text/html")
        elif url.path == "/data.json":
            self.send(200, json.dumps({"temp": 3, "city": "Москва"}, ensure_ascii=False),
                      "application/json; charset=utf-8")
        elif url.path == "/tiny.html":
            self.send(200, "<html><body><div id=root></div></body></html>", "text/html; charset=utf-8")
        elif url.path == "/file.zip":
            self.send(200, b"PK\x03\x04", "application/zip")
        elif url.path == "/forbidden":
            self.send(403, "no", "text/plain")
        elif url.path == "/ddg/html/":
            Handler.ddg_queries.append(query)
            self.send(200, DDG, "text/html; charset=utf-8")
        elif url.path == "/searx/search":
            rows = [] if query.get("q", [""])[0] == "пусто" else [
                {"title": "Сайт", "url": "https://site.ru/x", "content": "Описание",
                 "publishedDate": "2026-11-01T10:00:00"}]
            self.send(200, json.dumps({"results": rows}), "application/json")
        else:
            self.send(404, "not found", "text/plain")


class FakeSettings:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


class FakeRouter:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def has_provider(self, provider):
        return provider == "groq"

    async def chat(self, messages, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RouterError("лимит")
        return Completion(text="Ответ с источниками\n1. https://example.com")


class App:
    def __init__(self, router, settings):
        self.router, self.settings = router, settings


def _no_ddgs(*args, **kwargs):
    raise ToolError("пакет ddgs не установлен")


def setUpModule():
    global SERVER, BASE
    SERVER = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=SERVER.serve_forever, daemon=True).start()
    BASE = f"http://127.0.0.1:{SERVER.server_address[1]}"
    SAVED.update(ddg=web.DDG_HTML, ddgs=web.search_ddgs)
    web.DDG_HTML = BASE + "/ddg/html/"
    web.search_ddgs = _no_ddgs


def tearDownModule():
    web.DDG_HTML, web.search_ddgs = SAVED["ddg"], SAVED["ddgs"]
    SERVER.shutdown()


class WebTest(unittest.TestCase):
    def setUp(self):
        Handler.ddg_queries = []
        self.searx = FakeSettings(**{"web.searxng_url": BASE + "/searx"})

    def test_extract_article(self):
        title, body = web.extract(PAGE.decode("cp1251"))
        self.assertEqual(title, "Погода — новости")
        self.assertIn("## Прогноз", body)
        self.assertIn("• Понедельник: +3", body)
        self.assertTrue(body.startswith("Краткое описание страницы"))
        for junk in ("Меню", "var x", "Подвал"):
            self.assertNotIn(junk, body)

    def test_decode_and_urls(self):
        self.assertIn("Текст", web.decode("<meta charset=windows-1251><p>Текст</p>".encode("cp1251")))
        self.assertEqual(web.decode("Привет".encode("cp1251")), "Привет")
        self.assertEqual(web.normalize_url("example.com/путь"), "https://example.com/путь")
        for bad in ("file:///etc/passwd", "просто слова", ""):
            with self.assertRaises(ToolError):
                web.normalize_url(bad)

    def test_read_page_cp1251_json_and_errors(self):
        text = web.read_page(BASE + "/page.html")
        self.assertIn("Погода — новости", text)
        self.assertIn("• Вторник: дождь", text)
        self.assertIn('"city": "Москва"', web.read_page(BASE + "/data.json"))
        self.assertIn("browser open", web.read_page(BASE + "/tiny.html"))
        with self.assertRaises(ToolError) as ctx:
            web.read_page(BASE + "/forbidden")
        self.assertIn("403", str(ctx.exception))
        self.assertIn("browser open", str(ctx.exception))
        with self.assertRaises(ToolError):
            web.read_page(BASE + "/file.zip")

    def test_ddg_html_parser(self):
        rows = web.search_ddg_html("погода", 5, "ru-ru")
        self.assertEqual(rows, [{"title": "Пример А", "url": "https://example.com/a", "snippet": "Сниппет первый"},
                                {"title": "Второй", "url": "https://example.org/b", "snippet": "Ещё"}])
        self.assertEqual(Handler.ddg_queries[0]["kl"], ["ru-ru"])

    def test_search_providers_and_fallback(self):
        provider, rows = web.search(self.searx, "погода", 3)
        self.assertEqual((provider, rows[0]["url"]), ("SearXNG", "https://site.ru/x"))
        provider, rows = web.search(self.searx, "пусто", 3)
        self.assertEqual((provider, len(rows)), ("DuckDuckGo HTML", 2))
        text = web.format_results(provider, rows)
        self.assertIn("1. Пример А", text)
        self.assertIn("(источник: DuckDuckGo HTML", text)
        saved, web.DDG_HTML = web.DDG_HTML, BASE + "/missing/"
        try:
            with self.assertRaises(ToolError) as ctx:
                web.search(self.searx, "пусто", 3)
            self.assertIn("поиск не дал результатов", str(ctx.exception))
        finally:
            web.DDG_HTML = saved

    def test_deep_and_fallback(self):
        router = FakeRouter()
        app = App(router, self.searx)
        text = asyncio.run(web.handler("deep", {"query": "кто выиграл матч"}, Context(app=app)))
        self.assertTrue(text.startswith("Ответ с источниками"))
        call = router.calls[0]
        self.assertEqual(call["tools"], [{"type": "browser_search"}])
        self.assertEqual(call["tool_choice"], "required")
        self.assertEqual(call["chain"], web.DEEP_CHAIN)
        app.router = FakeRouter(fail=True)
        text = asyncio.run(web.handler("deep", {"query": "погода"}, Context(app=app)))
        self.assertTrue(text.startswith("Глубокий поиск недоступен"))
        self.assertIn("https://site.ru/x", text)

    def test_handler_search_and_fetch(self):
        ctx = Context(app=App(FakeRouter(), self.searx))
        self.assertIn("SearXNG", asyncio.run(web.handler("search", {"query": "погода", "limit": 50}, ctx)))
        self.assertIn("Москва", asyncio.run(web.handler("fetch", {"url": BASE + "/data.json"}, ctx)))
        with self.assertRaises(ToolError):
            asyncio.run(web.handler("search", {}, ctx))


if __name__ == "__main__":
    unittest.main()
