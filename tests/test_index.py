from ideabot.vault.index import build_index, render_for_prompt


def test_build_index(settings):
    ix = build_index(settings)
    assert set(ix.l2) == {"Медиабаинг", "Досуг", "Обо мне"}  # «Творческие идеи» не L2
    mb = ix.l2["Медиабаинг"]
    assert mb.moc == "Медиабаинг/Медиабаинг — карта заметок.md"  # найден как нода, в L3 не попал
    assert mb.notes == {"Офферы и связки": ("n-off", "Медиабаинг/Офферы и связки.md")}
    assert mb.groups == {"Офферы": "g-off"}
    dosug = ix.l2["Досуг"]
    assert dosug.shallow and dosug.notes == {} and dosug.groups == {"Кино": "g-kino"}
    assert dosug.moc == "Досуг/Досуг — карта заметок.md"  # найден по файлу в папке
    assert ix.l2["Обо мне"].moc is None
    assert ix.ideas == ["Старая идея"]
    assert "Запустить блог" in ix.goals


def test_tags(settings):
    ix = build_index(settings)
    assert ix.l2_by_tag("обо_мне") == "Обо мне"
    assert ix.l2_by_tag("МЕДИАБАИНГ") == "Медиабаинг"
    assert ix.l2_by_tag("нету") is None


def test_render_trims_biggest_notes_first(settings):
    ix = build_index(settings)
    full = render_for_prompt(ix, 10_000)
    assert "Офферы и связки" in full and "Досуг (только группы)" in full
    tight = render_for_prompt(ix, len(full) - 5)
    assert "Офферы и связки" not in tight and "Офферы" in tight
    only = render_for_prompt(ix, 10_000, only={"Досуг"})
    assert "Медиабаинг" not in only
