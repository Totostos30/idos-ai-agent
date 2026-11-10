"""Удаление ключей: по id хранилища, по id роутера (provider:id) и ключей из .env."""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest

TMP = pathlib.Path(tempfile.mkdtemp(prefix="idos-keys-"))
os.environ.setdefault("IDOS_HOME", str(TMP))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from core.keystore import KeyStore  # noqa: E402


class KeyRemoveTest(unittest.TestCase):
    def setUp(self) -> None:
        d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        env = d / ".env"
        env.write_text("GROQ_API_KEY=gsk_envkey_1234567890\n", encoding="utf-8")
        self.ks = KeyStore(d / "keys.json", env_file=env, environ={})

    def test_remove_by_router_id(self) -> None:
        item = self.ks.add("groq", "gsk_filekey_1234567890")
        self.assertEqual(len(self.ks.entries()), 2)
        self.assertTrue(self.ks.remove("groq:" + item["id"]))
        self.assertEqual([e["source"] for e in self.ks.entries()], ["env"])
        self.assertFalse(self.ks.remove("groq:" + item["id"]))

    def test_remove_by_plain_id_and_toggle(self) -> None:
        item = self.ks.add("groq", "gsk_filekey_1234567890")
        self.assertTrue(self.ks.set_enabled("groq:" + item["id"], False))
        self.assertFalse([e for e in self.ks.entries() if e["id"] == item["id"]][0]["enabled"])
        self.assertTrue(self.ks.remove(item["id"]))

    def test_env_key_is_hidden(self) -> None:
        env_id = self.ks.entries()[0]["id"]
        self.assertTrue(self.ks.remove("groq:" + env_id))
        self.assertEqual(self.ks.entries(), [])
        self.assertFalse(self.ks.remove(env_id))
        # тот же ключ можно добавить снова вручную
        self.ks.add("groq", "gsk_envkey_1234567890")
        self.assertEqual([e["source"] for e in self.ks.entries()], ["file"])

    def test_local_id(self) -> None:
        self.assertEqual(KeyStore.local_id("groq:k123"), "k123")
        self.assertEqual(KeyStore.local_id("anymodel:env-ab"), "env-ab")
        self.assertEqual(KeyStore.local_id("k123"), "k123")
        self.assertEqual(KeyStore.local_id("weird:k1"), "weird:k1")


if __name__ == "__main__":
    unittest.main()
