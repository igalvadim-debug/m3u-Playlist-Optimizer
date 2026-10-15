"""Импорт app.py с заглушкой gradio: проверяем функции вкладок без запущенного UI."""
import sys, types, tempfile
from pathlib import Path

class _Dummy:
    def __init__(self,*a,**k): pass
    def __getattr__(self,n): return _Dummy()
    def __call__(self,*a,**k):
        # декоратор .click(...) должен вернуть функцию-обработчик вызываемо — ок
        return _Dummy()
    def __enter__(self): return self
    def __exit__(self,*a): return False
    def __iter__(self): return iter([])

gr = types.ModuleType("gradio")
for name in ["Blocks","Tab","Row","Column","Markdown","File","Textbox","Button","Checkbox",
             "Slider","Dropdown","CheckboxGroup","State","Progress","JSON","Gallery"]:
    setattr(gr, name, _Dummy)
gr.themes = _Dummy()
sys.modules["gradio"] = gr
if "reportlab" not in sys.modules:
    try:
        import reportlab.platypus  # noqa
    except ImportError:
        rl = types.ModuleType("reportlab"); pl = types.ModuleType("reportlab.platypus")
        class SimpleDocTemplate:
            def __init__(self,*a,**k): pass
            def build(self,*a,**k): pass
        pl.SimpleDocTemplate = SimpleDocTemplate
        sys.modules["reportlab"]=rl; sys.modules["reportlab.platypus"]=pl

import app

# --- merger_load через app (files как список путей)
d = Path(tempfile.mkdtemp())
p1 = d/"a.m3u"; p2 = d/"b.m3u"
p1.write_text('#EXTM3U url-tvg="https://e/1.xml.gz"\n#EXTINF:-1 group-title="Г1",К1\nhttp://x/1.ts?tok=a\n#EXTINF:-1 group-title="Г1",К2\nhttp://x/2.ts\n', encoding="utf-8")
p2.write_text('#EXTINF:-1 group-title="Г2",К3\nhttp://x/1.ts?tok=b\n#EXTINF:-1 group-title="Г2",К4\nhttp://x/2.ts\n', encoding="utf-8")

state, groups_upd, target_upd, status = app.merger_load([str(p1), str(p2)], False)
assert "Дубликатов по адресу потока пропущено: 1" in status, status
assert "Осталось каналов: 3" in status, status
state_i, _, _, status_i = app.merger_load([str(p1), str(p2)], True)
assert "Дубликатов по адресу потока пропущено: 2" in status_i, status_i

# --- merger_save пишет файл и возвращает путь
out_file, text = app.merger_save(state, "", True)
assert out_file and Path(out_file).exists()
body = Path(out_file).read_text(encoding="utf-8")
assert body.count("#EXTM3U") == 1 and 'url-tvg="https://e/1.xml.gz"' in body.splitlines()[0], body.splitlines()[0]
assert "Каналов" not in body  # sanity: просто чтобы не пустой
assert len([l for l in body.splitlines() if l.startswith("http")]) == 3

# --- merger_delete/undo/merge через app-функции (они возвращают кортеж из 4)
st2 = state
_, groups_upd2, _, stat2 = app.merger_delete(st2, ["Г2 (1 каналов)"])
assert "Удалено групп: 1" in stat2, stat2
_, _, _, stat3 = app.merger_undo(st2)
assert "Отменено" in stat3, stat3
_, _, _, stat4 = app.merger_merge(st2, ["Г2 (1 каналов)"], "Новая группа")
assert "Объединено" in stat4, stat4
assert "Новая группа" in st2["groups"]

# --- _read_streams_for_sort (режим «Только сортировка»)
cb = lambda f, d, l=None: None
streams = app._read_streams_for_sort([str(p1), str(p2)], cb)
urls = [s["url"] for s in streams]
assert urls == ["http://x/1.ts?tok=a", "http://x/2.ts", "http://x/1.ts?tok=b"], urls

print("app stub tests passed.")
