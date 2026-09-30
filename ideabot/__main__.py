"""Один процесс: поллинг бота + очередь + повторы."""
from __future__ import annotations

import asyncio
import logging
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher

from .bot import TgNotifier, build_router
from .brain import Brain
from .collector import Collector
from .config import load_settings
from .db import DB
from .models import Incoming
from .pipeline import Pipeline
from .stt import transcribe
from .vault.sync import Writer


async def main() -> None:
    s = load_settings()
    s.logs_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[RotatingFileHandler(s.logs_dir / "ideabot.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"),
                  logging.StreamHandler()])
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    if not s.bot_token or not s.owner_id:
        raise SystemExit("Заполни BOT_TOKEN и OWNER_ID в .env")
    if not s.ideas_canvas_path.exists():
        raise SystemExit(f"Не нашёл холст {s.ideas_canvas_path}")
    db = DB(s.db_path)
    bot = Bot(s.bot_token)
    pipeline = Pipeline(s, db, Writer(s, db), Brain(s), lambda p: transcribe(s, p), TgNotifier(bot, db))

    async def on_flush(chat_id, parts):
        idea_id = db.create(chat_id, Incoming(parts))
        msg = await bot.send_message(chat_id, "⏳ Разбираю…")
        db.update(idea_id, card_msg_id=msg.message_id)
        pipeline.submit(idea_id)

    dp = Dispatcher()
    dp.include_router(build_router(s, db, pipeline, Collector(s.collect_window, on_flush)))
    pipeline.startup()
    logging.getLogger("ideabot").info("Idea Bot запущен, vault: %s", s.vault)
    try:
        await asyncio.gather(pipeline.run(), pipeline.retry_loop(), dp.start_polling(bot, handle_signals=False))
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
