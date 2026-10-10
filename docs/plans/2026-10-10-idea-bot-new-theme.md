# Idea Bot: новая тема по явной просьбе — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Бот распознаёт явную текстовую просьбу завести новую тему/подхолст в «Творческие идеи.canvas», показывает карточку с выбором (создать / использовать похожую / выбрать из существующих) и создаёт тему только после подтверждения кнопкой.

**Architecture:** Новое необязательное поле `new_theme` в ответе Claude (`brain.py`), новый промежуточный статус `await_theme` в пайплайне (по образцу уже существующего `await_dup`), новый модуль `config_themes.py` дописывает `config/themes.yaml` и мутирует `Settings.themes` в памяти процесса. Группа на холсте создаётся лениво уже существующей `ensure_theme_group()`, новый код в `canvas.py` не нужен.

**Tech Stack:** Python 3.13, aiogram, PyYAML, pytest, uv.

**Spec:** `docs/specs/2026-10-10-idea-bot-new-theme-design.md`

## Global Constraints

- Без длинного тире (—) в текстах карточек, коммитах и комментариях: запятая, двоеточие или дефис вместо него (см. `ideabot/cards.py:1` и общее правило проекта).
- Темы в `config/themes.yaml` остаются списком строк с ключом `fallback`; fallback-тема всегда должна входить в список (инвариант уже проверяется в `config.py:load_settings`, новый код не должен его нарушить).
- `Settings` это один общий объект на процесс (создаётся один раз в `__main__.py`, передаётся в `Pipeline`, `Brain`, `Writer`, `build_router`). Мутация `s.themes.insert(...)` должна менять список на месте (in-place), не пересоздавать объект, иначе часть компонентов продолжит видеть старый список.
- Новая тема предлагается Claude только по явной текстовой просьбе, никогда по эвристике «идея не подходит ни под одну тему».
- `uv run pytest -q` должен быть зелёным перед коммитом каждой задачи.
- Коммит в конце каждой задачи с припиской `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.

## Review Focus

- `new_theme.name` точно (посимвольно) совпадает с уже существующей темой: диалог не должен появляться, тема используется сразу молча.
- `add_theme()` вызван дважды с одним и тем же именем (повторный тап, гонка ретрая): тема не должна задублироваться ни в YAML, ни в `s.themes`.
- Название темы содержит YAML-специальные символы (двоеточие, кавычки): `config/themes.yaml` должен остаться валидным YAML после записи и перечитываться обратно без искажений.
- Кнопка «Использовать похожую» нажата или отрисована, когда `closest_existing` равен `null`: кнопки быть не должно, а обработчик не должен падать, если её всё же дёрнули (например, через старую клавиатуру после рестарта).
- `resolve_theme_create`/`resolve_theme_existing` вызваны, когда статус идеи уже не `await_theme` (повторный тап, идея отменена или уже записана): операция должна быть no-op, не создавать тему и не перезаписывать идею.

---

### Task 1: `Analysis.new_theme`

**Files:**
- Modify: `ideabot/models.py`
- Test: `tests/test_config_models.py`

**Interfaces:**
- Produces: `Analysis.new_theme: dict | None`, формат `{"name": str, "closest_existing": str | None}` или `None`. Участвует в `Analysis.to_dict()`/`Analysis.from_dict()` как обычное поле.

- [ ] **Step 1: Write the failing test**

Добавить в `tests/test_config_models.py` после `test_analysis_roundtrip`:

```python
def test_analysis_roundtrip_with_new_theme():
    a = Analysis(type="бизнес", theme="Разное", title="T", body_md="B",
                 new_theme={"name": "Идеи для НАП", "closest_existing": "Нячанг"})
    assert Analysis.from_dict(a.to_dict()) == a
    assert Analysis(type="мысль", theme="Разное", title="T", body_md="B").new_theme is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config_models.py::test_analysis_roundtrip_with_new_theme -v`
Expected: FAIL с `TypeError: Analysis.__init__() got an unexpected keyword argument 'new_theme'`

- [ ] **Step 3: Write minimal implementation**

В `ideabot/models.py`, в классе `Analysis`, добавить поле после `unknown_tags`:

```python
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
    new_theme: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Analysis":
        d = dict(d)
        d["links"] = [Link(**x) for x in d.get("links") or []]
        return cls(**d)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config_models.py -v`
Expected: PASS, все тесты в файле зелёные

- [ ] **Step 5: Commit**

```bash
git add ideabot/models.py tests/test_config_models.py
git commit -m "$(cat <<'EOF'
feat: поле new_theme в Analysis для предложения новой темы

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `brain.py` — промпт, схема, нормализация

