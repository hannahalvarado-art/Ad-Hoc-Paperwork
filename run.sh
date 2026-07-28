#!/usr/bin/env bash
# Starts the API and the frontend together. Ctrl-C stops both.
set -euo pipefail
cd "$(dirname "$0")"

# Windows venvs put executables in Scripts/ rather than bin/, so resolve the
# name once and use it everywhere below. Note that on Windows `python3` is
# usually the App Store stub, which is why `python` is tried first.
if [ -d backend/.venv/Scripts ]; then VENV_BIN=Scripts; else VENV_BIN=bin; fi

if [ ! -d backend/.venv ]; then
  echo "Creating backend virtualenv..."
  if command -v python >/dev/null; then PY=python; else PY=python3; fi
  "$PY" -m venv backend/.venv
  if [ -d backend/.venv/Scripts ]; then VENV_BIN=Scripts; fi
  backend/.venv/$VENV_BIN/pip install -q -r backend/requirements.txt
fi

if [ ! -f backend/data/adhoc.db ]; then
  echo "Seeding database..."
  (cd backend && .venv/$VENV_BIN/python seed.py)
fi

if [ ! -d frontend/node_modules ]; then
  echo "Installing frontend dependencies..."
  (cd frontend && npm install)
fi

trap 'kill 0' EXIT
(cd backend && .venv/$VENV_BIN/uvicorn app.main:app --reload --port 8000) &
(cd frontend && npm run dev) &
wait
