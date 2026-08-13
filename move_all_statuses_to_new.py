#!/usr/bin/env python3
"""
move_all_statuses_to_new.py

Osobny, celowo UPROSZCZONY skrypt: przenosi WSZYSTKIE statusy na tablicy
(niezależnie od tego, w której kolumnie aktualnie się znajdują, także te
z sekcji "Unmapped statuses") do JEDNEJ, STAŁEJ kolumny docelowej — "New In".

Dlaczego to osobny skrypt, a nie tryb w bulk_configure_columns.py:
- Tam każdy status miał INNĄ kolumnę docelową, co wymagało zawodnego
  przenoszenia po ID kolumny i skończyło się usunięciem tej funkcji.
- Tu cel jest zawsze TEN SAM, więc dużo mniej może pójść nie tak — ale
  mechanizm przeciągania w tej wersji Jiry i tak jest kapryśny, więc
  używamy tego samego, sprawdzonego na żywo mechanizmu klawiaturowego
  (Space -> strzałki -> Space) z weryfikacją pozycji i przez REST API,
  plus retry.

Wymaga:
- auth_state.json z zapisaną sesją (uruchom najpierw save_login_session.py,
  tak jak dla bulk_configure_columns.py).
- .env skonfigurowany tak samo jak dla bulk_configure_columns.py
  (JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN).

Użycie:
    python move_all_statuses_to_new.py --limit 1 --project-keys SSLES   # test
    python move_all_statuses_to_new.py                                   # wszystkie z arkusza
    python move_all_statuses_to_new.py --target-column "New In"          # inna nazwa kolumny
"""
import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import requests
from dotenv import load_dotenv
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jira_sync as core  # reużywamy odczytu arkusza i cache'u kluczy

JIRA_BASE_URL = os.environ["JIRA_BASE_URL"].rstrip("/")
JIRA_EMAIL = os.environ["JIRA_EMAIL"]
JIRA_API_TOKEN = os.environ["JIRA_API_TOKEN"]
AUTH = (JIRA_EMAIL, JIRA_API_TOKEN)
STATE_FILE = "auth_state.json"
REPORT_FILE = "move_all_to_new_report.json"

SELECTORS = {
    # Patrz bulk_configure_columns.py — samo [data-testid="column.data.test.id"]
    # pasuje TAKŻE do panelu "Kanban backlog" (nie prawdziwa kolumna), stąd
    # bardziej precyzyjny selektor wymagający rodzica column.header...
    "column": '[data-testid="column.header.data.test.id"] > [data-testid="column.data.test.id"]',
    "column_title": '[data-testid="column.header.title.data.test.id"]',
}

TIMEOUT_MS = 15_000


@dataclass
class ColumnState:
    name: str
    statuses: list[str]


def find_board_id(project_key: str) -> "int | None":
    url = f"{JIRA_BASE_URL}/rest/agile/1.0/board"
    resp = requests.get(url, auth=AUTH, params={"projectKeyOrId": project_key}, timeout=30)
    resp.raise_for_status()
    values = resp.json().get("values", [])
    return values[0]["id"] if values else None


def get_board_columns(board_id: int) -> list[ColumnState]:
    url = f"{JIRA_BASE_URL}/rest/agile/1.0/board/{board_id}/configuration"
    resp = requests.get(url, auth=AUTH, timeout=30)
    resp.raise_for_status()
    columns = resp.json().get("columnConfig", {}).get("columns", [])
    result = []
    for col in columns:
        statuses = [s["self"].rsplit("/", 1)[-1] for s in col.get("statuses", [])]
        result.append(ColumnState(name=col.get("name", ""), statuses=statuses))
    return result


def get_all_project_status_ids(project_key: str) -> list[str]:
    """Zwraca WSZYSTKIE statusy dostępne w projekcie (dla wszystkich typów
    zadań), niezależnie od tego, czy są aktualnie przypisane do jakiejś
    kolumny na tablicy, czy siedzą w "Unmapped statuses"."""
    url = f"{JIRA_BASE_URL}/rest/api/3/project/{project_key}/statuses"
    resp = requests.get(url, auth=AUTH, timeout=30)
    resp.raise_for_status()
    ids = set()
    for issue_type in resp.json():
        for status in issue_type.get("statuses", []):
            ids.add(str(status["id"]))
    return sorted(ids)


