#!/usr/bin/env python3
"""
configure_columns_via_cli.py

Odpowiednik bulk_configure_columns.py, ale zamiast kruchej automatyzacji
przeglądarką (Playwright + symulacja przeciągania), woła zainstalowane
LOKALNIE narzędzie "Jira Command Line Interface" (Appfire/dawniej Bob Swift),
które ma wprost udokumentowane akcje do konfiguracji kolumn tablicy:
  --action addBoardColumn --board X --column Y --status "s1,s2"
  --action updateBoardColumn --board X --column Y --status "s1,s2"
Działa przez REST API pod spodem, więc powinno być dużo bardziej niezawodne
niż drag-and-drop w przeglądarce.

WYMAGANIA WSTĘPNE (zrób to raz, ręcznie, PRZED uruchomieniem tego skryptu):
1. Zainstaluj apkę "Jira Command Line Interface (CLI)" w Jira (Marketplace,
   ma darmowy trial) - potrzebujesz uprawnień administratora Jiry.
2. Pobierz CLI Client (plik .zip, wymaga Java 8+) ze strony Appfire/Bob Swift
   i rozpakuj lokalnie.
3. Sprawdź, że polecenie działa z linii komend, np.:
     jira -s https://twoja-instancja.atlassian.net --user TWOJ_EMAIL --token TWOJ_TOKEN \
          --action getBoardColumnList --board "SSLES"
   Jeśli to zwróci listę kolumn - konfiguracja jest OK.
4. Ustaw w .env:
     JIRA_CLI_PATH=/pełna/ścieżka/do/jira   (albo samo "jira", jeśli jest w PATH)

NIEZWERYFIKOWANE Z MOJEJ STRONY: nie mam dostępu do zainstalowania/uruchomienia
tego narzędzia, więc DOKŁADNA składnia flag (zwłaszcza autentykacji) może
wymagać drobnej korekty względem tego, co pokaże `jira --action help
--common` u Ciebie. Przetestuj najpierw na 1 projekcie z --dry-run.

Użycie:
    python configure_columns_via_cli.py --dry-run --limit 1 --project-keys SSLES
    python configure_columns_via_cli.py --limit 1 --project-keys SSLES
    python configure_columns_via_cli.py
"""
import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jira_sync as core  # reużywamy odczytu arkusza i cache'u kluczy

JIRA_BASE_URL = os.environ["JIRA_BASE_URL"].rstrip("/")
JIRA_EMAIL = os.environ["JIRA_EMAIL"]
JIRA_API_TOKEN = os.environ["JIRA_API_TOKEN"]
AUTH = (JIRA_EMAIL, JIRA_API_TOKEN)

TEMPLATE_PROJECT_KEY = os.environ["JIRA_TEMPLATE_PROJECT_KEY"]
TEMPLATE_BOARD_ID = os.getenv("JIRA_TEMPLATE_BOARD_ID", "").strip()

JIRA_CLI_PATH = os.getenv("JIRA_CLI_PATH", "jira")
REPORT_FILE = "cli_column_report.json"


@dataclass
class TargetColumn:
    name: str
    statuses: list[str]  # ID statusów


# --------------------------------------------------------------------------- #
# Odczyt wzorca — te same, już sprawdzone funkcje przez REST (bez zmian
# względem bulk_configure_columns.py, bo ta część zawsze działała poprawnie;
# zmienia się tylko sposób ZAPISU, niżej).
# --------------------------------------------------------------------------- #

def find_board_id(project_key: str) -> "int | None":
    url = f"{JIRA_BASE_URL}/rest/agile/1.0/board"
    resp = requests.get(url, auth=AUTH, params={"projectKeyOrId": project_key}, timeout=30)
    resp.raise_for_status()
    values = resp.json().get("values", [])
    return values[0]["id"] if values else None


