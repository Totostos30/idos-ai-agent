"""Browser tool: helper unit tests plus a full flow in headless Chromium against local pages
(the flow is skipped when Playwright or a Chromium binary is missing)."""
from __future__ import annotations

import asyncio
import importlib.util
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TMP = pathlib.Path(tempfile.mkdtemp(prefix="idos-browser-"))
os.environ.setdefault("IDOS_HOME", str(TMP))
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from core.tools import Context, Result, ToolError  # noqa: E402
from core.tools import browser  # noqa: E402

CHROME = shutil.which("chromium") or shutil.which("google-chrome") or shutil.which("chrome") or ""
HAVE = importlib.util.find_spec("playwright") is not None and bool(CHROME)
HOME = ("<html><head><title>Главная</title></head><body><h1>Тест</h1><a href='/form'>Форма</a> "
        "<a href='/new' target='_blank'>Новая вкладка</a> "
        "<button onclick=\"document.getElementById('out').textContent='кнопка сработала'\">Нажми меня</button>"
        "<div id='out'></div><a href='/file.txt' download>Скачать файл</a><p>" + "Длинный текст. " * 40 +
        "</p></body></html>")
PAGES = {
    "/": HOME,
    "/form": ("<html><head><title>Форма</title></head><body><form action='/result'><label for='q'>Запрос</label>"
              "<input id='q' name='q' placeholder='Что ищем'><button type='submit'>Найти</button></form></body></html>"),
    "/new": "<html><head><title>Вторая</title></head><body><p>Вторая вкладка</p></body></html>",
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        headers = {"Content-Type": "text/html; charset=utf-8"}
        if url.path == "/result":
            query = urllib.parse.parse_qs(url.query).get("q", [""])[0]
            body = f"<html><head><title>Результат</title></head><body><p>Результат: {query}</p></body></html>"
        elif url.path == "/file.txt":
            body = "содержимое файла"
            headers = {"Content-Type": "text/plain; charset=utf-8",
                       "Content-Disposition": "attachment; filename*=UTF-8''" + urllib.parse.quote("отчёт.txt")}
        elif url.path in PAGES:
            body = PAGES[url.path]
        else:
            self.send_error(404)
            return
        data = body.encode("utf-8")
        self.send_response(200)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Settings:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


class App:
    def __init__(self, settings):
        self.settings = settings


class HelpersTest(unittest.TestCase):
    def test_to_url(self):
        search = "https://duckduckgo.com/?q="
        self.assertEqual(browser.to_url("example.com", search), "https://example.com")
        self.assertEqual(browser.to_url("ya.ru/search?x=1", search), "https://ya.ru/search?x=1")
        self.assertEqual(browser.to_url("localhost:8000/x", search), "http://localhost:8000/x")
        self.assertEqual(browser.to_url("https://a.b/c", search), "https://a.b/c")
        self.assertEqual(browser.to_url("погода в москве", search), search + urllib.parse.quote_plus("погода в москве"))
        self.assertEqual(browser.to_url("котики", search), search + urllib.parse.quote_plus("котики"))

    def test_names_and_format(self):
        disposition = {"content-disposition": "attachment; filename*=UTF-8''" + urllib.parse.quote("отчёт.txt")}
        self.assertEqual(browser.filename_from(disposition, "http://x/y"), "отчёт.txt")
        self.assertEqual(browser.filename_from({}, "http://x/files/a%20b.pdf?x=1"), "a b.pdf")
        self.assertEqual(browser.filename_from({"content-disposition": 'attachment; filename="r.csv"'}, "http://x/"), "r.csv")
        self.assertEqual(browser.safe_name("a:b/c?.txt"), "a_b_c_.txt")
        items = [{"ref": 1, "tag": "input", "type": "text", "role": "", "label": "Запрос", "text": "",
                  "placeholder": "Что ищем", "href": "", "checked": False, "view": True},
                 {"ref": 2, "tag": "a", "type": "", "role": "", "label": "", "text": "Форма", "placeholder": "",
                  "href": "/form", "checked": False, "view": False},
                 {"ref": 3, "tag": "input", "type": "checkbox", "role": "", "label": "Запомнить", "text": "on",
                  "placeholder": "", "href": "", "checked": True, "view": True}]
        lines = browser.format_elements(items).splitlines()
        self.assertEqual(lines[0], "[1] input[text] «Запрос» — подсказка «Что ищем»")
        self.assertEqual(lines[1], "[2] a «Форма» — /form, вне экрана")
        self.assertEqual(lines[2], "[3] input[checkbox] «Запомнить» — отмечено")


@unittest.skipUnless(HAVE, "нужны playwright и Chromium")
class BrowserFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.downloads = TMP / "downloads"
        cls.saved = (browser.PROFILE, browser.downloads_dir)
        browser.PROFILE = TMP / "profile"
        browser.downloads_dir = lambda: cls.downloads
        cls.app = App(Settings({"browser.executable": CHROME, "browser.headless": True,
                                "browser.search_url": cls.base + "/result?q="}))
        cls.loop = asyncio.new_event_loop()

    @classmethod
    def tearDownClass(cls):
        manager = getattr(cls.app, "browser", None)
        if manager is not None:
            cls.loop.run_until_complete(manager.close())
        cls.loop.close()
        browser.PROFILE, browser.downloads_dir = cls.saved
        cls.server.shutdown()

    def tool(self, action, **args):
        return self.loop.run_until_complete(browser.handler(action, args, Context(app=self.app)))

    def test_flow(self):
        out = self.tool("open", url=self.base + "/")
        self.assertIn("Главная", out)
        self.assertIn("button «Нажми меня»", out)
        self.assertRegex(out, r"\[\d+\] a «Форма» — /form")
        self.tool("click", target="Нажми меня")
        self.assertIn("кнопка сработала", self.tool("read"))
        self.tool("open", url=self.base + "/form")
        self.assertIn("Результат: погода", self.tool("type", target="Запрос", text="погода", submit=True))
        self.assertIn("Результат: котики", self.tool("open", query="котики"))
        self.assertIn("Результат: погода", self.tool("back"))
        self.tool("open", url=self.base + "/")
        self.assertIn("Вторая", self.tool("click", target="Новая вкладка"))
        tabs = self.tool("tabs").splitlines()
        self.assertEqual(len(tabs), 2)
        self.assertIn("← текущая", tabs[1])
        self.assertIn("Главная", self.tool("tab", index=1))
        self.assertIn("вкладка закрыта", self.tool("close_tab"))
        self.assertEqual(len(self.tool("tabs").splitlines()), 1)
        self.assertIn("отчёт.txt", self.tool("download", url=self.base + "/file.txt"))
        self.assertEqual((self.downloads / "отчёт.txt").read_text(encoding="utf-8"), "содержимое файла")
        self.tool("open", url=self.base + "/")
        self.assertIn("отчёт (2).txt", self.tool("download", target="Скачать файл"))
        shot = self.tool("screenshot")
        self.assertIsInstance(shot, Result)
        self.assertTrue(shot.image.startswith("data:image/jpeg;base64,"))
        self.assertEqual(self.tool("eval", text="document.title"), '"Главная"')
        self.assertIn("Главная", self.tool("scroll", direction="down"))
        self.assertIn("появилось", self.tool("wait", target="Длинный текст"))
        with self.assertRaises(ToolError):
            self.tool("click", ref="999")
        with self.assertRaises(ToolError):
            self.tool("click", target="Несуществующая кнопка")
        self.assertEqual(self.tool("close"), "браузер закрыт")


if __name__ == "__main__":
    unittest.main()
