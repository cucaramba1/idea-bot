import yaml

from ideabot.config_themes import add_theme


def test_add_theme_inserts_before_fallback_and_updates_settings(settings):
    add_theme(settings, "Идеи для НАП")
    data = yaml.safe_load((settings.root / "config" / "themes.yaml").read_text("utf-8"))
    assert data["themes"][-2:] == ["Идеи для НАП", "Разное"]
    assert data["fallback"] == "Разное"
    assert settings.themes[-2:] == ["Идеи для НАП", "Разное"]


def test_add_theme_is_idempotent(settings):
    add_theme(settings, "Идеи для НАП")
    add_theme(settings, "Идеи для НАП")
    data = yaml.safe_load((settings.root / "config" / "themes.yaml").read_text("utf-8"))
    assert data["themes"].count("Идеи для НАП") == 1
    assert settings.themes.count("Идеи для НАП") == 1


def test_add_theme_survives_yaml_special_characters(settings):
    add_theme(settings, "Тема: с двоеточием")
    data = yaml.safe_load((settings.root / "config" / "themes.yaml").read_text("utf-8"))
    assert "Тема: с двоеточием" in data["themes"]
    assert "Тема: с двоеточием" in settings.themes
