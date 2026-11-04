#!/usr/bin/env python3
"""
diagnose_subtask_types.py

Read-only. Dla podanych projektów pokazuje WSZYSTKIE typy zadań poziomu
-1 (podzadania) - id, nazwa, opis. Zbudowane po tym, jak
get_regular_subtask_type_id() (w convert_to_epic.py) zwróciło legacy typ
(id=10059, "Migrated...") jako "wybrany" dla GSESLA - podejrzenie: w tym
projekcie może NIE ISTNIEĆ żaden inny (nie-legacy) typ podzadania, więc
funkcja cicho spada na fallback (pierwszy z listy = legacy).

Użycie:
    python diagnose_subtask_types.py --project-keys GSESLA,HGGMIN,MBJFGG
"""
import argparse
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core


def get_issue_types(project_key: str, level: int) -> list:
    proj_resp = requests.get(f"{core.JIRA_BASE_URL}/rest/api/3/project/{project_key}", auth=core.jira_auth())
    proj_resp.raise_for_status()
    numeric_id = proj_resp.json()["id"]
    resp = requests.get(
        f"{core.JIRA_BASE_URL}/rest/api/3/issuetype/project", auth=core.jira_auth(),
        params={"projectId": numeric_id, "level": level},
    )
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-keys", required=True)
    args = parser.parse_args()

    for project_key in [k.strip() for k in args.project_keys.split(",") if k.strip()]:
        print(f"\n=== {project_key} ===")
        try:
            subtask_types = get_issue_types(project_key, level=-1)
        except Exception as exc:  # noqa: BLE001
            print(f"  [BŁĄD] {exc}")
            continue
        if not subtask_types:
            print("  BRAK żadnego typu poziomu -1 (podzadania) w tym projekcie!")
            continue
        for t in subtask_types:
            legacy = "MIGRATED" in t.get("description", "").upper()
            print(f"  id={t['id']:<8} name={t['name']!r:<20} legacy={legacy}  opis={t.get('description', '')!r}")
        n_regular = sum(1 for t in subtask_types if "migrated" not in t.get("description", "").lower())
        print(f"  -> {len(subtask_types)} typ(y) łącznie, {n_regular} nie-legacy")


if __name__ == "__main__":
    main()
