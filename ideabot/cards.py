"""Тексты карточек и клавиатуры. Без длинного тире."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton as B
from aiogram.types import InlineKeyboardMarkup

from .db import Idea

ICON = {"бизнес": "💼", "контент": "🎬", "мысль": "🧠", "прочее": "🎲"}


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
        "queued": "⏳ Записываю в vault (до 5 минут)…",
        "raw": f"📝 Сохранил без разбора ({idea.error}). Лежит в «Разное» с тегом #неразобрано.",
        "stt_failed": f"🎙 Не смог расшифровать голос ({idea.error}). Файл сохранён, можно повторить.",
        "await_dup": f"🤔 Похоже на «{a.duplicate_of if a else ''}». Дополнить её или завести новую?",
        "write_failed": f"⚠️ Не смог записать в vault ({idea.error}). Повторю сам через 5 минут.",
        "undone": "↩️ Отменено, из vault убрал.",
    }.get(idea.status, idea.status)


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


def l2_kb(idea_id: int, names: list[str], selected: set[int]) -> InlineKeyboardMarkup:
    rows = [[B(text=("✅ " if n in selected else "") + name, callback_data=f"tg:{idea_id}:{n}")]
            for n, name in enumerate(names)]
    rows.append([B(text="✅ Готово", callback_data=f"ok:{idea_id}"), B(text="✖️ Назад", callback_data=f"bk:{idea_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def theme_kb(idea_id: int, themes: list[str]) -> InlineKeyboardMarkup:
    rows = [[B(text=name, callback_data=f"th:{idea_id}:{n}")] for n, name in enumerate(themes)]
    rows.append([B(text="✖️ Назад", callback_data=f"bk2:{idea_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
