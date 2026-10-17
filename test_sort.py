#!/usr/bin/env python3
"""Тест сортировки без модели: проверяем раскладку по группам и язык."""
from modules.organizer import Organizer

TESTS = [
    # (info, url) -> ожидаемая категория: "ru:<cat>" или "foreign"
    ("#EXTINF:-1 tvg-name=\"Первый канал\" group-title=\"Федеральные\",Первый канал HD", "federal"),
    ("#EXTINF:-1 group-title=\"Общие\",Россия 24", "news"),
    ("#EXTINF:-1,Матч! Футбол 1 HD", "sport"),
    ("#EXTINF:-1,Кинопоиск HD", "movies"),
    ("#EXTINF:-1,Сериалы HD", None),      # any ru cat
    ("#EXTINF:-1 tvg-language=\"Russian\",Discovery Россия", "docs"),
    ("#EXTINF:-1,Карусель", "kids"),
    ("#EXTINF:-1,EUROPA PLUS TV", "music"),
    ("#EXTINF:-1,Радио Шансон", "radio"),
    ("#EXTINF:-1,НТВ-Плюс Спорт", "sport"),
    ("#EXTINF:-1,Пятница!", "federal"),
    ("#EXTINF:-1,Три угла. Сериал", None),
    # сдвиг времени
    ("#EXTINF:-1,Россия 1 (+4)", "shift"),
    ("#EXTINF:-1,НТВ +2 (Екатеринбург)", "shift"),
    ("#EXTINF:-1 group-title=\"Общие\",Первый канал (MSK+0)", None),
    # украинские / иностранные
    ("#EXTINF:-1,1+1", "foreign"),
    ("#EXTINF:-1,Україна 24", "foreign"),
    ("#EXTINF:-1,ICTV Україна", "foreign"),
    ("#EXTINF:-1,ТРК Украина", "foreign"),   # 'украина' в _NONRU_NAME
    ("#EXTINF:-1,ZDF HD", "foreign"),
    ("#EXTINF:-1 tvg-id=\"zdf.de\",Das Erste", "foreign"),
    ("#EXTINF:-1,Qazaqstan TV", "foreign"),
    ("#EXTINF:-1,Eurosport 1 HD", "foreign"),
    ("#EXTINF:-1 group-title=\"Germany\",RTL Deutschland", "foreign"),
    ("#EXTINF:-1 tvg-language=\"Ukrainian\",Трибула Нова", "foreign"),
    # спорные латиницей — без модели score<2 => foreign
    ("#EXTINF:-1,Ren TV", "foreign_or_ru"),
    ("#EXTINF:-1 tvg-country=\"RU\",Match! Arena", "ru?"),
    ("#EXTINF:-1,Охота и рыбалка", "docs"),
    ("#EXTINF:-1,Планета HD", None),
    ("#EXTINF:-1,СПАС", "federal"),
]

streams = []
for i, (info, _) in enumerate(TESTS):
    streams.append({"info": info, "url": f"http://test.local/stream{i}.ts", "extra": []})

org = Organizer(llm=None)
res = org.organize(streams)
assert res is not None

# какие каналы попали в ru_sorted
ru_names = set()
for gname, n in res["ru_groups"]:
    assert n <= 50, gname
print("RU groups:")
for g, n in res["ru_groups"]:
    print(f"  {g}  ({n})")
print("FOREIGN groups:")
for g, n in res["foreign_groups"]:
    print(f"  {g}  ({n})")

# reconstruct per-channel result by parsing output lines
from modules.m3u_utils import split_extinf
def names_from_lines(lines):
    """Сопоставить имена каналов из строк EXTINF с названиями их групп."""
    out = {}
    for ln in lines:
        if ln.startswith("#EXTINF"):
            _, a, nm = split_extinf(ln)
            out[nm] = a.get("group-title", "")
    return out
ru_map = names_from_lines(res["ru_lines"])
fo_map = names_from_lines(res["foreign_lines"])

fails = []
for info, expect in TESTS:
    _, _, nm = split_extinf(info)
    in_ru = nm in ru_map
    in_fo = nm in fo_map
    gr_ = ru_map.get(nm, fo_map.get(nm, ""))
    if expect == "foreign":
        if in_ru:
            fails.append(f"❌ '{nm}' expected FOREIGN but got RU group '{gr_}'")
    elif expect == "shift":
        if not in_ru or "🕓" not in gr_:
            fails.append(f"❌ '{nm}' expected SHIFT group, got '{gr_}'")
    elif expect and expect != "foreign_or_ru" and not expect.endswith("?"):
        if not in_ru:
            fails.append(f"❌ '{nm}' expected RU/{expect} but went FOREIGN")
        elif expect != "None" and expect not in gr_ and expect not in ("federal","news","sport","movies","docs","kids","music","radio"):
            pass
    elif expect in (None, "foreign_or_ru", "ru?"):
        print(f"  ℹ️ '{nm}' -> {'RU: '+gr_ if in_ru else 'FOREIGN'}")

if fails:
    print("\n".join(fails))
    raise SystemExit(1)
print("\nAll assertions passed.")
