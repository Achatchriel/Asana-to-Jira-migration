#!/usr/bin/env bash
# Instaluje wszystko, czego potrzeba (przy pierwszym uruchomieniu), i otwiera panel migracji.
#   ./start.sh               -> http://127.0.0.1:8765
#   ./start.sh --port 9000   -> inny port
# Zmienne: PYTHON=python3.12 (inny interpreter), NO_BROWSER=1 (nie otwieraj przeglądarki).
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "Nie znaleziono $PY. Zainstaluj Pythona 3.10+ (albo wskaż go: PYTHON=/sciezka/python ./start.sh)." >&2
  exit 1
fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "Potrzebny Python 3.10 lub nowszy (jest: $("$PY" --version 2>&1))." >&2
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "==> Tworzę środowisko .venv"
  "$PY" -m venv .venv
fi

REQ_HASH="$(sha256sum requirements.txt | cut -d' ' -f1)"
if [ "$(cat .venv/.requirements.sha 2>/dev/null || true)" != "$REQ_HASH" ]; then
  echo "==> Instaluję zależności (requirements.txt)"
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -r requirements.txt
  echo "$REQ_HASH" > .venv/.requirements.sha
fi

if [ ! -f .venv/.playwright_ok ]; then
  echo "==> Pobieram przeglądarkę dla Playwright (kroki 2-3: kolumny i statusy tablic)"
  if .venv/bin/python -m playwright install chromium; then
    touch .venv/.playwright_ok
  else
    echo "    Nie udało się pobrać przeglądarki - panel działa, ale kroki 2-3 nie zadziałają." >&2
    echo "    Na Linuksie mogą brakować bibliotek systemowych: sudo .venv/bin/python -m playwright install-deps chromium" >&2
  fi
fi

[ -f .env ] || echo "==> Brak pliku .env - uzupełnisz go w panelu (zakładka Start)."

PORT=8765
for ((i = 1; i <= $#; i++)); do
  if [ "${!i}" = "--port" ]; then j=$((i + 1)); PORT="${!j}"; fi
done
if [ -z "${NO_BROWSER:-}" ]; then
  ( sleep 1.5
    URL="http://127.0.0.1:$PORT"
    if command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1
    elif command -v open >/dev/null 2>&1; then open "$URL"; fi ) &
fi

exec .venv/bin/python migration_app/server.py "$@"
