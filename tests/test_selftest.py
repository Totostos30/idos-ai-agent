"""Self-test report: environment, keys, voices, GigaSTT, Whisper and tools — without network."""
from __future__ import annotations

import asyncio
import unittest

from core import bus, selftest
from core.app import App


class SelftestTest(unittest.TestCase):
    def tearDown(self) -> None:
        bus.attach(None)

    def test_report_without_keys(self):
        async def scenario():
            app = App(demo=True)
            try:
                return await selftest.check_app(app, full=False)
            finally:
                await app.close()

        report = asyncio.run(scenario())
        names = {c["name"]: c for c in report["checks"]}
        self.assertEqual(names["Python"]["level"], "ok")
        self.assertEqual(names["Ключи API"]["level"], "error")
        self.assertFalse(report["ok"])
        self.assertIn("GigaSTT", names)
        self.assertEqual(names["Whisper (Groq)"]["level"], "warn")
        self.assertTrue(any(n.startswith("Инструмент ") for n in names))
        self.assertTrue(any(n.startswith("Голос ") for n in names))
        text = selftest.format_report(report)
        self.assertIn("[FAIL] Ключи API", text)
        self.assertIn("Итог: ошибок", text)

    def test_full_check_tests_every_key_and_gigastt(self):
        async def scenario():
            app = App(demo=True)
            entry = app.keystore.add("groq", "gsk_test_key_self_0001", "тест")
            app.router.invalidate()
            calls = []

            async def fake_test(account_id):
                calls.append(account_id)
                return {"ok": True, "message": "ключ работает", "ms": 12}

            async def fake_probe(full=False, timeout=5.0):
                return {"ok": True, "rates": [16000], "latency_ms": 40, "final": True}

            async def fake_ensure(wait=25.0):
                return {"ok": True}

            app.router.test_account = fake_test
            app.stt.probe, app.stt.ensure = fake_probe, fake_ensure
            try:
                return await selftest.check_app(app, full=True), calls
            finally:
                app.keystore.remove(entry["id"])
                app.router.invalidate()
                await app.close()

        report, calls = asyncio.run(scenario())
        names = {c["name"]: c for c in report["checks"]}
        self.assertEqual(len(calls), 1)
        self.assertEqual(names["Ключи API"]["level"], "ok")
        keys = [c for n, c in names.items() if n.startswith("Ключ ")]
        self.assertEqual([c["level"] for c in keys], ["ok"])
        self.assertNotIn("gsk_test_key_self_0001", str(report))
        self.assertEqual(names["GigaSTT"]["level"], "ok")
        self.assertIn("40 мс", names["GigaSTT"]["detail"])
        self.assertEqual(names["Whisper (Groq)"]["level"], "ok")


if __name__ == "__main__":
    unittest.main()