**Files:**
- Modify: `ideabot/brain.py`
- Test: `tests/test_brain.py`

**Interfaces:**
- Consumes: `Analysis.new_theme` (Task 1), `Settings.themes: list[str]`, `Settings.fallback_theme: str`.
- Produces: `validate(raw, index, s, explicit, unknown) -> Analysis` теперь заполняет `new_theme` по правилам нормализации ниже. Используется в Task 4 (`pipeline.py`).

- [ ] **Step 1: Write the failing test**

Добавить в `tests/test_brain.py` после `test_validate_fixes_everything`:

```python
def test_validate_new_theme_normalizes(settings):
    ix = build_index(settings)
    # имя совпадает с уже существующей темой: диалог не нужен, тема используется напрямую
    a = validate({"type": "прочее", "theme": "Контент", "title": "T", "body_md": "b", "score": None,
                  "links": [], "duplicate_of": None, "new_theme": {"name": "Геймдизайн", "closest_existing": None}},
                 ix, settings, [], [])
    assert a.theme == "Геймдизайн" and a.new_theme is None

    # новое имя, closest_existing валиден
    a = validate({"type": "прочее", "theme": "Разное", "title": "T", "body_md": "b", "score": None,
                  "links": [], "duplicate_of": None,
                  "new_theme": {"name": "Идеи для НАП", "closest_existing": "Контент"}}, ix, settings, [], [])
    assert a.new_theme == {"name": "Идеи для НАП", "closest_existing": "Контент"}

    # closest_existing не входит в список тем: обнуляется
    a = validate({"type": "прочее", "theme": "Разное", "title": "T", "body_md": "b", "score": None,
                  "links": [], "duplicate_of": None,
                  "new_theme": {"name": "Идеи для НАП", "closest_existing": "Выдумка"}}, ix, settings, [], [])
    assert a.new_theme == {"name": "Идеи для НАП", "closest_existing": None}

    # new_theme отсутствует: поведение не меняется
    a = validate({"type": "прочее", "theme": "Разное", "title": "T", "body_md": "b", "score": None,
                  "links": [], "duplicate_of": None, "new_theme": None}, ix, settings, [], [])
    assert a.new_theme is None

    # пустое имя в new_theme игнорируется как отсутствие предложения
    a = validate({"type": "прочее", "theme": "Разное", "title": "T", "body_md": "b", "score": None,
                  "links": [], "duplicate_of": None, "new_theme": {"name": "  ", "closest_existing": None}},
                 ix, settings, [], [])
    assert a.new_theme is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_brain.py::test_validate_new_theme_normalizes -v`
Expected: FAIL с `TypeError: validate() got an unexpected keyword argument` или `KeyError`/`AttributeError` на `new_theme`, поскольку `raw.get("new_theme")` пока не обрабатывается.

- [ ] **Step 3: Write minimal implementation**

В `ideabot/brain.py`, добавить в конец блока `Правила:` в `SYSTEM` (после строки про `score`):

```python
- new_theme: заполняй только если текст явно и прямо просит завести новую тему или подхолст («создай тему», «заведи подхолст», «новая категория» и подобное). Никогда не заполняй её из-за того, что идея просто не вписывается ни в одну существующую тему, для этого есть фоллбек. Если заполняешь: name короткое название в стиле существующих тем, closest_existing самая близкая по смыслу существующая тема, если такая прослеживается, иначе null. Если не заполняешь, верни new_theme: null.
```

