#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
refresh_state_flags.py

Odświeża flagi hierarchii w task_sync_state.json na podstawie AKTUALNEGO
stanu w Jirze:
  - is_epic                 = zgłoszenie jest dziś Epikiem (poziom 1)
  - is_subtask              = zgłoszenie jest dziś Sub-taskiem (poziom -1)
  - top_level_ancestor_key  = dla Sub-taska: jego obecny rodzic w Jirze
                              (tam asana_jira_sync.py podepnie NOWE podzadania
                              z Asany, bo Sub-task nie może mieć dzieci);
                              dla pozostałych typów: usuwane.

Po co: flagi są zapisywane RAZ, przy tworzeniu zgłoszenia. Naprawy robione
bezpośrednio w Jirze (revert_false_epics.py, resolve_deep_chains.py,
retrofit_epics.py) zmieniły typy i rodziców, ale nie stan. asana_jira_sync.py
używa tych flag do decyzji, gdzie podpiąć nowe podzadania, oraz do podziału
"zaktualizowane" na Epiki/Taski/Sub-taski.

Zmienia WYŁĄCZNIE te trzy pola. Nie rusza powiązań Asana<->Jira, znaczników
czasu, zsynchronizowanych komentarzy/załączników ani wpisów deleted_in_jira.

Typy pobierane hurtowo przez JQL (100 zgłoszeń na zapytanie), nie po jednym.

Użycie:
    python refresh_state_flags.py --project-keys PROJ11,CMANBE
        (raport: co by się zmieniło - nic nie zapisuje)
    python refresh_state_flags.py --project-keys PROJ11,CMANBE --apply
        (zapis; przed zapisem kopia zapasowa task_sync_state.json)
    python refresh_state_flags.py --apply
        (wszystkie projekty ze stanu - dłużej, ok. 1 zapytanie na 100 zadań)

