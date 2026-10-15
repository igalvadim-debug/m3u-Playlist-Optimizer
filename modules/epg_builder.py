#!/usr/bin/env python3
"""
EPG Builder

Скачивает XMLTV (xml / xml.gz) из указанных ссылок, оставляет ТОЛЬКО каналы,
которые есть в плейлисте (сопоставление по имени канала), и вписывает tvg-id
в строки #EXTINF — чтобы плеер находил программу по id, а не по имени.

Зависимостей нет: urllib + gzip + xml.etree (всё стандартное).
Всё делается потоково: большие файлы (сотни МБ / ГБ после разжатия) не грузятся в память.
"""
import gzip
import io
import re
import urllib.request
import xml.etree.ElementTree as ET

SUFFIXES = ("fullhd", "fhd", "uhd", "4k", "hd", "sd")


def norm(name):
    """Нормализация имени канала для сопоставления (как в плеере EpgIndex.norm)."""
    if not name:
        return ""
    x = name.lower()
    x = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", x)
    x = re.sub(r"[^\w]+", "", x, flags=re.UNICODE).replace("_", "")
    cut = True
    while cut and len(x) > 3:
        cut = False
        for suf in SUFFIXES:
            if len(x) - len(suf) >= 2 and x.endswith(suf):
                x = x[:-len(suf)]
                cut = True
                break
    return x


def extinf_name(line):
    """Имя канала из строки #EXTINF (после первого 'запятой вне кавычек')."""
    body = line.split(":", 1)[1] if ":" in line else line
    inq = False
    for j, ch in enumerate(body):
        if ch == '"':
            inq = not inq
        elif ch == "," and not inq:
            return body[j + 1:].strip()
    return ""


def add_tvgid(line, tvg_id):
    """Вставляет/перезаписывает tvg-id в строке #EXTINF."""
    if not tvg_id or not line.startswith("#EXTINF:"):
        return line
    body = line[len("#EXTINF:"):]
    m = re.match(r"([^ ,]+)(.*)$", body, re.S)
    if not m:
        return line
    dur, rest = m.group(1), m.group(2)
    if "tvg-id=" in rest:
        rest = re.sub(r'tvg-id="[^"]*"', 'tvg-id="%s"' % tvg_id, rest, count=1)
        return "#EXTINF:" + dur + rest
    return "#EXTINF:" + dur + ' tvg-id="%s"' % tvg_id + rest


class _Prepend:
    """File-like: сначала отдаёт prefix (уже прочитанные байты), потом поток."""

    def __init__(self, prefix, stream):
        self._buf = prefix
        self._s = stream

    def read(self, n=-1):
        if not self._buf:
            return self._s.read(n)
        if n is None or n < 0:
            out = self._buf + self._s.read()
            self._buf = b""
            return out
        out = self._buf[:n]
        self._buf = self._buf[n:]
        if len(out) < n:
            out += self._s.read(n - len(out))
        return out


class EpgBuilder:
    def __init__(self):
        self.stats = {
            "channels_in_playlist": 0,
            "sources": 0,
            "epg_channels": 0,
            "programmes": 0,
            "tvgid_added": 0,
            "errors": [],
        }

    # ---------- плейлист ----------
    @staticmethod
    def read_playlist_names(m3u_files):
        names = set()
        for f in m3u_files:
            try:
                with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                    prev = None
                    for ln in fh:
                        s = ln.strip()
                        if s.startswith("#EXTINF"):
                            prev = s
                        elif s and not s.startswith("#") and re.match(r"^[a-z]+://", s, re.I):
                            if prev:
                                nm = extinf_name(prev)
                                if nm:
                                    names.add(norm(nm))
                            prev = None
            except Exception:
                pass
        return names

    # ---------- поток ----------
    @staticmethod
    def _open_stream(url, timeout=300):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (m3uGenius)"})
        resp = urllib.request.urlopen(req, timeout=timeout)
        head = resp.read(2)
        if head == b"\x1f\x8b":
            return gzip.GzipFile(fileobj=_Prepend(head, resp))
        return _Prepend(head, resp)

    # ---------- фильтрация ----------
    def _filter_into(self, stream, want_norm, name2id, out):
        kept_ids = set()
        context = ET.iterparse(stream, events=("start", "end"))
        try:
            _, root = next(context)
        except StopIteration:
            return
        count = 0
        for event, elem in context:
            if event != "end":
                continue
            tag = elem.tag.rsplit("}", 1)[-1]
            if tag == "channel":
                cid = elem.get("id") or ""
                disp = [(c.text or "") for c in elem if c.tag.rsplit("}", 1)[-1] == "display-name"]
                if any(norm(d) in want_norm for d in disp if d):
                    kept_ids.add(cid)
                    for d in disp:
                        if d:
                            name2id.setdefault(norm(d), cid)
                    out.write(ET.tostring(elem, encoding="utf-8"))
                    out.write(b"\n")
                    self.stats["epg_channels"] += 1
                elem.clear()
            elif tag == "programme":
                if elem.get("channel") in kept_ids:
                    out.write(ET.tostring(elem, encoding="utf-8"))
                    out.write(b"\n")
                    self.stats["programmes"] += 1
                elem.clear()
            count += 1
            if count % 2000 == 0:
                root.clear()  # освобождаем накопленные в корне узлы (потоково, без роста памяти)
        root.clear()

    def build(self, m3u_files, source_urls, progress_cb=None):
        def log(m):
            if progress_cb:
                progress_cb(m)

        want = self.read_playlist_names(m3u_files)
        self.stats["channels_in_playlist"] = len(want)
        log(f"Каналов в плейлисте: {len(want)}")

        urls = [u.strip() for u in re.split(r"[,\n]", source_urls or "") if u.strip()]
        name2id = {}
        out = io.BytesIO()
        out.write(b'<?xml version="1.0" encoding="UTF-8"?>\n<tv>\n')
        for i, u in enumerate(urls):
            self.stats["sources"] += 1
            log(f"Скачиваю/разбираю EPG [{i + 1}/{len(urls)}]: {u}")
            try:
                stream = self._open_stream(u)
                self._filter_into(stream, want, name2id, out)
            except Exception as e:
                self.stats["errors"].append(f"{u}: {e}")
                log(f"  \u2717 ошибка: {e}")
        out.write(b"</tv>\n")
        log(f"Сопоставлено каналов EPG: {self.stats['epg_channels']}")
        return out.getvalue(), name2id

    @staticmethod
    def annotate_playlist(m3u_files, name2id):
        lines = []
        added = 0
        for f in m3u_files:
            try:
                with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                    for ln in fh:
                        s = ln.rstrip("\r\n")
                        if s.startswith("#EXTINF"):
                            cid = name2id.get(norm(extinf_name(s)))
                            if cid:
                                new = add_tvgid(s, cid)
                                if new != s:
                                    added += 1
                                s = new
                        lines.append(s + "\n")
            except Exception:
                pass
        return lines, added
