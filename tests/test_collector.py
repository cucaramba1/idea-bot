import asyncio

from ideabot.collector import Collector
from ideabot.models import Part


def test_collects_within_window_and_splits_after():
    flushed = []

    async def on_flush(chat_id, parts):
        flushed.append((chat_id, [p.text for p in parts]))

    async def scenario():
        c = Collector(0.2, on_flush)
        c.add(1, Part(text="коммент"))
        await asyncio.sleep(0.1)
        c.add(1, Part(text="форвард", origin="Канал"))
        c.add(2, Part(text="другой чат"))
        await asyncio.sleep(0.35)
        c.add(1, Part(text="новая идея"))
        await asyncio.sleep(0.35)

    asyncio.run(scenario())
    assert sorted(flushed) == [(1, ["коммент", "форвард"]), (1, ["новая идея"]), (2, ["другой чат"])]