В `_LINK_ITEM` после него добавить константу схемы для `new_theme`:

```python
_NEW_THEME = {
    "type": ["object", "null"],
    "properties": {
        "name": {"type": "string"},
        "closest_existing": {"type": ["string", "null"]},
    },
    "required": ["name", "closest_existing"],
}
```

В `ANALYSIS_SCHEMA["properties"]` добавить `"new_theme": _NEW_THEME,` и в `ANALYSIS_SCHEMA["required"]` добавить `"new_theme"`:

```python
ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": list(TYPES)},
        "theme": {"type": "string"},
        "title": {"type": "string"},
        "body_md": {"type": "string"},
        "score": {"type": ["integer", "null"]},
        "links": {"type": "array", "items": _LINK_ITEM},
        "duplicate_of": {"type": ["string", "null"]},
        "new_theme": _NEW_THEME,
    },
    "required": ["type", "theme", "title", "body_md", "score", "links", "duplicate_of", "new_theme"],
}
```

В `validate()` заменить тело функции на:

```python
def validate(raw: dict, index: VaultIndex, s: Settings, explicit: list[str], unknown: list[str]) -> Analysis:
    t = raw.get("type") if raw.get("type") in TYPES else "прочее"
    theme = raw.get("theme") if raw.get("theme") in s.themes else s.fallback_theme
    title = (raw.get("title") or "").strip()[:80].strip() or "Идея без названия"
    score = raw.get("score")
    score = score if t == "бизнес" and isinstance(score, int) and 1 <= score <= 10 else None
    links = clean_links(raw.get("links") or [], index)
    for name in explicit:
        if name not in {l.l2 for l in links}:
            links.append(Link(name))
    dup = raw.get("duplicate_of")
    new_theme = None
    nt = raw.get("new_theme")
    if isinstance(nt, dict):
        name = (nt.get("name") or "").strip()
        if name and name in s.themes:
            theme = name  # уже есть такая тема, диалог не нужен
        elif name:
            closest = nt.get("closest_existing")
            new_theme = {"name": name, "closest_existing": closest if closest in s.themes else None}
    return Analysis(type=t, theme=theme, title=title, body_md=demote((raw.get("body_md") or "").strip()), score=score,
                    links=links, duplicate_of=dup if dup in index.ideas else None, unknown_tags=unknown,
                    new_theme=new_theme)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_brain.py -v`
Expected: PASS, все тесты в файле зелёные (включая старые `test_validate_fixes_everything`, который не передаёт `new_theme` вовсе, и `validate()` должен принимать его отсутствие так же, как `raw.get("new_theme")` возвращает `None`)

- [ ] **Step 5: Commit**

```bash
git add ideabot/brain.py tests/test_brain.py
git commit -m "$(cat <<'EOF'
feat: brain предлагает new_theme только по явной просьбе

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `config_themes.py` — запись новой темы

**Files:**
- Create: `ideabot/config_themes.py`
- Test: `tests/test_config_themes.py`

**Interfaces:**
- Consumes: `Settings.root: Path`, `Settings.themes: list[str]`, `Settings.fallback_theme: str` (все уже существуют в `config.py`), `ideabot.vault.fs.atomic_write`.
- Produces: `add_theme(s: Settings, name: str) -> None`. Используется в Task 4 (`pipeline.py`).

- [ ] **Step 1: Write the failing test**

Создать `tests/test_config_themes.py`:

```python
import yaml

from ideabot.config_themes import add_theme


def test_add_theme_inserts_before_fallback_and_updates_settings(settings):
    add_theme(settings, "Идеи для НАП")
    data = yaml.safe_load((settings.root / "config" / "themes.yaml").read_text("utf-8"))
    assert data["themes"][-2:] == ["Идеи для НАП", "Разное"]
    assert data["fallback"] == "Разное"
    assert settings.themes[-2:] == ["Идеи для НАП", "Разное"]


