#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
check_project_level_attachments.py

Sprawdza, czy którykolwiek z projektów w arkuszu ma załączniki
PRZYPIĘTE BEZPOŚREDNIO DO PROJEKTU (nie do żadnego konkretnego zadania) -
te NIE są migrowane przez asana_jira_sync.py (który obsługuje wyłącznie
załączniki zadań, patrz get_asana_attachments(task_gid)).

Asana ma DWA miejsca, gdzie mogą leżeć takie pliki (potwierdzone w
dokumentacji API - endpoint GET /attachments?parent=...):
  1. "Key resources" w przeglądzie projektu (parent=project_gid)
  2. Treść opisu projektu / project brief (parent=project_brief_gid)

Użycie:
    python check_project_level_attachments.py                # wszystkie projekty z arkusza
    python check_project_level_attachments.py --limit 10      # tylko pierwsze 10 (do testu)
    python check_project_level_attachments.py --save wynik.json
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core  # reużywamy odczytu arkusza i extract_asana_project_gid

ASANA_TOKEN = os.environ["ASANA_TOKEN"]
ASANA_HEADERS = {"Authorization": f"Bearer {ASANA_TOKEN}"}
ASANA_API = "https://app.asana.com/api/1.0"


def get_key_resource_attachments(project_gid: str) -> list[dict]:
    """Pliki w sekcji 'Key resources' przeglądu projektu."""
    resp = requests.get(
        f"{ASANA_API}/attachments", headers=ASANA_HEADERS,
        params={"parent": project_gid, "opt_fields": "name,download_url"}, timeout=30,
    )
    if resp.status_code != 200:
        return []
    return resp.json().get("data", [])


def get_project_brief_gid(project_gid: str) -> "str | None":
    resp = requests.get(
        f"{ASANA_API}/projects/{project_gid}", headers=ASANA_HEADERS,
        params={"opt_fields": "project_brief"}, timeout=30,
    )
    if resp.status_code != 200:
        return None
    brief = resp.json().get("data", {}).get("project_brief")
    return brief.get("gid") if brief else None


def get_project_brief_attachments(project_brief_gid: str) -> list[dict]:
    """Pliki osadzone w treści opisu (brief) projektu."""
    resp = requests.get(
        f"{ASANA_API}/attachments", headers=ASANA_HEADERS,
        params={"parent": project_brief_gid, "opt_fields": "name,download_url"}, timeout=30,
    )
    if resp.status_code != 200:
        return []
    return resp.json().get("data", [])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--save", type=str, default=None, help="Dodatkowo zapisz pełny wynik do pliku JSON.")
    args = parser.parse_args()

    print("Wczytuję arkusz...")
    sheet_rows = core.get_sheet_rows()
    rows_with_link = [r for r in sheet_rows if r.asana_project_link]
    if args.limit:
        rows_with_link = rows_with_link[: args.limit]

    total = len(rows_with_link)
    print(f"Projektów z asana_project_link do sprawdzenia: {total}\n")
    if not total:
        print("Brak wierszy z wypełnionym asana_project_link — nic do zrobienia.")
        return

    projects_with_files: list[dict] = []
    total_key_resource_files = 0
    total_brief_files = 0
    errors = 0

    for i, row in enumerate(rows_with_link):
        gid = core.extract_asana_project_gid(row.asana_project_link)
        if not gid:
            print(f"  [{i + 1}/{total}] [OSTRZEŻENIE] Nieprawidłowy link dla '{row.project_name}' — pomijam.")
            errors += 1
            continue

        try:
            key_resources = get_key_resource_attachments(gid)
            brief_gid = get_project_brief_gid(gid)
            brief_files = get_project_brief_attachments(brief_gid) if brief_gid else []

            total_key_resource_files += len(key_resources)
            total_brief_files += len(brief_files)

            if key_resources or brief_files:
                projects_with_files.append({
                    "project_name": row.project_name,
                    "asana_gid": gid,
                    "key_resource_files": [f["name"] for f in key_resources],
                    "project_brief_files": [f["name"] for f in brief_files],
                })
                print(f"  [{i + 1}/{total}] {row.project_name}: "
                      f"{len(key_resources)} plik(ów) Key Resources, {len(brief_files)} plik(ów) w opisie")
            else:
                print(f"  [{i + 1}/{total}] {row.project_name}: brak plików na poziomie projektu")

        except Exception as exc:  # noqa: BLE001
            print(f"  [{i + 1}/{total}] [OSTRZEŻENIE] Błąd dla '{row.project_name}': {exc}")
            errors += 1

        time.sleep(0.2)  # ostrożne tempo względem limitów API Asany

    print("\n" + "=" * 60)
    print(f"PODSUMOWANIE ({total - errors}/{total} projektów sprawdzonych poprawnie)")
    print(f"Projektów z JAKIMIKOLWIEK plikami na poziomie projektu: {len(projects_with_files)}")
    print(f"  - łącznie plików w Key Resources: {total_key_resource_files}")
    print(f"  - łącznie plików w opisie (project brief): {total_brief_files}")

    if projects_with_files:
        print(f"\nProjekty wymagające ręcznej uwagi ({len(projects_with_files)}):\n")
        for p in projects_with_files:
            print(f"  {p['project_name']} ({p['asana_gid']})")
            for f in p["key_resource_files"]:
                print(f"    [Key Resources] {f}")
            for f in p["project_brief_files"]:
                print(f"    [Opis projektu] {f}")
    else:
        print("\nŻaden projekt nie ma plików przypiętych bezpośrednio do projektu — "
              "wszystkie pliki, jeśli istnieją, są załącznikami zadań i zostaną zmigrowane normalnie.")

    if args.save:
        Path(args.save).write_text(
            json.dumps(projects_with_files, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nZapisano pełny wynik do: {args.save}")


if __name__ == "__main__":
    main()
