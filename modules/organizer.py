#!/usr/bin/env python3
"""
Умная сортировка плейлиста.

1. Определяет русскоязычные каналы (по названию, группе, tvg-атрибутам; спорные — Qwen по названию).
2. Русскоязычные: каналы со сдвигом времени (Россия +4 и т. п.) выделяются в отдельные группы по часовым поясам,
   остальные раскладываются по темам (правила + Qwen для неясных), затем режутся на группы по 40-50 каналов
   с творческими названиями (Qwen, запасной вариант — готовые названия).
3. Нерусскоязычные уходят в отдельный список (группы по исходному group-title, тоже не больше 50 каналов).
"""
import json
import math
import os
import re
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor, as_completed

from modules.m3u_utils import split_extinf, set_group, canonical_group

RU_FLAG = "🇷🇺"
WORLD = "🌍"
CHUNK_MAX = 50       # максимум каналов в группе
MIN_GROUP = 6       # тема меньше этого размера сливается в «сборную солянку»
# Куда сливается слишком маленькая тема (иначе — в «Разное»)
MERGE_TO = {"religion": "docs", "series": "movies", "entertain": "movies", "regional": "other"}
BATCH = 20           # каналов в одном запросе к модели

# Слова 18+ для ЖЁСТКОГО правила по названию (не зависит от языка канала).
ADULT_PAT = (r"\bxxx\b|18\+|\+18|эротик|erotic|playboy|\badult\b|brazzers|hustler|penthouse|порн|porn|"
             r"\banal\b|\bteens?\b|transsexual|transsex|транссексуал|\bтранс\b|deepthroat|\bmilf\b|\bsex\b|\bсекс|bangbros|"
             r"realitykings|pornhub|xhamster|redlight|\bdorcel\b|\bvivid\b")
ADULT_RE = re.compile(ADULT_PAT, re.I)

# Домены провайдеров и зоны, по которым канал считаем русскоязычным.
RU_DOMAIN_SUBSTR = ("klaipedalt", "akciatv")   # вхождения в хост
RU_DOMAIN_TLD = (".ru", ".su", ".xn--p1ai", ".black", ".red")                        # зоны-окончания хоста


def url_looks_russian(url):
    """True, если хост URL — русский провайдер/зона (.ru, klaipedalt, akciatv)."""
    try:
        host = (urlsplit(url or "").hostname or "").lower()
    except Exception:
        host = ""
    if not host:
        # адрес без схемы (klaipedalt.ru/path) или нестандартная запись
        m = re.match(r"\s*(?:[a-z][a-z0-9+.\-]*://)?(?:[^/@]*@)?([^/:?#\s]+)", url or "", re.I)
        host = (m.group(1) if m else "").lower()
    if not host:
        return False
    return host.endswith(RU_DOMAIN_TLD) or any(s in host for s in RU_DOMAIN_SUBSTR)


# ключ: (эмодзи, название темы, regex по "имя + группа", запасные названия групп)
CATS = {
    "federal": ("📡", "Федеральные каналы",
                r"^(первый|1 ?канал|россия|нтв\b|рен[ -]?тв|ren ?tv|тв[ -]?центр|отр\b|звезда|мир\b|домашний|пятница|"
                r"канал один|тнт|стс|ю\b|че\b|тв-?3\b|суббота|перец|2[xх]2|спас\b)",
                ["Главный эфир", "Эфир всей страны", "Классика телеэкрана", "Телевизор как раньше"]),
    "news": ("📰", "Новости",
             r"новост|\bnews\b|\bрбк\b|\brbc\b|россия[ -]?24|вести|euronews|иньюс|i-news|\bлайф\b|мир[ -]?24|"
             r"бизнес|business|\bcnn\b|\bbbc\b|al jazeera|аль-джазира|настоящее время|\bдождь\b|царьград",
             ["Главное сейчас", "В курсе событий", "Новостной пульс", "Лента дня", "Горячие новости"]),
    "movies": ("🎬", "Кино",
               r"кино|kino|flix|amedia|фильм|movie|\bfilm\b|cinema|hollywood|боевик|комеди|детектив|ужас|триллер|фантастик|"
               r"мосфильм|иллюзион|ностальг",
               ["Кинозал на диване", "Большой экран", "Вечер в кино", "Золотая коллекция", "Киноманам"]),
    "series": ("📺", "Сериалы",
               r"сериал|series|дорам|\bdrama\b|мелодрам",
               ["Сериальный марафон", "Сезон за сезоном", "Ещё одну серию", "Сериальный запой"]),
    "entertain": ("🎭", "Развлечения и шоу",
                  r"юмор|comedy|\bшоу\b|\bshow\b|квн|развлек|humor|ржак|смех|entertain",
                  ["Смех и шоу", "Весёлая компания", "Развлекательный микс", "Хорошее настроение"]),
    "sport": ("⚽", "Спорт",
              r"спорт|sport|матч|\bmatch\b|футбол|football|хоккей|hockey|бокс|\bufc\b|eurosport|\bкхл\b|формула|"
              r"\bf1\b|боец|\bnba\b|\bnhl\b|\bnfl\b|теннис|tennis|golf|гольф|единоборств|motorsport|автоспорт|экстрим",
              ["Азарт и адреналин", "Арена чемпионов", "Спортивный драйв", "Победный свисток", "До финального гонга"]),
    "kids": ("🧸", "Детям",
             r"детск|дети|\bkids?\b|мульт|cartoon|карусель|няня|тлум|disney|nickelodeon|\bnick\b|малыш|"
             r"союзмульт|уникум|\bbaby|jim jam|бибигон|гулливер|детям",
             ["Мультландия", "Для маленьких непосед", "Весёлая переменка", "Сказочная страна", "Детская планета"]),
    "music": ("🎵", "Музыка",
              r"музык|\bmusic\b|\bmtv\b|\bmuz\b|муз-?тв|\bru\.?tv\b|bridge|europa plus|шансон|\bvh1\b|clubbing|"
              r"\bhit\b|караоке|karaoke|\bdance\b|джаз|\bjazz\b|\brock\b|\brap\b",
              ["Музыкальная волна", "Хит-парад", "Заряд музыки", "Звуки и клипы", "Громче!"]),
    "docs": ("🌍", "Познавательные",
             r"discovery|national|nat ?geo|investigation|hystory|animal planet|истори|history|наука|science|познават|документал|docu|travel|"
             r"путешеств|техно|моя планета|охота|рыбалк|viasat|nature|планета|здоровье|health|кулинар|\bfood\b|cooking",
             ["Мир вокруг", "Познаём вместе", "Любопытным", "Загадки планеты", "Умный канал"]),
    "religion": ("🕊️", "Религия",
                 r"православ|ислам|церк|христиан|muslim|quran|коран|hidayat|religion|религ|\bистина\b",
                 ["Свет и вера", "Духовный мир"]),
    "regional": ("📍", "Региональные",
                 r"$^",  # только через модель
                 ["Родной край", "Свои люди", "Рядом с домом"]),
    "other": ("🎲", "Разное",
              r"$^",
              ["Сборная солянка", "Всего понемногу", "Остальное", "Что-нибудь ещё"]),
    "radio": ("📻", "Радио",
              r"радио|\bradio\b|\bfm\b",
              ["В эфире радио", "Радиоволна"]),
    "adult": ("🔞", "Для взрослых",
              ADULT_PAT,
              ["После полуночи", "Только для взрослых"]),
}

