#!/usr/bin/env python3
"""
m3uGenius - Gradio Interface
Графическая оболочка для работы с M3U плейлистами
"""
import gzip
import gradio as gr
import json
import os
import queue
import re
import threading
from pathlib import Path
from datetime import datetime
from reportlab.platypus import SimpleDocTemplate
from modules.cleaner import M3UCleaner
from modules.tester import M3UTester
from modules.converter import M3UConverter
from modules.merger import M3UMerger
from modules.llm_filter import LLMFilter, LLM_PORT
from modules.header import (DEFAULT_EPG, apply_header, EPG_CHOICES, EPG_SOURCES,
                           EPG_TITLES, epg_from_choice,
                           CATCHUP_LABELS, CATCHUP_DEFAULT, archive_params, catchup_mode_of)
from modules.m3u_utils import (parse_m3u, url_key, set_group, split_extinf, canonical_group,
                               merge_group_synonyms)
from modules.organizer import (Organizer, load_exceptions, load_priority, set_adult_hosts_file,
                               is_adult_host, load_databank, url_looks_russian, _norm as org_norm)
from modules import logos


OUTPUT_DIR = Path(os.environ.get("M3UGENIUS_OUTPUT_DIR", "outputs"))
# except.md — входной файл-конфиг: ищем рядом с проектом, даже если программу запустили из другой папки
EXCEPT_FILE = (OUTPUT_DIR if OUTPUT_DIR.is_absolute() else Path(__file__).resolve().parent / OUTPUT_DIR) / "except.md"
# Метод отделения 18+: подпись radio -> значение
PORN_METHODS = {"Только по названию": "name", "По названию и по кадру (VL)": "frame"}
FONT_PATH = Path("ttf/DejaVuSans.ttf")
# priority.md — порядок каналов внутри группы; sort_cache.json — кэш решений сортировки (удалите файл, чтобы сбросить)
_OUT_ABS = OUTPUT_DIR if OUTPUT_DIR.is_absolute() else Path(__file__).resolve().parent / OUTPUT_DIR
PRIORITY_FILE = _OUT_ABS / "priority.md"
SORT_CACHE_FILE = _OUT_ABS / "sort_cache.json"
ADULT_HOSTS_FILE = _OUT_ABS / "adult_hosts.md"    # хосты, потоки с которых сразу уходят в 18+
DATABANK_FILE = _OUT_ABS / "databank.json"       # датабанк каналов (язык/тема)
DEFAULT_ADULT_HOSTS = ("# Хосты, потоки с которых сразу отправляются в 18+ (без проверки названия и кадра).\n"
                       "# По одному на строку или через запятую; можно с http://, берётся только хост. Поддомены тоже считаются.\n"
                       "video2.tizam.cc\n")
DEFAULT_PRIORITY = ("# Порядок каналов внутри группы: эти каналы стоят первыми, в заданном порядке.\n"
                    "# Названия через запятую или по одному на строку. Строки с # — комментарии. HD/SD в названии не важны.\n"
                    "Первый канал, Россия 1, Матч ТВ, НТВ, Пятница, Звезда, ТВ Центр, РЕН ТВ, СТС, Домашний, "
                    "ТВ-3, ТНТ, Мир, Россия К, Россия 24, Карусель, ОТР, Спас, Че, Ю, Суббота, Перец\n")
SERVER_PORT = int(os.environ.get("M3UGENIUS_PORT", "7860"))

# Подсказки при наведении мыши (title-атрибут) для выпадающих списков EPG.
# Текст пояснений показывается ТОЛЬКО при наведении, без видимых подписей.
EPG_DROPDOWN_TIP = ("Выберите готовый EPG-источник из списка или вставьте свою ссылку.\n\n"
                    + EPG_TITLES)
EPG_MULTI_TIP = ("Можно выбрать несколько источников (или вписать свою ссылку).\n\n"
                 + EPG_TITLES)


def create_output_folder():
    """Создает папку с текущей датой и временем"""
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    folder = OUTPUT_DIR / timestamp
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def create_verified_folder():
    """Временная папка с проверенными каналами: название = день недели + дата (+время)"""
    folder = OUTPUT_DIR / datetime.now().strftime("Проверенные_%A_%d.%m.%Y_%H-%M-%S")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _paths(files):
    """Пути загруженных файлов (работает и со строками, и с объектами с .name)"""
    return [getattr(f, "name", f) for f in files]


_exceptions_cache = {"mtime": None, "obj": None}


def _load_exceptions():
    """Слова из outputs/except.md: канал с таким словом в названии всегда русскоязычный.
    Файл перечитывается только при изменении (по времени правки)."""
    try:
        mtime = EXCEPT_FILE.stat().st_mtime
    except OSError:
        return load_exceptions(EXCEPT_FILE)
    if _exceptions_cache["mtime"] != mtime:
        _exceptions_cache["obj"] = load_exceptions(EXCEPT_FILE)
        _exceptions_cache["mtime"] = mtime
    return _exceptions_cache["obj"]


def _init_adult_hosts():
    """Создаёт adult_hosts.md (с video2.tizam.cc) при первом запуске и подключает его к сортировке."""
    try:
        if not ADULT_HOSTS_FILE.exists():
            ADULT_HOSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            ADULT_HOSTS_FILE.write_text(DEFAULT_ADULT_HOSTS, encoding="utf-8")
    except OSError:
        pass
    set_adult_hosts_file(ADULT_HOSTS_FILE)


_init_adult_hosts()
_databank_cache = {"mtime": None, "obj": {}}


def _load_databank():
    """Датабанк каналов из outputs/databank.json (перечитывается только при изменении)."""
    try:
        mtime = DATABANK_FILE.stat().st_mtime
    except OSError:
        return {}
    if _databank_cache["mtime"] != mtime:
        _databank_cache["obj"] = load_databank(DATABANK_FILE)
        _databank_cache["mtime"] = mtime
    return _databank_cache["obj"]


_priority_cache = {"mtime": None, "obj": None}


def _load_priority():
    """Приоритет каналов из outputs/priority.md (создаётся с примером при первом запуске).
    Файл перечитывается только при изменении."""
    try:
        if not PRIORITY_FILE.exists():
            PRIORITY_FILE.parent.mkdir(parents=True, exist_ok=True)
            PRIORITY_FILE.write_text(DEFAULT_PRIORITY, encoding="utf-8")
        mtime = PRIORITY_FILE.stat().st_mtime
    except OSError:
        return load_priority(PRIORITY_FILE)
    if _priority_cache["mtime"] != mtime:
        _priority_cache["obj"] = load_priority(PRIORITY_FILE)
        _priority_cache["mtime"] = mtime
    return _priority_cache["obj"]


_logo_cache = {"obj": None}


def _logo_matcher():
    """LogoMatcher строится один раз: индексация ~10k иконок занимает время."""
    if _logo_cache["obj"] is None:
        _logo_cache["obj"] = logos.LogoMatcher()
    return _logo_cache["obj"]


def _toggle_catchup_src(choice):
    """Поле «Шаблон архива» нужно только для append/default — иначе скрываем."""
    return gr.update(visible=catchup_mode_of(choice) in ("append", "default"))


def _resolve_epg(dropdown, custom):
    """Итоговая EPG-ссылка: выбранный источник(ы) из списка + своё поле."""
    parts = []
    dd = dropdown if isinstance(dropdown, list) else ([dropdown] if dropdown else [])
    for d in dd:
        s = str(d).strip()
        if s:
            parts.append(s)
    if isinstance(custom, bool) or custom is None:
        c = ""  # защита от случайной передачи флага вместо строки
    else:
        c = str(custom).strip()
    if c:
        parts.append(c)
    return epg_from_choice(parts) if parts else ""


