import unittest

from core.agent import missing_tool_name
from core import tools

KNOWN = {"web": 1, "memory": 1, "shell": 1, "windows": 1, "tools": 1}
GROQ = "Tool call validation failed: tool call validation failed: attempted to call tool 'web' which was not in request.tools"


class MissingToolTest(unittest.TestCase):
    def test_groq_message(self):
        self.assertEqual(missing_tool_name(GROQ, KNOWN), "web")

    def test_aliases_and_namespaces(self):
        for name in ("browser.search", "browser", "web_search", "functions.web", "web.open"):
            self.assertEqual(missing_tool_name(f"attempted to call tool '{name}' which was not in request.tools", KNOWN), "web", name)
        self.assertEqual(missing_tool_name("attempted to call tool 'python' which was not", KNOWN), "shell")

    def test_unknown_or_meta(self):
        self.assertIsNone(missing_tool_name("attempted to call tool 'teleport' which was not in request.tools", KNOWN))
        self.assertIsNone(missing_tool_name("attempted to call tool 'tools' which was not", KNOWN))
        self.assertIsNone(missing_tool_name("failed to parse tool call arguments", KNOWN))

    def test_hints_for_places(self):
        low = "какая ближайшая к моему дому булочная"
        self.assertTrue(any(h in low for h in tools.HINTS["web"]))
        self.assertTrue(any(h in low for h in tools.HINTS["memory"]))


if __name__ == "__main__":
    unittest.main()
