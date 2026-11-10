"""Desktop layer without Windows: a fake `uiautomation` module, patched core.win input/window functions,
a fake screen grabber and a fake vision router."""
from __future__ import annotations

import asyncio
import io
import json
import sys
import time
import types
import unittest
from types import SimpleNamespace
from unittest import mock

from PIL import Image, ImageDraw

from core import win
from core.llm import Completion
from core.tools import Context, Result, ToolError, build_registry, computer, windows
from core.win import ocr, uia

PATTERNS = ("InvokePattern", "TogglePattern", "ValuePattern", "SelectionItemPattern", "ExpandCollapsePattern",
            "LegacyIAccessiblePattern", "ScrollItemPattern", "TextPattern")
METHODS = {"Invoke", "Toggle", "Select", "Expand", "Collapse", "SetValue", "DoDefaultAction", "ScrollIntoView"}


class FakePattern:
    def __init__(self, **attrs: object) -> None:
        self.calls: list[str] = []
        self.__dict__.update(attrs)

    def __getattr__(self, name: str):
        if name not in METHODS:
            raise AttributeError(name)

        def method(*args: object) -> None:
            self.calls.append(name)
            if name == "SetValue":
                self.Value = args[0]
            elif name == "Toggle":
                self.ToggleState = 1 - self.ToggleState
            elif name in ("Expand", "Collapse"):
                self.ExpandCollapseState = int(name == "Expand")
        return method


class FakeControl:
    def __init__(self, kind: str, name: str = "", rect=(0, 0, 100, 30), children=(), patterns=None,
                 offscreen: bool = False, cls: str = "", pid: int = 100) -> None:
        self.ControlTypeName, self.Name, self.AutomationId, self.ClassName = kind, name, "", cls
        self.ProcessId, self.IsOffscreen, self.IsEnabled = pid, offscreen, True
        self.BoundingRectangle = SimpleNamespace(left=rect[0], top=rect[1], right=rect[2], bottom=rect[3])
        self.children = list(children)
        self.patterns = patterns or {}
        self.focused = False

    def GetPattern(self, pattern_id: str):
        return self.patterns.get(pattern_id)

    def GetChildren(self) -> list:
        return self.children

    def SetFocus(self) -> bool:
        self.focused = True
        return True


def walk(control, includeTop=False, maxDepth=0xFFFFFFFF):
    def rec(node, depth):
        if depth <= maxDepth:
            yield node, depth
            for child in node.children:
                yield from rec(child, depth + 1)
    if includeTop:
        yield control, 0
    for child in control.children:
        yield from rec(child, 1)


def build() -> SimpleNamespace:
    t = SimpleNamespace()
    t.button = FakeControl("ButtonControl", "Сохранить", (10, 10, 110, 40), patterns={"InvokePattern": FakePattern()})
    t.check = FakeControl("CheckBoxControl", "Автосохранение", (10, 50, 30, 70),
                          patterns={"TogglePattern": FakePattern(ToggleState=0)})
    t.edit = FakeControl("EditControl", "Имя файла", (10, 80, 300, 100),
                         patterns={"ValuePattern": FakePattern(Value="черновик", IsReadOnly=False)})
    t.doc = FakeControl("DocumentControl", "Текст", (10, 110, 600, 400), patterns={
        "TextPattern": SimpleNamespace(DocumentRange=SimpleNamespace(GetText=lambda limit: "Привет, мир"))})
    t.combo = FakeControl("ComboBoxControl", "Кодировка", (10, 420, 200, 440), patterns={
        "ValuePattern": FakePattern(Value="UTF-8", IsReadOnly=True),
        "ExpandCollapsePattern": FakePattern(ExpandCollapseState=0)})
    hidden = FakeControl("ButtonControl", "Скрытая", offscreen=True)
    tiny = FakeControl("ButtonControl", "Ноль", (5, 5, 5, 5))
    label = FakeControl("TextControl", "Строка состояния: готово", (0, 400, 600, 420))
    pane = FakeControl("PaneControl", "", (0, 0, 600, 450), [t.button, t.check, t.edit, t.doc, hidden, tiny, label, t.combo])
    t.window = FakeControl("WindowControl", "Блокнот", (0, 0, 600, 450), [pane])
    t.menu_item = FakeControl("MenuItemControl", "Открыть", (20, 20, 120, 40), patterns={"InvokePattern": FakePattern()})
    menu = FakeControl("MenuControl", "Контекст", (20, 20, 200, 120), [t.menu_item], cls="#32768", pid=999)
    alien = FakeControl("WindowControl", "Чужое", (0, 0, 50, 50), [FakeControl("ButtonControl", "Чужая кнопка")],
                        cls="PopupHost", pid=555)
    t.root = FakeControl("PaneControl", "Рабочий стол", (0, 0, 1600, 900), [t.window, menu, alien])
    return t


