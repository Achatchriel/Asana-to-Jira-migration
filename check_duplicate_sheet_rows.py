#!/usr/bin/env python3
"""
check_duplicate_sheet_rows.py

Sprawdza, czy w arkuszu jest WIĘCEJ NIŻ JEDEN wiersz z tym samym
jira_key - a jeśli tak, czy mają RÓŻNE asana_project_link. To by
tłumaczyło niespójne wyniki między skryptami, które inaczej wybierają
"ten jeden właściwy" wiersz (pierwszy vs ostatni w arkuszu).

Użycie:
    python check_duplicate_sheet_rows.py           # wszystkie klucze z duplikatami
    python check_duplicate_sheet_rows.py HBGRMU    # tylko ten jeden klucz
"""
import sys
from pathlib import Path
from collections import defaultdict

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core

sheet_rows = core.get_sheet_rows()

by_key = defaultdict(list)
for row in sheet_rows:
    if row.jira_key:
        by_key[row.jira_key].append(row)

target = sys.argv[1] if len(sys.argv) > 1 else None

found_any = False
for jira_key, rows in sorted(by_key.items()):
    if target and jira_key != target:
        continue
    if len(rows) > 1:
        found_any = True
        links = {r.asana_project_link for r in rows}
        print(f"{jira_key}: {len(rows)} wierszy w arkuszu" + (" (RÓŻNE linki!)" if len(links) > 1 else " (te same linki)"))
        for r in rows:
            print(f"    wiersz {r.sheet_row_number}: project_name='{r.project_name}', asana_project_link='{r.asana_project_link}'")
            gid = core.extract_asana_project_gid(r.asana_project_link)
            print(f"        -> gid={gid}")

if not found_any:
    print(f"Brak duplikatów kluczy Jiry w arkuszu" + (f" dla {target}" if target else "") + ".")
