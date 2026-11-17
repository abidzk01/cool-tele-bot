#!/usr/bin/env bash
# Launcher bot Telegram: jalankan, cek, dan rawat tanpa perlu ingat perintah panjang.
#
#   ./run.sh            jalankan bot (foreground)
#   ./run.sh check      validasi token + dependency
#   ./run.sh push       dorong menu "/" & deskripsi ke Telegram
#   ./run.sh health     cek heartbeat (exit 0 sehat, 1 tidak)
#   ./run.sh sysinfo    cetak info sistem ke terminal
#   ./run.sh test       jalankan seluruh tes
#   ./run.sh update     perbarui yt-dlp & dependency
#   ./run.sh help       tampilkan bantuan ini
set -euo pipefail
cd "$(dirname "$0")"

VENV=.venv
PY="$VENV/bin/python"

usage() {
  sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
}

ensure_deps() {
  if [ ! -x "$PY" ]; then
    echo "==> Membuat virtualenv di $VENV"
    if command -v uv >/dev/null 2>&1; then
      uv venv --python 3.12 "$VENV"
    else
      python3 -m venv "$VENV"
    fi
  fi

  echo "==> Memasang dependency"
  if command -v uv >/dev/null 2>&1; then
    uv pip install --python "$PY" -r requirements.txt >/dev/null
  else
    "$PY" -m pip install --quiet --upgrade pip
    "$PY" -m pip install --quiet -r requirements.txt
  fi

  if [ ! -f .env ]; then
    echo "==> .env belum ada, menyalin dari .env.example (isi BOT_TOKEN dulu!)"
    cp .env.example .env
  fi
  mkdir -p downloads tmp cookies
}

upgrade() {
  ensure_deps
  echo "==> Memperbarui yt-dlp"
  if command -v uv >/dev/null 2>&1; then
    uv pip install --python "$PY" --upgrade yt-dlp
  else
    "$PY" -m pip install --quiet --upgrade yt-dlp
  fi
  echo "Selesai. Restart bot agar versi baru dipakai."
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
  *)              echo "Perintah tidak dikenal: $1" >&2; usage; exit 2 ;;
esac