#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
audit_epic_candidates.py — TYLKO ODCZYT, niczego nie zmienia.

Sprawdza każdego kandydata na Epik z epic_candidates.json i wykrywa
FAŁSZYWYCH kandydatów: zadania, które w Asanie są PODZADANIAMI (mają
rodzica w tym samym projekcie), a trafiły na listę tylko dlatego, że ktoś
dodał je dodatkowo do projektu (błąd poprawiony w detect_epic_candidates.py
— przypadek PROJ11-127 pod PROJ11-107).

Dla każdego fałszywego kandydata pokazuje, co się już stało w Jirze
(obecny typ i rodzic), żeby było wiadomo, co trzeba naprawić.

Wynik: podsumowanie na ekranie + epic_candidates_audit.csv

Użycie:
    python audit_epic_candidates.py
    python audit_epic_candidates.py --project-keys PROJ11,EFSSXEG
"""
import argparse
import csv
import json
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core
import detect_epic_candidates as dec

BASE = Path(__file__).resolve().parent
CANDIDATES_FILE = BASE / "epic_candidates.json"
STATE_FILE = BASE / "task_sync_state.json"
OUT_CSV = BASE / "epic_candidates_audit.csv"
ASANA = "https://app.asana.com/api/1.0"


def asana_task(gid: str, fields: str) -> dict:
    headers = {"Authorization": f"Bearer {core.ASANA_TOKEN}"}
    resp = dec._asana_get(f"{ASANA}/tasks/{gid}", headers, {"opt_fields": fields})
    if resp.status_code != 200:
        return {}
    return resp.json().get("data", {})


def jira_type_and_parent(key: str):
    if not key:
        return "", ""
    resp = requests.get(f"{core.JIRA_BASE_URL}/rest/api/3/issue/{key}",
                        auth=core.jira_auth(), params={"fields": "issuetype,parent"})
    if resp.status_code != 200:
        return f"(HTTP {resp.status_code})", ""
    f = resp.json().get("fields", {})
    return (f.get("issuetype") or {}).get("name", ""), (f.get("parent") or {}).get("key", "")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project-keys", default=None, help="Klucze po przecinku (domyślnie wszystkie z epic_candidates.json).")
    args = p.parse_args()

    if not CANDIDATES_FILE.exists():
        sys.exit("[BŁĄD] Brak epic_candidates.json")
    candidates = json.loads(CANDIDATES_FILE.read_text(encoding="utf-8") or "{}")
    state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
    rows_by_key = {r.jira_key: r for r in core.get_sheet_rows() if r.jira_key}

    keys = ([k.strip() for k in args.project_keys.split(",") if k.strip()]
            if args.project_keys else sorted(candidates))

    total_candidates = 0
    projects_with_candidates = 0
    rows_out = []
    parent_projects_cache = {}

    for jira_key in keys:
        gids = candidates.get(jira_key) or []
        if not gids:
            continue
        projects_with_candidates += 1
        total_candidates += len(gids)
        row = rows_by_key.get(jira_key)
        if not row:
            print(f"[OSTRZEŻENIE] {jira_key}: brak w arkuszu — pomijam")
            continue
        project_gid = core.extract_asana_project_gid(row.asana_project_link)
        project_state = state.get(jira_key, {})
        print(f"{jira_key}: sprawdzam {len(gids)} kandydat(ów)...")

        for gid in gids:
            task = asana_task(gid, "name,parent.gid,parent.name")
            parent = task.get("parent") or {}
            parent_gid = parent.get("gid")
            if not parent_gid:
                continue  # prawdziwe zadanie najwyższego poziomu — kandydat OK
            if parent_gid not in parent_projects_cache:
                pt = asana_task(parent_gid, "projects.gid")
                parent_projects_cache[parent_gid] = {pr["gid"] for pr in pt.get("projects", [])}
            if project_gid not in parent_projects_cache[parent_gid]:
                continue  # rodzic w innym projekcie — tu faktycznie szczyt hierarchii

            cand_key = (project_state.get(gid) or {}).get("jira_key", "")
            parent_key = (project_state.get(parent_gid) or {}).get("jira_key", "")
            jtype, jparent = jira_type_and_parent(cand_key)
            damaged = jtype == "Epic"
            rows_out.append({
                "projekt": jira_key,
                "kandydat": task.get("name", ""),
                "kandydat_jira": cand_key,
                "typ_w_jirze": jtype,
                "rodzic_w_jirze": jparent,
                "rodzic_asana": parent.get("name", ""),
                "rodzic_powinien_byc": parent_key,
                "wymaga_naprawy": "TAK" if damaged or (parent_key and jparent != parent_key) else "nie",
            })
            print(f"  FAŁSZYWY: '{task.get('name')}' {cand_key or '(brak w Jirze)'} "
                  f"— typ: {jtype or '-'}, rodzic w Jirze: {jparent or '-'}, "
                  f"powinien być pod: {parent_key or parent.get('name')}")

    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["projekt", "kandydat", "kandydat_jira", "typ_w_jirze",
                                          "rodzic_w_jirze", "rodzic_asana", "rodzic_powinien_byc",
                                          "wymaga_naprawy"])
        w.writeheader()
        w.writerows(rows_out)

    affected = sorted({r["projekt"] for r in rows_out})
    to_fix = [r for r in rows_out if r["wymaga_naprawy"] == "TAK"]
    print("\n" + "=" * 60)
    print(f"Projektów z kandydatami:        {projects_with_candidates}")
    print(f"Kandydatów łącznie:             {total_candidates}")
    print(f"FAŁSZYWYCH kandydatów:          {len(rows_out)} w {len(affected)} projekt(ach)")
    print(f"  z tego już zepsutych w Jirze: {len(to_fix)}")
    if affected:
        print(f"Projekty do ponownego wykrycia: {','.join(affected)}")
        print(f"  python detect_epic_candidates.py --project-keys {','.join(affected)} --force")
    print(f"Szczegóły: {OUT_CSV.name}")


if __name__ == "__main__":
    main()
