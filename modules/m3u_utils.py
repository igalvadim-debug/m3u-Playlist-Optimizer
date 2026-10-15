#!/usr/bin/env python3
"""
Общие функции для разбора и сборки M3U: строка #EXTINF, атрибуты, группы, ключ дубликата по URL.
"""
import re
from urllib.parse import urlsplit

_ATTR_RE = re.compile(r'([\w\-.]+)\s*=\s*(?:"([^"]*)"|([^\s"]+))')
_URL_RE = re.compile(r'^[a-z][a-z0-9+.\-]*://', re.I)


def _parse_attrs(text):
    attrs = {}
    for m in _ATTR_RE.finditer(text):
        attrs[m.group(1)] = m.group(2) if m.group(2) is not None else (m.group(3) or "")
    return attrs


def split_extinf(info):
    """'#EXTINF:-1 a="b",Имя' -> (длительность, dict атрибутов, имя)."""
    s = info.strip()
    body = s[len("#EXTINF:"):] if s.upper().startswith("#EXTINF:") else s
    in_quotes = False
    comma = -1
    for i, ch in enumerate(body):
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == "," and not in_quotes:
            comma = i
            break
    head = body[:comma] if comma >= 0 else body
    name = body[comma + 1:].strip() if comma >= 0 else ""
    m = re.match(r"\s*(-?\d+(?:\.\d+)?)", head)
    dur = m.group(1) if m else "-1"
    attr_part = head[m.end():] if m else head
    return dur, _parse_attrs(attr_part), name


def build_extinf(dur, attrs, name):
    parts = [f"#EXTINF:{dur}"]
    for k, v in attrs.items():
        parts.append(f'{k}="{str(v).replace(chr(34), chr(39))}"')
    return " ".join(parts) + "," + name


def set_group(info, group):
    """Возвращает строку #EXTINF с новым group-title (без перевода строки)."""
    dur, attrs, name = split_extinf(info)
    attrs["group-title"] = group
    return build_extinf(dur, attrs, name)


def url_key(url, ignore_query=False):
    """Ключ для поиска одинаковых адресов потоков."""
    u = url.strip()
    try:
        p = urlsplit(u)
        key = f"{p.scheme.lower()}://{p.netloc.lower()}{p.path}"
        if not ignore_query and p.query:
            key += "?" + p.query
        return key
    except Exception:
        return u.split("?", 1)[0] if ignore_query else u


def parse_m3u(path):
    """
    Читает M3U. Возвращает (список EPG-ссылок из шапки, список записей).
    Запись: dict(name, group, dur, attrs, extra, url, info)
    extra — доп. строки между #EXTINF и URL (#EXTVLCOPT и т. п.).
    """
    epg = []
    entries = []
    with open(path, "r", encoding="utf-8-sig", errors="ignore") as f:
        lines = f.read().splitlines()

    pending = None
    extra = []
    ext_group = None
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        up = line.upper()
        if up.startswith("#EXTM3U"):
            hdr = _parse_attrs(line[len("#EXTM3U"):])
            for key in ("url-tvg", "x-tvg-url"):
                if hdr.get(key):
                    epg.extend(u.strip() for u in hdr[key].split(",") if u.strip())
            continue
        if up.startswith("#EXTINF:"):
            pending = line
            extra = []
            ext_group = None
            continue
        if line.startswith("#"):
            if pending is not None:
                if up.startswith("#EXTGRP:"):
                    ext_group = line.split(":", 1)[1].strip()
                else:
                    extra.append(line)
            continue
        if pending is not None or _URL_RE.match(line):
            info = pending or "#EXTINF:-1,Без имени"
            dur, attrs, name = split_extinf(info)
            group = (attrs.get("group-title") or ext_group or "").strip()
            entries.append({
                "name": name or "Без имени",
                "group": group,
                "dur": dur,
                "attrs": attrs,
                "extra": list(extra),
                "url": line,
                "info": info,
            })
            pending = None
            extra = []
            ext_group = None
    return epg, entries


def entry_to_lines(entry, group=None):
    """Строки M3U для записи; group — новое имя группы (иначе исходное)."""
    attrs = dict(entry.get("attrs", {}))
    g = entry.get("group", "") if group is None else group
    if g:
        attrs["group-title"] = g
    info = build_extinf(entry.get("dur", "-1"), attrs, entry["name"])
    out = [info + "\n"]
    out.extend(x + "\n" for x in entry.get("extra", []))
    out.append(entry["url"] + "\n")
    return out