def test_add_theme_is_idempotent(settings):
    add_theme(settings, "Идеи для НАП")
    add_theme(settings, "Идеи для НАП")
    data = yaml.safe_load((settings.root / "config" / "themes.yaml").read_text("utf-8"))
    assert data["themes"].count("Идеи для НАП") == 1
    assert settings.themes.count("Идеи для НАП") == 1


def test_add_theme_survives_yaml_special_characters(settings):
    add_theme(settings, "Тема: с двоеточием")
    data = yaml.safe_load((settings.root / "config" / "themes.yaml").read_text("utf-8"))
    assert "Тема: с двоеточием" in data["themes"]
    assert "Тема: с двоеточием" in settings.themes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config_themes.py -v`
Expected: FAIL с `ModuleNotFoundError: No module named 'ideabot.config_themes'`

- [ ] **Step 3: Write minimal implementation**

Создать `ideabot/config_themes.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config_themes.py -v`
Expected: PASS, все три теста зелёные

- [ ] **Step 5: Commit**

```bash
git add ideabot/config_themes.py tests/test_config_themes.py
git commit -m "$(cat <<'EOF'
feat: add_theme дописывает тему в config/themes.yaml и в Settings

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `pipeline.py` — статус `await_theme` и резолверы

**Files:**
- Modify: `ideabot/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `Analysis.new_theme` (Task 1), `brain.validate` путём `Brain.analyze` (Task 2), `config_themes.add_theme` (Task 3).
- Produces: `Pipeline.resolve_theme_create(idea_id: int) -> None`, `Pipeline.resolve_theme_existing(idea_id: int, theme_name: str) -> None`. Статус идеи `"await_theme"`. Используется в Task 6 (`bot.py`).

- [ ] **Step 1: Write the failing test**

Добавить в `tests/test_pipeline.py` после `test_duplicate_target_deleted_falls_back_to_new`:

```python
def test_new_theme_asks_then_creates(settings, tmp_path):
    a = A(new_theme={"name": "Идеи для НАП", "closest_existing": "Контент"})
    db, p = make(settings, tmp_path, FakeBrain(a))
    i = db.create(1, Incoming([Part(text="создай тему и запиши идею")]))
    asyncio.run(p.process(i))
    idea = db.get(i)
    assert idea.status == "await_theme"
    assert idea.parsed.new_theme == {"name": "Идеи для НАП", "closest_existing": "Контент"}
    assert idea.note_path is None

    asyncio.run(p.resolve_theme_create(i))
    idea = db.get(i)
    assert idea.status == "saved" and idea.parsed.theme == "Идеи для НАП" and idea.parsed.new_theme is None
    assert "Идеи для НАП" in settings.themes
    cv = Canvas.load(settings.ideas_canvas_path)
    assert cv.group("Идеи для НАП") is not None


def test_new_theme_use_existing(settings, tmp_path):
    a = A(new_theme={"name": "Идеи для НАП", "closest_existing": "Контент"})
    db, p = make(settings, tmp_path, FakeBrain(a))
    i = db.create(1, Incoming([Part(text="x")]))
    asyncio.run(p.process(i))
    asyncio.run(p.resolve_theme_existing(i, "Контент"))
    idea = db.get(i)
    assert idea.status == "saved" and idea.parsed.theme == "Контент" and idea.parsed.new_theme is None
    assert "Идеи для НАП" not in settings.themes


def test_new_theme_stale_tap_is_noop(settings, tmp_path):
    a = A(new_theme={"name": "Идеи для НАП", "closest_existing": None})
    db, p = make(settings, tmp_path, FakeBrain(a))
    i = db.create(1, Incoming([Part(text="x")]))
    asyncio.run(p.process(i))
    asyncio.run(p.resolve_theme_create(i))
    before = db.get(i).note_path
    asyncio.run(p.resolve_theme_create(i))  # повторный тап после того, как идея уже записана
    asyncio.run(p.resolve_theme_existing(i, "Контент"))
    assert db.get(i).note_path == before and db.get(i).parsed.theme == "Идеи для НАП"


