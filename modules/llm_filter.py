#!/usr/bin/env python3
"""
M3U LLM-Filter Module

Шаг 1: FFmpeg отсеивает мёртвые потоки.
Шаг 2: из каждого рабочего потока берётся один кадр, и модель Qwen3-VL
       (через llama-server на порту 7845) решает: это заглушка с текстом
       об оплате/подписке или нормальная картинка. Заглушки удаляются.
"""
import base64
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from modules.tester import M3UTester
from modules.organizer import ADULT_RE

logger = logging.getLogger("m3ugenius.llm_filter")


# ---------------------------------------------------------------------------
# Настройки (переопределяются переменными окружения)
# ---------------------------------------------------------------------------
LLM_HOST = os.environ.get("M3UGENIUS_LLM_HOST", "127.0.0.1")
LLM_PORT = int(os.environ.get("M3UGENIUS_LLM_PORT", "7845"))

SERVER_EXE = Path(os.environ.get("M3UGENIUS_LLAMA_SERVER", r"D:\Unsloth\C-claude\llama.cpp\llama-server.exe"))
MODEL_PATH = Path(os.environ.get(
    "M3UGENIUS_MODEL_PATH", r"C:\ai_diffusion\models\LLM\Qwen3-VL-4B-Instruct-abliterated.Q8_0.gguf"))
MMPROJ_PATH = Path(os.environ.get(
    "M3UGENIUS_MMPROJ_PATH", r"C:\ai_diffusion\models\LLM\mmproj-F16.gguf"))

SERVER_LOG = Path(os.environ.get("M3UGENIUS_OUTPUT_DIR", "outputs")) / "llama_server_7845.log"
SERVER_START_TIMEOUT = 240  # секунд на загрузку модели
FRAME_STEP = 2.0  # интервал между кадрами одного канала, секунды (два кадра ≈ 4 сек)

PROMPT = (
    "You are checking a single frame taken from a live TV stream. "
    "Answer with EXACTLY one line, format TAG or TAG:LANG.\n"
    "TAG is one of:\n"
    "- STUB: the frame is a placeholder/stub screen instead of real TV content — its main content is a "
    "TEXT message that the channel/service cannot be watched. Examples: payment or subscription required "
    "(pay, top up balance, subscription expired, buy a package, prices, bank cards); the service/app is no "
    "longer available or shut down or you should use another app; not available in your country / geo-blocked; "
    "channel offline, no signal, transmission ended; error or access denied; a farewell/closing screen telling "
    "where to watch now. The stub text can be in any language.\n"
    "- ADULT: the frame shows explicit pornographic / 18+ content (visible nudity or a sex act).\n"
    "- OK: anything else — a normal TV picture (movie, show, news, sport, cartoon, music clip), a channel "
    "logo, a test card, a plain black screen, or a small watermark/ticker.\n"
    "LANG (optional, only when TAG is OK): the main on-screen language/country of the visible text or "
    "content, one of RU, DE, EN, AR, TR, FR, IT, ES, UA, PL, OTHER. If unsure, omit LANG.\n"
    "Examples: STUB | ADULT | OK | OK:RU | OK:DE | OK:EN | OK:AR | OK:TR.\n"
    "Answer with that single line only, no other words."
)

# Языки, которые распознаёт VL-модель (подсказка для групп иностранных каналов)
_LANG_OK = {"RU", "DE", "EN", "AR", "TR", "FR", "IT", "ES", "UA", "PL", "OTHER"}
_VERDICT_RE = re.compile(r"\b(STUB|ADULT|OK)\b\s*(?:[:;(\-]\s*([A-Za-z]{2}))?", re.I)


def parse_verdict(answer):
    """Ответ модели -> (тег, язык). Тег: STUB / ADULT / OK. Не распознали -> ('OK', '')."""
    m = _VERDICT_RE.search(answer or "")
    if not m:
        return "OK", ""
    tag = m.group(1).upper()
    lang = (m.group(2) or "").upper()
    return tag, (lang if lang in _LANG_OK else "")


def _top_reasons(dead, top=3):
    """Самые частые причины отказа FFmpeg: список (количество, текст)."""
    counts = {}
    for r in dead:
        raw = str(r.get("error") or r.get("status") or "?").strip().splitlines() or ["?"]
        key = re.sub(r"@ 0x?[0-9a-fA-F]+", "@", raw[0])[:90]
        counts[key] = counts.get(key, 0) + 1
    return sorted(((c, k) for k, c in counts.items()), reverse=True)[:top]


