"""aiogram: приём сообщений, кнопки, карточки."""
from __future__ import annotations

import logging
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, MessageOriginChannel,
                           MessageOriginChat, MessageOriginHiddenUser, MessageOriginUser)

from .cards import card_kb, card_text, l2_kb, theme_kb
from .collector import Collector
from .config import Settings
from .db import DB
from .models import Part
from .pipeline import Pipeline
from .vault.index import build_index

log = logging.getLogger(__name__)
HELLO = ("Кидай идею: текст, голос или форвард (можно с комментарием, склею всё, что пришло за 5 секунд). "
         "Хочешь привязать к конкретному холсту, добавь #название, например #медиабаинг или #личный_бренд.")


def selected_names(picks: dict[int, tuple[list, set]], idea_id: int) -> list | None:
    """Достаёт и убирает выбор пользователя. None значит список устарел (например после рестарта бота)."""
    entry = picks.pop(idea_id, None)
    if entry is None:
        return None
    names, sel = entry
    return [names[n] for n in sorted(sel)]


def origin_name(o) -> str:
    if isinstance(o, MessageOriginChannel):
        return o.chat.title or "канал"
    if isinstance(o, MessageOriginChat):
        return o.sender_chat.title or "чат"
    if isinstance(o, MessageOriginUser):
        return o.sender_user.full_name
    if isinstance(o, MessageOriginHiddenUser):
        return o.sender_user_name
    return "неизвестный источник"


async def to_part(m: Message, s: Settings) -> Part | None:
    origin = origin_name(m.forward_origin) if m.forward_origin else None
    text = m.text or m.caption or ""
    folder = s.media_dir / f"{m.chat.id}_{m.message_id}"
    media, name = None, ""
    if m.voice:
        media, name = m.voice, "voice.ogg"
    elif m.audio:
        media, name = m.audio, "audio" + (Path(m.audio.file_name).suffix if m.audio.file_name else ".mp3")
    elif m.video_note:
        media, name = m.video_note, "note.mp4"
    if media:
        folder.mkdir(parents=True, exist_ok=True)
        try:
            await m.bot.download(media, destination=folder / name)
        except TelegramBadRequest as e:  # больше 20 МБ Bot API не отдаёт
            log.warning("download %s: %s", name, e)
            await m.answer("Не смог скачать файл (у Telegram лимит для ботов 20 МБ). Пришли голос короче или текстом.")
            return Part(text=text, origin=origin) if text else None
        return Part(text=text, origin=origin, voice=str(folder / name))
    if m.photo:
        folder.mkdir(parents=True, exist_ok=True)
        await m.bot.download(m.photo[-1], destination=folder / "photo.jpg")
        return Part(text=text, origin=origin, photo=str(folder / "photo.jpg"))
    if m.video or m.animation:
        return Part(text=text, origin=origin, has_video=True)
    if text:
        return Part(text=text, origin=origin)
    return None


class TgNotifier:
    def __init__(self, bot: Bot, db: DB):
        self.bot, self.db = bot, db

    async def _show(self, idea_id: int, text: str, kb) -> None:
        idea = self.db.get(idea_id)
        if idea.card_msg_id:
            try:
                await self.bot.edit_message_text(text=text, chat_id=idea.chat_id, message_id=idea.card_msg_id,
                                                 reply_markup=kb)
                return
            except TelegramBadRequest as e:
                if "not modified" in str(e):
                    return
                log.warning("карточку #%s не отредактировать: %s", idea_id, e)
        msg = await self.bot.send_message(idea.chat_id, text, reply_markup=kb)
        self.db.update(idea_id, card_msg_id=msg.message_id)

    async def refresh(self, idea_id: int) -> None:
        idea = self.db.get(idea_id)
        await self._show(idea_id, card_text(idea), card_kb(idea))

    async def busy(self, idea_id: int, text: str) -> None:
        await self._show(idea_id, text, None)

    async def error(self, idea_id: int, text: str) -> None:
        idea = self.db.get(idea_id)
        await self.bot.send_message(idea.chat_id, text)