def norm(s: str) -> str:
    return s.strip().upper()


def open_columns_config(page: Page, project_key: str, board_id: int) -> None:
    url = f"{JIRA_BASE_URL}/jira/software/c/projects/{project_key}/boards/{board_id}/settings/columns"
    page.goto(url, timeout=TIMEOUT_MS)
    page.wait_for_selector(SELECTORS["column"], timeout=TIMEOUT_MS)
    # Panel "Unmapped statuses" donosi swoją zawartość ASYNCHRONICZNIE, już PO
    # wyrenderowaniu kolumn - bez tego czasem "nie znajdowało" statusów, które
    # chwilę później były już obecne.
    try:
        page.wait_for_selector('[data-rbd-draggable-id^="STATUS::"]', timeout=TIMEOUT_MS)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(500)


def find_status_card_by_id(page: Page, status_id: str, max_scroll_attempts: int = 15):
    """Znajduje kartę statusu po ID (data-rbd-draggable-id="STATUS::<id>::...").
    Jeśli jest w długiej, przewijanej sekcji "Unmapped statuses", przewija ją,
    żeby wymusić wyrenderowanie karty w DOM."""
    locator = page.locator(f'[data-rbd-draggable-id^="STATUS::{status_id}::"]').first
    if locator.count() > 0:
        return locator

    unmapped_section = page.locator('[data-testid="unmapped.status.column.section.data.test.id"]').first
    if unmapped_section.count() > 0:
        for _ in range(max_scroll_attempts):
            locator = page.locator(f'[data-rbd-draggable-id^="STATUS::{status_id}::"]').first
            if locator.count() > 0:
                return locator
            try:
                unmapped_section.evaluate("el => el.scrollBy(0, 200)")
            except Exception:  # noqa: BLE001
                break
            page.wait_for_timeout(150)

    return page.locator(f'[data-rbd-draggable-id^="STATUS::{status_id}::"]').first


def find_target_column_index(page: Page, target_name: str) -> "int | None":
    titles = page.locator(SELECTORS["column_title"]).all_inner_texts()
    for idx, title in enumerate(titles):
        if norm(title) == norm(target_name):
            return idx
    return None


def move_status_to_column_by_keyboard(page: Page, status_id: str, target_column_index: int) -> bool:
    """Przenosi status KLAWIATURĄ (Space -> strzałki -> Space) — sprawdzony na
    żywo mechanizm. Sprawdza kierunek na starcie (karta może być na lewo
    LUB na prawo od celu, w zależności od tego, gdzie aktualnie siedzi)."""
    card = find_status_card_by_id(page, status_id)
    if card.count() == 0:
        return False

    card.scroll_into_view_if_needed(timeout=TIMEOUT_MS)
    page.wait_for_timeout(400)
    # WAŻNE: oficjalna instrukcja dostępności Jiry mówi o dojściu do karty
    # klawiszem Tab, nie o programowym .focus(). Używamy .click() (prawdziwa
    # interakcja użytkownika, ustawia fokus tak jak zrobiłby to Tab), zamiast
    # .focus() na poziomie JS, które mogło nie uruchamiać tego samego
    # wewnętrznego stanu potrzebnego, żeby Space zarejestrowało "uniesienie".
    card.click(timeout=TIMEOUT_MS)
    page.wait_for_timeout(200)
    page.keyboard.press("Space")
    page.wait_for_timeout(600)

    target_column = page.locator(SELECTORS["column"]).nth(target_column_index)
    target_box_initial = target_column.bounding_box()
    card_box_initial = card.bounding_box()
    direction = "ArrowLeft"
    if card_box_initial and target_box_initial and card_box_initial["x"] <= target_box_initial["x"]:
        direction = "ArrowRight"

    max_presses = 30
    aligned = False
    for _ in range(max_presses):
        card_box = card.bounding_box()
        col_box = target_column.bounding_box()
        if card_box and col_box:
            card_left = card_box["x"]
            card_right = card_box["x"] + card_box["width"]
            fully_inside = col_box["x"] <= card_left and card_right <= col_box["x"] + col_box["width"]
            if fully_inside:
                page.wait_for_timeout(300)
                card_box2 = card.bounding_box()
                if card_box2:
                    still_inside = (
                        col_box["x"] <= card_box2["x"]
                        and card_box2["x"] + card_box2["width"] <= col_box["x"] + col_box["width"]
                    )
                    if still_inside:
                        aligned = True
                        break
        page.keyboard.press(direction)
        page.wait_for_timeout(450)

    if not aligned:
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        return False

    page.wait_for_timeout(1000)
    page.keyboard.press("Space")
    page.wait_for_timeout(1000)
    return True


