"""Всё общение с Claude: промпты, вызов `claude -p`, проверка ответа."""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

from .config import Settings
from .models import TYPES, Analysis, Incoming, Link
from .vault.index import VaultIndex, render_for_prompt
from .vault.notes import render_source


class BrainError(RuntimeError):
    pass


SYSTEM = """Ты разбираешь идеи Некита для его Obsidian. Отвечай по-русски, строго по JSON-схеме.

type: бизнес (продукт, заработок, проект ради денег), контент (ролик, пост, сценарий или его кусок), мысль (наблюдение, заметка, инсайт), прочее (всё остальное, в том числе шуточное).

Тон: для бизнеса честный и прямой, без комплиментов, слабые места называй прямо. Для остальных типов поддерживающий.

body_md по типу (markdown, жирные подписи полей):
- бизнес: **Суть**, **Потенциал** (оценка 1-10 и почему), **Затраты и сложность**, **Деньги** (как и сколько можно заработать, допущения помечай), **Риски и слабые места**, **Совпадение с целями** (только если цели даны), **Первый шаг**.
- контент: **Суть**, **Формат**, **Площадка**, **Хук**, **Как усилить**, **Первый шаг**.
- мысль: **Суть**, **Развитие**, **С чем связано** (из холстов и заметок ниже).
- прочее: **Суть** и короткий комментарий, без оценок.

Правила:
- Форвард это чужой пост, источник. Мысль Некита только его собственный текст. Не приписывай ему содержимое форварда.
- Не выдумывай факты и цифры. Прикидки помечай словами «оценка» или «допущение».
- Не используй длинное тире, пиши через запятую, двоеточие или дефис.
- title: короткий содержательный заголовок до 80 символов.
- theme: строго одна из перечисленных тем.
- links: 1-3 холста L2 по смыслу, явно указанные холсты включи обязательно. l3: точное название заметки или группы этого холста, если есть близкая по смыслу; l3_kind: file для заметки, group для группы, none если подходящей нет.
- duplicate_of: точное название уже существующей идеи, если новая по сути о том же, иначе null.
- score: целое 1-10 только для бизнеса, иначе null.
- new_theme: заполняй только если текст явно и прямо просит завести новую тему или подхолст («создай тему», «заведи подхолст», «новая категория» и подобное). Никогда не заполняй её из-за того, что идея просто не вписывается ни в одну существующую тему, для этого есть фоллбек. Если заполняешь: name короткое название в стиле существующих тем, closest_existing самая близкая по смыслу существующая тема, если такая прослеживается, иначе null. Если не заполняешь, верни new_theme: null.
"""

PICK_SYSTEM = """Некит сам выбрал холсты для своей идеи. Для каждого холста подбери самую близкую по смыслу заметку (l3_kind file) или группу (l3_kind group) из списка этого холста. Если близкой нет, l3 = null, l3_kind = none. Отвечай строго по JSON-схеме."""

DEEP_SYSTEM = """Ты помогаешь Некиту развить идею. Поищи в вебе: похожие решения и конкурентов, цифры рынка или спроса, полезные ссылки, подводные камни. Ответ: markdown без заголовка верхнего уровня, короткие пункты, в конце список источников ссылками. Не выдумывай: если чего-то не нашёл, так и напиши. Не используй длинное тире."""

_LINK_ITEM = {
    "type": "object",
    "properties": {
        "l2": {"type": "string"},
        "l3": {"type": ["string", "null"]},
        "l3_kind": {"type": "string", "enum": ["file", "group", "none"]},
    },
    "required": ["l2", "l3", "l3_kind"],
}
_NEW_THEME = {
    "type": ["object", "null"],
    "properties": {
        "name": {"type": "string"},
        "closest_existing": {"type": ["string", "null"]},
    },
    "required": ["name", "closest_existing"],
}
ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": list(TYPES)},
        "theme": {"type": "string"},
        "title": {"type": "string"},
        "body_md": {"type": "string"},
        "score": {"type": ["integer", "null"]},
        "links": {"type": "array", "items": _LINK_ITEM},
        "duplicate_of": {"type": ["string", "null"]},
        "new_theme": _NEW_THEME,
    },
    "required": ["type", "theme", "title", "body_md", "score", "links", "duplicate_of", "new_theme"],
}
LINKS_SCHEMA = {"type": "object", "properties": {"links": {"type": "array", "items": _LINK_ITEM}},
                "required": ["links"]}


