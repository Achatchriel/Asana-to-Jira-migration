#!/usr/bin/env python3
"""
diagnose_missing_projects_in_state.py

Porównuje klucze projektów Jiry z arkusza (kolumna jira_key) z kluczami
OBECNYMI jako projekty w task_sync_state.json - pokazuje, które konkretnie
brakuje i (na ile da się ustalić) dlaczego: brak jira_key w arkuszu,
brak/zły asana_project_link, projekt istnieje w Jirze ale ma zero zadań
w Asanie (stan pusty {} - usuwany przez porządkowanie), albo po prostu
nigdy nie był jeszcze synchronizowany.

Użycie:
    python diagnose_missing_projects_in_state.py
"""
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core

STATE_FILE = Path(__file__).resolve().parent / "task_sync_state.json"

print("Wczytuję arkusz...")
sheet_rows = core.get_sheet_rows()
print(f"  {len(sheet_rows)} wierszy w arkuszu łącznie.")

sheet_keys = [row.jira_key for row in sheet_rows if row.jira_key]
sheet_keys_unique = sorted(set(sheet_keys))
print(f"  {len(sheet_keys)} wierszy z wypełnionym jira_key ({len(sheet_keys_unique)} unikalnych kluczy).")

no_jira_key_rows = [row for row in sheet_rows if not row.jira_key]
if no_jira_key_rows:
    print(f"\n[INFO] {len(no_jira_key_rows)} wiersz(y) BEZ jira_key w ogóle (projekt jeszcze nie utworzony):")
    for row in no_jira_key_rows:
        print(f"    wiersz {row.sheet_row_number}: '{row.project_name}' (link: {row.asana_project_link or 'BRAK'})")

if len(sheet_keys) != len(sheet_keys_unique):
    dupes = [k for k in sheet_keys_unique if sheet_keys.count(k) > 1]
    print(f"\n[OSTRZEŻENIE] {len(dupes)} klucz(y) Jiry powtarza się w arkuszu więcej niż raz: {dupes}")
    print("  (sprawdź check_duplicate_sheet_rows.py, żeby zobaczyć dokładnie które wiersze)")

if not STATE_FILE.exists():
    print("\n[BŁĄD] Brak task_sync_state.json.")
    sys.exit(1)
state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
state_keys = set(state.keys())
print(f"\nProjektów obecnych w task_sync_state.json: {len(state_keys)}")

missing_from_state = [k for k in sheet_keys_unique if k not in state_keys]
extra_in_state = [k for k in state_keys if k not in sheet_keys_unique]

print("\n" + "=" * 60)
print(f"KLUCZE Z ARKUSZA BEZ ŻADNEGO WPISU W STANIE: {len(missing_from_state)}")
print("=" * 60)
link_by_key = {row.jira_key: row.asana_project_link for row in sheet_rows if row.jira_key}
for key in missing_from_state:
    link = link_by_key.get(key, "")
    reason = "brak asana_project_link" if not link else "link jest, ale NIGDY nie było zsynchronizowane (albo skończyło z 0 zadań i zostało wyczyszczone)"
    print(f"  {key}: {reason}")

if extra_in_state:
    print("\n" + "=" * 60)
    print(f"KLUCZE W STANIE, KTÓRYCH NIE MA (JUŻ) W ARKUSZU: {len(extra_in_state)}")
    print("=" * 60)
    for key in sorted(extra_in_state):
        print(f"  {key}")

print(f"\nPodsumowanie: {len(sheet_keys_unique)} unikalnych w arkuszu, {len(state_keys)} w stanie, "
      f"{len(missing_from_state)} brakujących, {len(extra_in_state)} nadmiarowych.")
