"""Что Claude знает о vault: холсты L2, их группы и заметки L3, уже записанные идеи, цели."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings
from .canvas import IDEAS_GROUP

log = logging.getLogger(__name__)


@dataclass
class L2Info:
    name: str
    canvas: Path
    moc: str | None
    notes: dict[str, tuple[str, str]]
    groups: dict[str, str]
    shallow: bool


@dataclass
class VaultIndex:
    l2: dict[str, L2Info]
    ideas: list[str]
    goals: str

    def l2_by_tag(self, tag: str) -> str | None:
        norm = tag.replace("_", " ").casefold()
        return next((name for name in self.l2 if name.casefold() == norm), None)


def build_index(s: Settings) -> VaultIndex:
    v = s.vault
    l2: dict[str, L2Info] = {}
    for p in sorted(v.glob("*.canvas")):
        if p.stem == s.ideas_canvas:
            continue
        try:
            data = json.loads(p.read_text("utf-8"))
        except (OSError, ValueError) as e:
            log.warning("не прочитал %s: %s", p.name, e)
            continue
        moc_name = f"{p.stem} — карта заметок"
        moc, notes, groups = None, {}, {}
        for n in data.get("nodes", []):
            if n.get("type") == "group" and n.get("label") and n["label"] != IDEAS_GROUP:
                groups[n["label"]] = n["id"]
            elif n.get("type") == "file" and str(n.get("file", "")).endswith(".md"):
                rel = n["file"]
                stem = Path(rel).stem
                if stem == moc_name:
                    moc = rel
                elif not rel.startswith(s.ideas_canvas + "/"):  # ноды-синки идей это не L3
                    notes.setdefault(stem, (n["id"], rel))
        if moc is None:
            moc = next((c for c in (f"{p.stem}/{moc_name}.md", f"{moc_name}.md") if (v / c).exists()), None)
        shallow = p.stem in s.shallow_canvases
        l2[p.stem] = L2Info(p.stem, p, moc, {} if shallow else notes, groups, shallow)
    ideas = []
    if s.ideas_dir.exists():
        ideas = sorted(f.stem for f in s.ideas_dir.glob("*.md") if f.name != s.moc_path.name)
    goals_p = v / s.goals_note
    goals = goals_p.read_text("utf-8") if goals_p.exists() else ""
    return VaultIndex(l2, ideas, goals)


def _render(index: VaultIndex, drop: set[str], only: set[str] | None) -> str:
    lines = ["## Холсты L2"]
    for info in index.l2.values():
        if only is not None and info.name not in only:
            continue
        lines.append(f"### {info.name}" + (" (только группы)" if info.shallow or info.name in drop else ""))
        if info.groups:
            lines.append("Группы: " + "; ".join(info.groups))
        if info.notes and info.name not in drop:
            lines.append("Заметки: " + "; ".join(info.notes))
    return "\n".join(lines)


def render_for_prompt(index: VaultIndex, max_chars: int, only: set[str] | None = None) -> str:
    """Не влезает в лимит: у самых больших холстов оставляем только группы."""
    drop: set[str] = set()
    text = _render(index, drop, only)
    for info in sorted(index.l2.values(), key=lambda i: len(i.notes), reverse=True):
        if len(text) <= max_chars or not info.notes:
            break
        drop.add(info.name)
        text = _render(index, drop, only)
    return text
