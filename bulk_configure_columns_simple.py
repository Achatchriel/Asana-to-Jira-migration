#!/usr/bin/env python3
"""
bulk_configure_columns_simple.py

Wariant bulk_configure_columns.py DLA PROJEKTÓW Z PROSTSZYM WZORCEM (4 kolumny:
Backlog/To Do/In Progress/Done — jedna z tablic REFERENCE2 innych niż 6110,
np. 7276), zamiast standardowego 11-kolumnowego układu.

Osobny plik (nie flaga w bulk_configure_columns.py), bo:
- Część projektów ma 11 kolumn (standardowy wzorzec, tablica 6110) —
  te obsługuje bulk_configure_columns.py bez zmian.
- Część projektów ma mieć ten prostszy, 4-kolumnowy układ — identyfikowane
  przez kolumnę "board_template_id" w arkuszu Google (patrz jira_sync.py).
  Ten skrypt przetwarza WYŁĄCZNIE wiersze, które MAJĄ wypełnioną tę kolumnę.

Reużywa funkcji z bulk_configure_columns.py (ten sam mechanizm tworzenia
kolumn i przenoszenia statusów — nic nie jest duplikowane).

Wymaga tego samego auth_state.json co bulk_configure_columns.py (patrz
save_login_session.py).

Użycie:
    python bulk_configure_columns_simple.py --dry-run --limit 1
    python bulk_configure_columns_simple.py --limit 1
    python bulk_configure_columns_simple.py --project-keys KLUCZ --template-board-id 7276
    python bulk_configure_columns_simple.py
"""
import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core  # reużywamy odczytu arkusza i cache'u kluczy

from bulk_configure_columns import (  # reużywamy sprawdzonych funkcji, bez duplikacji
    STATE_FILE,
    TIMEOUT_MS,
    SELECTORS,
    apply_target_columns,
    columns_match,
    find_board_id,
    get_board_columns,
    get_status_names,
    open_columns_config,
)

REPORT_FILE = "simple_column_report.json"


