"""Настройки: .env (секреты и пути) + config/*.yaml (параметры и темы)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv


@dataclass
class Settings:
    root: Path
    vault: Path
    bot_token: str
    owner_id: int
    claude_bin: str
    groq_key: str
    stt_model: str
    ideas_canvas: str
    themes: list[str]
    fallback_theme: str
    shallow_canvases: set[str]
    goals_note: str
    collect_window: float
    claude_timeout: int
    deep_timeout: int
    retry_every: int
    max_index_chars: int
    claude_stall: int = 120  # claude молчит столько секунд (ни одного события потока): завис, не ждём общий таймаут

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ideas.db"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def ideas_dir(self) -> Path:
        return self.vault / self.ideas_canvas

    @property
    def ideas_canvas_path(self) -> Path:
        return self.vault / f"{self.ideas_canvas}.canvas"

    @property
    def moc_path(self) -> Path:
        return self.ideas_dir / f"{self.ideas_canvas} — карта заметок.md"


def load_settings(root: Path | None = None) -> Settings:
    root = root or Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env")
    cfg = yaml.safe_load((root / "config" / "settings.yaml").read_text("utf-8"))
    th = yaml.safe_load((root / "config" / "themes.yaml").read_text("utf-8"))
    env = lambda k, d="": os.getenv(k, d).strip()  # noqa: E731
    home = Path.home()
    themes = list(th["themes"])
    if th["fallback"] not in themes:
        raise ValueError(f"fallback-тема «{th['fallback']}» должна быть в списке тем")
    return Settings(
        root=root,
        vault=Path(env("VAULT_DIR", str(home / "Documents/Obsidian storage"))),
        bot_token=env("BOT_TOKEN"),
        owner_id=int(env("OWNER_ID") or 0),
        claude_bin=env("CLAUDE_BIN", str(home / ".npm-global/bin/claude")),
        groq_key=env("GROQ_API_KEY"),
        stt_model=env("STT_MODEL", "whisper-large-v3-turbo"),
        ideas_canvas=cfg["ideas_canvas"],
        themes=themes,
        fallback_theme=th["fallback"],
        shallow_canvases=set(cfg.get("shallow_canvases") or []),
        goals_note=cfg["goals_note"],
        collect_window=float(cfg["collect_window"]),
        claude_timeout=int(cfg["claude_timeout"]),
        deep_timeout=int(cfg["deep_timeout"]),
        claude_stall=int(cfg.get("claude_stall", 120)),
        retry_every=int(cfg["retry_every"]),
        max_index_chars=int(cfg["max_index_chars"]),
    )
