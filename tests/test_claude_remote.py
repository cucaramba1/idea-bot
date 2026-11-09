"""Обёртка scripts/claude-remote.sh: сервер по SSH, запасной локальный запуск, код выхода."""
import json
import os
import stat
import subprocess
from pathlib import Path

WRAP = Path(__file__).resolve().parents[2] / "tools" / "claude-remote" / "claude-remote.sh"


def script(path: Path, body: str) -> str:
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def run(tmp_path, ssh_body: str, args: list[str], stdin="промпт", extra_env=None):
    ssh = script(tmp_path / "ssh", ssh_body)
    local = script(tmp_path / "claude", 'echo LOCAL "$@"; cat\n')
    env = {**os.environ, "CLAUDE_REMOTE_SSH": ssh, "CLAUDE_LOCAL_BIN": local, **(extra_env or {})}
    return subprocess.run([str(WRAP), *args], input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)


def test_goes_to_server_with_quoted_args_and_stdin(tmp_path):
    # поддельный ssh сохраняет удалённую команду (последний аргумент); LC_ALL=C = худший случай под launchd
    out = tmp_path / "cmd.txt"
    r = run(tmp_path, f'printf %s "${{@: -1}}" > {out}; cat\n', ["-p", "--system-prompt", "строка 1\nстрока 'два'"],
            extra_env={"LC_ALL": "C"})
    assert r.returncode == 0 and "LOCAL" not in r.stdout and "промпт" in r.stdout
    # удалённый bash должен разобрать команду ровно в исходные аргументы
    got = subprocess.run(["bash", "-c", f"eval \"set -- $(cat {out})\"; printf '%s|' \"$@\""], capture_output=True)
    assert got.stdout.decode("utf-8").endswith("/claude|-p|--system-prompt|строка 1\nстрока 'два'|")


def test_exit_code_of_remote_is_passed(tmp_path):
    assert run(tmp_path, "cat >/dev/null; exit 3\n", ["-p"]).returncode == 3


def test_ssh_down_falls_back_to_local(tmp_path):
    r = run(tmp_path, "exit 255\n", ["-p", "--model", "sonnet"])
    assert r.returncode == 0 and "LOCAL -p --model sonnet" in r.stdout and "промпт" in r.stdout


def test_photos_run_locally_without_ssh(tmp_path):
    r = run(tmp_path, "echo SSH_CALLED; exit 0\n", ["-p", "--add-dir", "/tmp/x"])
    assert "SSH_CALLED" not in r.stdout and "LOCAL -p --add-dir /tmp/x" in r.stdout