TREE = build()
fake_auto = types.ModuleType("uiautomation")
fake_auto.PatternId = SimpleNamespace(**{name: name for name in PATTERNS})
fake_auto.WalkControl = walk
fake_auto.ControlFromHandle = lambda hwnd: TREE.window if hwnd == 1001 else None
fake_auto.GetForegroundControl = lambda: TREE.window
fake_auto.GetRootControl = lambda: TREE.root
fake_auto.GetFocusedControl = lambda: TREE.edit
fake_auto.ControlFromPoint = lambda x, y: TREE.button
fake_auto.UIAutomationInitializerInThread = lambda debug=False: object()
sys.modules["uiautomation"] = fake_auto


def fresh_tree() -> None:
    global TREE
    TREE = build()
    uia._auto = None
    uia.REFS.clear()


class UiaTest(unittest.TestCase):
    def setUp(self) -> None:
        fresh_tree()

    def test_snapshot_find_menu(self) -> None:
        self.assertEqual(uia.snapshot(1001).splitlines(), [
            "[1] кнопка «Сохранить»", "[2] флажок «Автосохранение» — выкл",
            "[3] поле ввода «Имя файла» — значение «черновик»", "[4] документ «Текст»",
            "[5] список «Кодировка» — значение «UTF-8», свёрнуто"])
        self.assertEqual(sorted(uia.REFS), [1, 2, 3, 4, 5])
        self.assertEqual(uia.snapshot(1001, query="сохранить").splitlines(), ["[1] кнопка «Сохранить»"])
        self.assertIn("ничего не нашёл", uia.snapshot(1001, query="нет такого"))
        menu = uia.snapshot(1001, "menu").splitlines()
        self.assertEqual(menu[0], "[1] пункт меню «Открыть»")
        self.assertEqual(len(menu), 6)
        self.assertNotIn("Чужая", "\n".join(menu))
        cut = uia.snapshot(1001, limit=2).splitlines()
        self.assertEqual(len(cut), 3)
        self.assertIn("не все", cut[-1])
        with self.assertRaises(uia.UiaError):
            uia.snapshot(4242)

    def test_actions(self) -> None:
        uia.snapshot(1001)
        with mock.patch.multiple(win, click=mock.DEFAULT, hotkey=mock.DEFAULT, type_text=mock.DEFAULT) as m:
            self.assertEqual(uia.act(1, "click"), "нажато «Сохранить»")
            self.assertEqual(TREE.button.patterns["InvokePattern"].calls, ["Invoke"])
            self.assertEqual(uia.act(2, "click"), "нажато «Автосохранение»")
            self.assertEqual(TREE.check.patterns["TogglePattern"].ToggleState, 1)
            self.assertEqual(uia.act(3, "type", "отчёт"), "введено в «Имя файла»")
            self.assertEqual(TREE.edit.patterns["ValuePattern"].Value, "отчёт")
            self.assertIn("с клавиатуры", uia.act(5, "type", "1251"))
            m["hotkey"].assert_called_with("ctrl+a")
            m["type_text"].assert_called_with("1251")
            self.assertTrue(TREE.combo.focused)
            m["hotkey"].reset_mock()
            uia.act(4, "type", "ещё")
            m["hotkey"].assert_not_called()
            self.assertEqual(uia.act(5, "expand"), "expand: «Кодировка»")
            self.assertEqual(TREE.combo.patterns["ExpandCollapsePattern"].ExpandCollapseState, 1)
            self.assertEqual(uia.act(4, "get"), "Привет, мир")
            self.assertEqual(uia.act(3, "get"), "отчёт")
            self.assertIn("(60, 25)", uia.act(1, "right_click"))
            m["click"].assert_called_with(60, 25, "right", 1)
            uia.act(4, "toggle")
            m["click"].assert_called_with(305, 255, "left", 1)
            with self.assertRaises(uia.UiaError):
                uia.act(1, "collapse")
            with self.assertRaises(uia.UiaError):
                uia.act(99, "click")

    def test_read_and_describe(self) -> None:
        text = uia.read_text(1001)
        for part in ("Сохранить", "черновик", "Привет, мир", "Строка состояния: готово", "UTF-8"):
            self.assertIn(part, text)
        self.assertNotIn("Скрытая", text)
        self.assertEqual(uia.at_point(1, 1), "кнопка «Сохранить» в точке (60, 25)")
        self.assertEqual(uia.focused(), "поле ввода «Имя файла» — значение «черновик» в точке (155, 90)")

    def test_worker_call_and_timeout(self) -> None:
        self.assertTrue(asyncio.run(uia.call(uia.snapshot, 1001)).startswith("[1] кнопка"))
        with self.assertRaises(uia.UiaError):
            asyncio.run(uia.call(time.sleep, 0.5, timeout=0.05))
        self.assertIsNone(uia._worker)
        self.assertEqual(uia.REFS, {})
        self.assertIn("поле ввода", asyncio.run(uia.call(uia.focused)))


