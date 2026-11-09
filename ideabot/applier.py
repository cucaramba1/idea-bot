"""Ноут: `python -m ideabot.applier` забирает пакеты записи с сервера и применяет к настоящему vault (launchd раз в 5 минут)."""
from __future__ import annotations

import logging
import time

from .config import load_settings
from .db import DB
from .packets import run_applier


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = load_settings()
    print(time.strftime("%F %T"), run_applier(s, DB(s.data_dir / "applier.db")), flush=True)


if __name__ == "__main__":
    main()
