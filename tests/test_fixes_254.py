"""Regression tests for the 2.5.4 fixes."""
import asyncio
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from core import config  # noqa: E402
from core.tools import hint_in  # noqa: E402
from core.tools import shell  # noqa: E402


class HintTest(unittest.TestCase):
    def test_short_hints_need_word_start(self):
        self.assertTrue(hint_in("сон", " режим сон "))
        self.assertFalse(hint_in("сон", " персонал офиса "))
        self.assertTrue(hint_in("темн", " сделай потемнее "))
        self.assertTrue(hint_in("громч", " сделай погромче "))
        self.assertTrue(hint_in(".txt", " открой notes.txt "))


class MigrationTest(unittest.TestCase):
    def test_reasoning_format_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "settings.json"
            path.write_text(json.dumps({"models": {"defaults_rev": 4, "params": {
                "groq:openai/gpt-oss-*": {"reasoning_effort": "low", "reasoning_format": "hidden"}}}}), encoding="utf-8")
            settings = config.Settings(path)
            params = settings.get("models.params")["groq:openai/gpt-oss-*"]
            self.assertNotIn("reasoning_format", params)
            self.assertEqual(params["reasoning_effort"], "low")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["models"]["defaults_rev"], 5)


class BackgroundTest(unittest.TestCase):
    def test_kill_background_only_new(self):
        import subprocess
        import time

        old = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        shell.BACKGROUND.append((old, time.time() - 100))
        since = time.time()
        new = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        shell.BACKGROUND.append((new, time.time()))
        try:
            self.assertEqual(shell.kill_background(since), 1)
            new.wait(5)
            self.assertIsNone(old.poll())
        finally:
            for proc in (old, new):
                if proc.poll() is None:
                    proc.kill()
                proc.wait(5)
            shell.BACKGROUND.clear()


if __name__ == "__main__":
    unittest.main()
