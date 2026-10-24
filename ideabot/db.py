"""SQLite: какое сообщение стало какой заметкой и какими нодами. Нужно для отмены, перевеса, повторов."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .models import Analysis, Incoming

SCHEMA = """
CREATE TABLE IF NOT EXISTS ideas(
  id INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL,
  chat_id INTEGER NOT NULL,
  card_msg_id INTEGER,
  payload TEXT NOT NULL,
  analysis TEXT,
  note_path TEXT,
  mode TEXT,
  append_to TEXT,
  append_heading TEXT,
  deep INTEGER NOT NULL DEFAULT 0,
  error TEXT
);
CREATE TABLE IF NOT EXISTS links(
  id INTEGER PRIMARY KEY,
  idea_id INTEGER NOT NULL,
  canvas TEXT,
  node_id TEXT,
  node_created INTEGER NOT NULL DEFAULT 0,
  edge_id TEXT,
  wikilink TEXT
);
"""
UPDATABLE = {"status", "card_msg_id", "payload", "analysis", "note_path", "mode",
             "append_to", "append_heading", "deep", "error"}


@dataclass
class Idea:
    id: int
    created_at: str
    status: str
    chat_id: int
    card_msg_id: int | None
    payload: str
    analysis: str | None
    note_path: str | None
    mode: str | None
    append_to: str | None
    append_heading: str | None
    deep: int
    error: str | None

    @property
    def incoming(self) -> Incoming:
        return Incoming.from_json(self.payload)

    @property
    def parsed(self) -> Analysis | None:
        return Analysis.from_dict(json.loads(self.analysis)) if self.analysis else None


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.c = sqlite3.connect(path)
        self.c.row_factory = sqlite3.Row
        self.c.executescript(SCHEMA)

    def create(self, chat_id: int, inc: Incoming) -> int:
        cur = self.c.execute(
            "INSERT INTO ideas(created_at, status, chat_id, payload) VALUES(?, 'new', ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), chat_id, inc.to_json()))
        self.c.commit()
        return cur.lastrowid

    def get(self, idea_id: int) -> Idea | None:
        row = self.c.execute("SELECT * FROM ideas WHERE id=?", (idea_id,)).fetchone()
        return Idea(**dict(row)) if row else None

    def update(self, idea_id: int, **fields) -> None:
        if bad := set(fields) - UPDATABLE:
            raise ValueError(f"нельзя обновлять {bad}")
        sets = ", ".join(f"{k}=?" for k in fields)
        self.c.execute(f"UPDATE ideas SET {sets} WHERE id=?", (*fields.values(), idea_id))
        self.c.commit()

    def by_msg(self, chat_id: int, msg_id: int):
        """Идея, в которую вошло сообщение msg_id (payload хранит части с msg_id)."""
        for row in self.c.execute("SELECT id FROM ideas WHERE chat_id=? AND payload LIKE ? ORDER BY id DESC",
                                  (chat_id, f'%"msg_id": {int(msg_id)}%')):
            idea = self.get(row["id"])
            if any(p.msg_id == msg_id for p in idea.incoming.parts):
                return idea
        return None

    def by_status(self, *statuses: str) -> list[Idea]:
        q = ",".join("?" * len(statuses))
        rows = self.c.execute(f"SELECT * FROM ideas WHERE status IN ({q}) ORDER BY id", statuses).fetchall()
        return [Idea(**dict(r)) for r in rows]

    def add_link(self, idea_id: int, canvas: str | None, node_id: str | None = None,
                 node_created: bool = False, edge_id: str | None = None, wikilink: str | None = None) -> None:
        self.c.execute(
            "INSERT INTO links(idea_id, canvas, node_id, node_created, edge_id, wikilink) VALUES(?,?,?,?,?,?)",
            (idea_id, canvas, node_id, int(node_created), edge_id, wikilink))
        self.c.commit()

    def links(self, idea_id: int) -> list[dict]:
        rows = self.c.execute("SELECT * FROM links WHERE idea_id=? ORDER BY id", (idea_id,)).fetchall()
        return [dict(r) for r in rows]

    def delete_links(self, ids: list[int]) -> None:
        self.c.executemany("DELETE FROM links WHERE id=?", [(i,) for i in ids])
        self.c.commit()
