#!/usr/bin/env python3
"""
show_template_board_columns.py

Wypisuje konfigurację kolumn tablicy przypisanej do projektu wzorcowego
(JIRA_TEMPLATE_PROJECT_KEY z .env, albo podany jako argument).

Jira NIE udostępnia publicznego API do ustawiania kolumn tablicy — to jedyny
element konfiguracji projektu, którego nie da się skopiować automatycznie
(patrz README, sekcja "Kopiowanie konfiguracji z projektu wzorcowego").
Ten skrypt nie naprawia tego ograniczenia, tylko wypisuje gotową "ściągawkę",
żeby ręczne odtworzenie kolumn na nowej tablicy (Board -> Configure -> Columns)
zajęło dosłownie chwilę zamiast zgadywania.

Użycie:
    python show_template_board_columns.py                    # użyje JIRA_TEMPLATE_PROJECT_KEY z .env
    python show_template_board_columns.py INNYKLUCZ           # dla podanego klucza projektu
    python show_template_board_columns.py INNYKLUCZ 12345     # z jawnym ID tablicy (gdy projekt ma ich kilka)
"""
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)

JIRA_BASE_URL = os.environ["JIRA_BASE_URL"].rstrip("/")
JIRA_EMAIL = os.environ["JIRA_EMAIL"]
JIRA_API_TOKEN = os.environ["JIRA_API_TOKEN"]
AUTH = (JIRA_EMAIL, JIRA_API_TOKEN)


def find_board_id_for_project(project_key: str) -> int:
    """Zwraca ID tablicy dla projektu. Jeśli projekt ma WIĘCEJ NIŻ JEDNĄ tablicę,
    nie zgaduje — wypisuje kandydatów i każe podać ID jawnie jako 2. argument
    (bo wzięcie niewłaściwej tablicy dałoby zły wzorzec kolumn)."""
    url = f"{JIRA_BASE_URL}/rest/agile/1.0/board"
    resp = requests.get(url, auth=AUTH, params={"projectKeyOrId": project_key}, timeout=30)
    resp.raise_for_status()
    values = resp.json().get("values", [])
    if not values:
        raise RuntimeError(f"Nie znaleziono żadnej tablicy dla projektu '{project_key}'.")
    if len(values) > 1:
        print(f"UWAGA: projekt '{project_key}' ma {len(values)} tablic — podaj ID jawnie jako 2. argument:")
        for b in values:
            print(f"  - id {b['id']}: '{b['name']}'")
        print(f"\npython show_template_board_columns.py {project_key} <id>")
        sys.exit(1)
    return values[0]["id"]


def main() -> None:
    project_key = sys.argv[1] if len(sys.argv) > 1 else os.getenv("JIRA_TEMPLATE_PROJECT_KEY", "").strip()
    if not project_key:
        print("Podaj klucz projektu jako argument, albo ustaw JIRA_TEMPLATE_PROJECT_KEY w .env.")
        sys.exit(1)

    board_id = int(sys.argv[2]) if len(sys.argv) > 2 else find_board_id_for_project(project_key)
    url = f"{JIRA_BASE_URL}/rest/agile/1.0/board/{board_id}/configuration"
    resp = requests.get(url, auth=AUTH, timeout=30)
    resp.raise_for_status()
    config = resp.json()

    columns = config.get("columnConfig", {}).get("columns", [])
    print(f"Tablica projektu wzorcowego '{project_key}' (board ID {board_id}):\n")
    for i, col in enumerate(columns, start=1):
        statuses = ", ".join(s["name"] if isinstance(s, dict) and "name" in s else str(s) for s in col.get("statuses", []))
        print(f"  {i}. Kolumna: {col.get('name')!r}")
        print(f"     Statusy w tej kolumnie: {statuses or '(brak)'}")

    constraint = config.get("columnConfig", {}).get("constraintType")
    if constraint:
        print(f"\nTyp ograniczenia WIP: {constraint}")

    print(
        "\nOtwórz nową tablicę -> Board -> Configure -> Columns i odtwórz powyższy "
        "układ (przeciągnij statusy do odpowiednich kolumn, dodaj brakujące kolumny)."
    )


if __name__ == "__main__":
    main()
