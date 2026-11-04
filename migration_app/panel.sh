#!/usr/bin/env bash
# Uruchamia panel migracji: http://127.0.0.1:8765 (inny port: ./migration_app/panel.sh --port 9000)
cd "$(dirname "$0")/.." || exit 1
exec .venv/bin/python migration_app/server.py "$@"
