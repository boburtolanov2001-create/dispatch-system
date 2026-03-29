#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${1:?app dir is required}"
SERVICE_NAME="${2:?service name is required}"

cd "$APP_DIR"

if [ ! -d ".venv" ]; then
  if ! python3 -m venv --help >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then
      export DEBIAN_FRONTEND=noninteractive
      apt-get update
      apt-get install -y python3-venv
    else
      echo "python3-venv is required to create .venv"
      exit 1
    fi
  fi
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
