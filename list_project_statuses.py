#!/usr/bin/env python3
"""
list_project_statuses.py

Wypisuje wszystkie statusy FAKTYCZNIE dostępne w danym projekcie Jira (dla
każdego typu zadania) — przydatne, gdy section_status_map.json odwołuje się
do statusu, którego dany projekt nie ma (różne projekty, nawet z tego
samego wzorca, mogą mieć nieco inny zestaw statusów, jeśli kopiowanie
konfiguracji w jira_sync.py nie przebiegło dla któregoś w pełni bezbłędnie).

Projekt można podać kluczem albo dowolnym linkiem z Jiry, który go zawiera
(tablica, lista zadań, ustawienia, konkretne zgłoszenie).

Użycie:
    python list_project_statuses.py KLUCZPROJEKTU
    python list_project_statuses.py "https://twoja-instancja.atlassian.net/jira/software/c/projects/PROJ2/boards/123"
    python list_project_statuses.py "https://twoja-instancja.atlassian.net/browse/PROJ2-143"
"""
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)

JIRA_BASE_URL = os.environ["JIRA_BASE_URL"].rstrip("/")
JIRA_EMAIL = os.environ["JIRA_EMAIL"]
JIRA_API_TOKEN = os.environ["JIRA_API_TOKEN"]
AUTH = (JIRA_EMAIL, JIRA_API_TOKEN)

KEY_RE = r"[A-Za-z][A-Za-z0-9_]+"


def project_key_from(arg: str) -> "str | None":
    """Klucz projektu z klucza albo z linku Jiry."""
    arg = arg.strip()
    if not arg.lower().startswith("http"):
        return arg.upper() if re.fullmatch(KEY_RE, arg) else None
    url = urlparse(arg)
    patterns = [
        rf"/projects/({KEY_RE})(?:/|$)",           # .../jira/software/c/projects/KLUCZ/boards/1, .../projects/KLUCZ/settings
        rf"/browse/({KEY_RE})-\d+",                # .../browse/KLUCZ-123 (zgłoszenie)
        rf"/browse/({KEY_RE})(?:/|$)",             # .../browse/KLUCZ (stary link do projektu)
    ]
    for pattern in patterns:
        m = re.search(pattern, url.path)
        if m:
            return m.group(1).upper()
    query = parse_qs(url.query)
    for param in ("selectedIssue", "projectKey", "project"):
        value = (query.get(param) or [""])[0]
        m = re.match(rf"({KEY_RE})(?:-\d+)?$", value)
        if m:
            return m.group(1).upper()
    return None


def main() -> None:
    if len(sys.argv) < 2:
        print("Użycie: python list_project_statuses.py KLUCZPROJEKTU|LINK")
        sys.exit(1)

    project_key = project_key_from(sys.argv[1])
    if not project_key:
        print(f"[BŁĄD] Nie rozpoznano klucza projektu w: {sys.argv[1]}")
        sys.exit(1)

    project = requests.get(f"{JIRA_BASE_URL}/rest/api/3/project/{project_key}", auth=AUTH, timeout=30)
    if project.status_code != 200:
        print(f"[BŁĄD] Projekt '{project_key}' nie istnieje albo brak do niego dostępu ({project.status_code}).")
        sys.exit(1)
    print(f"Projekt: {project.json().get('name')} ({project_key})")
    print(f"Link:    {JIRA_BASE_URL}/browse/{project_key}")

    resp = requests.get(f"{JIRA_BASE_URL}/rest/api/3/project/{project_key}/statuses", auth=AUTH, timeout=30)
    resp.raise_for_status()

    for issue_type in resp.json():
        print(f"\nTyp zadania: {issue_type['name']}")
        for status in issue_type.get("statuses", []):
            print(f"  - '{status['name']}'  (id={status['id']})")


if __name__ == "__main__":
    main()