class OcrTest(unittest.TestCase):
    LINES = [ocr.make_line("Файл Правка Вид", [("Файл", 10, 5, 30, 12), ("Правка", 50, 5, 50, 12), ("Вид", 110, 5, 25, 12)]),
             ocr.make_line("Сохранить как…", [("Сохранить", 10, 40, 70, 14), ("как…", 85, 40, 30, 14)])]

    def test_find_text_and_shift(self) -> None:
        self.assertEqual(ocr.find_text(self.LINES, "правка"),
                         [{"text": "Файл Правка Вид", "x": 75, "y": 11, "box": (50, 5, 50, 12)}])
        hit = ocr.find_text(self.LINES, "Сохранить  КАК")[0]
        self.assertEqual((hit["x"], hit["y"], hit["box"]), (62, 47, (10, 40, 105, 14)))
        self.assertEqual(ocr.find_text(self.LINES, "нет такого"), [])
        self.assertEqual(ocr.find_text(self.LINES, ""), [])
        moved = ocr.shift(self.LINES, 100, 200, 2.0)
        self.assertEqual(moved[0].words[0], ("Файл", 120, 210, 60, 24))
        self.assertEqual((moved[0].x, moved[0].y), (120, 210))

    def test_recognize_fallback(self) -> None:
        img = Image.new("RGB", (420, 80), "white")
        ImageDraw.Draw(img).text((10, 25), "HELLO OCR 2026", fill="black")
        buf = io.BytesIO()
        img.resize((1680, 320)).save(buf, "PNG")
        # Windows OCR or Tesseract may be installed: then recognition succeeds, otherwise the error explains why.
        try:
            lines = asyncio.run(ocr.recognize(buf.getvalue(), "en"))
        except ocr.OcrUnavailable as exc:
            self.assertTrue(str(exc))
        else:
            self.assertIsInstance(lines, list)


WINDOWS = [
    {"hwnd": 1001, "title": "Блокнот — черновик.txt", "class": "Notepad", "pid": 100, "process": "notepad.exe",
     "minimized": False, "maximized": False, "rect": {"x": 100, "y": 100, "width": 800, "height": 600}},
    {"hwnd": 1002, "title": "Проводник", "class": "CabinetWClass", "pid": 200, "process": "explorer.exe",
     "minimized": True, "maximized": False, "rect": {"x": -32000, "y": -32000, "width": 160, "height": 28}}]
MONITORS = [{"x": 0, "y": 0, "width": 1600, "height": 900, "work": {"x": 0, "y": 0, "width": 1600, "height": 860},
             "primary": True}]


class DesktopBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        fresh_tree()
        names = ("focus", "show", "close", "move_window", "click", "hotkey", "type_text", "drag", "move", "scroll",
                 "switch_layout")
        self.m = {name: mock.MagicMock(return_value=True if name == "focus" else None) for name in names}
        funcs = dict(self.m, list_windows=lambda: [dict(w) for w in WINDOWS], foreground=lambda: dict(WINDOWS[0]),
                     monitors=lambda: [dict(m) for m in MONITORS], cursor_pos=lambda: (500, 500),
                     virtual_screen=lambda: {"x": 0, "y": 0, "width": 1600, "height": 900},
                     keyboard_layout=lambda: "ru", is_window=lambda hwnd: False)
        patcher = mock.patch.multiple(win, **funcs)
        patcher.start()
        self.addCleanup(patcher.stop)


class WindowsToolTest(DesktopBase):
    async def tool(self, action: str, **args: object):
        return await windows.handler(action, args, Context())

    async def test_windows_and_elements(self) -> None:
        listing = await self.tool("list")
        self.assertIn("1001: «Блокнот — черновик.txt» — notepad.exe (активно)", listing)
        self.assertIn("1002: «Проводник» — explorer.exe (свёрнуто)", listing)
        snap = await self.tool("snapshot")
        self.assertTrue(snap.startswith("1001: «Блокнот"))
        self.assertIn("[3] поле ввода «Имя файла» — значение «черновик»", snap)
        found = await self.tool("find", name="сохранить", window="notepad")
        self.assertIn("[1] кнопка «Сохранить»", found)
        self.assertEqual(await self.tool("click", ref="[1]"), "нажато «Сохранить»")
        await self.tool("snapshot")
        self.assertEqual(await self.tool("type", ref="3", text="отчёт"), "введено в «Имя файла»")
        with self.assertRaises(ToolError):
            await self.tool("click", ref="кнопка")
        with self.assertRaises(ToolError):
            await self.tool("click", ref="77")
        self.assertIn("в точке (60, 25)", await self.tool("point", x=1, y=1))
        self.assertIn("Привет, мир", await self.tool("read"))

    async def test_window_ops(self) -> None:
        self.assertIn("переключился на 1002", await self.tool("focus", window="проводник"))
        self.m["focus"].assert_called_with(1002)
        with self.assertRaises(ToolError):
            await self.tool("focus")
        self.assertTrue((await self.tool("minimize")).startswith("свернул 1001"))
        self.m["show"].assert_called_with(1001, "minimize")
        self.assertIn("(0, 0), 800×860", await self.tool("move", place="слева"))
        self.m["move_window"].assert_called_with(1001, 0, 0, 800, 860)
        await self.tool("move", width=1000)
        self.m["move_window"].assert_called_with(1001, 100, 100, 1000, 600)
        self.assertTrue((await self.tool("close", window="блокнот")).startswith("закрыл 1001"))
        self.m["close"].assert_called_with(1001)
        with self.assertRaises(ToolError):
            await self.tool("focus", window="фотошоп")
        with self.assertRaises(ToolError):
            await self.tool("move", place="diagonal")
        self.assertIn("размер 800×600", await self.tool("active"))

    def test_registry(self) -> None:
        with mock.patch.object(win, "IS_WINDOWS", True):
            reg = build_registry(None, ["windows", "computer"])
            self.assertEqual(sorted(reg.names()), ["computer", "windows"])
            schemas = {s["function"]["name"]: json.dumps(s, ensure_ascii=False) for s in reg.schemas()}
        self.assertIn('"ref"', schemas["windows"])
        self.assertIn('"target"', schemas["computer"])
        self.assertEqual(reg.kind("windows", "snapshot"), "read")
        self.assertEqual(reg.kind("computer", "click"), "act")
        self.assertEqual(reg.label("computer", "find", {"target": "Корзина"}), "Ищу на экране «Корзина»")


class VisionRouter:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def chat(self, messages: list, **kwargs: object) -> Completion:
        self.calls.append(kwargs)
        return Completion(text="Корзина в точке (800, 450)")


def png(width: int, height: int) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(out, "PNG")
    return out.getvalue()


