#!/usr/bin/env python3
"""
check_required_fields.py

Pokazuje, jakie pola są WYMAGANE przy tworzeniu zadania danego typu w danym
projekcie Jira — i jakie mają dozwolone wartości (jeśli to pole wyboru).
Przydatne, gdy tworzenie zadania kończy się błędem w stylu:
  "customfield_XXXXX": "Type is required."

Użycie:
    python check_required_fields.py KLUCZPROJEKTU
    python check_required_fields.py KLUCZPROJEKTU "Nazwa typu zadania"
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
        print("Użycie: python check_required_fields.py KLUCZPROJEKTU [\"Nazwa typu zadania\"]")
        sys.exit(1)

    project_key = sys.argv[1]
    issue_type_name = sys.argv[2] if len(sys.argv) > 2 else "Task"

    url = f"{JIRA_BASE_URL}/rest/api/3/issue/createmeta"
    params = {
        "projectKeys": project_key,
        "issuetypeNames": issue_type_name,
        "expand": "projects.issuetypes.fields",
    }
    resp = requests.get(url, auth=AUTH, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    projects = data.get("projects", [])
    if not projects:
        print(f"Nie znaleziono projektu '{project_key}' albo typu zadania '{issue_type_name}'.")
        sys.exit(1)

    issue_types = projects[0].get("issuetypes", [])
    if not issue_types:
        print(f"Projekt '{project_key}' nie ma typu zadania '{issue_type_name}'. Dostępne typy:")
        # Pobierz listę dostępnych typów bez filtra issuetypeNames
        resp2 = requests.get(
            f"{JIRA_BASE_URL}/rest/api/3/issue/createmeta",
            auth=AUTH, params={"projectKeys": project_key, "expand": "projects.issuetypes"},
            timeout=30,
        )
        resp2.raise_for_status()
        for it in resp2.json().get("projects", [{}])[0].get("issuetypes", []):
            print(f"  - {it['name']}")
        sys.exit(1)

    fields = issue_types[0].get("fields", {})
    print(f"Pola dla typu '{issue_type_name}' w projekcie '{project_key}':\n")

    required_fields = {k: v for k, v in fields.items() if v.get("required")}
    print(f"WYMAGANE pola ({len(required_fields)}):")
    for field_id, field_info in required_fields.items():
        name = field_info.get("name", field_id)
        print(f"\n  {field_id} — '{name}'")
        allowed = field_info.get("allowedValues")
        if allowed:
            print("    Dozwolone wartości:")
            for val in allowed:
                label = val.get("value") or val.get("name") or val.get("id")
                print(f"      - id={val.get('id')!r}  value={label!r}")
        else:
            schema = field_info.get("schema", {})
            print(f"    Typ pola: {schema.get('type')} (schema: {schema})")


if __name__ == "__main__":
    main()
