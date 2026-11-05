#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
remove_status_from_column.py

Zdejmuje status z kolumny tablicy, tak że wraca do "niezmapowanych"
(Unmapped statuses). Domyślnie: status "Backlog" z kolumny "Backlog".
Pozostałe statusy tej kolumny i wszystkie inne kolumny zostają bez zmian.

Zmiana kolumn tablicy nie jest dostępna w oficjalnym API, więc zapis idzie
tym samym wewnętrznym mechanizmem co przypisywanie statusów w copy_project.py
i run_column_assignments.py (przeglądarka z zapisaną sesją, auth_state.json).
Plan (które tablice, co się zmieni) liczony jest przez oficjalne API, bez
przeglądarki.

Użycie:
    python remove_status_from_column.py                                # raport, projekty z arkusza
    python remove_status_from_column.py --project-keys A,B             # raport, wskazane projekty
    python remove_status_from_column.py --project-keys A --apply       # zmiana
    python remove_status_from_column.py --board-ids 7863,7864 --apply  # konkretne tablice
    python remove_status_from_column.py --column "To Do" --status "Open" --project-keys A
"""
import argparse
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import copy_project as cp  # noqa: E402  - ten sam, sprawdzony mechanizm zapisu kolumn
import jira_sync as core  # noqa: E402

_STATUS_NAMES: dict = {}


def status_names(ids: list) -> dict:
    missing = [i for i in ids if i not in _STATUS_NAMES]
    if missing:
        _STATUS_NAMES.update(cp.get_status_names(missing))
    return {i: _STATUS_NAMES.get(i, i) for i in ids}


def project_boards(project_key: str) -> list:
    """Tablice przypisane do projektu (lokalizacja tablicy = ten projekt)."""
    boards, start = [], 0
    while True:
        resp = requests.get(f"{cp.JIRA_BASE_URL}/rest/agile/1.0/board", auth=cp.jira_auth(), timeout=30,
                            params={"projectKeyOrId": project_key, "startAt": start, "maxResults": 50})
        resp.raise_for_status()
        data = resp.json()
        boards.extend((b["id"], b.get("name", "")) for b in data.get("values", []))
        start += len(data.get("values", []))
        if data.get("isLast", True) or not data.get("values"):
            return boards


def board_name(board_id) -> str:
    resp = requests.get(f"{cp.JIRA_BASE_URL}/rest/agile/1.0/board/{board_id}", auth=cp.jira_auth(), timeout=30)
    return resp.json().get("name", str(board_id)) if resp.status_code == 200 else str(board_id)


def snapshot(board_id) -> dict:
    """{nazwa kolumny: zbiór ID statusów} - cała konfiguracja tablicy."""
    return {c["name"]: set(c["status_ids"]) for c in cp.get_board_columns_rest(board_id)}


def plan_for_board(board_id, column: str, status: str) -> "dict | None":
    """{"column", "keep", "remove_ids", "before"} albo None, gdy nie ma czego zmieniać.
    "skip" ustawione, gdy zapis po nazwach byłby niejednoznaczny."""
    before = snapshot(board_id)
    for col_name, ids in before.items():
        if col_name.strip().lower() != column.strip().lower():
            continue
        names = status_names(sorted(ids))
        remove_ids = {i for i in ids if names[i].strip().lower() == status.strip().lower()}
        if not remove_ids:
            return None
        keep_ids = ids - remove_ids
        keep = [names[i] for i in sorted(keep_ids)]
        plan = {"column": col_name, "keep": keep, "remove_ids": remove_ids, "before": before}
        # Mechanizm zapisu dopasowuje statusy PO NAZWIE - dwa różne statusy o tej samej
        # nazwie w kolumnie zostałyby sklejone w jeden. Takiej tablicy nie ruszamy.
        lowered = [n.strip().lower() for n in keep]
        if len(lowered) != len(set(lowered)):
            plan["skip"] = "w kolumnie są dwa różne statusy o tej samej nazwie - zmień ręcznie"
        return plan
    return None


def unexpected_changes(plan: dict, after: dict) -> list:
    """Różnice między stanem po zapisie a oczekiwanym (przed, minus zdejmowany status)."""
    expected = {c: (ids - plan["remove_ids"] if c == plan["column"] else ids) for c, ids in plan["before"].items()}
    diffs = []
    for col in sorted(set(expected) | set(after)):
        lost = expected.get(col, set()) - after.get(col, set())
        gained = after.get(col, set()) - expected.get(col, set())
        if lost or gained:
            names = status_names(sorted(lost | gained))
            diffs.append(f"kolumna '{col}': " + ", ".join(
                [f"zniknął {names[i]}" for i in sorted(lost)] + [f"doszedł {names[i]}" for i in sorted(gained)]))
    return diffs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project-keys", default=None, help="Klucze po przecinku (domyślnie: projekty z arkusza).")
    parser.add_argument("--board-ids", default=None, help="Konkretne ID tablic po przecinku (zamiast projektów).")
    parser.add_argument("--column", default="Backlog", help='Nazwa kolumny (domyślnie "Backlog").')
    parser.add_argument("--status", default="Backlog", help='Status do zdjęcia z kolumny (domyślnie "Backlog").')
    parser.add_argument("--apply", action="store_true", help="Wprowadź zmiany (domyślnie tylko raport).")
    args = parser.parse_args()

    protected = core.load_protected_projects()
    if args.board_ids:
        boards = []
        for b in [b.strip() for b in args.board_ids.split(",") if b.strip()]:
            resp = requests.get(f"{cp.JIRA_BASE_URL}/rest/agile/1.0/board/{b}", auth=cp.jira_auth(), timeout=30)
            loc = ((resp.json().get("location") or {}).get("projectKey") or "").upper() if resp.status_code == 200 else ""
            if loc in protected:
                print(f"[CHRONIONE] tablica {b} należy do {loc} - pomijam.")
                continue
            boards.append((b, board_name(b)))
        sources = {b: "--board-ids" for b, _ in boards}
    else:
        keys = ([k.strip().upper() for k in args.project_keys.split(",") if k.strip() and k.strip().upper() not in protected]
                if args.project_keys
                else sorted({r.jira_key for r in core.get_sheet_rows() if r.jira_key}))
        boards, sources = [], {}
        for key in keys:
            try:
                found = project_boards(key)
            except Exception as exc:  # noqa: BLE001
                print(f"[BŁĄD] {key}: nie udało się pobrać tablic ({exc})")
                continue
            if not found:
                print(f"{key}: brak tablic przypisanych do projektu")
            for b in found:
                boards.append(b)
                sources[b[0]] = key

    print(f"\nStatus '{args.status}' z kolumny '{args.column}' -> niezmapowane. Tablic do sprawdzenia: {len(boards)}\n")
    todo = []
    for board_id, name in boards:
        try:
            plan = plan_for_board(board_id, args.column, args.status)
        except Exception as exc:  # noqa: BLE001
            print(f"  [BŁĄD] tablica {board_id} '{name}': {exc}")
            continue
        label = f"{sources.get(board_id, '')} tablica {board_id} '{name}'"
        if plan is None:
            print(f"  {label}: bez zmian (brak kolumny albo statusu w niej)")
            continue
        if plan.get("skip"):
            print(f"  {label}: POMIJAM - {plan['skip']}")
            continue
        print(f"  {label}: zdejmuję '{args.status}' z '{plan['column']}'"
              + (f", zostają: {', '.join(plan['keep'])}" if plan["keep"] else ", kolumna zostanie pusta"))
        todo.append((board_id, label, plan))

    print(f"\nDo zmiany: {len(todo)} tablic.")
    if not todo:
        return
    if not args.apply:
        print("[TRYB RAPORTU] Nic nie zmieniono. Uruchom z --apply.")
        return
    if not cp.STATE_FILE.exists():
        print(f"[BŁĄD] Brak zapisanej sesji ({cp.STATE_FILE}). Uruchom: python browser_automation/save_login_session.py")
        sys.exit(1)

    ok, failed = 0, []
    with cp.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(cp.STATE_FILE))
        page = context.new_page()
        page.goto(f"{cp.JIRA_BASE_URL}/jira/your-work", wait_until="domcontentloaded")
        if "id.atlassian.com" in page.url or "/login" in page.url:
            print("[BŁĄD] Sesja przeglądarki wygasła. Uruchom: python browser_automation/save_login_session.py")
            sys.exit(1)
        if not cp.CLOUD_ID:
            cp.CLOUD_ID = cp.resolve_cloud_id(page)
        for board_id, label, plan in todo:
            try:
                cp.assign_statuses_via_browser(page, board_id, {plan["column"]: plan["keep"]})
                # Kontrola CAŁEJ tablicy przez oficjalne API: zmienić się mogło wyłącznie
                # zdjęcie tego jednego statusu z tej jednej kolumny.
                diffs = unexpected_changes(plan, snapshot(board_id))
                if not diffs:
                    print(f"  {label}: OK (pozostałe statusy i kolumny bez zmian)")
                    ok += 1
                else:
                    print(f"  {label}: [BŁĄD] nieoczekiwana zmiana: {'; '.join(diffs)}")
                    failed.append(label)
            except Exception as exc:  # noqa: BLE001
                if "UNAUTHENTICATED" in str(exc) or "sesja przeglądarki wygasła" in str(exc):
                    # Sesja wygasła w trakcie - każda kolejna tablica skończyłaby się tym samym.
                    print(f"  {label}: [PRZERWANO] sesja przeglądarki wygasła w trakcie przebiegu.\n"
                          f"  Odśwież ją: python browser_automation/save_login_session.py\n"
                          f"  i uruchom tę samą komendę ponownie - tablice już zmienione zostaną pominięte "
                          f"jako 'bez zmian'.")
                    failed.append(label)
                    break
                print(f"  {label}: [BŁĄD] {exc}")
                failed.append(label)
        browser.close()
    print(f"\nZmieniono: {ok}, błędy: {len(failed)}")


if __name__ == "__main__":
    main()
