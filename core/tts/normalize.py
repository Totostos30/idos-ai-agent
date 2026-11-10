"""Text → speakable Russian for TTS: drops markdown, links, code and emoji; reads numbers, dates, units and Latin."""
from __future__ import annotations

import re

DIGITS = ["ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
UNITS_M = [""] + DIGITS[1:]
UNITS_F = ["", "одна", "две"] + DIGITS[3:]
TEENS = ["десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать", "шестнадцать",
         "семнадцать", "восемнадцать", "девятнадцать"]
TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто"]
HUNDREDS = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот"]
SCALES = [(("тысяча", "тысячи", "тысяч"), True), (("миллион", "миллиона", "миллионов"), False),
          (("миллиард", "миллиарда", "миллиардов"), False), (("триллион", "триллиона", "триллионов"), False)]
DENOMS = {1: ("десятая", "десятых", "десятых"), 2: ("сотая", "сотых", "сотых"), 3: ("тысячная", "тысячных", "тысячных")}
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября",
          "декабря"]
ORD_DAY = ["", "первое", "второе", "третье", "четвёртое", "пятое", "шестое", "седьмое", "восьмое", "девятое", "десятое",
           "одиннадцатое", "двенадцатое", "тринадцатое", "четырнадцатое", "пятнадцатое", "шестнадцатое", "семнадцатое",
           "восемнадцатое", "девятнадцатое", "двадцатое"]
ORD_GEN = ["", "первого", "второго", "третьего", "четвёртого", "пятого", "шестого", "седьмого", "восьмого", "девятого"]
ORD_GEN_TEENS = ["десятого", "одиннадцатого", "двенадцатого", "тринадцатого", "четырнадцатого", "пятнадцатого",
                 "шестнадцатого", "семнадцатого", "восемнадцатого", "девятнадцатого"]
ORD_GEN_TENS = ["", "", "двадцатого", "тридцатого", "сорокового", "пятидесятого", "шестидесятого", "семидесятого",
                "восьмидесятого", "девяностого"]
UNIT_FORMS = {
    "%": ("процент", "процента", "процентов"),
    "°": ("градус", "градуса", "градусов"),
    "₽": ("рубль", "рубля", "рублей"),
    "руб": ("рубль", "рубля", "рублей"),
    "$": ("доллар", "доллара", "долларов"),
    "€": ("евро", "евро", "евро"),
    "км/ч": ("километр в час", "километра в час", "километров в час"),
    "км": ("километр", "километра", "километров"),
    "кг": ("килограмм", "килограмма", "килограммов"),
    "гб": ("гигабайт", "гигабайта", "гигабайт"),
    "мб": ("мегабайт", "мегабайта", "мегабайт"),
    "кб": ("килобайт", "килобайта", "килобайт"),
    "мс": ("миллисекунда", "миллисекунды", "миллисекунд"),
    "мин": ("минута", "минуты", "минут"),
    "сек": ("секунда", "секунды", "секунд"),
    "ч": ("час", "часа", "часов"),
}
FEMININE_UNITS = {"мс", "мин", "сек"}
LETTERS = dict(zip("abcdefghijklmnopqrstuvwxyz", [
    "эй", "би", "си", "ди", "и", "эф", "джи", "эйч", "ай", "джей", "кей", "эл", "эм", "эн", "оу", "пи", "кью", "ар", "эс",
    "ти", "ю", "ви", "дабл ю", "икс", "уай", "зет"]))
SINGLE = dict(zip("abcdefghijklmnopqrstuvwxyz", [
    "а", "б", "к", "д", "е", "ф", "г", "х", "и", "дж", "к", "л", "м", "н", "о", "п", "к", "р", "с", "т", "у", "в", "в",
    "кс", "и", "з"]))
DIGRAPHS = [("sch", "ш"), ("tch", "ч"), ("sh", "ш"), ("ch", "ч"), ("zh", "ж"), ("kh", "х"), ("th", "т"), ("ph", "ф"),
            ("ck", "к"), ("qu", "кв"), ("oo", "у"), ("ee", "и"), ("ea", "и"), ("ou", "ау"), ("ow", "оу"), ("ay", "эй"),
            ("ey", "эй"), ("oy", "ой"), ("yo", "йо"), ("ya", "я"), ("yu", "ю"), ("ye", "е"), ("ts", "ц"), ("wh", "в"),
            ("gh", "г")]
