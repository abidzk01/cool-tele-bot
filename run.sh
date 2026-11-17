#!/usr/bin/env bash
# Telegram bot launcher: start, check, and maintain the bot without long commands.
#
#   ./run.sh            start the bot (foreground)
#   ./run.sh check      validate token + dependencies
#   ./run.sh push       push the "/" menu and description to Telegram
#   ./run.sh health     check the heartbeat (exit 0 healthy, 1 unhealthy)
#   ./run.sh sysinfo    print system info to the terminal
#   ./run.sh test       run the full test suite
#   ./run.sh update     upgrade yt-dlp and dependencies
#   ./run.sh help       show this help
set -euo pipefail
cd "$(dirname "$0")"

VENV=.venv
PY="$VENV/bin/python"

usage() {
  sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
}

ensure_deps() {
  if [ ! -x "$PY" ]; then
    echo "==> Creating virtualenv in $VENV"
    if command -v uv >/dev/null 2>&1; then
      uv venv --python 3.12 "$VENV"
    else
      python3 -m venv "$VENV"
    fi
  fi

  echo "==> Installing dependencies"
  if command -v uv >/dev/null 2>&1; then
    uv pip install --python "$PY" -r requirements.txt >/dev/null
  else
    "$PY" -m pip install --quiet --upgrade pip
    "$PY" -m pip install --quiet -r requirements.txt
  fi

  if [ ! -f .env ]; then
    echo "==> .env is missing; copied from .env.example (set BOT_TOKEN first!)"
    cp .env.example .env
  fi
  mkdir -p downloads tmp cookies
}

upgrade() {
  ensure_deps
  echo "==> Upgrading yt-dlp"
  if command -v uv >/dev/null 2>&1; then
    uv pip install --python "$PY" --upgrade yt-dlp
  else
    "$PY" -m pip install --quiet --upgrade yt-dlp
  fi
  echo "Done. Restart the bot so the new version is used."
}

case "${1:-run}" in
  help|-h|--help) usage ;;
  run)            ensure_deps; exec "$PY" bot.py ;;
  check)          ensure_deps; exec "$PY" bot.py --check ;;
  push)           ensure_deps; exec "$PY" bot.py --push-commands ;;
  health)         exec "$PY" bot.py --health ;;
  sysinfo)        ensure_deps; exec "$PY" bot.py --sysinfo ;;
  test)           ensure_deps; exec "$PY" -m unittest discover -s tests -v ;;
  update)         upgrade ;;
  *)              echo "Unknown command: $1" >&2; usage; exit 2 ;;
esac