#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/Users/eduardobandeliz/Documents/Codex/2026-07-10/teng/work/github-check"
cd "$APP_DIR"

set -a
source "$APP_DIR/.env"
set +a

while true; do
  date '+[%Y-%m-%d %H:%M:%S] iniciando bot'
  PYTHONUNBUFFERED=1 "$APP_DIR/.venv/bin/python" "$APP_DIR/telegram_inventory_bot.py"
  status=$?
  date "+[%Y-%m-%d %H:%M:%S] bot salio con codigo $status; reiniciando en 5s"
  sleep 5
done
