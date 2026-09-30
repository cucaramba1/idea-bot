import json
import shutil
from pathlib import Path

import pytest

from ideabot.config import Settings, load_settings

PROJECT = Path(__file__).resolve().parent.parent


def _canvas(path: Path, nodes: list[dict], edges: list[dict] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"nodes": nodes, "edges": edges or []}, ensure_ascii=False, indent=1), "utf-8")


def _note(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")


@pytest.fixture
def vault(tmp_path) -> Path:
    v = tmp_path / "vault"
    _canvas(v / "Творческие идеи.canvas", [
        {"id": "g-gd", "type": "group", "x": 0, "y": 0, "width": 460, "height": 160, "label": "Геймдизайн"},
        {"id": "n-gd", "type": "file", "x": 30, "y": 50, "width": 400, "height": 90,
         "file": "Творческие идеи/Старая идея.md"},
        {"id": "g-misc", "type": "group", "x": 0, "y": 320, "width": 1460, "height": 230,
         "label": "Разное — цитаты (24.09.2026)", "color": "4"},
    ])
    _note(v / "Творческие идеи/Старая идея.md", "# Старая идея\n\nТекст.\n\n## Связанные заметки\n- [[Фильм]]\n")
    _note(v / "Творческие идеи/Творческие идеи — карта заметок.md",
          "# Творческие идеи — карта заметок\n\n## Геймдизайн\n\n- [[Старая идея]]\n")
    _canvas(v / "Медиабаинг.canvas", [
        {"id": "g-off", "type": "group", "x": 0, "y": 0, "width": 500, "height": 300, "label": "Офферы"},
        {"id": "n-off", "type": "file", "x": 20, "y": 40, "width": 400, "height": 100,
         "file": "Медиабаинг/Офферы и связки.md"},
        {"id": "n-moc", "type": "file", "x": 600, "y": 0, "width": 400, "height": 100,
         "file": "Медиабаинг/Медиабаинг — карта заметок.md"},
        {"id": "t-1", "type": "text", "x": 600, "y": 200, "width": 300, "height": 80, "text": "руками"},
    ])
    _note(v / "Медиабаинг/Офферы и связки.md", "# Офферы и связки\n")
    _note(v / "Медиабаинг/Медиабаинг — карта заметок.md", "# Медиабаинг — карта заметок\n")
    _canvas(v / "Досуг.canvas", [
        {"id": "g-kino", "type": "group", "x": 0, "y": 0, "width": 500, "height": 300, "label": "Кино"},
        {"id": "n-film", "type": "file", "x": 20, "y": 40, "width": 400, "height": 100, "file": "Досуг/Фильм.md"},
    ])
    _note(v / "Досуг/Фильм.md", "# Фильм\n")
    _note(v / "Досуг/Досуг — карта заметок.md", "# Досуг — карта заметок\n")
    _canvas(v / "Обо мне.canvas", [
        {"id": "t-me", "type": "text", "x": 0, "y": 0, "width": 300, "height": 100, "text": "обо мне"},
    ])
    _note(v / "Продуктивность и привычки/Мои цели.md", "# Мои цели\n\n- Запустить блог\n")
    return v


@pytest.fixture
def settings(tmp_path, vault, monkeypatch) -> Settings:
    root = tmp_path / "app"
    shutil.copytree(PROJECT / "config", root / "config")
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("OWNER_ID", "42")
    monkeypatch.setenv("VAULT_DIR", str(vault))
    monkeypatch.setenv("CLAUDE_BIN", "claude")
    monkeypatch.setenv("WHISPER_BIN", "mlx_whisper")
    return load_settings(root)