class ComputerToolTest(DesktopBase):
    def setUp(self) -> None:
        super().setUp()
        self.boxes: list[tuple] = []
        self.lines = [ocr.make_line("Файл Правка", [("Файл", 10, 5, 30, 12), ("Правка", 50, 5, 50, 12)])]

        def fake_grab(box: tuple) -> bytes:
            self.boxes.append(box)
            return png(box[2] - box[0], box[3] - box[1])

        async def fake_recognize(image: bytes, lang: str = "ru") -> list:
            return [ocr.make_line(line.text, list(line.words)) for line in self.lines]

        for target, values in ((computer, {"grab": fake_grab}), (ocr, {"recognize": fake_recognize})):
            patcher = mock.patch.multiple(target, **values)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.router = VisionRouter()
        self.ctx = Context(app=SimpleNamespace(router=self.router))

    async def tool(self, action: str, **args: object):
        return await computer.handler(action, args, self.ctx)

    async def test_find_and_click(self) -> None:
        self.assertEqual(await self.tool("find", target="правка"), "(75, 11) — текст «Файл Правка»")
        self.assertEqual(self.boxes[-1], (0, 0, 1600, 900))
        self.assertEqual(await self.tool("click", target="Правка"), "клик в (75, 11) — текст «Файл Правка»")
        self.m["click"].assert_called_with(75, 11, "left", 1)
        self.assertTrue((await self.tool("find", target="корзина")).startswith("(800, 450) — "))
        self.assertEqual(self.router.calls[-1]["purpose"], "vision")
        self.assertTrue((await self.tool("find", target="правка", area="window")).startswith("(175, 111)"))
        self.assertEqual(self.boxes[-1], (100, 100, 900, 700))
        self.assertIn("(900, 550)", await self.tool("click", target="корзина", area="window", visual=True))
        self.m["click"].assert_called_with(900, 550, "left", 1)
        self.assertEqual(await self.tool("click", x=10, y=20, button="правая"), "правый клик в (10, 20)")
        self.m["click"].assert_called_with(10, 20, "right", 1)
        self.assertEqual(await self.tool("click", x=5, y=5, count=2), "двойной клик в (5, 5)")
        with self.assertRaises(ToolError):
            await computer.handler("find", {"target": "корзина"}, Context())

    async def test_screen_actions(self) -> None:
        shot = await self.tool("screenshot", text="что на экране?")
        self.assertIsInstance(shot, Result)
        self.assertTrue(shot.image.startswith("data:image/jpeg;base64,"))
        self.assertEqual(shot.text, "экран: Корзина в точке (800, 450)")
        self.assertEqual(self.boxes[0], (0, 0, 1600, 900))
        self.assertEqual(await self.tool("ocr", coords=True), "экран:\nФайл Правка (55, 11)")
        self.lines = []
        self.assertEqual(await self.tool("ocr"), "экран: текста не найдено")
        await self.tool("ocr", area="10,20,300,200")
        self.assertEqual(self.boxes[-1], (10, 20, 310, 220))
        self.assertEqual(await self.tool("scroll", direction="down", amount=3), "прокрутил down на 3")
        self.m["scroll"].assert_called_with(-3, None, None, False)
        await self.tool("scroll", direction="вправо")
        self.m["scroll"].assert_called_with(5, None, None, True)
        self.assertEqual(await self.tool("type", text="привет", enter=True), "напечатал 6 симв. и нажал Enter")
        self.m["type_text"].assert_called_with("привет")
        self.m["hotkey"].assert_called_with("enter")
        self.assertEqual(await self.tool("keys", keys="ctrl+c", repeat=2), "нажал ctrl+c ×2")
        self.m["hotkey"].assert_called_with("ctrl+c", 2)
        await self.tool("drag", x=1, y=2, x2=30, y2=40)
        self.m["drag"].assert_called_with(1, 2, 30, 40, "left")
        with self.assertRaises(ToolError):
            await self.tool("drag", x=1, y=2)
        self.assertEqual(await self.tool("screen"), "Мониторы:\n1. 1600×900 в (0, 0) — основной")
        self.assertEqual(await self.tool("cursor"), "курсор в (500, 500): кнопка «Сохранить» в точке (60, 25)")
        self.assertEqual(await self.tool("layout", lang="en"), "раскладка: ru")
        self.m["switch_layout"].assert_called_with("en")
        self.assertEqual(await self.tool("move", x=3, y=4), "курсор в (3, 4)")
        self.m["move"].assert_called_with(3, 4)
        self.assertEqual(await self.tool("wait", seconds=0.2), "подождал 0.2 с")


if __name__ == "__main__":
    unittest.main()
