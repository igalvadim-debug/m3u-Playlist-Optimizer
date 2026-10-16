#!/usr/bin/env python3
"""
Умная сортировка плейлиста.

1. Определяет русскоязычные каналы (по названию, группе, tvg-атрибутам; спорные — Qwen по названию).
2. Русскоязычные: каналы со сдвигом времени (Россия +4 и т. п.) выделяются в отдельные группы по часовым поясам,
   остальные раскладываются по темам (правила + Qwen для неясных), затем режутся на группы по 40-50 каналов
   с творческими названиями (Qwen, запасной вариант — готовые названия).
3. Нерусскоязычные уходят в отдельный список (группы по исходному group-title, тоже не больше 50 каналов).
"""
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from modules.m3u_utils import split_extinf, set_group

RU_FLAG = "🇷🇺"
WORLD = "🌍"
CHUNK_MAX = 50       # максимум каналов в группе
MIN_GROUP = 12       # тема меньше этого размера сливается в «сборную солянку»
BATCH = 20           # каналов в одном запросе к модели

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
               r"кино|фильм|movie|\bfilm\b|cinema|hollywood|боевик|комеди|детектив|ужас|триллер|фантастик|"
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
             r"discovery|nat(ional)? ?geo|animal planet|истори|history|наука|science|познават|документал|docu|travel|"
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
              r"\bxxx\b|18\+|\+18|эротик|erotic|playboy|adult|brazzers|hustler|penthouse|порно|porn|секс",
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

SHIFT_REGIONS = {
    -1: "Калининград", 1: "Самара", 2: "Екатеринбург", 3: "Омск", 4: "Красноярск и Новосибирск",
    5: "Иркутск", 6: "Якутск", 7: "Владивосток", 8: "Магадан", 9: "Камчатка",
}
_SHIFT_RE = re.compile(r"(?:(?<=\s)|(?<=\())([+\-−])\s?(\d{1,2})(?=\s|\)|$|ч\b|h\b)")

_CYR = re.compile(r"[А-Яа-яЁё]")
# буквы украинского, белорусского, казахского и т. п. алфавитов, которых нет в русском
_UKR = re.compile(r"[ІіЇїЄєҐґЎўӘәҒғҚқҢңӨөҰұҮүҺһ]")
# латиница с диакритикой (ä ö ü ß é ñ ...): точно не русский канал
_LATIN_EXT = re.compile(r"[\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u00ff\u0100-\u017f]")
# явные признаки другой страны или языка в названии или группе
_NONRU_NAME = re.compile(
    r"deutsch|german|\bard\b|\bzdf\b|\brtl\b|sat\.?1|pro ?7\b|kabel ?eins|\borf\b|\bsrf\b|3sat|"
    r"tagesschau|super(?:rtl)?\b|sixx\b|tele[ -]?(5|berlin|club|de)\b|\bpuls\b|\bdaserste\b|"
    r"servus ?tv|joyn\b|\bwdr\b|\bnr?[d]w?\b|\bn-tv\b|arte|\bsky\b|\bdhl\b|\bbundesliga\b|\bzero\b|"
    # немецкие слова/топики в названиях и группах (с границами слов)
    r"\bhobby\b|\btier\b|\bheimatkino\b|\bvolksmusik\b|\bschlager\b|\bnachrichten\b|\bwetter\b|"
    r"\breportage\b|\bdoku(?:mentation)?\b|\bserien\b|\bfilme\b|"
    r"qazaq|kazakh|kazakstan|казах|қазақ|немецк|германи|украин|ukrain|турецк|turk|"
    r"польск|polish|англий|english|француз|french|итальянск|italian|испанск|spanish", re.I)
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
_TVGID_CC = re.compile(r"\.(ru|de|at|ch|kz|ua|uk|gb|us|fr|it|es|tr|pl|pt|nl|be|az|am|ge|uz|lv|lt|ee|md|il|ae|sa|eg|in|cn|jp|kr|br|mx|ca|au)(?:@\w+)?$")
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


