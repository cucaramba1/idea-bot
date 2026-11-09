"""Пакеты записи в vault: сервер разбирает идею, ноут пишет теми же Writer и add_theme, ответ (ack) возвращается.

Сервер (APPLY_MODE=packet): queue() кладёт запрос в data/outbox, идея получает статус queued, read_acks() применяет ответы.
Ноут: apply_inbox() по порядку номеров вызывает настоящий Writer, журнал отката лежит в data/applier.db (копия старой
ideas.db, поэтому отмена старых идей работает). Повторная доставка безопасна: data/applied хранит ответ на каждый ключ.
Всегда инициирует ноут (ssh/rsync наружу), сервер к ноуту не подключается. Образец: reels-radar/reels_radar/packets.py."""
from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
from dataclasses import asdict
from pathlib import Path

from .brain import clean_links
from .config_themes import add_theme
from .db import DB, IDEA_COLS
from .models import Link
from .vault.index import build_index
from .vault.sync import Writer

log = logging.getLogger(__name__)
ACK_FIELDS = ("status", "mode", "analysis", "note_path", "append_to", "append_heading", "deep", "error")
QUEUED_TEXT = "⏳ Записываю в vault (до 5 минут)…"


def packet_mode() -> bool:
    return os.environ.get("APPLY_MODE") == "packet"


def _atomic_json(path: Path, obj: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), "utf-8")
    os.replace(tmp, path)  # ноут не должен увидеть половину файла


# --- сервер ----------------------------------------------------------------------------------------------------

def queue(s, db: DB, idea_id: int, kind: str, *, hold: bool = True, **params) -> str:
    """kind: save | raw | relink | deep | undo. hold=True: идея в статусе queued до ответа, при ошибке вернём прежний.
    Имя файла начинается с номера, ноут применяет строго по порядку (undo не обгонит save)."""
    idea = db.get(idea_id)
    seq = int(db.kv_get("pkseq", "0")) + 1
    db.kv_set("pkseq", seq)
    key = f"{seq:08d}-{kind}-{idea_id}"
    out = Path(s.data_dir) / "outbox"
    out.mkdir(parents=True, exist_ok=True)
    _atomic_json(out / f"{key}.json", {"key": key, "kind": kind, "idea_id": idea_id, "params": params,
                                       "prev_status": idea.status, "row": {k: asdict(idea)[k] for k in IDEA_COLS}})
    if hold:
        db.update(idea_id, status="queued")
    return key


def read_acks(s, db: DB) -> list[dict]:
    """Ответы ноута -> база сервера. События для бота: done | failed (после них надо перерисовать карточку)."""
    events: list[dict] = []
    acks = Path(s.data_dir) / "acks"
    if not acks.exists():
        return events
    done_dir, out = acks / "done", Path(s.data_dir) / "outbox"
    for f in sorted(acks.glob("*.json")):
        try:
            ack = json.loads(f.read_text("utf-8"))
        except ValueError:
            log.error("битый ack %s, пропускаю", f.name)
            continue
        key, idea_id = ack["key"], ack["idea_id"]
        if db.get(idea_id) is not None and db.kv_get(f"pkdone:{key}") is None:
            if ack["ok"]:
                keep = ("note_path",) if ack["kind"] == "raw" else ACK_FIELDS  # сырую заметку не путаем с новым статусом идеи
                db.update(idea_id, **{k: v for k, v in ack["fields"].items() if k in keep})
                events.append({"type": "done", "idea_id": idea_id, "kind": ack["kind"], "params": ack.get("params") or {}})
            else:
                if db.get(idea_id).status == "queued":
                    db.update(idea_id, status=ack.get("prev_status") or "saved", error=ack.get("error", "")[:300])
                events.append({"type": "failed", "idea_id": idea_id, "kind": ack["kind"], "error": ack.get("error", "")})
            db.kv_set(f"pkdone:{key}", "1")
        (out / f"{key}.json").unlink(missing_ok=True)  # ноут ответил, запрос больше не нужен
        done_dir.mkdir(exist_ok=True)
        os.replace(f, done_dir / f.name)
    return events


# --- ноут ------------------------------------------------------------------------------------------------------

def _fields(db: DB, idea_id: int) -> dict:
    row = {k: asdict(db.get(idea_id))[k] for k in ACK_FIELDS}
    return row