NIE uruchamiaj równolegle z asana_jira_sync.py ani jira_asana_sync.py.
"""
import argparse
import csv
import shutil
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from asana_jira_sync import (  # noqa: E402  - reużywamy stanu i klienta HTTP z retry
    JIRA_AUTH,
    JIRA_BASE_URL,
    REQUEST_TIMEOUT,
    STATE_FILE,
    load_state,
    request_with_retry,
    save_state,
)

REPORT_FILE = Path(__file__).resolve().parent / "refresh_state_flags_report.csv"
BATCH = 100


def _search_batch(keys: list) -> dict:
    """Zwraca {klucz: {"level": int, "parent": klucz|None, "type": nazwa}} dla
    podanych kluczy. JQL 'key in (...)' odrzuca CAŁE zapytanie, gdy choć
    jeden klucz nie istnieje - wtedy dzielimy paczkę na pół, aż do
    pojedynczych kluczy (brakujące po prostu nie trafią do wyniku)."""
    if not keys:
        return {}
    found = {}
    next_token = None
    while True:
        body = {"jql": f"key in ({','.join(keys)})", "fields": ["issuetype", "parent"], "maxResults": BATCH}
        if next_token:
            body["nextPageToken"] = next_token
        resp = request_with_retry(
            "POST", f"{JIRA_BASE_URL}/rest/api/3/search/jql", auth=JIRA_AUTH,
            json=body, timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 400:
            if len(keys) == 1:
                return {}
            mid = len(keys) // 2
            return {**_search_batch(keys[:mid]), **_search_batch(keys[mid:])}
        resp.raise_for_status()
        data = resp.json()
        for issue in data.get("issues", []):
            it = issue["fields"]["issuetype"]
            level = it.get("hierarchyLevel")
            if level is None:  # starsze odpowiedzi - wnioskujemy z flagi subtask / nazwy
                level = -1 if it.get("subtask") else (1 if it.get("name") == "Epic" else 0)
            parent = issue["fields"].get("parent")
            found[issue["key"]] = {"level": level, "parent": parent["key"] if parent else None,
                                   "type": it.get("name", "")}
        next_token = data.get("nextPageToken")
        if not next_token:
            return found


def fetch_live(keys: list, label: str) -> dict:
    live = {}
    for i in range(0, len(keys), BATCH):
        live.update(_search_batch(keys[i:i + BATCH]))
        done = min(i + BATCH, len(keys))
        if done % 1000 < BATCH or done == len(keys):
            print(f"  {label}: pobrano typy {done}/{len(keys)}")
    return live


def desired_flags(live_issue: dict) -> dict:
    is_subtask = live_issue["level"] < 0
    return {
        "is_epic": live_issue["level"] >= 1,
        "is_subtask": is_subtask,
        "top_level_ancestor_key": live_issue["parent"] if is_subtask else None,
    }


def current_flags(entry: dict) -> dict:
    return {
        "is_epic": bool(entry.get("is_epic", False)),
        "is_subtask": bool(entry.get("is_subtask", False)),
        "top_level_ancestor_key": entry.get("top_level_ancestor_key") if entry.get("is_subtask") else None,
    }


def apply_flags(entry: dict, want: dict) -> None:
    entry["is_epic"] = want["is_epic"]
    # Jawne False (nie usuwanie klucza): sync_subtasks robi setdefault("is_subtask", True)
    # dla starych wpisów - usunięty klucz zostałby tam z powrotem ustawiony na True.
    entry["is_subtask"] = want["is_subtask"]
    if want["top_level_ancestor_key"]:
        entry["top_level_ancestor_key"] = want["top_level_ancestor_key"]
    else:
        entry.pop("top_level_ancestor_key", None)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project-keys", default=None, help="Klucze projektów po przecinku (domyślnie: wszystkie w stanie).")
    parser.add_argument("--apply", action="store_true", help="Zapisz zmiany (domyślnie tylko raport).")
    args = parser.parse_args()

    state = load_state()
    if args.project_keys:
        projects = [k.strip().upper() for k in args.project_keys.split(",") if k.strip()]
        unknown = [p for p in projects if p not in state]
        if unknown:
            print(f"[OSTRZEŻENIE] Brak w task_sync_state.json: {', '.join(unknown)}")
        projects = [p for p in projects if p in state]
    else:
        projects = sorted(state.keys())
    import jira_sync as _core
    projects = _core.refuse_if_protected(projects, "refresh_state_flags")
    print(f"Projektów do sprawdzenia: {len(projects)}" + ("" if args.apply else "  [TRYB RAPORTU]"))

    changes = []   # (projekt, asana_gid, jira_key, przed, po, typ_w_jirze)
    missing = []   # (projekt, asana_gid, jira_key)
    for project in projects:
        entries = {gid: e for gid, e in state[project].items()
                   if isinstance(e, dict) and e.get("jira_key") and not e.get("deleted_in_jira")}
        if not entries:
            continue
        live = fetch_live(sorted({e["jira_key"] for e in entries.values()}), project)
        for gid, entry in entries.items():
            issue = live.get(entry["jira_key"])
            if issue is None:
                missing.append((project, gid, entry["jira_key"]))
                continue
            before, after = current_flags(entry), desired_flags(issue)
            if before != after:
                changes.append((project, gid, entry["jira_key"], before, after, issue["type"]))

    # --- raport ---
    def flag_str(f: dict) -> str:
        kind = "Epic" if f["is_epic"] else ("Sub-task" if f["is_subtask"] else "Task")
        return kind + (f" (przodek {f['top_level_ancestor_key']})" if f["top_level_ancestor_key"] else "")

    kinds = {}
    for _, _, _, before, after, _ in changes:
        k = f"{flag_str(before).split(' ')[0]} -> {flag_str(after).split(' ')[0]}"
        kinds[k] = kinds.get(k, 0) + 1
    print("\n" + "=" * 60)
    print(f"Wpisów do zmiany: {len(changes)}")
    for k, n in sorted(kinds.items(), key=lambda x: -x[1]):
        print(f"  {k}: {n}")
    print(f"Nie znaleziono w Jirze (usunięte lub przeniesione): {len(missing)}")
    print("=" * 60)
    for project, gid, key, before, after, live_type in changes[:40]:
        print(f"  {key}: {flag_str(before)}  ->  {flag_str(after)}   [w Jirze: {live_type}]")
    if len(changes) > 40:
        print(f"  ... i jeszcze {len(changes) - 40} (pełna lista w {REPORT_FILE.name})")
    if missing:
        print(f"\nBrakujące zgłoszenia NIE są zmieniane. Jeśli zostały celowo usunięte, oznacz je przez "
              f"mark_deleted_as_skipped.py. Pierwsze: {', '.join(k for _, _, k in missing[:10])}")

    with open(REPORT_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["projekt", "asana_gid", "jira_key", "typ_w_jirze", "przed", "po"])
        for project, gid, key, before, after, live_type in changes:
            w.writerow([project, gid, key, live_type, flag_str(before), flag_str(after)])
        for project, gid, key in missing:
            w.writerow([project, gid, key, "NIE ZNALEZIONO", "", ""])
    print(f"\nPełny raport: {REPORT_FILE}")

    if not args.apply:
        print("\n[TRYB RAPORTU] Nic nie zapisano. Uruchom z --apply, żeby zapisać zmiany.")
        return
    if not changes:
        print("\nNic do zapisania - flagi są aktualne.")
        return

    backup = STATE_FILE.with_name(f"{STATE_FILE.stem}.backup_{datetime.now():%Y%m%d_%H%M%S}.json")
    shutil.copy2(STATE_FILE, backup)
    print(f"\nKopia zapasowa stanu: {backup}")

    # Stan wczytany PONOWNIE tuż przed zapisem i zmieniane są tylko dotknięte
    # projekty - save_state scala na poziomie projektu, więc przekazujemy
    # wyłącznie je, żeby nie nadpisać innych projektów starszą kopią.
    fresh = load_state()
    touched = {}
    for project, gid, _, _, after, _ in changes:
        entry = fresh.get(project, {}).get(gid)
        if isinstance(entry, dict):
            apply_flags(entry, after)
            touched[project] = fresh[project]
    save_state(touched)
    print(f"Zapisano zmiany flag dla {len(changes)} wpisów w {len(touched)} projektach.")


if __name__ == "__main__":
    main()
