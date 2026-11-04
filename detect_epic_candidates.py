#!/usr/bin/env python3
"""
detect_epic_candidates.py

URUCHOM PRZED MIGRACJĄ. Dla podanych projektów sprawdza w Asanie, które
zadania najwyższego poziomu mają PODZADANIA PODZADAŃ (3 poziomy
zagnieżdżenia) - Jira nie obsługuje tego wprost (podzadanie nie może mieć
własnego podzadania), więc takie zadania trzeba utworzyć jako Epic (żeby
poziom 1 mógł być zwykłym Task, powiązanym z Epikiem, a poziom 2 - jego
podzadaniem).

Wynik zapisywany do epic_candidates.json - asana_jira_sync.py CZYTA ten
plik (jeśli istnieje) i tworzy oznaczone zadania jako Epic zamiast Task.

Użycie:
    python detect_epic_candidates.py --project-keys KLUCZ1,KLUCZ2
"""
import argparse
import fcntl
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core
from notify import setup_file_logging, send_slack_summary
from pipeline_summary import is_pipeline_child

OUTPUT_FILE = Path(__file__).resolve().parent / "epic_candidates.json"
SUMMARY_DIR = Path(__file__).resolve().parent / "pipeline_summaries"


def load_existing_candidates() -> dict:
    """Wczytuje obecną zawartość epic_candidates.json (dla wznawiania -
    patrz --force w main). Pusty słownik gdy pliku nie ma lub jest pusty."""
    if not OUTPUT_FILE.exists():
        return {}
    try:
        content = OUTPUT_FILE.read_text(encoding="utf-8")
        return json.loads(content) if content.strip() else {}
    except Exception:  # noqa: BLE001
        return {}


def save_candidates_for_project(jira_key: str, epic_gids: list) -> None:
    """Bezpieczny zapis WYNIKU JEDNEGO projektu do wspólnego
    epic_candidates.json - blokada pliku (fcntl.flock) + scalenie z tym, co
    jest już na dysku (per klucz projektu), zamiast bezwarunkowego
    nadpisania całości. Bez tego kilka równoległych --shard nadpisywałoby
    nawzajem swoje wyniki (ten sam problem, który rozwiązuje save_state()
    w asana_jira_sync.py dla task_sync_state.json)."""
    OUTPUT_FILE.touch(exist_ok=True)
    lock_path = OUTPUT_FILE.with_suffix(".lock")
    with open(lock_path, "w") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        try:
            content = OUTPUT_FILE.read_text(encoding="utf-8")
            on_disk = json.loads(content) if content.strip() else {}
            on_disk[jira_key] = epic_gids
            tmp_path = OUTPUT_FILE.with_suffix(".json.tmp")
            tmp_path.write_text(json.dumps(on_disk, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp_path, OUTPUT_FILE)
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)


import pipeline_summary as _ps  # wspólne podsumowanie kroku (z błędami) dla run_full_pipeline.py


def write_shard_summary(shard_arg: "str | None", counts: dict) -> None:
    """Zapisuje liczbowe podsumowanie TEGO procesu (jednego sharda albo
    całości, jeśli --shard nie było użyte) do osobnego pliku w
    pipeline_summaries/ - run_full_pipeline.py odczytuje WSZYSTKIE takie
    pliki dla danego kroku po zakończeniu wszystkich równoległych procesów,
    sumuje liczby, i wysyła JEDNO zbiorcze podsumowanie zamiast N osobnych."""
    # Wspólny moduł: nazwa pliku z PIPELINE_SHARD (osobny plik na proces także przy
    # --project-keys) i dołączone błędy/ostrzeżenia zapisane przez _ps.record_*.
    _ps.write_shard_summary("detect_epic_candidates", shard_arg, counts)


_ASANA_TIMEOUT = 60  # sekund - podwojone względem pierwotnych 30
_ASANA_MAX_RETRIES = 4


def _asana_get(url: str, headers: dict, params: dict) -> "requests.Response":
    """Wrapper z retry i wykładniczym backoff dla zapytań do Asana API.
    Timeout Asany bywa chwilowy (szczyt ruchu, duże projekty) - kilka prób
    wystarczy żeby go ominąć bez przerywania całego przebiegu."""
    delay = 5
    last_exc = None
    for attempt in range(1, _ASANA_MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=_ASANA_TIMEOUT)
            return resp
        except (requests.exceptions.ReadTimeout,
                requests.exceptions.ConnectionError) as exc:
            last_exc = exc
            if attempt < _ASANA_MAX_RETRIES:
                print(f"      [OSTRZEŻENIE] Błąd sieci Asana ({type(exc).__name__}, próba {attempt}/{_ASANA_MAX_RETRIES}) "
                      f"— czekam {delay}s i próbuję ponownie...")
                time.sleep(delay)
                delay *= 2
    raise last_exc