def test_new_theme_after_duplicate_resolved_as_new(settings, tmp_path):
    a = A(duplicate_of="Старая идея", new_theme={"name": "Идеи для НАП", "closest_existing": None})
    db, p = make(settings, tmp_path, FakeBrain(a))
    i = db.create(1, Incoming([Part(text="x")]))
    asyncio.run(p.process(i))
    assert db.get(i).status == "await_dup"
    asyncio.run(p.resolve_duplicate(i, append=False))
    assert db.get(i).status == "await_theme"  # дубликат разрешился в "новую", вопрос про тему всё ещё впереди


def test_new_theme_after_duplicate_resolved_as_append_skips_theme(settings, tmp_path):
    a = A(duplicate_of="Старая идея", new_theme={"name": "Идеи для НАП", "closest_existing": None})
    db, p = make(settings, tmp_path, FakeBrain(a))
    i = db.create(1, Incoming([Part(text="x")]))
    asyncio.run(p.process(i))
    asyncio.run(p.resolve_duplicate(i, append=True))
    assert db.get(i).status == "saved" and db.get(i).mode == "append"  # append не спрашивает про тему
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pipeline.py -v -k new_theme`
Expected: FAIL. `test_new_theme_asks_then_creates` падает на `assert idea.status == "await_theme"` (сейчас идея сразу пишется и получает `"saved"`), остальные падают с `AttributeError: 'Pipeline' object has no attribute 'resolve_theme_create'`.

- [ ] **Step 3: Write minimal implementation**

В `ideabot/pipeline.py`:

1. Импорт `add_theme`:

```python
from .brain import BrainError, clean_links
from .config import Settings
from .config_themes import add_theme
from .db import DB
from .models import Analysis, Link
```

(добавлена строка `from .config_themes import add_theme` и `Analysis` в импорт из `.models`, нужен для type hint в новом хелпере).

2. Добавить `"await_theme"` в `WRITABLE_STATUSES`:

```python
WRITABLE_STATUSES = ("new", "raw", "stt_failed", "write_failed", "await_dup", "await_theme")
```

3. Заменить хвост `process()` (после `self.db.update(idea_id, mode="new")`) и тело `resolve_duplicate()`:

```python
        self.db.update(idea_id, mode="new")
        await self._route_new(idea_id, a)

    async def _route_new(self, idea_id: int, a: Analysis) -> None:
        if a.new_theme:
            self.db.update(idea_id, status="await_theme")
            await self.notifier.refresh(idea_id)
            return
        await self.write(idea_id)

    async def write(self, idea_id: int) -> None:
```

(новый метод `_route_new` вставляется перед существующим `write`, остальной код `write` не меняется).

4. Заменить `resolve_duplicate()`:

```python
    async def resolve_duplicate(self, idea_id: int, append: bool) -> None:
        if self.db.get(idea_id).status != "await_dup":
            return
        self.db.update(idea_id, mode="append" if append else "new")
        if append:
            await self.write(idea_id)
        else:
            await self._route_new(idea_id, self.db.get(idea_id).parsed)
```

5. Добавить два новых метода после `resolve_duplicate()`:

```python
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
            log.warning("add_theme #%s: %s", idea_id, e)
            self.db.update(idea_id, status="write_failed", error=str(e))
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: PASS, все тесты в файле зелёные (включая существующие, не связанные с новой темой)

- [ ] **Step 5: Commit**

