#!/usr/bin/env python3
"""
M3U Merger Module
Склейка плейлистов с отсевом одинаковых адресов потоков и правка групп в одном окне.
Группы берутся прямо из group-title плейлистов (playlist.md больше не нужен).
Состояние (state) — обычный dict, его хранит gr.State в интерфейсе.
"""
from pathlib import Path

from modules.header import split_epg
from modules.m3u_utils import parse_m3u, url_key, entry_to_lines

NO_GROUP = "Без группы"
HISTORY_LIMIT = 20


class M3UMerger:
    # ------------------------------------------------------------------
    # Загрузка и склейка
    # ------------------------------------------------------------------
    @staticmethod
    def new_state():
        return {"groups": {}, "epg": [], "history": [], "files": []}

    def load(self, paths, ignore_query=False, log=None):
        """
        Склеивает плейлисты по порядку. Если адрес потока уже встречался,
        вторая и последующие копии пропускаются. EPG берётся из первого файла, где она есть.
        Возвращает (state, stats).
        """
        state = self.new_state()
        seen = set()
        stats = {"files": 0, "total": 0, "duplicates": 0, "kept": 0, "errors": []}

        for path in paths:
            name = Path(path).name
            try:
                epg, entries = parse_m3u(path)
            except Exception as e:
                stats["errors"].append(f"{name}: {e}")
                continue
            stats["files"] += 1
            state["files"].append(name)
            if log:
                log(f"📂 {name}: каналов {len(entries)}")
            if epg and not state["epg"]:
                state["epg"] = epg[:1]  # только первая ссылка первого плейлиста, где она есть

            for e in entries:
                stats["total"] += 1
                key = url_key(e["url"], ignore_query)
                if key in seen:
                    stats["duplicates"] += 1
                    continue
                seen.add(key)
                group = e["group"] or NO_GROUP
                state["groups"].setdefault(group, []).append(e)
                stats["kept"] += 1
        return state, stats

    # ------------------------------------------------------------------
    # Группы
    # ------------------------------------------------------------------
    @staticmethod
    def labels(state):
        """Подписи для чекбоксов: 'Группа (N каналов)'."""
        return [f"{g} ({len(v)} каналов)" for g, v in state["groups"].items()]

    @staticmethod
    def names_from_labels(labels):
        return [l.rsplit(" (", 1)[0] for l in (labels or [])]

    @staticmethod
    def total(state):
        return sum(len(v) for v in state["groups"].values())

    @staticmethod
    def _push_history(state):
        state["history"].append({g: list(v) for g, v in state["groups"].items()})
        if len(state["history"]) > HISTORY_LIMIT:
            state["history"].pop(0)

    def delete_groups(self, state, names):
        names = [n for n in names if n in state["groups"]]
        if not names:
            return 0
        self._push_history(state)
        removed = 0
        for n in names:
            removed += len(state["groups"].pop(n))
        return removed

    def merge_groups(self, state, target, sources):
        """Переносит каналы из sources в target (target может быть новым именем)."""
        target = (target or "").strip()
        sources = [s for s in sources if s in state["groups"] and s != target]
        if not target or not sources:
            return 0
        self._push_history(state)
        moved = 0
        dest = state["groups"].setdefault(target, [])
        for s in sources:
            items = state["groups"].pop(s)
            dest.extend(items)
            moved += len(items)
        return moved

    def undo(self, state):
        if not state["history"]:
            return False
        state["groups"] = state["history"].pop()
        return True

    # ------------------------------------------------------------------
    # Сохранение
    # ------------------------------------------------------------------
    def write_lines(self, state):
        lines = ["#EXTM3U\n"]
        for group, entries in state["groups"].items():
            for e in entries:
                lines.extend(entry_to_lines(e, group))
        return lines

    @staticmethod
    def pick_epg(state, epg_field=""):
        """Одна ссылка EPG: из поля, если заполнено, иначе из первого плейлиста."""
        urls = split_epg(epg_field)
        if urls:
            return urls[0]
        return state["epg"][0] if state["epg"] else ""
