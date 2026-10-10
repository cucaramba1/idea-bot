"""Добавление новой темы в config/themes.yaml и в Settings в памяти процесса."""
from __future__ import annotations

import yaml

from .config import Settings
from .vault.fs import atomic_write


def add_theme(s: Settings, name: str) -> None:
    """Дописывает тему перед fallback-темой. Повторный вызов с тем же именем ничего не меняет."""
    path = s.root / "config" / "themes.yaml"
    data = yaml.safe_load(path.read_text("utf-8"))
    themes = list(data["themes"])
    if name not in themes:
        themes.insert(themes.index(data["fallback"]), name)
        data["themes"] = themes
        atomic_write(path, yaml.safe_dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False))
    if name not in s.themes:
        s.themes.insert(s.themes.index(s.fallback_theme), name)