```bash
git add ideabot/pipeline.py tests/test_pipeline.py
git commit -m "$(cat <<'EOF'
feat: статус await_theme и резолверы создания/выбора темы

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `cards.py` — карточка и клавиатура для `await_theme`

**Files:**
- Modify: `ideabot/cards.py`
- Test: `tests/test_cards.py`

**Interfaces:**
- Consumes: `Idea.status == "await_theme"`, `Idea.parsed.new_theme` (Task 1).
- Produces: `theme_kb(idea_id: int, themes: list[str]) -> InlineKeyboardMarkup`. Используется в Task 6 (`bot.py`). `card_text`/`card_kb` теперь обрабатывают статус `await_theme`.

- [ ] **Step 1: Write the failing test**

Добавить в `tests/test_cards.py`:

```python
def test_await_theme_card_with_closest():
    i = idea("await_theme", analysis=json.dumps(
        {"type": "прочее", "theme": "Разное", "title": "T", "body_md": "b", "score": None, "links": [],
         "duplicate_of": None, "unknown_tags": [], "new_theme": {"name": "Идеи для НАП", "closest_existing": "Нячанг"}},
        ensure_ascii=False))
    assert card_text(i) == "🆕 Завести тему «Идеи для НАП»? Похоже на «Нячанг», можно сразу туда."
    assert buttons(card_kb(i)) == ["nt:5:c", "nt:5:u", "nt:5:o"]


def test_await_theme_card_without_closest():
    i = idea("await_theme", analysis=json.dumps(
        {"type": "прочее", "theme": "Разное", "title": "T", "body_md": "b", "score": None, "links": [],
         "duplicate_of": None, "unknown_tags": [], "new_theme": {"name": "Идеи для НАП", "closest_existing": None}},
        ensure_ascii=False))
    assert card_text(i) == "🆕 Завести тему «Идеи для НАП»?"
    assert buttons(card_kb(i)) == ["nt:5:c", "nt:5:o"]


def test_theme_kb():
    kb = theme_kb(5, ["Контент", "Разное"])
    assert [b.text for row in kb.inline_keyboard for b in row] == ["Контент", "Разное", "✖️ Назад"]
    assert buttons(kb) == ["th:5:0", "th:5:1", "bk2:5"]
```

Добавить `theme_kb` в импорт из `ideabot.cards` в начале файла:

```python
from ideabot.cards import card_kb, card_text, l2_kb, theme_kb
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cards.py -v -k "await_theme or theme_kb"`
Expected: FAIL с `ImportError: cannot import name 'theme_kb'`

- [ ] **Step 3: Write minimal implementation**

В `ideabot/cards.py`, добавить новую ветку в `card_text()` перед финальным `return {...}.get(...)`:

```python
def card_text(idea: Idea) -> str:
    a = idea.parsed
    if idea.status == "saved" and a:
        head = f"{ICON.get(a.type, '💡')} {a.type} · тема «{a.theme}»" + (f" · оценка {a.score}/10" if a.score else "")
        lines = [f"💡 {a.title}", head]
        if idea.mode == "append":
            lines.insert(0, f"➕ Дополнил «{a.duplicate_of}»")
        if a.links:
            lines.append("🔗 " + ", ".join(l.l2 + (f" → «{l.l3}»" if l.l3 else "") for l in a.links))
        else:
            lines.append("🔗 без связей с другими холстами")
        if a.unknown_tags:
            lines.append("⚠️ " + ", ".join(f"холст #{t} не нашёл" for t in a.unknown_tags))
        if idea.deep:
            lines.append("🔍 дополнено")
        return "\n".join(lines)
    if idea.status == "await_theme" and a and a.new_theme:
        text = f"🆕 Завести тему «{a.new_theme['name']}»?"
        closest = a.new_theme.get("closest_existing")
        if closest:
            text += f" Похоже на «{closest}», можно сразу туда."
        return text
    return {
        "new": "⏳ Разбираю…",
        "raw": f"📝 Сохранил без разбора ({idea.error}). Лежит в «Разное» с тегом #неразобрано.",
        "stt_failed": f"🎙 Не смог расшифровать голос ({idea.error}). Файл сохранён, можно повторить.",
        "await_dup": f"🤔 Похоже на «{a.duplicate_of if a else ''}». Дополнить её или завести новую?",
        "write_failed": f"⚠️ Не смог записать в vault ({idea.error}). Повторю сам через 5 минут.",
        "undone": "↩️ Отменено, из vault убрал.",
    }.get(idea.status, idea.status)
