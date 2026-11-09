"""Пакеты записи: сервер разбирает идею, ноут пишет настоящим Writer, ответ правит базу и карточку сервера."""
import asyncio
import dataclasses
import shutil

import pytest

from ideabot import packets as P
from ideabot.db import DB
from ideabot.models import Incoming, Link, Part
from ideabot.pipeline import Pipeline
from ideabot.vault.sync import Writer
from test_pipeline import A, FakeBrain, FakeNotifier


@pytest.fixture
def pair(settings, tmp_path, monkeypatch):
    monkeypatch.setenv("APPLY_MODE", "packet")
    srv = dataclasses.replace(settings, root=tmp_path / "srv")
    db = DB(srv.data_dir / "ideas.db")
    pipe = Pipeline(srv, db, Writer(srv, db), FakeBrain(A()), None, FakeNotifier())
    home_db = DB(settings.data_dir / "applier.db")
    return srv, db, pipe, settings, home_db


def roundtrip(srv, home, home_db, pipe, db):
    """То, что делает rsync туда и обратно, и один проход applier + серверного ack_loop."""
    inbox = home.data_dir / "inbox"
    shutil.rmtree(inbox, ignore_errors=True)
    if (srv.data_dir / "outbox").exists():
        shutil.copytree(srv.data_dir / "outbox", inbox)
    keys = P.apply_inbox(home, home_db)
    (srv.data_dir / "acks").mkdir(exist_ok=True)
    for f in (home.data_dir / "acks_out").glob("*.json"):
        shutil.copy(f, srv.data_dir / "acks" / f.name)
        f.unlink()
    asyncio.run(pipe.on_acks(P.read_acks(srv, db)))
    return keys


def new_idea(db, pipe):
    i = db.create(1, Incoming([Part(text="идея про кофе")]))
    asyncio.run(pipe.process(i))
    return i


def test_save_roundtrip_writes_vault_and_updates_card(pair):
    srv, db, pipe, home, hdb = pair
    i = new_idea(db, pipe)
    assert db.get(i).status == "queued" and len(list((srv.data_dir / "outbox").glob("*.json"))) == 1
    assert roundtrip(srv, home, hdb, pipe, db)
    idea = db.get(i)
    assert idea.status == "saved" and idea.mode == "new" and (home.vault / idea.note_path).exists()
    assert pipe.notifier.events[-1] == ("refresh", i)
    assert not list((srv.data_dir / "outbox").glob("*.json"))


def test_redelivery_does_not_write_second_note(pair):
    srv, db, pipe, home, hdb = pair
    new_idea(db, pipe)
    inbox = home.data_dir / "inbox"
    shutil.copytree(srv.data_dir / "outbox", inbox)
    before = len(list(home.ideas_dir.glob("*.md")))
    P.apply_inbox(home, hdb)
    P.apply_inbox(home, hdb)  # ноут не успел отправить ack, rsync принёс пакет снова
    assert len(list(home.ideas_dir.glob("*.md"))) == before + 1


def test_undo_goes_after_save_by_number(pair):
    srv, db, pipe, home, hdb = pair
    i = new_idea(db, pipe)
    db.update(i, status="saved")  # как будто ответ уже был, но ноут ещё не получил второй пакет
    asyncio.run(pipe.undo(i))
    assert [p.name.split("-")[1] for p in sorted((srv.data_dir / "outbox").glob("*.json"))] == ["save", "undo"]
    roundtrip(srv, home, hdb, pipe, db)
    assert db.get(i).status == "undone" and db.get(i).note_path is None
    assert not [f for f in home.ideas_dir.glob("*.md") if "Мысль" in f.name]


def test_failed_write_shows_error_and_schedules_retry(pair, monkeypatch):
    srv, db, pipe, home, hdb = pair
    i = new_idea(db, pipe)
    monkeypatch.setattr(Writer, "save_new", lambda *a, **k: (_ for _ in ()).throw(OSError("диск полон")))
    roundtrip(srv, home, hdb, pipe, db)
    assert db.get(i).status == "write_failed"
    assert any(e[0] == "error" and "диск полон" in e[1] for e in pipe.notifier.events)


def test_relink_deep_roundtrip(pair):
    srv, db, pipe, home, hdb = pair
    i = new_idea(db, pipe)
    roundtrip(srv, home, hdb, pipe, db)
    asyncio.run(pipe.relink(i, ["Медиабаинг"]))
    assert db.get(i).status == "queued"
    roundtrip(srv, home, hdb, pipe, db)
    assert db.get(i).status == "saved" and [l.l2 for l in db.get(i).parsed.links] == ["Медиабаинг"]
    asyncio.run(pipe.deep(i))
    roundtrip(srv, home, hdb, pipe, db)
    assert db.get(i).deep == 1 and "находка" in (home.vault / db.get(i).note_path).read_text("utf-8")


def test_rewrite_undoes_then_reprocesses(pair):
    srv, db, pipe, home, hdb = pair
    i = new_idea(db, pipe)
    roundtrip(srv, home, hdb, pipe, db)
    old = db.get(i).note_path
    asyncio.run(pipe.rewrite(i, 0, "новый текст"))
    roundtrip(srv, home, hdb, pipe, db)
    assert not (home.vault / old).exists() and db.get(i).status == "new" and pipe.q.qsize() >= 1


def test_raw_ack_does_not_overwrite_new_status(pair):
    srv, db, pipe, home, hdb = pair
    pipe.brain.fail = True
    i = db.create(1, Incoming([Part(text="идея")]))
    asyncio.run(pipe.process(i))
    assert db.get(i).status == "raw"
    db.update(i, status="new")  # пока ноут писал сырую заметку, ты нажал «разобрать ещё раз»
    roundtrip(srv, home, hdb, pipe, db)
    assert db.get(i).status == "new" and db.get(i).note_path
