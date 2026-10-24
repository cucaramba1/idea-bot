"""Заметка идеи: имя файла, рендер, секции, MOC."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..models import Incoming
from .fs import atomic_write

LINKS_H = "## Связанные заметки"
_BAD = re.compile(r'[\\*?"<>|#^\[\]]')


def safe_filename(title: str) -> str:
    name = title.replace(":", " —").replace("/", "-")
    name = _BAD.sub("", name)
    name = re.sub(r"\s+", " ", name).strip().strip(".").strip()
    return name[:100].rstrip() or "Идея"


def unique_note_path(folder: Path, title: str) -> Path:
    base = safe_filename(title)
    path, n = folder / f"{base}.md", 2
    while path.exists():
        path, n = folder / f"{base} ({n}).md", n + 1
    return path


def quote(text: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())


def render_source(inc: Incoming) -> str:
    blocks = []
    if own := inc.own_text():
        blocks.append(own)
    for origin, text, video in inc.forwards():
        body = text or "(без текста)"
        if video:
            body += "\n(видео не смотрел, только подпись)"
        blocks.append(f"Форвард от «{origin}»:\n{body}")
    if photos := inc.photos():
        blocks.append(f"(фото: {len(photos)} шт.)")
    return "\n\n".join(blocks) or "(пусто)"


def _yaml(v) -> str:
    return str(v) if isinstance(v, (int, float)) else json.dumps(str(v), ensure_ascii=False)


def _links_block(links: list[str]) -> str:
    return "\n".join(f"- {x}" for x in links)


def render_note(meta: dict, title: str, body_md: str, source_text: str, links: list[str]) -> str:
    fm = "---\n" + "".join(f"{k}: {_yaml(v)}\n" for k, v in meta.items() if v is not None) + "---\n"
    return (f"{fm}# {title}\n\n{body_md.strip()}\n\n> [!quote]- Исходник\n{quote(source_text)}\n\n"
            f"{LINKS_H}\n{_links_block(links)}\n")


def _heading_pos(text: str, heading_line: str, last: bool = False) -> int:
    """Начало строки-заголовка целиком: «### …» из разбора и «> ## …» из цитаты не считаются."""
    found = [m.start() for m in re.finditer(rf"(?m)^{re.escape(heading_line)}[ \t]*$", text)]
    return (found[-1] if last else found[0]) if found else -1


def _links_pos(text: str) -> int:
    return _heading_pos(text, LINKS_H, last=True)


def get_links(text: str) -> list[str]:
    i = _links_pos(text)
    if i == -1:
        return []
    out = []
    for line in text[i + len(LINKS_H):].splitlines():
        if line.startswith("## "):
            break
        if line.startswith("- "):
            out.append(line[2:].strip())
    return out


def set_links(text: str, links: list[str]) -> str:
    i = _links_pos(text)
    if i == -1:
        return text.rstrip("\n") + f"\n\n{LINKS_H}\n{_links_block(links)}\n"
    j = text.find("\n## ", i + len(LINKS_H))
    tail = text[j:] if j != -1 else ""
    return text[:i] + f"{LINKS_H}\n{_links_block(links)}\n" + tail


def add_section(text: str, heading: str, body: str) -> str:
    """Секция встаёт перед «Связанными заметками», чтобы они всегда были в конце."""
    block = f"## {heading}\n\n{body.strip()}\n\n"
    i = _links_pos(text)
    if i == -1:
        return text.rstrip("\n") + "\n\n" + block
    return text[:i] + block + text[i:]


def remove_section(text: str, heading: str) -> str:
    h = f"## {heading}\n"
    i = _heading_pos(text, h[:-1])
    if i == -1:
        return text
    j = text.find("\n## ", i + len(h))
    return text[:i] + (text[j + 1:] if j != -1 else "")


def moc_add(moc: Path, theme: str, item: str) -> None:
    text = moc.read_text("utf-8") if moc.exists() else f"# {moc.stem}\n"
    lines = text.splitlines()
    head = f"## {theme}"
    hi = next((i for i, line in enumerate(lines) if line.strip() == head), None)
    if hi is None:
        atomic_write(moc, text.rstrip("\n") + f"\n\n{head}\n\n- {item}\n")
        return
    end = hi + 1
    while end < len(lines) and not lines[end].startswith("## "):
        end += 1
    last = end
    while last > hi + 1 and not lines[last - 1].strip():
        last -= 1
    lines.insert(last, f"- {item}")
    atomic_write(moc, "\n".join(lines) + "\n")


def moc_remove(moc: Path, item: str) -> None:
    """Убирает пункт. Если в его разделе больше ничего нет, убирает и заголовок раздела."""
    if not moc.exists():
        return
    out = []
    for sec in re.split(r"(?m)^(?=## )", moc.read_text("utf-8")):
        lines = sec.splitlines(keepends=True)
        kept = [x for x in lines if x.strip() != f"- {item}"]
        if len(kept) != len(lines) and sec.startswith("## ") and not any(x.strip() for x in kept[1:]):
            continue
        out.append("".join(kept))
    atomic_write(moc, "".join(out).rstrip("\n") + "\n")
