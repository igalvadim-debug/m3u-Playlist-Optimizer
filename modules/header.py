#!/usr/bin/env python3
"""
M3U Header Module
Единая шапка плейлиста: EPG-ссылка (url-tvg) и catchup.
Используется всеми вкладками, которые сохраняют M3U.
"""

DEFAULT_EPG = "https://www.open-epg.com/files/russia3.xml.gz"


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
