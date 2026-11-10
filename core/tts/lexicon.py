"""Словарь речи для Silero: единицы с согласованием по числу («760 мм рт. ст.» → «семьсот шестьдесят миллиметров
ртутного столба»), сокращения (т.е., и т.д., ул., д.), порядковые и римские числа, годы, русские аббревиатуры
(РФ → «эр эф») и пользовательский словарь произношения."""
from __future__ import annotations

import re
from typing import Any

from . import normalize as _n

RU = "А-Яа-яЁё"
NOT_WORD = r"(?![А-Яа-яЁёA-Za-z])"

# ------------------------------------------------------------------ порядковые числительные
ORD_UNITS = {1: ("перв", "h"), 2: ("втор", "s"), 3: ("трет", "t"), 4: ("четвёрт", "h"), 5: ("пят", "h"),
             6: ("шест", "s"), 7: ("седьм", "s"), 8: ("восьм", "s"), 9: ("девят", "h")}
ORD_TEENS = ["десят", "одиннадцат", "двенадцат", "тринадцат", "четырнадцат", "пятнадцат", "шестнадцат",
             "семнадцат", "восемнадцат", "девятнадцат"]
ORD_TENS = {2: ("двадцат", "h"), 3: ("тридцат", "h"), 4: ("сороков", "s"), 5: ("пятидесят", "h"),
            6: ("шестидесят", "h"), 7: ("семидесят", "h"), 8: ("восьмидесят", "h"), 9: ("девяност", "h")}
COMB = ["", "", "двух", "трёх", "четырёх", "пяти", "шести", "семи", "восьми", "девяти"]
HARD = dict(nom="ый", gen="ого", dat="ому", ins="ым", prep="ом", f="ая", f_gen="ой", f_acc="ую", n="ое",
            pl="ые", pl_gen="ых", pl_ins="ыми")
STRESSED = dict(HARD, nom="ой")
THIRD = dict(nom="ий", gen="ьего", dat="ьему", ins="ьим", prep="ьем", f="ья", f_gen="ьей", f_acc="ью", n="ье",
             pl="ьи", pl_gen="ьих", pl_ins="ьими")
ENDINGS = {"h": HARD, "s": STRESSED, "t": THIRD}


