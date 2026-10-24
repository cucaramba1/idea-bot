import asyncio
from pathlib import Path

import pytest

from ideabot.brain import BrainError
from ideabot.db import DB
from ideabot.models import Analysis, Incoming, Link, Part
from ideabot.pipeline import Pipeline
from ideabot.vault.canvas import Canvas
from ideabot.vault.sync import Writer


class FakeBrain:
    def __init__(self, result=None, fail=False):
        self.result, self.fail, self.calls = result, fail, 0

    async def analyze(self, inc, index):
        self.calls += 1
        if self.fail:
            raise BrainError("лимиты")
        return self.result

    async def pick_l3(self, a, names, index):
        return [Link(n) for n in names]

    async def deep_dive(self, a):
        return "- находка"


class FakeNotifier:
    def __init__(self):
        self.events = []

    async def refresh(self, idea_id):
        self.events.append(("refresh", idea_id))

    async def busy(self, idea_id, text):
        self.events.append(("busy", idea_id))

    async def error(self, idea_id, text):
        self.events.append(("error", text))


def make(settings, tmp_path, brain, stt=None):
    db = DB(tmp_path / "ideas.db")

    async def default_stt(path):
        return "расшифровка"

    return db, Pipeline(settings, db, Writer(settings, db), brain, stt or default_stt, FakeNotifier())


def A(**kw):
    base = dict(type="мысль", theme="Мысли и заметки", title="Мысль", body_md="b", links=[Link("Обо мне")])
    base.update(kw)
    return Analysis(**base)


def test_happy_path_with_voice(settings, tmp_path):
    db, p = make(settings, tmp_path, FakeBrain(A()))
    i = db.create(1, Incoming([Part(voice=str(tmp_path / "v.ogg"))]))
    asyncio.run(p.process(i))
    idea = db.get(i)
    assert idea.status == "saved" and idea.mode == "new"
    assert idea.incoming.own_text() == "расшифровка" and idea.incoming.source() == "голос"
    assert (settings.vault / idea.note_path).exists()
    assert p.notifier.events[-1] == ("refresh", i)


def test_stt_failure_keeps_idea(settings, tmp_path):
    async def bad(path):
        raise RuntimeError("whisper")

    db, p = make(settings, tmp_path, FakeBrain(A()), stt=bad)
    i = db.create(1, Incoming([Part(voice="/v.ogg")]))
    asyncio.run(p.process(i))
    assert db.get(i).status == "stt_failed" and db.get(i).note_path is None


def test_brain_failure_saves_raw_then_retry_replaces(settings, tmp_path):
    brain = FakeBrain(A(), fail=True)
    db, p = make(settings, tmp_path, brain)
    i = db.create(1, Incoming([Part(text="сырая мысль")]))
    asyncio.run(p.process(i))
    raw_path = db.get(i).note_path
    assert db.get(i).status == "raw" and "лимиты" in db.get(i).error
    assert (settings.vault / raw_path).exists()
    brain.fail = False
    asyncio.run(p.process(i))
    idea = db.get(i)
    assert idea.status == "saved" and not (settings.vault / raw_path).exists()
    assert (settings.vault / idea.note_path).exists()
    cv = Canvas.load(settings.ideas_canvas_path)
    assert cv.file_node(raw_path) is None and cv.file_node(idea.note_path) is not None


def test_duplicate_asks_then_appends(settings, tmp_path):
    db, p = make(settings, tmp_path, FakeBrain(A(duplicate_of="Старая идея")))
    i = db.create(1, Incoming([Part(text="про то же")]))
    asyncio.run(p.process(i))
    assert db.get(i).status == "await_dup"
    asyncio.run(p.resolve_duplicate(i, append=True))
    idea = db.get(i)
    assert idea.status == "saved" and idea.mode == "append"
    assert "## Дополнение (" in (settings.ideas_dir / "Старая идея.md").read_text("utf-8")


