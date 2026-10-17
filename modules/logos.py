#!/usr/bin/env python3
"""
Иконки каналов: подбор логотипа из папки tv-logos-main по названию канала
и запись его в атрибут tvg-logo.

Логотипы лежат в tv-logos-main/countries/<страна>/<имя>-<код страны>.png.
В плейлист пишется ссылка на тот же файл в СВОЁМ форке репозитория на GitHub
(по умолчанию igalvadim-debug/tv-logos; сменить можно переменной M3UGENIUS_LOGO_BASE).
Локальные пути не открылись бы на телефоне или приставке.
Если однозначного совпадения нет, иконка не ставится: чужая иконка хуже, чем никакая.
"""
import os
import re
from pathlib import Path
from urllib.parse import quote

from modules.m3u_utils import split_extinf, build_extinf

LOGO_ROOT = Path(__file__).resolve().parent.parent / "tv-logos-main" / "countries"
# Куда плейлист пишет ссылку на иконку. Свой форк: чужой репо могут закрыть/заблокировать.
# Переопределяется переменной окружения M3UGENIUS_LOGO_BASE (например, свой файл-сервер).
LOGO_BASE = os.environ.get(
    "M3UGENIUS_LOGO_BASE",
    "https://raw.githubusercontent.com/igalvadim-debug/tv-logos/main/countries/",
)

_CYR = re.compile(r"[А-Яа-яЁёІіЇїЄє]")

_TR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y",
    "ь": "", "э": "e", "ю": "yu", "я": "ya", "і": "i", "ї": "yi", "є": "ye", "ґ": "g",
}
_NOISE = {"hd", "fhd", "uhd", "4k", "sd", "hevc", "orig", "original"}
_GENERIC = {"tv", "channel", "kanal"}

# предпочтение стран: сначала Россия (для русских каналов), потом международные папки
_PREF_RU = ["russia", "international", "world-europe", "world-asia", "world-middle-east",
            "world-latin-america", "world-africa"]
_PREF_ANY = _PREF_RU[1:]

# названия, которые транслитерацией не найти: слаг названия -> файл (относительно countries/)
ALIASES = {
    "pervyy-kanal": "russia/channel-one-ru.png",
    "pervyy": "russia/channel-one-ru.png",
    "1-kanal": "russia/channel-one-ru.png",
    "perviy-kanal": "russia/channel-one-ru.png",
    "pyatyy-kanal": "russia/channel-5-ru.png",
    "5-kanal": "russia/channel-5-ru.png",
    "match-tv": "russia/match-ru.png",
    "match": "russia/match-ru.png",
    "tv-3": "russia/tv3_1-ru.png",
    "tv3": "russia/tv3_1-ru.png",
    "disney": "russia/kanal-disney-ru.png",
    "kanal-disney": "russia/kanal-disney-ru.png",
    "ntv": "russia/ntv-ru.png",
    "tnt": "russia/tnt-ru.png",
    "sts": "russia/sts-ru.png",
    "muz-tv": "russia/muz-tv-ru.png",
    "pyatnitsa": "russia/pyatnitsa-ru.png",
}

_CC = {"de": "germany", "at": "austria", "ch": "switzerland", "fr": "france", "it": "italy", "es": "spain",
       "pt": "portugal", "nl": "netherlands", "be": "belgium", "uk": "united-kingdom", "gb": "united-kingdom",
       "us": "united-states", "ca": "canada", "tr": "turkey", "pl": "poland", "ua": "ukraine", "ru": "russia",
       "gr": "greece", "ro": "romania", "bg": "bulgaria", "cz": "czech-republic", "hu": "hungary",
       "rs": "serbia", "hr": "croatia", "il": "israel", "in": "india", "au": "australia", "br": "brazil",
       "mx": "mexico", "ae": "united-arab-emirates", "az": "azerbaijan", "lt": "lithuania"}
_WORDS = [
    (r"german|deutsch|германи|немецк", "germany"), (r"franc|french|франц", "france"),
    (r"ital|итальян", "italy"), (r"spain|spanish|espa|испан", "spain"), (r"turk|турец", "turkey"),
    (r"polish|polska|poland|польск", "poland"), (r"ukrain|украин", "ukraine"),
    (r"\buk\b|united kingdom|british|england", "united-kingdom"), (r"\busa\b|american|united states", "united-states"),
    (r"portug", "portugal"), (r"nether|dutch", "netherlands"), (r"austria", "austria"), (r"swiss", "switzerland"),
    (r"greek|greece", "greece"), (r"romania", "romania"), (r"bulgaria", "bulgaria"), (r"czech", "czech-republic"),
    (r"hungar", "hungary"), (r"serbia", "serbia"), (r"croat", "croatia"), (r"israel", "israel"),
    (r"india", "india"), (r"arab|emirates", "united-arab-emirates"), (r"canada", "canada"),
    (r"australia", "australia"),
]