WORDS = {
    "telegram": "телеграм", "windows": "виндоус", "google": "гугл", "youtube": "ютуб", "chrome": "хром", "edge": "эдж",
    "firefox": "файрфокс", "discord": "дискорд", "steam": "стим", "spotify": "спотифай", "word": "ворд",
    "excel": "эксель", "powerpoint": "пауэрпоинт", "powershell": "пауэршелл", "python": "пайтон", "github": "гитхаб",
    "git": "гит", "groq": "грок", "openai": "опен эй ай", "gpt": "джи пи ти", "chatgpt": "чат джи пи ти",
    "claude": "клод", "notion": "ноушн", "zoom": "зум", "skype": "скайп", "whatsapp": "ватсап", "viber": "вайбер",
    "wi-fi": "вай фай", "wifi": "вай фай", "bluetooth": "блютус", "ok": "окей", "okay": "окей", "email": "имейл",
    "e-mail": "имейл", "online": "онлайн", "offline": "офлайн", "idos": "эйдос", "vs": "вэ эс", "code": "код",
    "visual": "вижуал", "studio": "студио", "microsoft": "майкрософт", "apple": "эппл", "android": "андроид",
    "iphone": "айфон", "json": "джейсон", "linux": "линукс", "yandex": "яндекс", "gmail": "джимейл",
    "outlook": "аутлук", "office": "офис", "explorer": "эксплорер", "cerebras": "церебрас", "gemini": "джемини",
    "llama": "лама", "qwen": "квен", "whisper": "виспер", "silero": "силеро", "store": "стор", "paint": "пэйнт",
    "notepad": "нотпад", "gb": "гигабайт", "mb": "мегабайт", "kb": "килобайт", "the": "зе", "and": "энд", "of": "оф",
    "to": "ту", "for": "фор", "with": "виз", "app": "апп", "web": "веб", "pro": "про", "max": "макс", "mini": "мини",
    "plus": "плюс", "free": "фри", "turbo": "турбо", "oss": "о эс эс", "obs": "о би эс", "vpn": "ви пи эн",
}
WORDS.update({
    "hello": "хэллоу", "hi": "хай", "world": "ворлд", "music": "мьюзик", "play": "плэй", "player": "плеер", "stop": "стоп",
    "start": "старт", "news": "ньюс", "new": "нью", "file": "файл", "files": "файлы", "update": "апдейт",
    "download": "даунлоад", "upload": "аплоад", "server": "сервер", "cloud": "клауд", "smart": "смарт", "phone": "фон",
    "ipad": "айпад", "mac": "мак", "macbook": "макбук", "browser": "браузер", "search": "сёрч", "home": "хоум",
    "page": "пейдж", "video": "видео", "live": "лайв", "stream": "стрим", "game": "гейм", "games": "геймс", "love": "лав",
    "you": "ю", "my": "май", "me": "ми", "is": "из", "it": "ит", "in": "ин", "on": "он", "time": "тайм", "night": "найт",
    "day": "дэй", "life": "лайф", "yes": "йес", "no": "ноу", "good": "гуд", "best": "бест", "top": "топ", "hit": "хит",
    "star": "стар", "dance": "дэнс", "rock": "рок", "pop": "поп", "rap": "рэп", "remix": "ремикс", "feat": "фит",
    "ft": "фит", "official": "офишл", "lyrics": "лирикс", "mode": "мод", "dark": "дарк", "light": "лайт", "team": "тим",
    "user": "юзер", "admin": "админ", "password": "пароль", "login": "логин", "account": "аккаунт", "settings": "сеттингс",
    "chat": "чат", "bot": "бот", "agent": "эйджент", "island": "айленд", "dynamic": "дайнамик", "assistant": "ассистент",
    "voice": "войс", "model": "модель", "key": "ки", "token": "токен", "limit": "лимит", "error": "эррор", "debug": "дебаг",
    "log": "лог", "backup": "бэкап", "cache": "кэш", "docker": "докер", "node": "ноуд", "npm": "эн пи эм", "rust": "раст",
    "tauri": "таури", "java": "джава", "javascript": "джаваскрипт", "typescript": "тайпскрипт", "react": "реакт",
    "nvidia": "энвидиа", "amd": "эй эм ди", "intel": "интел", "rtx": "эр тэ икс", "gtx": "джи ти икс", "geforce": "джифорс",
    "radeon": "радеон", "ryzen": "райзен", "core": "кор", "samsung": "самсунг", "xiaomi": "сяоми", "huawei": "хуавэй",
    "honor": "хонор", "sony": "сони", "lg": "эл джи", "asus": "асус", "lenovo": "леново", "acer": "асер", "hp": "эйч пи",
    "dell": "делл", "tesla": "тесла", "netflix": "нетфликс", "twitch": "твич", "tiktok": "тикток", "instagram": "инстаграм",
    "facebook": "фейсбук", "twitter": "твиттер", "reddit": "реддит", "wikipedia": "википедия", "amazon": "амазон",
    "ozon": "озон", "wildberries": "вайлдберриз", "avito": "авито", "vk": "вэ ка", "vkontakte": "вконтакте",
    "rutube": "рутуб", "kinopoisk": "кинопоиск", "sber": "сбер", "tinkoff": "тинькофф", "epic": "эпик",
    "minecraft": "майнкрафт", "dota": "дота", "valorant": "валорант", "fortnite": "фортнайт", "gta": "джи ти эй",
    "counter": "каунтер", "strike": "страйк", "cs": "кс", "playstation": "плейстейшн", "xbox": "иксбокс",
    "nintendo": "нинтендо", "adobe": "адоби", "photoshop": "фотошоп", "premiere": "премьер", "figma": "фигма",
    "blender": "блендер", "unity": "юнити", "unreal": "анриал", "engine": "энджин", "vlc": "ви эл си", "winrar": "винрар",
    "zip": "зип", "rar": "рар", "exe": "экзе", "mp3": "эм пэ три", "mp4": "эм пэ четыре", "mp": "эм пэ", "jpg": "джипег",
    "jpeg": "джипег", "png": "пэ эн гэ", "gif": "гиф", "wav": "вав", "pdf": "пэ дэ эф", "docx": "докс", "txt": "тэ экс тэ",
    "http": "аш тэ тэ пэ", "https": "аш тэ тэ пэ эс", "www": "дабл ю дабл ю дабл ю", "dns": "ди эн эс", "hdmi": "эйч ди эм ай",
    "usb": "ю эс би", "ssd": "эс эс ди", "hdd": "эйч ди ди", "cpu": "си пи ю", "gpu": "джи пи ю", "ram": "рам",
    "nasa": "наса", "ios": "ай оу эс", "macos": "мак оу эс", "wi": "вай", "fi": "фай", "ai": "эй ай", "it": "ай ти",
    "ui": "ю ай", "ux": "ю экс", "faq": "фак", "seo": "сео", "crm": "си ар эм", "sms": "эс эм эс", "pin": "пин",
    "qr": "кью ар", "gps": "джи пи эс", "nfc": "эн эф си", "led": "лед", "oled": "олед", "4k": "четыре кей", "hd": "эйч ди",
    "full": "фулл", "ultra": "ультра", "air": "эйр", "lite": "лайт", "note": "ноут", "galaxy": "гэлакси", "pixel": "пиксель",
    "yandex music": "яндекс музыка", "alice": "алиса", "siri": "сири", "alexa": "алекса", "copilot": "копайлот",
    "deepseek": "дипсик", "mistral": "мистраль", "anthropic": "антропик", "meta": "мета", "kimi": "кими",
    "premium": "премиум", "plus": "плюс", "ok google": "окей гугл", "desktop": "десктоп", "screenshot": "скриншот",
    "click": "клик", "enter": "энтер", "escape": "эскейп", "esc": "эскейп", "tab": "таб", "shift": "шифт", "ctrl": "контрол",
    "alt": "альт", "space": "спейс", "delete": "делит", "win": "вин", "fn": "эф эн", "api": "эй пи ай", "url": "ю ар эл",
    "html": "аш тэ эм эль", "css": "си эс эс", "sql": "эс кью эл", "id": "ай ди", "pc": "пи си", "tv": "ти ви",
})
LONG_VOWEL = {"a": "эй", "e": "и", "i": "ай", "o": "оу", "u": "ю", "y": "ай"}
PATTERNS = [("tion", "шн"), ("sion", "жн"), ("ture", "чер"), ("ight", "айт"), ("igh", "ай"), ("ough", "оу"),
            ("augh", "о"), ("dge", "дж"), ("ai", "эй"), ("oa", "оу"), ("au", "о"), ("aw", "о"), ("ew", "ью")]
