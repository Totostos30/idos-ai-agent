"""Шифрование API-ключей на диске: DPAPI Windows, обратимость и отсутствие открытого текста."""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import unittest

TMP = pathlib.Path(tempfile.mkdtemp(prefix="idos-crypt-"))
os.environ.setdefault("IDOS_HOME", str(TMP))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from core import config  # noqa: E402
from core.keystore import ENC_PREFIX, KeyStore, protect_secret, unprotect_secret  # noqa: E402

SECRET = "gsk_roundtrip_secret_1234567890"


@unittest.skipUnless(config.IS_WINDOWS, "DPAPI доступен только в Windows")
class KeyEncryptionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        self.path = self.dir / "keys.json"
        self.store = KeyStore(self.path, env_file=self.dir / ".env", environ={})

    def test_roundtrip(self) -> None:
        encrypted = protect_secret(SECRET)
        self.assertTrue(encrypted.startswith(ENC_PREFIX))
        self.assertNotIn(SECRET, encrypted)
        self.assertEqual(unprotect_secret(encrypted), SECRET)

    def test_plaintext_is_untouched(self) -> None:
        self.assertEqual(protect_secret(""), "")
        self.assertEqual(unprotect_secret("gsk_plain_secret_1234567890"), "gsk_plain_secret_1234567890")

    def test_corrupted_value_is_unreadable(self) -> None:
        self.assertEqual(unprotect_secret(ENC_PREFIX + "not-base64"), "")

    def test_file_has_no_plaintext_and_entries_decrypt(self) -> None:
        item = self.store.add("groq", SECRET, label="тест")
        raw = self.path.read_text(encoding="utf-8")
        self.assertNotIn(SECRET, raw)
        self.assertIn(ENC_PREFIX, raw)
        parsed = json.loads(raw)
        self.assertEqual(len(parsed["keys"]), 1)
        self.assertTrue(parsed["keys"][0]["secret"].startswith(ENC_PREFIX))
        entries = self.store.entries("groq")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["secret"], SECRET)
        self.assertEqual(entries[0]["id"], item["id"])

    def test_plaintext_file_from_old_version_is_migrated(self) -> None:
        self.path.write_text(json.dumps({"keys": [{"id": "kold01", "provider": "groq", "secret": SECRET,
                                                   "label": "", "enabled": True}], "env": {}}),
                             encoding="utf-8")
        self.assertEqual(self.store.migrate_plaintext(), 1)
        raw = self.path.read_text(encoding="utf-8")
        self.assertNotIn(SECRET, raw)
        self.assertIn(ENC_PREFIX, raw)
        self.assertEqual(self.store.entries("groq")[0]["secret"], SECRET)
        self.assertEqual(self.store.migrate_plaintext(), 0)

    def test_unreadable_entry_is_skipped(self) -> None:
        self.path.write_text(json.dumps({"keys": [{"id": "kbad01", "provider": "groq",
                                                   "secret": ENC_PREFIX + "bm90LWEta2V5", "enabled": True}],
                                         "env": {}}), encoding="utf-8")
        self.assertEqual(self.store.entries("groq"), [])


if __name__ == "__main__":
    unittest.main()
