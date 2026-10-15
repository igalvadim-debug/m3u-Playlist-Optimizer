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


# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------
LLM_HOST = "127.0.0.1"
LLM_PORT = 7845

SERVER_EXE = Path(r"D:\Unsloth\C-claude\llama.cpp\llama-server.exe")
MODEL_PATH = Path(r"C:\ai_diffusion\models\LLM\Qwen3-VL-4B-Instruct-abliterated.Q8_0.gguf")
MMPROJ_PATH = Path(r"C:\ai_diffusion\models\LLM\mmproj-F16.gguf")

SERVER_LOG = Path("outputs") / "llama_server_7845.log"
SERVER_START_TIMEOUT = 240  # секунд на загрузку модели
FRAME_STEP = 1.5  # интервал между кадрами одного канала, секунды (заглушки-анимации меняются во времени)

PROMPT = (
    "You are checking a single frame taken from a live TV stream. "
    "Decide whether the frame is a placeholder/stub screen instead of real TV content: "
    "a screen whose main content is a TEXT message saying that the channel or service cannot be watched. "
    "Examples of such stubs: "
    "(1) payment or subscription required: 'pay for the subscription', 'subscription expired', "
    "'top up your balance', 'access is closed until payment', 'buy a package', prices, bank cards, "
    "activation or renewal instructions; "
    "(2) the service or app is no longer available, was discontinued or shut down, 'no longer available "
    "on this device', 'the end', \"C'est fini\", please use another app or update the app; "
    "(3) the channel is not available in your country or region, geo-blocked; "
    "(4) the channel is offline, temporarily unavailable, off air, 'no signal', transmission ended; "
    "(5) error or access-denied messages, device not supported; "
    "(6) a farewell or closing screen: 'that's all', 'that's a wrap', 'it's over', 'we are leaving', "
    "or an instruction to go to a website or app to find where to watch the channels now or next. "
    "The text can be in any language (Russian, English, German, French, etc.). "
    "A normal TV picture (movie, show, news, sport, cartoon, music clip), a channel logo, a test card, "
    "a plain black screen, or a small watermark/ticker is NOT such a stub. "
    "Answer with exactly one word: STUB if it is such a stub screen, otherwise OK."
)


def _top_reasons(dead, top=3):
    """Самые частые причины отказа FFmpeg: список (количество, текст)."""
    counts = {}
    for r in dead:
        raw = str(r.get("error") or r.get("status") or "?").strip().splitlines() or ["?"]
        key = re.sub(r"@ 0x?[0-9a-fA-F]+", "@", raw[0])[:90]
        counts[key] = counts.get(key, 0) + 1
    return sorted(((c, k) for k, c in counts.items()), reverse=True)[:top]


class LLMFilter:
    def __init__(self, ffmpeg_timeout=8, ffmpeg_workers=15, llm_workers=2,
                 remove_no_frame=False, frames_per_channel=3, keep_server=False):
        self.keep_server = bool(keep_server)
        self.ffmpeg_timeout = int(ffmpeg_timeout)
        self.ffmpeg_workers = int(ffmpeg_workers)
        self.llm_workers = max(1, int(llm_workers))
        self.remove_no_frame = bool(remove_no_frame)
        self.frames_per_channel = max(1, min(8, int(frames_per_channel)))

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
            "no_frame": 0, "no_frame_removed": 0, "llm_errors": 0,
            "kept": 0, "interrupted": False, "bad_audio": 0,
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

        # Отделяем каналы с неподдерживаемым аудио (MP2/MP1): в LLM не идут, в отдельный файл
        self._bad_audio = [s for s in working if s.get("audio") in M3UTester.BAD_AUDIO]
        if self._bad_audio:
            working = [s for s in working if s.get("audio") not in M3UTester.BAD_AUDIO]
            self._emit(0.5, "FFmpeg: аудио", f"🔇 Без звука (MP2/MP1): {len(self._bad_audio)} — в отдельный файл")
        stats["bad_audio"] = len(self._bad_audio)
        kept, removed = [], []

        if stats["interrupted"]:
            return self._build(kept, removed, stats)

        if not working:
            self._emit(1.0, "Готово", "⚠️ Рабочих потоков не найдено")
            return self._build(kept, removed, stats)

        # ---------------- Шаг 2: модель ----------------
        try:
            self.start_server()

            n = len(working)
            self._emit(0.5, f"Модель: 0/{n}", f"🤖 Шаг 2: модель проверяет {n} рабочих каналов")

            pool_size = self.llm_workers * 4
            sem = threading.Semaphore(self.llm_workers)

            def process(stream):
                if self._stopped():
                    return stream, "stopped", ""
                frames = self.grab_frames(stream["url"])
                if not frames:
                    single = self.grab_frame(stream["url"])
                    frames = [single] if single else []
                if not frames:
                    return stream, "noframe", ""
                last_answer, error = "", ""
                with sem:
                    for jpeg in frames:
                        if self._stopped():
                            return stream, "stopped", ""
                        try:
                            last_answer = self.ask_model(jpeg)
                        except Exception as e:
                            error = str(e)[:120]
                            continue
                        if self.is_payment(last_answer):
                            return stream, "payment", last_answer
                if error and not last_answer:
                    return stream, "llmerror", error
                return stream, "ok", last_answer

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
                    stream, verdict, answer = fut.result()
                    done += 1
                    results[stream["idx"]] = (stream, verdict, answer)
                    name = self.channel_name(stream["info"])

                    line = None
                    if verdict == "payment":
                        stats["payment"] += 1
                        line = f"🗑️ Удалён (заглушка): {name}"
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
                return self._build(kept, removed, stats)

            for idx in sorted(results):
                stream, verdict, answer = results[idx]
                if verdict == "payment":
                    removed.append((stream, "заглушка: " + answer))
                elif verdict == "noframe" and self.remove_no_frame:
                    stats["no_frame_removed"] += 1
                    removed.append((stream, "кадр не получен"))
                else:
                    kept.append(stream)

        finally:
            if not self.keep_server:
                self.stop_server()

        self._emit(1.0, "Готово", "✅ Готово")
        return self._build(kept, removed, stats)

    # ------------------------------------------------------------------
    def _build(self, kept, removed, stats):
        bad_audio = getattr(self, '_bad_audio', [])
        stats["kept"] = len(kept)
        now = datetime.now().isoformat()

        kept_lines = ["#EXTM3U\n",
                      f"# Сгенерировано: {now}\n",
                      f"# Каналов оставлено: {len(kept)}\n",
                      f"# Удалено заглушек: {stats['payment']}\n\n"]
        for s in kept:
            kept_lines.append(f"{s['info']}\n")
            kept_lines.append(f"{s['url']}\n")

        removed_lines = ["#EXTM3U\n",
                         f"# Удалённые каналы (отчёт) — {now}\n\n"]
        for s, reason in removed:
            removed_lines.append(f"# Причина: {reason}\n")
            removed_lines.append(f"{s['info']}\n")
            removed_lines.append(f"{s['url']}\n")

        bad_audio_lines = ["#EXTM3U\n",
                           f"# Каналы без звука в приложении (MP2/MP1) — {now}\n",
                           f"# Таких каналов: {len(bad_audio)}\n\n"]
        for s in bad_audio:
            bad_audio_lines.append(f"{s['info']}\n")
            bad_audio_lines.append(f"{s['url']}\n")

        return {"kept_lines": kept_lines, "removed_lines": removed_lines, "kept_streams": kept,
                "bad_audio_lines": bad_audio_lines, "stats": stats}
