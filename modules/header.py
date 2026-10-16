#!/usr/bin/env python3
"""
M3U Header Module
Единая шапка плейлиста: EPG-ссылка (url-tvg) и catchup.
Используется всеми вкладками, которые сохраняют M3U.
"""

DEFAULT_EPG = "https://www.open-epg.com/files/russia3.xml.gz"

# Готовые EPG-источники: (название для выпадающего списка, ссылка, подсказка при наведении мыши)
EPG_SOURCES = [
    ("OTTCLUB", "https://ottepg.ru/ottepg.xml.gz",
     "EPG для OTTCLUB: https://ottepg.ru/ottepg.xml.gz"),
    ("Kineskop TV", "http://st.kineskop.tv/epg.xml.gz",
     "EPG для Kineskop TV: http://st.kineskop.tv/epg.xml.gz"),
    ("Sharavoz TV", "http://iptv-content.webhop.net/guide.xml",
     "EPG для Sharavoz TV: http://iptv-content.webhop.net/guide.xml"),
    ("Shara TV", "http://stb.shara-tv.org/epg/epgtv.xml.gz",
     "EPG для Shara TV: http://stb.shara-tv.org/epg/epgtv.xml.gz"),
    ("TopIPTV", "http://topiptv.info/download/topiptv.xml.gz",
     "EPG для TopIPTV: http://topiptv.info/download/topiptv.xml.gz"),
    ("ILOOK TV (Edem TV)", "http://epg.it999.ru/edem.xml.gz",
     "EPG для ILOOK TV (Edem TV): http://epg.it999.ru/edem.xml.gz"),
    ("IPTVx.one (категории, логотипы, ТБ ГИД)", "https://iptvx.one/epg/epg.xml.gz",
     "Категории, логотипы и ТБ ГИД: https://iptvx.one/epg/epg.xml.gz"),
    ("IPTVx.one lite (только название передачи)", "https://iptvx.one/epg/epg_lite.xml.gz",
     "Отображает только название передачи: https://iptvx.one/epg/epg_lite.xml.gz"),
    ("Teleguide (максимально полный)", "http://www.teleguide.info/download/new3/xmltv.xml.gz",
     "Максимально полный EPG: http://www.teleguide.info/download/new3/xmltv.xml.gz"),
]

EPG_CHOICES = [name for name, _, _ in EPG_SOURCES]
EPG_URL_BY_NAME = {name: url for name, url, _ in EPG_SOURCES}
EPG_TITLES = "\n".join(f"{i + 1}. {title}" for i, (_, _, title) in enumerate(EPG_SOURCES))


def epg_from_choice(choice):
    """Ссылка(и) из выбранного пункта списка: имя источника или свой URL как есть."""
    if not choice:
        return ""
    parts = []
    for c in choice if isinstance(choice, (list, tuple)) else [choice]:
        c = str(c).strip()
        if not c:
            continue
        parts.append(EPG_URL_BY_NAME.get(c, c))
    return ",".join(parts)


def split_epg(epg_url=""):
    """Список ссылок EPG из строки (разделители: запятая, пробел, перевод строки)."""
    urls = [u.strip().replace('"', '') for u in (epg_url or "").replace("\n", ",").replace(" ", ",").split(",")]
    return [u for u in urls if u]


def build_header(epg_url="", catchup=True, single=False):
    """
    Собирает строку заголовка, например:
    #EXTM3U catchup="append" catchup-source="?utc=${start}" url-tvg="https://..."
    single=True: в шапку попадает только первая ссылка EPG.
    Возвращает None, если ни EPG, ни catchup не нужны.
    """
    parts = ["#EXTM3U"]
    if catchup:
        parts.append('catchup="append"')
        parts.append('catchup-source="?utc=${start}"')

    urls = split_epg(epg_url)
    if single:
        urls = urls[:1]
    if urls:
        parts.append('url-tvg="' + ",".join(urls) + '"')

    if len(parts) == 1:
        return None
    return " ".join(parts) + "\n"


def apply_header(lines, epg_url="", catchup=True, single=False):
    """
    Ставит заголовок первой строкой списка lines (старые #EXTM3U убираются).
    Если EPG пустая и catchup выключен, строки возвращаются без изменений.
    """
    header = build_header(epg_url, catchup, single)
    if header is None:
        return lines
    rest = [ln for ln in lines if not ln.strip().startswith("#EXTM3U")]
    return [header] + rest
