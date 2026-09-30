"""Данные, которые ходят между ботом, очередью, Claude и записью в vault."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

TYPES = ("бизнес", "контент", "мысль", "прочее")


@dataclass
class Part:
    """Одно сообщение из Telegram, уже без aiogram-объектов."""
    text: str = ""
    origin: str | None = None   # None = написал сам Некит, иначе источник форварда
    photo: str | None = None    # путь к скачанному фото
    voice: str | None = None    # путь к аудио, пока не расшифровано
    was_voice: bool = False
    has_video: bool = False


@dataclass
class Incoming:
    """Идея = склейка нескольких сообщений."""
    parts: list[Part] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps([asdict(p) for p in self.parts], ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> "Incoming":
        return cls([Part(**d) for d in json.loads(s)])

    def own_text(self) -> str:
        return "\n\n".join(p.text.strip() for p in self.parts if p.origin is None and p.text.strip())

    def forwards(self) -> list[tuple[str, str, bool]]:
        """[(источник, текст, было ли видео)] с сохранением порядка источников."""
        acc: dict[str, list] = {}
        for p in self.parts:
            if p.origin is None:
                continue
            entry = acc.setdefault(p.origin, [[], False])
            if p.text.strip():
                entry[0].append(p.text.strip())
            entry[1] = entry[1] or p.has_video
        return [(o, "\n\n".join(texts), video) for o, (texts, video) in acc.items()]

    def photos(self) -> list[str]:
        return [p.photo for p in self.parts if p.photo]

    def source(self) -> str:
        if any(p.origin is not None for p in self.parts):
            return "форвард"
        if any(p.was_voice for p in self.parts):
            return "голос"
        return "текст"

    def is_empty(self) -> bool:
        return not (self.own_text() or self.forwards() or self.photos() or any(p.voice for p in self.parts))


@dataclass
class Link:
    l2: str
    l3: str | None = None
    l3_kind: str = "none"  # file | group | none


@dataclass
class Analysis:
    type: str
    theme: str
    title: str
    body_md: str
    score: int | None = None
    links: list[Link] = field(default_factory=list)
    duplicate_of: str | None = None
    unknown_tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Analysis":
        d = dict(d)
        d["links"] = [Link(**x) for x in d.get("links") or []]
        return cls(**d)
