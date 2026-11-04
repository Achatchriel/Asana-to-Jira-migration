#!/usr/bin/env python3
"""
diagnose_cross_project_contamination.py

Skanuje CAŁY task_sync_state.json i szuka wpisów, gdzie zadanie śledzone
pod projektem X ma zapisany klucz Jiry Y-liczba, gdzie Y != X - czyli
zadanie z Asany "myśli", że należy do jednego projektu Jiry, ale jego
faktyczny klucz wskazuje na INNY projekt. Ten wzorzec skażenia potwierdzony
ręcznie w dwóch niezależnych parach (PROJ7/PROJ6, PROJ3/PROJ8) -
ten skrypt sprawdza, czy jest więcej takich przypadków, i pokazuje pełną
skalę na raz, zamiast odkrywać je pojedynczo przy każdym uruchomieniu
retrofit_epics.py.

Użycie:
    python diagnose_cross_project_contamination.py
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parent / "task_sync_state.json"

if not STATE_FILE.exists():
    print("[BŁĄD] Brak task_sync_state.json.")
    sys.exit(1)

state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
print(f"Sprawdzam {len(state)} projekt(ów) w stanie...\n")

contaminated_pairs = defaultdict(list)  # (tracked_project, actual_project) -> [(gid, jira_key), ...]
total_contaminated = 0
total_entries = 0

for tracked_project, project_state in state.items():
    for gid, entry in project_state.items():
        jira_key = entry.get("jira_key")
        if not jira_key:
            continue
        total_entries += 1
        actual_project = jira_key.split("-")[0]
        if actual_project != tracked_project:
            contaminated_pairs[(tracked_project, actual_project)].append((gid, jira_key))
            total_contaminated += 1

pct = (total_contaminated / total_entries * 100) if total_entries else 0
print("=" * 60)
print(f"WYNIK: {total_contaminated} skażonych wpisów na {total_entries} łącznie ({pct:.2f}%)")
print("=" * 60)

if not contaminated_pairs:
    print("\nBrak skażenia między projektami - wszystkie wpisy mają klucz Jiry zgodny z projektem, pod którym są śledzone.")
    sys.exit(0)

print(f"\nZnalezione pary projektów (śledzony -> faktyczny w kluczu), posortowane wg liczby wystąpień:")
for (tracked, actual), entries in sorted(contaminated_pairs.items(), key=lambda x: -len(x[1])):
    print(f"\n  {tracked} -> {actual}: {len(entries)} wpis(ów)")
    for gid, jira_key in entries[:5]:
        print(f"    gid={gid} -> {jira_key}")
    if len(entries) > 5:
        print(f"    ... i {len(entries) - 5} więcej")

print(f"\n\nŁĄCZNIE {len(contaminated_pairs)} par(y) skażonych projektów, {total_contaminated} wpisów do ręcznego sprawdzenia.")
print("Zanim uruchomisz retrofit_epics.py na tych projektach, warto zrozumieć PRZYCZYNĘ tego skażenia")
print("(np. czy te zadania w Asanie są faktycznie wieloprzypisane do dwóch projektów).")
