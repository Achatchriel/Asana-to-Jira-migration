#!/usr/bin/env python3
"""
notify.py

Współdzielony moduł dla wszystkich skryptów w tym pakiecie:
1. setup_file_logging(script_name) — "podwaja" wszystko, co idzie do konsoli
   (print), zapisując to też do pliku logs/<script_name>_<data>.log. Nie
   trzeba zmieniać żadnego print() w istniejącym kodzie.
2. send_slack_summary(text) — wysyła wiadomość na Slack przez Incoming
   Webhook, jeśli SLACK_WEBHOOK_URL jest ustawiony w .env. Jeśli nie jest
   ustawiony, funkcja nic nie robi (cicho, bez błędu) — Slack jest opcjonalny.

E-mail celowo NIE jest tu zaimplementowany — wymagałby danych serwera SMTP
(host, port, użytkownik, hasło/app-password), których nie mieliśmy. Jeśli
wolisz e-mail zamiast/obok Slacka, daj znać dane SMTP, dopiszę to analogicznie
(ta sama zasada: cicho pomijane, jeśli nie skonfigurowane).

Użycie w innym skrypcie:
    from notify import setup_file_logging, send_slack_summary

    setup_file_logging("jira_sync")
    ... reszta skryptu, zwykłe print() ...
    send_slack_summary(f"jira_sync.py: utworzono {n} projektów, {m} błędów.")
"""
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "").strip()
LOG_DIR = Path("logs")


class _Tee:
    """Plik-podobny obiekt, który pisze jednocześnie do konsoli i do pliku."""

    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for s in self._streams:
            s.write(data)
            s.flush()

    def flush(self):
        for s in self._streams:
            s.flush()


def setup_file_logging(script_name: str) -> Path:
    """Od tego momentu wszystko, co idzie na stdout/stderr (czyli każdy
    print()), trafia RÓWNIEŻ do pliku logs/<script_name>_<data-godzina>.log.
    Zwraca ścieżkę do utworzonego pliku logu."""
    LOG_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"{script_name}_{timestamp}.log"
    log_file = open(log_path, "w", encoding="utf-8")

    sys.stdout = _Tee(sys.__stdout__, log_file)
    sys.stderr = _Tee(sys.__stderr__, log_file)

    print(f"[log zapisywany do: {log_path}]")
    return log_path


def send_slack_summary(text: str) -> None:
    """Wysyła wiadomość na Slack przez Incoming Webhook. Bez ustawionego
    SLACK_WEBHOOK_URL w .env - nic nie robi (cicho, żeby nie wymagać Slacka
    od wszystkich)."""
    if not SLACK_WEBHOOK_URL:
        return
    try:
        resp = requests.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=15)
        if resp.status_code >= 300:
            print(f"[OSTRZEŻENIE] Nie udało się wysłać podsumowania na Slack: {resp.status_code} {resp.text}")
    except Exception as exc:  # noqa: BLE001
        print(f"[OSTRZEŻENIE] Nie udało się wysłać podsumowania na Slack: {exc}")
