#!/usr/bin/env python3
"""Интеграционный тест вкладки Merger (без gradio): загрузка, дубликаты, группы, undo, сохранение."""
import tempfile, os
from pathlib import Path
from modules.merger import M3UMerger
from modules.header import apply_header

PL1 = """#EXTM3U url-tvg="https://epg.one/a.xml.gz,https://epg.two/b.xml.gz"
#EXTINF:-1 tvg-id="a.ru" group-title="Федеральные",Первый канал
http://host1/streamA.ts?token=111
#EXTINF:-1 group-title="Федеральные",Россия 1
http://host1/streamB.ts
#EXTINF:-1,Без группы канал
http://host1/streamC.ts
"""
PL2 = """#EXTM3U url-tvg="https://epg.three/c.xml.gz"
#EXTINF:-1 group-title="Спорт",Матч ТВ
http://host1/streamA.ts?token=222
#EXTINF:-1 group-title="Спорт",Матч Игра
http://host1/streamD.ts
#EXTINF:-1 group-title="Федеральные",НТВ
http://host1/streamB.ts
"""

d = tempfile.mkdtemp()
p1 = Path(d) / "pl1.m3u"; p1.write_text(PL1, encoding="utf-8")
p2 = Path(d) / "pl2.m3u"; p2.write_text(PL2, encoding="utf-8")
m = M3UMerger()

# --- строгие ключи: ?token разный -> streamA не дубль, streamB дубль
state, stats = m.load([str(p1), str(p2)], ignore_query=False)
assert stats["total"] == 6 and stats["duplicates"] == 1 and stats["kept"] == 5, stats
assert state["epg"] == ["https://epg.one/a.xml.gz"], state["epg"]

# --- ignore_query=True: streamA?token тоже дубль
state2, stats2 = m.load([str(p1), str(p2)], ignore_query=True)
assert stats2["duplicates"] == 2 and stats2["kept"] == 4, stats2

# --- удаление + undo
names = list(state["groups"])
removed = m.delete_groups(state, ["Спорт"])
assert removed == 2 and "Спорт" not in state["groups"]
assert m.undo(state) and "Спорт" in state["groups"] and m.total(state) == 5

# --- объединение в новую группу с названием
moved = m.merge_groups(state, "🇷🇺 Всё лучшее", ["Федеральные", "Спорт"])
assert moved == 4 and "Федеральные" not in state["groups"]
assert len(state["groups"]["🇷🇺 Всё лучшее"]) == 4
# отмена объединения
assert m.undo(state) and "Федеральные" in state["groups"]

# --- merge целевая группа среди источников (не дублирует каналы)
before = M3UMerger.total(state)
moved = m.merge_groups(state, "Федеральные", ["Федеральные", "Спорт"])
assert moved == 2 and len(state["groups"]["Федеральные"]) == 4
assert M3UMerger.total(state) == before, "каналы не должны теряться/дублироваться при merge"
urls = [x["url"] for g in state["groups"].values() for x in g]
assert len(urls) == len(set(urls)), "при merge появились дубликаты каналов"

# --- EPG: из поля если заполнено, иначе первая из первого плейлиста; в шапке одна ссылка
lines = m.write_lines(state)
epg_field = "https://my.epg/x.xml.gz, https://my.epg/y.xml.gz"
out = apply_header(lines, m.pick_epg(state, epg_field), True, single=True)
assert out[0].startswith("#EXTM3U"), out[0]
assert 'url-tvg="https://my.epg/x.xml.gz"' in out[0] and "y.xml.gz" not in out[0], out[0]
assert sum(1 for ln in out if ln.upper().startswith("#EXTM3U")) == 1
out2 = apply_header(lines, m.pick_epg(state, ""), True, single=True)
assert 'url-tvg="https://epg.one/a.xml.gz"' in out2[0], out2[0]

# --- группы читаются из файлов, EXTGRP тоже
assert set(state["groups"]) >= {"Федеральные", "Без группы"}, state["groups"].keys()

print("Merger integration test passed.")