def _epg_dropdown(label="EPG ссылка (url-tvg)", multi=False, value=None):
    """Выпадающий список готовых EPG-источников; свой URL можно ввести прямо здесь.
    Подсказка показывается только при наведении мыши (tooltip), без видимого текста."""
    return gr.Dropdown(label=label, choices=EPG_CHOICES,
                       value=value if not multi else (value or []),
                       multiselect=multi, allow_custom_value=True, interactive=True,
                       elem_classes=["epg-tip"], visible=True)


# Подсказки для пунктов раскрытого списка: "название — пояснение со ссылкой"
EPG_ITEMS_TIP = "\n".join(f"{name} \u2014 {tip}" for name, _, tip in EPG_SOURCES)
# Тот же текст в виде JS-объекта {название: пояснение} — поиск по aria-label пункта
EPG_ITEMS_MAP = {name: tip for name, _, tip in EPG_SOURCES}

# CSS+JS для тултипов: текст-пояснение всплывает ТОЛЬКО при наведении мыши,
# никаких видимых подписей под списком. Работает и для пунктов выпадающего списка.
EPG_HEAD_HTML = """
<style>
/* стилизованный тултип; виден только при наведении мыши */
.epg-tooltip {
    position: fixed;
    z-index: 99999;
    max-width: 560px;
    background: #1f2937;
    color: #f9fafb;
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 13px;
    line-height: 1.5;
    white-space: pre-line;
    box-shadow: 0 4px 16px rgba(0,0,0,.35);
    pointer-events: none;
    display: none;
}
.epg-tip input, .epg-tip button { cursor: help; }
li[data-testid="dropdown-option"] { cursor: help; }
</style>
<script>
(function () {
    const ITEMS = __ITEMS__;
    const TIPS = {
        single: __SINGLE__,
        multi: __MULTI__
    };
    let tipEl = null;
    function ensureTip() {
        if (!tipEl || !document.body.contains(tipEl)) {
            tipEl = document.createElement('div');
            tipEl.className = 'epg-tooltip';
            document.body.appendChild(tipEl);
        }
        return tipEl;
    }
    function showTip(text, x, y) {
        if (!text) return hideTip();
        const t = ensureTip();
        t.textContent = text;
        t.style.display = 'block';
        const w = t.offsetWidth, h = t.offsetHeight;
        const left = Math.min(Math.max(8, x + 14), window.innerWidth - w - 8);
        let top = y + 18;
        if (top + h > window.innerHeight - 8) top = Math.max(8, y - h - 12);
        t.style.left = left + 'px';
        t.style.top = top + 'px';
    }
    function hideTip() { if (tipEl) tipEl.style.display = 'none'; }
    // тултип у самого списка: вложен в контейнер компонента -> не теряется при перерисовке
    function attachTip(wrap) {
        if (!wrap || wrap.dataset.epgTip === '1') return;
        wrap.dataset.epgTip = '1';
        const host = wrap.parentElement || wrap;
        const el = document.createElement('div');
        el.className = 'epg-tooltip';
        el.style.pointerEvents = 'none';
        host.appendChild(el);
        wrap.addEventListener('mouseover', function (e) {
            // пункт раскрытого списка -> своя подсказка с ссылкой
            const li = e.target.closest ? e.target.closest('li[data-testid="dropdown-option"]') : null;
            if (li) {
                const name = li.getAttribute('aria-label') || (li.innerText || '').trim().split('\\n')[0];
                if (ITEMS[name] !== undefined) {
                    el.textContent = name + ' \\u2014 ' + ITEMS[name];
                    el.style.display = 'block';
                    return;
                }
            }
            const multi = !!wrap.querySelector('input[type=checkbox]');
            const lines = (multi ? TIPS.multi : TIPS.single).split('\\n').filter(s => s.trim());
            el.textContent = lines.join('\\n');
            el.style.display = 'block';
        });
        wrap.addEventListener('mousemove', function (e) {
            if (el.style.display !== 'block') return;
            const r = host.getBoundingClientRect();
            const w = el.offsetWidth, h = el.offsetHeight;
            const left = Math.min(Math.max(-r.left + 8, e.clientX - r.left + 14),
                                  window.innerWidth - r.left - w - 8);
            let top = e.clientY - r.top + 18;
            if (top + h > window.innerHeight - r.top - 8) top = Math.max(-r.top + 8, e.clientY - r.top - h - 12);
            el.style.left = left + 'px';
            el.style.top = top + 'px';
        });
        wrap.addEventListener('mouseout', function (e) {
            if (!wrap.contains(e.relatedTarget)) el.style.display = 'none';
        });
    }
    function scan() {
        document.querySelectorAll('.epg-tip').forEach(attachTip);
    }
    scan();
    new MutationObserver(scan).observe(document.body, {childList: true, subtree: true});
})();
</script>
""".replace("__ITEMS__", json.dumps(EPG_ITEMS_MAP, ensure_ascii=False)) \
   .replace("__SINGLE__", json.dumps(EPG_DROPDOWN_TIP, ensure_ascii=False)) \
   .replace("__MULTI__", json.dumps(EPG_MULTI_TIP, ensure_ascii=False))


def cleaner_function(files, blocklist_text, epg_choice=None, epg_custom="", catchup_choice="", catchup_source=""):
    """Очистка и объединение M3U файлов"""
    if not files:
        return None, "Ошибка: не выбраны файлы"
    epg_url = _resolve_epg(epg_choice, epg_custom)
    
    output_folder = create_output_folder()
    cleaner = M3UCleaner()
    
    progress_log = []
    def log_progress(msg):
        progress_log.append(msg)
    
    file_paths = [f.name for f in files]
    result, stats = cleaner.clean_m3u(file_paths, blocklist_text, log_progress)
    
    if result is None:
        return None, f"Ошибка: {stats.get('error', 'Неизвестная ошибка')}"
    
    _mode, _src = archive_params(catchup_choice, catchup_source)
    result = apply_header(result, epg_url, _mode, catchup_source=_src)
    result = merge_group_synonyms(result)
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


def tester_function(files, timeout, workers, epg_choice=None, epg_custom="", catchup_choice="", catchup_source=""):
    """Тестирование потоков (список нарезается на куски < 300 каналов)"""
    if not files:
        return None, "Ошибка: не выбраны файлы"
    epg_url = _resolve_epg(epg_choice, epg_custom)

    output_folder = create_output_folder()
    verified_folder = create_verified_folder()
    tester = M3UTester(timeout=timeout, max_workers=workers)

    progress_log = []
    def log_progress(msg):
        progress_log.append(msg)

    file_paths = [f.name for f in files]
    result, stats = tester.test_playlists(file_paths, log_progress,
                                          chunk_size=CHUNK_SIZE, save_dir=verified_folder)

    if result is None:
        return None, f"Ошибка: {stats.get('error', 'Неизвестная ошибка')}"

    _mode, _src = archive_params(catchup_choice, catchup_source)
    result = apply_header(result, epg_url, _mode, catchup_source=_src)
    result = merge_group_synonyms(result)
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

