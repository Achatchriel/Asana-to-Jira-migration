#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
find_duplicate_tasks.py

Sprawdza WSZYSTKIE zadania we wszystkich projektach migracji i wykrywa
potencjalne DUPLIKATY - dwa lub więcej zadań w TYM SAMYM projekcie
Jira o DOKŁADNIE tym samym tytule. To najbardziej wiarygodny sygnał
duplikatu, biorąc pod uwagę, że synchronizacja mapuje jedno zadanie
Asany na jedno zadanie Jiry (po tytule + numerze zadania w Asanie).

WAŻNE - może dawać FAŁSZYWE ALARMY: jeśli w Asanie faktycznie istniały
dwa RÓŻNE zadania o tym samym tytule (np. dwa razy "Kickoff Call" w tym
samym projekcie), to NIE jest duplikat z naszej synchronizacji, tylko
prawidłowe odzwierciedlenie danych źródłowych. Skrypt tylko WSKAZUJE
podejrzane przypadki do RĘCZNEGO sprawdzenia - nic nie usuwa automatycznie.

Użycie:
    python find_duplicate_tasks.py                  # wszystkie projekty
    python find_duplicate_tasks.py --project KLUCZ   # tylko jeden projekt (szybki test)
"""
import argparse
import sys
from collections import defaultdict
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core


def get_all_summaries(project_key: str) -> list:
    """Zwraca listę (summary, issue_key) dla WSZYSTKICH zadań w projekcie,
    stronicując przez /search/jql."""
    results = []
    next_page_token = None
    while True:
        body = {"jql": f"project = {project_key}", "maxResults": 100, "fields": ["summary"]}
        if next_page_token:
            body["nextPageToken"] = next_page_token
        resp = requests.post(
            f"{core.JIRA_BASE_URL}/rest/api/3/search/jql",
            auth=core.jira_auth(),
            json=body,
        )
        if resp.status_code != 200:
            print(f"   [OSTRZEŻENIE] Błąd stronicowania dla {project_key}: {resp.status_code} {resp.text[:150]}")
            break
        data = resp.json()
        for issue in data.get("issues", []):
            summary = issue.get("fields", {}).get("summary", "")
            results.append((summary, issue["key"]))
        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            break
    return results


def find_duplicates_in_project(project_key: str) -> dict:
    """Zwraca {summary: [issue_key1, issue_key2, ...]} TYLKO dla tytułów
    występujących WIĘCEJ NIŻ RAZ w tym projekcie."""
    summaries = get_all_summaries(project_key)
    by_summary = defaultdict(list)
    for summary, key in summaries:
        by_summary[summary].append(key)
    return {s: keys for s, keys in by_summary.items() if len(keys) > 1}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", help="Sprawdź tylko ten jeden klucz projektu (szybki test), zamiast wszystkich.")
    args = parser.parse_args()

    if args.project:
        project_keys = [args.project]
    else:
        print("Wczytuję listę projektów z arkusza...")
        sheet_rows = core.get_sheet_rows()
        project_keys = sorted(set(row.jira_key for row in sheet_rows if row.jira_key))
        print(f"Znaleziono {len(project_keys)} unikalnych kluczy projektów Jiry.")

    print(f"\nSprawdzam duplikaty (stronicowanie przez wszystkie zadania - to może potrwać kilka-kilkanaście minut)...\n")

    total_duplicate_groups = 0
    total_duplicate_extra_issues = 0  # ile ZBĘDNYCH (ponad pierwszy) zadań w grupach duplikatów
    projects_with_duplicates = []

    for i, key in enumerate(project_keys, 1):
        dupes = find_duplicates_in_project(key)
        if dupes:
            projects_with_duplicates.append(key)
            total_duplicate_groups += len(dupes)
            print(f"[{i}/{len(project_keys)}] {key}: ZNALEZIONO {len(dupes)} podejrzanych grup")
            for summary, issue_keys in dupes.items():
                total_duplicate_extra_issues += len(issue_keys) - 1
                print(f"    '{summary}' -> {', '.join(issue_keys)}")
        else:
            print(f"[{i}/{len(project_keys)}] {key}: OK, brak duplikatów")

    print("\n" + "=" * 60)
    print(f"PODSUMOWANIE")
    print(f"  Projektów z podejrzanymi duplikatami: {len(projects_with_duplicates)} / {len(project_keys)}")
    print(f"  Grup zdublowanych tytułów: {total_duplicate_groups}")
    print(f"  Szacunkowo zbędnych zadań (do ręcznej weryfikacji): {total_duplicate_extra_issues}")
    print("=" * 60)
    if total_duplicate_groups:
        print("\nPAMIĘTAJ: to mogą być FAŁSZYWE ALARMY (dwa różne, prawdziwe zadania")
        print("Asany o tym samym tytule). Sprawdź ręcznie w Jirze przed usunięciem")
        print("czegokolwiek - np. porównaj daty utworzenia i treść komentarzy.")


if __name__ == "__main__":
    main()
