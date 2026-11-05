#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
count_migrated_tasks.py

Liczy ŁĄCZNĄ liczbę zadań we wszystkich projektach migracji BEZPOŚREDNIO
w Jirze (przez /rest/api/3/search/approximate-count - aktualny, poprawny
sposób liczenia w Jira Cloud) - to jest PRAWDA ŹRÓDŁOWA, bez ryzyka błędu
przy rekonstrukcji/sumowaniu z logów wielu, nakładających się przebiegów
synchronizacji.

Sprawdza WSZYSTKIE klucze Jiry z arkusza (kolumna jira_key), jedno
zapytanie POST per projekt.

Użycie:
    python count_migrated_tasks.py                    # łączna liczba zadań + per projekt
    python count_migrated_tasks.py --summary-only      # tylko łączna liczba zadań
    python count_migrated_tasks.py --with-attachments  # DODATKOWO liczy załączniki
    python count_migrated_tasks.py --compare-asana     # DODATKOWO porównuje z Asaną (wszystkie poziomy)
    python count_migrated_tasks.py --compare-full      # jak wyżej + podział na poziomy/typy + załączniki
    (porównania są ZNACZNIE wolniejsze - jedno zapytanie do Asany na każde
    zadanie, żeby znaleźć podzadania na każdym poziomie)

PORÓWNANIE Z ASANĄ (od wersji z hierarchią Epików):
  - Asana: wszystkie zadania projektu NA KAŻDYM POZIOMIE (podzadania podzadań
    też), każde liczone RAZ - podzadania dodane też do listy projektu nie są
    liczone podwójnie (ta sama logika co compare_project_totals.py),
  - Jira: wszystkie zgłoszenia projektu,
  - oczekiwane w Jirze = Asana - zadania oznaczone jako usunięte w Jirze
    (deleted_in_jira w task_sync_state.json),
  - rozbieżność jest rozbijana na przyczyny: niezmigrowane (z poziomem),
    usunięte w Jirze, nadmiarowe w Jirze (bez powiązania w stanie).
  Porównanie "taski vs taski" i "podtaski vs podtaski" nie ma sensu po
  wprowadzeniu Epików (dziecko Epika jest w Asanie podzadaniem, a w Jirze
  Taskiem; głębsze poziomy są spłaszczane), dlatego porównywana jest suma,
  a podział na poziomy/typy jest tylko informacyjny.
  Załączniki: liczone tylko te z plikiem do pobrania (download_url) - tylko
  takie przenosi synchronizacja; linki do zewnętrznych dysków osobno.
