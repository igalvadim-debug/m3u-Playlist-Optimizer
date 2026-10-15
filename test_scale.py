#!/usr/bin/env python3
"""Масштабный тест сортировки: ~2000 каналов, размеры групп, порядок, отсутствие потерь."""
import random
from modules.organizer import Organizer
from modules.m3u_utils import split_extinf

random.seed(42)
RU_TPL = ["Первый канал", "Россия 1", "НТВ", "Матч ТВ", "Кинопоиск", "Иллюзион+", "Карусель",
          "МУЗ-ТВ", "EUROPA PLUS TV", "Радио Шансон", "Discovery Россия", "Охота и рыбалка",
          "ТНТ", "СТС", "Пятница!", "Ю", "Че", "РЕН ТВ", "Звезда", "Мир 24", "Время +4",
          "НТВ (+2)", "Пятый канал", "Домашний", "КВН ТВ", "Сериал HD", "Футбол 1"]
FO_TPL = ["Das Erste", "ZDF HD", "RTL", "1+1", "ICTV", "Qazaqstan", "Eurosport 1", "CNN International"]
GROUPS = ["Федеральные", "Спорт", "Кино", "Детям", "Музыка", "", "Общие"]

streams = []
n_ru = n_fo = 0
for i in range(2000):
    if i % 5 == 0:
        name = random.choice(FO_TPL) + f" {i}"
        fo = True
    else:
        base = random.choice(RU_TPL)
        suffix = "" if random.random() < 0.6 else f" HD {i}"
        name = (base + suffix).strip()
        fo = False
    g = random.choice(GROUPS)
    info = f'#EXTINF:-1 group-title="{g}",{name}' if g else f"#EXTINF:-1,{name}"
    streams.append({"info": info, "url": f"http://h/s{i}.ts", "extra": []})
    n_fo += fo; n_ru += not fo

org = Organizer(llm=None)
res = org.organize(streams)
assert res is not None
st = res["stats"]
print("stats:", st)

# 1. ни один канал не потерян
ru_cnt = sum(n for _, n in res["ru_groups"])
fo_cnt = sum(n for _, n in res["foreign_groups"])
assert ru_cnt + fo_cnt == len(streams), (ru_cnt, fo_cnt)
urls_out = [ln.strip() for ln in res["ru_lines"] + res["foreign_lines"] if ln.startswith("http")]
assert len(urls_out) == len(streams) and set(urls_out) == {s["url"] for s in streams}

# 2. размер групп <= 50
assert all(n <= 50 for _, n in res["ru_groups"]), [g for g, n in res["ru_groups"] if n > 50]
assert all(n <= 50 for _, n in res["foreign_groups"])

# 3. все RU-группы с флагом 🇷🇺, нерусские в foreign и без флага RU
assert all(g.startswith("🇷🇺") for g, _ in res["ru_groups"])
assert all(g.startswith("🌍") for g, _ in res["foreign_groups"])

# 4. украинские/немецкие точно не в ru_sorted
bad = [g for g, _ in res["ru_groups"]]
for ln in res["ru_lines"]:
    if ln.startswith("#EXTINF"):
        _, a, nm = split_extinf(ln)
        assert not any(ch in nm for ch in "іїєґIЇЄ"), nm

# 5. группы в файле идут тем же порядком, что список ru_groups
order_in_file = []
for ln in res["ru_lines"]:
    if ln.startswith("#EXTINF"):
        _, a, _ = split_extinf(ln)
        g = a.get("group-title")
        if not order_in_file or order_in_file[-1] != g:
            order_in_file.append(g)
assert order_in_file == [g for g, _ in res["ru_groups"]], "порядок групп в файле сбился"

print(f"OK: ru={st['ru']} ({st['ru_groups']} групп), foreign={st['foreign']} ({st['foreign_groups']} групп), shifted={st['shifted']}")
names = [g for g, _ in res["ru_groups"]]
assert len(names) == len(set(names)), "есть повторяющиеся названия групп!"
print("Названия групп уникальны.")
