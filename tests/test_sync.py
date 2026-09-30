import json

import pytest

from ideabot.db import DB
from ideabot.models import Analysis, Incoming, Link, Part
from ideabot.vault.canvas import IDEAS_GROUP, Canvas
from ideabot.vault.index import build_index
from ideabot.vault.notes import get_links
from ideabot.vault.sync import Writer


@pytest.fixture
def env(settings, tmp_path):
    db = DB(tmp_path / "ideas.db")
    return settings, db, Writer(settings, db)


def analysis(**kw):
    base = dict(type="контент", theme="Контент", title="Рилс: офферы", body_md="**Суть**: x",
                links=[Link("Медиабаинг", "Офферы и связки", "file"), Link("Досуг", "Кино", "group"), Link("Обо мне")])
    base.update(kw)
    return Analysis(**base)


def snapshot(v):
    return {p.relative_to(v).as_posix(): p.read_text("utf-8") for p in v.rglob("*") if p.is_file()}


def test_save_new_everywhere(env):
    s, db, w = env
    inc = Incoming([Part(text="мысль"), Part(text="пост", origin="Канал")])
    i = db.create(1, inc)
    rel = w.save_new(i, analysis(), inc, build_index(s))
    assert rel == "Творческие идеи/Рилс — офферы.md"
    text = (s.vault / rel).read_text("utf-8")
    assert 'forward_from: "Канал"' in text and f"idea_bot_id: {i}" in text
    assert get_links(text) == ["[[Медиабаинг/Офферы и связки|Офферы и связки]]",
                               "[[Досуг/Досуг — карта заметок|Досуг — карта заметок]]",
                               "[[Обо мне.canvas]]"]
    assert "## Контент\n\n- [[Рилс — офферы]]" in s.moc_path.read_text("utf-8")
    ideas = Canvas.load(s.ideas_canvas_path)
    assert ideas.file_node(rel) in ideas.children(ideas.group("Контент"))
    mb = Canvas.load(s.vault / "Медиабаинг.canvas")
    node = mb.file_node(rel)
    assert node in mb.children(mb.group(IDEAS_GROUP))
    assert {"fromNode": node["id"], "toNode": "n-off"}.items() <= mb.edges[0].items()
    dosug = Canvas.load(s.vault / "Досуг.canvas")
    assert dosug.edges[0]["toNode"] == "g-kino"
    assert Canvas.load(s.vault / "Обо мне.canvas").edges == []


def test_undo_restores_vault_exactly(env):
    s, db, w = env
    before = snapshot(s.vault)
    inc = Incoming([Part(text="x")])
    i = db.create(1, inc)
    w.save_new(i, analysis(), inc, build_index(s))
    w.undo(i)
    after = snapshot(s.vault)
    # холсты сравниваем по смыслу (форматирование json может отличаться).
    # На холсте идей остаётся пустая группа темы «Контент»: это место под будущие идеи, так задумано.
    ideas_after = json.loads(after.pop("Творческие идеи.canvas"))
    ideas_before = json.loads(before.pop("Творческие идеи.canvas"))
    assert [n for n in ideas_after["nodes"] if n.get("label") != "Контент"] == ideas_before["nodes"]
    for k in before:
        if k.endswith(".canvas"):
            assert json.loads(after[k]) == json.loads(before[k]), k
        elif k.endswith("карта заметок.md"):
            assert after[k].strip() == before[k].strip(), k
        else:
            assert after[k] == before[k], k
    assert set(after) == set(before)
    assert db.links(i) == []


def test_save_raw_goes_to_fallback(env):
    s, db, w = env
    inc = Incoming([Part(text="один два три четыре пять шесть семь восемь девять")])
    i = db.create(1, inc)
    rel = w.save_raw(i, inc)
    assert rel == "Творческие идеи/Неразобрано — один два три четыре пять шесть семь восемь.md"
    assert "#неразобрано" in (s.vault / rel).read_text("utf-8")
    cv = Canvas.load(s.ideas_canvas_path)
    assert cv.file_node(rel) in cv.children(cv.group("Разное"))


def test_relink_moves_l2_only(env):
    s, db, w = env
    inc = Incoming([Part(text="x")])
    i = db.create(1, inc)
    ix = build_index(s)
    rel = w.save_new(i, analysis(), inc, ix)
    db.update(i, note_path=rel)
    w.relink(i, [Link("Обо мне")], ix)
    assert Canvas.load(s.vault / "Медиабаинг.canvas").file_node(rel) is None
    assert Canvas.load(s.vault / "Медиабаинг.canvas").group(IDEAS_GROUP) is None
    assert Canvas.load(s.vault / "Обо мне.canvas").file_node(rel) is not None
    assert Canvas.load(s.ideas_canvas_path).file_node(rel) is not None
    assert get_links((s.vault / rel).read_text("utf-8")) == ["[[Обо мне.canvas]]"]


def test_append_and_undo_append(env):
    s, db, w = env
    target = s.ideas_dir / "Старая идея.md"
    before = target.read_text("utf-8")
    inc = Incoming([Part(text="ещё мысль")])
    i = db.create(1, inc)
    ix = build_index(s)
    rel = w.append(i, "Старая идея", analysis(links=[Link("Медиабаинг")]), inc, ix)
    text = target.read_text("utf-8")
    assert rel == "Творческие идеи/Старая идея.md" and "## Дополнение (" in text and "ещё мысль" in text
    assert get_links(text) == ["[[Фильм]]", "[[Медиабаинг/Медиабаинг — карта заметок|Медиабаинг — карта заметок]]"]
    assert Canvas.load(s.vault / "Медиабаинг.canvas").file_node(rel) is not None
    w.undo(i)
    assert target.read_text("utf-8") == before
    assert Canvas.load(s.vault / "Медиабаинг.canvas").file_node(rel) is None


def test_add_deep(env):
    s, db, w = env
    inc = Incoming([Part(text="x")])
    i = db.create(1, inc)
    rel = w.save_new(i, analysis(links=[]), inc, build_index(s))
    db.update(i, note_path=rel)
    w.add_deep(i, "- нашёл вот это")
    text = (s.vault / rel).read_text("utf-8")
    assert text.index("## Копнули глубже (") < text.index("## Связанные заметки")
    assert db.get(i).deep == 1
