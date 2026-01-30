#!/usr/bin/env python3
"""
M3U Cleaner Module
Блокирует домены, объединяет M3U файлы, удаляет дубликаты
"""
import os
import hashlib
from urllib.parse import urlparse
from pathlib import Path


class M3UCleaner:
    def __init__(self):
        self.blocked_domains = set()
        self.blocked_patterns = set()
    
    def normalize_domain(self, url):
        """Извлекает домен из URL"""
        try:
            if not url.startswith(('http://', 'https://', 'udp://')):
                url = 'http://' + url
            parsed = urlparse(url)
            domain = parsed.netloc or parsed.path.split('/')[0]
            if ':' in domain:
                domain = domain.split(':')[0]
            return domain.lower()
        except:
            return url.lower()
    
    def add_block_entry(self, entry):
        """Добавляет URL/домен в блоклист"""
        self.blocked_patterns.add(entry.lower())
        domain = self.normalize_domain(entry)
        if domain:
            self.blocked_domains.add(domain)
    
    def load_blocklist_from_text(self, text):
        """Загружает блоклист из текста"""
        count = 0
        for line in text.strip().split('\n'):
            entry = line.strip()
            if entry and not entry.startswith('#'):
                self.add_block_entry(entry)
                count += 1
        return count
    
    def is_blocked(self, url):
        """Проверяет, заблокирован ли URL"""
        url_lower = url.lower()
        for pattern in self.blocked_patterns:
            if pattern in url_lower:
                return True
        domain = self.normalize_domain(url)
        if domain in self.blocked_domains:
            return True
        return False
    
    def clean_m3u(self, input_files, blocklist_text="", progress_callback=None):
        """Очищает и объединяет M3U файлы"""
        if blocklist_text:
            self.load_blocklist_from_text(blocklist_text)
        
        all_lines = []
        
        for input_file in input_files:
            try:
                with open(input_file, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                    all_lines.extend(lines)
                    if progress_callback:
                        progress_callback(f"Прочитано: {input_file}")
            except Exception as e:
                if progress_callback:
                    progress_callback(f"Ошибка чтения {input_file}: {e}")
        
        if not all_lines:
            return None, {"error": "Нет данных для обработки"}
        
        filtered = []
        current_extinf = None
        seen_blocks = set()
        
        stats = {
            'total': 0,
            'blocked': 0,
            'kept': 0,
            'duplicates': 0
        }
        
        header_written = False
        
        for line in all_lines:
            line_stripped = line.strip()
            
            if line_stripped.startswith('#EXTM3U'):
                if not header_written:
                    filtered.append(line)
                    header_written = True
                continue
            
            if line_stripped.startswith('#') and not line_stripped.startswith('#EXTINF'):
                filtered.append(line)
                continue
            
            if line_stripped.startswith('#EXTINF'):
                current_extinf = line
                continue
            
            if not line_stripped:
                filtered.append(line)
                continue
            
            if line_stripped.startswith(('http', 'udp', 'rtmp', 'rtsp')):
                stats['total'] += 1
                
                block_id = (current_extinf.strip() if current_extinf else "") + "|" + line_stripped
                
                if block_id in seen_blocks:
                    stats['duplicates'] += 1
                    current_extinf = None
                    continue
                
                if self.is_blocked(line_stripped):
                    stats['blocked'] += 1
                    current_extinf = None
                else:
                    stats['kept'] += 1
                    seen_blocks.add(block_id)
                    if current_extinf:
                        filtered.append(current_extinf)
                    filtered.append(line)
                    current_extinf = None
        
        return filtered, stats