```

Добавить новую ветку в `card_kb()` перед `return InlineKeyboardMarkup(...)`:

```python
def card_kb(idea: Idea) -> InlineKeyboardMarkup | None:
    i = idea.id
    rows: list[list[B]] = []
    if idea.status == "saved" and idea.mode == "append":
        rows = [[B(text="↩️ Отменить", callback_data=f"un:{i}")]]
    elif idea.status == "saved":
        top = [B(text="🔁 Поменять холсты", callback_data=f"ch:{i}")]
        if not idea.deep:
            top.append(B(text="🔍 Копнуть глубже", callback_data=f"dp:{i}"))
        rows = [top, [B(text="↩️ Отменить", callback_data=f"un:{i}")]]
    elif idea.status == "raw":
        rows = [[B(text="🔄 Разобрать ещё раз", callback_data=f"rt:{i}"), B(text="↩️ Отменить", callback_data=f"un:{i}")]]
    elif idea.status == "stt_failed":
        rows = [[B(text="🔄 Повторить", callback_data=f"rt:{i}")]]
    elif idea.status == "await_dup":
        rows = [[B(text="➕ Дополнить", callback_data=f"du:{i}:a"), B(text="🆕 Новая", callback_data=f"du:{i}:n")]]
    elif idea.status == "await_theme" and idea.parsed and idea.parsed.new_theme:
        nt = idea.parsed.new_theme
        row = [B(text=f"🆕 Создать «{nt['name']}»", callback_data=f"nt:{i}:c")]
        if nt.get("closest_existing"):
            row.append(B(text=f"📂 Использовать «{nt['closest_existing']}»", callback_data=f"nt:{i}:u"))
        rows = [row, [B(text="📋 Другая тема", callback_data=f"nt:{i}:o")]]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
