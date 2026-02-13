#!/usr/bin/env python3
"""
m3uGenius - Gradio Interface optimized for Hugging Face Spaces
Графическая оболочка для работы с M3U плейлистами
OPTIMIZED FOR GRADIO 4.44.1
"""
import gradio as gr
import os
from pathlib import Path
from datetime import datetime
from reportlab.platypus import SimpleDocTemplate
from modules.cleaner import M3UCleaner
from modules.tester import M3UTester
from modules.converter import M3UConverter
from modules.merger import M3UMerger


OUTPUT_DIR = Path("outputs")
FONT_PATH = Path("ttf/DejaVuSans.ttf")

# Für Hugging Face Spaces verwenden wir korrekten Adressbindung
HF_SPACE_URL = os.getenv("SPACE_ID")  # Will be set by Hugging Face
LOCALHOST = "127.0.0.1"


def create_output_folder():
    """Создает папку с текущей датой и временем"""
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    folder = OUTPUT_DIR / timestamp
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def cleaner_function(files, blocklist_text):
    """Очистка и объединение M3U файлов"""
    if not files:
        return None, "Ошибка: не выбраны файлы"
    
    output_folder = create_output_folder()
    cleaner = M3UCleaner()
    
    progress_log = []
    def log_progress(msg):
        progress_log.append(msg)
    
    # Gradio 4.44.1: files is already a list of file paths (strings)
    file_paths = files
    
    result, stats = cleaner.clean_m3u(file_paths, blocklist_text, log_progress)
    
    if result is None:
        return None, f"Ошибка: {stats.get('error', 'Неизвестная ошибка')}"
    
    output_file = output_folder / "cleaned.m3u"
    with open(output_file, 'w', encoding='utf-8') as f:
        f.writelines(result)
    
    stats_text = f"""✅ Обработка завершена!

📊 Статистика:
- Всего потоков: {stats['total']}
- Заблокировано: {stats['blocked']} ({stats['blocked']/max(1,stats['total'])*100:.1f}%)
- Дубликатов удалено: {stats['duplicates']} ({stats['duplicates']/max(1,stats['total'])*100:.1f}%)
- Сохранено: {stats['kept']} ({stats['kept']/max(1,stats['total'])*100:.1f}%)

💾 Сохранено: {output_file}
"""
    
    return str(output_file), stats_text


def tester_function(files, timeout, workers):
    """Тестирование потоков"""
    if not files:
        return None, "Ошибка: не выбраны файлы"
    
    output_folder = create_output_folder()
    tester = M3UTester(timeout=timeout, max_workers=workers)
    
    progress_log = []
    def log_progress(msg):
        progress_log.append(msg)
    
    # Gradio 4.44.1: files is already a list of file paths (strings)
    file_paths = files
    
    result, stats = tester.test_playlists(file_paths, log_progress)
    
    if result is None:
        return None, f"Ошибка: {stats.get('error', 'Неизвестная ошибка')}"
    
    output_file = output_folder / "tested_working.m3u"
    with open(output_file, 'w', encoding='utf-8') as f:
        f.writelines(result)
    
    stats_text = f"""✅ Тестирование завершено!

📊 Статистика:
- Найдено потоков: {stats['total_streams_found']}
- Протестировано: {stats['streams_tested']}
- Рабочих: {stats['streams_working']} ({stats['streams_working']/max(1,stats['streams_tested'])*100:.1f}%)
- Нерабочих: {stats['streams_failed']} ({stats['streams_failed']/max(1,stats['streams_tested'])*100:.1f}%)
- Дубликатов удалено: {stats['streams_duplicate']}

💾 Сохранено: {output_file}
"""
    
    return str(output_file), stats_text


