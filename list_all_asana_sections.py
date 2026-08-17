#!/usr/bin/env python3
"""
list_all_asana_sections.py

Przechodzi przez WSZYSTKIE wiersze arkusza (z wypełnionym asana_project_link),
zbiera nazwy sekcji z każdego projektu Asany, i wypisuje UNIKALNĄ,
posortowaną listę (bez duplikatów) — niezależnie od tego, w ilu projektach
dana sekcja występuje.

Dodatkowo porównuje wynik z aktualnym section_status_map.json i pokazuje
OSOBNO sekcje, które JESZCZE NIE MAJĄ wpisu — to bezpośrednia "lista do
zrobienia" przy uzupełnianiu mapy.

Użycie:
    python list_all_asana_sections.py                  # wszystkie wiersze z arkusza
    python list_all_asana_sections.py --limit 10        # tylko pierwsze 10 (do testu)
    python list_all_asana_sections.py --save wynik.json # dodatkowo zapisz do pliku
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

SECTION_STATUS_MAP_FILE = Path("section_status_map.json")


def get_project_sections(project_gid: str) -> list[str]:
    url = f"{ASANA_API}/projects/{project_gid}/sections"
    resp = requests.get(url, headers=ASANA_HEADERS, params={"opt_fields": "name"}, timeout=30)
    resp.raise_for_status()
    return [s["name"] for s in resp.json().get("data", [])]


def load_existing_map() -> dict:
    if SECTION_STATUS_MAP_FILE.exists():
        try:
            data = json.loads(SECTION_STATUS_MAP_FILE.read_text(encoding="utf-8"))
            data.pop("_komentarz", None)
            return data
        except Exception:  # noqa: BLE001
            return {}
    return {}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--save", type=str, default=None, help="Dodatkowo zapisz pełny wynik do pliku JSON.")
    args = parser.parse_args()

    print("Wczytuję arkusz...")
    sheet_rows = core.get_sheet_rows()
    rows_with_link = [r for r in sheet_rows if r.asana_project_link]
    if args.limit:
        rows_with_link = rows_with_link[: args.limit]

    total = len(rows_with_link)
    print(f"Projektów z asana_project_link do sprawdzenia: {total}")
    if not total:
        print("Brak wierszy z wypełnionym asana_project_link — nic do zrobienia.")
        return

    unique_sections: set = set()
    section_to_projects: dict = {}  # nazwa sekcji -> lista nazw projektów (do kontekstu)
    errors = 0

    for i, row in enumerate(rows_with_link):
        gid = core.extract_asana_project_gid(row.asana_project_link)
        if not gid:
            print(f"  [{i + 1}/{total}] [OSTRZEŻENIE] Nieprawidłowy link dla '{row.project_name}' — pomijam.")
            errors += 1
            continue
        try:
            sections = get_project_sections(gid)
            for s in sections:
                unique_sections.add(s)
                section_to_projects.setdefault(s, []).append(row.project_name or gid)
            print(f"  [{i + 1}/{total}] {row.project_name or gid}: {len(sections)} sekcji")
        except Exception as exc:  # noqa: BLE001
            print(f"  [{i + 1}/{total}] [OSTRZEŻENIE] Błąd dla '{row.project_name}': {exc}")
            errors += 1

        time.sleep(0.2)  # ostrożne tempo względem limitów API Asany

    existing_map = load_existing_map()
    already_mapped = {k for k in existing_map.keys()}
    missing = sorted(unique_sections - already_mapped)
    already = sorted(unique_sections & already_mapped)

    print("\n" + "=" * 60)
    print(f"PODSUMOWANIE ({total - errors}/{total} projektów sprawdzonych poprawnie)")
    print(f"Unikalnych sekcji znalezionych łącznie: {len(unique_sections)}")
    print(f"  - już mają wpis w section_status_map.json: {len(already)}")
    print(f"  - BRAK WPISU (do uzupełnienia): {len(missing)}")

    if missing:
        print(f"\nSekcje BEZ wpisu w section_status_map.json ({len(missing)}):\n")
        for name in missing:
            example_project = section_to_projects[name][0]
            print(f'  "{name}: {example_project}": "",')

    if args.save:
        out = {
            "unique_sections": sorted(unique_sections),
            "missing_from_map": missing,
            "already_mapped": already,
            "section_to_projects": section_to_projects,
        }
        Path(args.save).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nZapisano pełny wynik do: {args.save}")


if __name__ == "__main__":
    main()
