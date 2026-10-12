#!/usr/bin/env python3
"""
M3U Header Module
Единая шапка плейлиста: EPG-ссылка (url-tvg) и catchup.
Используется всеми вкладками, которые сохраняют M3U.
"""

DEFAULT_EPG = "https://www.open-epg.com/files/russia3.xml.gz"


def build_header(epg_url="", catchup=True):
    """
    Собирает строку заголовка, например:
    #EXTM3U catchup="append" catchup-source="?utc=${start}" url-tvg="https://..."
    Возвращает None, если ни EPG, ни catchup не нужны.
    """
    parts = ["#EXTM3U"]
    if catchup:
        parts.append('catchup="append"')
        parts.append('catchup-source="?utc=${start}"')

    # Можно указать несколько ссылок через запятую или пробел
    urls = [u.strip().replace('"', '') for u in (epg_url or "").replace("\n", ",").replace(" ", ",").split(",")]
    urls = [u for u in urls if u]
    if urls:
        parts.append('url-tvg="' + ",".join(urls) + '"')

    if len(parts) == 1:
        return None
    return " ".join(parts) + "\n"


def apply_header(lines, epg_url="", catchup=True):
    """
    Ставит заголовок первой строкой списка lines (старые #EXTM3U убираются).
    Если EPG пустая и catchup выключен, строки возвращаются без изменений.
    """
    header = build_header(epg_url, catchup)
    if header is None:
        return lines
    rest = [ln for ln in lines if not ln.strip().startswith("#EXTM3U")]
    return [header] + rest
