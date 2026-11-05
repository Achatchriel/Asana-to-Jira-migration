#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
backfill_jira_links.py

Uzupełnia kolumnę 'jira_link' w arkuszu dla WSZYSTKICH już istniejących
wierszy, które mają wypełniony 'jira_key', ale PUSTĄ (albo brakującą)
komórkę w 'jira_link'. To jednorazowy skrypt do "dogonienia" wierszy
utworzonych ZANIM kolumna 'jira_link' została dodana do arkusza -
jira_sync.py sam zapisuje link tylko dla NOWO generowanych kluczy
(patrz README, sekcja 7), nie retroaktywnie.

CELOWO NIE nadpisuje komórek, które już mają jakąkolwiek wartość w
jira_link (np. ręcznie wklejony inny link) - tylko wypełnia PUSTE.

Użycie:
    python backfill_jira_links.py --dry-run   # pokaż plan, nic nie zapisuj
    python backfill_jira_links.py             # faktyczny zapis
"""
import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Tylko pokaż plan, nic nie zapisuj.")
    args = parser.parse_args()

    creds = service_account.Credentials.from_service_account_file(
        core.GOOGLE_APPLICATION_CREDENTIALS, scopes=core.SHEETS_SCOPES
    )
    service = build("sheets", "v4", credentials=creds)

    tab_name = core.GOOGLE_SHEET_RANGE.split("!")[0]
    print("Wczytuję arkusz...")
    result = service.spreadsheets().values().get(
        spreadsheetId=core.GOOGLE_SHEET_ID, range=core.GOOGLE_SHEET_RANGE
    ).execute()
    values = result.get("values", [])
    if not values:
        print("Arkusz jest pusty.")
        return

    header = [h.strip().lower() for h in values[0]]
    if "jira_key" not in header:
        print("[BŁĄD] Kolumna 'jira_key' nie istnieje w arkuszu.")
        sys.exit(1)
    if "jira_link" not in header:
        print("[BŁĄD] Kolumna 'jira_link' nie istnieje w arkuszu - dodaj ją najpierw w nagłówku.")
        sys.exit(1)

    key_idx = header.index("jira_key")
    link_idx = header.index("jira_link")
    link_col_letter = core._col_index_to_letter(link_idx)

    to_fill = []  # [(row_num, jira_key), ...]
    already_has_link = 0
    no_key = 0
    for i, raw_row in enumerate(values[1:], start=2):
        key = raw_row[key_idx].strip() if key_idx < len(raw_row) else ""
        link = raw_row[link_idx].strip() if link_idx < len(raw_row) else ""
        if not key:
            no_key += 1
            continue
        if link:
            already_has_link += 1
            continue
        to_fill.append((i, key))

    print(f"\nWierszy z kluczem, ale BEZ linku (do uzupełnienia): {len(to_fill)}")
    print(f"Wierszy z kluczem, które JUŻ MAJĄ link (pomijane): {already_has_link}")
    print(f"Wierszy bez klucza w ogóle (pomijane): {no_key}")

    if not to_fill:
        print("\nNic do zrobienia.")
        return

    if args.dry_run:
        print("\n[DRY-RUN] Przykłady (pierwsze 10) tego, co zostałoby zapisane:")
        for row_num, key in to_fill[:10]:
            print(f"  wiersz {row_num}: {key} -> {core.JIRA_BASE_URL}/browse/{key}")
        print(f"\nUruchom bez --dry-run, żeby faktycznie zapisać wszystkie {len(to_fill)}.")
        return

    data = [
        {"range": f"{tab_name}!{link_col_letter}{row_num}", "values": [[f"{core.JIRA_BASE_URL}/browse/{key}"]]}
        for row_num, key in to_fill
    ]
    print(f"\nZapisuję {len(data)} linków...")
    service.spreadsheets().values().batchUpdate(
        spreadsheetId=core.GOOGLE_SHEET_ID, body={"valueInputOption": "RAW", "data": data}
    ).execute()
    print("Gotowe.")


if __name__ == "__main__":
    main()
