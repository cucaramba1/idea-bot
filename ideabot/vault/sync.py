"""Writer: всё, что меняет vault. Каждое изменение холста записывается в БД, чтобы его можно было откатить."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from ..config import Settings
from ..db import DB
from ..models import Analysis, Incoming, Link
from .canvas import Canvas, add_to_group, drop_empty_side_group, ensure_side_group, ensure_theme_group
from .fs import atomic_write, wikilink
from .index import VaultIndex
from .notes import (add_section, get_links, moc_add, moc_remove, quote, remove_section, render_note,
                    render_source, set_links, unique_note_path)

RAW_BODY = "#неразобрано\n\nClaude не смог разобрать идею, исходник ниже. Нажми «🔄 Разобрать ещё раз» в боте."


class Writer:
    def __init__(self, s: Settings, db: DB):
        self.s = s
        self.db = db

    def _rel(self, p: Path) -> str:
        return p.relative_to(self.s.vault).as_posix()

    def _wikilinks(self, links: list[Link], index: VaultIndex | None) -> list[str]:
        out: list[str] = []
        for link in links:
            info = index.l2[link.l2]
            if link.l3_kind == "file":
                target = info.notes[link.l3][1]
            else:
                target = info.moc or info.canvas.name
            if (w := wikilink(target)) not in out:
                out.append(w)
        return out

    def _related(self, a: Analysis | None) -> list[str]:
        rel_dir = self._rel(self.s.ideas_dir)
        return [wikilink(f"{rel_dir}/{name}.md") for name in (a.related if a else [])
                if (self.s.ideas_dir / f"{name}.md").exists()]

    def save_new(self, idea_id: int, a: Analysis, inc: Incoming, index: VaultIndex | None) -> str:
        path = unique_note_path(self.s.ideas_dir, a.title)
        rel = self._rel(path)
        meta = {"type": a.type, "theme": a.theme, "created": date.today().isoformat(), "source": inc.source(),
                "forward_from": ", ".join(o for o, _, _ in inc.forwards()) or None, "score": a.score,
                "idea_bot_id": idea_id}
        atomic_write(path, render_note(meta, a.title, a.body_md, render_source(inc),
                                       self._wikilinks(a.links, index) + self._related(a)))
        self.db.update(idea_id, note_path=rel)  # сразу: если дальше упадём, undo найдёт что откатить
        moc_add(self.s.moc_path, a.theme, f"[[{path.stem}]]")
        cv = Canvas.load(self.s.ideas_canvas_path)
        node = add_to_group(cv, ensure_theme_group(cv, a.theme, self.s.themes), rel)
        cv.save()
        self.db.add_link(idea_id, self._rel(cv.path), node_id=node["id"], node_created=True)
        self._sync_l2(idea_id, rel, a.links, index)
        return rel

    def save_raw(self, idea_id: int, inc: Incoming) -> str:
        text = inc.own_text() or next((t for _, t, _ in inc.forwards() if t), "")
        words = " ".join(text.split()[:8])
        a = Analysis(type="прочее", theme=self.s.fallback_theme,
                     title=f"Неразобрано: {words or f'идея {idea_id}'}", body_md=RAW_BODY)
        return self.save_new(idea_id, a, inc, None)

    def _sync_l2(self, idea_id: int, note_rel: str, links: list[Link], index: VaultIndex | None) -> None:
        for link in links:
            info = index.l2[link.l2]
            cv = Canvas.load(info.canvas)
            node = cv.file_node(note_rel)
            created = node is None
            if created:
                node = add_to_group(cv, ensure_side_group(cv), note_rel)
            target = None
            if link.l3_kind == "file" and link.l3 in info.notes:
                target = info.notes[link.l3][0]
            elif link.l3_kind == "group":
                target = info.groups.get(link.l3)
            edge_id = cv.add_edge(node["id"], target)["id"] if target and cv.node(target) else None
            cv.save()
            self.db.add_link(idea_id, self._rel(info.canvas), node_id=node["id"], node_created=created, edge_id=edge_id)

    def _remove_canvas_links(self, rows: list[dict]) -> None:
        by_canvas: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            if r["canvas"]:
                by_canvas[r["canvas"]].append(r)
        for crel, rs in by_canvas.items():
            path = self.s.vault / crel
            if not path.exists():
                continue
            cv = Canvas.load(path)
            for r in rs:
                if r["edge_id"]:
                    cv.remove_edge(r["edge_id"])
                if r["node_created"] and r["node_id"]:
                    cv.remove_node(r["node_id"])
            drop_empty_side_group(cv)
            cv.save()

    def relink(self, idea_id: int, links: list[Link], index: VaultIndex) -> None:
        idea = self.db.get(idea_id)
        ideas_canvas = self._rel(self.s.ideas_canvas_path)
        rows = [r for r in self.db.links(idea_id) if r["canvas"] and r["canvas"] != ideas_canvas]
        self._remove_canvas_links(rows)
        self.db.delete_links([r["id"] for r in rows])
        path = self.s.vault / idea.note_path
        # похожие идеи остаются: «Поменять холсты» меняет только холсты
        atomic_write(path, set_links(path.read_text("utf-8"), self._wikilinks(links, index) + self._related(idea.parsed)))
        self._sync_l2(idea_id, idea.note_path, links, index)

    def append(self, idea_id: int, target_title: str, a: Analysis, inc: Incoming, index: VaultIndex) -> str:
        path = self.s.ideas_dir / f"{target_title}.md"
        rel = self._rel(path)
        heading = f"Дополнение ({datetime.now():%Y-%m-%d %H:%M})"
        body = f"{a.body_md.strip()}\n\n> [!quote]- Исходник\n{quote(render_source(inc))}"
        text = add_section(path.read_text("utf-8"), heading, body)
        old = get_links(text)
        new = [x for x in self._wikilinks(a.links, index) if x not in old]
        atomic_write(path, set_links(text, old + new))
        self.db.update(idea_id, note_path=rel, append_to=rel, append_heading=heading)
        for x in new:
            self.db.add_link(idea_id, None, wikilink=x)
        self._sync_l2(idea_id, rel, a.links, index)
        return rel

    def undo(self, idea_id: int) -> None:
        idea = self.db.get(idea_id)
        rows = self.db.links(idea_id)
        self._remove_canvas_links(rows)
        if idea.append_to:
            path = self.s.vault / idea.append_to
            if path.exists():
                text = remove_section(path.read_text("utf-8"), idea.append_heading)
                added = {r["wikilink"] for r in rows if r["wikilink"]}
                atomic_write(path, set_links(text, [x for x in get_links(text) if x not in added]))
        elif idea.note_path:
            path = self.s.vault / idea.note_path
            path.unlink(missing_ok=True)
            moc_remove(self.s.moc_path, f"[[{path.stem}]]")
        self.db.delete_links([r["id"] for r in rows])
        self.db.update(idea_id, note_path=None, append_to=None, append_heading=None)

    def add_deep(self, idea_id: int, md: str) -> None:
        idea = self.db.get(idea_id)
        path = self.s.vault / idea.note_path
        atomic_write(path, add_section(path.read_text("utf-8"), f"Копнули глубже ({date.today().isoformat()})", md))
        self.db.update(idea_id, deep=1)
