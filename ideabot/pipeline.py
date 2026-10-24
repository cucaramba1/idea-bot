"""Очередь идей. Главное правило: идея не теряется, при любом сбое она в vault или в БД с кнопкой повтора."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from dataclasses import asdict

from .brain import BrainError, clean_links
from .config import Settings
from .config_themes import add_theme
from .db import DB
from .models import Analysis, Link
from .vault.index import build_index
from .vault.sync import Writer

log = logging.getLogger(__name__)
WRITABLE_STATUSES = ("new", "raw", "stt_failed", "write_failed", "await_dup", "await_theme")


class Notifier(Protocol):
    async def refresh(self, idea_id: int) -> None: ...
    async def busy(self, idea_id: int, text: str) -> None: ...
    async def error(self, idea_id: int, text: str) -> None: ...


CLAUDE_ALERTS = {
    "login": "🔑 Claude разлогинен, идеи не разбираются. В терминале: claude, потом /login. "
             "Сырые идеи потом разберу кнопкой 🔄.",
    "limit": "⏳ У Claude кончился лимит подписки. Идеи сохраняю сырыми, после сброса лимита жми 🔄 на карточках.",
}
ALERT_FIRST, ALERT_MAX = 3600, 12 * 3600  # пауза между одинаковыми алертами: час, дальше вдвое, не больше 12 ч


class Pipeline:
    def __init__(self, s: Settings, db: DB, writer: Writer, brain, stt: Callable[[Path], Awaitable[str]],
                 notifier: Notifier, index_fn=build_index):
        self.s, self.db, self.writer, self.brain, self.stt = s, db, writer, brain, stt
        self.notifier = notifier
        self.index_fn = index_fn
        self.q: asyncio.Queue = asyncio.Queue()
        self.vault_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._alerts: dict[str, tuple[float, float]] = {}  # kind -> (когда можно снова, следующая пауза)

    async def claude_alert(self, idea_id: int, e: BrainError) -> None:
        if e.kind not in CLAUDE_ALERTS:
            return
        now = time.monotonic()
        nxt, pause = self._alerts.get(e.kind, (0.0, ALERT_FIRST))
        if now < nxt:
            return
        self._alerts[e.kind] = (now + pause, min(pause * 2, ALERT_MAX))
        await self.notifier.error(idea_id, CLAUDE_ALERTS[e.kind])

    def submit(self, idea_id: int) -> None:
        self.q.put_nowait(("process", idea_id, ()))

    def spawn(self, coro, idea_id: int | None = None) -> None:
        t = asyncio.create_task(self._guarded(coro, idea_id))
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def _guarded(self, coro, idea_id: int | None) -> None:
        """Ошибка кнопочной задачи в лог и перерисовка карточки, иначе она висит на «⏳…» без кнопок."""
        try:
            await coro
        except Exception:
            log.exception("фоновая задача #%s упала", idea_id)
            if idea_id is not None:
                try:
                    await self.notifier.refresh(idea_id)
                except Exception:
                    log.exception("refresh #%s", idea_id)

    def startup(self) -> None:
        for idea in self.db.by_status("new"):
            self.submit(idea.id)
        for idea in self.db.by_status("write_failed"):
            self.q.put_nowait(("write", idea.id, ()))

    async def run(self) -> None:
        while True:
            kind, idea_id, args = await self.q.get()
            try:
                await getattr(self, kind)(idea_id, *args)
            except Exception:
                log.exception("%s #%s упал", kind, idea_id)
            finally:
                self.q.task_done()

    async def retry_loop(self) -> None:
        while True:
            await asyncio.sleep(self.s.retry_every)
            for idea in self.db.by_status("write_failed"):
                self.q.put_nowait(("write", idea.id, ()))

    async def process(self, idea_id: int) -> None:
        idea = self.db.get(idea_id)
        if idea is None or idea.status not in ("new", "stt_failed", "raw"):
            return
        inc = idea.incoming
        for part in inc.parts:
            if not part.voice:
                continue
            try:
                part.text = ((part.text + "\n\n") if part.text else "") + (await self.stt(Path(part.voice))).strip()
            except Exception as e:
                log.warning("stt #%s: %s", idea_id, e)
                self.db.update(idea_id, status="stt_failed", error=str(e))
                await self.notifier.refresh(idea_id)
                return
            part.voice, part.was_voice = None, True
            self.db.update(idea_id, payload=inc.to_json())
        if inc.is_empty():
            self.db.update(idea_id, status="undone", error="пустое сообщение")
            await self.notifier.refresh(idea_id)
            return
        index = self.index_fn(self.s)
        if idea.note_path:  # ретрай: заметка (сырая или недописанная) не должна выглядеть как «уже существующая идея»
            own_stem = Path(idea.note_path).stem
            index.ideas = [name for name in index.ideas if name != own_stem]
        try:
            a = await self.brain.analyze(inc, index)
        except BrainError as e:
            log.warning("brain #%s: %s", idea_id, e)
            await self.claude_alert(idea_id, e)
            if idea.status != "raw":
                async with self.vault_lock:
                    self.writer.save_raw(idea_id, inc)
            self.db.update(idea_id, status="raw", error=str(e))
            await self.notifier.refresh(idea_id)
            return
        self._alerts.clear()  # claude снова отвечает: следующий сбой сообщим сразу
        self.db.update(idea_id, analysis=json.dumps(a.to_dict(), ensure_ascii=False), error=None)
        if a.duplicate_of:
            self.db.update(idea_id, status="await_dup")
            await self.notifier.refresh(idea_id)
            return
        self.db.update(idea_id, mode="new")
        await self._route_new(idea_id, a)

    async def _route_new(self, idea_id: int, a: Analysis) -> None:
        if a.new_theme:
            self.db.update(idea_id, status="await_theme")
            await self.notifier.refresh(idea_id)
            return
        await self.write(idea_id)

    async def write(self, idea_id: int) -> None:
        idea = self.db.get(idea_id)
        if idea is None or idea.status not in WRITABLE_STATUSES:
            return  # идея уже записана/отменена: хвост повторной постановки в очередь
        a, inc = idea.parsed, idea.incoming
        async with self.vault_lock:
            try:
                if idea.note_path:
                    self.writer.undo(idea_id)  # хвосты сырой или недописанной записи
                index = self.index_fn(self.s)
                a.links = clean_links([asdict(link) for link in a.links], index)
                append = idea.mode == "append" and (self.s.ideas_dir / f"{a.duplicate_of}.md").exists()
                if append:
                    self.writer.append(idea_id, a.duplicate_of, a, inc, index)
                else:
                    self.writer.save_new(idea_id, a, inc, index)
            except Exception as e:  # холст с битым JSON, пропавший L3, диск и т.п. — идея не должна зависнуть
                log.warning("write #%s: %s", idea_id, e)
                self.db.update(idea_id, status="write_failed", error=str(e))
                await self.notifier.refresh(idea_id)
                return
        self.db.update(idea_id, status="saved", mode="append" if append else "new",
                       analysis=json.dumps(a.to_dict(), ensure_ascii=False), error=None)
        await self.notifier.refresh(idea_id)

    async def resolve_duplicate(self, idea_id: int, append: bool) -> None:
        if self.db.get(idea_id).status != "await_dup":
            return
        self.db.update(idea_id, mode="append" if append else "new")
        if append:
            await self.write(idea_id)
        else:
            await self._route_new(idea_id, self.db.get(idea_id).parsed)

    async def resolve_theme_create(self, idea_id: int) -> None:
        idea = self.db.get(idea_id)
        if idea is None or idea.status != "await_theme":
            return
        a = idea.parsed
        name = a.new_theme["name"]
        try:
            async with self.vault_lock:
                add_theme(self.s, name)
        except Exception as e:
            # статус остаётся await_theme (не write_failed): фоновый retry_loop трогает только
            # write_failed, а тут нужен новый тап владельца, иначе идея воскреснет в старой теме
            log.warning("add_theme #%s: %s", idea_id, e)
            await self.notifier.error(idea_id, f"Не смог создать тему, попробуй ещё раз: {e}")
            await self.notifier.refresh(idea_id)
            return
        a.theme, a.new_theme = name, None
        self.db.update(idea_id, analysis=json.dumps(a.to_dict(), ensure_ascii=False))
        await self.write(idea_id)

    async def resolve_theme_existing(self, idea_id: int, theme_name: str) -> None:
        idea = self.db.get(idea_id)
        if idea is None or idea.status != "await_theme" or theme_name not in self.s.themes:
            return
        a = idea.parsed
        a.theme, a.new_theme = theme_name, None
        self.db.update(idea_id, analysis=json.dumps(a.to_dict(), ensure_ascii=False))
        await self.write(idea_id)

    async def relink(self, idea_id: int, l2_names: list[str]) -> None:
        idea = self.db.get(idea_id)
        if idea.status != "saved" or idea.mode != "new":
            return
        a = idea.parsed
        index = self.index_fn(self.s)
        try:
            links = await self.brain.pick_l3(a, l2_names, index)
        except BrainError as e:
            log.warning("pick_l3 #%s: %s", idea_id, e)
            links = [Link(n) for n in l2_names if n in index.l2]
        async with self.vault_lock:
            idea = self.db.get(idea_id)
            if idea is None or idea.status != "saved":  # пока claude думал, идею отменили
                await self.notifier.refresh(idea_id)
                return
            self.writer.relink(idea_id, links, index)
        a.links = links
        self.db.update(idea_id, analysis=json.dumps(a.to_dict(), ensure_ascii=False))
        await self.notifier.refresh(idea_id)

    async def deep(self, idea_id: int) -> None:
        idea = self.db.get(idea_id)
        if idea.status != "saved" or idea.deep:
            return
        try:
            md = await self.brain.deep_dive(idea.parsed)
        except BrainError as e:
            await self.notifier.error(idea_id, f"Не вышло копнуть глубже: {e}")
            await self.notifier.refresh(idea_id)
            return
        async with self.vault_lock:
            idea = self.db.get(idea_id)
            if idea is None or idea.status != "saved" or idea.deep:  # отменили или второй тап уже дописал
                await self.notifier.refresh(idea_id)
                return
            self.writer.add_deep(idea_id, md)
        await self.notifier.refresh(idea_id)

    async def undo(self, idea_id: int) -> None:
        async with self.vault_lock:
            self.writer.undo(idea_id)
        self.db.update(idea_id, status="undone")
        await self.notifier.refresh(idea_id)