# порядок определения темы по правилам
RULE_ORDER = ["adult", "radio", "kids", "sport", "news", "series", "movies", "music", "docs",
              "religion", "entertain", "federal"]
# порядок групп в итоговом файле
OUT_ORDER = ["federal", "news", "movies", "series", "entertain", "sport", "kids", "music", "docs",
             "religion", "regional", "other", "shift", "radio", "adult"]
# нумерация для запроса к модели
LLM_CATS = ["news", "movies", "series", "sport", "kids", "music", "docs", "entertain",
            "federal", "religion", "regional", "other"]
NEVER_POOL = {"adult", "radio"}

_CATS_RE = {k: re.compile(v[2], re.I) for k, v in CATS.items()}

# Исходный group-title из плейлиста (после склейки синонимов) -> тема. Надёжнее, чем regex по названию.
# Правила для известных семейств каналов: без модели такие названия (Yosso TV Nature, Z!Horror HD ...)
# иначе не понять. Проверяются по названию раньше общих regex. Ключи — темы из CATS.
_FAM = r"^(?:yosso\s?tv|z!)\s*"
_NAME_HINTS = [
    (re.compile(_FAM + r"(?:music|rock|hits?)\b"), "music"),
    (re.compile(_FAM + r"(?:nature|science|travel|history|discovery|animal)"), "docs"),
    (re.compile(_FAM + r"disney|" + _FAM + r"kids"), "kids"),
    (re.compile(_FAM + r"(?:drama|serial|sitcom)"), "series"),
    (re.compile(_FAM + r"smile"), "entertain"),
    (re.compile(_FAM + r"(?:action|adventure|thriller|horror|crime|detective|fantasy|military|comedy|family|"
                r"channel|best|oblivion|adrenaline|vhs|sci-?fi)"), "movies"),
    (re.compile(r"^top\s?budo"), "sport"),
    (re.compile(r"^top\s?80"), "music"),
    (re.compile(r"^top\s?cinema|^cineman|^\.?sci-?fi\b"), "movies"),
]
_GROUP_CAT = {"Музыка": "music", "Новости": "news", "Кино": "movies", "Дети": "kids", "Спорт": "sport",
              "Документальные": "docs", "Путешествия": "docs", "Развлекательные": "entertain",
              "Кухня": "docs", "Религия": "religion", "Для взрослых": "adult"}

# Группы иностранных каналов по языку, который VL-модель определила по кадру (подсказка).
LANG_GROUP = {
    "DE": "🇩🇪 Германия", "EN": "🇬🇧 Английские", "AR": "🌍 Арабские", "TR": "🇹🇷 Турецкие",
    "FR": "🇫🇷 Французские", "IT": "🇮🇹 Итальянские", "ES": "🇪🇸 Испанские", "PL": "🇵🇱 Польские",
    "UA": "🇺🇦 Украинские",
}

SHIFT_REGIONS = {
    -1: "Калининград", 1: "Самара", 2: "Екатеринбург", 3: "Омск", 4: "Красноярск и Новосибирск",
    5: "Иркутск", 6: "Якутск", 7: "Владивосток", 8: "Магадан", 9: "Камчатка",
}
_SHIFT_RE = re.compile(r"(?:(?<=\s)|(?<=\()|(?<=msk)|(?<=мск))([+\-−])\s?(\d{1,2})(?=\s|\)|$|ч\b|h\b)", re.I)

_CYR = re.compile(r"[А-Яа-яЁё]")
# буквы украинского, белорусского, казахского и т. п. алфавитов, которых нет в русском
_UKR = re.compile(r"[ІіЇїЄєҐґЎўӘәҒғҚқҢңӨөҰұҮүҺһ]")
# латиница с диакритикой (ä ö ü ß é ñ ...): точно не русский канал
_LATIN_EXT = re.compile(r"[\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u00ff\u0100-\u017f]")
# явные признаки другой страны или языка в названии или группе
_NONRU_NAME = re.compile(
    r"deutsch|german|\bard\b|\bzdf\b|\brtl\b|sat\.?1|pro ?7\b|kabel ?eins|\borf\b|\bsrf\b|3sat|"
    r"tagesschau|super(?:rtl)?\b|sixx\b|tele[ -]?(5|berlin|club|de)\b|\bpuls\b|\bdaserste\b|"
    r"servus ?tv|joyn\b|\bwdr\b|\b(?:ndr|mdr|swr|rbb)\b|\bn-tv\b|\barte\b|\bsky\b|\bdhl\b|\bbundesliga\b|\bzero\b|"
    # немецкие слова/топики в названиях и группах (с границами слов)
    r"\bhobby\b|\btier\b|\bheimatkino\b|\bvolksmusik\b|\bschlager\b|\bnachrichten\b|\bwetter\b|"
    r"\breportage\b|\bdoku(?:mentation)?\b|\bserien\b|\bfilme\b|"
    r"\ba21 network\b|\b9x tashan\b|\b4fun\b|\bal emarat\b|\bal hadath\b|\babc spark\b", re.I)
# Названия стран и языков. У канала с кириллицей в названии («Футбол. Англия», «Украина 24»)
# это только слабый признак, у латинского названия — сильный.
_COUNTRY_WORD = re.compile(
    r"qazaq|kazakh|kazakstan|казах|қазақ|немецк|германи|украин|ukrain|турецк|turk|"
    r"польск|polish|англий|english|француз|french|итальянск|italian|испанск|spanish|"
    r"\bitaly\b|\bitalia\b|germany|deutschland|\bfrance\b|\bspain\b|espa[nñ]a|portugal|netherlands|nederland|holland|"
    r"austria|[oö]sterreich|switzerland|schweiz|suisse|\bpoland\b|polska|greece|turkey|brazil|brasil|mexico|"
    r"\bindia\b|japan|\bchina\b|korea|israel|bulgaria|romania|serbia|croatia|hungary|czech|slovak|sweden|norway|"
    r"denmark|finland|britain|\b(?:usa|uk)\b", re.I)
# Украина и Беларусь -> отдельная группа «СНГ» (страны и языки в названии/группе, буквы укр./бел. алфавита)
_CIS_WORD = re.compile(
    r"украин|україн|ukrain|беларус|белорус|білорус|belarus|казах|қазақ|kazakh|qazaq|"
    r"молдов|moldov|армени|армян|armenia|азербайдж|azerbaij|узбекистан|uzbekistan|кыргыз|kyrgyz|таджик|tajik|туркмен|"
    r"латви|latvia|литв|lithuania|lietuv|эстони|estonia|eesti", re.I)
