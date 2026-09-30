import asyncio

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
