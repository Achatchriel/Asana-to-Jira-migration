#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
sheet_projects.py — TYLKO ODCZYT. Zapisuje do migration_app/cache/sheet.json
projekty z arkusza Google tak, jak widzą je skrypty jiraSync: klucz z kolumny
jira_key albo z project_key_cache.json. Panel uruchamia go jako podproces,
żeby zawsze używać aktualnego .env (bez importowania skryptów do procesu serwera).

Odczyt bywa wolny (kilka minut): dla wierszy bez nazwy get_sheet_rows() pobiera
nazwę z Asany projekt po projekcie - dlatego wynik trafia do cache, z którego
korzystają panel i asana_check.py.
"""

import contextlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_FILE = Path(__file__).resolve().parent / "cache" / "sheet.json"
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import jira_sync as core  # noqa: E402


def main() -> None:
    core.check_config()
    cache = core.load_key_cache()
    rows = []
    # Komunikaty skryptów (np. [CHRONIONE]) idą na stderr, żeby nie mieszały się z wynikiem.
    with contextlib.redirect_stdout(sys.stderr):
        sheet_rows = core.get_sheet_rows()
    for row in sheet_rows:
        rows.append({
            "name": row.project_name,
            "jira_key": (row.jira_key or cache.get(core.normalize(row.project_name)) or "").upper(),
            "key_from_sheet": bool(row.jira_key),
            "asana_link": row.asana_project_link,
            "board_template_id": row.board_template_id,
            "sheet_row": row.sheet_row_number,
        })
    CACHE_FILE.parent.mkdir(exist_ok=True)
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"rows": rows, "loaded_at": time.time()}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, CACHE_FILE)
    print(f"Zapisano {len(rows)} wierszy arkusza do {CACHE_FILE.relative_to(ROOT)}", file=sys.stderr)


if __name__ == "__main__":
    main()