_CIS_LETTERS = re.compile(r"[ІіЇїЄєҐґЎўӘәҒғҚқҢңӨөҰұҮүҺһ]")   # укр., бел., каз. буквы
_KZ_ONLY = re.compile(r"[ӘәҒғҚқҢңӨөҰұҮүҺһ]")
# Зоны СНГ и Прибалтики: каналы не уходят в иностранные, а собираются в группу «СНГ»
_CIS_TLD = {"ua", "by", "kz", "md", "am", "az", "uz", "kg", "tj", "tm", "ge", "lv", "lt", "ee"}
_CIS_COUNTRY = _CIS_TLD | {"ukraine", "украина", "belarus", "беларусь", "kazakhstan", "казахстан", "moldova",
                           "молдова", "armenia", "армения", "latvia", "lithuania", "estonia"}
# Зоны чужих стран: по домену потока страна определяется однозначно (.com/.net/.tv и т. п. — нейтральны)
FOREIGN_TLD = {"at", "de", "ch", "li", "nl", "be", "lu", "fr", "it", "es", "pt", "pl", "cz", "sk", "hu", "ro", "bg",
               "rs", "hr", "si", "ba", "mk", "al", "me", "gr", "cy", "tr", "uk", "gb", "ie", "dk", "se", "no", "fi",
               "is", "il", "ae", "sa", "eg", "ma", "dz", "tn", "lb", "iq", "ir", "in", "pk", "bd", "cn", "jp", "kr",
               "tw", "hk", "th", "vn", "id", "my", "ph", "sg", "au", "nz", "za", "ng", "ke", "br", "mx", "ar", "cl",
               "co", "pe", "ve", "uy", "us", "ca"}


# Суффикс страны в конце названия «4Fun TV PL», «9X Tashan IN», «AMC CA», «Al Hadath AE» (заглавными, отдельным словом; далее может стоять HD).
_NAME_CC = re.compile(r"(?<=\s)([A-Z]{2})(?:\s*\(?(?:HD|FHD|SD|4K)\)?)?\s*$")
# неоднозначные коды (ID = Investigation Discovery, AM/FM и т. п.) исключены
_NAME_CC_FOREIGN = FOREIGN_TLD - {"id", "is", "no", "me", "my", "co", "al", "li", "ng"}
_NAME_CC_CIS = {"ua", "by", "kz", "md", "az", "uz", "kg", "tj", "lv", "lt", "ee"}


def _name_cc(name):
    m = _NAME_CC.search(name or "")
    return m.group(1).lower() if m else ""


def _host_tld(url):
    """Доменная зона хоста из URL ('' для IP-адресов и хостов без точки)."""
    try:
        host = (urlsplit(url or "").hostname or "").lower()
    except Exception:
        host = ""
    if not host:
        m = re.match(r"\s*(?:[a-z][a-z0-9+.\-]*://)?(?:[^/@]*@)?([^/:?#\s]+)", url or "", re.I)
        host = (m.group(1) if m else "").lower()
    host = host.rstrip(".")
    if "." not in host:
        return ""
    tld = host.rsplit(".", 1)[1]
    return "" if tld.isdigit() else tld
# чисто немецкие каналы, определяемые по точному совпадению имени (после нормализации)
_DE_NAMES = {
    "das erste", "zdf", "zdfneo", "3sat", "kika", "phoenix", "rtl", "rtll", "rtl2", "rtlup",
    "sat1", "pro7", "prosieben", "kabel1", "kabeleins", "sixx", "tele5", "superrtl",
    "vox", "n-tv", "ntv", "welt", "hr", "mdr", "ndr", "rbb", "swr", "wdr", "br", "arte",
    "servustv", "puls4", "atv", "orf1", "orf2", "joyn", "maxdome", "sportal", "sport1",
    "dazn", "einsplus", "one", "zdfkultur", "zdfinfo", "zendern", "nickde", "disneyde",
}
# tvg-id с немецким/австрийским суффиксом без точки: zdf_de, orf_at ...
_TVGID_UNDER_CC = re.compile(r"[_.-](de|at|ch)[_.-]?$")
# суффикс страны в tvg-id (ZDF.de, Khabar.kz)
_TVGID_CC = re.compile(r"\.(ru|de|at|ch|kz|ua|uk|gb|us|fr|it|es|tr|pl|pt|nl|be|az|am|ge|uz|lv|lt|ee|md|by|kg|tj|tm|il|ae|sa|eg|in|cn|jp|kr|br|mx|ca|au)(?:@\w+)?$")
_RU_LATIN = re.compile(r"russia|rossiya|russian|match ?tv|ren ?tv|pyatnitsa|zvezda|karusel|domashniy|muz-?tv|"
                       r"mir ?24|perviy|pervyj|1 ?kanal", re.I)
_FOREIGN_GROUP = re.compile(r"\b(germany|deutsch|german|france|french|italy|italia|spain|turk|usa|\buk\b|english|arab|"
                            r"ukrain|poland|polska|portug|brasil|india|china|japan|korea|israel|netherland|sweden|norway|"
                            r"denmark|finland|greece|bulgaria|romania|serbia|croatia|albania|armenia|georgia|azerbaij|"
                            r"uzbek|latvia|lithuan|eston|moldov)", re.I)
_QUALITY = re.compile(r"\b(hd|fhd|uhd|4k|sd|hevc|orig|оригинал)\b|\(.*?\)|\[.*?\]", re.I)
_RU_LANG = re.compile(r"\b(ru|rus|russian)\b|русск", re.I)
_RU_COUNTRY = {"ru", "rus", "russia", "россия", "рф"}
# известные русскоязычные каналы, записанные латиницей (EUROPA PLUS, RU.TV и т. п.)
_RU_BRANDS = re.compile(
    r"europa\s?plus|\bru[ ._-]?tv\b|shanson|radika|record\s?(tv|dance)\b|russkij|rossijsk|"
    r"\bren\b|match!?\s?(tv|arena|football|boets|fighting|igra|premier|planeta|live)?\b|"
    r"karusel|lubimoe|lyubimoe|\bnashe\b|otvet\b|zvezda|domashni|pyatnica|piatnica|subbota|"
    r"perec\b|\btnt\b|\bsts\b|muz\s?tv|tvcentr|obshchestvo|moste?\b|"
    r"(discovery|nat\s?geo|history|animal\s?planet|via?sat|paramount|cartoon|nick(elodeon)?)"
    r"\s?\b(russia|rus|ru|hd\s?ru)\b", re.I)


def _norm(name):
    return re.sub(r"\s+", " ", _QUALITY.sub("", name.lower())).strip()


def _chunk_sizes(n):
    k = max(1, math.ceil(n / CHUNK_MAX))
    base, extra = divmod(n, k)
    return [base + (1 if i < extra else 0) for i in range(k)]