def test_duplicate_target_deleted_falls_back_to_new(settings, tmp_path):
    db, p = make(settings, tmp_path, FakeBrain(A(duplicate_of="Старая идея")))
    i = db.create(1, Incoming([Part(text="про то же")]))
    asyncio.run(p.process(i))
    (settings.ideas_dir / "Старая идея.md").unlink()
    asyncio.run(p.resolve_duplicate(i, append=True))
    idea = db.get(i)
    assert idea.status == "saved" and idea.mode == "new" and idea.note_path.endswith("Мысль.md")


def test_new_theme_asks_then_creates(settings, tmp_path):
    a = A(new_theme={"name": "Идеи для НАП", "closest_existing": "Контент"})
    db, p = make(settings, tmp_path, FakeBrain(a))
    i = db.create(1, Incoming([Part(text="создай тему и запиши идею")]))
    asyncio.run(p.process(i))
    idea = db.get(i)
    assert idea.status == "await_theme"
    assert idea.parsed.new_theme == {"name": "Идеи для НАП", "closest_existing": "Контент"}
    assert idea.note_path is None

    asyncio.run(p.resolve_theme_create(i))
    idea = db.get(i)
    assert idea.status == "saved" and idea.parsed.theme == "Идеи для НАП" and idea.parsed.new_theme is None
    assert "Идеи для НАП" in settings.themes
    cv = Canvas.load(settings.ideas_canvas_path)
    assert cv.group("Идеи для НАП") is not None


def test_new_theme_use_existing(settings, tmp_path):
    a = A(new_theme={"name": "Идеи для НАП", "closest_existing": "Контент"})
    db, p = make(settings, tmp_path, FakeBrain(a))
    i = db.create(1, Incoming([Part(text="x")]))
    asyncio.run(p.process(i))
    asyncio.run(p.resolve_theme_existing(i, "Контент"))
    idea = db.get(i)
    assert idea.status == "saved" and idea.parsed.theme == "Контент" and idea.parsed.new_theme is None
    assert "Идеи для НАП" not in settings.themes


def test_new_theme_stale_tap_is_noop(settings, tmp_path):
    a = A(new_theme={"name": "Идеи для НАП", "closest_existing": None})
    db, p = make(settings, tmp_path, FakeBrain(a))
    i = db.create(1, Incoming([Part(text="x")]))
    asyncio.run(p.process(i))
    asyncio.run(p.resolve_theme_create(i))
    before = db.get(i).note_path
    asyncio.run(p.resolve_theme_create(i))  # повторный тап после того, как идея уже записана
    asyncio.run(p.resolve_theme_existing(i, "Контент"))
    assert db.get(i).note_path == before and db.get(i).parsed.theme == "Идеи для НАП"


def test_new_theme_after_duplicate_resolved_as_new(settings, tmp_path):
    a = A(duplicate_of="Старая идея", new_theme={"name": "Идеи для НАП", "closest_existing": None})
    db, p = make(settings, tmp_path, FakeBrain(a))
    i = db.create(1, Incoming([Part(text="x")]))
    asyncio.run(p.process(i))
    assert db.get(i).status == "await_dup"
    asyncio.run(p.resolve_duplicate(i, append=False))
    assert db.get(i).status == "await_theme"  # дубликат разрешился в "новую", вопрос про тему всё ещё впереди


def test_new_theme_create_failure_keeps_await_theme_for_retry(settings, tmp_path, monkeypatch):
    a = A(new_theme={"name": "Идеи для НАП", "closest_existing": None})
    db, p = make(settings, tmp_path, FakeBrain(a))
    i = db.create(1, Incoming([Part(text="x")]))
    asyncio.run(p.process(i))

    import ideabot.pipeline as pipeline_module

    def boom(s, name):
        raise OSError("диск")

    monkeypatch.setattr(pipeline_module, "add_theme", boom)
    asyncio.run(p.resolve_theme_create(i))
    idea = db.get(i)
    assert idea.status == "await_theme" and idea.note_path is None and idea.parsed.new_theme is not None
    assert "Идеи для НАП" not in settings.themes
    assert any(kind == "error" for kind, _ in p.notifier.events)