SYMBOLS = [("→", " — "), ("&", " и "), ("=", " равно "), ("+", " плюс "), ("/", " "), ("\\", " "), ("№", " номер "),
           ("#", " "), ("•", ", "), ("_", " "), ("«", ""), ("»", ""), ('"', ""), ("*", " "), ("~", " примерно "),
           ("|", ", ")]

CODE_BLOCK = re.compile(r"```.*?(?:```|$)", re.S)
INLINE_CODE = re.compile(r"`([^`\n]*)`")
MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
URL = re.compile(r"(?:https?:/" r"/|www\.)[^\s)\]>]+", re.I)
WIN_PATH = re.compile(r"\b[A-Za-z]:\\(?:[^\s\\]+\\)*([^\s\\]+)")
EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D]")
LIST_MARK = re.compile(r"^\s{0,3}(?:#{1,6}|>+|[-*+•]|\d{1,2}[.)])\s+")
CELSIUS = re.compile(r"°\s?[CcСс](?![A-Za-zА-Яа-яЁё])")
LATIN = re.compile(r"[A-Za-z][A-Za-z'’]*(?:-[A-Za-z][A-Za-z'’]*)*")
DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
VERSION = re.compile(r"\b\d+(?:\.\d+){2,}\b")
SHORT_DATE = re.compile(r"(?<![\d.,])(\d{2})\.(\d{2})(?![\d.,])")
TIME = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
GROUPED = re.compile(r"(?<![\d.,])(\d{1,3})((?:[ \u00a0]\d{3})+)(?!\d)")
ORDINAL = re.compile(r"(\d+)-(?:й|го|я|е|ое|ый|ий|ая|ой|му|м|х)\b")
GLUED = re.compile(r"(?<=\d)(?=[A-Za-zА-Яа-яЁё])|(?<=[A-Za-zА-Яа-яЁё])(?=\d)")
PREFIX_MONEY = re.compile(r"([$€₽])\s?(\d+(?:[.,]\d+)?)")
NEGATIVE = re.compile(r"(?<![\w])[-−](?=\d)")
NUMBER = re.compile(r"(\d+)(?:[.,](\d+))?(?:\s?(%|°|₽|руб|\$|€|км/ч|км|кг|гб|мб|кб|мс|мин|сек|ч)(?![А-Яа-яЁёA-Za-z]))?",
                    re.I)
