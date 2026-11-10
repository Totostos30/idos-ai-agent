"""Единая версия: core/config.py — источник истины, остальные файлы должны совпадать."""
from __future__ import annotations

import json
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import config  # noqa: E402


def json_version(path: pathlib.Path) -> str:
    return str(json.loads(path.read_text(encoding="utf-8-sig")).get("version", ""))


class VersionSyncTest(unittest.TestCase):
    def test_package_json(self) -> None:
        self.assertEqual(json_version(ROOT / "package.json"), config.VERSION)

    def test_package_lock(self) -> None:
        data = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(str(data.get("version")), config.VERSION)
        self.assertEqual(str(data["packages"][""].get("version")), config.VERSION)

    def test_tauri_conf(self) -> None:
        self.assertEqual(json_version(ROOT / "src-tauri" / "tauri.conf.json"), config.VERSION)

    def test_cargo_toml(self) -> None:
        text = (ROOT / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8-sig")
        match = re.search(r'(?m)^version = "([^"]+)"', text)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), config.VERSION)

    def test_cargo_lock(self) -> None:
        text = (ROOT / "src-tauri" / "Cargo.lock").read_text(encoding="utf-8-sig")
        match = re.search(r'(?ms)^name = "idos"\r?\nversion = "([^"]+)"', text)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), config.VERSION)

    def test_docs_titles(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8-sig").splitlines()[0]
        install = (ROOT / "INSTALL.md").read_text(encoding="utf-8-sig").splitlines()[0]
        self.assertIn(config.VERSION, readme)
        self.assertIn(config.VERSION, install)

    def test_builtin_skills_exist(self) -> None:
        from core.skills import Skills

        names = {item["name"] for item in Skills().list() if item["builtin"]}
        self.assertIn("windows-apps", names)
        self.assertIn("web-research", names)
        self.assertIn("files-cleanup", names)


if __name__ == "__main__":
    unittest.main()
