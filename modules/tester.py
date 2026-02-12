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
            '-loglevel', 'error',
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
            if process.returncode == 0:
                return {
                    **stream_info,
                    'status': 'working',
                    'error': None,
                    'tested_at': datetime.now().isoformat()
                }
            else:
                # Декодируем ошибку
                err = stderr.decode('utf-8', errors='ignore') if stderr else ""
                return {
                    **stream_info,
                    'status': 'failed',
                    'error': err[:100] if err else "Unknown error",
                    'tested_at': datetime.now().isoformat()
                }
                
        except Exception as e:
            return {
                **stream_info,
                'status': 'error',
                'error': str(e),
                'tested_at': datetime.now().isoformat()
            }
    
    def test_playlists(self, m3u_files, progress_callback=None):
        """
        Тестирование списка плейлистов
        С максимальной стабильностью и поддержкой отмены
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
        
        if progress_callback:
            progress_callback(f"🔍 Найдено {len(all_streams)} уникальных потоков. Начинаем тестирование...")
        
        tested_count = 0
        
        try:
            # Параллельное тестирование потоков
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                future_to_stream = {
                    executor.submit(self.test_stream, stream): stream 
                    for stream in all_streams
                }
                
                for future in as_completed(future_to_stream):
                    tested_count += 1
                    result = future.result()
                    
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
                    if progress_callback and (tested_count % 10 == 0 or tested_count == len(all_streams)):
                        progress = (tested_count / len(all_streams)) * 100
                        progress_callback(
                            f"{status_icon} Протестировано: {tested_count}/{len(all_streams)} ({progress:.1f}%) | "
                            f"Рабочих: {self.stats['streams_working']}"
                        )
        
        except KeyboardInterrupt:
            if progress_callback:
                progress_callback("⚠️ Прервано пользователем!")
            raise
        
        # Формируем выходной M3U файл
        output_lines = ["#EXTM3U\n"]
        output_lines.append(f"# Сгенерировано: {datetime.now().isoformat()}\n")
        output_lines.append(f"# Всего протестировано: {self.stats['streams_tested']}\n")
        output_lines.append(f"# Рабочих потоков: {len(self.working_streams)}\n")
        output_lines.append(f"# Дубликатов удалено: {self.stats['streams_duplicate']}\n")
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