def _ord_parts(n: int) -> tuple[str, str, str] | None:
    """(cardinal head, ordinal stem, ending kind) or None when the number is too big for an ordinal."""
    rest = n % 100
    if rest:
        head = _n.number_words(n - rest) if n - rest else ""
        if 10 <= rest < 20:
            return head, ORD_TEENS[rest - 10], "h"
        if rest % 10:
            stem, kind = ORD_UNITS[rest % 10]
            if rest >= 20:
                head = f"{head} {_n.TENS[rest // 10]}".strip()
            return head, stem, kind
        stem, kind = ORD_TENS[rest // 10]
        return head, stem, kind
    if n % 1000:
        base = n - n % 1000
        return (_n.number_words(base) if base else ""), COMB[(n % 1000) // 100] + "сот", "h"
    if n < 10_000:
        return "", COMB[n // 1000] + "тысячн", "h"
    return None


def ordinal_words(n: int, case: str = "nom") -> str:
    """Порядковое: (5, 'nom') → «пятый», (2026, 'prep') → «две тысячи двадцать шестом», (3, 'f') → «третья»."""
    n = int(n)
    if n == 0:
        return "нулев" + STRESSED.get(case, "ой")
    parts = _ord_parts(n)
    if parts is None:
        return _n.number_words(n)
    head, stem, kind = parts
    return f"{head} {stem}{ENDINGS[kind].get(case, ENDINGS[kind]['nom'])}".strip()


def _kind(n: int) -> str:
    parts = _ord_parts(int(n)) if int(n) else ("", "", "s")
    return parts[2] if parts else "h"


SUFFIX_CASE = {"й": "nom", "ый": "nom", "ий": "nom", "го": "gen", "ого": "gen", "его": "gen", "му": "dat",
               "ому": "dat", "ему": "dat", "м": "prep", "ом": "prep", "ем": "prep", "ым": "ins", "им": "ins",
               "я": "f", "ая": "f", "ья": "f", "ю": "f_acc", "ую": "f_acc", "е": "n", "ое": "n", "ье": "n",
               "ые": "pl", "х": "pl_gen", "ых": "pl_gen", "их": "pl_gen", "ми": "pl_ins", "ыми": "pl_ins"}
ORDINAL = re.compile(r"(?<![\d.,])(\d{1,6})-(ыми|ого|его|ому|ему|ый|ий|ой|ая|ья|ую|ое|ье|ые|ых|их|ом|ем|ым|им|ми|го|му|й|я|ю|е|м|х)"
                     r"(?![А-Яа-яЁё])(\s+год(?:ы|ах|ов)\b)?")


def _ordinal(match: re.Match) -> str:
    n, suffix, years = int(match.group(1)), match.group(2), match.group(3) or ""
    if suffix == "ой":
        case = "nom" if _kind(n) == "s" else "f_gen"
    elif suffix == "е" and years:
        case = "pl"
    else:
        case = SUFFIX_CASE.get(suffix, "nom")
    return ordinal_words(n, case) + years


# ------------------------------------------------------------------ годы и даты со словами месяцев
YEAR = re.compile(r"(?:\b(в|во|с|со|до|от|к|ко|после|около|на|по|за|из|с\s+начала)\s+)?(?<![\d.,])(\d{4}|\d{3}(?=\s?год))"
                  r"\s?(году|года|годом|годах|годов|годы|год|г\.?)(?![А-Яа-яЁё])", re.I)
YEAR_CASE = {"году": "prep", "года": "gen", "годом": "ins", "годах": "pl_gen", "годов": "pl_gen", "годы": "pl",
             "год": "nom"}
GEN_PREPS = {"с", "со", "до", "от", "после", "около", "из", "кроме", "для", "без", "начиная с"}


def _year(match: re.Match) -> str:
    prep, year, word = (match.group(1) or ""), int(match.group(2)), match.group(3).lower()
    low = prep.lower()
    if word.startswith("г") and word not in YEAR_CASE:
        if low in ("в", "во"):
            case, word = "prep", "году"
        elif low in ("к", "ко"):
            case, word = "dat", "году"
        elif low in ("на", "за", "по"):
            case, word = "nom", "год"
        else:
            case, word = "gen", "года"
    else:
        case = "dat" if word == "году" and low in ("к", "ко") else YEAR_CASE[word]
    text = f"{ordinal_words(year, case)} {word}"
    text = f"{prep} {text}" if prep else text
    return text + _keep_dot(match)


MONTH_ABBR = {"янв": "января", "фев": "февраля", "февр": "февраля", "мар": "марта", "апр": "апреля",
              "июн": "июня", "июл": "июля", "авг": "августа", "сен": "сентября", "сент": "сентября",
              "окт": "октября", "ноя": "ноября", "нояб": "ноября", "дек": "декабря"}
MONTH_SHORT = re.compile(r"(?<![\d.,])(\d{1,2})\s?(янв|февр|фев|мар|апр|июн|июл|авг|сент|сен|окт|нояб|ноя|дек)\.?"
                         r"(?![А-Яа-яЁё])", re.I)
DAY_MONTH = re.compile(r"(?:\b(до|с|со|от|после|около|кроме|для|из|без|к|ко)\s+)?(?<![\d.,:])(\d{1,2})(?=\s+(?:"
                       + "|".join(_n.MONTHS) + r")(?![а-яё]))", re.I)


def _day_month(match: re.Match) -> str:
    prep, day = match.group(1) or "", int(match.group(2))
    if not 1 <= day <= 31:
        return match.group(0)
    low = prep.lower()
    case = "dat" if low in ("к", "ко") else "gen" if low else "n"
    return (prep + " " if prep else "") + ordinal_words(day, case)


# ------------------------------------------------------------------ числа с единицами измерения
def _u(pattern: str, one: str, few: str, many: str, fem: bool = False) -> tuple[str, tuple[str, str, str], bool]:
    return pattern, (one, few, many), fem


# Порядок важен: длинные сокращения раньше коротких («мм рт. ст.» раньше «мм», «км/ч» раньше «км»).
UNITS = [
    _u(r"мм\s?рт\.?\s?ст\.?", "миллиметр ртутного столба", "миллиметра ртутного столба", "миллиметров ртутного столба"),
    _u(r"км/ч", "километр в час", "километра в час", "километров в час"),
    _u(r"(?i:km/h)", "километр в час", "километра в час", "километров в час"),
    _u(r"м/с", "метр в секунду", "метра в секунду", "метров в секунду"),
    _u(r"(?i:m/s)", "метр в секунду", "метра в секунду", "метров в секунду"),
    _u(r"об/мин", "оборот в минуту", "оборота в минуту", "оборотов в минуту"),
    _u(r"(?:Гбит|Гб)/с|(?i:gbps)", "гигабит в секунду", "гигабита в секунду", "гигабит в секунду"),
    _u(r"(?:Мбит|Мб)/с|(?i:mbps)", "мегабит в секунду", "мегабита в секунду", "мегабит в секунду"),
    _u(r"(?:кбит|Кбит)/с|(?i:kbps)", "килобит в секунду", "килобита в секунду", "килобит в секунду"),
    _u(r"МБ/с|(?i:mb/s)", "мегабайт в секунду", "мегабайта в секунду", "мегабайт в секунду"),
    _u(r"кВт\s?[·*⋅]?\s?ч|(?i:kwh)", "киловатт-час", "киловатт-часа", "киловатт-часов"),
    _u(r"мА\s?[·*⋅]?\s?ч|(?i:mah)", "миллиампер-час", "миллиампер-часа", "миллиампер-часов"),
    _u(r"(?:кв\.\s?км|км²|км2)", "квадратный километр", "квадратных километра", "квадратных километров"),
    _u(r"(?:кв\.\s?м|м²|м2)", "квадратный метр", "квадратных метра", "квадратных метров"),
    _u(r"(?:куб\.\s?м|м³|м3)", "кубический метр", "кубических метра", "кубических метров"),
    _u(r"(?:см²|см2)", "квадратный сантиметр", "квадратных сантиметра", "квадратных сантиметров"),
    _u(r"гПа|(?i:hpa)", "гектопаскаль", "гектопаскаля", "гектопаскалей"),
    _u(r"кПа|(?i:kpa)", "килопаскаль", "килопаскаля", "килопаскалей"),
    _u(r"МПа", "мегапаскаль", "мегапаскаля", "мегапаскалей"),
    _u(r"Па", "паскаль", "паскаля", "паскалей"),
    _u(r"ГГц|(?i:ghz)", "гигагерц", "гигагерца", "гигагерц"),
    _u(r"МГц|(?i:mhz)", "мегагерц", "мегагерца", "мегагерц"),
    _u(r"кГц|(?i:khz)", "килогерц", "килогерца", "килогерц"),
    _u(r"Гц|(?i:hz)", "герц", "герца", "герц"),
    _u(r"МВт|(?i:mw)", "мегаватт", "мегаватта", "мегаватт"),
    _u(r"кВт|(?i:kw)", "киловатт", "киловатта", "киловатт"),
    _u(r"Вт|W", "ватт", "ватта", "ватт"),
    _u(r"ккал|(?i:kcal)", "килокалория", "килокалории", "килокалорий", True),
    _u(r"кал\.?", "калория", "калории", "калорий", True),
    _u(r"(?i:тб|tb)", "терабайт", "терабайта", "терабайт"),
    _u(r"(?i:гб|gb)", "гигабайт", "гигабайта", "гигабайт"),
    _u(r"(?i:мб|mb)", "мегабайт", "мегабайта", "мегабайт"),
    _u(r"(?i:кб|kb)", "килобайт", "килобайта", "килобайт"),
    _u(r"мкм", "микрометр", "микрометра", "микрометров"),
    _u(r"нм|(?i:nm)", "нанометр", "нанометра", "нанометров"),
    _u(r"мм|(?i:mm)", "миллиметр", "миллиметра", "миллиметров"),
    _u(r"см|(?i:cm)", "сантиметр", "сантиметра", "сантиметров"),
    _u(r"км|(?i:km)", "километр", "километра", "километров"),
    _u(r"мг|(?i:mg)", "миллиграмм", "миллиграмма", "миллиграммов"),
    _u(r"кг|(?i:kg)", "килограмм", "килограмма", "килограммов"),
    _u(r"мл|(?i:ml)", "миллилитр", "миллилитра", "миллилитров"),
    _u(r"мкс", "микросекунда", "микросекунды", "микросекунд", True),
    _u(r"мс|(?i:ms)", "миллисекунда", "миллисекунды", "миллисекунд", True),
    _u(r"сек\.?|(?i:sec)", "секунда", "секунды", "секунд", True),
    _u(r"мин\.?|(?i:min)", "минута", "минуты", "минут", True),
    _u(r"дн\.?", "день", "дня", "дней"),
    _u(r"нед\.?", "неделя", "недели", "недель", True),
    _u(r"мес\.?", "месяц", "месяца", "месяцев"),
    _u(r"шт\.?", "штука", "штуки", "штук", True),
    _u(r"чел\.?", "человек", "человека", "человек"),
    _u(r"руб\.?|₽", "рубль", "рубля", "рублей"),
    _u(r"коп\.?", "копейка", "копейки", "копеек", True),
    _u(r"долл\.?|\$", "доллар", "доллара", "долларов"),
    _u(r"€|евро", "евро", "евро", "евро"),
    _u(r"£", "фунт", "фунта", "фунтов"),
    _u(r"¥", "иена", "иены", "иен", True),
    _u(r"₸", "тенге", "тенге", "тенге"),
    _u(r"₴", "гривна", "гривны", "гривен", True),
    _u(r"(?i:fps)", "кадр в секунду", "кадра в секунду", "кадров в секунду"),
    _u(r"(?i:px)", "пиксель", "пикселя", "пикселей"),
    _u(r"(?i:dpi)", "точка на дюйм", "точки на дюйм", "точек на дюйм", True),
    _u(r"(?i:дб|db)", "децибел", "децибела", "децибел"),
    _u(r"%", "процент", "процента", "процентов"),
    _u(r"‰", "промилле", "промилле", "промилле"),
    _u(r"°", "градус", "градуса", "градусов"),
    _u(r"м", "метр", "метра", "метров"),
    _u(r"г|(?i:g)", "грамм", "грамма", "граммов"),
    _u(r"т", "тонна", "тонны", "тонн", True),
    _u(r"л", "литр", "литра", "литров"),
    _u(r"ч\.?", "час", "часа", "часов"),
    _u(r"с(?=\s*(?:[.,;:!?)]|$))", "секунда", "секунды", "секунд", True),
    _u(r"В(?=\s*(?:[.,;:!?)]|$))", "вольт", "вольта", "вольт"),
    _u(r"А(?=\s*(?:[.,;:!?)]|$))", "ампер", "ампера", "ампер"),
]
UNIT_RES = [(re.compile(p), forms, fem) for p, forms, fem in UNITS]
SCALES = {"тыс": (("тысяча", "тысячи", "тысяч"), True), "млн": (("миллион", "миллиона", "миллионов"), False),
          "млрд": (("миллиард", "миллиарда", "миллиардов"), False),
          "трлн": (("триллион", "триллиона", "триллионов"), False)}
NUMBER = re.compile(r"(\d+)(?:[.,](\d+))?(?:\s?(тыс|млн|млрд|трлн)\.?(?![А-Яа-яЁёA-Za-z]))?"
                    r"(?:\s?(" + "|".join(f"(?:{p})" for p, _f, _m in UNITS) + r")(?![А-Яа-яЁёA-Za-z0-9]))?")


def _keep_dot(match: re.Match) -> str:
    """A dotted abbreviation at the end of a sentence also ends the sentence: «… 5 руб. Потом» keeps the period."""
    if not match.group(0).endswith("."):
        return ""
    after = match.string[match.end():]
    return "." if not after.strip() or re.match(r"\s+[А-ЯЁA-Z]", after) else ""


def unit_for(text: str) -> tuple[tuple[str, str, str], bool] | None:
    for rx, forms, fem in UNIT_RES:
        if rx.fullmatch(text):
            return forms, fem
    return None


def number_with_unit(match: re.Match) -> str:
    whole, frac, scale, unit_text = match.group(1), match.group(2), match.group(3), match.group(4)
    unit = unit_for(unit_text) if unit_text else None
    forms, fem = unit if unit else (None, False)
    if scale:
        sforms, sfem = SCALES[scale]
        if frac is not None:
            words = f"{_n.decimal_words(whole, frac)} {sforms[1]}"
        else:
            words = f"{_n.number_words(int(whole), sfem)} {_n.plural(int(whole), sforms)}"
        return words + (f" {forms[2]}" if forms else "") + _keep_dot(match)
    if frac is not None:
        words = _n.decimal_words(whole, frac) + (f" {forms[1]}" if forms else "")
    elif len(whole) > 1 and whole.startswith("0"):
        words = " ".join(_n.DIGITS[int(d)] for d in whole) + (f" {forms[2]}" if forms else "")
    else:
        words = _n.number_words(int(whole), fem) + (f" {_n.plural(int(whole[-2:]), forms)}" if forms else "")
    return words + _keep_dot(match)


# ------------------------------------------------------------------ сокращения без чисел
PRE_ABBR = [  # перед цифрой: «д. 5» → «дом пять»
    ("д", "дом"), ("кв", "квартира"), ("стр", "страница"), ("с", "страница"), ("рис", "рисунок"),
    ("табл", "таблица"), ("гл", "глава"), ("п", "пункт"), ("пп", "подпункт"), ("ст", "статья"), ("корп", "корпус"),
    ("эт", "этаж"), ("тел", "телефон"), ("вып", "выпуск"), ("ч", "часть"), ("т", "том"), ("стр-е", "строение"),
    ("оф", "офис"), ("каб", "кабинет"), ("ауд", "аудитория"), ("кл", "класс"),
]
PRE_ABBR_RE = re.compile(r"(?<![А-Яа-яЁё.])(" + "|".join(re.escape(a) for a, _ in sorted(PRE_ABBR, key=lambda x: -len(x[0])))
                         + r")\.\s?(?=\d)", re.I)
PRE_ABBR_MAP = dict(PRE_ABBR)

ABBR = [
    (r"мм\s?рт\.?\s?ст\.?", "миллиметры ртутного столба"),
    (r"и\s?т\.\s?д\.", "и так далее"), (r"и\s?т\.\s?п\.", "и тому подобное"), (r"и\s?др\.", "и другие"),
    (r"и\s?пр\.", "и прочее"), (r"т\.\s?е\.", "то есть"), (r"т\.\s?к\.", "так как"), (r"т\.\s?н\.", "так называемый"),
    (r"т\.\s?ч\.", "том числе"), (r"т\.\s?о\.", "таким образом"), (r"т\.\s?д\.", "так далее"),
    (r"т\.\s?п\.", "тому подобное"), (r"ч\.\s?т\.\s?д\.", "что и требовалось доказать"),
    (r"до\s?н\.\s?э\.", "до нашей эры"), (r"н\.\s?э\.", "нашей эры"), (r"с\.\s?г\.", "сего года"),
    (r"напр\.", "например"), (r"см\.(?=\s)", "смотри"), (r"ок\.(?=\s)", "около"), (r"прим\.", "примечание"),
    (r"вкл\.", "включительно"), (r"искл\.", "исключительно"), (r"ул\.", "улица"), (r"пр-т|просп\.", "проспект"),
    (r"пр-д", "проезд"), (r"пер\.", "переулок"), (r"пл\.", "площадь"), (r"наб\.", "набережная"), (r"ш\.(?=\s?[А-ЯЁ])", "шоссе"),
    (r"обл\.", "область"), (r"р-н", "район"), (r"р-на", "района"), (r"г\.(?=\s?[А-ЯЁ])", "город"),
    (r"пос\.", "посёлок"), (r"дер\.", "деревня"), (r"респ\.", "республика"), (r"акад\.", "академик"),
    (r"проф\.", "профессор"), (r"доц\.", "доцент"), (r"им\.", "имени"), (r"зам\.", "заместитель"),
    (r"гл\.\s?ред\.", "главный редактор"), (r"ген\.\s?дир\.", "генеральный директор"), (r"тыс\.", "тысяч"),
    (r"млн\.?", "миллионов"), (r"млрд\.?", "миллиардов"), (r"трлн\.?", "триллионов"), (r"руб\.", "рублей"),
    (r"коп\.", "копеек"), (r"долл\.", "долларов"), (r"мин\.(?=\s)", "минут"), (r"сек\.", "секунд"),
    (r"км/ч", "километров в час"), (r"м/с", "метров в секунду"), (r"об/мин", "оборотов в минуту"),
    (r"гПа", "гектопаскалей"), (r"кПа", "килопаскалей"), (r"кВт\s?[·*⋅]?\s?ч", "киловатт-часов"), (r"шт\.", "штук"),
    (r"чел\.", "человек"), (r"гг\.", "годы"), (r"вв\.", "века"), (r"англ\.", "английский"), (r"рус\.", "русский"),
    (r"лат\.", "латинский"), (r"греч\.", "греческий"), (r"сокр\.", "сокращённо"), (r"стр\.", "страница"),
    (r"рис\.", "рисунок"), (r"табл\.", "таблица"), (r"мес\.", "месяцев"), (r"кв\.\s?м", "квадратных метров"),
    (r"(?i:etc)\.?", "и так далее"), (r"(?i:e\.\s?g)\.", "например"), (r"(?i:i\.\s?e)\.", "то есть"),
    (r"[Пп]н\.?(?=[\s,]|$)", "понедельник"), (r"вт\.?(?=[\s,]|$)", "вторник"), (r"[Сс]р(?=[\s,]|$)", "среда"),
    (r"[Чч]т\.?(?=[\s,]|$)", "четверг"), (r"[Пп]т\.?(?=[\s,]|$)", "пятница"), (r"[Сс]б\.?(?=[\s,]|$)", "суббота"),
    (r"вс\.?(?=[\s,]|$)", "воскресенье"),
]
ABBR_RES = [(re.compile(r"(?<![А-Яа-яЁёA-Za-z\-])(?:" + p + r")(?![А-Яа-яЁёA-Za-z])"), r) for p, r in ABBR]

SYMBOL_WORDS = [
    (re.compile(r"(?<=\d)\s?[xх×]\s?(?=\d)"), " на "), (re.compile(r"\b24/7\b"), "круглосуточно"),
    (re.compile(r"±"), " плюс минус "), (re.compile(r"×"), " на "), (re.compile(r"≈"), " примерно "),
    (re.compile(r"≥|>="), " больше или равно "), (re.compile(r"≤|<="), " меньше или равно "),
    (re.compile(r"≠|!="), " не равно "), (re.compile(r"(?<=\s)>(?=\s)"), " больше "),
    (re.compile(r"(?<=\s)<(?=\s)"), " меньше "), (re.compile(r"½"), " с половиной "), (re.compile(r"¼"), " с четвертью "),
    (re.compile(r"²"), " в квадрате "), (re.compile(r"³"), " в кубе "), (re.compile(r"§"), " параграф "),
    (re.compile(r"@"), " собака "),
]
LEFTOVER = [("%", " процентов "), ("‰", " промилле "), ("°", " градусов "), ("₽", " рублей "), ("$", " долларов "),
            ("€", " евро "), ("£", " фунтов ")]


# Сокращения перед именем или названием: точка после них не конец предложения («ул. Ленина», «проф. Иванов»).
NO_DOT = {"улица", "проспект", "проезд", "переулок", "площадь", "набережная", "шоссе", "город", "посёлок",
          "деревня", "республика", "академик", "профессор", "доцент", "имени", "заместитель", "смотри", "около",
          "например", "примечание", "то есть", "так как", "так называемый", "том числе", "таким образом",
          "главный редактор", "генеральный директор", "страница", "рисунок", "таблица"}


def _abbr_sub(rx: re.Pattern, replacement: str, text: str) -> str:
    if replacement in NO_DOT:
        return rx.sub(lambda m: replacement + ("." if m.group(0).endswith(".") and not m.string[m.end():].strip() else ""), text)
    return rx.sub(lambda m: replacement + _keep_dot(m), text)


# ------------------------------------------------------------------ римские числа
ROMAN_VALID = re.compile(r"M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})")
ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
ROMAN_KEYS = {
    "век": ("nom", "век"), "века": ("gen", "века"), "веке": ("prep", "веке"), "веку": ("dat", "веку"),
    "веком": ("ins", "веком"), "в.": ("nom", "век"), "вв.": ("nom", "века"), "съезд": ("nom", "съезд"),
    "съезда": ("gen", "съезда"), "съезде": ("prep", "съезде"), "том": ("nom", "том"), "тома": ("gen", "тома"),
    "томе": ("prep", "томе"), "глава": ("f", "глава"), "главы": ("f_gen", "главы"), "главе": ("f_gen", "главе"),
    "часть": ("f", "часть"), "части": ("f_gen", "части"), "квартал": ("nom", "квартал"),
    "квартала": ("gen", "квартала"), "квартале": ("prep", "квартале"), "степень": ("f", "степень"),
    "степени": ("f_gen", "степени"), "группа": ("f", "группа"), "группы": ("f_gen", "группы"),
    "группе": ("f_gen", "группе"), "место": ("n", "место"), "места": ("gen", "места"), "месте": ("prep", "месте"),
    "тысячелетие": ("n", "тысячелетие"), "тысячелетия": ("gen", "тысячелетия"), "сезон": ("nom", "сезон"),
    "сезона": ("gen", "сезона"), "этап": ("nom", "этап"), "этапа": ("gen", "этапа"), "тур": ("nom", "тур"),
    "уровень": ("nom", "уровень"), "уровня": ("gen", "уровня"), "созыва": ("gen", "созыва"),
}
ROMAN_CTX = re.compile(r"(?<![A-Za-z])([IVXLCDM]+)(?:\s?[–—-]\s?([IVXLCDM]+))?\s+("
                       + "|".join(re.escape(k) for k in sorted(ROMAN_KEYS, key=len, reverse=True)) + r")(?![А-Яа-яЁё])")
ROMAN_NAME = re.compile(r"(?<![А-Яа-яЁё])([А-ЯЁ][а-яё]+)\s+([IVX]{1,5})(?![A-Za-z0-9])(?!\s+[A-Za-z])")
FEMININE_NOUNS = {"часть", "степень", "дочь", "мать", "любовь"}


def roman_value(token: str) -> int | None:
    if not token or not ROMAN_VALID.fullmatch(token):
        return None
    total = 0
    for ch, nxt in zip(token, token[1:] + " "):
        value = ROMAN_VALUES[ch]
        total += -value if nxt != " " and ROMAN_VALUES[nxt] > value else value
    return total or None


def _roman_ctx(match: re.Match) -> str:
    first, second, key = roman_value(match.group(1)), roman_value(match.group(2) or ""), match.group(3)
    if first is None or (match.group(2) and second is None):
        return match.group(0)
    case, word = ROMAN_KEYS[key]
    text = ordinal_words(first, case) + (f" — {ordinal_words(second, case)}" if second else "")
    return f"{text} {word}" + _keep_dot(match)


def _roman_name(match: re.Match) -> str:
    name, value = match.group(1), roman_value(match.group(2))
    if value is None:
        return match.group(0)
    fem = name[-1] in "ая" or name.lower() in FEMININE_NOUNS
    return f"{name} {ordinal_words(value, 'f' if fem else 'nom')}"


# ------------------------------------------------------------------ русские аббревиатуры
RU_LETTERS = {"А": "а", "Б": "бэ", "В": "вэ", "Г": "гэ", "Д": "дэ", "Е": "е", "Ё": "ё", "Ж": "жэ", "З": "зэ",
              "И": "и", "Й": "и краткое", "К": "ка", "Л": "эль", "М": "эм", "Н": "эн", "О": "о", "П": "пэ",
              "Р": "эр", "С": "эс", "Т": "тэ", "У": "у", "Ф": "эф", "Х": "ха", "Ц": "цэ", "Ч": "чэ", "Ш": "ша",
              "Щ": "ща", "Ъ": "", "Ы": "ы", "Ь": "", "Э": "э", "Ю": "ю", "Я": "я"}
RU_VOWELS = set("АЕЁИОУЫЭЮЯ")
RU_ACRONYM_SPECIAL = {"США": "сэ шэ а", "ИТ": "ай ти", "ЦСКА": "цэ эс ка", "ФРГ": "эф эр гэ", "ЮАР": "юар",
                      "ГИБДД": "гэ и бэ дэ дэ", "ВДНХ": "вэ дэ эн ха", "ЕГЭ": "е гэ э", "ОГЭ": "о гэ э"}
RU_ACRONYM_WORDS = {"ГОСТ", "СНИЛС", "ЗАГС", "МХАТ", "ОМОН", "ТАСС", "СМИ", "МКАД", "ТЮЗ", "ГУЛАГ", "НИИ", "ООН",
                    "ВУЗ", "МИД", "НАТО", "ГУМ", "ЦУМ", "ЖЭК", "ВОЗ", "ГЭС", "АЭС", "ТЭЦ", "ВИЧ", "РАН", "ОПЕК",
                    "ЮНЕСКО", "ИНН", "ОМС", "ВАК", "СИЗО", "ОСАГО", "КАСКО", "НЭП", "ТАСС", "РИА", "МЭР", "ВТБ"}
RU_ACRONYM_WORDS -= {"ВТБ", "ИНН", "ОМС"}
COMMON_CAPS = {"ДА", "НЕТ", "НЕ", "НО", "ИЛИ", "ВСЁ", "ВСЕ", "ЭТО", "ВОТ", "ТАК", "ЕЩЁ", "УЖЕ", "КАК", "ЧТО", "ГДЕ",
               "КТО", "ТАМ", "ТУТ", "ВЫ", "МЫ", "ОН", "ОНА", "ОНИ", "ТЫ", "ДЛЯ", "СТОП", "ВАЖНО", "ОЧЕНЬ", "ТОЛЬКО",
               "СЕЙЧАС", "НАДО", "НУЖНО", "ПО", "НА", "ДО", "ОТ", "ЗА", "ИЗ", "СО", "КО", "ВО", "ОК", "ЖДИ", "ВНИМАНИЕ"}
SPELL_TWO = {"ИП", "ЕС", "ОС", "ИК", "АО", "УК", "ИИ", "ЖК", "ТВ", "ПК", "ЧП", "РФ", "ЦБ", "МО", "ЯО", "ИЖ", "ЭВМ"}
RU_ACRONYM = re.compile(r"(?<![А-Яа-яЁё])[А-ЯЁ]{2,6}(?![А-Яа-яЁё])")
CAPS_RUN = re.compile(r"(?:[А-ЯЁ]{2,}[\s,!?.:;—-]+){2,}[А-ЯЁ]{2,}")


def spell_ru(token: str) -> str:
    return " ".join(filter(None, (RU_LETTERS.get(ch, ch.lower()) for ch in token)))


def _max_consonants(token: str) -> int:
    best = run = 0
    for ch in token:
        run = 0 if ch in RU_VOWELS else run + 1
        best = max(best, run)
    return best


def _acronym(match: re.Match) -> str:
    token = match.group(0)
    if token in RU_ACRONYM_SPECIAL:
        return RU_ACRONYM_SPECIAL[token]
    if token in COMMON_CAPS or token in RU_ACRONYM_WORDS:
        return token.lower()
    vowels = sum(ch in RU_VOWELS for ch in token)
    if vowels == 0 or vowels == len(token):
        return spell_ru(token)
    if len(token) == 2:
        return spell_ru(token) if token in SPELL_TWO else token.lower()
    if len(token) >= 5:
        return token.lower() if _max_consonants(token) < 3 and not re.search(r"([^АЕЁИОУЫЭЮЯ])\1$", token) else spell_ru(token)
    pairs = zip(token, token[1:])
    alternating = all((a in RU_VOWELS) != (b in RU_VOWELS) for a, b in pairs)
    return token.lower() if alternating else spell_ru(token)


def _caps_run(match: re.Match) -> str:
    words = re.findall(r"[А-ЯЁ]+", match.group(0))
    return match.group(0).lower() if len(words) >= 3 and any(len(w) >= 5 for w in words) else match.group(0)


def acronyms(text: str) -> str:
    text = CAPS_RUN.sub(_caps_run, text)
    return RU_ACRONYM.sub(_acronym, text)


# ------------------------------------------------------------------ пользовательский словарь
def parse_lexicon(value: Any) -> dict[str, str]:
    """Accepts a dict, a list of «слово = как читать» lines or one multi-line string."""
    if isinstance(value, dict):
        items = value.items()
    else:
        lines = value.splitlines() if isinstance(value, str) else list(value or [])
        items = []
        for line in lines:
            if isinstance(line, str) and "=" in line and not line.strip().startswith("#"):
                left, right = line.split("=", 1)
                items.append((left, right))
    out: dict[str, str] = {}
    for key, val in items:
        key, val = str(key).strip(), str(val).strip()
        if key and val is not None:
            out[key] = val
    return out


def apply_user(text: str, lexicon: Any) -> str:
    entries = parse_lexicon(lexicon)
    for key in sorted(entries, key=len, reverse=True):
        pattern = re.escape(key)
        if key[:1].isalnum():
            pattern = r"(?<![\wЁё])" + pattern
        if key[-1:].isalnum():
            pattern += r"(?![\wЁё])"
        text = re.sub(pattern, lambda _m, v=entries[key]: v, text, flags=re.I)
    return text


# ------------------------------------------------------------------ шаги конвейера
def before_numbers(text: str) -> str:
    """Runs on raw text: roman numerals, «д. 5», «1920x1080», symbols with words."""
    text = ROMAN_CTX.sub(_roman_ctx, text)
    text = ROMAN_NAME.sub(_roman_name, text)
    text = PRE_ABBR_RE.sub(lambda m: PRE_ABBR_MAP.get(m.group(1).lower(), m.group(1)) + " ", text)
    for rx, words in SYMBOL_WORDS:
        text = rx.sub(words, text)
    return text


def number_forms(text: str) -> str:
    """Ordinals, years and «9 нояб.» — must run before plain numbers."""
    text = ORDINAL.sub(_ordinal, text)
    text = YEAR.sub(_year, text)
    text = MONTH_SHORT.sub(lambda m: f"{m.group(1)} {MONTH_ABBR[m.group(2).lower()]}" + _keep_dot(m), text)
    return DAY_MONTH.sub(_day_month, text)


def after_numbers(text: str) -> str:
    for rx, replacement in ABBR_RES:
        text = _abbr_sub(rx, replacement, text)
    for src, dst in LEFTOVER:
        text = text.replace(src, dst)
    return text