def converter_function(files):
    """Конвертация M3U в PDF/HTML/MD"""
    if not files:
        return None, None, None, "Ошибка: не выбраны файлы"
    
    output_folder = create_output_folder()
    converter = M3UConverter(str(FONT_PATH))
    
    progress_log = []
    def log_progress(msg):
        progress_log.append(msg)
    
    # Gradio 4.44.1: files is already a list of file paths (strings)
    file_paths = files
    
    pdf_story, html_content, md_content = converter.convert_to_formats(file_paths, "playlist", log_progress)
    
    pdf_file = output_folder / "playlist.pdf"
    html_file = output_folder / "playlist.html"
    md_file = output_folder / "playlist.md"
    
    doc = SimpleDocTemplate(str(pdf_file), pagesize=(612, 792))
    doc.build(pdf_story)
    
    with open(html_file, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    with open(md_file, 'w', encoding='utf-8') as f:
        f.write(md_content)
    
    stats_text = f"""✅ Конвертация завершена!

💾 Сохранено:
- PDF: {pdf_file}
- HTML: {html_file}
- MD: {md_file}
"""
    
    return str(pdf_file), str(html_file), str(md_file), stats_text



def merger_load_groups(m3u_files, md_file):
    """Загрузка групп из MD для отображения чекбоксов"""
    if not m3u_files or not md_file:
        return gr.update(choices=[], value=[]), "Загрузите M3U файлы и MD файл"
    
    try:
        # Gradio 4.44.1: md_file is already a string path
        with open(md_file, 'r', encoding='utf-8') as f:
            md_content = f.read()
        
        merger = M3UMerger()
        md_groups = merger.parse_md_groups(md_content)
        
        # Gradio 4.44.1: m3u_files is already a list of file paths (strings)
        file_paths = m3u_files
        
        url_to_entry = merger.parse_m3u_files(file_paths, md_groups)
        grouped = merger.rebuild_grouped_data(url_to_entry)
        
        group_list = merger.get_group_list(grouped)
        
        group_display = []
        for group in group_list:
            count = len(grouped[group])
            group_display.append(f"{group} ({count} каналов)")
        
        return gr.update(choices=group_display, value=[]), f"✅ Загружено {len(group_list)} групп"
    
    except Exception as e:
        return gr.update(choices=[], value=[]), f"❌ Ошибка: {str(e)}"


def merger_delete_groups(m3u_files, md_file, selected_groups):
    """Удаление выбранных групп"""
    if not selected_groups:
        return None, "Не выбраны группы для удаления"
    
    try:
        # Gradio 4.44.1: md_file is already a string path
        with open(md_file, 'r', encoding='utf-8') as f:
            md_content = f.read()
        
        merger = M3UMerger()
        md_groups = merger.parse_md_groups(md_content)
        
        # Gradio 4.44.1: m3u_files is already a list of file paths (strings)
        file_paths = m3u_files
        
        url_to_entry = merger.parse_m3u_files(file_paths, md_groups)
        grouped = merger.rebuild_grouped_data(url_to_entry)
        
        groups_to_delete = [g.split(' (')[0] for g in selected_groups]
        grouped = merger.delete_groups(grouped, groups_to_delete)
        
        output_folder = create_output_folder()
        output_file = output_folder / "merged_deleted.m3u"
        
        result = merger.write_m3u(grouped)
        with open(output_file, 'w', encoding='utf-8') as f:
            f.writelines(result)
        
        total_channels = sum(len(channels) for channels in grouped.values())
        stats_text = f"""✅ Удаление завершено!

📊 Результат:
- Групп осталось: {len(grouped)}
- Каналов: {total_channels}
- Удалено групп: {len(groups_to_delete)}

💾 Сохранено: {output_file}
"""
        
        return str(output_file), stats_text
    
    except Exception as e:
        return None, f"❌ Ошибка: {str(e)}"



def merger_merge_groups(m3u_files, md_file, target_group, source_groups):
    """Объединение групп"""
    if not target_group or not source_groups:
        return None, "Выберите целевую группу и группы для объединения"
    
    try:
        # Gradio 4.44.1: md_file is already a string path
        with open(md_file, 'r', encoding='utf-8') as f:
            md_content = f.read()
        
        merger = M3UMerger()
        md_groups = merger.parse_md_groups(md_content)
        
        # Gradio 4.44.1: m3u_files is already a list of file paths (strings)
        file_paths = m3u_files
        
        url_to_entry = merger.parse_m3u_files(file_paths, md_groups)
        grouped = merger.rebuild_grouped_data(url_to_entry)
        
        target = target_group.split(' (')[0]
        sources = [g.split(' (')[0] for g in source_groups if g != target_group]
        
        grouped = merger.merge_groups(grouped, target, sources)
        
        output_folder = create_output_folder()
        output_file = output_folder / "merged_combined.m3u"
        
        result = merger.write_m3u(grouped)
        with open(output_file, 'w', encoding='utf-8') as f:
            f.writelines(result)
        
        total_channels = sum(len(channels) for channels in grouped.values())
        stats_text = f"""✅ Объединение завершено!

📊 Результат:
- Групп: {len(grouped)}
- Каналов: {total_channels}
- Объединено в: {target}

💾 Сохранено: {output_file}
"""
        
        return str(output_file), stats_text
    
    except Exception as e:
        return None, f"❌ Ошибка: {str(e)}"


# Создание Gradio интерфейса
with gr.Blocks(title="m3uGenius", theme=gr.themes.Soft()) as app:
    gr.Markdown("# 🎯 m3uGenius")
    gr.Markdown("Универсальная обработка M3U плейлистов")
    
    with gr.Tabs():
        # TAB 1: Cleaner
        with gr.Tab("🧹 Cleaner"):
            gr.Markdown("### Очистка и объединение M3U файлов")
            with gr.Row():
                with gr.Column():
                    cleaner_files = gr.File(label="M3U файлы", file_count="multiple", file_types=[".m3u", ".m3u8"])
                    cleaner_blocklist = gr.Textbox(label="Блоклист (один домен/URL на строку)", lines=5, placeholder="example.com\nbad-domain.net")
                    cleaner_btn = gr.Button("🚀 Запустить очистку", variant="primary")
                with gr.Column():
                    cleaner_output = gr.File(label="Результат")
                    cleaner_stats = gr.Textbox(label="Статистика", lines=10)
            
            cleaner_btn.click(
                cleaner_function,
                inputs=[cleaner_files, cleaner_blocklist],
                outputs=[cleaner_output, cleaner_stats],
                api_name="cleaner"
            )
        
        # TAB 2: Tester
        with gr.Tab("🔍 Tester"):
            gr.Markdown("### Тестирование потоков через FFmpeg")
            with gr.Row():
                with gr.Column():
                    tester_files = gr.File(label="M3U файлы", file_count="multiple", file_types=[".m3u", ".m3u8"])
                    tester_timeout = gr.Slider(minimum=3, maximum=20, value=8, step=1, label="Timeout (секунды)")
                    tester_workers = gr.Slider(minimum=5, maximum=50, value=15, step=5, label="Параллельных потоков")
                    tester_btn = gr.Button("🚀 Запустить тестирование", variant="primary")
                with gr.Column():
                    tester_output = gr.File(label="Результат")
                    tester_stats = gr.Textbox(label="Статистика", lines=10)
            
            tester_btn.click(
                tester_function,
                inputs=[tester_files, tester_timeout, tester_workers],
                outputs=[tester_output, tester_stats],
                api_name="tester"
            )
        
        # TAB 3: Converter
        with gr.Tab("📄 Converter"):
            gr.Markdown("### Конвертация M3U в PDF/HTML/MD")
            with gr.Row():
                with gr.Column():
                    converter_files = gr.File(label="M3U файлы", file_count="multiple", file_types=[".m3u", ".m3u8"])
                    converter_btn = gr.Button("🚀 Конвертировать", variant="primary")
                with gr.Column():
                    converter_pdf = gr.File(label="PDF")
                    converter_html = gr.File(label="HTML")
                    converter_md = gr.File(label="Markdown")
                    converter_stats = gr.Textbox(label="Статистика", lines=5)
            
            converter_btn.click(
                converter_function,
                inputs=[converter_files],
                outputs=[converter_pdf, converter_html, converter_md, converter_stats],
                api_name="converter"
            )
        
        # TAB 4: Merger
        with gr.Tab("🔀 Merger"):
            gr.Markdown("### Умное объединение по группам")
            with gr.Row():
                with gr.Column():
                    merger_m3u_files = gr.File(label="M3U файлы", file_count="multiple", file_types=[".m3u", ".m3u8"])
                    merger_md_file = gr.File(label="MD файл с группами", file_count="single", file_types=[".md"])
                    merger_load_btn = gr.Button("📥 Загрузить группы")
                    merger_groups = gr.CheckboxGroup(label="Группы", choices=[], interactive=True)
                    merger_load_status = gr.Textbox(label="Статус загрузки", lines=2)
                
                with gr.Column():
                    gr.Markdown("#### Удалить выбранные группы")
                    merger_delete_btn = gr.Button("🗑️ Удалить группы", variant="stop")
                    
                    gr.Markdown("#### Объединить группы")
                    merger_target = gr.Dropdown(label="Целевая группа (куда)", choices=[], interactive=True)
                    merger_sources = gr.CheckboxGroup(label="Исходные группы (откуда)", choices=[], interactive=True)
                    merger_merge_btn = gr.Button("🔗 Объединить", variant="primary")
                    
                    merger_output = gr.File(label="Результат")
                    merger_stats = gr.Textbox(label="Статистика", lines=8)
            
            def update_dropdowns(m3u_files, md_file):
                checkboxes, status = merger_load_groups(m3u_files, md_file)
                choices = checkboxes.get('choices', [])
                return (
                    checkboxes,
                    gr.update(choices=choices),
                    gr.update(choices=choices),
                    status
                )
            
            merger_load_btn.click(
                update_dropdowns,
                inputs=[merger_m3u_files, merger_md_file],
                outputs=[merger_groups, merger_target, merger_sources, merger_load_status],
                api_name="merger_load"
            )
            
            merger_delete_btn.click(
                merger_delete_groups,
                inputs=[merger_m3u_files, merger_md_file, merger_groups],
                outputs=[merger_output, merger_stats],
                api_name="merger_delete"
            )
            
            merger_merge_btn.click(
                merger_merge_groups,
                inputs=[merger_m3u_files, merger_md_file, merger_target, merger_sources],
                outputs=[merger_output, merger_stats],
                api_name="merger_merge"
            )


if __name__ == "__main__":
    OUTPUT_DIR.mkdir(exist_ok=True)
    print("🚀 Запуск m3uGenius...")
    
    # Simplified launch for Hugging Face Spaces - let Gradio handle port automatically
    app.launch(
        server_name="0.0.0.0",
        allowed_paths=["."]  # Important for file access in Gradio 4+
    )
