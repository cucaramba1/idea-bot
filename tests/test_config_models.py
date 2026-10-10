from ideabot.models import Analysis, Incoming, Link, Part


def test_settings_loaded(settings, vault):
    assert settings.vault == vault
    assert settings.owner_id == 42
    assert settings.themes[0] == "Бизнес-идеи / заработок"
    assert settings.fallback_theme == "Разное"
    assert settings.shallow_canvases == {"Досуг", "Фитнес и здоровье"}
    assert settings.ideas_canvas_path == vault / "Творческие идеи.canvas"
    assert settings.moc_path == vault / "Творческие идеи" / "Творческие идеи — карта заметок.md"
    assert settings.db_path.name == "ideas.db"


def test_incoming_roundtrip_and_views():
    inc = Incoming([
        Part(text="моя мысль"),
        Part(text="пост 1", origin="Канал X"),
        Part(text="", origin="Канал X", photo="/tmp/p.jpg"),
        Part(text="", origin="Канал X", has_video=True),
        Part(text="голосом", was_voice=True),
    ])
    back = Incoming.from_json(inc.to_json())
    assert back == inc
    assert back.own_text() == "моя мысль\n\nголосом"
    assert back.forwards() == [("Канал X", "пост 1", True)]
    assert back.photos() == ["/tmp/p.jpg"]
    assert back.source() == "форвард"
    assert Incoming([Part(text="a", was_voice=True)]).source() == "голос"
    assert Incoming([Part(text="a")]).source() == "текст"
    assert Incoming([Part(text="  ")]).is_empty()
    assert not Incoming([Part(voice="/tmp/v.ogg")]).is_empty()


def test_analysis_roundtrip():
    a = Analysis(type="бизнес", theme="Контент", title="T", body_md="B", score=7,
                 links=[Link("Медиабаинг", "Офферы", "group")], duplicate_of=None, unknown_tags=["xx"])
    assert Analysis.from_dict(a.to_dict()) == a


def test_analysis_roundtrip_with_new_theme():
    a = Analysis(type="бизнес", theme="Разное", title="T", body_md="B",
                 new_theme={"name": "Идеи для НАП", "closest_existing": "Нячанг"})
    assert Analysis.from_dict(a.to_dict()) == a
    assert Analysis(type="мысль", theme="Разное", title="T", body_md="B").new_theme is None
