#!/bin/zsh
# Idea Bot: автозапуск и управление.
#   scripts/setup.sh install | restart | stop | status | logs | uninstall
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.cucaramba.idea-bot"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PY="$ROOT/.venv/bin/python"
DOMAIN="gui/$(id -u)"

write_plist() {
  mkdir -p "$ROOT/logs"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>-m</string><string>ideabot</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:$HOME/.local/bin:$HOME/.npm-global/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
  <key>StandardOutPath</key><string>$ROOT/logs/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$ROOT/logs/launchd.err.log</string>
</dict>
</plist>
EOF
}

case "${1:-install}" in
  install)
    cd "$ROOT"
    [[ -f .env ]] || { cp .env.example .env; echo "Заполни BOT_TOKEN и OWNER_ID в $ROOT/.env и запусти install ещё раз"; exit 1; }
    [[ -x "$PY" ]] || uv sync
    write_plist
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    launchctl bootstrap "$DOMAIN" "$PLIST"
    echo "✅ Idea Bot установлен и запущен"
    ;;
  restart) launchctl kickstart -k "$DOMAIN/$LABEL" && echo "🔄 перезапущен" ;;
  stop) launchctl bootout "$DOMAIN/$LABEL" && echo "⏹ остановлен" ;;
  status) launchctl print "$DOMAIN/$LABEL" | grep -E "state|pid|last exit" ;;
  logs) tail -n 50 -f "$ROOT/logs/ideabot.log" ;;
  uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "🗑 автозапуск удалён, data/ не тронута"
    ;;
  *) echo "usage: $0 install|restart|stop|status|logs|uninstall"; exit 1 ;;
esac
