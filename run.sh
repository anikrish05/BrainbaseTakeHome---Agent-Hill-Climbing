#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo 'Create the Python environment first: uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt'
  exit 1
fi
if [ ! -f frontend/dist/index.html ]; then
  echo 'Build the frontend first: cd frontend && npm install && npm run build'
  exit 1
fi
exec .venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port "${PORT:-8000}" --no-access-log