def _clean_title(text):
    """Название группы от модели: первая строка, без эмодзи/кавычек, 2-4 слова, по-русски."""
    if not text:
        return None
    s = text.strip().splitlines()[0]
    s = re.sub(r"[^\w\s\-:!&,.]", "", s, flags=re.U).strip(" -:,.")
    s = re.sub(r"\s+", " ", s)
    words = s.split()
    if not words:
        return None
    s = " ".join(words[:4])
    if len(s) < 3 or len(s) > 34 or len(_CYR.findall(s)) < 3:
        return None
    return s[0].upper() + s[1:]


class ExceptionList:
    """
    Слова из except.md. Канал считается русскоязычным (не удаляется и не уходит в иностранные),
    если его НАЗВАНИЕ начинается с такого слова ИЛИ слово встречается в названии целиком.
    Регистр не важен.
    Короткие слова (до 3 символов: RT, MM, MS, VF, FX) ищутся как начало названия или как
    отдельное слово — иначе «rt» нашлось бы в Sport, Arte и сотнях других названий.
    """

    def __init__(self, words):
        self.words = []
        self._sub = []     # длинные: ищем где угодно в названии
        self._short = []   # короткие: начало названия ИЛИ отдельное слово
        for w in words:
            w = w.strip()
            if not w or w in self.words:
                continue
            self.words.append(w)
            if len(w) <= 3:
                self._short.append((w.casefold(),
                                    re.compile(r"^\W*" + re.escape(w) + (r"(?!\w)" if (w[-1].isalnum() or w[-1] == "_") else ""), re.I)))
            else:
                self._sub.append(w.casefold())

    def __len__(self):
        return len(self.words)

    def match(self, name):
        low = (name or "").strip().casefold()
        if any(w in low for w in self._sub):
            return True
        # короткие слова (RT, TOP, FX...) — только в НАЧАЛЕ названия и целым словом: «RT Doc» да, «RTV»/«BBC Top Gear» нет
        return any(rx.search(name or "") for w, rx in self._short)


def load_exceptions(path):
    """Читает except.md: слова через запятую, кавычки и точка с запятой допускаются в любом виде."""
    try:
        text = open(path, "r", encoding="utf-8-sig", errors="ignore").read()
    except OSError:
        return ExceptionList([])
    text = re.sub(r"^\s*except\s*:", "", text, flags=re.I)
    words = []
    for part in re.split(r"[,;\n]", text):
        w = part.strip().strip('"\'“”«»').strip()
        if w and not w.startswith("#"):
            words.append(w)
    return ExceptionList(words)


class PriorityList:
    """Слова из priority.md: каналы с таким именем стоят в группе первыми, в заданном порядке.
    Совпадение: название (без HD/SD и скобок) равно слову или начинается с него + пробел."""

    def __init__(self, words):
        self.words = [w.strip().casefold() for w in words if w and w.strip()]

    def __len__(self):
        return len(self.words)

    def rank(self, name):
        n = _norm(name or "")
        for i, w in enumerate(self.words):
            if n == w or n.startswith(w + " "):
                return i
        return 10 ** 6


def load_priority(path):
    """Читает priority.md: названия через запятую или по одному на строку; строки с # — комментарии."""
    try:
        text = open(path, "r", encoding="utf-8-sig", errors="ignore").read()
    except OSError:
        return PriorityList([])
    words = []
    for line in text.splitlines():
        if line.strip().startswith("#"):
            continue
        for part in re.split(r"[,;]", line):
            w = part.strip().strip('"\'“”«»').strip()
            if w:
                words.append(w)
    return PriorityList(words)


_Q_RANK = [(re.compile(r"\b(4k|uhd)\b", re.I), 0),
           (re.compile(r"\bfhd\b|\b1080p?\b", re.I), 1),
           (re.compile(r"\bhd\b|\b720p?\b", re.I), 2),
           (re.compile(r"\bsd\b|\b(?:480|360)p?\b", re.I), 4)]
_Q_WORDS = re.compile(r"\b(hd|fhd|uhd|4k|sd|hevc|orig|оригинал|1080p?|720p?|480p?|360p?)\b", re.I)


def _quality_rank(name):
    """0 = 4K, 1 = FHD, 2 = HD, 3 = без пометки, 4 = SD (чем меньше, тем лучше)."""
    for rx, r in _Q_RANK:
        if rx.search(name or ""):
            return r
    return 3


def _dedupe_key(name):
    """Имя без пометок качества (скобки и +N остаются: это разные каналы)."""
    return re.sub(r"\s+", " ", _Q_WORDS.sub("", (name or "").lower())).strip()