def get_asana_top_level_tasks(project_gid):
    """Zwraca zadania NAJWYŻSZEGO poziomu projektu.

    UWAGA: /projects/{gid}/tasks zwraca KAŻDE zadanie należące do projektu —
    także podzadania, które ktoś dodatkowo dodał do projektu (w Asanie
    podzadanie może być członkiem projektu i wtedy widać je na liście).
    Bez filtra takie podzadanie z własnymi podzadaniami wyglądało na zadanie
    najwyższego poziomu i zostawało Epikiem — potwierdzony przypadek:
    AKMPUN-127 (podzadanie AKMPUN-107) zamienione na Epik, przez co retrofit
    najpierw zrobił z niego Task pod 107, a chwilę później Epik.

    Wykluczamy zadania, których rodzic JEST w tym samym projekcie (tamte
    obsłuży przejście przez rodzica). Jeśli rodzic jest w innym projekcie,
    zadanie zostaje — w tym projekcie jest faktycznie szczytem hierarchii."""
    headers = {"Authorization": f"Bearer {core.ASANA_TOKEN}"}
    url = f"https://app.asana.com/api/1.0/projects/{project_gid}/tasks"
    tasks = []
    offset = None
    while True:
        params = {"opt_fields": "gid,name,parent.gid", "limit": 100}
        if offset:
            params["offset"] = offset
        resp = _asana_get(url, headers, params)
        resp.raise_for_status()
        payload = resp.json()
        tasks.extend(payload.get("data", []))
        offset = payload.get("next_page", {}).get("offset") if payload.get("next_page") else None
        if not offset:
            break

    project_gids = {t["gid"] for t in tasks}
    top_level, nested = [], []
    for t in tasks:
        parent_gid = (t.get("parent") or {}).get("gid")
        (nested if parent_gid in project_gids else top_level).append(t)
    if nested:
        print(f"  [INFO] Pomijam {len(nested)} podzadań dodanych do projektu (mają rodzica "
              f"w tym projekcie — NIE są zadaniami najwyższego poziomu): "
              + ", ".join(f"'{t['name']}'" for t in nested[:10])
              + (" ..." if len(nested) > 10 else ""))
    return top_level


def get_asana_subtask_gids(task_gid):
    headers = {"Authorization": f"Bearer {core.ASANA_TOKEN}"}
    url = f"https://app.asana.com/api/1.0/tasks/{task_gid}/subtasks"
    resp = _asana_get(url, headers, {"opt_fields": "gid", "limit": 100})
    if resp.status_code != 200:
        return []
    return [t["gid"] for t in resp.json().get("data", [])]


def main() -> None:
    setup_file_logging("detect_epic_candidates")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project-keys", default=None, help="Klucze Jiry (z arkusza) po przecinku. Domyślnie: WSZYSTKIE projekty z arkusza z wypełnionym jira_key.")
    parser.add_argument("--shard", default=None, help="Format 'K/N' - dzieli listę projektów na N równoległych procesów, ten bierze K-tą część.")
    parser.add_argument("--force", action="store_true",
                        help="Przeskanuj wszystkie projekty od nowa, nawet te już obecne w "
                             "epic_candidates.json. Domyślnie skrypt WZNAWIA - pomija już zapisane "
                             "projekty, żeby po padzie można było po prostu uruchomić go ponownie.")
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

    if not args.force:
        # Wznawianie: pomijamy projekty JUŻ zapisane w epic_candidates.json.
        # Dzięki temu po padzie sharda (timeout/DNS/zerwane połączenie) można po
        # prostu uruchomić ten sam shard ponownie - dokończy od miejsca, w którym
        # skończył, zamiast skanować wszystko od zera. --force wymusza pełny
        # ponowny skan (np. gdy struktura w Asanie się zmieniła).
        already_done = set(load_existing_candidates().keys())
        skipped = [k for k in jira_keys if k in already_done]
        jira_keys = [k for k in jira_keys if k not in already_done]
        if skipped:
            print(f"[WZNAWIANIE] Pomijam {len(skipped)} projekt(ów) już obecnych w "
                  f"{OUTPUT_FILE.name} — zostało {len(jira_keys)} do sprawdzenia. "
                  f"(Użyj --force, żeby przeskanować wszystko od nowa.)")

    total_candidates = 0
    failed_projects = 0
    for jira_key in jira_keys:
      try:
        row = rows_by_key.get(jira_key)
        if not row:
            print(f"[OSTRZEŻENIE] Nie znaleziono wiersza z jira_key={jira_key} w arkuszu - pomijam.")
            continue
        gid = core.extract_asana_project_gid(row.asana_project_link)
        print(f"\n{jira_key}: sprawdzam projekt Asany (gid={gid})...")

        top_level = get_asana_top_level_tasks(gid)
        print(f"  {len(top_level)} zadań najwyższego poziomu — sprawdzam podzadania każdego...")

        epic_gids = []
        for i, task in enumerate(top_level, 1):
            sub_gids = get_asana_subtask_gids(task["gid"])
            has_deep_nesting = any(get_asana_subtask_gids(sub_gid) for sub_gid in sub_gids)
            if has_deep_nesting:
                epic_gids.append(task["gid"])
                print(f"    KANDYDAT NA EPIC: '{task['name']}' (gid={task['gid']}) - ma podzadanie z własnymi podzadaniami")
            if i % 50 == 0:
                print(f"    ...{i}/{len(top_level)} sprawdzonych")

        save_candidates_for_project(jira_key, epic_gids)
        total_candidates += len(epic_gids)
        print(f"  Znaleziono {len(epic_gids)} kandydat(ów) na Epic w {jira_key}.")
      except Exception as exc:  # noqa: BLE001 - jeden projekt nie przerywa całego kroku
        failed_projects += 1
        print(f"  [BŁĄD] {jira_key}: {exc}")
        _ps.record_error(jira_key, "wykrywanie Epików", str(exc)[:300])

    print(f"\nZapisano do {OUTPUT_FILE}. asana_jira_sync.py automatycznie to uwzględni przy najbliższym uruchomieniu.")
    write_shard_summary(args.shard, {"sprawdzone_projekty": len(jira_keys), "kandydaci_na_epic": total_candidates,
                                     "bledy": failed_projects})
    if not is_pipeline_child():
        send_slack_summary(
            f"detect_epic_candidates.py: sprawdzono {len(jira_keys)} projekt(ów)"
            + (f" (shard {args.shard})" if args.shard else "")
            + f", znaleziono {total_candidates} kandydat(ów) na Epic."
        )


if __name__ == "__main__":
    main()
