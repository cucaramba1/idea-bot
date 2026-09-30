import pytest

from ideabot.db import DB
from ideabot.models import Incoming, Part


def test_crud(tmp_path):
    db = DB(tmp_path / "d" / "ideas.db")
    i = db.create(7, Incoming([Part(text="x")]))
    idea = db.get(i)
    assert idea.status == "new" and idea.chat_id == 7 and idea.incoming.own_text() == "x"
    assert idea.parsed is None
    db.update(i, status="saved", note_path="A/B.md", deep=1)
    assert db.get(i).note_path == "A/B.md" and db.get(i).deep == 1
    assert [x.id for x in db.by_status("saved", "raw")] == [i]
    db.add_link(i, "M.canvas", node_id="n", node_created=True, edge_id="e")
    db.add_link(i, None, wikilink="[[A]]")
    rows = db.links(i)
    assert rows[0]["node_created"] == 1 and rows[1]["canvas"] is None
    db.delete_links([rows[0]["id"]])
    assert len(db.links(i)) == 1
    with pytest.raises(ValueError):
        db.update(i, hack="x")