Porównanie wykonuje compare_project_totals.py (jedna implementacja dla obu
narzędzi); --compare-full odpowiada: python compare_project_totals.py --all --attachments
Szczegóły rozbieżności dla projektu: python compare_project_totals.py KLUCZ --details
"""
import argparse
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core
from compare_project_totals import run_comparison, sheet_projects  # noqa: E402


def count_issues_in_projects(project_keys: list) -> dict:
    """Zwraca {project_key: liczba_zadan}, jedno zapytanie POST per projekt
    do /search/approximate-count (aktualny, poprawny sposób liczenia w Jira
    Cloud - stary endpoint /search z polem 'total' jest deprecated, a nowy
    /search/jql w ogóle nie zwraca liczby, tylko listę + stronicowanie)."""
    counts = {}
    for key in project_keys:
        resp = requests.post(
            f"{core.JIRA_BASE_URL}/rest/api/3/search/approximate-count",
            auth=core.jira_auth(),
            json={"jql": f"project = {key}"},
        )
        if resp.status_code == 200:
            counts[key] = resp.json().get("count")
        else:
            print(f"   [OSTRZEŻENIE] Błąd dla projektu {key}: {resp.status_code} {resp.text[:150]}")
            counts[key] = None
    return counts


def count_attachments_in_project(project_key: str) -> int:
    """Liczy załączniki we WSZYSTKICH zadaniach danego projektu - stronicuje
    przez wszystkie zadania (pole 'attachment' - lista załączników per
    zadanie) i sumuje długości list. Jira nie ma prostszego, bezpośredniego
    sposobu policzenia SAMYCH załączników bez przejścia przez zadania."""
    total_attachments = 0
    next_page_token = None
    while True:
        body = {"jql": f"project = {project_key}", "maxResults": 100, "fields": ["attachment"]}
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
            total_attachments += len(issue.get("fields", {}).get("attachment") or [])
        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            break
    return total_attachments


def _asana_paginated_count(url: str) -> int:
    """Generyczne liczenie elementów pod danym URL-em Asany (zadania w
    projekcie / podzadania zadania / załączniki zadania) - ta sama logika
    ponawiania co count_asana_tasks, reużyta zamiast duplikowana."""
    headers = {"Authorization": f"Bearer {core.ASANA_TOKEN}"}
    params = {"opt_fields": "gid", "limit": 100}
    count = 0
    offset = None
    while True:
        if offset:
            params["offset"] = offset
        resp = None
        for attempt in range(4):
            try:
                resp = requests.get(url, headers=headers, params=params, timeout=30)
            except requests.exceptions.RequestException:
                time.sleep(2 ** attempt)
                continue
            if resp.status_code == 200:
                break
            if resp.status_code == 429:
                time.sleep(int(resp.headers.get("Retry-After", 2 ** attempt)))
                continue
            time.sleep(2 ** attempt)
        else:
            print(f"   [OSTRZEŻENIE] Nie udało się pobrać {url} po 4 próbach - wynik może być NIEPEŁNY.")
            return count
        if resp is None or resp.status_code != 200:
            return count
        payload = resp.json()
        count += len(payload.get("data", []))
        offset = payload.get("next_page", {}).get("offset") if payload.get("next_page") else None
        if not offset:
            return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary-only", action="store_true", help="Pokaż tylko łączną liczbę, bez podziału per projekt.")
    parser.add_argument("--with-attachments", action="store_true", help="DODATKOWO policz załączniki (znacznie wolniejsze - stronicuje przez wszystkie zadania).")
    parser.add_argument("--compare-asana", action="store_true", help="DODATKOWO porównaj liczbę zadań w Jirze z liczbą zadań w odpowiadającym projekcie Asany, wskaż rozbieżności.")
    parser.add_argument("--compare-full", action="store_true", help="Pełne porównanie: taski, podtaski I załączniki naraz, Jira vs Asana (najwolniejsza opcja - przechodzi przez KAŻDE zadanie).")
    args = parser.parse_args()

    print("Wczytuję listę projektów z arkusza...")
    sheet_rows = core.get_sheet_rows()
    project_keys = [row.jira_key for row in sheet_rows if row.jira_key]
    project_keys = sorted(set(project_keys))
    print(f"Znaleziono {len(project_keys)} unikalnych kluczy projektów Jiry w arkuszu.")

    print("Liczę zadania w Jirze (JQL, wsadowo)...")
    counts = count_issues_in_projects(project_keys)

    valid_counts = {k: v for k, v in counts.items() if v is not None}
    total = sum(valid_counts.values())
    failed = [k for k, v in counts.items() if v is None]

    print("\n" + "=" * 60)
    print(f"ŁĄCZNA LICZBA ZADAŃ W JIRZE (wszystkie projekty migracji): {total}")
    print(f"Projektów sprawdzonych poprawnie: {len(valid_counts)} / {len(project_keys)}")
    if failed:
        print(f"Projektów z błędem sprawdzania: {len(failed)} ({', '.join(failed[:10])}{'...' if len(failed) > 10 else ''})")
    print("=" * 60)

    if not args.summary_only:
        print("\nPodział per projekt (malejąco):")
        for key, count in sorted(valid_counts.items(), key=lambda x: -x[1])[:30]:
            print(f"  {key}: {count}")
        if len(valid_counts) > 30:
            print(f"  ... i {len(valid_counts) - 30} kolejnych projektów")

    if args.with_attachments:
        print(f"\nLiczę załączniki (stronicowanie przez wszystkie zadania - to może potrwać kilka-kilkanaście minut dla {len(valid_counts)} projektów)...")
        total_attachments = 0
        for i, key in enumerate(valid_counts, 1):
            n = count_attachments_in_project(key)
            total_attachments += n
            print(f"  [{i}/{len(valid_counts)}] {key}: {n} załączników")
        print("\n" + "=" * 60)
        print(f"ŁĄCZNA LICZBA ZAŁĄCZNIKÓW W JIRZE: {total_attachments}")
        print("=" * 60)

    if args.compare_asana or args.compare_full:
        # Jedna implementacja porównania dla obu narzędzi (compare_project_totals.py):
        # wszystkie poziomy, każde zadanie raz, przyczyny rozbieżności ze stanu migracji.
        single, multi = sheet_projects()
        keys = sorted(k for k in valid_counts if k in single or k in multi)
        print(f"\nPorównanie z Asaną (wszystkie poziomy) dla {len(keys)} projektów - jedno zapytanie do "
              f"Asany na zadanie, więc to może potrwać. Równolegle: compare_project_totals.py --all --shard K/N")
        run_comparison(keys, attachments=args.compare_full, details=False)

if __name__ == "__main__":
    main()
