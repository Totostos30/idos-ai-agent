"""Шум сброшенных соединений: Proactor на Windows не должен засорять лог трассировками."""
from __future__ import annotations

import asyncio
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from core.util import install_asyncio_error_handler  # noqa: E402


class AsyncioNoiseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.forwarded: list[dict] = []
        self.loop.set_exception_handler(lambda _loop, context: self.forwarded.append(context))
        install_asyncio_error_handler(self.loop)

    def tearDown(self) -> None:
        self.loop.close()

    def report(self, message: str, exc: BaseException | None = None) -> None:
        context = {"message": message}
        if exc is not None:
            context["exception"] = exc
        self.loop.call_exception_handler(context)

    def test_connection_reset_is_silent(self) -> None:
        self.report("Fatal error on transport", ConnectionResetError(10054, "Соединение сброшено"))
        self.report("lost", OSError(10053, "программно прервано"))
        self.report("_call_connection_lost() got an unexpected keyword argument")
        self.assertEqual(self.forwarded, [])

    def test_real_errors_are_forwarded(self) -> None:
        self.report("boom", ValueError("настоящая ошибка"))
        self.assertEqual(len(self.forwarded), 1)
        self.assertIsInstance(self.forwarded[0]["exception"], ValueError)


if __name__ == "__main__":
    unittest.main()