💾 Сохранено:
- Единый файл рабочих: {output_file}
- Папка с проверенными каналами (куски): {verified_folder}
"""

    result_files = [str(output_file)]
    return result_files, stats_text


# ---------------------------------------------------------------------------
# Конвейер фильтрации: временная папка -> куски < 300 каналов -> прогон по
# очереди -> результаты в папке проверенных -> объединение -> отсев
# иностранных -> разбивка на группы по темам
# ---------------------------------------------------------------------------
CHUNK_SIZE = 280  # максимум каналов в одном куске (всегда меньше 300)


def _load_streams_chunked(file_paths, verified_folder, log):
    """Читает плейлисты и нарезает список на куски < 300 каналов."""
    seen = set()
    streams = []
    dups = 0
    for p in file_paths:
        log(0.0, "Чтение плейлистов...", f"📂 Читаю: {Path(p).name}")
        _, entries = parse_m3u(p)
        for e in entries:
            key = url_key(e["url"])
            if key in seen:
                dups += 1
                continue
            seen.add(key)
            info = set_group(e["info"], e["group"]) if e["group"] else e["info"]
            streams.append({"info": info, "url": e["url"], "extra": e["extra"]})
    chunks = [streams[i:i + CHUNK_SIZE] for i in range(0, len(streams), CHUNK_SIZE)]
    log(0.0, "Нарезка списка",
        f"🔍 Каналов: {len(streams)} (дубликатов пропущено: {dups}). "
        f"Нарезаю на {len(chunks)} кусок(ов) по ≤{CHUNK_SIZE}. "
        f"Папка проверенных: {verified_folder}")
    return streams, chunks


def _save_chunk_result(verified_folder, idx, subset, kept):
    """Сохраняет результат куска в папку с проверенными каналами."""
    lines = ["#EXTM3U\n", f"# Кусок {idx}: каналов {len(subset)}, оставлено {len(kept)}\n"]
    kept_ids = {id(s) for s in kept}
    for s in subset:
        mark = "OK" if id(s) in kept_ids else "FAIL"
        lines.append(f"# [{mark}]\n")
        lines.append(s["info"] + "\n")
        lines.extend(x + "\n" for x in s.get("extra", []))
        lines.append(s["url"] + "\n")
    path = Path(verified_folder) / f"chunk_{idx:03d}.m3u"
    path.write_text("".join(lines), encoding="utf-8")
    return path


def _merge_chunks(verified_folder):
    """В конце объединяет все сохранённые куски в один файл merged_all.m3u."""
    from modules.m3u_utils import entry_to_lines
    merged = ["#EXTM3U\n", "# Объединённый результат всех кусков\n"]
    urls_seen = set()
    for cf in sorted(Path(verified_folder).glob("chunk_*.m3u")):
        _, entries = parse_m3u(str(cf))
        for e in entries:
            k = url_key(e["url"])
            if k in urls_seen:
                continue
            urls_seen.add(k)
            merged.extend(entry_to_lines(e))
    out = Path(verified_folder) / "merged_all.m3u"
    out.write_text("".join(merged), encoding="utf-8")
    return out, len(urls_seen)


def _filter_chunk(streams):
    """Фильтрация одного куска: убираем пустые адреса и каналы без имени."""
    keep = []
    for s in streams:
        try:
            _, _, name = split_extinf(s["info"])
        except Exception:
            name = ""
        url = (s.get("url") or "").strip()
        if not url or not name.strip():
            continue
        keep.append(s)
    return keep


def _split_foreign_keep(streams, exceptions=None):
    """Отсеивает иностранные каналы: всё, что не подтверждено как русскоязычное
    (правила Organizer'а: >= 2 — точно русский; < 2 — сомнительные/иностранные).
    Канал, в названии которого есть слово из except.md, всегда остаётся (русскоязычный).
    Возвращает (ru, foreign, сколько спасено по except.md)."""
    from modules.organizer import Organizer
    ru, foreign = [], []
    kept_by_exc = 0
    for s in streams:
        e = {"attrs": {}, "name": "", "group": "", "url": s.get("url", "")}
        try:
            _, attrs, name = split_extinf(s["info"])
            e["attrs"], e["name"] = attrs, name or ""
            e["group"] = (attrs.get("group-title") or "").strip()
        except Exception:
            pass
        if is_adult_host(e.get("url")):
            ru.append(s)   # 18+-хост: язык не важен, дальше его заберёт _split_adult
            continue
        rec = _load_databank().get(org_norm(e["name"]))
        if rec and rec.get("lang") in ("ru", "cis", "foreign") and not url_looks_russian(e.get("url")):
            (foreign if rec["lang"] == "foreign" else ru).append(s)   # датабанк важнее правил
            continue
        if exceptions and exceptions.match(e["name"]) and not Organizer._foreign_evidence(e):
            ru.append(s)
            kept_by_exc += 1
            continue
        if Organizer._is_cis(e):
            ru.append(s)   # Украина/Беларусь остаются: Organizer соберёт их в группу «СНГ»
            continue
        score = Organizer._lang_score(e)
        # спорные (0…1) с кириллицей в названии считаем русскими, остальные — иностранными
        is_ru = score >= 2 or (score >= 0 and bool(re.search(r"[А-Яа-яЁё]", e["name"])))
        (ru if is_ru else foreign).append(s)
    return ru, foreign, kept_by_exc


def _split_adult(streams):
    """18+ по названию — в отдельный список (язык канала не важен). -> (adult, rest)."""
    from modules.organizer import ADULT_RE
    adult, rest = [], []   # хост из adult_hosts.md — 18+ сразу, не глядя
    for s in streams:
        try:
            _, _, name = split_extinf(s["info"])
        except Exception:
            name = ""
        (adult if (is_adult_host(s.get("url")) or ADULT_RE.search(name or "")) else rest).append(s)
    return adult, rest


def _vl_adult_split(streams, cb):
    """Отделяет 18+ ПО КАДРУ через VL-модель (LLMFilter) -> (adult, rest).
    Если модель/сервер недоступны — тихий откат: ([], streams)."""
    from modules.llm_filter import LLMFilter, parse_verdict
    filt = LLMFilter(frames_per_channel=1)
    try:
        filt.check_files()
        filt.start_server()
    except Exception as e:
        cb(0.85, "18+ по кадру", f"⚠️ VL недоступна ({e}) — отделяю только по названию")
        return [], streams
    adult, rest = [], []
    try:
        n = len(streams)
        for i, s in enumerate(streams, 1):
            frame = filt.grab_frame(s["url"])
            is_adult = False
            if frame:
                try:
                    tag, _ = parse_verdict(filt.ask_model(frame))
                    is_adult = (tag == "ADULT")
                except Exception:
                    pass
            (adult if is_adult else rest).append(s)
            cb(0.85 + 0.1 * i / max(1, n), "18+ по кадру",
               f"🔞 Кадры: {i}/{n} · найдено 18+: {len(adult)}")
    finally:
        filt.stop_server()
    return adult, rest


def _group_by_topics(streams):
    """Группировка по темам — та же логика, что и в вкладке LLM-Filter (Organizer без модели, язык не перепроверяется):
    сдвиги времени, группы по ≤ 50, приоритеты, качество, кэш названий. Возвращает (строки, сводка)."""
    org = Organizer(llm=None, exceptions=_load_exceptions(), priority=_load_priority(),
                    cache_path=SORT_CACHE_FILE, skip_language=True, databank=_load_databank())
    res = org.organize(streams)
    return res["ru_lines"], res["ru_groups"]


def _group_by_topics_old(streams):
    """Разбивает каналы на группы по темам (правила Organizer'а, без модели)."""
    from modules.organizer import Organizer, CATS
    groups_map = {}
    for s in streams:
        _, attrs, name = split_extinf(s["info"])
        e = {"attrs": attrs, "name": name or "",
             "group": (attrs.get("group-title") or "").strip()}
        cat = Organizer._rule_category(e)
        if not cat or cat == "other":
            g = canonical_group(e["group"]) or "Разное"
        else:
            emoji, title, _, _ = CATS[cat]
            g = f"{emoji} {title}"
        groups_map.setdefault(g, []).append(s)
    ordered = sorted(groups_map.items(), key=lambda kv: kv[0])
    lines = ["#EXTM3U\n"]
    summary = []
    for gname, part in ordered:
        summary.append((gname, len(part)))
        for s in part:
            lines.append(set_group(s["info"], gname) + "\n")
            lines.extend(x + "\n" for x in s.get("extra", []))
            lines.append(s["url"] + "\n")
    return lines, summary


def _write_simple_m3u(path, streams, epg, mode, src, group=None):
    """Пишет простой m3u (шапка + каналы). group — принудительная группа (иначе исходная)."""
    path = Path(path)
    lines = ["#EXTM3U\n"]
    for s in streams:
        info = set_group(s["info"], group) if group else s["info"]
        lines.append(info + "\n")
        lines.extend(x + "\n" for x in s.get("extra", []))
        lines.append(s["url"] + "\n")
    lines = apply_header(lines, epg, mode, catchup_source=src)
    path.write_text("".join(lines), encoding="utf-8")
    return path


def run_pipeline(files, epg_choice, epg_custom, catchup_choice="", catchup_source="", do_test=False,
                 timeout=8, workers=15,
                 remove_foreign=True, group_topics=True, add_logos=True,
                 separate_porn=False, porn_method="Только по названию", progress=gr.Progress()):
    """Полный конвейер фильтрации: временная папка с проверенными каналами →
    нарезка списка на куски < 300 каналов → прогон кусков по очереди (при
    включённой FFmpeg-проверке) → сохранение каждого результата в папке
    проверенных → объединение всего в один файл → отсев иностранных каналов →
    разбивка на группы по темам."""
    if not files:
        yield None, "Ошибка: не выбраны файлы"
        return

    epg = _resolve_epg(epg_choice, epg_custom)
    file_paths = _paths(files)
    events = queue.Queue()
    holder = {}

    def cb(frac, desc, line=None):
        events.put(("p", frac, desc, line))

    def worker():
        try:
            # Временная папка для нарезки и результатов кусков (удаляется в конце),
            # отдельная постоянная папка с проверенными каналами: название = день + дата
            tmp_folder = OUTPUT_DIR / datetime.now().strftime("_tmp_фильтр_%H-%M-%S")
            tmp_folder.mkdir(parents=True, exist_ok=True)
            verified_folder = create_verified_folder()
            streams, chunks = _load_streams_chunked(file_paths, verified_folder, cb)
            if not streams:
                raise RuntimeError("В файлах нет каналов")

            kept_all = []
            total_chunks = len(chunks)
            for ci, chunk in enumerate(chunks, start=1):
                base = ci / (total_chunks + 2.0)
                cb(base, f"Кусок {ci}/{total_chunks}",
                   f"🧩 Прогоняю кусок {ci}/{total_chunks}: каналов {len(chunk)}")
                if do_test:
                    # FFmpeg-проверка только этого куска, по очереди
                    tester = M3UTester(timeout=timeout, max_workers=workers)
                    res, st = tester.test_playlists([_chunk_to_tmpfile(tmp_folder, ci, chunk)],
                                                    lambda m: cb(base, f"Кусок {ci}/{total_chunks}", m),
                                                    chunk_size=len(chunk) + 1,
                                                    save_dir=tmp_folder / f"_test_{ci:03d}")
                    if res is None:
                        kept = list(chunk)
                    else:
                        ok_urls = {s["url"] for s in tester.working_streams}
                        kept = [s for s in chunk if s["url"] in ok_urls]
                else:
                    kept = _filter_chunk(chunk)
                kept_all.extend(kept)
                _save_chunk_result(verified_folder, ci, chunk, kept)

            merged_path, merged_count = _merge_chunks(verified_folder)
            cb(0.75, "Объединение кусков",
               f"🔗 Объединил {total_chunks} кусков в {merged_path.name}: каналов {merged_count}")

            final = kept_all
            foreign_n = 0
            kept_by_exc = 0

            # 18+/порно — только если включена галочка; язык не важен
            adult_list = []
            adult_n = 0
            if separate_porn:
                adult_list, final = _split_adult(final)   # жёстко по названию
                if PORN_METHODS.get(porn_method, "name") == "frame" and final:
                    cb(0.85, "18+ по кадру", f"🔞 Проверяю кадры VL: каналов {len(final)}")
                    extra, final = _vl_adult_split(final, cb)
                    adult_list.extend(extra)
                adult_n = len(adult_list)
                if adult_n:
                    cb(0.88, "18+ отдельно", f"🔞 18+ вынесено в отдельный список: {adult_n}")

            foreign_list = []
            if remove_foreign:
                ru, foreign_list, kept_by_exc = _split_foreign_keep(final, _load_exceptions())
                foreign_n = len(foreign_list)
                final = ru
                cb(0.9, "Отсев иностранных",
                   f"🌍 Иностранных отсеяно: {foreign_n} (сохранены в foreign.m3u), осталось: {len(final)}"
                   + (f" (по списку except.md сохранено: {kept_by_exc})" if kept_by_exc else ""))

            out_folder = create_output_folder()
            if group_topics:
                lines, summary = _group_by_topics(final)
            else:
                lines = ["#EXTM3U\n"]
                for s in final:
                    lines.append(s["info"] + "\n")
                    lines.extend(x + "\n" for x in s.get("extra", []))
                    lines.append(s["url"] + "\n")
                summary = []
            icons_n = 0
            if add_logos:
                lines, icons_n, _ = logos.apply_logos_to_lines(lines, _logo_matcher())
                cb(0.95, "Иконки каналов", f"🖼 Пришито иконок: {icons_n}")
            _mode, _src = archive_params(catchup_choice, catchup_source)
            lines = apply_header(lines, epg, _mode, catchup_source=_src)
            result_file = out_folder / "final_filtered.m3u"
            result_file.write_text("".join(lines), encoding="utf-8")

            # 🌍 иностранные и 🔞 18+ — сохраняем в отдельные файлы (ничего не теряем)
            extra_files = []
            if foreign_list:
                extra_files.append(str(_write_simple_m3u(
                    out_folder / "foreign.m3u", foreign_list, epg, _mode, _src)))
            if adult_list:
                extra_files.append(str(_write_simple_m3u(
                    out_folder / "adult_18plus.m3u", adult_list, epg, _mode, _src, group="🔞 18+")))

            # временная папка больше не нужна — удаляем
            import shutil
            shutil.rmtree(tmp_folder, ignore_errors=True)
            cb(1.0, "Готово", f"🧹 Временная папка удалена; куски сохранены в: {verified_folder}")
            holder["result"] = (str(result_file), str(merged_path), total_chunks,
                                len(streams), len(final), foreign_n, adult_n, summary, icons_n, kept_by_exc,
                                extra_files)
        except Exception as e:
            holder["error"] = str(e)
        finally:
            events.put(("done",))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    log_lines = []
    while True:
        try:
            ev = events.get(timeout=0.5)
        except queue.Empty:
            continue
        if ev[0] == "done":
            break
        _, frac, desc, line = ev
        progress(max(0.0, min(1.0, frac)), desc=desc)
        if line:
            log_lines.append(line)
            yield None, "\n".join(log_lines[-200:])

    if "error" in holder:
        yield None, "\n".join(log_lines[-200:]) + f"\n\n❌ Ошибка: {holder['error']}"
        return

    (result_file, merged_path, nchunks, total, final_n, foreign_n, adult_n,
     summary, icons_n, kept_by_exc, extra_files) = holder["result"]
    text = ("✅ Готово!\n\n"
            f"📊 Конвейер:\n"
            f"- Каналов всего: {total}\n"
            f"- Кусков прогнано (< 300 каналов): {nchunks}\n"
            f"- Объединённый файл кусков: {merged_path}\n"
            + (f"- 🔞 18+ вынесено в отдельный файл: {adult_n}\n" if adult_n else "")
            + (f"- 🌍 Иностранных отсеяно (сохранены в foreign.m3u): {foreign_n}\n" if remove_foreign else "")
            + (f"- Сохранено по списку except.md: {kept_by_exc}\n" if kept_by_exc else "")
            + (f"- Пришито иконок: {icons_n}\n" if add_logos else "")
            + f"- Итоговых каналов: {final_n}\n"
            f"- 💾 Результат: {result_file}\n")
    if extra_files:
        text += "- 📄 Отдельно сохранены:\n" + "\n".join(f"    • {p}" for p in extra_files) + "\n"
    if summary:
        text += "\n🗂 Группы по темам:\n" + "\n".join(f"  • {g} — {n}" for g, n in summary[:60])
    yield [result_file, merged_path] + extra_files, text


def _chunk_to_tmpfile(tmp_folder, idx, chunk):
    """Записывает кусок во временный m3u для FFmpeg-проверки, возвращает путь."""
    lines = ["#EXTM3U\n"]
    for s in chunk:
        lines.append(s["info"] + "\n")
        lines.extend(x + "\n" for x in s.get("extra", []))
        lines.append(s["url"] + "\n")
    path = Path(tmp_folder) / f"_in_{idx:03d}.m3u"
    path.write_text("".join(lines), encoding="utf-8")
    return str(path)


def epg_function(files, sources_choice=None, sources_custom="", epg_choice=None, epg_custom="", catchup_choice="", catchup_source=""):
    """EPG под выбранные каналы: скачать XMLTV, отфильтровать, вписать tvg-id"""
    if not files:
        return None, "Ошибка: не выбраны файлы"
    sources = _resolve_epg(sources_choice, sources_custom)
    epg_url = _resolve_epg(epg_choice, epg_custom)

    from modules.epg_builder import EpgBuilder
    output_folder = create_output_folder()
    builder = EpgBuilder()

    progress_log = []
    def log_progress(msg):
        progress_log.append(msg)

    xml, name2id = builder.build([f.name for f in files], sources, log_progress)

    epg_file = output_folder / "epg_filtered.xml.gz"
    with open(epg_file, "wb") as f:
        f.write(gzip.compress(xml))

    lines, added = EpgBuilder.annotate_playlist([f.name for f in files], name2id)
    # Ссылка в шапку плейлиста: своё значение, а если пусто — берём те же ссылки EPG
    lines = merge_group_synonyms(lines)
    header_url = (epg_url or "").strip() or sources
    _mode, _src = archive_params(catchup_choice, catchup_source)
    lines = apply_header(lines, header_url, _mode, catchup_source=_src)
    m3u_file = output_folder / "playlist_tvgid.m3u"
    with open(m3u_file, "w", encoding="utf-8") as f:
        f.writelines(lines)

    st = builder.stats
    stats_text = f"""✅ EPG готов!

📊 Статистика:
- Каналов в плейлисте: {st['channels_in_playlist']}
- Источников EPG: {st['sources']}
- Каналов EPG оставлено: {st['epg_channels']}
- Передач: {st['programmes']}
- Проставлено tvg-id: {added}
- Ошибки: {len(st['errors'])}

💾 Сохранено:
- EPG под мои каналы: {epg_file}
- Плейлист с tvg-id: {m3u_file}
"""
    if st["errors"]:
        stats_text += "\n⚠️ " + "\n".join(str(e) for e in st["errors"][:5])
    return [str(epg_file), str(m3u_file)], stats_text


def converter_function(files):
    """Конвертация M3U в PDF/HTML/MD"""
    if not files:
        return None, None, None, "Ошибка: не выбраны файлы"
    
    output_folder = create_output_folder()
    converter = M3UConverter(str(FONT_PATH))
    
    progress_log = []
    def log_progress(msg):
        progress_log.append(msg)
    
    file_paths = [f.name for f in files]
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


# ---------------------------------------------------------------------------
# Merger: склейка плейлистов и правка групп в одном окне
# ---------------------------------------------------------------------------
_merger = M3UMerger()


def _merger_view(state, status):
    """Обновляет чекбоксы групп и список целевых групп"""
    labels = _merger.labels(state)
    names = list(state["groups"].keys())
    return (state,
            gr.update(choices=labels, value=[]),
            gr.update(choices=names, value=None),
            status)


def merger_load(files, ignore_query):
    """Склеивает плейлисты (одинаковые адреса потоков пропускаются) и показывает группы"""
    state = M3UMerger.new_state()
    if not files:
        return _merger_view(state, "Загрузите M3U файлы")
    try:
        state, stats = _merger.load(_paths(files), bool(ignore_query))
    except Exception as e:
        return _merger_view(state, f"❌ Ошибка: {e}")

    epg = state["epg"][0] if state["epg"] else "—"
    text = (f"✅ Склеено файлов: {stats['files']}\n"
            f"Каналов всего: {stats['total']}\n"
            f"Дубликатов по адресу потока пропущено: {stats['duplicates']}\n"
            f"Осталось каналов: {stats['kept']}, групп: {len(state['groups'])}\n"
            f"EPG из первого плейлиста: {epg}")
    if stats["errors"]:
        text += "\n⚠️ " + "\n".join(stats["errors"])
    return _merger_view(state, text)


def merger_delete(state, selected):
    """Удаляет выбранные группы"""
    names = _merger.names_from_labels(selected)
    if not names:
        return _merger_view(state, "Выберите группы для удаления")
    removed = _merger.delete_groups(state, names)
    return _merger_view(state, f"🗑️ Удалено групп: {len(names)}, каналов: {removed}\n"
                               f"Осталось: групп {len(state['groups'])}, каналов {_merger.total(state)}")


def merger_merge(state, selected, target):
    """Объединяет выбранные группы в одну (можно ввести новое название)"""
    names = _merger.names_from_labels(selected)
    target = (target or "").strip()
    if not target:
        return _merger_view(state, "Укажите название группы, в которую объединять (можно ввести новое)")
    if not [n for n in names if n != target]:
        return _merger_view(state, "Выберите хотя бы одну группу для объединения (кроме целевой)")
    moved = _merger.merge_groups(state, target, names)
    return _merger_view(state, f"🔗 Объединено в «{target}»: перенесено каналов {moved}\n"
                               f"Осталось: групп {len(state['groups'])}, каналов {_merger.total(state)}")


def merger_undo(state):
    """Отменяет последнее удаление или объединение"""
    if _merger.undo(state):
        return _merger_view(state, f"↩️ Отменено. Групп {len(state['groups'])}, каналов {_merger.total(state)}")
    return _merger_view(state, "Нечего отменять")


def merger_save(state, epg_choice=None, epg_custom="", catchup_choice="", catchup_source=""):
    """Сохраняет итоговый плейлист. В шапке только одна ссылка EPG"""
    if not state or not state["groups"]:
        return None, "Нет данных: сначала склейте плейлисты"
    chosen = _resolve_epg(epg_choice, epg_custom)
    epg = chosen or _merger.pick_epg(state, "")
    _mode, _src = archive_params(catchup_choice, catchup_source)
    lines = apply_header(_merger.write_lines(state), epg, _mode, single=True, catchup_source=_src)
    output_folder = create_output_folder()
    output_file = output_folder / "merged.m3u"
    with open(output_file, "w", encoding="utf-8") as f:
        f.writelines(lines)
    text = (f"✅ Сохранено: {output_file}\n"
            f"Групп: {len(state['groups'])}, каналов: {_merger.total(state)}\n"
            f"EPG в шапке: {epg or 'нет'}")
    return str(output_file), text


# ---------------------------------------------------------------------------
# LLM-фильтр и умная сортировка
# ---------------------------------------------------------------------------
def _read_streams_for_sort(file_paths, cb):
    """Читает плейлисты для режима «только сортировка», дубликаты по адресу пропускаются"""
    seen = set()
    streams = []
    dups = 0
    for p in file_paths:
        cb(0.0, "Чтение плейлистов...", f"📂 Читаю: {Path(p).name}")
        _, entries = parse_m3u(p)
        for e in entries:
            key = url_key(e["url"])
            if key in seen:
                dups += 1
                continue
            seen.add(key)
            info = set_group(e["info"], e["group"]) if e["group"] else e["info"]
            streams.append({"info": info, "url": e["url"], "extra": e["extra"]})
    cb(0.0, "Чтение плейлистов...", f"🔍 Каналов: {len(streams)} (дубликатов по адресу пропущено: {dups})")
    return streams


def _run_sort(filt, streams, cb, stop_event, llm_workers, best_quality=False):
    """Запускает умную сортировку. Если модель недоступна, работает по правилам"""
    llm = None
    try:
        filt.start_server()
        llm = filt
    except Exception as e:
        cb(0.0, "Сортировка без модели", f"⚠️ Модель недоступна ({e}). Сортирую только по правилам")
    org = Organizer(llm=llm, cb=cb, stop=stop_event, llm_workers=llm_workers, exceptions=_load_exceptions(),
                    priority=_load_priority(), cache_path=SORT_CACHE_FILE, best_quality=best_quality,
                    databank=_load_databank())
    return org.organize(streams)


def _groups_text(groups, limit=60):
    rows = [f"  • {g} — {n}" for g, n in groups[:limit]]
    if len(groups) > limit:
        rows.append(f"  … ещё групп: {len(groups) - limit}")
    return "\n".join(rows)


def llm_filter_function(files, timeout, ffmpeg_workers, llm_workers, frames, remove_no_frame,
                        sort_enabled, sort_only, epg_choice=None, epg_custom="",
                        catchup_choice="", catchup_source="",
                        add_logos=True, separate_porn=True, best_quality=False, porn_method="По названию и по кадру (VL)",
                        progress=gr.Progress()):
    """FFmpeg-отсев + проверка кадров моделью (удаление заглушек) + умная сортировка по группам"""
    if not files:
        yield None, "Ошибка: не выбраны файлы"
        return
    epg_url = _resolve_epg(epg_choice, epg_custom)

    sort_enabled = bool(sort_enabled or sort_only)
    file_paths = _paths(files)
    events = queue.Queue()
    stop_event = threading.Event()
    holder = {}

    def cb(frac, desc, line=None):
        events.put(("p", frac, desc, line))

    def worker():
        filt = None
        try:
            filt = LLMFilter(
                ffmpeg_timeout=timeout,
                ffmpeg_workers=ffmpeg_workers,
                llm_workers=llm_workers,
                frames_per_channel=frames,
                remove_no_frame=remove_no_frame,
                keep_server=sort_enabled,
                separate_adult=separate_porn,
                adult_by_frame=(PORN_METHODS.get(porn_method, "frame") == "frame"),
            )
            streams = None
            if sort_only:
                streams = _read_streams_for_sort(file_paths, cb)
                if not streams:
                    raise RuntimeError("В файлах нет каналов")
            else:
                res = filt.run(file_paths, cb, stop_event)
                holder["result"] = res
                st = res["stats"]
                if sort_enabled and not st["interrupted"] and st["ffmpeg_working"] > 0:
                    streams = res["kept_streams"]
            if sort_enabled and streams:
                holder["sorted"] = _run_sort(filt, streams, cb, stop_event, llm_workers, best_quality)
        except Exception as e:
            holder["error"] = str(e)
        finally:
            if filt is not None:
                filt.stop_server()
            events.put(("done",))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    log_lines = []
    try:
        while True:
            try:
                ev = events.get(timeout=0.5)
            except queue.Empty:
                continue
            if ev[0] == "done":
                break
            _, frac, desc, line = ev
            progress(frac, desc=desc)
            if line:
                log_lines.append(line)
                yield None, "\n".join(log_lines[-200:])
    finally:
        stop_event.set()

    if "error" in holder:
        yield None, "\n".join(log_lines[-200:]) + f"\n\n❌ Ошибка: {holder['error']}"
        return

    result = holder.get("result")
    sorted_res = holder.get("sorted")
    stats = result["stats"] if result else None

    if stats and stats["interrupted"]:
        yield None, "\n".join(log_lines[-200:]) + "\n\n⚠️ Прервано, результат не сохранён"
        return

    if stats and stats["ffmpeg_working"] == 0:
        yield None, "\n".join(log_lines[-200:]) + "\n\n❌ FFmpeg не нашёл ни одного рабочего потока, файл не сохранён.\nВозможные причины: провайдер временно блокирует запросы (слишком много проверок подряд), нет интернета или загружен не тот файл. Подождите несколько минут и повторите. Если снова 0, уменьшите «Параллельных FFmpeg-проверок» до 5."
        return

    if sort_only and sorted_res is None:
        yield None, "\n".join(log_lines[-200:]) + "\n\n⚠️ Сортировка прервана, результат не сохранён"
        return

    output_folder = create_output_folder()
    out_files = []
    saved = []

    icons_n = 0
    _mode, _src = archive_params(catchup_choice, catchup_source)
    if sorted_res is not None:
        ru_lines = sorted_res["ru_lines"]
        foreign_lines = sorted_res["foreign_lines"]
        if add_logos:
            ru_lines, n1, _ = logos.apply_logos_to_lines(ru_lines, _logo_matcher())
            foreign_lines, n2, _ = logos.apply_logos_to_lines(foreign_lines, _logo_matcher())
            icons_n += n1 + n2
        ru_file = output_folder / "ru_sorted.m3u"
        foreign_file = output_folder / "foreign.m3u"
        with open(ru_file, 'w', encoding='utf-8') as f:
            f.writelines(apply_header(ru_lines, epg_url, _mode, catchup_source=_src))
        with open(foreign_file, 'w', encoding='utf-8') as f:
            f.writelines(apply_header(foreign_lines, epg_url, _mode, catchup_source=_src))
        out_files += [str(ru_file), str(foreign_file)]
        if sorted_res.get("report_lines"):
            sort_report = output_folder / "sort_report.txt"
            with open(sort_report, 'w', encoding='utf-8') as f:
                f.writelines(sorted_res["report_lines"])
            out_files.append(str(sort_report))
            saved.append(f"- 📋 Отчёт сортировки (почему канал в группе): {sort_report}")
        saved.append(f"- Русскоязычные по темам: {ru_file}")
        saved.append(f"- Нерусскоязычные (отдельно): {foreign_file}")

    if result is not None:
        if sorted_res is None:
            kept_lines = merge_group_synonyms(result["kept_lines"])
            if add_logos:
                kept_lines, n1, _ = logos.apply_logos_to_lines(kept_lines, _logo_matcher())
                icons_n += n1
            kept_file = output_folder / "llm_filtered.m3u"
            with open(kept_file, 'w', encoding='utf-8') as f:
                f.writelines(apply_header(kept_lines, epg_url, _mode, catchup_source=_src))
            out_files.append(str(kept_file))
            saved.append(f"- Чистый плейлист: {kept_file}")
        removed_file = output_folder / "llm_removed.m3u"
        with open(removed_file, 'w', encoding='utf-8') as f:
            f.writelines(result["removed_lines"])
        out_files.append(str(removed_file))
        saved.append(f"- Удалённые (отчёт): {removed_file}")
        if result.get("adult_lines"):
            adult_file = output_folder / "adult_18plus.m3u"
            with open(adult_file, 'w', encoding='utf-8') as f:
                f.writelines(result["adult_lines"])
            out_files.append(str(adult_file))
            saved.append(f"- 18+ отдельно: {adult_file}")
        if result.get("report_lines"):
            report_file = output_folder / "llm_report.txt"
            with open(report_file, 'w', encoding='utf-8') as f:
                f.writelines(result["report_lines"])
            out_files.append(str(report_file))
            saved.append(f"- 📋 Отчёт (что удалено/оставлено): {report_file}")

    text = "✅ Готово!\n"
    if stats:
        text += f"""
📊 Фильтр:
- Уникальных потоков: {stats['found']} (дубликатов пропущено: {stats['duplicates']})
- FFmpeg: рабочих {stats['ffmpeg_working']}, мёртвых {stats['ffmpeg_dead']}
- Проверено моделью: {stats['checked']}
- Удалено заглушек (оплата, «сервис недоступен» и т. п.): {stats['payment']}
- Вынесено 18+ (по названию + по кадру): {stats.get('adult', 0)}
- Нормальных: {stats['ok']}
- Кадр не получен: {stats['no_frame']} (удалено из них: {stats['no_frame_removed']})
- Ошибок модели: {stats['llm_errors']}
- Осталось в плейлисте: {stats['kept']}
"""
    if sorted_res is not None:
        s = sorted_res["stats"]
        text += f"""
🗂 Сортировка:
- Русскоязычных каналов: {s['ru']} (со сдвигом времени: {s['shifted']}), групп: {s['ru_groups']}
- Нерусскоязычных каналов: {s['foreign']}, групп: {s['foreign_groups']}
- Запросов к модели: {s['llm_calls']} (ошибок {s['llm_errors']}); язык решила модель для {s['llm_lang']}, тему для {s['llm_cat']}

🇷🇺 Группы русскоязычных:
{_groups_text(sorted_res['ru_groups'])}
"""
    if add_logos and icons_n:
        text += f"\n🖼 Пришито иконок: {icons_n}\n"
    text += "\n💾 Сохранено:\n" + "\n".join(saved)
    yield out_files, text


# Создание Gradio интерфейса
with gr.Blocks(title="m3uGenius", theme=gr.themes.Soft(), head=EPG_HEAD_HTML) as app:
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
                    with gr.Row():
                        cleaner_epg = _epg_dropdown()
                        cleaner_epg_custom = gr.Textbox(label="Своя EPG ссылка", value="", placeholder="своя ссылка xml.gz")
                    cleaner_catchup = gr.Dropdown(label="Способ запроса архива", choices=CATCHUP_LABELS, value=CATCHUP_DEFAULT)
                    cleaner_catchup_src = gr.Textbox(label="Шаблон архива (только append/default; пусто = ?utc=${start})", value="")
                    cleaner_btn = gr.Button("🚀 Запустить очистку", variant="primary")
                with gr.Column():
                    cleaner_output = gr.File(label="Результат")
                    cleaner_stats = gr.Textbox(label="Статистика", lines=10)
            
            cleaner_btn.click(
                cleaner_function,
                inputs=[cleaner_files, cleaner_blocklist, cleaner_epg, cleaner_epg_custom, cleaner_catchup, cleaner_catchup_src],
                outputs=[cleaner_output, cleaner_stats]
            )
            cleaner_catchup.change(_toggle_catchup_src, inputs=cleaner_catchup, outputs=cleaner_catchup_src)
        
        # TAB 2: Tester
        with gr.Tab("🔍 Tester"):
            gr.Markdown("### Тестирование потоков через FFmpeg")
            with gr.Row():
                with gr.Column():
                    tester_files = gr.File(label="M3U файлы", file_count="multiple", file_types=[".m3u", ".m3u8"])
                    tester_timeout = gr.Slider(minimum=3, maximum=20, value=8, step=1, label="Timeout (секунды)")
                    tester_workers = gr.Slider(minimum=5, maximum=50, value=15, step=5, label="Параллельных потоков")
                    with gr.Row():
                        tester_epg = _epg_dropdown()
                        tester_epg_custom = gr.Textbox(label="Своя EPG ссылка", value="", placeholder="своя ссылка xml.gz")
                    tester_catchup = gr.Dropdown(label="Способ запроса архива", choices=CATCHUP_LABELS, value=CATCHUP_DEFAULT)
                    tester_catchup_src = gr.Textbox(label="Шаблон архива (только append/default; пусто = ?utc=${start})", value="")
                    tester_btn = gr.Button("🚀 Запустить тестирование", variant="primary")
                with gr.Column():
                    tester_output = gr.File(label="Единый плейлист рабочих потоков (включая MP1/MP2)", file_count="multiple")
                    tester_stats = gr.Textbox(label="Статистика", lines=10)
            
            tester_btn.click(
                tester_function,
                inputs=[tester_files, tester_timeout, tester_workers, tester_epg, tester_epg_custom, tester_catchup, tester_catchup_src],
                outputs=[tester_output, tester_stats]
            )
            tester_catchup.change(_toggle_catchup_src, inputs=tester_catchup, outputs=tester_catchup_src)

        # TAB: Фильтр-конвейер (нарезка на куски < 300 каналов)
        with gr.Tab("🧩 Фильтр-конвейер"):
            gr.Markdown("### Полный конвейер фильтрации")
            with gr.Row():
                with gr.Column():
                    pipe_files = gr.File(label="M3U файлы", file_count="multiple", file_types=[".m3u", ".m3u8"])
                    pipe_test = gr.Checkbox(label="FFmpeg-проверка потоков (иначе — только базовая фильтрация)", value=False)
                    pipe_timeout = gr.Slider(minimum=3, maximum=20, value=8, step=1, label="Timeout (секунды)")
                    pipe_workers = gr.Slider(minimum=5, maximum=50, value=15, step=5, label="Параллельных FFmpeg-потоков")
                    pipe_foreign = gr.Checkbox(label="Отсеять иностранные каналы (сохраняются в foreign.m3u)", value=True)
                    pipe_topics = gr.Checkbox(label="Разбить на группы по темам", value=True)
                    pipe_logos = gr.Checkbox(label="Пришить иконки каналов (tvg-logo из tv-logos-main)", value=True)
                    pipe_porn = gr.Checkbox(label="Отделять 18+/порно в отдельный список (adult_18plus.m3u)", value=False)
                    pipe_porn_method = gr.Radio(choices=list(PORN_METHODS), value="Только по названию",
                                                label="Метод отделения 18+")
                    with gr.Row():
                        pipe_epg = _epg_dropdown()
                        pipe_epg_custom = gr.Textbox(label="Своя EPG ссылка", value="", placeholder="своя ссылка xml.gz")
                    pipe_catchup = gr.Dropdown(label="Способ запроса архива", choices=CATCHUP_LABELS, value=CATCHUP_DEFAULT)
                    pipe_catchup_src = gr.Textbox(label="Шаблон архива (только append/default; пусто = ?utc=${start})", value="")
                    pipe_btn = gr.Button("🚀 Запустить конвейер", variant="primary")
                with gr.Column():
                    pipe_output = gr.File(label="Результат (итоговый + объединённый файл кусков)", file_count="multiple")
                    pipe_stats = gr.Textbox(label="Ход работы / Статистика", lines=16)

            pipe_btn.click(
                run_pipeline,
                inputs=[pipe_files, pipe_epg, pipe_epg_custom, pipe_catchup, pipe_catchup_src, pipe_test,
                        pipe_timeout, pipe_workers, pipe_foreign, pipe_topics, pipe_logos,
                        pipe_porn, pipe_porn_method],
                outputs=[pipe_output, pipe_stats]
            )
            pipe_catchup.change(_toggle_catchup_src, inputs=pipe_catchup, outputs=pipe_catchup_src)
        
        # TAB: EPG
        with gr.Tab("📺 EPG"):
            gr.Markdown("### EPG под мои каналы: скачать XMLTV, отфильтровать по каналам плейлиста и вписать tvg-id")
            with gr.Row():
                with gr.Column():
                    epg_files = gr.File(label="M3U файлы", file_count="multiple", file_types=[".m3u", ".m3u8"])
                    with gr.Row():
                        epg_sources = _epg_dropdown("Источники EPG (xml / xml.gz)", multi=True,
                                                    value=["IPTVx.one (категории, логотипы, ТБ ГИД)"])
                        epg_sources_custom = gr.Textbox(label="Своя ссылка EPG", lines=2, value="",
                                                        placeholder="можно несколько, по одной на строку")
                    with gr.Row():
                        epg_epgurl = _epg_dropdown("EPG ссылка в шапку плейлиста (url-tvg)")
                        epg_epgurl_custom = gr.Textbox(label="Своя EPG ссылка в шапку", value="",
                                                       placeholder="пусто = впишу ссылки источников")
                    epg_catchup = gr.Dropdown(label="Способ запроса архива", choices=CATCHUP_LABELS, value=CATCHUP_DEFAULT)
                    epg_catchup_src = gr.Textbox(label="Шаблон архива (только append/default; пусто = ?utc=${start})", value="")
                    epg_btn = gr.Button("🚀 Собрать EPG", variant="primary")
                with gr.Column():
                    epg_output = gr.File(label="Результат (EPG + плейлист с tvg-id)", file_count="multiple")
                    epg_stats = gr.Textbox(label="Статистика", lines=12)

            epg_btn.click(
                epg_function,
                inputs=[epg_files, epg_sources, epg_sources_custom, epg_epgurl, epg_epgurl_custom, epg_catchup, epg_catchup_src],
                outputs=[epg_output, epg_stats]
            )
            epg_catchup.change(_toggle_catchup_src, inputs=epg_catchup, outputs=epg_catchup_src)

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
                outputs=[converter_pdf, converter_html, converter_md, converter_stats]
            )
        
        # TAB 4: Merger
        with gr.Tab("🔀 Merger"):
            gr.Markdown("### Склейка плейлистов и правка групп в одном окне\n"
                        "Загрузите плейлисты: одинаковые адреса потоков берутся один раз (первый выигрывает), "
                        "группы читаются прямо из плейлистов. Удаляйте и объединяйте группы сколько нужно, "
                        "а когда всё готово, нажмите «Сохранить». В шапку попадёт только одна ссылка EPG.")
            merger_state = gr.State(M3UMerger.new_state())
            with gr.Row():
                with gr.Column():
                    merger_files = gr.File(label="M3U файлы (порядок важен: EPG и дубликаты берутся от первых)", file_count="multiple", file_types=[".m3u", ".m3u8"])
                    merger_ignore_query = gr.Checkbox(label="Считать одинаковыми адреса, отличающиеся только параметрами после «?»", value=False)
                    merger_load_btn = gr.Button("📥 Склеить и загрузить группы", variant="primary")
                    merger_groups = gr.CheckboxGroup(label="Группы (отметьте нужные)", choices=[], interactive=True)

                with gr.Column():
                    merger_status = gr.Textbox(label="Статус", lines=6)
                    with gr.Row():
                        merger_delete_btn = gr.Button("🗑️ Удалить выбранные", variant="stop")
                        merger_undo_btn = gr.Button("↩️ Отменить последнее")
                    merger_target = gr.Dropdown(label="Объединить выбранные в группу (выберите или введите новое название)",
                                                choices=[], value=None, allow_custom_value=True, interactive=True)
                    merger_merge_btn = gr.Button("🔗 Объединить выбранные", variant="primary")

                    gr.Markdown("#### Сохранение")
                    with gr.Row():
                        merger_epg = _epg_dropdown("EPG ссылка (url-tvg)")
                        merger_epg_custom = gr.Textbox(label="Своя EPG ссылка", value="", placeholder="пусто = возьму ссылку из первого плейлиста")
                    merger_catchup = gr.Dropdown(label="Способ запроса архива", choices=CATCHUP_LABELS, value=CATCHUP_DEFAULT)
                    merger_catchup_src = gr.Textbox(label="Шаблон архива (только append/default; пусто = ?utc=${start})", value="")
                    merger_save_btn = gr.Button("💾 Сохранить плейлист", variant="primary")
                    merger_output = gr.File(label="Результат")

            _view_outputs = [merger_state, merger_groups, merger_target, merger_status]

            merger_load_btn.click(merger_load, inputs=[merger_files, merger_ignore_query], outputs=_view_outputs)
            merger_delete_btn.click(merger_delete, inputs=[merger_state, merger_groups], outputs=_view_outputs)
            merger_merge_btn.click(merger_merge, inputs=[merger_state, merger_groups, merger_target], outputs=_view_outputs)
            merger_undo_btn.click(merger_undo, inputs=[merger_state], outputs=_view_outputs)
            merger_save_btn.click(
                merger_save,
                inputs=[merger_state, merger_epg, merger_epg_custom, merger_catchup, merger_catchup_src],
                outputs=[merger_output, merger_status]
            )
            merger_catchup.change(_toggle_catchup_src, inputs=merger_catchup, outputs=merger_catchup_src)

        # TAB 5: LLM-Filter
        with gr.Tab("🤖 LLM-Filter"):
            gr.Markdown(f"### Удаление каналов-заглушек и умная сортировка (Qwen3-VL, llama-server, порт {LLM_PORT})")
            gr.Markdown("Шаг 1: FFmpeg отсеивает мёртвые потоки. Шаг 2: из рабочих берётся кадр, модель решает — заглушка с текстом (оплата/подписка, «сервис больше недоступен», недоступно в регионе, канал офлайн, ошибка) или нормальная картинка. Заглушки удаляются. Сервер модели запускается и останавливается автоматически.\n\n"
                        "Галочка «Отсортировать»: русскоязычные каналы раскладываются по темам и часовым поясам (Россия +4 и т. п.) в группы по 40–50 каналов с творческими названиями и флагом, нерусскоязычные уходят в отдельный файл.")
            with gr.Row():
                with gr.Column():
                    llm_files = gr.File(label="M3U файлы", file_count="multiple", file_types=[".m3u", ".m3u8"])
                    llm_timeout = gr.Slider(minimum=3, maximum=20, value=8, step=1, label="Timeout FFmpeg (секунды)")
                    llm_ffmpeg_workers = gr.Slider(minimum=5, maximum=50, value=15, step=5, label="Параллельных FFmpeg-проверок")
                    llm_workers = gr.Slider(minimum=1, maximum=4, value=2, step=1, label="Параллельных запросов к модели")
                    llm_frames = gr.Slider(minimum=1, maximum=6, value=3, step=1, label="Кадров на канал (больше = надёжнее ловит анимированные заглушки, но дольше)")
                    llm_remove_noframe = gr.Checkbox(label="Удалять каналы, у которых не удалось получить кадр", value=False)
                    llm_sort = gr.Checkbox(label="🗂 Отсортировать: русскоязычные по темам (группы по 40–50 каналов), нерусскоязычные в отдельный файл", value=False)
                    llm_sort_only = gr.Checkbox(label="Только сортировка, без проверки потоков (быстро, для уже очищенного плейлиста)", value=False)
                    llm_best = gr.Checkbox(label="Оставлять только лучшее качество, если канал в списке повторяется (HD / FHD / SD одного и того же; только с сортировкой)", value=False)
                    llm_logos = gr.Checkbox(label="Пришить иконки каналов (tvg-logo)", value=True)
                    llm_porn = gr.Checkbox(label="Отделять 18+/порно в отдельный список (adult_18plus.m3u)", value=True)
                    llm_porn_method = gr.Radio(choices=list(PORN_METHODS),
                                               value="По названию и по кадру (VL)",
                                               label="Метод отделения 18+")
                    with gr.Row():
                        llm_epg = _epg_dropdown()
                        llm_epg_custom = gr.Textbox(label="Своя EPG ссылка", value="", placeholder="своя ссылка xml.gz")
                    llm_catchup = gr.Dropdown(label="Способ запроса архива", choices=CATCHUP_LABELS, value=CATCHUP_DEFAULT)
                    llm_catchup_src = gr.Textbox(label="Шаблон архива (только append/default; пусто = ?utc=${start})", value="")
                    llm_btn = gr.Button("🚀 Запустить", variant="primary")
                with gr.Column():
                    llm_output = gr.File(label="Результат", file_count="multiple")
                    llm_stats = gr.Textbox(label="Ход работы / Статистика", lines=18)

            llm_btn.click(
                llm_filter_function,
                inputs=[llm_files, llm_timeout, llm_ffmpeg_workers, llm_workers, llm_frames, llm_remove_noframe,
                        llm_sort, llm_sort_only, llm_epg, llm_epg_custom, llm_catchup, llm_catchup_src, llm_logos,
                        llm_porn, llm_best, llm_porn_method],
                outputs=[llm_output, llm_stats]
            )
            llm_catchup.change(_toggle_catchup_src, inputs=llm_catchup, outputs=llm_catchup_src)


if __name__ == "__main__":
    OUTPUT_DIR.mkdir(exist_ok=True)
    print(f"🚀 Запуск m3uGenius на http://127.0.0.1:{SERVER_PORT} ...")
    app.launch(share=False, server_name="127.0.0.1", server_port=SERVER_PORT)
