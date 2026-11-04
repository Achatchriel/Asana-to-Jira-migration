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
    """Plik-podobny obiekt, który pisze jednocześnie do konsoli i do pliku,
    oraz (jeśli włączone) automatycznie przekazuje na Slack każdą PEŁNĄ
    linijkę zawierającą [BŁĄD] albo [OSTRZEŻENIE] - bez potrzeby ręcznego
    wołania czegokolwiek gdziekolwiek indziej w kodzie skryptów."""

    def __init__(self, *streams, forward_errors_to_slack: bool = False):
        self._streams = streams
        self._forward_errors_to_slack = forward_errors_to_slack
        self._line_buffer = ""  # print() woła write() OSOBNO dla treści i dla
        # "\n" na końcu - bez buforowania rozrywalibyśmy szukane frazy w połowie

    def write(self, data):
        for s in self._streams:
            s.write(data)
            s.flush()
        if self._forward_errors_to_slack:
            self._buffer_and_maybe_forward(data)

    def _buffer_and_maybe_forward(self, data: str) -> None:
        self._line_buffer += data
        while "\n" in self._line_buffer:
            line, self._line_buffer = self._line_buffer.split("\n", 1)
            stripped = line.strip()
            if "[BŁĄD]" in stripped or "[OSTRZEŻENIE]" in stripped:
                _forward_line_to_slack(stripped)

    def flush(self):
        for s in self._streams:
            s.flush()


def _forward_line_to_slack(line: str) -> None:
    """Wysyła POJEDYNCZĄ linijkę błędu/ostrzeżenia na Slack, natychmiast po
    jej wypisaniu. UWAGA: w razie niepowodzenia wysyłki, komunikat o tym
    idzie WPROST do sys.__stderr__ (z pominięciem _Tee) - inaczej sam ten
    komunikat (jako kolejne "[nie udało się...]") mógłby zostać złapany
    przez tę samą logikę i próbować wysłać SAM SIEBIE, w kółko."""
    if not SLACK_WEBHOOK_URL:
        return
    try:
        emoji = "🚨" if "[BŁĄD]" in line else "⚠️"
        resp = requests.post(SLACK_WEBHOOK_URL, json={"text": f"{emoji} {line}"}, timeout=15)
        if resp.status_code >= 300:
            print(f"[nie udało się przekazać linii na Slack: {resp.status_code}]", file=sys.__stderr__)
    except Exception as exc:  # noqa: BLE001
        print(f"[nie udało się przekazać linii na Slack: {exc}]", file=sys.__stderr__)


def setup_file_logging(script_name: str, forward_errors_to_slack: bool = True) -> Path:
    """Od tego momentu wszystko, co idzie na stdout/stderr (czyli każdy
    print()), trafia RÓWNIEŻ do pliku logs/<script_name>_<data-godzina>.log.
    Domyślnie (forward_errors_to_slack=True) każda linijka zawierająca
    [BŁĄD] albo [OSTRZEŻENIE] leci NATYCHMIAST na Slack, automatycznie -
    bez potrzeby wołania send_slack_summary() w tym celu (ta funkcja
    zostaje do ręcznego, KOŃCOWEGO podsumowania całego przebiegu, osobno).
    Zwraca ścieżkę do utworzonego pliku logu."""
    LOG_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"{script_name}_{timestamp}.log"
    log_file = open(log_path, "w", encoding="utf-8")

    sys.stdout = _Tee(sys.__stdout__, log_file, forward_errors_to_slack=forward_errors_to_slack)
    sys.stderr = _Tee(sys.__stderr__, log_file, forward_errors_to_slack=forward_errors_to_slack)

    print(f"[log zapisywany do: {log_path}]")
    if forward_errors_to_slack and SLACK_WEBHOOK_URL:
        print("[błędy/ostrzeżenia będą też przekazywane na Slack na bieżąco]")
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
