#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
migrate_project.py — migracja JEDNEGO projektu: link do projektu w Asanie → nowy projekt w Jirze
z zadaniami, bez arkusza Google (karta „Migracja projektu” w panelu).

Kroki:
  1. odczyt projektu z Asany (nazwa z linku);
  2. utworzenie projektu w Jirze, konfiguracja z jednego z dwóch źródeł:
       --config template  - projekt wzorcowy z .env (JIRA_TEMPLATE_PROJECT_KEY), jak jira_sync.py:
                            te same schematy co wzorzec, grupa deweloperów i administratorzy z .env
                            (albo osoby i grupy z --roles-file);
       --config source    - projekt źródłowy (--source), jak clone_project.py: własne kopie schematów
                            nazwane od nowego projektu, tablice z kolumnami i statusami, role z --roles-file;
  3. indywidualna mapa sekcja Asany → status Jiry (--map-file) zapisana jako
     section_status_map.<KLUCZ>.json (ma pierwszeństwo przed mapą główną);
  4. kolumny i statusy tablicy według tablicy wzorcowej (tylko --config template; kopia ze
     źródła przenosi tablice sama);
  5. wykrycie kandydatów na Epiki i synchronizacja zadań Asana → Jira (pomijane z --no-sync).

  python migrate_project.py --asana-link URL --key XYZ --config template --map-file mapa.json --dry-run
  python migrate_project.py --asana-link URL --key XYZ --config source --source ABC --map-file mapa.json

--map-file: {"Sekcja Asany": "Status Jiry" | {"pomin": true}, "__ukonczone__": "Status"}.
Projekt spoza arkusza nie bierze udziału w run_full_pipeline.py - kolejne synchronizacje:
  python asana_jira_sync.py --project-keys XYZ --asana-link URL
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import jira_sync as core  # noqa: E402

PY = sys.executable


def step(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}", flush=True)


def run(args: list) -> int:
    print(f"$ {' '.join(Path(a).name if i == 1 else a for i, a in enumerate([PY] + args))}", flush=True)
    return subprocess.run([PY, *args], cwd=ROOT).returncode


def project_exists(key: str) -> bool:
    return core.jira_project_exists(key)


def create_from_template(key: str, name: str, lead: str, description: str, roles_file: str, no_roles: bool, dry: bool) -> None:
    tpl = core.JIRA_TEMPLATE_PROJECT_KEY
    if not tpl:
        raise SystemExit("Brak JIRA_TEMPLATE_PROJECT_KEY w .env - wybierz --config source albo ustaw projekt wzorcowy.")
    lead = lead or core.DEFAULT_LEAD_ACCOUNT_ID
    if not lead:
        raise SystemExit("Brak leada: podaj --lead albo ustaw DEFAULT_LEAD_ACCOUNT_ID w .env.")
    project = core.ProjectToCreate(name=name, jira_key=key, project_type_key=core.DEFAULT_PROJECT_TYPE_KEY,
                                   template_key=core.DEFAULT_TEMPLATE_KEY, lead_account_id=lead,
                                   description=description, matched_in_asana=True)
    schemes = core.get_template_schemes(tpl)
    if dry:
        print(f"[DRY-RUN] Utworzyłbym projekt {key} „{name}” (lead {lead}) ze schematami projektu wzorcowego {tpl}.")
    else:
        result = core.create_jira_project(project, schemes)
        print(f"Utworzono projekt {key} „{name}” (id {result['id']}).")
        for err in core.apply_template_schemes(str(result["id"]), schemes):
            print(f"[OSTRZEŻENIE] Nie udało się podpiąć: {err}")
    if no_roles:
        print("Role: nikogo nie dodaję.")
        return
    if roles_file:
        import clone_project as cp  # ta sama obsługa wyboru ról co w kopii projektu
        cp.DRY = dry
        selection = json.loads(Path(roles_file).read_text(encoding="utf-8"))
        print(f"Role według wyboru ({roles_file}): {len(selection)}")
        cp.apply_roles(key, selection)
        return
    # Jak jira_sync.py: grupa/osoby deweloperów i administratorzy z .env
    if core.DEVELOPER_GROUP_NAME or core.DEVELOPER_ACCOUNT_IDS:
        for role_name in [r.strip() for r in core.DEVELOPER_ROLE_NAME.split(",") if r.strip()]:
            role_id = core.get_role_id_by_name(role_name)
            if not role_id:
                print(f"[OSTRZEŻENIE] Nie ma roli „{role_name}” - pomijam deweloperów.")
                continue
            if dry:
                print(f"[DRY-RUN] Rola „{role_name}”: grupa {core.DEVELOPER_GROUP_NAME or '-'}, osoby {core.DEVELOPER_ACCOUNT_IDS or '-'}")
                continue
            if core.DEVELOPER_GROUP_NAME:
                core.add_group_to_project_role(key, role_id, core.DEVELOPER_GROUP_NAME)
            if core.DEVELOPER_ACCOUNT_IDS:
                core.add_users_to_project_role(key, role_id, [a.strip() for a in core.DEVELOPER_ACCOUNT_IDS.split(",") if a.strip()])
            print(f"Rola „{role_name}”: dodano deweloperów z .env.")
    admins = [a.strip() for a in core.ADMIN_ACCOUNT_ID.split(",") if a.strip()]
    if core.ADD_LEAD_AS_ADMIN and lead not in admins:
        admins.append(lead)
    if admins:
        role_id = core.get_role_id_by_name(core.ADMIN_ROLE_NAME)
        if role_id and not dry:
            core.add_users_to_project_role(key, role_id, admins)
        print(f"{'[DRY-RUN] ' if dry else ''}Rola „{core.ADMIN_ROLE_NAME}”: {len(admins)} osób z .env.")