def process_project(page: Page, project_key: str, board_id: int, target_column_name: str) -> str:
    """Zwraca status: 'ok' / 'niezgodnosc' / 'brak_kolumny'."""
    open_columns_config(page, project_key, board_id)

    target_index = find_target_column_index(page, target_column_name)
    if target_index is None:
        print(f"  [OSTRZEŻENIE] Nie znaleziono kolumny '{target_column_name}' na tej tablicy — pomijam projekt.")
        return "brak_kolumny"

    all_status_ids = get_all_project_status_ids(project_key)
    print(f"  Statusów do przeniesienia: {len(all_status_ids)}")

    failures = []
    for status_id in all_status_ids:
        # Pomiń, jeśli status JUŻ jest w kolumnie docelowej (odczyt na żywo z REST).
        current = get_board_columns(board_id)
        already_there = any(
            status_id in c.statuses for c in current if norm(c.name) == norm(target_column_name)
        )
        if already_there:
            continue

        success = False
        for _attempt in range(2):
            moved = move_status_to_column_by_keyboard(page, status_id, target_index)
            if not moved:
                continue
            page.wait_for_timeout(600)
            current = get_board_columns(board_id)
            if any(status_id in c.statuses for c in current if norm(c.name) == norm(target_column_name)):
                success = True
                break
        if not success:
            failures.append(status_id)
            print(f"    [OSTRZEŻENIE] Nie udało się przenieść statusu id={status_id}.")

    return "ok" if not failures else "niezgodnosc"


def get_project_keys(args) -> list[str]:
    if args.project_keys:
        return [k.strip().upper() for k in args.project_keys.split(",") if k.strip()]
    sheet_rows = core.get_sheet_rows()
    cache = core.load_key_cache()
    keys = []
    for row in sheet_rows:
        norm_name = core.normalize(row.project_name)
        key = row.jira_key or cache.get(norm_name)
        if key:
            keys.append(key)
    return keys


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--project-keys", type=str, default=None)
    parser.add_argument("--target-column", type=str, default="New In")
    parser.add_argument("--interactive", action="store_true", help="Tryb widoczny przeglądarki.")
    args = parser.parse_args()

    if not Path(STATE_FILE).exists():
        print(f"Brak pliku {STATE_FILE}. Uruchom najpierw: python save_login_session.py")
        sys.exit(1)

    project_keys = get_project_keys(args)
    if args.limit:
        project_keys = project_keys[: args.limit]
    print(f"Projektów do przetworzenia: {len(project_keys)}")
    print(f"Kolumna docelowa: '{args.target_column}'")

    report = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.interactive)
        context = browser.new_context(storage_state=STATE_FILE)
        page = context.new_page()

        for i, key in enumerate(project_keys):
            print(f"\n[{i + 1}/{len(project_keys)}] {key}")
            board_id = find_board_id(key)
            if not board_id:
                print("  Brak tablicy — pomijam.")
                report.append({"project": key, "status": "brak_tablicy"})
                continue

            try:
                status = process_project(page, key, board_id, args.target_column)
                print(f"  {status.upper()}")
                report.append({"project": key, "status": status})
            except Exception as exc:  # noqa: BLE001
                print(f"  BŁĄD: {exc}")
                report.append({"project": key, "status": "blad", "error": str(exc)})

        browser.close()

    Path(REPORT_FILE).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "=" * 60)
    print("PODSUMOWANIE")
    print(f"  OK:            {sum(1 for r in report if r['status'] == 'ok')}")
    print(f"  Niezgodność:   {sum(1 for r in report if r['status'] == 'niezgodnosc')}")
    print(f"  Brak kolumny:  {sum(1 for r in report if r['status'] == 'brak_kolumny')}")
    print(f"  Błędy:         {sum(1 for r in report if r['status'] == 'blad')}")
    print(f"  Brak tablicy:  {sum(1 for r in report if r['status'] == 'brak_tablicy')}")
    print(f"\nSzczegóły w {REPORT_FILE}")


if __name__ == "__main__":
    main()