def test_new_theme_after_duplicate_resolved_as_append_skips_theme(settings, tmp_path):
    a = A(duplicate_of="Старая идея", new_theme={"name": "Идеи для НАП", "closest_existing": None})
    db, p = make(settings, tmp_path, FakeBrain(a))
    i = db.create(1, Incoming([Part(text="x")]))
    asyncio.run(p.process(i))
    asyncio.run(p.resolve_duplicate(i, append=True))
    assert db.get(i).status == "saved" and db.get(i).mode == "append"  # append не спрашивает про тему


def test_write_failure_then_retry_no_duplicates(settings, tmp_path, monkeypatch):
    db, p = make(settings, tmp_path, FakeBrain(A()))
    i = db.create(1, Incoming([Part(text="x")]))
    real = p.writer._sync_l2
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("диск")
        return real(*a, **kw)

    monkeypatch.setattr(p.writer, "_sync_l2", flaky)
    asyncio.run(p.process(i))
    assert db.get(i).status == "write_failed"
    asyncio.run(p.write(i))
    idea = db.get(i)
    assert idea.status == "saved"
    cv = Canvas.load(settings.ideas_canvas_path)
    assert sum(1 for n in cv.nodes if n.get("file") == idea.note_path) == 1
    assert len(list(settings.ideas_dir.glob("Мысль*.md"))) == 1


def test_startup_requeues(settings, tmp_path):
    db, p = make(settings, tmp_path, FakeBrain(A()))
    a = db.create(1, Incoming([Part(text="x")]))
    b = db.create(1, Incoming([Part(text="y")]))
    db.update(b, status="write_failed", analysis='{"type":"мысль","theme":"Разное","title":"Y","body_md":"b"}', mode="new")
    p.startup()
    assert [p.q.get_nowait() for _ in range(2)] == [("process", a, ()), ("write", b, ())]


def test_empty_idea_is_undone(settings, tmp_path):
    db, p = make(settings, tmp_path, FakeBrain(A()))
    i = db.create(1, Incoming([Part(text="   ")]))
    asyncio.run(p.process(i))
    assert db.get(i).status == "undone"


def test_l3_vanished_before_write_falls_back_to_l2_only(settings, tmp_path):
    a = A(links=[Link("Медиабаинг", "Офферы и связки", "file")])
    db, p = make(settings, tmp_path, FakeBrain(a))
    mb = Canvas.load(settings.vault / "Медиабаинг.canvas")
    mb.remove_node("n-off")  # L3 (нода заметки) пропала с холста между анализом и записью
    mb.save()
    i = db.create(1, Incoming([Part(text="x")]))
    asyncio.run(p.process(i))
    idea = db.get(i)
    assert idea.status == "saved"
    assert [l.l3 for l in idea.parsed.links] == [None]


def test_brain_subprocess_missing_binary_saves_raw(settings, tmp_path):
    class BadBinaryBrain:
        async def analyze(self, inc, index):
            from ideabot.brain import run_claude
            settings.claude_bin = str(tmp_path / "нет-такого-бинаря")
            return await run_claude(settings, "x", system="S", schema={"type": "object"}, timeout=5)

    db, p = make(settings, tmp_path, BadBinaryBrain())
    i = db.create(1, Incoming([Part(text="x")]))
    asyncio.run(p.process(i))
    idea = db.get(i)
    assert idea.status == "raw" and idea.note_path is not None


def test_retry_excludes_own_raw_note_from_existing_ideas(settings, tmp_path):
    brain = FakeBrain(A(), fail=True)
    db, p = make(settings, tmp_path, brain)
    i = db.create(1, Incoming([Part(text="сырая мысль про кофе")]))
    asyncio.run(p.process(i))
    assert db.get(i).status == "raw"
    raw_stem = Path(db.get(i).note_path).stem

    class RecordingBrain(FakeBrain):
        async def analyze(self, inc, index):
            self.seen_ideas = list(index.ideas)
            return await super().analyze(inc, index)

    recorder = RecordingBrain(A())
    p.brain = recorder
    asyncio.run(p.process(i))
    assert raw_stem not in recorder.seen_ideas


