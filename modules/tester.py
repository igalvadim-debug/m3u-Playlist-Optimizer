#!/usr/bin/env python3
"""
M3U Tester Module
Тестирует потоки через FFmpeg
"""
import subprocess
from pathlib import Path
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import hashlib


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
    
    def get_stream_hash(self, url):
        parsed = urlparse(url)
        clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        return hashlib.md5(clean_url.encode('utf-8')).hexdigest()
    
    def extract_streams_from_m3u(self, m3u_path):
        streams = []
        try:
            with open(m3u_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
            
            current_info = None
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                if line.startswith('#EXTINF:'):
                    current_info = line
                    continue
                if line and not line.startswith('#') and line.startswith(('http://', 'https://', 'rtmp://', 'rtsp://', 'udp://', 'rtp://')):
                    stream_hash = self.get_stream_hash(line)
                    if stream_hash in self.seen_streams:
                        self.stats['streams_duplicate'] += 1
                        current_info = None
                        continue
                    self.seen_streams.add(stream_hash)
                    streams.append({
                        'url': line,
                        'info': current_info if current_info else "#EXTINF:-1,Неизвестный канал",
                        'hash': stream_hash
                    })
                    current_info = None
        except Exception as e:
            pass
        return streams
    
    def test_stream(self, stream_info):
        url = stream_info['url']
        ffmpeg_cmd = [
            'ffmpeg', '-hide_banner', '-loglevel', 'error',
            '-timeout', str(self.timeout * 1000000),
            '-i', url, '-t', '3', '-c', 'copy', '-f', 'null', '-'
        ]
        try:
            process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                _, stderr = process.communicate(timeout=self.timeout + 1)
            except subprocess.TimeoutExpired:
                process.kill()
                return {**stream_info, 'status': 'timeout', 'error': f'Timeout после {self.timeout} секунд'}
            if process.returncode == 0:
                return {**stream_info, 'status': 'working', 'error': None}
            else:
                err = stderr.decode('utf-8', errors='ignore') if stderr else ""
                return {**stream_info, 'status': 'failed', 'error': err[:80] if err else "Unknown error"}
        except Exception as e:
            return {**stream_info, 'status': 'error', 'error': str(e)}
    
    def test_playlists(self, m3u_files, progress_callback=None):
        all_streams = []
        for m3u_file in m3u_files:
            if progress_callback:
                progress_callback(f"Извлечение: {Path(m3u_file).name}")
            streams = self.extract_streams_from_m3u(m3u_file)
            all_streams.extend(streams)
            self.stats['total_streams_found'] += len(streams)
        
        if not all_streams:
            return None, {"error": "Нет потоков для тестирования"}
        
        if progress_callback:
            progress_callback(f"Найдено {len(all_streams)} уникальных потоков. Начинаем тестирование...")
        
        tested_count = 0
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_stream = {executor.submit(self.test_stream, stream): stream for stream in all_streams}
            for future in as_completed(future_to_stream):
                tested_count += 1
                result = future.result()
                self.stats['streams_tested'] += 1
                if result['status'] == 'working':
                    self.stats['streams_working'] += 1
                    self.working_streams.append(result)
                else:
                    self.stats['streams_failed'] += 1
                if progress_callback and tested_count % 10 == 0:
                    progress = (tested_count / len(all_streams)) * 100
                    progress_callback(f"Тестировано: {tested_count}/{len(all_streams)} ({progress:.1f}%)")
        
        output_lines = ["#EXTM3U\n"]
        output_lines.append(f"# Сгенерировано: {datetime.now().isoformat()}\n")
        output_lines.append(f"# Рабочих потоков: {len(self.working_streams)}\n\n")
        
        for stream in sorted(self.working_streams, key=lambda x: x['info']):
            output_lines.append(f"{stream['info']}\n")
            output_lines.append(f"{stream['url']}\n")
        
        return output_lines, self.stats