```

Добавить новую функцию в конец файла, после `l2_kb`:

```python
def theme_kb(idea_id: int, themes: list[str]) -> InlineKeyboardMarkup:
    rows = [[B(text=name, callback_data=f"th:{idea_id}:{n}")] for n, name in enumerate(themes)]
    rows.append([B(text="✖️ Назад", callback_data=f"bk2:{idea_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cards.py -v`
Expected: PASS, все тесты в файле зелёные

- [ ] **Step 5: Commit**

```bash
git add ideabot/cards.py tests/test_cards.py
git commit -m "$(cat <<'EOF'
feat: карточка и клавиатура await_theme, theme_kb

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: `bot.py` — обработчики кнопок

**Files:**
- Modify: `ideabot/bot.py`
- Test: нет нового модульного теста (роутер aiogram в этом проекте не покрыт юнит-тестами и для существующих кнопок, например `du:`/`rt:`, см. `tests/test_bot.py`; проверка только через полный прогон тестов и живую проверку в Задаче 7)

**Interfaces:**
- Consumes: `Pipeline.resolve_theme_create`, `Pipeline.resolve_theme_existing` (Task 4), `cards.theme_kb` (Task 5).

- [ ] **Step 1: Write the failing check**

Это модификация без нового юнит-теста (см. обоснование в Files выше). Сначала прогнать полный набор тестов и убедиться, что он зелёный до изменений:

Run: `uv run pytest -q`
Expected: PASS, все тесты зелёные (ничего из Task 6 ещё не добавлено, это контрольная точка перед правкой `bot.py`)

- [ ] **Step 2: Implement the handlers**

В `ideabot/bot.py`, заменить строку импорта карточек:

```python
from .cards import card_kb, card_text, l2_kb, theme_kb
```

Добавить новые обработчики в `build_router()`, после существующего `dup()` (`du:\d+:[an]$`) и перед `retry()`:

```python
    @r.callback_query(F.data.regexp(r"^nt:\d+:c$"))
    async def new_theme_create(c: CallbackQuery):
        await c.answer()
        await notifier.busy(_id(c), "🆕 Завожу тему…")
        pipeline.spawn(pipeline.resolve_theme_create(_id(c)))

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
        pipeline.spawn(pipeline.resolve_theme_existing(i, closest))

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
        pipeline.spawn(pipeline.resolve_theme_existing(i, names[n]))

    @r.callback_query(F.data.regexp(r"^bk2:\d+$"))
    async def theme_back(c: CallbackQuery):
        await notifier.refresh(_id(c))
        await c.answer()
```

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS, все тесты зелёные (новые обработчики не ломают импорт и не меняют поведение существующих кнопок)

- [ ] **Step 4: Import sanity check**

Run: `uv run python -c "import ideabot.__main__"`
Expected: импорт без ошибок (ловит опечатки в именах функций/регулярках, которые pytest не вызывает напрямую)

- [ ] **Step 5: Commit**

```bash
git add ideabot/bot.py
git commit -m "$(cat <<'EOF'
feat: обработчики кнопок для создания и выбора темы

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Живая проверка с Некитом и память

Ручная задача, делается вместе с Некитом на реальном боте `@cucaramba_idea_bot`. Код меняется только если проверка нашла баг: тогда сначала тест, воспроизводящий баг, потом фикс, потом новый коммит (не amend).

**Files:**
- Modify: `docs/plans/2026-09-26-idea-bot.md` (отметить находку задачи 11 закрытой), `~/Claude Code/idea-bot/HANDOFF.md`
- Modify: `/Users/cucaramba/.claude/projects/-Users-cucaramba-Claude-Code/memory/idea_bot_project.md` и `MEMORY.md` (если там нет этой детали)

- [ ] **Step 1: Перезапустить бота**

```bash
cd ~/"Claude Code"/idea-bot && ./scripts/setup.sh restart && sleep 2 && ./scripts/setup.sh status
```
Expected: `state = running`, новый `pid`.

- [ ] **Step 2: Сценарий «Создать»**

Некит пишет боту текст с явной просьбой создать новую тему и занести идею (например, снова про «Идеи для НАП» или любой новый пример). Проверить в Telegram: карточка `await_theme` с кнопками «Создать», (если есть похожая) «Использовать», «Другая тема». Нажать «Создать». Проверить в Obsidian: `config/themes.yaml` содержит новую тему перед `Разное`, в `Творческие идеи.canvas` появилась новая группа с этим названием и нодой идеи внутри, карточка в Telegram стала обычной `saved`.

- [ ] **Step 3: Сценарий «Использовать похожую»**

Повторить с другой идеей и другой явной просьбой создать тему, в этот раз нажать кнопку «Использовать «похожая»» (если бот предложил её). Проверить: новая тема в `config/themes.yaml` и на холсте не появилась, идея легла в предложенную похожую тему.

- [ ] **Step 4: Сценарий «Другая тема»**

Повторить ещё раз, на карточке `await_theme` нажать «Другая тема», выбрать любую существующую тему из списка. Проверить: идея легла в выбранную тему, новых тем не появилось.

- [ ] **Step 5: Запустить полный набор тестов ещё раз**

Run: `cd ~/"Claude Code"/idea-bot && uv run pytest -q`
Expected: все тесты зелёные (контрольная проверка перед финальным коммитом, на случай если в шагах 2-4 нашёлся и чинился баг)

- [ ] **Step 6: Обновить HANDOFF.md**

Добавить в `HANDOFF.md` раздел про эту фичу: дата, что сделано (новая тема по явной просьбе, спека `docs/specs/2026-10-10-idea-bot-new-theme-design.md`, план `docs/plans/2026-10-10-idea-bot-new-theme.md`), результат живой проверки трёх сценариев, отклонения от плана если были.

- [ ] **Step 7: Commit**

```bash
cd ~/"Claude Code"/idea-bot
git add HANDOFF.md
git commit -m "$(cat <<'EOF'
docs: результаты живой проверки новой темы по явной просьбе

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 8: Обновить память**

Проверить, упоминает ли `/Users/cucaramba/.claude/projects/-Users-cucaramba-Claude-Code/memory/idea_bot_project.md` эту фичу. Если нет и деталь не выводится из кода/HANDOFF, дополнить memory-файл одной строкой про то, что новая тема заводится только по явной просьбе и требует подтверждения кнопкой (не автоматически), со ссылкой `[[idea_bot_project]]` при необходимости.
