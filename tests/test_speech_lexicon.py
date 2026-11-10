"""Произношение Silero: единицы, сокращения, аббревиатуры, английские слова, порядковые, словарь."""
import asyncio
import unittest

from core.tts.normalize import normalize, sentences
from core.tts.lexicon import parse_lexicon


def n(text, **kw):
    return normalize(text, 10_000, True, **kw).lower()


class UnitsTest(unittest.TestCase):
    def test_pressure(self):
        self.assertIn("семьсот шестьдесят миллиметров ртутного столба", n("760 мм рт. ст."))
        self.assertIn("один миллиметр ртутного столба", n("1 мм рт. ст."))
        self.assertIn("миллиметра ртутного столба", n("742 мм рт.ст."))
        self.assertIn("миллиметры ртутного столба", n("Давление в мм рт. ст. выше нормы"))
        self.assertIn("тысяча тринадцать гектопаскалей", n("1013 гПа"))

    def test_speed_money_misc(self):
        self.assertIn("пять метров в секунду", n("ветер 5 м/с"))
        self.assertIn("две тысячи рублей", n("2 тыс. руб."))
        self.assertIn("километров в час", n("60 км/ч"))
        self.assertIn("шестнадцать гигабайт", n("16 GB"))
        self.assertIn("одна минута", n("1 мин."))
        self.assertIn("пятнадцать процентов", n("15%"))


class AbbrTest(unittest.TestCase):
    def test_common(self):
        self.assertIn("то есть", n("т.е. дёшево"))
        self.assertIn("и так далее", n("яблоки, груши и т.д."))
        out = n("ул. Ленина, д. 5, кв. 12")
        self.assertIn("улица ленина", out)
        self.assertIn("дом пять", out)
        self.assertIn("квартира двенадцать", out)

    def test_acronyms(self):
        self.assertIn("эр эф", n("РФ"))
        self.assertIn("эм чэ эс", n("МЧС"))
        self.assertIn("гост", n("ГОСТ"))
        self.assertIn("эй пи ай", n("API"))
        self.assertIn("энвидиа эр тэ икс четыре тысячи пятьдесят", n("NVIDIA RTX 4050"))

    def test_english(self):
        out = n("hello world")
        self.assertIn("хэллоу ворлд", out)
        self.assertNotRegex(n("station name"), "[a-z]")
        self.assertIn("эйдос", n("idos"))


class OrdinalTest(unittest.TestCase):
    def test_roman(self):
        self.assertIn("двадцать первый век", n("XXI век"))
        self.assertIn("пётр первый", n("Пётр I"))
        self.assertIn("екатерина вторая", n("Екатерина II"))

    def test_dates(self):
        self.assertIn("в две тысячи двадцать шестом году", n("в 2026 году"))
        self.assertIn("в две тысячи двадцать шестом году", n("в 2026 г."))
        self.assertIn("пятый", n("5-й раз"))
        self.assertIn("девятое ноября", n("9 ноября"))
        self.assertIn("до девятого ноября", n("до 9 ноября"))


class SentenceTest(unittest.TestCase):
    def test_no_split_inside_units(self):
        parts = sentences(normalize("Давление 760 мм рт. ст. Ветер слабый.", 10_000, True))
        self.assertTrue(any("ртутного столба" in p for p in parts))
        self.assertFalse(any(p.strip().startswith("ст") for p in parts))


class DictionaryTest(unittest.TestCase):
    def test_user_lexicon(self):
        self.assertIn("видеокарта", n("ГПУ горячая", lexicon={"ГПУ": "видеокарта"}))
        self.assertIn("кубернетес", n("kubernetes up", lexicon="Kubernetes = кубернетес"))
        self.assertEqual(parse_lexicon(["a = b", "мусор"]), {"a": "b"})

    def test_voice_tool_teach_forget(self):
        from core.tools import voice_tool

        class S:
            def __init__(self):
                self.d = {}

            def get(self, k, default=None):
                return self.d.get(k, default)

            def set(self, k, v, save=True):
                self.d[k] = v

        class T:
            settings = S()

        class Ctx:
            class app:
                tts = T()

        run = lambda a, args: asyncio.run(voice_tool.handler(a, args, Ctx()))
        self.assertIn("запомнил", run("teach", {"word": "ГПУ", "say": "видеокарта"}))
        self.assertIn("ГПУ = видеокарта", run("dictionary", {}))
        self.assertIn("видеокарта", run("preview", {"text": "моя ГПУ"}))
        self.assertIn("забыл", run("forget", {"word": "гпу"}))
        self.assertIn("пуст", run("dictionary", {}))


if __name__ == "__main__":
    unittest.main()
