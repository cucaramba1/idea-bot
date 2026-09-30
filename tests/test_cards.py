import json
from datetime import datetime

from aiogram.types import Chat, MessageOriginChannel, MessageOriginHiddenUser, MessageOriginUser, User

from ideabot.bot import origin_name
from ideabot.cards import card_kb, card_text, l2_kb
from ideabot.db import Idea


def idea(status, **kw):
    a = {"type": "бизнес", "theme": "Бизнес-идеи / заработок", "title": "Сервис X", "body_md": "b", "score": 6,
         "links": [{"l2": "Медиабаинг", "l3": "Офферы", "l3_kind": "group"}, {"l2": "СММ", "l3": None, "l3_kind": "none"}],
         "duplicate_of": None, "unknown_tags": ["нету"]}
    base = dict(id=5, created_at="", status=status, chat_id=1, card_msg_id=10, payload="[]",
                analysis=json.dumps(a, ensure_ascii=False), note_path="x.md", mode="new", append_to=None,
                append_heading=None, deep=0, error="лимиты")
    base.update(kw)
    return Idea(**base)


def buttons(kb):
    return [b.callback_data for row in kb.inline_keyboard for b in row]


def test_saved_card():
    t = card_text(idea("saved"))
    assert t.splitlines()[0] == "💡 Сервис X"
    assert "💼 бизнес · тема «Бизнес-идеи / заработок» · оценка 6/10" in t
    assert "🔗 Медиабаинг → «Офферы», СММ" in t
    assert "холст #нету не нашёл" in t
    assert "—" not in t
    assert buttons(card_kb(idea("saved"))) == ["ch:5", "dp:5", "un:5"]
    assert buttons(card_kb(idea("saved", deep=1))) == ["ch:5", "un:5"]
    assert buttons(card_kb(idea("saved", mode="append"))) == ["un:5"]


def test_other_statuses():
    assert buttons(card_kb(idea("raw"))) == ["rt:5", "un:5"]
    assert "лимиты" in card_text(idea("raw"))
    assert buttons(card_kb(idea("stt_failed"))) == ["rt:5"]
    assert buttons(card_kb(idea("await_dup"))) == ["du:5:a", "du:5:n"]
    assert card_kb(idea("undone")) is None and card_kb(idea("write_failed")) is None


def test_l2_kb():
    kb = l2_kb(5, ["Медиабаинг", "СММ"], {1})
    assert [b.text for row in kb.inline_keyboard for b in row] == ["Медиабаинг", "✅ СММ", "✅ Готово", "✖️ Назад"]
    assert buttons(kb) == ["tg:5:0", "tg:5:1", "ok:5", "bk:5"]


def test_origin_name():
    d = datetime.now()
    assert origin_name(MessageOriginChannel(date=d, chat=Chat(id=1, type="channel", title="Канал"), message_id=1)) == "Канал"
    assert origin_name(MessageOriginUser(date=d, sender_user=User(id=1, is_bot=False, first_name="Вася"))) == "Вася"
    assert origin_name(MessageOriginHiddenUser(date=d, sender_user_name="Аноним")) == "Аноним"
