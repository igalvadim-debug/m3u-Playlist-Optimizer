#!/usr/bin/env python3
"""
M3U Tester Module
Тестирование M3U потоков через FFmpeg
ОБНОВЛЕННАЯ ВЕРСИЯ - Максимально стабильная проверка через FFmpeg
"""
import subprocess
from pathlib import Path
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import hashlib
import sys


class M3UTester:
    def __init__(self, timeout=8, max_workers=15):
        self.timeout = timeout
        self.max_workers = max_workers
        self.seen_streams = set()
        self.working_streams = []
        self.stats = {
            'total_streams_found': 0,
            'streams_tested': 0,
            'streams_working': 0,
            'streams_failed': 0,
            'streams_duplicate': 0
        }
        
        # Проверка FFmpeg при инициализации
        self._check_ffmpeg()
    
    def _check_ffmpeg(self):
        """Проверка доступности FFmpeg"""
        try:
            result = subprocess.run(
                ['ffmpeg', '-version'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=5
            )
            print("✓ FFmpeg найден и доступен")
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            print("✗ ОШИБКА: FFmpeg не найден или не работает!")
            print("  Установите FFmpeg и добавьте его в PATH")
            raise RuntimeError("FFmpeg недоступен")
    
    def get_stream_hash(self, url):
        """Создание хеша URL для определения дубликатов"""
        parsed = urlparse(url)
        clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        return hashlib.md5(clean_url.encode('utf-8')).hexdigest()
    
    def extract_streams_from_m3u(self, m3u_path):
        """Извлечение потоков из M3U файла"""
        streams = []
        try:
            with open(m3u_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
            
            current_info = None
            for line in lines:
                line = line.strip()
                
                if not line:
                    continue
                
                # Сохраняем информацию о канале
                if line.startswith('#EXTINF:'):
                    current_info = line
                    continue
                
                # Проверяем URL потока
                if (line and not line.startswith('#') and 
                    (line.startswith('http://') or 
                     line.startswith('https://') or
                     line.startswith('rtmp://') or
                     line.startswith('rtsp://') or
                     line.startswith('udp://') or
                     line.startswith('rtp://'))):
                    
                    stream_hash = self.get_stream_hash(line)
                    
                    # Пропускаем дубликаты
                    if stream_hash in self.seen_streams:
                        self.stats['streams_duplicate'] += 1
                        current_info = None
                        continue
                    
                    self.seen_streams.add(stream_hash)
                    
                    streams.append({
                        'url': line,
                        'info': current_info if current_info else "#EXTINF:-1,Неизвестный канал",
                        'hash': stream_hash,
                        'source_file': Path(m3u_path).name
                    })
                    current_info = None
                    
        except Exception as e:
            print(f"  ✗ Ошибка при чтении {Path(m3u_path).name}: {e}")
            
        return streams
    
    def test_stream(self, stream_info):
        """
        МАКСИМАЛЬНО СТАБИЛЬНАЯ ПРОВЕРКА ПОТОКА ЧЕРЕЗ FFMPEG
        Точная копия из m3u_combiner_fixed.py
        """
        url = stream_info['url']
        
        # Команда FFmpeg для проверки потока
        ffmpeg_cmd = [
            'ffmpeg',
            '-hide_banner',
            '-loglevel', 'info',
            '-timeout', str(self.timeout * 1000000),  # Микросекунды
            '-i', url,
            '-t', '3',  # Проверяем первые 3 секунды
            '-c', 'copy',  # Копируем без перекодирования
            '-f', 'null',  # Выход в null (не сохраняем)
            '-'
        ]
        
        try:
            # Запускаем процесс FFmpeg
            process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE
            )
            
            try:
                # Ждем завершения с таймаутом
                _, stderr = process.communicate(timeout=self.timeout + 1)
            except subprocess.TimeoutExpired:
                # Убиваем процесс при таймауте
                process.kill()
                process.wait()  # Ждем полного завершения
                return {
                    **stream_info,
                    'status': 'timeout',
                    'error': f'Timeout после {self.timeout} секунд',
                    'tested_at': datetime.now().isoformat()
                }
            
            # Проверяем результат
            err_text = stderr.decode('utf-8', errors='ignore') if stderr else ""
            audio = ''
            for _l in err_text.splitlines():
                if 'Audio:' in _l:
                    audio = _l.split('Audio:', 1)[1].strip().split(',')[0].strip().split(' ')[0].lower()
                    break
            if process.returncode == 0:
                return {
                    **stream_info,
                    'status': 'working',
                    'audio': audio,
                    'error': None,
                    'tested_at': datetime.now().isoformat()
                }
            else:
                # Декодируем ошибку
                _eline = [l for l in err_text.splitlines() if any(w in l.lower() for w in ('error','invalid','failed','unable','refused','not found','timed out','no route','http'))]
                err = _eline[-1] if _eline else (err_text.strip().splitlines()[-1] if err_text.strip() else "Unknown error")
                return {
                    **stream_info,
                    'status': 'failed',
                    'error': err[:120],
                    'tested_at': datetime.now().isoformat()
                }
                
        except Exception as e:
            return {
                **stream_info,
                'status': 'error',
                'error': str(e),
                'tested_at': datetime.now().isoformat()
            }
    
    def test_playlists(self, m3u_files, progress_callback=None, chunk_size=280, save_dir=None):
        """
        Тестирование списка плейлистов кусками (по ~chunk_size каналов, всегда < 300).

        Перед фильтрацией создаётся временная папка с проверенными каналами
        (название: день недели + дата), список нарезается на куски, каждый кусок
        прогоняется по очереди, результат сохраняется в этой папке, а в конце
        всё объединяется в один выходной список.

        save_dir — куда складывать файлы кусков (по умолчанию outputs/Проверенные_...).
        Возвращает (output_lines, stats); self.save_dir — папка проверенных каналов.
        """
        all_streams = []

        # Извлекаем потоки из всех файлов
        for m3u_file in m3u_files:
            if progress_callback:
                progress_callback(f"📂 Обрабатывается: {Path(m3u_file).name}")

            streams = self.extract_streams_from_m3u(m3u_file)
            all_streams.extend(streams)
            self.stats['total_streams_found'] += len(streams)

        if not all_streams:
            return None, {"error": "Нет потоков для тестирования"}

        # Временная папка с проверенными каналами: название = день, дата, время
        if save_dir is None:
            save_dir = Path("outputs") / datetime.now().strftime("Проверенные_%A_%d.%m.%Y_%H-%M-%S")
        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        self.save_dir = save_dir

        # Нарезаем список на куски меньше 300 каналов
        chunk_size = max(1, min(int(chunk_size), 299))
        chunks = [all_streams[i:i + chunk_size] for i in range(0, len(all_streams), chunk_size)]

        if progress_callback:
            progress_callback(
                f"🔍 Найдено {len(all_streams)} уникальных потоков. "
                f"Делю на {len(chunks)} кусок(ов) по ≤{chunk_size} и прогоняю по очереди...\n"
                f"📁 Папка проверенных каналов: {save_dir}")

        tested_count = 0
        total = len(all_streams)

        try:
            for ci, chunk in enumerate(chunks, start=1):
                if progress_callback:
                    progress_callback(f"🧩 Кусок {ci}/{len(chunks)}: каналов {len(chunk)}")

                # Прогоняем кусок целиком перед переходом к следующему
                with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    future_to_stream = {
                        executor.submit(self.test_stream, stream): stream
                        for stream in chunk
                    }

                    for future in as_completed(future_to_stream):
                        tested_count += 1
                        result = future.result()
                        stream = future_to_stream[future]
                        stream['result'] = result

                        self.stats['streams_tested'] += 1

                        # Обрабатываем результат
                        if result['status'] == 'working':
                            self.stats['streams_working'] += 1
                            self.working_streams.append(result)
                            status_icon = "✅"
                        else:
                            self.stats['streams_failed'] += 1
                            status_icon = "❌"

                        # Прогресс каждые 10 потоков
                        if progress_callback and (tested_count % 10 == 0 or tested_count == total):
                            progress = (tested_count / total) * 100
                            progress_callback(
                                f"{status_icon} Протестировано: {tested_count}/{total} ({progress:.1f}%) | "
                                f"Рабочих: {self.stats['streams_working']}"
                            )

                # Результат куска — в папку с проверенными каналами
                chunk_file = save_dir / f"chunk_{ci:03d}.m3u"
                self._write_chunk(chunk_file, chunk)

                if progress_callback:
                    progress_callback(f"💾 Кусок {ci}/{len(chunks)} сохранён: {chunk_file.name}")

        except KeyboardInterrupt:
            if progress_callback:
                progress_callback("⚠️ Прервано пользователем!")
            raise

        # В конце объединяем результаты всех кусков в один файл
        output_lines = ["#EXTM3U\n"]
        output_lines.append(f"# Сгенерировано: {datetime.now().isoformat()}\n")
        output_lines.append(f"# Всего протестировано: {self.stats['streams_tested']}\n")
        output_lines.append(f"# Рабочих потоков: {len(self.working_streams)}\n")
        output_lines.append(f"# Дубликатов удалено: {self.stats['streams_duplicate']}\n")
        output_lines.append(f"# Кусков прогнано: {len(chunks)}, папка проверенных: {save_dir}\n")
        output_lines.append("#" + "="*60 + "\n\n")

        # Сортируем по исходному файлу
        current_source = None
        for stream in sorted(self.working_streams, key=lambda x: x.get('source_file', '')):
            # Добавляем разделитель между источниками
            if stream.get('source_file') != current_source:
                current_source = stream.get('source_file')
                if current_source:
                    output_lines.append(f"\n# ИСТОЧНИК: {current_source}\n")
                    output_lines.append("#" + "-"*50 + "\n")

            output_lines.append(f"{stream['info']}\n")
            output_lines.append(f"{stream['url']}\n")

        return output_lines, self.stats

    @staticmethod
    def _write_chunk(path, chunk):
        """Сохраняет проверенный кусок: все каналы с пометкой OK/FAIL."""
        lines = ["#EXTM3U\n", "# Проверенный кусок (папка с проверенными каналами)\n"]
        for s in chunk:
            res = s.get('result') or {}
            status = res.get('status', 'unknown')
            mark = "OK" if status == 'working' else "FAIL"
            lines.append(f"# [{mark}] {s.get('source_file', '')}\n")
            lines.append(f"{s['info']}\n")
            lines.append(f"{s['url']}\n")
        Path(path).write_text("".join(lines), encoding="utf-8")