class Organizer:
    def __init__(self, llm=None, cb=None, stop=None, llm_workers=2, exceptions=None,
                 priority=None, cache_path=None, skip_language=False, best_quality=False):
        self.exceptions = exceptions
        self.priority = priority            # PriorityList из priority.md
        self.skip_language = skip_language  # все каналы уже русскоязычные (язык не проверяется)
        self.best_quality = best_quality    # у одинаковых каналов оставлять только лучшее качество
        self.cache_path = cache_path
        self.cache = self._load_cache(cache_path) if cache_path else None
        self.llm = llm
        self.cb = cb
        self.stop = stop
        self.workers = max(1, int(llm_workers))
        self.llm_ok = llm is not None
        self.stats = {"exceptions": 0, "ru": 0, "foreign": 0, "shifted": 0, "ru_groups": 0, "foreign_groups": 0,
                      "llm_calls": 0, "llm_errors": 0, "llm_lang": 0, "llm_cat": 0,
                      "dedup": 0, "by_domain": 0, "cis": 0}

    # ------------------------------------------------------------------
    @staticmethod
    def _load_cache(path):
        """Кэш решений (язык, тема, названия групп): повторные запуски быстрее и одинаковее."""
        data = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        for k in ("lang", "cat", "titles"):
            if not isinstance(data.get(k), dict):
                data[k] = {}
        return data

    def _save_cache(self):
        if self.cache is None or not self.cache_path:
            return
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.cache_path)), exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, ensure_ascii=False)
        except OSError:
            pass

    @staticmethod
    def _ck(e):
        """Ключ кэша: нормализованное имя + группа."""
        return _norm(e["name"]) + "|" + (e.get("group") or "").strip().lower()

    def _sort_items(self, items, themed=False):
        """Порядок внутри группы: [тема — только в группах со сдвигом времени], приоритет из priority.md,
        имя, качество (лучшее первым: 4K, FHD, HD, без пометки, SD)."""
        def key(e):
            pr = self.priority.rank(e["name"]) if self.priority else 10 ** 6
            th = e.get("theme") or "other"
            th_i = OUT_ORDER.index(th) if (themed and th in OUT_ORDER) else 0
            return (th_i, pr, _norm(e["name"]), _quality_rank(e["name"]), e["name"])
        return sorted(items, key=key)

    def _best_quality(self, items):
        """У одинаковых каналов (отличаются только HD/SD/FHD) оставляет первый = лучший."""
        seen = set()
        out = []
        for e in items:
            k = _dedupe_key(e["name"])
            if k in seen:
                self.stats["dedup"] += 1
                continue
            seen.add(k)
            out.append(e)
        return out

    @staticmethod
    def _range_label(part):
        """«А–К» — первая буква первого и последнего канала части (для навигации между частями одной темы)."""
        def first(e):
            m = re.search(r"\w", _norm(e["name"]))
            return m.group(0).upper() if m else ""
        a, b = first(part[0]), first(part[-1])
        if not a or not b:
            return ""
        return a if a == b else f"{a}–{b}"

    def _report_lines(self, ru_groups, foreign_groups):
        """Отчёт «почему канал здесь»: группа | канал | язык (причина) | тема (причина)."""
        lines = ["группа | канал | язык (почему) | тема (почему)\n"]
        for tag, groups in (("RU", ru_groups), ("FOREIGN", foreign_groups)):
            for gname, part in groups:
                for e in part:
                    lang = e.get("lang_why") or f"правила (score {e.get('score', '?')})"
                    lines.append(f"{gname} | {e['name']} | {tag}: {lang} | "
                                 f"{e.get('cat', '-')}: {e.get('cat_why') or '-'}\n")
        return lines

    @staticmethod
    def _foreign_evidence(e):
        """Жёсткий признак чужой страны: домен чужой страны в URL или страна/немецкое имя в названии
        без кириллицы. Такой признак важнее слов из except.md (иначе «RTV» из .at считался бы русским)."""
        name = e.get("name") or ""
        if _CYR.search(name) or Organizer._is_cis(e):
            return False
        if _host_tld(e.get("url")) in FOREIGN_TLD or _name_cc(name) in _NAME_CC_FOREIGN:
            return True
        return bool(_NONRU_NAME.search(name) or _COUNTRY_WORD.search(name))

    @staticmethod
    def _is_cis(e):
        """Канал из Украины или Беларуси: слово в названии/группе, буквы укр./бел. алфавита,
        tvg-id .ua/.by или tvg-country UA/BY. Казахские буквы в названии — не СНГ в этом смысле."""
        name = e.get("name") or ""
        group = e.get("group") or ""
        attrs = e.get("attrs") or {}
        if _CIS_LETTERS.search(name) or _CIS_WORD.search(name) or (group and _CIS_WORD.search(group)):
            return True
        m = _TVGID_CC.search((attrs.get("tvg-id") or "").lower().strip())
        if m and m.group(1) in _CIS_TLD:
            return True
        if _host_tld(e.get("url")) in _CIS_TLD or _name_cc(name) in _NAME_CC_CIS:
            return True
        return (attrs.get("tvg-country") or "").lower().strip() in _CIS_COUNTRY

    def _p(self, frac, desc, line=None):
        if self.cb:
            self.cb(frac, desc, line)

    def _stopped(self):
        return self.stop is not None and self.stop.is_set()

    def _ask(self, prompt, max_tokens):
        if not self.llm_ok:
            return None
        try:
            self.stats["llm_calls"] += 1
            return self.llm.ask_text(prompt, max_tokens)
        except Exception:
            self.stats["llm_errors"] += 1
            return None

    def _batches(self, items, make_prompt, parse, max_tokens, base, span, desc):
        """Запросы к модели пачками. items — список (id, текст). Возвращает dict id -> ответ."""
        if not items or not self.llm_ok:
            return {}
        chunks = [items[i:i + BATCH] for i in range(0, len(items), BATCH)]
        result = {}
        done = 0
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = {ex.submit(self._ask, make_prompt(c), max_tokens): c for c in chunks}
            for fut in as_completed(futs):
                if self._stopped():
                    for f in futs:
                        f.cancel()
                    break
                chunk = futs[fut]
                answer = fut.result()
                done += 1
                if answer:
                    for local_no, value in parse(answer).items():
                        if 1 <= local_no <= len(chunk):
                            result[chunk[local_no - 1][0]] = value
                self._p(base + span * done / len(chunks), f"{desc}: {done}/{len(chunks)}")
        return result

    # ------------------------------------------------------------------
    # Язык
    # ------------------------------------------------------------------
    @staticmethod
    def _lang_score(e):
        """Score Russian-language evidence in an entry's name, group, and attrs.

        Higher scores favor Russian; _detect_language treats >= 2 as Russian
        and <= -2 as foreign. Foreign name markers can override Russian metadata.
        A foreign topic found only in the group is weakened when the name has
        Russian letters or explicit Russian language/ID metadata is present.
        """
        # URL на домене провайдера (.ru / klaipedalt / akciatv) -> сразу русскоязычный
        if url_looks_russian(e.get("url")):
            return 3
        name, group, attrs = e["name"], e["group"], e["attrs"]
        score = 0
        if _name_cc(name) in _NAME_CC_FOREIGN and not _CYR.search(name):
            return -4   # «...  PL / IN / CA / AE / US» — суффикс чужой страны в названии
        if _host_tld(e.get("url")) in FOREIGN_TLD:
            score -= 3   # домен чужой страны (.at .nl .it .de ...)
        if _UKR.search(name) or _LATIN_EXT.search(name):
            return -4
        # явные метаданные языка/страны считаем ДО проверки названия/группы:
        # русские каналы могут лежать в группе с немецким топом (group-title="Nachrichten")
        lang = (attrs.get("tvg-language") or "").lower()
        has_ru_lang = bool(lang and _RU_LANG.search(lang))
        tvg_id = (attrs.get("tvg-id") or "").lower().strip()
        m = _TVGID_CC.search(tvg_id)
        id_is_ru = bool(m and m.group(1) == "ru")
        explicit_ru_meta = has_ru_lang or id_is_ru
        has_cyr_name = bool(_CYR.search(name))
        name_strong = bool(_NONRU_NAME.search(name))
        name_country = bool(_COUNTRY_WORD.search(name))
        group_hit = bool(group and (_NONRU_NAME.search(group) or _COUNTRY_WORD.search(group)))
        if name_strong or name_country or group_hit:
            # немецкий топик только в группе — слабый признак: не перебивает
            # кириллическое название или явные русские метаданные
            # жёсткий признак: немецкое имя или страна в латинском названии
            hard = name_strong or (name_country and not has_cyr_name)
            if not hard and (has_cyr_name or explicit_ru_meta):
                score -= 1
                if has_cyr_name:
                    score += 2   # кириллическое имя перевешивает немецкий топик группы
                elif id_is_ru:
                    score += 1   # явный .ru tvg-id тоже перевешивает топик группы
            else:
                return -4
        elif explicit_ru_meta:
            # явные русские метаданные перевешивают немецкий топики в названии/группе
            score += 2
        # точное совпадение с известным немецким каналом (после снятия HD/SD и скобок)
        if _norm(name) in _DE_NAMES:
            return -4
        if has_cyr_name:
            score += 2
        if _RU_LATIN.search(name) or _RU_BRANDS.search(name):
            score += 2
        if lang:
            score += 3 if has_ru_lang else -3
        if m:
            score += 2 if id_is_ru else -3
        elif _TVGID_UNDER_CC.search(tvg_id):
            score -= 3
        country = (attrs.get("tvg-country") or "").lower().strip()
        if country:
            score += 2 if country in _RU_COUNTRY else -1
        if group:
            if re.search(r"russia|russian|россия|русск|\bрф\b", group, re.I):
                score += 2
            elif _FOREIGN_GROUP.search(group) and not has_cyr_name:
                score -= 2
        return score

    def _detect_language(self, entries):
        """Set score and ru fields on entries in place using rules and an optional LLM.

        Ask the model about borderline scores and Russian candidates without
        Russian letters in their names; the latter can only be downgraded.
        Model request errors are counted and use the rule result. Unresolved
        entries become foreign. Progress callback errors propagate.
        """
        undecided = []
        weak = []
        for e in entries:
            if self.exceptions and self.exceptions.match(e["name"]) and not self._foreign_evidence(e):
                e["ru"] = True          # слово из except.md: всегда русскоязычный
                e["score"] = 9
                e["lang_why"] = "слово из except.md"
                self.stats["exceptions"] += 1
                continue
            if url_looks_russian(e.get("url")):
                # домен .ru / .black / .red / klaipedalt / akciatv: всегда русскоязычный, модель не пересматривает
                e["ru"] = True
                e["score"] = 9
                e["lang_why"] = "домен в URL"
                self.stats["by_domain"] += 1
                continue
            s = self._lang_score(e)
            e["score"] = s
            if s >= 2:
                e["ru"] = True
                has_ru_signal = _CYR.search(e["name"]) or _RU_LATIN.search(e["name"]) or \
                    _RU_BRANDS.search(e["name"]) or \
                    bool((e["attrs"].get("tvg-language") or "").lower() and _RU_LANG.search(e["attrs"]["tvg-language"])) or \
                    bool(_TVGID_CC.search((e["attrs"].get("tvg-id") or "").lower().strip()) and
                         _TVGID_CC.search((e["attrs"].get("tvg-id") or "").lower().strip()).group(1) == "ru")
                if not has_ru_signal:
                    weak.append(e)   # русский только по косвенным признакам: перепроверит модель
            elif s <= -2:
                e["ru"] = False
            else:
                e["ru"] = None
                undecided.append(e)
        check = undecided + weak
        if check and self.cache is not None:
            left = []
            for e in check:
                hit = self.cache["lang"].get(self._ck(e))
                if hit is None:
                    left.append(e)
                elif e["ru"] is None or hit is False:
                    e["ru"] = hit
                    e["lang_why"] = "кэш"
            check = left
        if check and self.llm_ok:
            items = [(id(e), f"{e['name']} | group: {e['group'] or '-'}") for e in check]

            def make_prompt(chunk):
                """Return a numbered RU/OTHER prompt for (entry ID, label) pairs."""
                lines = "\n".join(f"{i}. {t}" for i, (_, t) in enumerate(chunk, 1))
                return (
                    "You classify TV channels by BROADCAST LANGUAGE. Rules:\n"
                    "1. Answer RU ONLY IF you are certain the channel broadcasts mainly in Russian: "
                    "it has a clearly Russian name (Cyrillic), or is a known Russian brand "
                    "(Первый канал, Россия, НТВ, Матч ТВ, ТНТ, СТС, РЕН ТВ, Карусель, RU.TV и т. п.), "
                    "or an international brand with an explicit Russian feed (Discovery Russia, Nickelodeon Russia).\n"
                    "2. German channels are NEVER RU. This includes public (Das Erste, ZDF, ZDFneo, arte, 3sat, Kika, "
                    "Phoenix, one, tagesschau24, DW), private (RTL, RTL Zwei, SUPER RTL, VOX, ProSieben, Sat.1, "
                    "Kabel Eins, sixx, Tele 5, DMAX, TNT Comedy, TNT Serie, Sky Atlantic, Sky Sport Bundesliga, "
                    "Sport1, Sportal, ran), Austrian/Swiss (ORF 1, ORF 2, ServusTV, SRF, Puls 4) and any channel whose "
                    "name contains German words like 'Doku', 'Serien', 'Filme', 'Heimatkino', 'Nachrichten', 'Wetter', "
                    "'Hobby', 'Tier', 'Volksmusik', 'Schlager', 'Bundesliga'.\n"
                    "3. Answer OTHER for everything that is not certainly Russian: German, Kazakh, Ukrainian, "
                    "Belarusian, English, Turkish, Polish, Arabic etc., and international channels without an "
                    "explicit Russian feed.\n"
                    "4. If you are not sure, answer OTHER. Never guess RU.\n"
                    "Examples: 'Первый канал HD' RU; 'Россия 24' RU; 'Матч ТВ' RU; 'Discovery Россия' RU; "
                    "'ZDF HD' OTHER; 'Das Erste' OTHER; 'RTL Deutschland' OTHER; 'VOX' OTHER; 'ProSieben FUN' OTHER; "
                    "'Sky Sport Bundesliga 1' OTHER; 'Tele 5 HD' OTHER; 'arte' OTHER; 'Qazaqstan' OTHER; "
                    "'Қазақстан' OTHER; 'Khabar 24' OTHER; 'Discovery Channel' OTHER; 'Eurosport 1' OTHER; '1+1' OTHER.\n"
                    "Answer strictly one line per channel in the form 'N:RU' or 'N:OTHER'. Do not add comments.\n\n"
                    + lines
                )

            def parse(text):
                out = {}
                for m in re.finditer(r"(\d+)\s*[:=\-.)]\s*(RU|OTHER)", text.upper()):
                    out[int(m.group(1))] = (m.group(2) == "RU")
                return out

            res = self._batches(items, make_prompt, parse, BATCH * 7, 0.0, 0.2, "Язык (модель)")
            for e in check:
                if id(e) in res and self.cache is not None:
                    self.cache["lang"][self._ck(e)] = res[id(e)]
                if id(e) in res and (e["ru"] is None or res[id(e)] is False):
                    e["lang_why"] = "модель"
                    e["ru"] = res[id(e)]
                    self.stats["llm_lang"] += 1
        for e in entries:
            if e["ru"] is None:
                e["ru"] = False

    # ------------------------------------------------------------------
    # Тема
    # ------------------------------------------------------------------
    @staticmethod
    def _rule_category(e):
        """Тема по правилам: 18+/радио — всегда первыми; дальше совпадения regex по названию+группе;
        если совпало несколько — побеждает тема из исходного group-title; без совпадений — тема группы."""
        name_l = (e["name"] or "").lower().strip()
        group = e.get("group") or ""
        text = f"{name_l} {group}".lower()
        for key in ("adult", "radio"):
            if _CATS_RE[key].search(text):
                return key
        for rx, hcat in _NAME_HINTS:
            if rx.search(name_l):
                return hcat
        gcat = _GROUP_CAT.get(canonical_group(group)) if group else None
        hits = [k for k in RULE_ORDER
                if k not in ("adult", "radio", "federal") and _CATS_RE[k].search(text)]
        if gcat in hits:
            return gcat
        if hits:
            return hits[0]
        if _CATS_RE["federal"].search(name_l):
            return "federal"
        return gcat

    @staticmethod
    def _rule_category_old(e):
        text = f"{e['name']} {e['group']}".lower()
        for key in RULE_ORDER:
            rx = _CATS_RE[key]
            if key == "federal":
                if rx.search(e["name"].lower().strip()):
                    return key
            elif rx.search(text):
                return key
        return None

    def _classify(self, ru_entries, base, span):
        pending = []
        for e in ru_entries:
            m = None
            for sign, num in [(mm.group(1), mm.group(2)) for mm in _SHIFT_RE.finditer(e["name"])][-1:]:
                n = int(num)
                n = -n if sign in "-−" else n
                if n in SHIFT_REGIONS:
                    m = n
            if m is not None:
                e["cat"] = "shift"
                e["cat_why"] = "сдвиг времени"
                e["theme"] = self._rule_category(e) or "other"
                e["shift"] = m
                self.stats["shifted"] += 1
                continue
            cat = self._rule_category(e)
            e["cat"] = cat
            if cat:
                e["cat_why"] = "правило"
            if cat is None:
                pending.append(e)

        if pending and self.cache is not None:
            left = []
            for e in pending:
                hit = self.cache["cat"].get(self._ck(e))
                if hit in CATS:
                    e["cat"] = hit
                    e["cat_why"] = "кэш"
                else:
                    left.append(e)
            pending = left
        if pending and self.llm_ok:
            items = [(id(e), f"{e['name']} | group: {e['group'] or '-'}") for e in pending]
            cats_text = "\n".join(f"{i}={CATS[k][1]}" for i, k in enumerate(LLM_CATS, 1))

            def make_prompt(chunk):
                lines = "\n".join(f"{i}. {t}" for i, (_, t) in enumerate(chunk, 1))
                return ("Classify each Russian-language TV channel into ONE category by its name. Categories:\n"
                        + cats_text + "\nIf unsure choose the 'Разное' category. "
                        "Answer strictly one line per channel in the form 'N:C' where C is the category number.\n\n"
                        + lines)

            def parse(text):
                out = {}
                for m in re.finditer(r"(\d+)\s*[:=\-.)]\s*(\d+)", text):
                    c = int(m.group(2))
                    if 1 <= c <= len(LLM_CATS):
                        out[int(m.group(1))] = LLM_CATS[c - 1]
                return out

            res = self._batches(items, make_prompt, parse, BATCH * 7, base, span, "Темы (модель)")
            for e in pending:
                if id(e) in res:
                    e["cat"] = res[id(e)]
                    e["cat_why"] = "модель"
                    if self.cache is not None:
                        self.cache["cat"][self._ck(e)] = res[id(e)]
                    self.stats["llm_cat"] += 1
        for e in ru_entries:
            if e["cat"] is None:
                e["cat"] = "other"

    # ------------------------------------------------------------------
    # Названия групп
    # ------------------------------------------------------------------
    def _creative_title(self, cat, names, idx, used):
        ck = f"{cat}|{idx}"
        if self.cache is not None:
            cached = self.cache["titles"].get(ck)
            if cached and cached.lower() not in used:
                used.add(cached.lower())
                return cached     # названия групп не меняются между запусками
        title = None
        if self.llm_ok and not self._stopped():
            sample = ", ".join(_norm(n) for n in names[:12])
            prompt = ("Придумай короткое творческое название (2-3 слова, по-русски, без кавычек и эмодзи) "
                      f"для подборки ТВ-каналов. Тема подборки: {CATS[cat][1]}. Примеры каналов: {sample}. "
                      "Ответь только названием.")
            title = _clean_title(self._ask(prompt, 24))
        if not title or title.lower() in used:
            pool = CATS[cat][3]
            for k in range(len(pool)):
                cand = pool[(idx + k) % len(pool)]
                if cand.lower() not in used:
                    title = cand
                    break
            else:
                title = f"{pool[idx % len(pool)]} · {idx // len(pool) + 1}"
        used.add(title.lower())
        if self.cache is not None:
            self.cache["titles"][ck] = title
        return title

    # ------------------------------------------------------------------
    def _merge_groups_llm(self, names):
        """Модель сводит синонимичные имена групп -> {старое: новое}.
        Безопасно: цель обязана быть из этого же списка, иначе группа остаётся как есть —
        значит ни один канал не теряется (только переименование ключей)."""
        if not names:
            return {}
        numbered = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(names))
        prompt = (
            "Ниже список названий групп ТВ-каналов. Сведи синонимичные группы к одному имени "
            "(например «Музыка»/«Музыкальные»/«Танцевальные»; «Новости»/«Новостные»; "
            "«Кино»/«Фильмы»/«Кинозал»; «Документальные»/«Познавательные»/«История»). "
            "Для каждой группы верни итоговое имя — используй имя из ЭТОГО ЖЕ списка "
            "(выбирай самую общую/крупную группу). Каждую группу укажи ровно один раз, ничего не пропускай. "
            "Формат: одна строка на группу «N=ИТОГОВОЕ_ИМЯ». Без пояснений.\n\n" + numbered
        )
        ans = self._ask(prompt, max_tokens=40 + 10 * len(names))
        if not ans:
            return {}
        valid = set(names)
        out = {}
        for m in re.finditer(r"(\d+)\s*[=:]\s*(.+)", ans):
            i = int(m.group(1))
            if not (1 <= i <= len(names)):
                continue
            new = m.group(2).strip().strip('"\'“”«»').splitlines()[0].strip()[:40]
            if new in valid:
                out[names[i - 1]] = new
        return out

    def organize(self, streams):
        """
        streams: список dict с ключами 'info' (строка #EXTINF) и 'url'.
        Возвращает dict: ru_lines, foreign_lines (без шапки, начинаются с #EXTM3U), stats, ru_groups, foreign_groups.
        None при прерывании.
        """
        entries = []
        for s in streams:
            _, attrs, name = split_extinf(s["info"])
            entries.append({"stream": s, "name": name or "Без имени",
                            "group": (attrs.get("group-title") or "").strip(), "attrs": attrs,
                            "lang": (s.get("lang") or "").upper(), "url": s.get("url", "")})
        if not entries:
            return {"ru_lines": ["#EXTM3U\n"], "foreign_lines": ["#EXTM3U\n"], "stats": self.stats,
                    "ru_groups": [], "foreign_groups": []}

        self._p(0.0, "Сортировка: язык", f"🗂 Сортировка: каналов {len(entries)}")
        if self.skip_language:
            for e in entries:
                e["ru"] = True
                e["score"] = 9
                if self.exceptions and self.exceptions.match(e["name"]) and not self._foreign_evidence(e):
                    e["lang_why"] = "слово из except.md"
                elif url_looks_russian(e.get("url")):
                    e["lang_why"] = "домен в URL"
                else:
                    e["lang_why"] = "язык не проверялся"
        else:
            self._detect_language(entries)
        if self._stopped():
            return None
        # Украина и Беларусь — отдельная группа «СНГ» (внутри русского файла); слова из except.md и домены .ru это не меняют
        cis = []
        for e in entries:
            if e.get("lang_why") not in ("слово из except.md", "домен в URL") and self._is_cis(e):
                e["cis"] = True
                e["cat"] = "cis"
                e["cat_why"] = "Украина/Беларусь"
                cis.append(e)
        ru = [e for e in entries if e["ru"] and not e.get("cis")]
        foreign = [e for e in entries if not e["ru"] and not e.get("cis")]
        self.stats["ru"], self.stats["foreign"] = len(ru), len(foreign)
        self._p(0.2, "Сортировка: темы", f"🇷🇺 Русскоязычных: {len(ru)}, остальных: {len(foreign)}")

        self._classify(ru, 0.2, 0.3)
        if self._stopped():
            return None

        # ---------- русские группы ----------
        buckets = {}
        for e in ru:
            key = ("shift", e["shift"]) if e["cat"] == "shift" else (e["cat"], None)
            buckets.setdefault(key, []).append(e)

        # мелкие темы -> «Разное»
        for key in sorted(buckets, key=lambda k: len(buckets[k])):
            cat, _ = key
            if cat not in NEVER_POOL and cat not in ("shift", "other", "federal") and len(buckets[key]) < MIN_GROUP:
                buckets.setdefault((MERGE_TO.get(cat, "other"), None), []).extend(buckets.pop(key))

        def order_key(key):
            cat, shift = key
            return (OUT_ORDER.index(cat), shift if shift is not None else 0)

        used = set()
        ru_groups = []   # (имя группы, [entries])
        keys = sorted(buckets, key=order_key)
        total_chunks = sum(len(_chunk_sizes(len(buckets[k]))) for k in keys) or 1
        made = 0
        for key in keys:
            if self._stopped():
                return None
            cat, shift = key
            items = self._sort_items(buckets[key], themed=(cat == "shift"))
            if self.best_quality:
                items = self._best_quality(items)
            multi = len(_chunk_sizes(len(items))) > 1
            pos = 0
            for idx, size in enumerate(_chunk_sizes(len(items))):
                part = items[pos:pos + size]
                pos += size
                if cat == "shift":
                    sign = "+" if shift > 0 else "-"
                    title = f"Время {sign}{abs(shift)}: {SHIFT_REGIONS[shift]}"
                    if idx:
                        title += f" · {idx + 1}"
                    gname = f"{RU_FLAG} 🕓 {title}"
                else:
                    title = self._creative_title(cat, [e["name"] for e in part], idx, used)
                    rng = self._range_label(part) if multi else ""
                    gname = f"{RU_FLAG} {CATS[cat][0]} {title}" + (f" · {rng}" if rng else "")
                ru_groups.append((gname, part))
                made += 1
                self._p(0.5 + 0.4 * made / total_chunks, f"Названия групп: {made}/{total_chunks}",
                        f"   {gname} — {len(part)} кан.")

        # ---------- СНГ: Украина и Беларусь (СНГ, СНГ 2, СНГ 3 ... по ≤ 50 каналов) ----------
        if cis:
            cis_items = self._sort_items(cis)
            if self.best_quality:
                cis_items = self._best_quality(cis_items)
            self.stats["cis"] = len(cis_items)
            pos = 0
            for idx, size in enumerate(_chunk_sizes(len(cis_items))):
                part = cis_items[pos:pos + size]
                pos += size
                ru_groups.append(("🌐 СНГ" + (f" {idx + 1}" if idx else ""), part))
            self._p(0.92, "СНГ", f"🌐 Украина/Беларусь в группе «СНГ»: {len(cis_items)}")

        # ---------- нерусские ----------
        fb = {}
        for e in foreign:
            base = canonical_group(e["group"]) or \
                (e["attrs"].get("tvg-country") or "").strip().upper() or "Прочие"
            g = LANG_GROUP.get(e.get("lang") or "") or base
            fb.setdefault(g, []).append(e)

        # LLM сводит синонимичные группы (только переименование ключей — каналы не теряются)
        if self.llm_ok and len(fb) >= 2 and not self._stopped():
            mapping = self._merge_groups_llm(sorted(fb))
            if mapping:
                merged = {}
                for g, items in fb.items():
                    merged.setdefault(mapping.get(g, g), []).extend(items)
                fb = merged

        pool = []
        lang_groups = set(LANG_GROUP.values())
        for g in list(fb):
            if len(fb[g]) < MIN_GROUP and g not in lang_groups:
                pool.extend(fb.pop(g))
        foreign_groups = []
        for g in sorted(fb, key=lambda x: x.lower()):
            items = self._sort_items(fb[g])
            pos = 0
            for idx, size in enumerate(_chunk_sizes(len(items))):
                part = items[pos:pos + size]
                pos += size
                foreign_groups.append((f"{WORLD} {g}" + (f" · {idx + 1}" if idx else ""), part))
        if pool:
            pool = self._sort_items(pool)
            pos = 0
            for idx, size in enumerate(_chunk_sizes(len(pool))):
                part = pool[pos:pos + size]
                pos += size
                foreign_groups.append((f"{WORLD} Разное иностранное" + (f" · {idx + 1}" if idx else ""), part))

        self._save_cache()
        self.stats["ru_groups"] = len(ru_groups)
        self.stats["foreign_groups"] = len(foreign_groups)
        self._p(0.95, "Сортировка: сборка файлов",
                f"🌍 Нерусскоязычных групп: {len(foreign_groups)}")

        return {
            "report_lines": self._report_lines(ru_groups, foreign_groups),
            "ru_lines": self._to_lines(ru_groups),
            "foreign_lines": self._to_lines(foreign_groups),
            "stats": self.stats,
            "ru_groups": [(g, len(p)) for g, p in ru_groups],
            "foreign_groups": [(g, len(p)) for g, p in foreign_groups],
        }

    @staticmethod
    def _to_lines(groups):
        lines = ["#EXTM3U\n"]
        for gname, part in groups:
            for e in part:
                s = e["stream"]
                lines.append(set_group(s["info"], gname) + "\n")
                lines.extend(x + "\n" for x in s.get("extra", []))
                lines.append(s["url"] + "\n")
        return lines
