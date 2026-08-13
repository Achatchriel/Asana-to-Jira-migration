#!/usr/bin/env python3
"""
list_project_statuses.py

Wypisuje wszystkie statusy FAKTYCZNIE dostępne w danym projekcie Jira (dla
typu zadania "Task") — przydatne, gdy section_status_map.json odwołuje się
do statusu, którego dany projekt nie ma (różne projekty, nawet z tego
samego wzorca, mogą mieć nieco inny zestaw statusów, jeśli kopiowanie
konfiguracji w jira_sync.py nie przebiegło dla któregoś w pełni bezbłędnie).

Użycie:
    python list_project_statuses.py KLUCZPROJEKTU
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


def main() -> None:
    if len(sys.argv) < 2:
        print("Użycie: python list_project_statuses.py KLUCZPROJEKTU")
        sys.exit(1)

    project_key = sys.argv[1]
    url = f"{JIRA_BASE_URL}/rest/api/3/project/{project_key}/statuses"
    resp = requests.get(url, auth=AUTH, timeout=30)
    resp.raise_for_status()

    for issue_type in resp.json():
        print(f"\nTyp zadania: {issue_type['name']}")
        for status in issue_type.get("statuses", []):
            print(f"  - '{status['name']}'  (id={status['id']})")


if __name__ == "__main__":
    main()