ALLOWED = re.compile(r"[^0-9А-Яа-яЁё .,!?:;()\-–—…]")


def plural(n: int, forms: tuple[str, str, str]) -> str:
    n = abs(int(n)) % 100
    if 10 < n < 20:
        return forms[2]
    n %= 10
    return forms[0] if n == 1 else forms[1] if 2 <= n <= 4 else forms[2]


def _triple(n: int, feminine: bool) -> list[str]:
    rest = n % 100
    words = [HUNDREDS[n // 100]]
    if 10 <= rest < 20:
        words.append(TEENS[rest - 10])
    else:
        words += [TENS[rest // 10], (UNITS_F if feminine else UNITS_M)[rest % 10]]
    return [w for w in words if w]


def number_words(n: int, feminine: bool = False) -> str:
    """Cardinal in words: 2026 → «две тысячи двадцать шесть»."""
    n = int(n)
    if n < 0:
        return "минус " + number_words(-n, feminine)
    if n == 0:
        return "ноль"
    if n >= 10 ** 15:
        return " ".join(DIGITS[int(d)] for d in str(n))
    groups = []
    while n:
        groups.append(n % 1000)
        n //= 1000
    words: list[str] = []
    for index in range(len(groups) - 1, -1, -1):
        value = groups[index]
        if not value:
            continue
        if index == 0:
            words += _triple(value, feminine)
            continue
        forms, fem = SCALES[index - 1]
        if value == 1 and index == 1 and not words:
            words.append(forms[0])
        else:
            words += _triple(value, fem) + [plural(value, forms)]
    return " ".join(words)


def decimal_words(whole: str, frac: str) -> str:
    w = int(whole or 0)
    if len(frac) in DENOMS:
        f = int(frac)
        return (f"{number_words(w, True)} {plural(w, ('целая', 'целых', 'целых'))} "
                f"{number_words(f, True)} {plural(f, DENOMS[len(frac)])}")
    return number_words(w) + " запятая " + " ".join(DIGITS[int(d)] for d in frac)


def day_words(day: int) -> str:
    if day <= 20:
        return ORD_DAY[day]
    if day == 30:
        return "тридцатое"
    return ("двадцать " if day < 30 else "тридцать ") + ORD_DAY[day % 10]


def year_words(year: int) -> str:
    """Genitive ordinal for «… года»: 2026 → «две тысячи двадцать шестого»."""
    rest = year % 100
    if rest == 0:
        return "двухтысячного" if year == 2000 else number_words(year)
    head = number_words(year - rest) if year - rest else ""
    if 10 <= rest < 20:
        tail = ORD_GEN_TEENS[rest - 10]
    elif rest % 10 == 0:
        tail = ORD_GEN_TENS[rest // 10]
    else:
        tail = (TENS[rest // 10] + " " if rest >= 20 else "") + ORD_GEN[rest % 10]
    return f"{head} {tail}".strip()


def _date(day: int, month: int, year: int | None, raw: str) -> str:
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return raw
    text = f"{day_words(day)} {MONTHS[month - 1]}"
    return f"{text} {year_words(year)} года" if year else text


def _time(match: re.Match) -> str:
    hours, minutes = int(match.group(1)), int(match.group(2))
    if minutes == 0:
        tail = "ноль ноль"
    elif minutes < 10:
        tail = "ноль " + DIGITS[minutes]
    else:
        tail = number_words(minutes, True)
    return f"{number_words(hours)} {tail}"


def _number(match: re.Match) -> str:
    whole, frac, unit = match.group(1), match.group(2), (match.group(3) or "").lower()
    forms = UNIT_FORMS.get(unit)
    if frac is not None:
        return decimal_words(whole, frac) + (" " + forms[1] if forms else "")
    if len(whole) > 1 and whole.startswith("0"):
        words = " ".join(DIGITS[int(d)] for d in whole)
    else:
        words = number_words(int(whole), unit in FEMININE_UNITS)
    return words + (" " + plural(int(whole[-2:]), forms) if forms else "")


def expand_numbers(text: str) -> str:
    text = DATE.sub(lambda m: _date(int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(0)), text)
    text = ISO_DATE.sub(lambda m: _date(int(m.group(3)), int(m.group(2)), int(m.group(1)), m.group(0)), text)
    text = VERSION.sub(lambda m: " точка ".join(number_words(int(p)) for p in m.group(0).split(".")), text)
    text = SHORT_DATE.sub(lambda m: _date(int(m.group(1)), int(m.group(2)), None, m.group(0)), text)
    text = TIME.sub(_time, text)
    text = GROUPED.sub(lambda m: m.group(1) + re.sub(r"\D", "", m.group(2)), text)
    text = LX.number_forms(text)
    text = GLUED.sub(" ", text)
    text = PREFIX_MONEY.sub(r"\2 \1", text)
    text = NEGATIVE.sub("минус ", text)
    return LX.NUMBER.sub(LX.number_with_unit, text)


def translit(word: str) -> str:
    """Rough English → Russian reading: «name» → «нэйм», «station» → «стэйшн», «happy» → «хэппи»."""
    w, tail = word.lower(), ""
    magic = re.search(r"(?<![aeiou])([aeiou])([bcdfgklmnprstvz])e$", w)
    if magic and len(w) >= 4:
        consonant = {"c": "с", "g": "дж", "s": "с"}.get(magic.group(2), SINGLE[magic.group(2)])
        tail, w = LONG_VOWEL[magic.group(1)] + consonant, w[:magic.start()]
    elif w.endswith("ous") and len(w) > 4:
        tail, w = "ас", w[:-3]
    elif re.search(r"[^aeiou]le$", w) and len(w) > 3:
        tail, w = "л", w[:-2]
    elif re.search(r"[^aeiouy]y$", w) and len(w) > 1:
        tail, w = ("ай" if len(w) <= 3 else "и"), w[:-1]
    out, i = [], 0
    while i < len(w):
        for src, dst in PATTERNS + DIGRAPHS:
            if w.startswith(src, i):
                out.append(dst)
                i += len(src)
                break
        else:
            ch, nxt = w[i], w[i + 1:i + 2]
            if ch == "c" and nxt in ("e", "i", "y"):
                out.append("с")
            elif ch == "g" and nxt in ("e", "i") and i > 0:
                out.append("дж")
            elif ch == "y" and i == 0:
                out.append("й")
            elif ch == "x":
                out.append("кс")
            elif ch == "j":
                out.append("дж")
            elif not (ch == "e" and not nxt and not tail and len(word) > 3):
                out.append(SINGLE.get(ch, ""))
            i += 1
    return "".join(out) + tail


def _latin_word(word: str) -> str:
    low = word.lower().replace("’", "").replace("'", "")
    if low in WORDS:
        return WORDS[low]
    if len(low) == 1:
        return LETTERS.get(low, "")
    if word.isupper() and len(word) <= 5:
        return " ".join(LETTERS.get(ch, "") for ch in low)
    return translit(low)


def latin_to_cyrillic(text: str) -> str:
    text = re.sub(r"\bvs\s?code\b", "вэ эс код", text, flags=re.I)

    def repl(match: re.Match) -> str:
        token = match.group(0)
        if token.lower() in WORDS:
            return WORDS[token.lower()]
        parts = []
        for part in token.split("-"):
            if part.lower() in WORDS or not part:
                parts.append(part)
            else:
                parts += re.sub(r"(?<=[a-z])(?=[A-Z])", " ", part).split()
        return " ".join(_latin_word(part) for part in parts if part)

    return LATIN.sub(repl, text)


def clean(text: str) -> str:
    """Drop markup that should not be read aloud; every line becomes a sentence."""
    text = CODE_BLOCK.sub(" (код опущен). ", str(text or ""))
    text = INLINE_CODE.sub(r"\1", text)
    text = MD_LINK.sub(r"\1", text)
    text = URL.sub(" ссылка ", text)
    text = WIN_PATH.sub(r"\1", text)
    text = EMOJI.sub("", text)
    lines = []
    for line in text.splitlines():
        line = re.sub(r"\*\*|__|~~", "", LIST_MARK.sub("", line)).strip(" \t|")
        if line:
            lines.append(line if line[-1] in ".!?…:;," else line + ".")
    return " ".join(lines)


def normalize(text: str, max_chars: int = 700, full: bool = True, lexicon=None) -> str:
    """Speakable text. full=True also reads numbers, units, abbreviations, acronyms and Latin aloud (Silero cannot).
    lexicon — user pronunciation dictionary (dict or «слово = как читать» lines), applied first."""
    text = clean(text)
    if lexicon:
        text = LX.apply_user(text, lexicon)
    if full:
        text = LX.before_numbers(CELSIUS.sub("°", text))
        text = LX.after_numbers(expand_numbers(text))
        text = LX.acronyms(latin_to_cyrillic(text))
    for src, dst in SYMBOLS:
        text = text.replace(src, dst)
    if full:
        text = ALLOWED.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.!?;:…])", r"\1", text)
    text = re.sub(r"([,.!?;:])(?:\s*[,.;:])+", r"\1", text)
    if len(text) > max_chars:
        cut = max(text.rfind(". ", 0, max_chars), text.rfind("! ", 0, max_chars), text.rfind("? ", 0, max_chars))
        text = text[:cut + 1] if cut > max_chars // 3 else text[:max_chars].rsplit(" ", 1)[0] + "…"
    return text


def sentences(text: str, limit: int = 260) -> list[str]:
    """Synthesis chunks: the first sentence alone (speech starts sooner), then up to `limit` characters each."""
    pieces: list[str] = []
    for part in re.split(r"(?<=[.!?…;])\s+", text.strip()):
        while len(part) > limit:
            cut = part.rfind(", ", 0, limit)
            if cut < limit // 3:
                cut = part.rfind(" ", 0, limit)
            if cut < limit // 3:
                cut = limit
            pieces.append(part[:cut + 1].strip())
            part = part[cut + 1:].strip()
        if part:
            pieces.append(part)
    chunks: list[str] = []
    for piece in pieces:
        if len(chunks) > 1 and len(chunks[-1]) + len(piece) + 1 <= limit:
            chunks[-1] += " " + piece
        else:
            chunks.append(piece)
    return chunks


from . import lexicon as LX  # noqa: E402  (lexicon uses the helpers above)
