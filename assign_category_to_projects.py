#!/usr/bin/env python3
"""
assign_category_to_projects.py

Przypisuje JEDNĄ kategorię projektu (Project Category) do podanych, JUŻ
ISTNIEJĄCYCH projektów Jiry. Kategoria musi już istnieć w Jirze (Ustawienia
-> Projekty -> Kategorie projektów) - ten skrypt jej nie tworzy, tylko
przypisuje istniejącą.

Użycie:
    python assign_category_to_projects.py --category "Migracja 2026" \\
        --project-keys FMPGEU,PROJ9,PROJ10

    python assign_category_to_projects.py --category "Migracja 2026" --all
        # wszystkie projekty z arkusza, które mają wypełniony jira_key
"""
import argparse
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core


def get_category_id_by_name(name: str) -> str | None:
    """Szuka kategorii po nazwie, IGNORUJĄC białe znaki na początku/końcu -
    niektóre kategorie w Jirze mają niewidoczne białe znaki (np. tabulację)
    w swojej nazwie (potwierdzony ręcznie przypadek: 'FirmaDE,archive\\t'),
    przez co dokładne porównanie nigdy by nie zadziałało."""
    resp = requests.get(f"{core.JIRA_BASE_URL}/rest/api/3/projectCategory", auth=core.jira_auth(), timeout=30)
    resp.raise_for_status()
    target = name.strip()
    for cat in resp.json():
        if (cat.get("name") or "").strip() == target:
            return cat.get("id")
    return None


def assign_category(project_key: str, category_id: str) -> None:
    resp = requests.put(
        f"{core.JIRA_BASE_URL}/rest/api/3/project/{project_key}",
        auth=core.jira_auth(),
        headers={"Content-Type": "application/json"},
        json={"categoryId": category_id},
        timeout=30,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"{resp.status_code} {resp.text[:200]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--category", help="Nazwa istniejącej kategorii projektu.")
    parser.add_argument("--list-categories", action="store_true", help="Tylko wypisz wszystkie kategorie widziane przez API Jiry, nic nie przypisuj.")
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument("--project-keys", type=str, help="Lista kluczy Jiry po przecinku.")
    group.add_argument("--all", action="store_true", help="Wszystkie projekty z arkusza z wypełnionym jira_key.")
    args = parser.parse_args()

    if args.list_categories:
        resp = requests.get(f"{core.JIRA_BASE_URL}/rest/api/3/projectCategory", auth=core.jira_auth(), timeout=30)
        resp.raise_for_status()
        cats = resp.json()
        print(f"Kategorie widziane przez /rest/api/3/projectCategory ({len(cats)}):")
        for c in cats:
            print(f"  id={c.get('id')}  name={c.get('name')!r}")
        return

    if not args.category or not (args.project_keys or args.all):
        parser.error("--category oraz (--project-keys lub --all) są wymagane, chyba że używasz --list-categories.")

    if args.all:
        print("Wczytuję listę projektów z arkusza...")
        sheet_rows = core.get_sheet_rows()
        project_keys = sorted(set(row.jira_key for row in sheet_rows if row.jira_key))
        print(f"Znaleziono {len(project_keys)} projektów z wypełnionym jira_key.")
    else:
        project_keys = [k.strip() for k in args.project_keys.split(",") if k.strip()]

    print(f"\nSzukam kategorii '{args.category}' w Jirze...")
    category_id = get_category_id_by_name(args.category)
    if not category_id:
        print(f"[BŁĄD] Nie znaleziono kategorii '{args.category}' w Jirze. Utwórz ją najpierw ręcznie "
              f"(Ustawienia -> Projekty -> Kategorie projektów).")
        sys.exit(1)
    print(f"  Znaleziono (id {category_id}).\n")

    ok, failed = 0, []
    for i, key in enumerate(project_keys, 1):
        try:
            assign_category(key, category_id)
            print(f"[{i}/{len(project_keys)}] {key}: OK")
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[{i}/{len(project_keys)}] {key}: [BŁĄD] {exc}")
            failed.append((key, str(exc)))

    print("\n" + "=" * 60)
    print(f"PODSUMOWANIE: OK={ok}, błędy={len(failed)}")
    print("=" * 60)
    if failed:
        print("\nProjekty z błędem:")
        for key, err in failed:
            print(f"  {key}: {err}")


if __name__ == "__main__":
    main()