def get_project_pairs(args) -> list[tuple[str, str]]:
    """Zwraca (project_key, board_template_id) TYLKO dla wierszy arkusza,
    które MAJĄ wypełnioną kolumnę board_template_id — reszta jest pomijana
    (nie należy do tego skryptu, obsługuje ją bulk_configure_columns.py)."""
    if args.project_keys:
        keys = [k.strip().upper() for k in args.project_keys.split(",") if k.strip()]
        if not args.template_board_id:
            print("Błąd: --project-keys wymaga też --template-board-id (nie ma skąd wziąć wzorca z arkusza).")
            sys.exit(1)
        return [(k, args.template_board_id) for k in keys]

    sheet_rows = core.get_sheet_rows()
    cache = core.load_key_cache()
    reserved = set(cache.values())
    pairs = []
    for row in sheet_rows:
        board_template_id = (row.board_template_id or "").strip()
        if not board_template_id:
            continue  # ten wiersz używa standardowego wzorca - nie nasza sprawa
        key = core.assign_jira_key(row, cache, reserved)
        pairs.append((key, board_template_id))
    return pairs


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ustawia kolumny/statusy dla projektów z alternatywnym wzorcem (kolumna board_template_id w arkuszu)."
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--project-keys", type=str, default=None)
    parser.add_argument(
        "--template-board-id", type=str, default=None,
        help="Wymagane razem z --project-keys (bo bez wiersza arkusza nie ma skąd wziąć wzorca).",
    )
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-old-columns", action="store_true")
    args = parser.parse_args()

    if not Path(STATE_FILE).exists() and not args.dry_run:
        print(f"Brak pliku {STATE_FILE}. Uruchom najpierw: python save_login_session.py")
        sys.exit(1)

    project_pairs = get_project_pairs(args)
    if args.limit:
        project_pairs = project_pairs[: args.limit]
    print(f"Projektów z alternatywnym wzorcem do przetworzenia: {len(project_pairs)}")

    # Wzorce cache'owane per board_template_id (w praktyce zwykle jeden,
    # ale różne wiersze mogą wskazywać różne tablice-bliźniaki).
    template_cache: dict[str, list] = {}

    def get_template_for(board_template_id: str) -> list:
        if board_template_id not in template_cache:
            cols = get_board_columns(int(board_template_id))
            ids = [sid for c in cols for sid in c.statuses]
            names = get_status_names(ids)
            template_cache[board_template_id] = cols
            print(f"\n[Wzorzec: tablica {board_template_id}] Docelowy układ kolumn:")
            for c in cols:
                nm = [names.get(sid, sid) for sid in c.statuses]
                print(f"  {c.name}: {', '.join(nm)}")
        return template_cache[board_template_id]

    if args.dry_run:
        for key, board_template_id in project_pairs:
            target_columns = get_template_for(board_template_id)
            print(f"  [DRY-RUN] {key}: ustawiłbym kolumny wg wzorca {board_template_id} ({len(target_columns)} kolumn).")
        return

    report = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.interactive)
        context = browser.new_context(storage_state=STATE_FILE)
        page = context.new_page()

        for i, (key, board_template_id) in enumerate(project_pairs):
            print(f"\n[{i + 1}/{len(project_pairs)}] {key} (wzorzec: tablica {board_template_id})")
            target_columns = get_template_for(board_template_id)

            board_id = find_board_id(key)
            if not board_id:
                print("  Brak tablicy — pomijam.")
                report.append({"project": key, "status": "brak_tablicy"})
                continue

            try:
                is_calibration_run = args.interactive and i == 0
                open_columns_config(page, key, board_id, wait_for_columns=not is_calibration_run)

                if is_calibration_run:
                    print("  >>> TRYB PODGLĄDU: sprawdź w Playwright Inspector, potem Resume.")
                    page.pause()
                    page.wait_for_selector(SELECTORS["column"], timeout=TIMEOUT_MS)

                apply_target_columns(page, target_columns, board_id, delete_old_columns=not args.keep_old_columns)

                time.sleep(1)
                result_columns = get_board_columns(board_id)
                if columns_match(target_columns, result_columns):
                    print("  OK — układ zgodny ze wzorcem.")
                    report.append({"project": key, "status": "ok"})
                else:
                    print("  NIEZGODNOŚĆ — wymaga ręcznego sprawdzenia.")
                    report.append({"project": key, "status": "niezgodnosc"})

            except PlaywrightTimeoutError as exc:
                screenshot_path = f"error_{key}.png"
                try:
                    page.screenshot(path=screenshot_path, full_page=True)
                    print(f"  BŁĄD (timeout): {exc}\n  Zrzut ekranu zapisany do {screenshot_path}")
                except Exception:  # noqa: BLE001
                    print(f"  BŁĄD (timeout): {exc}")
                report.append({"project": key, "status": "blad", "error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                screenshot_path = f"error_{key}.png"
                try:
                    page.screenshot(path=screenshot_path, full_page=True)
                    print(f"  BŁĄD: {exc}\n  Zrzut ekranu zapisany do {screenshot_path}")
                except Exception:  # noqa: BLE001
                    print(f"  BŁĄD: {exc}")
                report.append({"project": key, "status": "blad", "error": str(exc)})

        browser.close()

    Path(REPORT_FILE).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "=" * 60)
    print("PODSUMOWANIE")
    print(f"  OK:            {sum(1 for r in report if r['status'] == 'ok')}")
    print(f"  Niezgodność:   {sum(1 for r in report if r['status'] == 'niezgodnosc')}")
    print(f"  Błędy:         {sum(1 for r in report if r['status'] == 'blad')}")
    print(f"  Brak tablicy:  {sum(1 for r in report if r['status'] == 'brak_tablicy')}")
    print(f"\nSzczegóły w {REPORT_FILE}")


if __name__ == "__main__":
    main()