def apply_packet(s, db: DB, packet: dict) -> dict:
    """Пакет -> ack. Ошибки записи не бросает, а возвращает в ack (ok=False), чтобы сервер показал их Некиту."""
    key, kind, idea_id = packet["key"], packet["kind"], packet["idea_id"]
    base = {"key": key, "kind": kind, "idea_id": idea_id, "prev_status": packet.get("prev_status"),
            "params": {k: v for k, v in (packet.get("params") or {}).items() if k == "after"}}
    try:
        db.sync_row(packet["row"])
        w, p = Writer(s, db), packet.get("params") or {}
        idea = db.get(idea_id)
        a, inc = idea.parsed, idea.incoming
        if kind == "raw":
            if not idea.note_path:
                w.save_raw(idea_id, inc)
        elif kind == "save":
            if a.theme not in s.themes:
                add_theme(s, a.theme)  # тема, созданная кнопкой «Создать», появилась на сервере; тут заводим на ноуте
            if idea.note_path:
                w.undo(idea_id)  # хвосты сырой или недописанной записи
            index = build_index(s)
            a.links = clean_links([asdict(link) for link in a.links], index)
            append = idea.mode == "append" and (s.ideas_dir / f"{a.duplicate_of}.md").exists()
            if append:
                w.append(idea_id, a.duplicate_of, a, inc, index)
            else:
                w.save_new(idea_id, a, inc, index)
            db.update(idea_id, status="saved", mode="append" if append else "new",
                      analysis=json.dumps(a.to_dict(), ensure_ascii=False), error=None)
        elif kind == "relink":
            links = [Link(**x) for x in p["links"]]
            w.relink(idea_id, links, build_index(s))
            a.links = links
            db.update(idea_id, status="saved", analysis=json.dumps(a.to_dict(), ensure_ascii=False))
        elif kind == "deep":
            w.add_deep(idea_id, p["md"])
            db.update(idea_id, status="saved")
        elif kind == "undo":
            w.undo(idea_id)
            db.update(idea_id, status="undone")
        else:
            return {**base, "ok": False, "error": f"неизвестный тип пакета: {kind}"}
        return {**base, "ok": True, "fields": _fields(db, idea_id)}
    except Exception as e:  # noqa: BLE001 диск, права, битый холст: сервер покажет текст, кнопки вернутся
        log.exception("apply %s", key)
        return {**base, "ok": False, "error": f"{type(e).__name__}: {e}"}


def apply_inbox(s, db: DB) -> list[str]:
    """Пакеты из data/inbox по порядку номеров -> data/acks_out/<key>.json. Журнал data/applied страхует от повтора."""
    data = Path(s.data_dir)
    inbox, acks_out, ledger = data / "inbox", data / "acks_out", data / "applied"
    acks_out.mkdir(parents=True, exist_ok=True)
    ledger.mkdir(parents=True, exist_ok=True)
    done = []
    for f in sorted(inbox.glob("*.json")) if inbox.exists() else []:
        try:
            packet = json.loads(f.read_text("utf-8"))
        except ValueError:
            log.error("битый пакет %s, пропускаю", f.name)
            continue
        key = packet["key"]
        if (ledger / f"{key}.json").exists():
            ack = json.loads((ledger / f"{key}.json").read_text("utf-8"))  # уже писали: тот же ответ, vault не трогаем
        else:
            ack = apply_packet(s, db, packet)
            if ack["ok"]:
                _atomic_json(ledger / f"{key}.json", ack)
        _atomic_json(acks_out / f"{key}.json", ack)
        done.append(key)
    return done


def run_applier(s, db: DB, run=subprocess.run) -> dict:
    """Ноут: забрать пакеты с сервера, применить, отправить ответы. launchd раз в 5 минут."""
    rep: dict = {"applied": [], "skipped": None}
    host, key, root = os.environ.get("REMOTE_HOST"), os.environ.get("REMOTE_KEY"), os.environ.get("REMOTE_ROOT", "idea-bot")
    if not host or not key:
        rep["skipped"] = "REMOTE_HOST/REMOTE_KEY не заданы"
        return rep
    ssh = f"ssh -i {shlex.quote(key)} -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=10"
    data = Path(s.data_dir)

    def rsync(*args: str):
        return run(["rsync", "-a", "-e", ssh, *args], capture_output=True, text=True)

    r = run([*shlex.split(ssh), host, f"mkdir -p {root}/data/acks {root}/data/outbox"], capture_output=True, text=True)
    if r.returncode != 0:
        rep["skipped"] = f"сервер недоступен: {r.stderr.strip()[-120:]}"
        return rep
    (data / "inbox").mkdir(parents=True, exist_ok=True)
    r = rsync("--delete", "--exclude=*.tmp", f"{host}:{root}/data/outbox/", f"{data / 'inbox'}/")
    if r.returncode != 0:
        rep["skipped"] = f"не забрал пакеты (rsync {r.returncode}): {r.stderr.strip()[-120:]}"
        return rep
    rep["applied"] = apply_inbox(s, db)
    out = data / "acks_out"
    if out.exists() and any(out.glob("*.json")):
        r = rsync("--remove-source-files", f"{out}/", f"{host}:{root}/data/acks/")
        if r.returncode != 0:  # ответы остаются на диске, уйдут в следующий раз; записанное повторно не запишется
            log.error("ответы не ушли (rsync %s): %s", r.returncode, r.stderr.strip()[-200:])
    return rep
