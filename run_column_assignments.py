#!/usr/bin/env python3
"""
run_column_assignments.py

Pełna automatyzacja end-to-end przypisywania statusów do kolumn — łączy:
1. generate_column_assignments.py (odczyt arkusza + wzorców przez zwykłe API) -
   generuje listę {boardId, projectKey, mapping} dla każdego projektu.
2. assign_statuses_to_columns.js (odkryty wewnętrzny endpoint GreenHopper) -
   wykonywany PRZEZ PLAYWRIGHT (page.evaluate) w prawdziwej, zalogowanej
   sesji przeglądarki (auth_state.json) - bez ręcznego wklejania w konsoli.

To NIE jest automatyzacja przeciągania myszą (ta okazała się zbyt zawodna) -
to bezpośrednie wywołanie tego samego zapytania sieciowego, którego używa
UI Jiry wewnętrznie, tylko sterowane programowo. Wymaga wcześniej zapisanej
sesji logowania: python save_login_session.py

UWAGA: to wciąż NIEOFICJALNE, wewnętrzne API Atlassiana (patrz komentarz na
górze assign_statuses_to_columns.js) - może się zdezaktualizować po
aktualizacji Jiry. Testuj najpierw na małej próbce (--limit).

Użycie:
    python run_column_assignments.py --limit 1 --project-keys KLUCZ   # test na 1 projekcie
    python run_column_assignments.py --limit 5                         # test na próbce z arkusza
    python run_column_assignments.py                                   # wszystkie projekty
"""
import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from generate_column_assignments import get_mapping_for_template, get_project_pairs
from bulk_configure_columns import JIRA_BASE_URL, STATE_FILE, find_board_id

REPORT_FILE = "column_assignment_report.json"
JS_LIBRARY_PATH = Path(__file__).resolve().parent / "assign_statuses_to_columns.js"


def build_configs(args) -> list[dict]:
    project_pairs = get_project_pairs(args)
    if args.limit:
        project_pairs = project_pairs[: args.limit]

    configs = []
    print(f"Generuję konfigurację dla {len(project_pairs)} projekt(ów)...")
    for i, (key, board_template_id) in enumerate(project_pairs):
        print(f"  [{i + 1}/{len(project_pairs)}] {key}")
        board_id = find_board_id(key)
        if not board_id:
            print(f"    [OSTRZEŻENIE] Brak tablicy dla {key} — pomijam.")
            continue
        mapping = get_mapping_for_template(board_template_id)
        configs.append({"boardId": board_id, "projectKey": key, "mapping": mapping})
    return configs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--project-keys", type=str, default=None)
    parser.add_argument("--template-board-id", type=str, default=None)
    parser.add_argument("--delay-ms", type=int, default=800, help="Przerwa między projektami (ms).")
    args = parser.parse_args()

    if not Path(STATE_FILE).exists():
        print(f"Brak pliku {STATE_FILE}. Uruchom najpierw: python save_login_session.py")
        sys.exit(1)

    configs = build_configs(args)
    if not configs:
        print("Brak projektów do przetworzenia.")
        return

    print(f"\nWykonuję przez przeglądarkę (Playwright) dla {len(configs)} projekt(ów)...")
    js_library = JS_LIBRARY_PATH.read_text(encoding="utf-8")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=STATE_FILE)
        page = context.new_page()
        page.goto(f"{JIRA_BASE_URL}/jira/your-work", wait_until="domcontentloaded")

        # Wstrzykuje definicje funkcji (assignStatusesToColumns, assignStatusesToManyBoards)
        # do kontekstu strony - dokładnie to, co robiłbyś ręcznie wklejając w konsoli.
        page.evaluate(js_library)

        # Wywołuje właściwą funkcję wsadową, przekazując wygenerowaną konfigurację
        # i przerwę między projektami jako argumenty.
        report = page.evaluate(
            "([configs, delayMs]) => assignStatusesToManyBoards(configs, delayMs)",
            [configs, args.delay_ms],
        )

        browser.close()

    Path(REPORT_FILE).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    ok = sum(1 for r in report if r["status"] == "ok")
    failed = [r for r in report if r["status"] != "ok"]
    print("\n" + "=" * 60)
    print("PODSUMOWANIE")
    print(f"  OK:    {ok}")
    print(f"  Błędy: {len(failed)}")
    if failed:
        for f in failed:
            print(f"    - {f.get('projectKey', f.get('boardId'))}: {f.get('error')}")
    print(f"\nSzczegóły w {REPORT_FILE}")


if __name__ == "__main__":
    main()
