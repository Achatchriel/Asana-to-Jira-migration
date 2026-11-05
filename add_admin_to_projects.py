#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
add_admin_to_projects.py

Dodaje JEDNĄ osobę (po accountId) do roli Administrator w podanych, JUŻ
ISTNIEJĄCYCH projektach Jiry. Reużywa tej samej, sprawdzonej logiki co
sekcja 6 w jira_sync.py (get_role_id_by_name, add_users_to_project_role),
tylko jako osobny, powtarzalny krok dla projektów, które już zostały
utworzone (nie tylko przy tworzeniu nowych).

Użycie:
    python add_admin_to_projects.py --account-id 5b10a2844c20165700ede21 \\
        --project-keys FMPGEU,PROJ9,PROJ10

    python add_admin_to_projects.py --account-id 5b10a2844c20165700ede21 --all
        # wszystkie projekty z arkusza, które mają wypełniony jira_key
"""
import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-id", required=True, help="accountId osoby do dodania jako Administrator.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--project-keys", type=str, help="Lista kluczy Jiry po przecinku.")
    group.add_argument("--all", action="store_true", help="Wszystkie projekty z arkusza z wypełnionym jira_key.")
    parser.add_argument("--role-name", default=core.ADMIN_ROLE_NAME, help=f"Nazwa roli (domyślnie z .env: '{core.ADMIN_ROLE_NAME}').")
    args = parser.parse_args()

    if args.all:
        print("Wczytuję listę projektów z arkusza...")
        sheet_rows = core.get_sheet_rows()
        project_keys = sorted(set(row.jira_key for row in sheet_rows if row.jira_key))
        print(f"Znaleziono {len(project_keys)} projektów z wypełnionym jira_key.")
    else:
        project_keys = [k.strip() for k in args.project_keys.split(",") if k.strip()]

    print(f"\nSzukam roli '{args.role_name}' w Jirze...")
    role_id = core.get_role_id_by_name(args.role_name)
    if not role_id:
        print(f"[BŁĄD] Nie znaleziono roli '{args.role_name}' w Jirze.")
        sys.exit(1)
    print(f"  Znaleziono (id {role_id}).\n")

    ok, failed = 0, []
    for i, key in enumerate(project_keys, 1):
        try:
            core.add_users_to_project_role(key, role_id, [args.account_id])
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
