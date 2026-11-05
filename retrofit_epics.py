#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
retrofit_epics.py

Dla JUŻ ZMIGROWANYCH projektów: wykrywa zadania z podzadaniami podzadań
(jak detect_epic_candidates.py), i dla tych, które JUŻ MAJĄ klucz Jiry w
task_sync_state.json (czyli były już zmigrowane ZANIM istniała obsługa
Epików), retroaktywnie:
  1. Konwertuje zadanie najwyższego poziomu Task -> Epic
  2. Konwertuje jego bezpośrednie podzadania Sub-task -> Task, powiązane z Epikiem
  3. KLUCZOWE: jeśli któreś głębsze zadanie zostało wcześniej SPŁASZCZONE
     (przypięte pod dziadkiem zamiast pod prawdziwym, bezpośrednim
     rodzicem - patrz mechanizm spłaszczania w asana_jira_sync.py),
     PRZEPINA je na właściwe miejsce (zmienia pole "parent" w Jirze).

Zadania, które JESZCZE NIE mają klucza w stanie, są pomijane przez ten
skrypt - zostaną poprawnie utworzone (jako Epic/Task/Sub-task) przy
najbliższym normalnym uruchomieniu asana_jira_sync.py, pod warunkiem że
epic_candidates.json jest aktualny (uruchom najpierw detect_epic_candidates.py).

Użycie:
    python retrofit_epics.py --project-keys KLUCZ1,KLUCZ2 --dry-run
    python retrofit_epics.py --project-keys KLUCZ1,KLUCZ2
    python retrofit_epics.py --dry-run    # wszystkie projekty z arkusza
    python retrofit_epics.py              # wszystkie projekty z arkusza, faktyczna naprawa
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
import detect_epic_candidates as dec
import convert_to_epic as cte
from notify import setup_file_logging, send_slack_summary

STATE_FILE = Path(__file__).resolve().parent / "task_sync_state.json"


def get_issue_type_and_parent(issue_key: str):
    resp = requests.get(
        f"{core.JIRA_BASE_URL}/rest/api/3/issue/{issue_key}",
        auth=core.jira_auth(), params={"fields": "issuetype,parent,summary"},
    )
    if resp.status_code != 200:
        return None, None, None
    fields = resp.json().get("fields", {})
    issue_type = (fields.get("issuetype") or {}).get("name")
    parent_key = (fields.get("parent") or {}).get("key")
    summary = fields.get("summary", "")
    return issue_type, parent_key, summary


def set_parent(issue_key: str, new_parent_key: str, dry_run: bool) -> None:
    if dry_run:
        print(f"        [DRY-RUN] Zmieniłbym rodzica {issue_key} na {new_parent_key} (naprawa spłaszczenia).")
        return
    resp = requests.put(
        f"{core.JIRA_BASE_URL}/rest/api/3/issue/{issue_key}",
        auth=core.jira_auth(), headers={"Content-Type": "application/json"},
        json={"fields": {"parent": {"key": new_parent_key}}},
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"Błąd zmiany rodzica: {resp.status_code} {resp.text[:300]}")
    print(f"        {issue_key}: przepięto rodzica na {new_parent_key} (naprawiono spłaszczenie).")


def project_of(issue_key: str) -> str:
    return issue_key.split("-")[0]


def assert_same_project(child_key: str, parent_key: str, expected_project: str) -> None:
    """Zabezpieczenie przed skażonymi danymi w task_sync_state.json - jeśli
    zadanie z gid śledzonego pod projektem X ma faktycznie zapisany klucz
    Jiry z INNEGO projektu (potwierdzony ręcznie przypadek: PROJ7 próbował
    przepiąć zadanie pod PROJ6-65 - dwa RÓŻNE projekty), Jira i tak by to
    odrzuciła ("Issues... must be created in the same project as the
    parent"), ale lepiej wykryć to WCZEŚNIEJ, z jasnym komunikatem
    wskazującym na źródło problemu (skażony stan), niż dostać nieczytelny
    błąd API."""
    child_proj, parent_proj = project_of(child_key), project_of(parent_key)
    if child_proj != parent_proj or child_proj != expected_project:
        raise RuntimeError(
            f"SKAŻONE DANE W task_sync_state.json: próba powiązania {child_key} (projekt {child_proj}) "
            f"z {parent_key} (projekt {parent_proj}) w kontekście projektu {expected_project} - "
            f"to NIE może być poprawne (Jira i tak by to odrzuciła). Sprawdź task_sync_state.json "
            f"ręcznie dla tych kluczy - prawdopodobnie zadanie w Asanie jest wieloprzypisane "
            f"(multi-homed) do więcej niż jednego projektu, i state pomieszał ich klucze Jiry."
        )


