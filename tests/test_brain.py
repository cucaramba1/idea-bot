import asyncio
import json
import stat

import pytest

from ideabot.brain import Brain, BrainError, clean_links, explicit_l2, run_claude, validate
from ideabot.models import Analysis, Incoming, Link, Part
from ideabot.vault.index import build_index


def fake_claude(tmp_path, out: dict, sleep: float = 0) -> str:
    """Скрипт вместо claude: пишет argv и stdin в файлы и печатает заданный JSON."""
    p = tmp_path / "fake_claude.py"
    p.write_text(
        "#!/usr/bin/env python3\nimport sys, json, time\n"
        f"open({str(tmp_path / 'argv.json')!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        f"open({str(tmp_path / 'stdin.txt')!r}, 'w').write(sys.stdin.read())\n"
        f"time.sleep({sleep})\n"
        f"print({json.dumps(json.dumps(out))})\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def test_explicit_tags(settings):
    ix = build_index(settings)
    inc = Incoming([Part(text="идея #медиабаинг и #обо_мне #нету"), Part(text="#досуг", origin="Канал")])
    assert explicit_l2(inc, ix) == (["Медиабаинг", "Обо мне"], ["нету"])  # теги из форварда не считаются


def test_clean_links(settings):
    ix = build_index(settings)
    links = clean_links([
        {"l2": "Медиабаинг", "l3": "Офферы и связки", "l3_kind": "file"},
        {"l2": "Медиабаинг", "l3": "Офферы", "l3_kind": "group"},     # дубль L2
        {"l2": "Досуг", "l3": "Фильм", "l3_kind": "file"},            # shallow: заметок нет
        {"l2": "Нет такого", "l3": None, "l3_kind": "none"},
        {"l2": "Обо мне", "l3": "Выдумка", "l3_kind": "file"},
    ], ix)
    assert links == [Link("Медиабаинг", "Офферы и связки", "file"), Link("Досуг"), Link("Обо мне")]
    assert clean_links([{"l2": "Досуг", "l3": "Кино", "l3_kind": "file"}], ix) == [Link("Досуг", "Кино", "group")]


def test_validate_fixes_everything(settings):
    ix = build_index(settings)
    raw = {"type": "мысль", "theme": "Космос", "title": "  " + "т" * 120, "body_md": "## Раздел\n# Ещё\nb",
           "score": 9, "links": [], "duplicate_of": "Нет такой"}
    a = validate(raw, ix, settings, ["Обо мне"], ["нету"])
    assert a.type == "мысль" and a.theme == "Разное" and len(a.title) == 80
    assert a.score is None and a.duplicate_of is None
    assert a.links == [Link("Обо мне")] and a.unknown_tags == ["нету"]
    assert a.body_md == "### Раздел\n### Ещё\nb"
    b = validate({"type": "бизнес", "theme": "Контент", "title": "", "body_md": "x", "score": 7,
                  "links": [], "duplicate_of": "Старая идея"}, ix, settings, [], [])
    assert b.score == 7 and b.title == "Идея без названия" and b.duplicate_of == "Старая идея"


def test_validate_new_theme_normalizes(settings):
    ix = build_index(settings)
    # имя совпадает с уже существующей темой: диалог не нужен, тема используется напрямую
    a = validate({"type": "прочее", "theme": "Контент", "title": "T", "body_md": "b", "score": None,
                  "links": [], "duplicate_of": None, "new_theme": {"name": "Геймдизайн", "closest_existing": None}},
                 ix, settings, [], [])
    assert a.theme == "Геймдизайн" and a.new_theme is None

    # новое имя, closest_existing валиден
    a = validate({"type": "прочее", "theme": "Разное", "title": "T", "body_md": "b", "score": None,
                  "links": [], "duplicate_of": None,
                  "new_theme": {"name": "Идеи для НАП", "closest_existing": "Контент"}}, ix, settings, [], [])
    assert a.new_theme == {"name": "Идеи для НАП", "closest_existing": "Контент"}

    # closest_existing не входит в список тем: обнуляется
    a = validate({"type": "прочее", "theme": "Разное", "title": "T", "body_md": "b", "score": None,
                  "links": [], "duplicate_of": None,
                  "new_theme": {"name": "Идеи для НАП", "closest_existing": "Выдумка"}}, ix, settings, [], [])
    assert a.new_theme == {"name": "Идеи для НАП", "closest_existing": None}

    # new_theme отсутствует: поведение не меняется
    a = validate({"type": "прочее", "theme": "Разное", "title": "T", "body_md": "b", "score": None,
                  "links": [], "duplicate_of": None, "new_theme": None}, ix, settings, [], [])
    assert a.new_theme is None

    # пустое имя в new_theme игнорируется как отсутствие предложения
    a = validate({"type": "прочее", "theme": "Разное", "title": "T", "body_md": "b", "score": None,
                  "links": [], "duplicate_of": None, "new_theme": {"name": "  ", "closest_existing": None}},
                 ix, settings, [], [])
    assert a.new_theme is None


def test_run_claude_structured_and_flags(tmp_path, settings):
    settings.claude_bin = fake_claude(tmp_path, {"is_error": False, "structured_output": {"ok": 1}})
    out = asyncio.run(run_claude(settings, "привет", system="S", schema={"type": "object"},
                                 tools=("Read",), add_dirs=("/tmp/m",), timeout=10))
    assert out == {"ok": 1}
    argv = json.loads((tmp_path / "argv.json").read_text())
    assert argv[argv.index("--tools") + 1] == "Read"
    assert argv[argv.index("--allowedTools") + 1] == "Read"
    assert argv[argv.index("--add-dir") + 1] == "/tmp/m"
    assert (tmp_path / "stdin.txt").read_text() == "привет"


def test_run_claude_errors(tmp_path, settings):
    settings.claude_bin = fake_claude(tmp_path, {"is_error": True, "result": "limit"})
    with pytest.raises(BrainError, match="limit"):
        asyncio.run(run_claude(settings, "x", system="S", schema={}, timeout=10))
    settings.claude_bin = fake_claude(tmp_path, {}, sleep=5)
    with pytest.raises(BrainError, match="не ответил"):
        asyncio.run(run_claude(settings, "x", system="S", timeout=1))


def test_analyze_end_to_end(tmp_path, settings):
    settings.claude_bin = fake_claude(tmp_path, {"structured_output": {
        "type": "контент", "theme": "Контент", "title": "Рилс про офферы", "body_md": "**Суть**: x",
        "score": None, "links": [{"l2": "Медиабаинг", "l3": "Офферы и связки", "l3_kind": "file"}],
        "duplicate_of": None}})
    ix = build_index(settings)
    a = asyncio.run(Brain(settings).analyze(Incoming([Part(text="снять рилс #обо_мне")]), ix))
    assert a.title == "Рилс про офферы"
    assert [l.l2 for l in a.links] == ["Медиабаинг", "Обо мне"]
    prompt = (tmp_path / "stdin.txt").read_text()
    assert "Явно указанные холсты: Обо мне" in prompt and "Старая идея" in prompt and "Запустить блог" in prompt
    argv = json.loads((tmp_path / "argv.json").read_text())
    assert argv[argv.index("--tools") + 1] == "" and "--allowedTools" not in argv


def test_pick_l3_keeps_exactly_requested(tmp_path, settings):
    settings.claude_bin = fake_claude(tmp_path, {"structured_output": {"links": [
        {"l2": "Досуг", "l3": "Кино", "l3_kind": "group"}, {"l2": "Медиабаинг", "l3": None, "l3_kind": "none"}]}})
    ix = build_index(settings)
    a = Analysis(type="мысль", theme="Разное", title="T", body_md="B")
    links = asyncio.run(Brain(settings).pick_l3(a, ["Досуг", "Обо мне"], ix))
    assert links == [Link("Досуг", "Кино", "group"), Link("Обо мне")]