def test_write_does_not_resurrect_an_undone_idea(settings, tmp_path):
    """Двойная постановка в очередь (retry_loop + повторная обработка) не должна воскрешать отменённую идею."""
    db, p = make(settings, tmp_path, FakeBrain(A()))
    i = db.create(1, Incoming([Part(text="x")]))
    asyncio.run(p.process(i))
    note_path = db.get(i).note_path
    asyncio.run(p.undo(i))
    assert db.get(i).status == "undone"
    asyncio.run(p.write(i))  # хвост второй копии "write" в очереди, идея уже отменена
    idea = db.get(i)
    assert idea.status == "undone"
    assert not (settings.vault / note_path).exists()


def test_relink_deep_undo(settings, tmp_path):
    db, p = make(settings, tmp_path, FakeBrain(A()))
    i = db.create(1, Incoming([Part(text="x")]))
    asyncio.run(p.process(i))
    rel = db.get(i).note_path
    asyncio.run(p.relink(i, ["Медиабаинг"]))
    assert [l.l2 for l in db.get(i).parsed.links] == ["Медиабаинг"]
    assert Canvas.load(settings.vault / "Медиабаинг.canvas").file_node(rel) is not None
    asyncio.run(p.deep(i))
    assert db.get(i).deep == 1
    asyncio.run(p.undo(i))
    assert db.get(i).status == "undone" and not (settings.vault / rel).exists()


def test_claude_logout_alerts_once_with_backoff(settings, tmp_path):
    brain = FakeBrain(A())
    db, p = make(settings, tmp_path, brain)
    err = BrainError("claude exit 1: Invalid API key · Please run /login")
    assert err.kind == "login" and BrainError("You've hit your session limit · resets 8pm").kind == "limit"
    assert BrainError("Context limit reached").kind == "error"

    async def boom(inc, index):
        raise err
    brain.analyze = boom
    ids = [db.create(1, Incoming([Part(text=f"идея {n}")])) for n in range(3)]
    for i in ids:
        asyncio.run(p.process(i))
    alerts = [t for k, t in p.notifier.events if k == "error"]
    assert len(alerts) == 1 and "/login" in alerts[0]  # три сбоя подряд, алерт один
    assert all(db.get(i).status == "raw" for i in ids)


def test_edit_before_and_after_saving(settings, tmp_path):
    db, p = make(settings, tmp_path, FakeBrain(A()))
    i = db.create(1, Incoming([Part(text="кофейня у моря", msg_id=5)]))
    assert db.by_msg(1, 5).id == i and db.by_msg(1, 6) is None and db.by_msg(2, 5) is None
    p.apply_edit(i, 5, "кофейня у моря с коворкингом")  # ещё в очереди
    asyncio.run(p.process(i))
    first = db.get(i)
    assert first.status == "saved" and "коворкингом" in first.incoming.own_text()
    old_note = settings.vault / first.note_path
    assert old_note.exists()
    asyncio.run(p.rewrite(i, 5, "кофейня у моря с коворкингом и прокатом сапов"))
    assert not old_note.exists() and db.get(i).status == "new"  # старая заметка откатилась
    asyncio.run(p.process(i))
    again = db.get(i)
    assert again.status == "saved" and "сапов" in again.incoming.own_text()
    assert (settings.vault / again.note_path).exists()


def test_collector_edit_changes_buffered_part():
    from ideabot.collector import Collector

    async def flush(chat, parts):
        pass
    c = Collector(10, flush)

    async def go():
        c.add(1, Part(text="опечатка", msg_id=7))
        ok = c.edit(1, 7, "исправил")
        res = (ok, c._buf[1][0].text, c.edit(1, 8, "x"))
        for t in c._timers.values():
            t.cancel()
        return res
    assert asyncio.run(go()) == (True, "исправил", False)
