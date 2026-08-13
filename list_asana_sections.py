#!/usr/bin/env python3
"""
list_asana_sections.py

Wypisuje wszystkie UNIKALNE nazwy sekcji użyte w danym projekcie Asany —
punkt wyjścia do zbudowania section_status_map.json (mapowania sekcja Asany
-> status Jira, używanego przez asana_jira_sync.py).

Użycie:
    python list_asana_sections.py "Dokładna nazwa projektu w Asanie"
    python list_asana_sections.py --link "https://app.asana.com/0/123456789/list"
"""
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core  # reużywamy normalize() i extract_asana_project_gid()

ASANA_TOKEN = os.environ["ASANA_TOKEN"]
ASANA_WORKSPACE_GID = os.environ["ASANA_WORKSPACE_GID"]
ASANA_HEADERS = {"Authorization": f"Bearer {ASANA_TOKEN}"}
ASANA_API = "https://app.asana.com/api/1.0"


def find_asana_project_gid(project_name: str) -> "str | None":
    url = f"{ASANA_API}/workspaces/{ASANA_WORKSPACE_GID}/projects"
    params = {"opt_fields": "name", "limit": 100}
    norm_target = core.normalize(project_name)
    offset = None
    while True:
        if offset:
            params["offset"] = offset
        resp = requests.get(url, headers=ASANA_HEADERS, params=params, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        for p in payload.get("data", []):
            if core.normalize(p["name"]) == norm_target:
                return p["gid"]
        offset = payload.get("next_page", {}).get("offset") if payload.get("next_page") else None
        if not offset:
            return None


def main() -> None:
    if len(sys.argv) < 2:
        print(
            'Użycie:\n'
            '  python list_asana_sections.py "Dokładna nazwa projektu w Asanie"\n'
            '  python list_asana_sections.py --link "https://app.asana.com/0/123456789/list"'
        )
        sys.exit(1)

    if sys.argv[1] == "--link":
        if len(sys.argv) < 3:
            print("Brak linku po --link.")
            sys.exit(1)
        gid = core.extract_asana_project_gid(sys.argv[2])
        if not gid:
            print(f"Nie rozpoznano formatu linku: {sys.argv[2]}")
            sys.exit(1)
        label = sys.argv[2]
    else:
        project_name = sys.argv[1]
        gid = find_asana_project_gid(project_name)
        if not gid:
            print(f"Nie znaleziono projektu '{project_name}' w Asanie.")
            sys.exit(1)
        label = project_name

    url = f"{ASANA_API}/projects/{gid}/sections"
    resp = requests.get(url, headers=ASANA_HEADERS, params={"opt_fields": "name"}, timeout=30)
    resp.raise_for_status()
    sections = [s["name"] for s in resp.json().get("data", [])]

    print(f"Sekcje w projekcie '{label}' ({len(sections)}):\n")
    for name in sections:
        print(f'  "{name}": "",')

    print(
        "\nSkopiuj powyższe do section_status_map.json i wpisz docelową nazwę "
        "statusu Jira po prawej stronie każdego wpisu (albo zostaw pusty string, "
        "żeby pominąć — zadanie zostanie wtedy przy domyślnym statusie startowym)."
    )


if __name__ == "__main__":
    main()