def write_section_map(key: str, map_file: str, dry: bool) -> None:
    data = json.loads(Path(map_file).read_text(encoding="utf-8"))
    entries = {k: v for k, v in data.items() if v}
    sections = {k: v for k, v in entries.items() if not k.startswith("__")}
    print(f"Sekcji w mapie: {len(sections)}, ukończone → {entries.get('__ukonczone__') or '(domyślnie z mapy głównej)'}")
    for sec, val in sections.items():
        print(f"  {sec} → {'(pomiń - nie migruj)' if isinstance(val, dict) and val.get('pomin') else val}")
    target = ROOT / f"section_status_map.{key}.json"
    if dry:
        print(f"[DRY-RUN] Zapisałbym {target.name}.")
        return
    if target.exists():
        backup = ROOT / "migration_app" / "backups" / f"{target.name}.{time.strftime('%Y%m%d_%H%M%S')}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup)
    target.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Zapisano {target.name}.")


def template_board_id() -> str:
    board = os.getenv("JIRA_TEMPLATE_BOARD_ID", "").strip()
    if board:
        return board
    import board_columns as bc
    return str(bc.default_template_board())


def main() -> None:
    ap = argparse.ArgumentParser(description="Migracja jednego projektu: link do Asany → nowy projekt w Jirze.")
    ap.add_argument("--asana-link", required=True, help="Link do projektu w Asanie")
    ap.add_argument("--key", required=True, help="Klucz nowego projektu Jiry")
    ap.add_argument("--name", default="", help="Nazwa projektu w Jirze (domyślnie nazwa z Asany)")
    ap.add_argument("--config", choices=["template", "source"], default="template",
                    help="Konfiguracja z projektu wzorcowego (.env) albo z projektu źródłowego (--source)")
    ap.add_argument("--source", default="", help="Projekt źródłowy dla --config source")
    ap.add_argument("--name-base", choices=["key", "name"], default="key", help="Nazwy kopii schematów (--config source)")
    ap.add_argument("--lead", default="", help="Lead (accountId); domyślnie DEFAULT_LEAD_ACCOUNT_ID albo lead źródła")
    ap.add_argument("--description", default="", help="Opis projektu (domyślnie pusty)")
    ap.add_argument("--map-file", default="", help="Mapa sekcja Asany → status Jiry (JSON)")
    ap.add_argument("--roles-file", default="", help="Kto z jaką rolą (JSON, jak w kopii projektu)")
    ap.add_argument("--no-roles", action="store_true", help="Nie dodawaj nikogo do ról")
    ap.add_argument("--existing-target", action="store_true", help="Projekt już istnieje - pomiń tworzenie, dokończ resztę")
    ap.add_argument("--no-sync", action="store_true", help="Tylko utwórz i skonfiguruj, bez migracji zadań")
    ap.add_argument("--dry-run", action="store_true", help="Podgląd, nic nie zapisuj")
    a = ap.parse_args()
    core.check_config()
    key = a.key.strip().upper()
    dry = a.dry_run

    step("1. Projekt w Asanie")
    gid = core.extract_asana_project_gid(a.asana_link)
    if not gid:
        sys.exit(f"Nie rozpoznano linku do projektu Asany: {a.asana_link}")
    asana = core.get_asana_project_by_gid(gid)
    if not asana:
        sys.exit(f"Projekt Asany {gid} nie istnieje albo token nie ma do niego dostępu.")
    name = a.name.strip() or asana.name
    print(f"{asana.name} (gid {gid}) → {key} „{name}”")

    step("2. Projekt w Jirze")
    exists = project_exists(key)
    if exists and not a.existing_target:
        sys.exit(f"Projekt {key} już istnieje. Zaznacz „projekt już istnieje”, żeby dokończyć pozostałe kroki.")
    if not exists and a.existing_target:
        sys.exit(f"Projekt {key} nie istnieje - uruchom bez --existing-target.")
    if exists:
        print(f"Projekt {key} istnieje - pomijam tworzenie.")
    elif a.config == "template":
        create_from_template(key, name, a.lead, a.description, a.roles_file, a.no_roles, dry)
    else:
        if not a.source:
            sys.exit("--config source wymaga --source (klucz projektu źródłowego).")
        cmd = ["clone_project.py", "--source", a.source.upper(), "--target-key", key, "--target-name", name,
               "--name-base", a.name_base]
        cmd += ["--lead-account-id", a.lead] if a.lead else []
        cmd += ["--description", a.description] if a.description else []
        cmd += ["--roles-file", a.roles_file] if a.roles_file and not a.no_roles else []
        cmd += ["--dry-run"] if dry else []
        rc = run(cmd)
        if rc not in (0, 2):
            sys.exit(f"Kopia projektu źródłowego nie powiodła się (kod {rc}).")
    created = exists or not dry  # w podglądzie nowy projekt jeszcze nie istnieje

    step("3. Mapa sekcja Asany → status Jiry")
    if a.map_file:
        write_section_map(key, a.map_file, dry)
    else:
        print("Bez indywidualnej mapy - synchronizacja użyje mapy głównej (section_status_map.json).")

    if a.config == "template":
        step("4. Kolumny i statusy tablicy")
        if created:
            rc = run(["board_columns.py", "--project-keys", key, "--template-board-id", template_board_id()]
                     + (["--dry-run"] if dry else []))
            if rc not in (0, 2):
                print(f"[OSTRZEŻENIE] Kolumny tablicy: kod {rc} - sprawdź log powyżej.")
        else:
            print("[DRY-RUN] Kolumny i statusy ustawię po utworzeniu projektu (wzorzec: tablica "
                  f"{template_board_id()}).")

    if a.no_sync:
        print("\nPominięto migrację zadań (--no-sync).")
        return
    if not created:
        step("5-6. Kandydaci na Epiki i migracja zadań")
        print("[DRY-RUN] Podgląd wykrywania Epików i synchronizacji zadań jest możliwy, gdy projekt istnieje - "
              "uruchom ponownie z „projekt już istnieje” po jego utworzeniu.")
        return
    step("5. Kandydaci na Epiki (odczyt z Asany)")
    run(["detect_epic_candidates.py", "--project-keys", key, "--asana-link", a.asana_link, "--force"])

    step("6. Migracja zadań Asana → Jira")
    rc = run(["asana_jira_sync.py", "--project-keys", key, "--asana-link", a.asana_link, "--asana-name", asana.name]
             + (["--dry-run"] if dry else []))
    print(f"\n{'PODGLĄD - nic nie zapisano' if dry else 'GOTOWE'}: {asana.name} → {key}")
    print(f"Kolejne synchronizacje: python asana_jira_sync.py --project-keys {key} --asana-link \"{a.asana_link}\"")
    sys.exit(rc)


if __name__ == "__main__":
    main()
