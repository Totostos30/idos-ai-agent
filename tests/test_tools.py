"""Tool registry + platform-independent tools (files, shell, document, memory, skill, model)."""
from __future__ import annotations

import asyncio
import io
import os
import pathlib
import sys
import tempfile
import threading
import unittest

TMP = tempfile.mkdtemp(prefix="idos-tools-")
os.environ.setdefault("IDOS_HOME", TMP)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from core import config, vision  # noqa: E402
from core import db as dbmod  # noqa: E402
from core import skills as skills_mod  # noqa: E402
from core.llm import Completion  # noqa: E402
from core.memory import Memory  # noqa: E402
from core.router import RouterError  # noqa: E402
from core.tools import Aborted, Context, build_registry, short_value  # noqa: E402

MODULES = ["files", "shell", "document", "memory_tool", "skill", "model"]


class FakeRouter:
    def __init__(self) -> None:
        self.active = "groq:openai/gpt-oss-120b"
        self.vision_calls: list[dict] = []

    def status(self) -> dict:
        bucket = {"remaining_requests": 990, "limit_requests": 1000, "reset_requests_in": None,
                  "remaining_tokens": 7000, "limit_tokens": 8000, "reset_tokens_in": None, "cooldown_in": None,
                  "last_error": "", "used_today": 10, "tokens_today": 5000}
        return {"accounts": [{"id": "groq:k1", "provider": "groq", "label": "Groq · ключ 1", "masked": "gsk_…abcd",
                              "source": "settings", "enabled": True, "disabled": False, "disabled_reason": "",
                              "buckets": {"openai/gpt-oss-120b": bucket}, "current": True},
                             {"id": "groq:k2", "provider": "groq", "label": "Groq · ключ 2", "masked": "gsk_…efgh",
                              "source": "settings", "enabled": True, "disabled": True,
                              "disabled_reason": "ключ отклонён (401)", "buckets": {}, "current": False}],
                "providers": {"groq": {"name": "Groq", "keys_url": "", "free": True, "paid": False, "no_key": False,
                                        "models": ["openai/gpt-oss-120b"]},
                              "openai": {"name": "OpenAI", "keys_url": "", "free": False, "paid": True, "no_key": False,
                                         "models": ["gpt-5"]}},
                "chain": [self.active, "groq:openai/gpt-oss-20b"], "active": self.active, "fast": "", "vision": "",
                "current": {"spec": self.active, "account": "Groq · ключ 1"}}

    def has_provider(self, provider: str) -> bool:
        return provider == "groq"

    async def list_models(self, provider: str) -> list[str]:
        if provider != "groq":
            raise RouterError("нет ключа")
        return ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]

    async def resolve_model(self, query: str) -> str:
        if "платн" in query:
            raise RouterError("Платная модель ещё не настроена")
        return "groq:openai/gpt-oss-20b"

    def set_active(self, spec: str) -> None:
        self.active = spec

    async def chat(self, messages: list, **kwargs: object) -> Completion:
        self.vision_calls.append({"messages": messages, **kwargs})
        return Completion(text="Кнопка «ОК» в точке (100, 50)")


class App:
    def __init__(self) -> None:
        base = pathlib.Path(tempfile.mkdtemp(prefix="app-", dir=TMP))
        self.settings = config.Settings(base / "settings.json")
        self.db = dbmod.DB(base / "t.db")
        self.memory = Memory(self.db)
        self.user_skills = base / "skills"
        skills_mod.USER_SKILLS = self.user_skills
        self.skills = skills_mod.Skills([(self.user_skills, False)])
        self.router = FakeRouter()


class ToolsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app = App()
        self.reg = build_registry(self.app, MODULES)
        self.ctx = Context(app=self.app, chat_id="t")

    def tearDown(self) -> None:
        self.app.db.conn.close()

    async def call(self, tool: str, **args: object):
        return await self.reg.run(tool, dict(args), self.ctx)

    def test_registry_and_labels(self) -> None:
        self.assertEqual(sorted(self.reg.names()), ["document", "files", "memory", "model", "shell", "skill"])
        names = [s["function"]["name"] for s in self.reg.schemas()]
        self.assertIn("files", names)
        self.assertEqual(self.reg.kind("files", "delete"), "danger")
        self.assertEqual(self.reg.kind("files", "read"), "read")
        label = self.reg.label("files", "read", {"path": "C:\\Users\\Антон\\Desktop\\отчёт за май.docx"})
        self.assertEqual(label, "Читаю отчёт за май.docx")
        self.assertEqual(short_value("Get-ChildItem C:/Users/x | Select-Object Name, Length")[:13], "Get-ChildItem")
        self.assertEqual(short_value("https://www.example.com/a/b?c=1"), "example.com")
        self.assertTrue(self.reg.needs_confirm("shell", "run", "danger"))
        self.assertFalse(self.reg.needs_confirm("shell", "run", "full"))
        broken = build_registry(self.app, ["files", "no_such_module"])
        self.assertEqual(broken.names(), ["files"])

    async def test_files_cycle(self) -> None:
        out = await self.call("files", action="write", path="notes/a.txt", text="первая строка\n")
        self.assertTrue(out.ok, out.text)
        target = config.WORKSPACE / "notes" / "a.txt"
        self.assertTrue(target.exists())
        out = await self.call("files", action="append", path="notes/a.txt", text="вторая\n")
        self.assertIn("дописан", out.text)
        out = await self.call("files", action="read", path=str(target))
        self.assertIn("первая строка\nвторая", out.text)
        out = await self.call("files", action="write", path="notes/a.txt", text="новое")
        self.assertIn("прежняя версия сохранена", out.text)
        out = await self.call("files", action="list", path="workspace/notes")
        self.assertIn("a.txt", out.text)
        out = await self.call("files", action="search", path="workspace", pattern="*.txt")
        self.assertIn("a.txt", out.text)
        out = await self.call("files", action="search", path="workspace", text="новое")
        self.assertIn("a.txt", out.text)
        out = await self.call("files", action="rename", path="notes/a.txt", to="b.txt")
        self.assertTrue(out.ok, out.text)
        self.assertTrue((config.WORKSPACE / "notes" / "b.txt").exists())
        out = await self.call("files", action="copy", path="notes/b.txt", to="copy")
        self.assertTrue(out.ok, out.text)
        out = await self.call("files", action="copy", path="notes/b.txt", to="copy")
        self.assertFalse(out.ok)
        self.assertIn("overwrite", out.text)
        out = await self.call("files", action="delete", path="notes/b.txt")
        self.assertIn("резервная копия", out.text)
        self.assertFalse((config.WORKSPACE / "notes" / "b.txt").exists())
        out = await self.call("files", action="restore", path="notes/b.txt")
        self.assertTrue(out.ok, out.text)
        self.assertEqual((config.WORKSPACE / "notes" / "b.txt").read_text(encoding="utf-8"), "новое")
        out = await self.call("files", action="info", path="notes/b.txt")
        self.assertIn("размер", out.text)

    async def test_files_guards(self) -> None:
        secret = config.WORKSPACE / ".env"
        secret.parent.mkdir(parents=True, exist_ok=True)
        secret.write_text("GROQ_API_KEY=x", encoding="utf-8")
        out = await self.call("files", action="read", path=str(secret))
        self.assertFalse(out.ok)
        self.assertIn("запрещено", out.text)
        out = await self.call("files", action="write", path=str(config.ROOT / "core" / "evil.py"), text="x")
        self.assertFalse(out.ok)
        self.assertIn("нельзя", out.text)
        out = await self.call("files", action="delete", path="home")
        self.assertFalse(out.ok)
        out = await self.call("files", action="read", path="nope/none.txt")
        self.assertFalse(out.ok)
        self.assertIn("не найден", out.text)
        out = await self.call("files", action="explode", path="x")
        self.assertIn("нет действия", out.text)
        csv = await self.call("files", action="write", path="t.csv", text="имя;сумма")
        self.assertTrue(csv.ok)
        self.assertEqual((config.WORKSPACE / "t.csv").read_bytes()[:3], b"\xef\xbb\xbf")

    async def test_image_read_uses_vision(self) -> None:
        from PIL import Image

        path = config.WORKSPACE / "shot.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (3200, 1800), "white").save(path)
        out = await self.call("files", action="read", path=str(path), text="что тут?")
        self.assertTrue(out.ok, out.text)
        self.assertIn("Кнопка", out.text)
        call = self.app.router.vision_calls[0]
        self.assertEqual(call["purpose"], "vision")
        url = call["messages"][0]["content"][1]["image_url"]["url"]
        self.assertTrue(url.startswith("data:image/jpeg;base64,"))
        self.assertLess(len(url), 4_000_000)
        self.assertEqual(vision.rescale("точка (100, 50)", 2.0, (10, 0)), "точка (210, 100)")
        data, mime, scale, size = vision.prepare(path.read_bytes())
        self.assertEqual(size, (1600, 900))
        self.assertAlmostEqual(scale, 2.0)

    async def test_document(self) -> None:
        import docx

        path = config.WORKSPACE / "doc.docx"
        path.parent.mkdir(parents=True, exist_ok=True)
        document = docx.Document()
        document.add_paragraph("Договор № 7 от вчера")
        document.save(path)
        out = await self.call("document", action="read", path=str(path))
        self.assertTrue(out.ok, out.text)
        self.assertIn("Договор № 7", out.text)
        out = await self.call("files", action="read", path=str(path))
        self.assertIn("Договор", out.text)
        html = config.WORKSPACE / "page.html"
        html.write_text("<html><body><h1>Заголовок</h1><script>x()</script><p>текст</p></body></html>",
                        encoding="utf-8")
        out = await self.call("document", path=str(html))
        self.assertIn("Заголовок", out.text)
        self.assertNotIn("x()", out.text)

    async def test_shell(self) -> None:
        out = await self.call("shell", action="run", command="echo привет")
        self.assertTrue(out.ok, out.text)
        self.assertIn("привет", out.text)
        out = await self.call("shell", action="run", command="exit 3")
        self.assertFalse(out.ok)
        self.assertIn("код выхода: 3", out.text)
        out = await self.call("shell", action="python", code="print('итого', 2 + 2)")
        self.assertIn("итого 4", out.text)
        out = await self.call("shell", action="run", command="sleep 5", timeout=1)
        self.assertIn("тайм-аут", out.text)
        cancel = threading.Event()
        ctx = Context(app=self.app, chat_id="t", cancel=cancel)
        threading.Timer(0.4, cancel.set).start()
        with self.assertRaises(Aborted):
            await self.reg.run("shell", {"action": "run", "command": "sleep 10"}, ctx)
        out = await self.call("shell", action="run", command="sleep 1", background=True)
        self.assertIn("PID", out.text)

    async def test_memory_and_skills(self) -> None:
        out = await self.call("memory", action="save", text="Любимый браузер — Яндекс", tags="profile")
        self.assertIn("запомнил", out.text)
        out = await self.call("memory", action="search", query="браузер")
        self.assertIn("Яндекс", out.text)
        out = await self.call("memory", action="list")
        self.assertIn("всего записей: 1", out.text)
        out = await self.call("memory", action="delete", query="браузер")
        self.assertIn("забыл", out.text)
        out = await self.call("skill", action="save", name="Отчёт", description="как сделать отчёт",
                              text="1. Открыть Excel")
        self.assertIn("сохранён", out.text)
        out = await self.call("skill", action="list")
        self.assertIn("как сделать отчёт", out.text)
        out = await self.call("skill", action="load", name="отчёт")
        self.assertIn("Открыть Excel", out.text)
        out = await self.call("skill", action="load", name="нетакого")
        self.assertFalse(out.ok)
        self.assertIn("не найден", out.text)
        out = await self.call("skill", action="delete", name="отчёт")
        self.assertIn("удалён", out.text)

    async def test_model_tool(self) -> None:
        out = await self.call("model", action="current")
        self.assertIn("groq:openai/gpt-oss-120b", out.text)
        out = await self.call("model", action="list")
        self.assertIn("groq ✓", out.text)
        self.assertIn("openai (OpenAI, платно)", out.text)
        out = await self.call("model", action="list", provider="groq")
        self.assertIn("gpt-oss-20b", out.text)
        out = await self.call("model", action="switch", query="быстрая")
        self.assertEqual(self.app.router.active, "groq:openai/gpt-oss-20b")
        out = await self.call("model", action="switch", query="платная")
        self.assertFalse(out.ok)
        self.assertIn("Платная модель", out.text)
        out = await self.call("model", action="status")
        self.assertIn("запросов 990/1000", out.text)
        self.assertIn("отключён", out.text)


if __name__ == "__main__":
    unittest.main()
