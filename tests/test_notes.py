from ideabot.models import Incoming, Part
from ideabot.vault import notes
from ideabot.vault.fs import atomic_write, wikilink


def test_wikilink():
    assert wikilink("Медиабаинг/Офферы и связки.md") == "[[Медиабаинг/Офферы и связки|Офферы и связки]]"
    assert wikilink("Идея.md") == "[[Идея]]"
    assert wikilink("Обо мне.canvas") == "[[Обо мне.canvas]]"


def test_atomic_write_creates_dirs_and_leaves_no_tmp(tmp_path):
    p = tmp_path / "a" / "b.md"
    atomic_write(p, "привет")
    assert p.read_text("utf-8") == "привет"
    assert [x.name for x in p.parent.iterdir()] == ["b.md"]


def test_safe_filename_rules():
    assert notes.safe_filename("Идея: бот/сервис") == "Идея — бот-сервис"
    assert notes.safe_filename('a#b?c[d]e|f"g') == "abcdefg"
    assert len(notes.safe_filename("я" * 300)) == 100
    assert notes.safe_filename(" ... ") == "Идея"


def test_unique_note_path(tmp_path):
    (tmp_path / "X.md").write_text("")
    (tmp_path / "X (2).md").write_text("")
    assert notes.unique_note_path(tmp_path, "X").name == "X (3).md"
    assert notes.unique_note_path(tmp_path, "Y").name == "Y.md"


def test_render_source_marks_forwards_and_video():
    inc = Incoming([Part(text="мой коммент"), Part(text="чужой пост", origin="Канал"),
                    Part(origin="Канал", has_video=True), Part(origin="Канал", photo="/p.jpg")])
    src = notes.render_source(inc)
    assert src.startswith("мой коммент")
    assert "Форвард от «Канал»:\nчужой пост\n(видео не смотрел, только подпись)" in src
    assert "(фото: 1 шт.)" in src


def test_render_note_and_links_roundtrip():
    text = notes.render_note({"type": "мысль", "score": None, "idea_bot_id": 3}, "Заголовок",
                             "**Суть**: x", "строка 1\n\nстрока 2", ["[[A]]", "[[B]]"])
    assert text.startswith('---\ntype: "мысль"\nidea_bot_id: 3\n---\n# Заголовок\n')
    assert "> [!quote]- Исходник\n> строка 1\n>\n> строка 2\n" in text
    assert notes.get_links(text) == ["[[A]]", "[[B]]"]
    text2 = notes.set_links(text, ["[[C]]"])
    assert notes.get_links(text2) == ["[[C]]"]


def test_sections_add_before_links_and_remove_cleanly():
    base = "# T\n\nтело\n\n## Связанные заметки\n- [[A]]\n"
    added = notes.add_section(base, "Дополнение (2026-09-26 10:00)", "новое")
    assert added.index("## Дополнение") < added.index("## Связанные заметки")
    assert notes.remove_section(added, "Дополнение (2026-09-26 10:00)") == base


def test_moc_add_into_existing_and_new_heading(tmp_path):
    moc = tmp_path / "moc.md"
    moc.write_text("# MOC\n\n## Геймдизайн\n\n- [[Старая]]\n\n## Другое\n\n- [[Z]]\n", "utf-8")
    notes.moc_add(moc, "Геймдизайн", "[[Новая]]")
    notes.moc_add(moc, "Контент", "[[Ролик]]")
    text = moc.read_text("utf-8")
    assert "## Геймдизайн\n\n- [[Старая]]\n- [[Новая]]\n\n## Другое" in text
    assert text.endswith("## Контент\n\n- [[Ролик]]\n")
    notes.moc_remove(moc, "[[Новая]]")
    text = moc.read_text("utf-8")
    assert "[[Новая]]" not in text and "## Геймдизайн\n\n- [[Старая]]\n" in text
    notes.moc_remove(moc, "[[Ролик]]")
    assert "## Контент" not in moc.read_text("utf-8")
