"""Склейка: всё, что пришло подряд с паузами меньше окна, это одна идея (коммент + форвард, альбом)."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from .models import Part

log = logging.getLogger(__name__)


class Collector:
    def __init__(self, window: float, on_flush: Callable[[int, list[Part]], Awaitable[None]]):
        self.window = window
        self.on_flush = on_flush
        self._buf: dict[int, list[Part]] = {}
        self._timers: dict[int, asyncio.Task] = {}

    def add(self, chat_id: int, part: Part) -> None:
        self._buf.setdefault(chat_id, []).append(part)
        if t := self._timers.pop(chat_id, None):
            t.cancel()
        self._timers[chat_id] = asyncio.create_task(self._later(chat_id))

    async def _later(self, chat_id: int) -> None:
        await asyncio.sleep(self.window)
        self._timers.pop(chat_id, None)
        parts = self._buf.pop(chat_id, [])
        if parts:
            try:
                await self.on_flush(chat_id, parts)
            except Exception:
                log.exception("не смог принять идею")
