#!/usr/bin/env python3
"""
M3U Converter Module
Конвертирует M3U в PDF/HTML/MD
"""
import os
import re
from pathlib import Path
from collections import defaultdict
from xml.sax.saxutils import escape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors


GENRE_KEYWORDS = {
    "кино": "🎬", "фильм": "🎥", "спорт": "⚽", "новост": "📰", "музык": "🎵",
    "детск": "👶", "радио": "📻", "документ": "📘", "познаватель": "🧠", "религи": "🕊️",
    "погод": "🌤️", "взросл": "🔞", "локальн": "📍", "регион": "🏘️", "украин": "🇺🇦",
    "грузи": "🇬🇪", "аргентин": "🇦🇷", "россия": "🇷🇺", "webcam": "👁️", "кинозал": "📽️",
    "теннис": "🎾", "баскетбол": "🏀", "футбол": "⚽", "спортивн": "🏅", "life": "🌿",
    "prime": "💎", "hd": "📺", "int": "🌍", "vpn": "🔒", "serial": "🎬", "match": "⚔️"
}


class M3UConverter:
    def __init__(self, font_path):
        self.font_path = font_path
        if os.path.isfile(font_path):
            pdfmetrics.registerFont(TTFont('DejaVu', font_path))
    
    def extract_group_and_channel(self, line):
        if not line.startswith('#EXTINF:'):
            return None, None
        group = "Без группы"
        if 'group-title="' in line:
            start = line.find('group-title="') + len('group-title="')
            end = line.find('"', start)
            if end != -1:
                group = line[start:end].strip() or "Без группы"
        parts = line.rsplit(',', 1)
        channel_name = parts[1].strip() if len(parts) == 2 else "(без названия)"
        return group, channel_name
    
    def is_url(self, line):
        line = line.strip().lower()
        return line.startswith(('http://', 'https://', 'rtmp://', 'rtp://', 'udp://', 'mms://'))
    
    def parse_m3u(self, file_path):
        groups = {}
        current_group = "Без группы"
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = [line.rstrip('\n') for line in f]
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith('#EXTINF:'):
                group, channel = self.extract_group_and_channel(line)
                if group:
                    current_group = group
                if channel and not self.is_url(channel):
                    groups.setdefault(current_group, []).append(channel)
            elif i > 0 and lines[i-1].startswith('#EXTINF:') and not self.is_url(line) and line:
                groups.setdefault(current_group, []).append(line)
            i += 1
        return groups
    
    def find_emoji_for_group(self, group_name):
        gl = group_name.lower()
        for kw, emoji in GENRE_KEYWORDS.items():
            if kw in gl:
                return emoji
        return ""
    
    def convert_to_formats(self, m3u_files, output_base_name, progress_callback=None):
        all_data = {}
        for m3u_file in m3u_files:
            if progress_callback:
                progress_callback(f"Парсинг: {Path(m3u_file).name}")
            groups = self.parse_m3u(m3u_file)
            all_data[Path(m3u_file).name] = groups
        
        pdf_content = self.build_pdf_content(all_data)
        html_content = self.build_html_content(all_data)
        md_content = self.build_markdown_content(all_data)
        
        return pdf_content, html_content, md_content
    
    def build_pdf_content(self, all_data):
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontName='DejaVu', fontSize=16, spaceAfter=12, alignment=1)
        group_style = ParagraphStyle('Group', parent=styles['Heading2'], fontName='DejaVu', fontSize=12, spaceAfter=6, textColor=colors.darkblue)
        channel_style = ParagraphStyle('Channel', parent=styles['Normal'], fontName='DejaVu', fontSize=10)
        
        story = []
        for filename, groups in all_data.items():
            story.append(Paragraph(f"📁 {escape(filename)}", title_style))
            story.append(Spacer(1, 12))
            if not groups:
                story.append(Paragraph("Нет каналов", channel_style))
                story.append(Spacer(1, 12))
                continue
            for group_name in sorted(groups.keys()):
                emoji = self.find_emoji_for_group(group_name)
                display = f"{emoji} {group_name}" if emoji else group_name
                story.append(Paragraph(f"🔹 {escape(display)}", group_style))
                for ch in sorted(set(groups[group_name])):
                    story.append(Paragraph(escape(ch), channel_style))
                story.append(Spacer(1, 6))
            story.append(Spacer(1, 24))
        return story
    
    def build_html_content(self, all_data):
        html = '''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Playlist Overview</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; background: #f9f9f9; }
        h1 { text-align: center; color: #2c3e50; }
        .file { margin-bottom: 40px; }
        .group { margin-top: 20px; }
        .group-name { font-size: 1.3em; color: #2980b9; margin-bottom: 8px; }
        .channel { margin-left: 20px; padding: 2px 0; }
    </style>
</head>
<body>
    <h1>📺 Playlist Summary</h1>
'''
        for filename, groups in all_data.items():
            html += f'\n    <div class="file">\n        <h2>📁 {filename}</h2>\n'
            if not groups:
                html += '        <p>Нет каналов</p>\n'
            else:
                for group_name in sorted(groups.keys()):
                    emoji = self.find_emoji_for_group(group_name)
                    display = f"{emoji} {group_name}" if emoji else group_name
                    html += f'        <div class="group">\n            <div class="group-name">🔹 {display}</div>\n'
                    for ch in sorted(set(groups[group_name])):
                        html += f'            <div class="channel">{ch}</div>\n'
                    html += '        </div>\n'
            html += '    </div>\n'
        html += '</body>\n</html>'
        return html
    
    def build_markdown_content(self, all_data):
        md = "# 📺 Playlist Summary\n\n"
        for filename, groups in all_data.items():
            md += f"## 📁 {filename}\n\n"
            if not groups:
                md += "Нет каналов\n\n"
            else:
                for group_name in sorted(groups.keys()):
                    emoji = self.find_emoji_for_group(group_name)
                    display = f"{emoji} {group_name}" if emoji else group_name
                    md += f"### 🔹 {display}\n\n"
                    for ch in sorted(set(groups[group_name])):
                        md += f"- {ch}\n"
                    md += "\n"
        return md