def translit(s):
    return "".join(_TR.get(ch, ch) for ch in s.lower())


def _tokens(name):
    s = name.lower()
    s = re.sub(r"\[.*?\]|\(.*?\)", " ", s)
    s = re.sub(r"(?<=\s)[+\-−]\d{1,2}(?=\s|$)", " ", s)   # сдвиг времени: НТВ +4
    s = translit(s)
    return [t for t in re.split(r"[^a-z0-9_]+", s) if t]


def _variants_from_tokens(tokens):
    """Варианты слага: с шумом и без, без tv/channel/kanal, с цифрой слитно и раздельно"""
    out = []

    def add(seq):
        if not seq:
            return
        v = "-".join(seq)
        for cand in (v, re.sub(r"-(\d+)$", r"\1", v), re.sub(r"([a-z])(\d+)$", r"\1-\2", v)):
            if cand and cand not in out:
                out.append(cand)

    clean = [t for t in tokens if t not in _NOISE]
    for base in (clean, tokens):
        add(base)
        add([t for t in base if t not in _GENERIC])
    return out


def name_variants(name):
    return _variants_from_tokens(_tokens(name))


def _logo_variants(stem):
    stem = re.sub(r"-[a-z]{2}$", "", stem)            # суффикс страны: ntv-ru -> ntv
    return _variants_from_tokens([t for t in re.split(r"[-]+", stem.lower()) if t])


class LogoMatcher:
    def __init__(self, root=LOGO_ROOT, base=LOGO_BASE):
        self.root = Path(root)
        self.base = base
        self.index = {}
        self.files = 0
        self._build()

    def _build(self):
        if not self.root.exists():
            return
        for cdir in sorted(p for p in self.root.iterdir() if p.is_dir()):
            for f in sorted(cdir.rglob("*.png")):
                self.files += 1
                rel = f.relative_to(self.root).as_posix()
                for v in _logo_variants(f.stem):
                    self.index.setdefault(v, []).append((cdir.name, rel))

    @staticmethod
    def _hints(attrs, group):
        hint = set()
        attrs = attrs or {}
        cc = (attrs.get("tvg-country") or "").lower().strip()
        if cc in _CC:
            hint.add(_CC[cc])
        m = re.search(r"\.([a-z]{2})$", (attrs.get("tvg-id") or "").lower())
        if m and m.group(1) in _CC:
            hint.add(_CC[m.group(1)])
        text = f"{group or ''} {attrs.get('tvg-language') or ''}".lower()
        for rx, country in _WORDS:
            if re.search(rx, text):
                hint.add(country)
        return hint

    def _pick(self, cands, attrs, group, has_cyr, prefer_ru):
        by_country = {}
        for c, rel in cands:
            by_country.setdefault(c, rel)
        for pref in (_PREF_RU if prefer_ru else _PREF_ANY):
            if pref in by_country:
                return by_country[pref]
        if has_cyr:
            return None     # кириллическое имя нашлось только у иностранных каналов: не рискуем
        if len(by_country) == 1:
            return next(iter(by_country.values()))
        for c in self._hints(attrs, group):
            if c in by_country:
                return by_country[c]
        return None

    def find(self, name, attrs=None, group="", prefer_ru=True):
        """Ссылка на логотип или None"""
        variants = name_variants(name)
        if not variants:
            return None
        for v in variants:
            rel = ALIASES.get(v)
            if rel and (self.root / rel).exists():
                return self.base + quote(rel)
        has_cyr = bool(_CYR.search(name))
        for v in variants:
            cands = self.index.get(v)
            if not cands:
                continue
            rel = self._pick(cands, attrs, group, has_cyr, prefer_ru)
            if rel:
                return self.base + quote(rel)
        return None


def apply_logos_to_lines(lines, matcher, overwrite=False, prefer_ru=True):
    """
    Проставляет tvg-logo в строках #EXTINF.
    Возвращает (новые строки, сколько иконок пришито, сколько каналов всего).
    """
    out = []
    added = 0
    total = 0
    for line in lines:
        if not line.lstrip().upper().startswith("#EXTINF:"):
            out.append(line)
            continue
        total += 1
        dur, attrs, name = split_extinf(line)
        if attrs.get("tvg-logo") and not overwrite:
            out.append(line)
            continue
        url = matcher.find(name, attrs, attrs.get("group-title", ""), prefer_ru)
        if url:
            attrs["tvg-logo"] = url
            out.append(build_extinf(dur, attrs, name) + "\n")
            added += 1
        else:
            out.append(line)
    return out, added, total
