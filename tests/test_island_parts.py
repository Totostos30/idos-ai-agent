import pathlib
import re
import unittest

from core.config import DEFAULTS

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"


class IslandPartsTest(unittest.TestCase):
    def setUp(self):
        self.parts = (SRC / "parts.js").read_text(encoding="utf-8")
        self.island = (SRC / "island.js").read_text(encoding="utf-8")

    def keys(self, name):
        block = self.parts.split("export const " + name, 1)[1].split("];\n", 1)[0]
        return re.findall(r'\["([a-z_]+)", "', block)

    def test_defaults(self):
        isl = DEFAULTS["island"]
        self.assertEqual(isl["parts"], {})
        self.assertEqual(isl["hidden_tabs"], [])
        self.assertEqual(isl["hidden_tools"], [])

    def test_every_used_part_is_in_catalog(self):
        used = set(re.findall(r'\bon\("([a-z_]+)"\)', self.island))
        catalog = set(self.keys("PARTS"))
        self.assertTrue(used, "island.js не использует on()")
        self.assertEqual(sorted(used - catalog), [])
        self.assertEqual(sorted(catalog - used), [], "в каталоге есть неиспользуемые части")

    def test_tools_match_buttons(self):
        ids = re.findall(r'^  \["([a-z_]+)", "', self.island.split("const TOOL_BUTTONS", 1)[1].split("];", 1)[0], re.M)
        self.assertEqual(ids, self.keys("TOOL_LIST"))

    def test_tabs(self):
        self.assertEqual(self.keys("TAB_LIST"), ["now", "tools", "clip", "agent"])
        self.assertIn("edit: panelEdit", self.island)


if __name__ == "__main__":
    unittest.main()
