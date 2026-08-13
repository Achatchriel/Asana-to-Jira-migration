#!/usr/bin/env python3
"""
check_task_section.py

Diagnostyka: sprawdza sekcję zadania Asany DWOMA różnymi sposobami:
1. Przez listę zadań projektu (/projects/{gid}/tasks) - tego używa asana_jira_sync.py.
2. Przez bezpośrednie zapytanie o TO KONKRETNE zadanie (/tasks/{task_gid}).

Jeśli te dwa źródła dają RÓŻNE wyniki, to dowód na to, że endpoint listy
zwraca nieaktualne dane (jakiś wewnętrzny cache/indeks po stronie Asany).

Użycie:
    python check_task_section.py <task_gid> <project_gid>

Przykład (na podstawie ostatniego logu):
    python check_task_section.py 1216988554374932 1216984763687260
"""
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)

ASANA_TOKEN = os.environ["ASANA_TOKEN"]
ASANA_HEADERS = {"Authorization": f"Bearer {ASANA_TOKEN}"}
ASANA_API = "https://app.asana.com/api/1.0"


def main() -> None:
    if len(sys.argv) < 3:
        print("Użycie: python check_task_section.py <task_gid> <project_gid>")
        sys.exit(1)

    task_gid = sys.argv[1]
    project_gid = sys.argv[2]

    print("=" * 60)
    print("METODA 1: przez listę zadań projektu (/projects/{gid}/tasks)")
    print("=" * 60)
    url = f"{ASANA_API}/projects/{project_gid}/tasks"
    resp = requests.get(
        url, headers=ASANA_HEADERS,
        params={"opt_fields": "name,memberships.section.name,memberships.project.gid", "limit": 100},
        timeout=30,
    )
    resp.raise_for_status()
    found = False
    for t in resp.json().get("data", []):
        if t["gid"] == task_gid:
            found = True
            print(f"Zadanie: {t.get('name')!r}")
            print(f"Memberships: {t.get('memberships')}")
    if not found:
        print("Zadanie NIE występuje na liście tego projektu (!).")

    print()
    print("=" * 60)
    print("METODA 2: przez bezpośrednie zapytanie o zadanie (/tasks/{task_gid})")
    print("=" * 60)
    url2 = f"{ASANA_API}/tasks/{task_gid}"
    resp2 = requests.get(
        url2, headers=ASANA_HEADERS,
        params={"opt_fields": "name,memberships.section.name,memberships.project.gid"},
        timeout=30,
    )
    resp2.raise_for_status()
    data2 = resp2.json()["data"]
    print(f"Zadanie: {data2.get('name')!r}")
    print(f"Memberships: {data2.get('memberships')}")

    print()
    print("=" * 60)
    print("WYNIK:")
    m1 = None
    for t in resp.json().get("data", []):
        if t["gid"] == task_gid:
            for m in t.get("memberships", []):
                if m.get("project", {}).get("gid") == project_gid:
                    m1 = (m.get("section") or {}).get("name")
    m2 = None
    for m in data2.get("memberships", []):
        if m.get("project", {}).get("gid") == project_gid:
            m2 = (m.get("section") or {}).get("name")

    print(f"  Metoda 1 (lista projektu):    sekcja = {m1!r}")
    print(f"  Metoda 2 (pojedyncze zadanie): sekcja = {m2!r}")
    if m1 != m2:
        print("\n  RÓŻNICA POTWIERDZONA — endpoint listy zwraca nieaktualne dane!")
    else:
        print("\n  Identyczne — to NIE jest problem z cache'em listy.")


if __name__ == "__main__":
    main()