async def run_claude(s: Settings, prompt: str, *, system: str, schema: dict | None = None,
                     tools: tuple[str, ...] = (), add_dirs: tuple[str, ...] = (), timeout: int) -> dict | str:
    args = [s.claude_bin, "-p", "--output-format", "json", "--no-session-persistence",
            "--system-prompt", system, "--tools", ",".join(tools)]
    if tools:
        args += ["--allowedTools", ",".join(tools)]  # без этого в -p тул упрётся в запрос разрешения
    for d in add_dirs:
        args += ["--add-dir", d]
    if schema is not None:
        args += ["--json-schema", json.dumps(schema, ensure_ascii=False)]
    s.data_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PATH": f"{os.path.dirname(s.claude_bin)}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"}
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(s.data_dir), env=env)
    except OSError as e:  # бинарь не найден, нет прав на запуск и т.п. (частый случай под launchd)
        raise BrainError(f"не смог запустить claude: {e}")
    try:
        out, err = await asyncio.wait_for(proc.communicate(prompt.encode()), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise BrainError(f"claude не ответил за {timeout} с")
    if proc.returncode != 0:
        raise BrainError(f"claude exit {proc.returncode}: {err.decode(errors='ignore')[-300:]}")
    try:
        data = json.loads(out)
    except ValueError:
        raise BrainError("claude вернул не JSON")
    if data.get("is_error"):
        raise BrainError(f"claude: {str(data.get('result'))[:300]}")
    if schema is not None:
        so = data.get("structured_output")
        if not isinstance(so, dict):
            raise BrainError("claude не вернул structured_output")
        return so
    return str(data.get("result") or "")


def explicit_l2(inc: Incoming, index: VaultIndex) -> tuple[list[str], list[str]]:
    found, unknown = [], []
    for tag in re.findall(r"#([\w-]+)", inc.own_text()):
        name = index.l2_by_tag(tag)
        if name and name not in found:
            found.append(name)
        elif not name and tag not in unknown:
            unknown.append(tag)
    return found, unknown


def clean_links(items: list[dict], index: VaultIndex) -> list[Link]:
    out, seen = [], set()
    for it in items:
        l2 = it.get("l2")
        info = index.l2.get(l2)
        if info is None or l2 in seen:
            continue
        seen.add(l2)
        l3 = it.get("l3")
        if l3 in info.notes:
            out.append(Link(l2, l3, "file"))
        elif l3 in info.groups:
            out.append(Link(l2, l3, "group"))
        else:
            out.append(Link(l2))
    return out


def demote(md: str) -> str:
    """Заголовки # и ## из ответа Claude -> ###: иначе они сломают секции заметки."""
    return re.sub(r"(?m)^#{1,2} ", "### ", md)


def validate(raw: dict, index: VaultIndex, s: Settings, explicit: list[str], unknown: list[str]) -> Analysis:
    t = raw.get("type") if raw.get("type") in TYPES else "прочее"
    theme = raw.get("theme") if raw.get("theme") in s.themes else s.fallback_theme
    title = (raw.get("title") or "").strip()[:80].strip() or "Идея без названия"
    score = raw.get("score")
    score = score if t == "бизнес" and isinstance(score, int) and 1 <= score <= 10 else None
    links = clean_links(raw.get("links") or [], index)
    for name in explicit:
        if name not in {l.l2 for l in links}:
            links.append(Link(name))
    dup = raw.get("duplicate_of")
    new_theme = None
    nt = raw.get("new_theme")
    if isinstance(nt, dict):
        name = (nt.get("name") or "").strip()
        if name and name in s.themes:
            theme = name  # уже есть такая тема, диалог не нужен
        elif name:
            closest = nt.get("closest_existing")
            new_theme = {"name": name, "closest_existing": closest if closest in s.themes else None}
    return Analysis(type=t, theme=theme, title=title, body_md=demote((raw.get("body_md") or "").strip()), score=score,
                    links=links, duplicate_of=dup if dup in index.ideas else None, unknown_tags=unknown,
                    new_theme=new_theme)


class Brain:
    def __init__(self, s: Settings):
        self.s = s

    async def analyze(self, inc: Incoming, index: VaultIndex) -> Analysis:
        explicit, unknown = explicit_l2(inc, index)
        photos = inc.photos()
        parts = [
            "Темы: " + ", ".join(self.s.themes),
            "Явно указанные холсты: " + (", ".join(explicit) or "нет"),
            "", render_for_prompt(index, self.s.max_index_chars),
            "", "## Уже существующие идеи", "\n".join(index.ideas) or "(нет)",
        ]
        if index.goals:
            parts += ["", "## Цели Некита", index.goals[:4000]]
        parts += ["", "## Идея", render_source(inc)]
        if photos:
            parts += ["", "Фото к идее, посмотри их тулом Read:", *photos]
        raw = await run_claude(
            self.s, "\n".join(parts), system=SYSTEM, schema=ANALYSIS_SCHEMA,
            tools=("Read",) if photos else (), add_dirs=tuple(sorted({str(Path(p).parent) for p in photos})),
            timeout=self.s.claude_timeout)
        return validate(raw, index, self.s, explicit, unknown)

    async def pick_l3(self, a: Analysis, l2_names: list[str], index: VaultIndex) -> list[Link]:
        prompt = "\n".join([f"# {a.title}", "", a.body_md, "",
                            render_for_prompt(index, self.s.max_index_chars, only=set(l2_names))])
        raw = await run_claude(self.s, prompt, system=PICK_SYSTEM, schema=LINKS_SCHEMA,
                               timeout=self.s.claude_timeout)
        got = {l.l2: l for l in clean_links(raw.get("links") or [], index)}
        return [got.get(n, Link(n)) for n in l2_names if n in index.l2]

    async def deep_dive(self, a: Analysis) -> str:
        md = await run_claude(self.s, f"# {a.title}\n\n{a.body_md}", system=DEEP_SYSTEM,
                              tools=("WebSearch", "WebFetch"), timeout=self.s.deep_timeout)
        if not md.strip():
            raise BrainError("claude вернул пустой ответ")
        return demote(md.strip())
