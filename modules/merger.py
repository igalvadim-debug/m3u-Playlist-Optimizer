#!/usr/bin/env python3
"""
M3U Merger Module
Умное объединение M3U файлов по группам с чекбоксами
"""
import re
from pathlib import Path
from collections import defaultdict


class M3UMerger:
    def __init__(self):
        pass
    
    def parse_md_groups(self, md_content):
        """Парсит группы из Markdown контента"""
        group_to_channels = defaultdict(set)
        current_group = None
        for line in md_content.split('\n'):
            line = line.strip()
            if line.startswith('### 🔹'):
                group_raw = line[6:].strip()
                group_clean = re.sub(r'^[\U0001F300-\U0001F9FF]+', '', group_raw).strip()
                current_group = group_clean
            elif line.startswith('- ') and current_group:
                channel_name = line[2:].strip()
                if channel_name:
                    group_to_channels[current_group].add(channel_name)
        return dict(group_to_channels)
    
    def is_radio(self, channel_name, group_name=""):
        radio_keywords = {'radio', 'радио', 'fm', 'am', 'smooth', 'jazz', 'music', 'музыка', '📻'}
        combined = (channel_name + " " + group_name).lower()
        return any(kw in combined for kw in radio_keywords)
    
    def parse_m3u_files(self, m3u_files, md_groups, progress_callback=None):
        """Парсит M3U файлы и группирует по MD"""
        url_to_entry = {}
        channel_to_group = {}
        for group, channels in md_groups.items():
            for ch in channels:
                channel_to_group[ch] = group
        
        for m3u_file in m3u_files:
            if progress_callback:
                progress_callback(f"Парсинг: {Path(m3u_file).name}")
            
            with open(m3u_file, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
            
            i = 0
            while i < len(lines):
                line = lines[i].strip()
                if line.startswith('#EXTINF:'):
                    parts = line.rsplit(',', 1)
                    channel_name = parts[1].strip() if len(parts) == 2 else "(без имени)"
                    if i + 1 < len(lines):
                        url = lines[i + 1].strip()
                        if url and not url.startswith('#') and url.startswith(('http://', 'https://')):
                            if self.is_radio(channel_name):
                                i += 2
                                continue
                            final_group = channel_to_group.get(channel_name, "Без группы")
                            if url not in url_to_entry:
                                url_to_entry[url] = (channel_name, final_group)
                    i += 2
                else:
                    i += 1
        
        return url_to_entry
    
    def rebuild_grouped_data(self, url_to_entry):
        """Восстанавливает группировку"""
        grouped = defaultdict(list)
        for url, (name, group) in url_to_entry.items():
            grouped[group].append((name, url))
        return grouped
    
    def get_group_list(self, grouped):
        """Возвращает список групп для чекбоксов"""
        return sorted(grouped.keys())
    
    def merge_groups(self, grouped, target_group, source_groups):
        """Объединяет группы"""
        for src_group in source_groups:
            if src_group != target_group and src_group in grouped:
                grouped[target_group].extend(grouped[src_group])
                del grouped[src_group]
        return grouped
    
    def delete_groups(self, grouped, groups_to_delete):
        """Удаляет группы"""
        for group in groups_to_delete:
            if group in grouped:
                del grouped[group]
        return grouped
    
    def write_m3u(self, grouped_data):
        """Записывает M3U контент"""
        lines = ["#EXTM3U\n"]
        for group in sorted(grouped_data.keys()):
            for channel_name, url in grouped_data[group]:
                lines.append(f'#EXTINF:-1 group-title="{group}",{channel_name}\n')
                lines.append(f'{url}\n')
        return lines