def list_boards_for_project(project_key: str) -> list[dict]:
    url = f"{JIRA_BASE_URL}/rest/agile/1.0/board"
    resp = requests.get(url, auth=AUTH, params={"projectKeyOrId": project_key}, timeout=30)
    resp.raise_for_status()
    return resp.json().get("values", [])


def resolve_template_board_id() -> int:
    if TEMPLATE_BOARD_ID:
        return int(TEMPLATE_BOARD_ID)
    boards = list_boards_for_project(TEMPLATE_PROJECT_KEY)
    if not boards:
        print(f"Nie znaleziono żadnej tablicy dla projektu wzorcowego '{TEMPLATE_PROJECT_KEY}'.")
        sys.exit(1)
    if len(boards) == 1:
        return boards[0]["id"]
    print(f"UWAGA: projekt wzorcowy ma {len(boards)} tablic — ustaw JIRA_TEMPLATE_BOARD_ID w .env. Kandydaci:")
    for b in boards:
        print(f"  - id {b['id']}: '{b['name']}'")
    sys.exit(1)


def get_board_columns(board_id: int) -> list[TargetColumn]:
    url = f"{JIRA_BASE_URL}/rest/agile/1.0/board/{board_id}/configuration"
    resp = requests.get(url, auth=AUTH, timeout=30)
    resp.raise_for_status()
    columns = resp.json().get("columnConfig", {}).get("columns", [])
    result = []
    for col in columns:
        statuses = [s["self"].rsplit("/", 1)[-1] for s in col.get("statuses", [])]
        result.append(TargetColumn(name=col.get("name", ""), statuses=statuses))
    return result


def get_status_names(status_ids: list[str]) -> dict[str, str]:
    names = {}
    for sid in status_ids:
        url = f"{JIRA_BASE_URL}/rest/api/3/status/{sid}"
        resp = requests.get(url, auth=AUTH, timeout=30)
        if resp.status_code == 200:
            names[sid] = resp.json()["name"]
    return names


def get_board_name(board_id: int) -> str:
    url = f"{JIRA_BASE_URL}/rest/agile/1.0/board/{board_id}"
    resp = requests.get(url, auth=AUTH, timeout=30)
    resp.raise_for_status()
    return resp.json()["name"]


# --------------------------------------------------------------------------- #
# Zapis — przez Jira CLI (subprocess), zamiast przeglądarki
# --------------------------------------------------------------------------- #

def run_cli(action: str, extra_args: list[str], dry_run: bool) -> tuple[bool, str]:
    """Woła Jira CLI. Zwraca (sukces, output). W trybie dry-run tylko pokazuje
    komendę, nic nie wykonuje."""
    cmd = [
        JIRA_CLI_PATH,
        "-s", JIRA_BASE_URL,
        "--user", JIRA_EMAIL,
        "--token", JIRA_API_TOKEN,
        "--action", action,
        *extra_args,
    ]
    printable = " ".join(
        c if c != JIRA_API_TOKEN else "***" for c in cmd
    )
    if dry_run:
        print(f"    [DRY-RUN] {printable}")
        return True, ""

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    output = (result.stdout or "") + (result.stderr or "")
    success = result.returncode == 0
    if not success:
        print(f"    [BŁĄD CLI] {printable}\n      -> {output.strip()}")
    return success, output


def get_current_column_names_via_cli(board_name: str, dry_run: bool) -> list[str]:
    """Odczyt aktualnych kolumn PRZEZ CLI (żeby sprawdzić idempotentnie, co już
    istnieje) — w dry-run nie da się tego sprawdzić naprawdę, więc zwraca []
    (skrypt w dry-run pokaże wszystkie planowane komendy, nawet dla już
    istniejących kolumn)."""
    if dry_run:
        return []
    success, output = run_cli(
        "getBoardColumnList", ["--board", board_name, "--outputFormat", "999", "--columns", "Name"],
        dry_run=False,
    )
    if not success:
        return []
    # outputFormat 999 = jedna wartość na linię, bez nagłówka - dopasuj, jeśli
    # Twoja wersja CLI formatuje to inaczej (sprawdź ręcznie: dodaj print(output)).
    return [line.strip() for line in output.splitlines() if line.strip()]


