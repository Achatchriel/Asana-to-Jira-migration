#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
clean_stale_task_state.py

Sprawdza WSZYSTKIE wpisy w task_sync_state.json i weryfikuje, czy klucz
Jiry, na który wskazują, FAKTYCZNIE istnieje. Usuwa te, które nie istnieją
("nieaktualne") - dzięki temu przy kolejnej synchronizacji te zadania z
Asany zostaną poprawnie UTWORZONE OD NOWA, zamiast wiecznie kończyć się
błędem 404 przy próbie "aktualizacji"/zmiany statusu nieistniejącego
zadania (dokładnie ten błąd, który powtarzał się dla WGIFOP-*, SSLESN-*
i prawdopodobnie innych projektów).

Sprawdzanie WSADOWE przez JQL (key in (...)) - setki kluczy jednym
zapytaniem, zamiast osobnego zapytania na każdy (dużo szybsze dla dużej
skali danych).

Użycie:
    python clean_stale_task_state.py --dry-run     # tylko pokaż, co jest nieaktualne
    python clean_stale_task_state.py                # faktycznie wyczyść
"""
import argparse
import json
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core

STATE_FILE = Path(__file__).resolve().parent / "task_sync_state.json"
BATCH_SIZE = 100  # ile kluczy sprawdzać jednym zapytaniem JQL naraz


def check_keys_exist(keys: list) -> set:
    """Zwraca PODZBIÓR podanych kluczy, które FAKTYCZNIE istnieją w Jirze."""
    existing = set()
    for i in range(0, len(keys), BATCH_SIZE):
        batch = keys[i:i + BATCH_SIZE]
        jql = "key in (" + ",".join(batch) + ")"
        resp = requests.get(
            f"{core.JIRA_BASE_URL}/rest/api/3/search/jql",
            auth=core.jira_auth(), params={"jql": jql, "maxResults": BATCH_SIZE, "fields": "key"},
        )
        if resp.status_code != 200:
            print(f"   [OSTRZEŻENIE] Błąd sprawdzania partii {i}-{i+len(batch)}: {resp.status_code} {resp.text[:200]}")
            continue
        for issue in resp.json().get("issues", []):
            existing.add(issue["key"])
    return existing


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Tylko pokaż, co jest nieaktualne, nic nie usuwaj.")
    args = parser.parse_args()

    if not STATE_FILE.exists():
        print(f"[BŁĄD] Nie znaleziono {STATE_FILE}.")
        sys.exit(1)

    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    print(f"Wczytano stan dla {len(state)} projekt(ów).")

    all_refs = []  # (jira_project_key, asana_gid, jira_issue_key)
    for jira_project_key, project_state in state.items():
        for asana_gid, entry in project_state.items():
            issue_key = entry.get("jira_key")
            if issue_key:
                all_refs.append((jira_project_key, asana_gid, issue_key))

    print(f"Łącznie zadań do sprawdzenia: {len(all_refs)}")
    print("Sprawdzam istnienie w Jirze (wsadowo, przez JQL)...")

    all_issue_keys = [ref[2] for ref in all_refs]
    existing_keys = check_keys_exist(all_issue_keys)

    stale_refs = [ref for ref in all_refs if ref[2] not in existing_keys]
    pct = (len(stale_refs) / len(all_refs) * 100) if all_refs else 0
    print(f"\nZnaleziono {len(stale_refs)} nieaktualnych wpisów (na {len(all_refs)} łącznie, {pct:.1f}%).")

    if not stale_refs:
        print("Brak nieaktualnych wpisów — stan jest czysty.")
        return

    stale_by_project = {}
    for jira_project_key, asana_gid, issue_key in stale_refs:
        stale_by_project.setdefault(jira_project_key, []).append(issue_key)

    print("\nNieaktualne wpisy per projekt:")
    for proj, keys in sorted(stale_by_project.items(), key=lambda x: -len(x[1])):
        print(f"  {proj}: {len(keys)} nieaktualnych ({keys[0]}...{keys[-1]})")

    if args.dry_run:
        print("\n[DRY-RUN] Nic nie usunięto. Uruchom bez --dry-run, żeby faktycznie wyczyścić.")
        return

    backup_path = STATE_FILE.with_suffix(".json.backup")
    backup_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nKopia zapasowa oryginalnego stanu: {backup_path}")

    print(f"Usuwam {len(stale_refs)} nieaktualnych wpisów ze stanu...")
    for jira_project_key, asana_gid, issue_key in stale_refs:
        del state[jira_project_key][asana_gid]

    state = {k: v for k, v in state.items() if v}

    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wyczyszczono. Te {len(stale_refs)} zadań zostanie poprawnie UTWORZONYCH OD NOWA "
          f"przy następnym uruchomieniu synchronizacji.")


if __name__ == "__main__":
    main()
