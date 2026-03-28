#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${1:?app dir is required}"
SERVICE_NAME="${2:?service name is required}"

cd "$APP_DIR"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

if [ ! -f "tracked_drivers.json" ]; then
  printf '{}\n' > tracked_drivers.json
fi

if [ ! -f "user_assignments.json" ]; then
  printf '{}\n' > user_assignments.json
fi

if [ ! -f "users.json" ]; then
  printf '{}\n' > users.json
fi

PYTHONPYCACHEPREFIX=/tmp/dispatch-pyc .venv/bin/python -m py_compile app.py

sudo systemctl restart "$SERVICE_NAME"
sudo systemctl status "$SERVICE_NAME" --no-pager