class Organizer:
    def __init__(self, llm=None, cb=None, stop=None, llm_workers=2):
        self.llm = llm
        self.cb = cb
        self.stop = stop
        self.workers = max(1, int(llm_workers))
        self.llm_ok = llm is not None
        self.stats = {"ru": 0, "foreign": 0, "shifted": 0, "ru_groups": 0, "foreign_groups": 0,
                      "llm_calls": 0, "llm_errors": 0, "llm_lang": 0, "llm_cat": 0}

    # ------------------------------------------------------------------
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
        name, group, attrs = e["name"], e["group"], e["attrs"]
        score = 0
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
        if _NONRU_NAME.search(name) or (group and _NONRU_NAME.search(group)):
            # немецкий топик только в группе — слабый признак: не перебивает
            # кириллическое название или явные русские метаданные
            if group and not _NONRU_NAME.search(name) and (has_cyr_name or explicit_ru_meta):
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
                if id(e) in res and (e["ru"] is None or res[id(e)] is False):
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
                e["shift"] = m
                self.stats["shifted"] += 1
                continue
            cat = self._rule_category(e)
            e["cat"] = cat
            if cat is None:
                pending.append(e)

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
                    self.stats["llm_cat"] += 1
        for e in ru_entries:
            if e["cat"] is None:
                e["cat"] = "other"

    # ------------------------------------------------------------------
    # Названия групп
    # ------------------------------------------------------------------
    def _creative_title(self, cat, names, idx, used):
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
        return title

    # ------------------------------------------------------------------
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
                            "group": (attrs.get("group-title") or "").strip(), "attrs": attrs})
        if not entries:
            return {"ru_lines": ["#EXTM3U\n"], "foreign_lines": ["#EXTM3U\n"], "stats": self.stats,
                    "ru_groups": [], "foreign_groups": []}

        self._p(0.0, "Сортировка: язык", f"🗂 Сортировка: каналов {len(entries)}")
        self._detect_language(entries)
        if self._stopped():
            return None
        ru = [e for e in entries if e["ru"]]
        foreign = [e for e in entries if not e["ru"]]
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
        for key in list(buckets):
            cat, _ = key
            if cat not in NEVER_POOL and cat not in ("shift", "other") and len(buckets[key]) < MIN_GROUP:
                buckets.setdefault(("other", None), []).extend(buckets.pop(key))

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
            items = sorted(buckets[key], key=lambda e: (_norm(e["name"]), e["name"]))
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
                    gname = f"{RU_FLAG} {CATS[cat][0]} {title}"
                ru_groups.append((gname, part))
                made += 1
                self._p(0.5 + 0.4 * made / total_chunks, f"Названия групп: {made}/{total_chunks}",
                        f"   {gname} — {len(part)} кан.")

        # ---------- нерусские ----------
        fb = {}
        for e in foreign:
            g = e["group"] or (e["attrs"].get("tvg-country") or "").strip().upper() or "Прочие"
            fb.setdefault(g, []).append(e)
        pool = []
        for g in list(fb):
            if len(fb[g]) < MIN_GROUP:
                pool.extend(fb.pop(g))
        foreign_groups = []
        for g in sorted(fb, key=lambda x: x.lower()):
            items = sorted(fb[g], key=lambda e: (_norm(e["name"]), e["name"]))
            pos = 0
            for idx, size in enumerate(_chunk_sizes(len(items))):
                part = items[pos:pos + size]
                pos += size
                foreign_groups.append((f"{WORLD} {g}" + (f" · {idx + 1}" if idx else ""), part))
        if pool:
            pool.sort(key=lambda e: (_norm(e["name"]), e["name"]))
            pos = 0
            for idx, size in enumerate(_chunk_sizes(len(pool))):
                part = pool[pos:pos + size]
                pos += size
                foreign_groups.append((f"{WORLD} Разное иностранное" + (f" · {idx + 1}" if idx else ""), part))

        self.stats["ru_groups"] = len(ru_groups)
        self.stats["foreign_groups"] = len(foreign_groups)
        self._p(0.95, "Сортировка: сборка файлов",
                f"🌍 Нерусскоязычных групп: {len(foreign_groups)}")

        return {
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