def main() -> None:
    setup_file_logging("retrofit_epics")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project-keys", default=None, help="Klucze Jiry (z arkusza) po przecinku. Domyślnie: WSZYSTKIE projekty z arkusza z wypełnionym jira_key.")
    parser.add_argument("--shard", default=None, help="Format 'K/N' - dzieli listę projektów na N równoległych procesów, ten bierze K-tą część.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sheet_rows = core.get_sheet_rows()
    rows_by_key = {r.jira_key: r for r in sheet_rows if r.jira_key}

    if args.project_keys:
        jira_keys = [k.strip() for k in args.project_keys.split(",") if k.strip()]
    else:
        jira_keys = sorted(rows_by_key.keys())
        print(f"Brak --project-keys — sprawdzam WSZYSTKIE {len(jira_keys)} projekt(y) z arkusza.")

    jira_keys = core.apply_shard(jira_keys, args.shard)
    if args.shard:
        print(f"--shard {args.shard}: ten proces sprawdzi {len(jira_keys)} projekt(ów).")

    if not STATE_FILE.exists():
        print("[BŁĄD] Brak task_sync_state.json.")
        sys.exit(1)
    full_state = json.loads(STATE_FILE.read_text(encoding="utf-8"))

    def _save_state() -> None:
        """Zapisuje full_state z powrotem do task_sync_state.json.
        KLUCZOWE: bez tego, flagi is_subtask/top_level_ancestor_key
        aktualizowane niżej (po konwersji sub1 na Task i przepięciu sub2)
        żyłyby TYLKO w pamięci tego procesu i znikały po zakończeniu -
        każda KOLEJNA normalna synchronizacja (asana_jira_sync.py) nadal
        widziałaby starą flagę is_subtask=True i dalej spłaszczałaby nowo
        dodane w Asanie podzadania wprost pod Epika, mimo że retrofit
        naprawił hierarchię w samej Jirze. Zapisujemy po każdym projekcie
        (nie tylko na końcu), żeby nie stracić postępu przy błędzie
        w trakcie przetwarzania kolejnych projektów."""
        if args.dry_run:
            return  # dry-run: nic nie zmieniło się naprawdę w Jirze, nie dotykaj stanu
        STATE_FILE.write_text(json.dumps(full_state, indent=2, ensure_ascii=False), encoding="utf-8")

    epics_converted, tasks_converted, reparented, skipped_not_migrated, errors = 0, 0, 0, 0, 0

    for jira_key in jira_keys:
        row = rows_by_key.get(jira_key)
        if not row:
            print(f"[OSTRZEŻENIE] Nie znaleziono wiersza z jira_key={jira_key} w arkuszu - pomijam.")
            continue
        asana_gid = core.extract_asana_project_gid(row.asana_project_link)
        project_state = full_state.setdefault(jira_key, {})
        print(f"\n{jira_key}: sprawdzam projekt Asany (gid={asana_gid}), {len(project_state)} zadań już w stanie...")

        top_level = dec.get_asana_top_level_tasks(asana_gid)
        for top_task in top_level:
            top_gid = top_task["gid"]
            sub1_gids = dec.get_asana_subtask_gids(top_gid)
            has_deep_nesting = any(dec.get_asana_subtask_gids(g) for g in sub1_gids)
            if not has_deep_nesting:
                continue

            top_entry = project_state.get(top_gid)
            if not top_entry or not top_entry.get("jira_key"):
                print(f"  '{top_task['name']}' ma głębsze zagnieżdżenie, ale JESZCZE NIE jest zmigrowane - "
                      f"pomijam (zostanie poprawnie utworzone przy najbliższej normalnej synchronizacji).")
                skipped_not_migrated += 1
                continue

            epic_key = top_entry["jira_key"]
            try:
                current_type, _, summary = get_issue_type_and_parent(epic_key)
                print(f"\n  '{top_task['name']}' ({epic_key}, obecny typ: {current_type}):")
                if current_type != "Epic":
                    cte.convert_issue_to_epic(epic_key, summary or top_task["name"], args.dry_run)
                    epics_converted += 1
                else:
                    print(f"    Już jest Epikiem - bez zmian.")

                for sub1_gid in sub1_gids:
                    sub1_entry = project_state.get(sub1_gid)
                    if not sub1_entry or not sub1_entry.get("jira_key"):
                        continue  # zostanie poprawnie utworzone przy najbliższej synchronizacji
                    sub1_key = sub1_entry["jira_key"]
                    sub1_type, sub1_parent, _ = get_issue_type_and_parent(sub1_key)

                    try:
                        if sub1_type != "Task":
                            assert_same_project(sub1_key, epic_key, jira_key)
                            task_type_name = cte.get_non_subtask_issue_type_name(jira_key)
                            cte.convert_subtask_to_task(sub1_key, task_type_name, epic_key, args.dry_run)
                            tasks_converted += 1
                        elif sub1_parent != epic_key:
                            assert_same_project(sub1_key, epic_key, jira_key)
                            set_parent(sub1_key, epic_key, args.dry_run)
                        # KLUCZOWE: po tym punkcie sub1_key jest (albo już był, albo
                        # właśnie stał się) typu Task z rodzicem epic_key. Zaktualizuj
                        # stan lokalny, żeby normalny asana_jira_sync.py PRZESTAŁ
                        # spłaszczać jego przyszłe podzadania wprost pod Epika
                        # (mechanizm spłaszczania w sync_subtasks patrzy WYŁĄCZNIE na
                        # tę flagę "is_subtask" — bez tej aktualizacji, retrofit
                        # naprawia Jirę, ale każda KOLEJNA synchronizacja i tak
                        # zniszczy naprawę, tworząc nowe wnuki wprost pod epikiem).
                        if not args.dry_run:
                            sub1_entry["is_subtask"] = False
                            sub1_entry.pop("top_level_ancestor_key", None)
                    except Exception as exc:  # noqa: BLE001
                        print(f"    [BŁĄD] Nie udało się skonwertować/przepiąć {sub1_key}: {exc}")
                        errors += 1
                        continue  # nie próbuj dalej z tym konkretnym podzadaniem, ale przejdź do następnego

                    sub2_gids = dec.get_asana_subtask_gids(sub1_gid)
                    for sub2_gid in sub2_gids:
                        sub2_entry = project_state.get(sub2_gid)
                        if not sub2_entry or not sub2_entry.get("jira_key"):
                            continue  # zostanie poprawnie utworzone przy najbliższej synchronizacji
                        sub2_key = sub2_entry["jira_key"]
                        try:
                            _, sub2_parent, _ = get_issue_type_and_parent(sub2_key)
                            if sub2_parent != sub1_key:
                                assert_same_project(sub2_key, sub1_key, jira_key)
                                try:
                                    set_parent(sub2_key, sub1_key, args.dry_run)
                                    reparented += 1
                                except RuntimeError as set_exc:
                                    if "appropriate hierarchy" in str(set_exc) or "same project as the parent" in str(set_exc):
                                        # Prawdopodobnie legacy Subtask (id=10059, "Migrated...") - ten
                                        # sam wzorzec co przy sub1 (PROJ8-36..43). Samo set_parent
                                        # (PUT z polem parent) jest odrzucane dla tego typu. Konwertujemy
                                        # na regularny Sub-task via Bulk Move, potem ustawiamy rodzica.
                                        print(f"        [INFO] {sub2_key}: set_parent odrzucony "
                                              f"(prawdopodobnie legacy Subtask) — używam Bulk Move.")
                                        cte.bulk_convert_subtask_and_reparent(sub2_key, sub1_key, args.dry_run)
                                        reparented += 1
                                    else:
                                        raise
                            # KLUCZOWE (patrz analogiczny komentarz przy sub1 wyżej):
                            # sub2 jest teraz (albo już był) prawdziwym Sub-taskiem
                            # pod sub1_key (Task, nie Epic) — zapisz to w stanie, żeby
                            # normalny sync poprawnie spłaszczał EWENTUALNE własne
                            # podzadania sub2 (poziom 4 w Asanie) pod sub1_key, a nie
                            # pod epikiem (top_level_ancestor_key musi wskazywać
                            # najbliższego przodka, który MOŻE mieć realne dzieci
                            # w Jirze — czyli sub1_key, nie epic_key).
                            if not args.dry_run:
                                sub2_entry["is_subtask"] = True
                                sub2_entry["top_level_ancestor_key"] = sub1_key
                        except Exception as exc:  # noqa: BLE001
                            print(f"      [BŁĄD] Nie udało się przepiąć {sub2_key} -> {sub1_key}: {exc}")
                            errors += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  [BŁĄD] '{top_task['name']}' ({epic_key}): {exc}")
                errors += 1
                continue  # nie przerywaj CAŁEGO przebiegu - przejdź do następnego kandydata/projektu

        _save_state()  # zapisz postęp po KAŻDYM projekcie - nie czekaj do końca całego przebiegu

    print("\n" + "=" * 60)
    print("PODSUMOWANIE")
    print(f"  Skonwertowanych na Epic: {epics_converted}")
    print(f"  Skonwertowanych Sub-task -> Task: {tasks_converted}")
    print(f"  Przepiętych (naprawione spłaszczenie): {reparented}")
    print(f"  Pominiętych (jeszcze nie zmigrowane - obsłuży normalna synchronizacja): {skipped_not_migrated}")
    print(f"  Błędy (nie zatrzymały reszty przebiegu): {errors}")
    print("=" * 60)

    counts = {
        "epics_converted": epics_converted,
        "tasks_converted": tasks_converted,
        "reparented": reparented,
        "skipped_not_migrated": skipped_not_migrated,
        "errors": errors,
    }
    dec.SUMMARY_DIR.mkdir(exist_ok=True)
    suffix = args.shard.replace("/", "-") if args.shard else "single"
    (dec.SUMMARY_DIR / f"retrofit_epics_{suffix}.json").write_text(json.dumps(counts, indent=2), encoding="utf-8")

    send_slack_summary(
        f"retrofit_epics.py: Epic={epics_converted}, Task={tasks_converted}, "
        f"przepięte={reparented}, pominięte={skipped_not_migrated}"
        + (f" (shard {args.shard})" if args.shard else "") + "."
    )


if __name__ == "__main__":
    main()
