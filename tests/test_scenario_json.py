"""Извлечение JSON из ответа модели для переменных сценария: пояснения и ```-блоки не ломают разбор."""
from __future__ import annotations

import asyncio
import pathlib
import sys
import unittest
from types import SimpleNamespace

TMP = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from core import scenarios as sc  # noqa: E402
from core.llm import Completion  # noqa: E402


class FakeRouter:
    def __init__(self, text: str) -> None:
        self.text = text

    async def chat(self, messages, **kwargs):
        return Completion(text=self.text)


class JsonPayloadTest(unittest.TestCase):
    def test_bare_and_fenced(self) -> None:
        self.assertEqual(sc.json_payload('{"city": "Казань"}'), {"city": "Казань"})
        self.assertEqual(sc.json_payload('```json\n{"city": "Казань"}\n```'), {"city": "Казань"})
        self.assertEqual(sc.json_payload('```\n{"a": 1}\n```'), {"a": 1})

    def test_prose_around_object(self) -> None:
        text = 'Вот значения: {"city": "Казань", "note": "фигурная скобка } внутри"} — готово.'
        self.assertEqual(sc.json_payload(text), {"city": "Казань", "note": "фигурная скобка } внутри"})

    def test_nested_object(self) -> None:
        text = 'Ответ: {"a": {"b": 2}, "c": "}"} конец'
        self.assertEqual(sc.json_payload(text), {"a": {"b": 2}, "c": "}"})

    def test_no_json(self) -> None:
        self.assertIsNone(sc.json_payload("не могу извлечь"))
        self.assertIsNone(sc.json_payload(""))
        self.assertIsNone(sc.json_payload("{сломано"))

    def test_first_json_object(self) -> None:
        self.assertEqual(sc.first_json_object('до {"a": "x{y}"} после {}'), '{"a": "x{y}"}')


class ExtractVariablesTest(unittest.TestCase):
    def extract(self, reply: str) -> dict:
        engine = object.__new__(sc.Scenarios)
        engine.app = SimpleNamespace(router=FakeRouter(reply))
        definitions = [{"name": "city", "description": "город"}]
        return asyncio.run(engine._extract_variables("погода в Казани", definitions))

    def test_values_are_taken_from_noisy_reply(self) -> None:
        self.assertEqual(self.extract('Конечно! {"city": "Казань"}'), {"city": "Казань"})

    def test_unknown_and_empty_fields_are_dropped(self) -> None:
        self.assertEqual(self.extract('{"city": " ", "other": "секрет"}'), {})

    def test_invalid_reply_is_empty(self) -> None:
        self.assertEqual(self.extract("без JSON"), {})


if __name__ == "__main__":
    unittest.main()
