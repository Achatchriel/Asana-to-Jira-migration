#!/usr/bin/env python3
"""
count_state_entries.py

Pomocniczy skrypt: liczy, ile zadań jest zmapowanych w task_sync_state.json
dla danego klucza projektu Jira - i pokazuje pierwsze kilka par (Asana gid ->
Jira key), żeby można było ręcznie sprawdzić w Asanie, czy te gid-y faktycznie
należą do właściwego projektu.

Użycie:
    python count_state_entries.py KLUCZPROJEKTU
"""
import json
import sys
from pathlib import Path

STATE_FILE = Path("task_sync_state.json")


def main() -> None:
    if len(sys.argv) < 2:
        print("Użycie: python count_state_entries.py KLUCZPROJEKTU")
        sys.exit(1)

    project_key = sys.argv[1]
    if not STATE_FILE.exists():
        print(f"Brak pliku {STATE_FILE}.")
        sys.exit(1)

    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    entries = state.get(project_key, {})

    print(f"Liczba zmapowanych zadań dla '{project_key}': {len(entries)}")
    print("\nPrzykładowe wpisy (pierwsze 10):")
    for i, (asana_gid, info) in enumerate(entries.items()):
        if i >= 10:
            break
        print(f"  asana_gid={asana_gid} -> {info.get('jira_key')} (modified_at={info.get('asana_modified_at')})")


if __name__ == "__main__":
    main()
