"""Голос -> текст через Groq API (whisper-large-v3-turbo): никакой локальной модели, RAM почти не растёт."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import aiohttp

from .config import Settings

log = logging.getLogger(__name__)
_lock = asyncio.Lock()  # бесплатный лимит Groq общий: голосовые идут по одному

URL = "https://api.groq.com/openai/v1/audio/transcriptions"
PROMPT = "Идея для бизнеса, контента или заметка. Рилс, пост, медиабаинг, личный бренд, Obsidian."
MAX_BYTES = 25 * 1024 * 1024  # лимит free-плана Groq на файл


async def transcribe(s: Settings, audio: Path) -> str:
    if not s.groq_key:
        raise RuntimeError("нет GROQ_API_KEY в .env")
    if audio.stat().st_size > MAX_BYTES:
        raise RuntimeError("голосовое больше 25 МБ, Groq не возьмёт")
    async with _lock:
        for attempt in range(3):
            form = aiohttp.FormData()
            form.add_field("file", audio.read_bytes(), filename=audio.name, content_type="audio/ogg")
            form.add_field("model", s.stt_model)
            form.add_field("language", "ru")
            form.add_field("temperature", "0")
            form.add_field("prompt", PROMPT)
            form.add_field("response_format", "json")
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as ses:
                    async with ses.post(URL, data=form, headers={"Authorization": f"Bearer {s.groq_key}"}) as r:
                        body = await r.text()
                        if r.status == 200:
                            return (await asyncio.to_thread(_text, body)).strip()
                        wait = float(r.headers.get("retry-after", 10)) if r.status == 429 else 5
                        retry = r.status == 429 or r.status >= 500
                        log.warning("groq stt http %s (попытка %s): %s", r.status, attempt + 1, body[:200])
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                wait, retry = 5, True
                log.warning("groq stt сеть (попытка %s): %s", attempt + 1, e)
            if not retry or attempt == 2:
                break
            await asyncio.sleep(min(wait, 30))
        raise RuntimeError("Groq не смог расшифровать голос")


def _text(body: str) -> str:
    return str(json.loads(body).get("text") or "")