# Кодеки аудио из вывода ffprobe: пустота или «не-звук» — плохое аудио канала
_AUDIO_OK_CODECS = {"aac", "mp3", "mp2", "mp2float", "ac3", "eac3", "opus", "vorbis", "pcm_s16le",
                    "pcm_s16be", "pcm_s24le", "pcm_f32le", "adts", "alac", "flac", "amrnb", "amrwb",
                    "cook", "atrc", "sipr", "ra_144", "ra_288", "dca", "truehd", "mp4als"}


def _probe_audio(url, timeout=8):
    """Проверить аудио дорожку потока через ffprobe.

    Возвращает True (аудио в порядке), False (аудио нет или оно не звуковое)
    либо None (проверить невозможно: ffprobe молчит/таймаут — канал остаётся в плейлисте).
    """
    cmd = ["ffprobe", "-hide_banner", "-loglevel", "error",
           "-select_streams", "a",
           "-show_entries", "stream=codec_type,codec_name",
           "-of", "csv=p=0", url]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except Exception:
        return None
    try:
        out, _ = proc.communicate(timeout=timeout + 2)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:
            pass
        return None
    text = (out or b"").decode("utf-8", errors="ignore")
    codecs = []
    types = []
    for line in text.splitlines():
        parts = [p.strip().lower() for p in line.split(",")]
        if not any(parts):
            continue
        if "audio" in parts:
            types.append("audio")
        codecs.extend(p for p in parts if p and p != "audio")
    if not codecs and not types:
        # ffprobe ничего не нашёл: таймаут сети или действительно нет аудио
        if proc.returncode == 0:
            return False
        return None
    if "audio" not in types:
        return False
    return all(c in _AUDIO_OK_CODECS for c in codecs)


