#!/usr/bin/env python3
"""
add_group_to_projects.py

Dodaje JEDNĄ grupę do JEDNEJ LUB WIELU ról (po przecinku) w podanych, JUŻ
ISTNIEJĄCYCH projektach Jiry. Reużywa tej samej logiki co sekcja 5
w jira_sync.py, tylko jako osobny, powtarzalny krok dla projektów, które
już zostały utworzone.

Użycie:
    python add_group_to_projects.py --group "MOJA GRUPA: Staff" \\
        --role-names Developer,Users --project-keys FMPGEU,HBGRMU2,HBGRMU3

    python add_group_to_projects.py --group "MOJA GRUPA: Staff" \\
        --role-names Developer,Users --all
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
    parser.add_argument("--group", required=True, help="Nazwa grupy do dodania.")
    parser.add_argument("--role-names", required=True, help="Nazwy ról po przecinku, np. 'Developer,Users'.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--project-keys", type=str, help="Lista kluczy Jiry po przecinku.")
    group.add_argument("--all", action="store_true", help="Wszystkie projekty z arkusza z wypełnionym jira_key.")
    args = parser.parse_args()

    if args.all:
        print("Wczytuję listę projektów z arkusza...")
        sheet_rows = core.get_sheet_rows()
        project_keys = sorted(set(row.jira_key for row in sheet_rows if row.jira_key))
        print(f"Znaleziono {len(project_keys)} projektów z wypełnionym jira_key.")
    else:
        project_keys = [k.strip() for k in args.project_keys.split(",") if k.strip()]

    role_names = [r.strip() for r in args.role_names.split(",") if r.strip()]

    print(f"\nSzukam ról: {', '.join(role_names)}...")
    roles: list[tuple[str, str]] = []  # [(nazwa, id), ...]
    for role_name in role_names:
        role_id = core.get_role_id_by_name(role_name)
        if role_id:
            roles.append((role_name, role_id))
            print(f"  '{role_name}': znaleziono (id {role_id})")
        else:
            print(f"  '{role_name}': [OSTRZEŻENIE] nie znaleziono w Jirze - pominięta")

    if not roles:
        print("\n[BŁĄD] Żadna z podanych ról nie istnieje w Jirze.")
        sys.exit(1)

    print()
    ok, failed = 0, []
    for i, key in enumerate(project_keys, 1):
        project_errors = []
        for role_name, role_id in roles:
            try:
                core.add_group_to_project_role(key, role_id, args.group)
            except Exception as exc:  # noqa: BLE001
                project_errors.append(f"{role_name}: {exc}")
        if project_errors:
            print(f"[{i}/{len(project_keys)}] {key}: [BŁĄD] {'; '.join(project_errors)}")
            failed.append((key, "; ".join(project_errors)))
        else:
            print(f"[{i}/{len(project_keys)}] {key}: OK ({len(roles)} rol(e))")
            ok += 1

    print("\n" + "=" * 60)
    print(f"PODSUMOWANIE: OK={ok}, błędy={len(failed)}")
    print("=" * 60)
    if failed:
        print("\nProjekty z błędem (choć w co najmniej jednej roli):")
        for key, err in failed:
            print(f"  {key}: {err}")


if __name__ == "__main__":
    main()
