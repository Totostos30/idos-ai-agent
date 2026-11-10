"""Пипетка: одинаковые кнопки различаются по ref (RuntimeId, путь, №N из одинаковых)."""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest

os.environ.setdefault("IDOS_HOME", tempfile.mkdtemp(prefix="idos-ref-"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from core.scenarios import resolve_in, twins  # noqa: E402


class Rect:
    def __init__(self, l, t, r, b):
        self.left, self.top, self.right, self.bottom = l, t, r, b


class C:
    _next = 100

    def __init__(self, kind, name="", aid="", rect=(0, 0, 10, 10), kids=None):
        C._next += 1
        self.rid = [42, C._next]
        self.ControlTypeName, self.Name, self.AutomationId = kind, name, aid
        self.BoundingRectangle = Rect(*rect)
        self.IsOffscreen = False
        self.kids = kids or []

    def GetRuntimeId(self):
        return self.rid

    def GetChildren(self):
        return list(self.kids)


class Auto:
    def WalkControl(self, root, includeTop=False, maxDepth=40):
        def walk(node, depth):
            for k in node.kids:
                yield k, depth
                yield from walk(k, depth + 1)
        return walk(root, 1)


def window():
    # три одинаковые кнопки «Воспроизвести» в разных строках плейлиста
    rows = [C("ListItemControl", f"Трек {i}", rect=(0, 100 * i, 1000, 100 * i + 90),
              kids=[C("ButtonControl", "Воспроизвести", rect=(900, 100 * i + 10, 960, 100 * i + 70))]) for i in range(3)]
    lst = C("ListControl", "Плейлист", rect=(0, 0, 1000, 300), kids=rows)
    root = C("WindowControl", "Яндекс Музыка", rect=(0, 0, 1000, 1000), kids=[lst])
    return root, rows


def target_for(root, rows, i, **extra):
    btn = rows[i].kids[0]
    same = twins(Auto(), root, {"type": "ButtonControl", "name": "Воспроизвести"})
    t = {"type": "ButtonControl", "name": "Воспроизвести", "rx": 0.93, "ry": (100 * i + 40) / 1000,
         "path": [{"type": "ListControl", "name": "Плейлист", "id": "", "index": 0},
                  {"type": "ListItemControl", "name": f"Трек {i}", "id": "", "index": i},
                  {"type": "ButtonControl", "name": "Воспроизвести", "id": "", "index": 0}],
         "ref": {"runtime": btn.rid, "ordinal": same.index(btn) + 1, "twins": len(same)}}
    t.update(extra)
    return t, btn


class RefTest(unittest.TestCase):
    def test_runtime_ref_picks_exact_twin(self):
        root, rows = window()
        t, btn = target_for(root, rows, 2)
        found = resolve_in(Auto(), root, t)
        self.assertIs(found["control"], btn)
        self.assertIn("тот же элемент", found["how"])

    def test_after_restart_path_keeps_the_right_twin(self):
        root, rows = window()
        t, _ = target_for(root, rows, 1)
        root2, rows2 = window()  # программу перезапустили — RuntimeId другие
        found = resolve_in(Auto(), root2, t)
        self.assertIs(found["control"], rows2[1].kids[0])
        self.assertIn("путь", found["how"])

    def test_ordinal_when_path_breaks(self):
        root, rows = window()
        t, _ = target_for(root, rows, 2, path=[{"type": "PaneControl", "index": 0}])
        root2, rows2 = window()
        found = resolve_in(Auto(), root2, t)
        self.assertIs(found["control"], rows2[2].kids[0])
        self.assertIn("№3 из 3", found["how"])

    def test_twin_count_changed_uses_nearest_to_picked_place(self):
        root, rows = window()
        t, _ = target_for(root, rows, 1, path=[])
        t["ref"]["twins"] = 5
        root2, rows2 = window()
        found = resolve_in(Auto(), root2, t)
        self.assertIs(found["control"], rows2[1].kids[0])
        self.assertIn("ближайший", found["how"])

    def test_never_first_twin_by_default(self):
        root, rows = window()
        t, _ = target_for(root, rows, 2)
        t.pop("ref")
        t["path"] = []
        found = resolve_in(Auto(), window()[0], t)
        self.assertNotEqual(found["y"], 40)  # не первая кнопка
        self.assertEqual(found["y"], 240)


if __name__ == "__main__":
    unittest.main()