class LLMFilter:
    def __init__(self, ffmpeg_timeout=8, ffmpeg_workers=15, llm_workers=2,
                 remove_no_frame=False, frames_per_channel=2, keep_server=False,
                 check_bad_audio=True, separate_adult=True, adult_by_frame=True):
        self.separate_adult = bool(separate_adult)
        self.adult_by_frame = bool(adult_by_frame)
        self.keep_server = bool(keep_server)
        self.ffmpeg_timeout = int(ffmpeg_timeout)
        self.ffmpeg_workers = int(ffmpeg_workers)
        self.llm_workers = max(1, int(llm_workers))
        self.remove_no_frame = bool(remove_no_frame)
        self.frames_per_channel = max(1, min(8, int(frames_per_channel)))
        self.check_bad_audio = bool(check_bad_audio)

        self._proc = None
        self._own_server = False
        self._log_handle = None
        self._cb = None
        self._stop = None

    # ------------------------------------------------------------------
    # Вспомогательное
    # ------------------------------------------------------------------
    def _emit(self, frac, desc, line=None):
        if self._cb:
            self._cb(frac, desc, line)

    def _stopped(self):
        return self._stop is not None and self._stop.is_set()

    @staticmethod
    def channel_name(info):
        return info.rsplit(",", 1)[-1].strip() if "," in info else info

    @staticmethod
    def check_files():
        missing = [str(p) for p in (SERVER_EXE, MODEL_PATH, MMPROJ_PATH) if not p.exists()]
        if missing:
            raise FileNotFoundError("Не найдены файлы:\n" + "\n".join(missing))

    # ------------------------------------------------------------------
    # llama-server
    # ------------------------------------------------------------------
    def _server_ready(self):
        try:
            with urllib.request.urlopen(f"http://{LLM_HOST}:{LLM_PORT}/health", timeout=2) as r:
                return r.status == 200
        except Exception:
            return False

    def start_server(self):
        if self._server_ready():
            if self._proc is None:
                self._own_server = False
            self._emit(0.5, "Сервер модели уже запущен", f"🤖 llama-server уже работает на порту {LLM_PORT}, использую его")
            return

        self.check_files()
        SERVER_LOG.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            str(SERVER_EXE),
            "-m", str(MODEL_PATH),
            "--mmproj", str(MMPROJ_PATH),
            "-c", str(4096 * self.llm_workers),
            "-np", str(self.llm_workers),
            "-ngl", "99",
            "-fa", "on",
            "--jinja",
            "--host", LLM_HOST,
            "--port", str(LLM_PORT),
            "--temp", "0",
        ]
        self._emit(0.5, "Запуск сервера модели...", f"🚀 Запускаю llama-server на порту {LLM_PORT} (загрузка модели может занять время)")

        self._log_handle = open(SERVER_LOG, "wb")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self._proc = subprocess.Popen(
            cmd, stdout=self._log_handle, stderr=subprocess.STDOUT, creationflags=flags
        )
        self._own_server = True

        started = time.time()
        while time.time() - started < SERVER_START_TIMEOUT:
            if self._stopped():
                raise RuntimeError("Прервано пользователем")
            if self._proc.poll() is not None:
                raise RuntimeError(
                    f"llama-server завершился с кодом {self._proc.returncode}. "
                    f"Смотри лог: {SERVER_LOG.resolve()}"
                )
            if self._server_ready():
                self._emit(0.5, "Сервер модели готов", f"✅ llama-server готов (порт {LLM_PORT})")
                return
            time.sleep(1)

        raise RuntimeError(
            f"llama-server не запустился за {SERVER_START_TIMEOUT} сек. Смотри лог: {SERVER_LOG.resolve()}"
        )

    def stop_server(self):
        if self._proc is not None and self._own_server:
            try:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait()
            except Exception:
                pass
        self._proc = None
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except Exception:
                pass
            self._log_handle = None

    # ------------------------------------------------------------------
    # Кадр и запрос к модели
    # ------------------------------------------------------------------
    def grab_frame(self, url):
        """Один кадр из потока (JPEG, bytes) или None."""
        base_vf = "scale='min(768,iw)':-2"
        # сначала пропускаем первые кадры (часто чёрные/неполные), затем запасной вариант
        for vf in ("select=gte(n\\,10)," + base_vf, base_vf):
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-timeout", str(self.ffmpeg_timeout * 1000000),
                "-i", url,
                "-vf", vf,
                "-frames:v", "1",
                "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "4",
                "-",
            ]
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                try:
                    data, _ = proc.communicate(timeout=self.ffmpeg_timeout + 6)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()
                    continue
                if data and len(data) > 1000 and data[:2] == b"\xff\xd8":
                    return data
            except Exception:
                continue
        return None

    def grab_frames(self, url):
        """Несколько кадров с интервалом FRAME_STEP секунд (список JPEG bytes, может быть пустым)."""
        n = self.frames_per_channel
        if n <= 1:
            return []
        tmp = Path(tempfile.mkdtemp(prefix="m3u_frames_"))
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-timeout", str(self.ffmpeg_timeout * 1000000),
            "-i", url,
            "-vf", f"fps=1/{FRAME_STEP},scale='min(768,iw)':-2",
            "-frames:v", str(n), "-q:v", "4",
            str(tmp / "f_%d.jpg"),
        ]
        frames = []
        try:
            try:
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=self.ffmpeg_timeout + 10 + n * FRAME_STEP)
            except subprocess.TimeoutExpired:
                pass
            files = sorted(tmp.glob("f_*.jpg"), key=lambda p: int(p.stem.split("_")[1]))
            for p in files:
                data = p.read_bytes()
                if len(data) > 1000 and data[:2] == b"\xff\xd8":
                    frames.append(data)
        except Exception:
            pass
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return frames

    def ask_model(self, jpeg_bytes):
        b64 = base64.b64encode(jpeg_bytes).decode("ascii")
        payload = {
            "model": "qwen3-vl",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
                ],
            }],
            "temperature": 0,
            "max_tokens": 10,
            "stream": False,
        }
        req = urllib.request.Request(
            f"http://{LLM_HOST}:{LLM_PORT}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()

    def ask_text(self, prompt, max_tokens=120):
        """Текстовый запрос к модели (без картинки). Возвращает ответ строкой."""
        payload = {
            "model": "qwen3-vl",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": int(max_tokens),
            "stream": False,
        }
        req = urllib.request.Request(
            f"http://{LLM_HOST}:{LLM_PORT}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()

    @staticmethod
    def is_payment(answer):
        up = answer.upper()
        return "STUB" in up or "PAYMENT" in up

    # ------------------------------------------------------------------
    # Основной процесс
    # ------------------------------------------------------------------
    def run(self, m3u_files, progress_cb=None, stop_event=None):
        """
        progress_cb(frac, desc, line=None): frac 0..1, desc — подпись полосы прогресса,
        line — строка для журнала (или None).
        Возвращает dict: kept_lines, removed_lines, stats.
        """
        self._cb = progress_cb
        self._stop = stop_event

        stats = {
            "found": 0, "duplicates": 0,
            "ffmpeg_working": 0, "ffmpeg_dead": 0,
            "checked": 0, "payment": 0, "ok": 0,
            "no_frame": 0, "no_frame_removed": 0, "llm_errors": 0, "adult": 0,
            "bad_audio": 0, "bad_audio_removed": 0,
            "kept": 0, "interrupted": False,
        }

        # ---------------- Шаг 1: FFmpeg ----------------
        tester = M3UTester(timeout=self.ffmpeg_timeout, max_workers=self.ffmpeg_workers)

        streams = []
        for f in m3u_files:
            self._emit(0.0, "Чтение плейлистов...", f"📂 Читаю: {Path(f).name}")
            streams.extend(tester.extract_streams_from_m3u(f))
        for i, s in enumerate(streams):
            s["idx"] = i

        stats["found"] = len(streams)
        stats["duplicates"] = tester.stats["streams_duplicate"]

        if not streams:
            raise RuntimeError("В файлах нет потоков для проверки")

        kept, removed, bad_audio, adult = [], [], [], []

        # ---- Жёсткое правило 18+ по названию (если включено; язык не важен) ----
        if self.separate_adult:
            adult = [(s, "18+ (имя)") for s in streams if ADULT_RE.search(self.channel_name(s["info"]))]
            if adult:
                stats["adult"] += len(adult)
                streams = [s for s in streams if not ADULT_RE.search(self.channel_name(s["info"]))]
                self._emit(0.0, "18+ по названию",
                           f"🔞 Вынесено 18+ по названию: {len(adult)}")
        if not streams:
            self._emit(1.0, "Готово", "🔞 Все каналы вынесены как 18+")
            return self._build(kept, removed, stats, adult)

        self._emit(0.0, "FFmpeg: проверка потоков", f"🔍 Уникальных потоков: {len(streams)} (дубликатов пропущено: {stats['duplicates']}). Шаг 1: FFmpeg-проверка")

        working = []
        dead = []
        done = 0
        total = len(streams)
        with ThreadPoolExecutor(max_workers=self.ffmpeg_workers) as ex:
            futures = [ex.submit(tester.test_stream, s) for s in streams]
            for fut in as_completed(futures):
                if self._stopped():
                    for f2 in futures:
                        f2.cancel()
                    stats["interrupted"] = True
                    break
                res = fut.result()
                done += 1
                if res["status"] == "working":
                    stats["ffmpeg_working"] += 1
                    working.append(res)
                else:
                    stats["ffmpeg_dead"] += 1
                    dead.append(res)
                line = None
                if done % 25 == 0 or done == total:
                    line = f"FFmpeg: {done}/{total} · рабочих: {stats['ffmpeg_working']}"
                self._emit(0.5 * done / total,
                           f"FFmpeg: {done}/{total} · рабочих {stats['ffmpeg_working']}", line)

        # Диагностика: самые частые причины отказов
        if dead and not stats["interrupted"]:
            for cnt, why in _top_reasons(dead):
                self._emit(0.5, "FFmpeg: причины отказов", f"   ↳ {cnt}× {why}")

        # Почти всё «мертво»: возможно, провайдер временно режет запросы. Проба и повтор.
        if not stats["interrupted"] and total >= 20 and len(dead) >= 0.9 * total:
            self._emit(0.5, "FFmpeg: пауза перед повтором",
                       "⚠️ Почти все потоки отмечены мёртвыми. Жду 20 сек и проверяю 10 потоков по 3 параллельно")
            for _ in range(20):
                if self._stopped():
                    break
                time.sleep(1)
            with ThreadPoolExecutor(max_workers=3) as ex:
                probe_res = list(ex.map(tester.test_stream, dead[:10]))
            if any(r["status"] == "working" for r in probe_res) and not self._stopped():
                self._emit(0.5, "FFmpeg: повторная проверка",
                           "🔁 Часть потоков отвечает. Повторяю проверку мёртвых с 5 потоками")
                retry, dead = dead, []
                stats["ffmpeg_dead"] = 0
                done = 0
                with ThreadPoolExecutor(max_workers=5) as ex:
                    futures = [ex.submit(tester.test_stream, s) for s in retry]
                    for fut in as_completed(futures):
                        if self._stopped():
                            for f2 in futures:
                                f2.cancel()
                            stats["interrupted"] = True
                            break
                        res = fut.result()
                        done += 1
                        if res["status"] == "working":
                            stats["ffmpeg_working"] += 1
                            working.append(res)
                        else:
                            stats["ffmpeg_dead"] += 1
                            dead.append(res)
                        line = None
                        if done % 25 == 0 or done == len(retry):
                            line = f"Повтор: {done}/{len(retry)} · рабочих: {stats['ffmpeg_working']}"
                        self._emit(0.5 * done / len(retry),
                                   f"Повтор: {done}/{len(retry)} · рабочих {stats['ffmpeg_working']}", line)
            else:
                self._emit(0.5, "FFmpeg: потоки не отвечают",
                           "❌ Проба не нашла ни одного рабочего потока, повтор не нужен")

        working.sort(key=lambda s: s["idx"])

        if stats["interrupted"]:
            return self._build(kept, removed, stats, adult)

        if not working:
            self._emit(1.0, "Готово", "⚠️ Рабочих потоков не найдено")
            return self._build(kept, removed, stats, adult)

        # ---------------- Шаг 2: плохое аудио (ffprobe) ----------------
        # Каналы без звуковой дорожки откладываются в отдельный список:
        # они НЕ попадают ни в сортировку, ни в основной плейлист.
        if self.check_bad_audio:
            self._emit(0.5, "Проверка аудио", f"🔊 Шаг 2: ffprobe проверяет аудио у {len(working)} рабочих каналов")
            aud_checked, aud_ok = [], []
            with ThreadPoolExecutor(max_workers=self.ffmpeg_workers) as ex:
                futures = {ex.submit(_probe_audio, s["url"], self.ffmpeg_timeout): s for s in working}
                done = 0
                total = len(futures)
                for fut in as_completed(futures):
                    if self._stopped():
                        for f2 in futures:
                            f2.cancel()
                        stats["interrupted"] = True
                        return self._build(kept, removed, stats, adult)
                    s = futures[fut]
                    try:
                        verdict = fut.result()
                    except Exception:
                        verdict = None
                    done += 1
                    if verdict is False:
                        stats["bad_audio"] += 1
                        bad_audio.append(s)
                        line = f"🔇 Плохое аудио (нет звуковой дорожки): {self.channel_name(s['info'])}"
                    else:
                        # True или None (не удалось проверить) — канал остаётся
                        aud_ok.append(s)
                        line = None
                    aud_checked.append(s)
                    if done % 25 == 0 or done == total:
                        self._emit(0.5 + 0.1 * done / max(1, total),
                                   f"Аудио: {done}/{total} · проблемных {stats['bad_audio']}", line)
            working = [s for s in aud_checked if s in set(map(id, []))] if False else \
                sorted(aud_ok, key=lambda s: s["idx"])
            self._emit(0.6, "Модель: подготовка",
                       f"🔇 Отложено из-за плохого аудио: {stats['bad_audio']}, дальше проверяются {len(working)}")

        # ---------------- Шаг 3: модель ----------------
        try:
            self.start_server()

            n = len(working)
            if n:
                self._emit(0.6, f"Модель: 0/{n}", f"🤖 Модель проверяет {n} рабочих каналов")

            pool_size = self.llm_workers * 4
            sem = threading.Semaphore(self.llm_workers)

            def process(stream):
                if self._stopped():
                    return stream, "stopped", "", ""
                frames = self.grab_frames(stream["url"])
                if not frames:
                    single = self.grab_frame(stream["url"])
                    frames = [single] if single else []
                if not frames:
                    return stream, "noframe", "", ""
                last_answer, last_lang, error = "", "", ""
                with sem:
                    for jpeg in frames:
                        if self._stopped():
                            return stream, "stopped", "", ""
                        try:
                            last_answer = self.ask_model(jpeg)
                        except Exception as e:
                            error = str(e)[:120]
                            continue
                        tag, lang = parse_verdict(last_answer)
                        if lang:
                            last_lang = lang
                        if tag == "STUB":
                            return stream, "stub", last_lang, last_answer
                        if tag == "ADULT" and self.separate_adult and self.adult_by_frame:
                            return stream, "adult", last_lang, last_answer
                if error and not last_answer:
                    return stream, "llmerror", "", error
                return stream, "ok", last_lang, last_answer

            results = {}
            done = 0
            with ThreadPoolExecutor(max_workers=pool_size) as ex:
                futures = [ex.submit(process, s) for s in working]
                for fut in as_completed(futures):
                    if self._stopped():
                        for f2 in futures:
                            f2.cancel()
                        stats["interrupted"] = True
                        break
                    stream, verdict, lang, answer = fut.result()
                    done += 1
                    results[stream["idx"]] = (stream, verdict, lang, answer)
                    name = self.channel_name(stream["info"])

                    line = None
                    if verdict == "stub":
                        stats["payment"] += 1
                        line = f"🗑️ Удалён (заглушка): {name}"
                    elif verdict == "adult":
                        stats["adult"] += 1
                        line = f"🔞 18+ по кадру: {name}"
                    elif verdict == "ok":
                        stats["ok"] += 1
                    elif verdict == "noframe":
                        stats["no_frame"] += 1
                        line = f"⚠️ Нет кадра: {name}"
                    elif verdict == "llmerror":
                        stats["llm_errors"] += 1
                        line = f"❗ Ошибка модели ({name}): {answer}"

                    stats["checked"] = done
                    self._emit(0.5 + 0.5 * done / n,
                               f"Модель: {done}/{n} · удалено заглушек {stats['payment']}", line)

            if stats["interrupted"]:
                return self._build(kept, removed, stats, adult)

            for idx in sorted(results):
                stream, verdict, lang, answer = results[idx]
                if verdict == "stub":
                    removed.append((stream, "заглушка — " + (answer or "?")))
                elif verdict == "adult":
                    adult.append((stream, "18+ (кадр)"))
                elif verdict == "noframe" and self.remove_no_frame:
                    stats["no_frame_removed"] += 1
                    removed.append((stream, "кадр не получен"))
                else:
                    if lang:
                        stream["lang"] = lang
                    kept.append(stream)

        finally:
            if not self.keep_server:
                self.stop_server()

        self._emit(1.0, "Готово", "✅ Готово")
        return self._build(kept, removed, stats, adult)

    # ------------------------------------------------------------------
    def _build(self, kept, removed, stats, adult=None):
        adult = adult or []
        stats["kept"] = len(kept)
        now = datetime.now().isoformat()

        kept_lines = ["#EXTM3U\n",
                      f"# Сгенерировано: {now}\n",
                      f"# Каналов оставлено: {len(kept)}\n",
                      f"# Удалено заглушек: {stats['payment']}\n",
                      f"# Вынесено 18+: {len(adult)}\n\n"]
        for s in kept:
            kept_lines.append(f"{s['info']}\n")
            kept_lines.append(f"{s['url']}\n")

        adult_lines = ["#EXTM3U\n",
                       f"# Каналы 18+ — {now}\n\n"]
        for s, reason in adult:
            adult_lines.append(f"# Причина: {reason}\n")
            adult_lines.append(f"{s['info']}\n")
            adult_lines.append(f"{s['url']}\n")

        removed_lines = ["#EXTM3U\n",
                         f"# Удалённые каналы (отчёт) — {now}\n\n"]
        for s, reason in removed:
            removed_lines.append(f"# Причина: {reason}\n")
            removed_lines.append(f"{s['info']}\n")
            removed_lines.append(f"{s['url']}\n")

        report_lines = [f"# Отчёт LLM-фильтра — {now}\n",
                        f"# Оставлено: {len(kept)} | Удалено (заглушки): {len(removed)} | 18+: {len(adult)}\n\n"]
        for s in kept:
            report_lines.append(f"OK     | {self.channel_name(s['info'])}\n")
        for s, reason in removed:
            report_lines.append(f"УДАЛЕНО | {reason} | {self.channel_name(s['info'])}\n")
        for s, reason in adult:
            report_lines.append(f"18+    | {reason} | {self.channel_name(s['info'])}\n")

        return {"kept_lines": kept_lines, "removed_lines": removed_lines,
                "adult_lines": adult_lines, "report_lines": report_lines,
                "kept_streams": kept, "stats": stats}
