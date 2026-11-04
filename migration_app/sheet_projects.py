#!/usr/bin/env python3
"""
sheet_projects.py — TYLKO ODCZYT. Wypisuje (JSON na stdout) projekty z arkusza
Google tak, jak widzą je skrypty jiraSync: klucz z kolumny jira_key albo z
project_key_cache.json. Panel uruchamia go jako podproces, żeby zawsze używać
aktualnego .env (bez importowania skryptów do procesu serwera).
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import jira_sync as core  # noqa: E402


def main() -> None:
    core.check_config()
    cache = core.load_key_cache()
    rows = []
    for row in core.get_sheet_rows():
        rows.append({
            "name": row.project_name,
            "jira_key": (row.jira_key or cache.get(core.normalize(row.project_name)) or "").upper(),
            "key_from_sheet": bool(row.jira_key),
            "asana_link": row.asana_project_link,
            "board_template_id": row.board_template_id,
            "sheet_row": row.sheet_row_number,
        })
    json.dump({"rows": rows}, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
