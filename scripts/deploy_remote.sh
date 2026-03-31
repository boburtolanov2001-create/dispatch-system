#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${1:?app dir is required}"
SERVICE_NAME="${2:?service name is required}"

cd "$APP_DIR"

install_venv_package() {
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "python3 venv support is required, but apt-get is not available"
    exit 1
  fi

  local py_minor
  py_minor="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"

  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y "python${py_minor}-venv" python3-venv
}

if [ -d ".venv" ] && { [ ! -x ".venv/bin/python" ] || [ ! -x ".venv/bin/pip" ]; }; then
  rm -rf .venv
fi

if [ ! -d ".venv" ]; then
  if ! python3 -m venv .venv >/dev/null 2>&1; then
    rm -rf .venv
    install_venv_package
    python3 -m venv .venv
  fi
fi

if [ ! -x ".venv/bin/pip" ]; then
  .venv/bin/python -m ensurepip --upgrade
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