def build_router(s: Settings, db: DB, pipeline: Pipeline, collector: Collector) -> Router:
    r = Router()
    r.message.filter(F.from_user.id == s.owner_id)
    r.callback_query.filter(F.from_user.id == s.owner_id)
    r.edited_message.filter(F.from_user.id == s.owner_id)  # фильтр message на правки не действует
    picks: dict[int, tuple[list[str], set[int]]] = {}
    notifier = pipeline.notifier

    @r.message(CommandStart())
    async def start(m: Message):
        await m.answer(HELLO)

    @r.message()
    async def incoming(m: Message):
        part = await to_part(m, s)
        if part is None:
            await m.answer("Такое не понимаю: нужен текст, голос, фото или форвард.")
            return
        part.msg_id = m.message_id
        collector.add(m.chat.id, part)

    edits: dict[int, str] = {}  # msg_id -> новый текст, ждёт кнопки «Переписать»

    @r.edited_message()
    async def edited(m: Message):
        text = m.text or m.caption or ""
        if not text or collector.edit(m.chat.id, m.message_id, text):
            return
        idea = db.by_msg(m.chat.id, m.message_id)
        if idea is None:
            return
        if idea.status == "new":  # ещё в очереди: разберём уже с правкой
            pipeline.apply_edit(idea.id, m.message_id, text)
            return
        if idea.status == "undone":
            return
        edits[m.message_id] = text
        title = idea.parsed.title if idea.parsed else "идея"
        await m.reply(f"Это сообщение уже в идее «{title}». Переписать заметку с правкой?",
                      reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                          InlineKeyboardButton(text="✏️ Переписать с правкой", callback_data=f"ed:{idea.id}:{m.message_id}")]]))

    @r.callback_query(F.data.regexp(r"^ed:\d+:\d+$"))
    async def rewrite(c: CallbackQuery):
        _, i, mid = c.data.split(":")
        text = edits.pop(int(mid), None)
        if text is None:
            await c.answer("Правка устарела, поправь сообщение ещё раз")
            return
        await c.message.edit_reply_markup(reply_markup=None)
        await c.answer("Переписываю")
        pipeline.spawn(pipeline.rewrite(int(i), int(mid), text), int(i))

    def _id(c: CallbackQuery) -> int:
        return int(c.data.split(":")[1])

    @r.callback_query(F.data.regexp(r"^ch:\d+$"))
    async def change(c: CallbackQuery):
        i = _id(c)
        idea = db.get(i)
        if idea.status != "saved" or idea.mode != "new":
            await c.answer("Для этой идеи холсты не меняются")
            return
        names = sorted(build_index(s).l2)
        current = {l.l2 for l in idea.parsed.links}
        picks[i] = (names, {n for n, name in enumerate(names) if name in current})
        await c.message.edit_reply_markup(reply_markup=l2_kb(i, *picks[i]))
        await c.answer()

    @r.callback_query(F.data.regexp(r"^tg:\d+:\d+$"))
    async def toggle(c: CallbackQuery):
        i, n = _id(c), int(c.data.split(":")[2])
        if i not in picks:
            await c.answer("Список устарел, нажми «Поменять холсты» ещё раз")
            return
        picks[i][1].symmetric_difference_update({n})
        await c.message.edit_reply_markup(reply_markup=l2_kb(i, *picks[i]))
        await c.answer()

    @r.callback_query(F.data.regexp(r"^ok:\d+$"))
    async def done(c: CallbackQuery):
        i = _id(c)
        names = selected_names(picks, i)
        if names is None:
            await c.answer("Список устарел, нажми «Поменять холсты» ещё раз")
            await notifier.refresh(i)
            return
        await c.answer("Перевешиваю…")
        await notifier.busy(i, "🔁 Перевешиваю связи…")
        pipeline.spawn(pipeline.relink(i, names), i)

    @r.callback_query(F.data.regexp(r"^bk:\d+$"))
    async def back(c: CallbackQuery):
        picks.pop(_id(c), None)
        await notifier.refresh(_id(c))
        await c.answer()

    @r.callback_query(F.data.regexp(r"^dp:\d+$"))
    async def deep(c: CallbackQuery):
        await c.answer("Копаю, это 1-3 минуты")
        await notifier.busy(_id(c), "🔍 Копаю глубже, 1-3 минуты…")
        pipeline.spawn(pipeline.deep(_id(c)), _id(c))

    @r.callback_query(F.data.regexp(r"^un:\d+$"))
    async def undo(c: CallbackQuery):
        await c.answer()
        pipeline.spawn(pipeline.undo(_id(c)), _id(c))

    @r.callback_query(F.data.regexp(r"^du:\d+:[an]$"))
    async def dup(c: CallbackQuery):
        await c.answer()
        await notifier.busy(_id(c), "⏳ Записываю…")
        pipeline.spawn(pipeline.resolve_duplicate(_id(c), append=c.data.endswith(":a")), _id(c))

    @r.callback_query(F.data.regexp(r"^nt:\d+:c$"))
    async def new_theme_create(c: CallbackQuery):
        await c.answer()
        await notifier.busy(_id(c), "🆕 Завожу тему…")
        pipeline.spawn(pipeline.resolve_theme_create(_id(c)), _id(c))

    @r.callback_query(F.data.regexp(r"^nt:\d+:u$"))
    async def new_theme_use_closest(c: CallbackQuery):
        i = _id(c)
        idea = db.get(i)
        nt = idea.parsed.new_theme if idea and idea.parsed else None
        closest = nt.get("closest_existing") if nt else None
        if not closest:
            await c.answer("Нет похожей темы")
            return
        await c.answer()
        await notifier.busy(i, "⏳ Записываю…")
        pipeline.spawn(pipeline.resolve_theme_existing(i, closest), i)

    @r.callback_query(F.data.regexp(r"^nt:\d+:o$"))
    async def new_theme_other(c: CallbackQuery):
        i = _id(c)
        await c.message.edit_reply_markup(reply_markup=theme_kb(i, sorted(s.themes)))
        await c.answer()

    @r.callback_query(F.data.regexp(r"^th:\d+:\d+$"))
    async def theme_pick(c: CallbackQuery):
        i, n = _id(c), int(c.data.split(":")[2])
        names = sorted(s.themes)
        if n >= len(names):
            await c.answer("Список устарел, нажми «Поменять холсты» ещё раз")
            await notifier.refresh(i)
            return
        await c.answer()
        await notifier.busy(i, "⏳ Записываю…")
        pipeline.spawn(pipeline.resolve_theme_existing(i, names[n]), i)

    @r.callback_query(F.data.regexp(r"^bk2:\d+$"))
    async def theme_back(c: CallbackQuery):
        await notifier.refresh(_id(c))
        await c.answer()

    @r.callback_query(F.data.regexp(r"^rt:\d+$"))
    async def retry(c: CallbackQuery):
        await c.answer()
        await notifier.busy(_id(c), "⏳ Разбираю…")
        pipeline.submit(_id(c))

    return r