def apply_columns_via_cli(project_key: str, board_id: int, target_columns: list[TargetColumn],
                           status_names: dict[str, str], dry_run: bool) -> bool:
    board_name = get_board_name(board_id)
    existing_names = {n.upper() for n in get_current_column_names_via_cli(board_name, dry_run)}

    all_ok = True
    for target in target_columns:
        status_list = ",".join(status_names.get(sid, sid) for sid in target.statuses)
        if not status_list:
            continue  # pusta kolumna (np. Backlog) - nic do zmapowania

        if target.name.upper() in existing_names:
            # Kolumna już istnieje - zaktualizuj mapowanie statusów
            ok, _ = run_cli(
                "updateBoardColumn",
                ["--board", board_name, "--column", target.name, "--status", status_list],
                dry_run,
            )
        else:
            # Nowa kolumna - utwórz z od razu przypisanymi statusami
            ok, _ = run_cli(
                "addBoardColumn",
                ["--board", board_name, "--column", target.name, "--status", status_list],
                dry_run,
            )
        all_ok = all_ok and ok

    return all_ok


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def get_project_keys(args) -> list[str]:
    if args.project_keys:
        return [k.strip().upper() for k in args.project_keys.split(",") if k.strip()]
    sheet_rows = core.get_sheet_rows()
    cache = core.load_key_cache()
    keys = []
    for row in sheet_rows:
        norm_name = core.normalize(row.project_name)
        key = row.jira_key or cache.get(norm_name)
        if key:
            keys.append(key)
    return keys


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--project-keys", type=str, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"Odczytuję docelowy układ kolumn z projektu wzorcowego '{TEMPLATE_PROJECT_KEY}'...")
    template_board_id = resolve_template_board_id()
    print(f"  Używam tablicy id {template_board_id}.")
    target_columns = get_board_columns(template_board_id)
    all_status_ids = [sid for c in target_columns for sid in c.statuses]
    status_names = get_status_names(all_status_ids)

    print("Docelowy układ kolumn:")
    for c in target_columns:
        names = [status_names.get(sid, sid) for sid in c.statuses]
        print(f"  {c.name}: {', '.join(names)}")

    project_keys = get_project_keys(args)
    if args.limit:
        project_keys = project_keys[: args.limit]
    print(f"\nProjektów do przetworzenia: {len(project_keys)}")
    if args.dry_run:
        print("TRYB DRY-RUN — pokazuję komendy, nic nie wykonuję.\n")

    report = []
    for i, key in enumerate(project_keys):
        print(f"\n[{i + 1}/{len(project_keys)}] {key}")
        board_id = find_board_id(key)
        if not board_id:
            print("  Brak tablicy — pomijam.")
            report.append({"project": key, "status": "brak_tablicy"})
            continue

        try:
            ok = apply_columns_via_cli(key, board_id, target_columns, status_names, args.dry_run)
            status = "ok" if ok else "blad"
            print(f"  {status.upper()}")
            report.append({"project": key, "status": status})
        except Exception as exc:  # noqa: BLE001
            print(f"  BŁĄD: {exc}")
            report.append({"project": key, "status": "blad", "error": str(exc)})

    Path(REPORT_FILE).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "=" * 60)
    print("PODSUMOWANIE")
    print(f"  OK:            {sum(1 for r in report if r['status'] == 'ok')}")
    print(f"  Błędy:         {sum(1 for r in report if r['status'] == 'blad')}")
    print(f"  Brak tablicy:  {sum(1 for r in report if r['status'] == 'brak_tablicy')}")
    print(f"\nSzczegóły w {REPORT_FILE}")


if __name__ == "__main__":
    main()
