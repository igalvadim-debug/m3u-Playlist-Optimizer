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
import http.client
import io
import ipaddress
import re
import socket
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

SUFFIXES = ("fullhd", "fhd", "uhd", "4k", "hd", "sd")
MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024  # потолок на скачанный ответ, защита от OOM/зловредных серверов


def _safe_socket_connect(address, timeout=None, source_address=None, *, all_errors=False):
    """socket.create_connection с проверкой: запрещённые адреса (SSRF-guard, в т. ч. DNS-rebinding).
    Совместим по сигнатуре с socket.create_connection (используется как instance-level
    _create_connection в HTTPConnection.connect)."""
    host, port = address
    infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    if not infos:
        raise OSError(f"Cannot determine address of {host!r}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError(f"EPG-источник указывает на запрещённый адрес: {ip}")
    family, stype, proto, _, addr = infos[0]
    sock = socket.socket(family, stype, proto)
    if timeout is not None:
        sock.settimeout(timeout)
    if source_address:
        sock.bind(source_address)
    try:
        sock.connect(addr)
    except OSError:
        sock.close()
        raise
    return sock


class _SafeHTTPConnection(http.client.HTTPConnection):
    """SSRF-guard только для этого экземпляра: HTTPConnection.connect использует
    self._create_connection (по умолчанию socket.create_connection)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)  # базовый init присваивает self._create_connection
        self._create_connection = _safe_socket_connect


class _SafeHTTPSConnection(http.client.HTTPSConnection):
    """connect() у HTTPSConnection вызывает super().connect() (HTTPConnection),
    который берёт self._create_connection — guard наследуется без глобального monkey-patch."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _safe_socket_connect


class _SafeHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_SafeHTTPConnection, req)


class _SafeHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_SafeHTTPSConnection, req)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Проверяет каждую цель редиректа через validate_epg_url (в т. ч. смену схемы на ftp и т. п.)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        reason = validate_epg_url(newurl)
        if reason:
            raise urllib.error.HTTPError(req.full_url, code, f"редирект запрещён: {reason}", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SafeHTTPHandler(), _SafeHTTPSHandler(), _SafeRedirectHandler())


def validate_epg_url(url):
    """Проверяет, что ссылка безопасна для скачивания. Возвращает причину отказа или None."""
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return "некорректный URL"
    if parsed.scheme not in ("http", "https"):
        return f"схема '{parsed.scheme}' запрещена (только http/https)"
    host = parsed.hostname
    if not host:
        return "в URL нет хоста"
    try:
        # если хост — это IP, проверяем сразу; иначе резолвим и проверяем все адреса
        try:
            ips = [ipaddress.ip_address(host)]
        except ValueError:
            try:
                infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                                           0, socket.SOCK_STREAM)
            except socket.gaierror:
                return f"не удалось разрешить хост '{host}'"
            ips = [ipaddress.ip_address(i[4][0]) for i in infos]
        if not ips:
            return f"хост '{host}' не имеет адресов"
        for ip in ips:
            if not ip.is_global:
                return f"хост '{host}' указывает на запрещённый адрес {ip} (локальная сеть/loopback)"
    except ValueError as e:
        return str(e)
    return None


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


class _LimitedRead:
    """File-like обёртка: отдаёт не более limit байт, дальше — пустой поток (защита от OOM)."""

    def __init__(self, stream, limit):
        self._s = stream
        self._left = int(limit)

    def read(self, n=-1):
        if self._left <= 0:
            return b""
        if n is None or n < 0:
            data = self._s.read(self._left)
        else:
            data = self._s.read(min(n, self._left))
        self._left -= len(data)
        return data

    def readline(self, limit=-1):
        # для совместимости с gzip.GzipFile(fileobj=...), который читает заголовок построчно
        chunk = self.read(1)
        line = chunk
        while chunk and chunk != b"\n":
            if limit >= 0 and len(line) >= limit:
                break
            chunk = self.read(1)
            line += chunk
        return line

    def seek(self, *args):
        raise io.UnsupportedOperation("seek")

    seekable = lambda self: False


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
        resp = _opener.open(req, timeout=timeout)  # SSRF-guard: соединение только с публичными IP
        # Потолковый лимит объёма скачивания (защита от «бесконечного» ответа / OOM)
        stream = _LimitedRead(resp, MAX_DOWNLOAD_BYTES)
        head = stream.read(2)
        if head == b"\x1f\x8b":
            # лимит применяем и к РАЗЖАТОМУ потоку: gzip-бомбы могут дать ГБ данных из малых файлов
            return _LimitedRead(gzip.GzipFile(fileobj=_Prepend(head, stream)), MAX_DOWNLOAD_BYTES)
        return _Prepend(head, stream)

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
            reason = validate_epg_url(u)
            if reason:
                self.stats["errors"].append(f"{u}: {reason}")
                log(f"  \u2717 пропущен небезопасный источник [{i + 1}/{len(urls)}] {u}: {reason}")
                continue
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
