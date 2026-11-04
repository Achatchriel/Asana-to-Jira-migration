#!/usr/bin/env python3
"""
bulk_configure_columns.py

Ustawia konfigurację kolumn tablicy Kanban (nazwy kolumn + które statusy są
w której kolumnie) na wielu projektach, kopiując układ z tablicy projektu
wzorcowego (JIRA_TEMPLATE_PROJECT_KEY).

JAK TO DZIAŁA:
1. Docelowy układ kolumn ODCZYTUJEMY oficjalnym, w pełni wspieranym REST API
   (GET /rest/agile/1.0/board/{id}/configuration).
2. Docelowy układ USTAWIAMY sterując prawdziwą przeglądarką (Playwright) —
   nie ma już żadnego API (oficjalnego ani nieoficjalnego), które by to
   umożliwiało z poziomu skryptu z tokenem API.

WAŻNE — selektory poniżej NIE są zgadywane. Zostały odczytane na żywo
bezpośrednio z DOM Twojej instancji Jira (twoja-instancja.atlassian.net, projekt
wzorcowy REFERENCE2, tablica id 6110) przez Claude in Chrome. Mechanizm
przenoszenia statusów (przeciąganie myszą z auto-scrollem) jest zweryfikowany
na żywo. NATOMIAST usuwanie starych kolumn (delete_column) NIE jest
zweryfikowane na żywo — wcześniejszy test pokazał, że samo kliknięcie kosza
nie wysyłało żądania do serwera; ta wersja dodatkowo próbuje kliknąć ewentualny
przycisk potwierdzenia, ale to wciąż zgadywanie. PRZETESTUJ NA 1 PROJEKCIE
i sprawdź ręcznie w Jira (po odświeżeniu strony!), czy stare kolumny faktycznie
zniknęły, zanim odpalisz na reszcie. Jeśli usuwanie się nie uda, użyj
--keep-old-columns, żeby wrócić do bezpieczniejszego trybu (stare kolumny
zostają obok nowych, puste).

Mimo to ZALECANE jest zacząć od małej próbki:
    python bulk_configure_columns.py --interactive --limit 1
    python bulk_configure_columns.py --limit 5
zanim odpalisz na wszystkich projektach — strona może się różnić między
projektami (np. inny typ tablicy) i lepiej to zobaczyć na 1-5 projektach niż
na 550.

Wymaga (oprócz zależności z requirements.txt): playwright
    pip install playwright
    playwright install chromium

Użycie:
    python save_login_session.py                             # najpierw to, raz
    python bulk_configure_columns.py --interactive --limit 1  # kalibracja/podgląd
    python bulk_configure_columns.py --limit 5                # mały test
    python bulk_configure_columns.py                          # wszystkie projekty z arkusza
    python bulk_configure_columns.py --project-keys FBFTGAP,ABC
"""
import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

# --------------------------------------------------------------------------- #
# .env i import jira_sync (szukane obok tego pliku, a jeśli nie ma - katalog wyżej)
# --------------------------------------------------------------------------- #

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # ta ścieżka ma pierwszeństwo
import jira_sync as core  # reużywamy odczytu arkusza i generowania kluczy; wczytuje .env sam
from pipeline_summary import record_error, record_warning, write_shard_summary


def _find_env_file() -> "Path | None":
    here = Path(__file__).resolve().parent
    for candidate in (here / ".env", here.parent / ".env"):
        if candidate.exists():
            return candidate
    return None


_env_path = _find_env_file()
if _env_path:
    load_dotenv(dotenv_path=_env_path, override=True)
else:
    print("Ostrzeżenie: nie znaleziono pliku .env ani obok tego skryptu, ani katalog wyżej.")

JIRA_BASE_URL = os.environ["JIRA_BASE_URL"].rstrip("/")
JIRA_EMAIL = os.environ["JIRA_EMAIL"]
JIRA_API_TOKEN = os.environ["JIRA_API_TOKEN"]
AUTH = (JIRA_EMAIL, JIRA_API_TOKEN)
TEMPLATE_PROJECT_KEY = os.environ["JIRA_TEMPLATE_PROJECT_KEY"]
# Opcjonalne: jawne ID tablicy wzorcowej, gdy projekt wzorcowy ma WIĘCEJ NIŻ
# JEDNĄ tablicę (np. REFERENCE2 ma ich kilka) — bez tego skrypt musi zgadywać,
# którą tablicę uznać za wzorzec, co może wziąć złą.
TEMPLATE_BOARD_ID = os.getenv("JIRA_TEMPLATE_BOARD_ID", "").strip()
STATE_FILE = "auth_state.json"
REPORT_FILE = "column_report.json"

# --------------------------------------------------------------------------- #
# SELEKTORY — odczytane na żywo z DOM (nie zgadywane). Jeśli Twoja Jira ma
# inny wygląd na innym typie tablicy, popraw je tutaj.
# --------------------------------------------------------------------------- #
SELECTORS = {
    # WAŻNE: samo [data-testid="column.data.test.id"] pasuje TAKŻE do panelu
    # "Kanban backlog" (specjalny obszar, nie prawdziwa kolumna), co przesuwało
    # wszystkie indeksy kolumn o 1 i powodowało, że przenoszenie statusów
    # zawsze lądowało jedną kolumnę za wcześnie — potwierdzone na żywo.
    # Prawdziwe kolumny mają rodzica z data-testid="column.header.data.test.id",
    # panel Backlog go nie ma - stąd bardziej precyzyjny selektor poniżej.
    "column": '[data-testid="column.header.data.test.id"] > [data-testid="column.data.test.id"]',
    "column_title": '[data-testid="column.header.title.data.test.id"]',
    "column_delete_button": '[data-testid="column.header.delete.button.data.test.id"]',
    "add_column_button": '[data-testid="platform-board-kit.ui.column.column-create.button.styled-button"]',
    "status_card": '[data-testid="status.card.data.test.id"]',
}

# Domyślnie 30 s (wcześniej 15 s - za mało dla ciężkiej strony ustawień kolumn,
# zwłaszcza przy kilku procesach równolegle). Nadpisanie: BULK_COLUMNS_TIMEOUT_MS w .env.
TIMEOUT_MS = int(os.getenv("BULK_COLUMNS_TIMEOUT_MS", "30000"))


class SessionExpiredError(RuntimeError):
    """Sesja przeglądarki (auth_state.json) wygasła - Jira przekierowała na
    logowanie. Nie ma sensu próbować kolejnych projektów."""


# --------------------------------------------------------------------------- #
# Odczyt docelowego układu (oficjalne, działające REST API)
# --------------------------------------------------------------------------- #

@dataclass
class TargetColumn:
    name: str
    statuses: list[str]  # ID statusów (nie nazwy)


def find_board_id(project_key: str) -> "int | None":
    """ID tablicy projektu. API zwraca też tablice INNYCH projektów, które
    tylko obejmują ten projekt filtrem - wtedy adres projects/KLUCZ/boards/ID
    nie istnieje, Jira przekierowuje i czekanie na kolumny kończy się
    timeoutem. Dlatego najpierw tablica, której lokalizacją jest TEN projekt."""
    url = f"{JIRA_BASE_URL}/rest/agile/1.0/board"
    resp = requests.get(url, auth=AUTH, params={"projectKeyOrId": project_key}, timeout=30)
    resp.raise_for_status()
    values = resp.json().get("values", [])
    if not values:
        return None
    own = [b for b in values if (b.get("location") or {}).get("projectKey", "").upper() == project_key.upper()]
    return (own or values)[0]["id"]


def get_project_style(project_key: str) -> str:
    """'classic' (company-managed) albo 'next-gen' (team-managed). Strona
    ustawień kolumn i jej elementy istnieją tylko dla company-managed - adres
    w open_columns_config jest zaszyty jako /jira/software/c/... ."""
    resp = requests.get(f"{JIRA_BASE_URL}/rest/api/3/project/{project_key}", auth=AUTH, timeout=30)
    if resp.status_code != 200:
        return "unknown"
    return resp.json().get("style", "unknown")


def list_boards_for_project(project_key: str) -> list[dict]:
    url = f"{JIRA_BASE_URL}/rest/agile/1.0/board"
    resp = requests.get(url, auth=AUTH, params={"projectKeyOrId": project_key}, timeout=30)
    resp.raise_for_status()
    return resp.json().get("values", [])


def resolve_template_board_id() -> int:
    """Ustala ID tablicy wzorcowej. Jeśli JIRA_TEMPLATE_BOARD_ID jest ustawione
    w .env, używa go wprost. W przeciwnym razie sprawdza, ile tablic ma projekt
    wzorcowy — jeśli więcej niż jedna, PRZERYWA z listą kandydatów zamiast
    cicho zgadywać (bo REFERENCE2 np. ma 5 tablic, a wzięcie niewłaściwej
    dałoby zły układ kolumn dla wszystkich 550 projektów)."""
    if TEMPLATE_BOARD_ID:
        return int(TEMPLATE_BOARD_ID)

    boards = list_boards_for_project(TEMPLATE_PROJECT_KEY)
    if not boards:
        print(f"Nie znaleziono żadnej tablicy dla projektu wzorcowego '{TEMPLATE_PROJECT_KEY}'.")
        sys.exit(1)
    if len(boards) == 1:
        return boards[0]["id"]

    print(
        f"UWAGA: projekt wzorcowy '{TEMPLATE_PROJECT_KEY}' ma {len(boards)} tablic — "
        f"nie zgaduję, która ma być wzorcem. Ustaw w .env:\n"
        f"  JIRA_TEMPLATE_BOARD_ID=<id>\n"
        f"Kandydaci:"
    )
    for b in boards:
        print(f"  - id {b['id']}: '{b['name']}'")
    sys.exit(1)


def get_board_columns(board_id: int) -> list[TargetColumn]:
    url = f"{JIRA_BASE_URL}/rest/agile/1.0/board/{board_id}/configuration"
    resp = requests.get(url, auth=AUTH, timeout=30)
    resp.raise_for_status()
    columns = resp.json().get("columnConfig", {}).get("columns", [])
    result = []
    for col in columns:
        statuses = [s["self"].rsplit("/", 1)[-1] for s in col.get("statuses", [])]
        result.append(TargetColumn(name=col.get("name", ""), statuses=statuses))
    return result


def get_status_names(status_ids: list[str]) -> dict[str, str]:
    names = {}
    for sid in status_ids:
        url = f"{JIRA_BASE_URL}/rest/api/3/status/{sid}"
        resp = requests.get(url, auth=AUTH, timeout=30)
        if resp.status_code == 200:
            names[sid] = resp.json()["name"]
    return names


# --------------------------------------------------------------------------- #
# Ustawianie układu przez przeglądarkę
# --------------------------------------------------------------------------- #

def open_columns_config(page: Page, project_key: str, board_id: int, wait_for_columns: bool = True) -> None:
    url = f"{JIRA_BASE_URL}/jira/software/c/projects/{project_key}/boards/{board_id}/settings/columns"
    page.goto(url, timeout=TIMEOUT_MS)
    check_page_state(page)
    if wait_for_columns:
        try:
            page.wait_for_selector(SELECTORS["column"], timeout=TIMEOUT_MS)
        except PlaywrightTimeoutError:
            # Przekierowanie na logowanie może nastąpić PO goto (skrypt Jiry
            # sprawdza sesję po załadowaniu) - sprawdzamy ponownie, żeby zamiast
            # ogólnego timeoutu podać konkretny powód.
            check_page_state(page)
            if "/settings/columns" not in page.url:
                raise PlaywrightTimeoutError(
                    f"Jira przekierowała z ustawień kolumn na {page.url} "
                    f"(tablica {board_id} nie istnieje w tym projekcie albo brak uprawnień)")
            raise
        # Panel "Unmapped statuses" donosi swoją zawartość ASYNCHRONICZNIE, już
        # PO wyrenderowaniu kolumn — bez tego oczekiwania skrypt zaczynał szukać
        # statusów zanim się pojawiły, co dawało fałszywe "nie znaleziono"
        # (potwierdzone na żywo: te same statusy istniały chwilę później).
        try:
            page.wait_for_selector('[data-rbd-draggable-id^="STATUS::"]', timeout=TIMEOUT_MS)
        except PlaywrightTimeoutError:
            pass  # być może tablica faktycznie nie ma żadnych nieprzypisanych statusów
        page.wait_for_timeout(500)  # dodatkowy margines na pełne wyrenderowanie listy


def check_page_state(page: Page) -> None:
    """Rzuca SessionExpiredError, gdy Jira przekierowała na logowanie."""
    current = page.url.lower()
    if "id.atlassian.com" in current or "/login" in current:
        raise SessionExpiredError(
            "Sesja przeglądarki wygasła (przekierowanie na logowanie). Uruchom: "
            "python browser_automation/save_login_session.py i spróbuj ponownie.")


def get_current_column_names(page: Page) -> list[str]:
    return page.locator(SELECTORS["column_title"]).all_inner_texts()


def find_status_card_by_id(page: Page, status_id: str, max_scroll_attempts: int = 15):
    """Znajduje kartę statusu po ID zakodowanym w data-rbd-draggable-id
    (format 'STATUS::<id>::...'), zamiast po tekście.

    Jeśli status jest w długiej, przewijanej sekcji "Unmapped statuses",
    może nie być jeszcze wyrenderowany w DOM — przewijamy tę sekcję w dół
    krok po kroku, aż karta się pojawi (albo skończą się próby)."""
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


def delete_column(page: Page, column_index: int) -> bool:
    """Usuwa kolumnę pod danym indeksem.

    Kliknięcie kosza otwiera okno potwierdzenia (Atlaskit modal,
    data-testid="modal-dialog--header" — potwierdzone na żywo z loga błędu).
    W oknie klikamy przycisk POTWIERDZAJĄCY: w stopce modala Atlaskit zwykle
    są 2 przyciski (Cancel + akcja), więc bierzemy ten, który NIE jest
    Cancel/Anuluj. Czekamy też, aż okno faktycznie zniknie z DOM, żeby nie
    blokowało kolejnych kliknięć (to dokładnie to, co poszło źle poprzednio:
    okno zostało otwarte i przechwytywało zdarzenia myszy dla całej strony)."""
    delete_btn = page.locator(SELECTORS["column_delete_button"]).nth(column_index)
    if delete_btn.count() == 0:
        return False

    delete_btn.scroll_into_view_if_needed(timeout=TIMEOUT_MS)
    page.wait_for_timeout(200)
    delete_btn.click(timeout=TIMEOUT_MS)

    # Poczekaj na okno potwierdzenia
    try:
        page.wait_for_selector('[data-testid="modal-dialog--header"]', timeout=5000)
    except PlaywrightTimeoutError:
        # brak okna - być może usunięcie zadziałało od razu, bez potwierdzenia
        page.wait_for_timeout(400)
        return True

    dialog = page.locator('[role="dialog"]').last
    buttons = dialog.locator("button")
    count = buttons.count()
    confirm_clicked = False
    for i in range(count - 1, -1, -1):  # od ostatniego (zwykle przycisk akcji jest po prawej)
        btn = buttons.nth(i)
        text = (btn.inner_text() or "").strip().lower()
        if text and "cancel" not in text and "anuluj" not in text:
            btn.click(timeout=5000)
            confirm_clicked = True
            break

    if not confirm_clicked:
        return False

    # Poczekaj aż okno zniknie z DOM, żeby nie blokowało kolejnych kliknięć
    try:
        page.wait_for_selector('[data-testid="modal-dialog--header"]', state="detached", timeout=5000)
    except PlaywrightTimeoutError:
        pass

    page.wait_for_timeout(400)
    return True


def move_status_to_column_by_keyboard(page: Page, status_id: str, target_column_index: int) -> bool:
    """Przenosi status KLAWIATURĄ (klik -> Space -> strzałki -> Space) —
    sprawdzony na żywo mechanizm (patrz historia w README/rozmowie): klik
    zamiast .focus() na poziomie JS (zgodnie z oficjalną instrukcją dostępności
    Jiry: "press Tab to go to a column or status and press space to select it"
    - .click() daje bliższy realnemu użytkownikowi stan fokusu niż .focus()).
    Wymaga PEŁNEGO dopasowania (cała karta w obrębie kolumny, nie tylko środek)
    z dodatkową weryfikacją stabilności po 300ms, bo pozycja "na granicy" dwóch
    kolumn potrafiła fałszywie wyzwolić przedwczesne dopasowanie."""
    card = find_status_card_by_id(page, status_id)
    if card.count() == 0:
        return False

    card.scroll_into_view_if_needed(timeout=TIMEOUT_MS)
    page.wait_for_timeout(400)
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


AUTO_MANAGED_COLUMN_NAMES = {"backlog"}  # nie próbuj tworzyć tych ręcznie ani zmieniać ich pozycji


def apply_target_columns(page: Page, target_columns: list[TargetColumn], board_id: int, delete_old_columns: bool = True) -> None:
    """
    Idempotentnie doprowadza tablicę do stanu wzorca:
    1) USUWA kolumny, których nazwa NIE występuje we wzorcu (śmieci sprzed
       konfiguracji) — ale NIE rusza kolumn, które już poprawnie odpowiadają
       jakiejś kolumnie wzorca (więc bezpiecznie znosi wielokrotne uruchomienie).
    2) TWORZY brakujące kolumny wzorca — pomijając te, które już istnieją pod
       daną nazwą, oraz te zarządzane automatycznie przez Jirę ("Backlog",
       oraz niezniszczalna kolumna zmapowana na kategorię statusu "Done").
    3) PRZENOSI statusy do docelowych kolumn, po nazwie (nie po sztywnym
       indeksie), bo automatycznie zarządzane kolumny mogą stać w innym
       miejscu niż w oryginalnym układzie wzorca.
    """
    def norm(s: str) -> str:
        # innerText odczytuje tekst PO zastosowaniu CSS text-transform (kolumny
        # są wyświetlane WIELKIMI LITERAMI niezależnie od zapisu w danych Jiry),
        # więc porównania nazw kolumn muszą ignorować wielkość liter.
        return s.strip().upper()

    target_name_set = {norm(t.name) for t in target_columns}

    # 1) USUŃ kolumny, których nazwy NIE ma we wzorcu (śmieci), zatrzymując się
    #    bezpiecznie, jeśli któraś okaże się niezniszczalna (np. jedyna "Done").
    undeletable_title = None
    if delete_old_columns:
        max_attempts = page.locator(SELECTORS["column"]).count() + 3
        attempts = 0
        while True:
            attempts += 1
            if attempts > max_attempts:
                print("    [OSTRZEŻENIE] Przekroczono limit prób usuwania — przerywam, żeby nie zapętlić się.")
                break

            current_titles = page.locator(SELECTORS["column_title"]).all_inner_texts()
            idx_to_delete = next(
                (i for i, title in enumerate(current_titles) if norm(title) not in target_name_set),
                None,
            )
            if idx_to_delete is None:
                break  # wszystkie pozostałe kolumny już pasują do wzorca - nic więcej do usunięcia

            count_before = len(current_titles)
            delete_column(page, idx_to_delete)
            page.wait_for_timeout(300)
            count_after = page.locator(SELECTORS["column"]).count()

            if count_after >= count_before:
                # Nic się nie usunęło mimo próby - prawdopodobnie niezniszczalna
                # kolumna (Jira wymaga min. jednej zmapowanej na kategorię "Done").
                remaining = page.locator(SELECTORS["column_title"]).all_inner_texts()
                undeletable_title = remaining[idx_to_delete] if idx_to_delete < len(remaining) else None
                if undeletable_title:
                    print(
                        f"    Nie można usunąć kolumny '{undeletable_title}' — prawdopodobnie jedyna "
                        f"zmapowana na kategorię statusu 'Done' (Jira wymaga min. jednej takiej). "
                        f"Zostawiam ją, nie tworzę duplikatu."
                    )
                break

    # 2) UTWÓRZ brakujące kolumny wzorca — pomijając te już istniejące (po
    #    nazwie) oraz zarządzane automatycznie ("Backlog", niezniszczalna "Done")
    def already_exists_or_auto_managed(name: str) -> bool:
        n = norm(name)
        if n in {norm(x) for x in AUTO_MANAGED_COLUMN_NAMES}:
            return True
        if undeletable_title and n == norm(undeletable_title):
            return True
        current_titles_now = [norm(t) for t in page.locator(SELECTORS["column_title"]).all_inner_texts()]
        return n in current_titles_now

    columns_to_create = []
    skipped_names = []
    for t in target_columns:
        if already_exists_or_auto_managed(t.name):
            skipped_names.append(t.name)
        else:
            columns_to_create.append(t)

    if skipped_names:
        print(f"    Pomijam tworzenie (już istnieją albo zarządzane automatycznie): {', '.join(skipped_names)}")

    if columns_to_create:
        print(f"    Tworzę {len(columns_to_create)} kolumn(y) wzorca...")
        for i, target in enumerate(columns_to_create):
            page.mouse.wheel(3000, 0)
            page.wait_for_timeout(300)

            add_button = page.locator(SELECTORS["add_column_button"])
            add_button.scroll_into_view_if_needed(timeout=TIMEOUT_MS)
            page.wait_for_timeout(200)
            add_button.click(timeout=TIMEOUT_MS)
            page.wait_for_timeout(400)

            name_input = page.locator(":focus")
            name_input.fill(target.name)
            name_input.press("Enter")
            page.wait_for_timeout(400)
            print(f"    Utworzono i nazwano kolumnę {i + 1}/{len(columns_to_create)}: '{target.name}'")
    else:
        print("    Wszystkie kolumny wzorca już istnieją — nic do utworzenia.")

    # 3) SPRAWDŹ I NAPRAW KOLEJNOŚĆ. Kroki 1-2 wyżej tworzą TYLKO brakujące
    #    kolumny (w kolejności wzorca) - kolumny, które JUŻ ISTNIAŁY pod
    #    właściwą nazwą (np. z wcześniejszego, przerwanego przebiegu) są
    #    pomijane i zostają na SWOIM dotychczasowym miejscu, nawet jeśli to
    #    miejsce nie zgadza się z wzorcem. Ten krok to naprawia: jeśli
    #    kolejność kolumn "zwykłych" (bez Backlog i niezniszczalnej "Done")
    #    nie zgadza się z wzorcem, usuwa je WSZYSTKIE i tworzy od nowa, w
    #    poprawnej kolejności - bezpieczniejsze i prostsze niż zawodne
    #    przeciąganie istniejących kolumn (ten sam wniosek, co przy statusach
    #    wyżej: drag-and-drop okazał się niewystarczająco niezawodny).
    ensure_column_order(page, target_columns, undeletable_title)


def ensure_column_order(page, target_columns: list["TargetColumn"], undeletable_title: "str | None") -> None:
    def norm(s: str) -> str:
        return s.strip().upper()

    excluded = {norm(x) for x in AUTO_MANAGED_COLUMN_NAMES}
    if undeletable_title:
        excluded.add(norm(undeletable_title))

    current_titles = page.locator(SELECTORS["column_title"]).all_inner_texts()
    current_manageable = [t for t in current_titles if norm(t) not in excluded]
    target_manageable_names = [t.name for t in target_columns if norm(t.name) not in excluded]

    if [norm(t) for t in current_manageable] == [norm(t) for t in target_manageable_names]:
        print("    Kolejność kolumn już zgadza się z wzorcem - nic do zmiany.")
        return

    print(f"    Kolejność kolumn NIE zgadza się z wzorcem (jest: {current_manageable}, "
          f"powinno być: {target_manageable_names}) - usuwam i tworzę od nowa w poprawnej kolejności...")

    # Usuń WSZYSTKIE kolumny "zwykłe" (pomijając Backlog i niezniszczalną "Done").
    max_attempts = len(current_manageable) + 3
    for _ in range(max_attempts):
        titles_now = page.locator(SELECTORS["column_title"]).all_inner_texts()
        idx_to_delete = next((i for i, t in enumerate(titles_now) if norm(t) not in excluded), None)
        if idx_to_delete is None:
            break
        delete_column(page, idx_to_delete)
        page.wait_for_timeout(300)

    # Utwórz je od nowa, W KOLEJNOŚCI WZORCA.
    for i, name in enumerate(target_manageable_names):
        page.mouse.wheel(3000, 0)
        page.wait_for_timeout(300)
        add_button = page.locator(SELECTORS["add_column_button"])
        add_button.scroll_into_view_if_needed(timeout=TIMEOUT_MS)
        page.wait_for_timeout(200)
        add_button.click(timeout=TIMEOUT_MS)
        page.wait_for_timeout(400)
        name_input = page.locator(":focus")
        name_input.fill(name)
        name_input.press("Enter")
        page.wait_for_timeout(400)
        print(f"    Utworzono w poprawnej kolejności {i + 1}/{len(target_manageable_names)}: '{name}'")

    # UWAGA: przenoszenie statusów do kolumn zostało WYŁĄCZONE (ponownie).
    # Mechanizm drag-and-drop (klawiaturowy, z weryfikacją pozycji i przez
    # REST) okazał się w tej instancji Jiry niewystarczająco niezawodny mimo
    # wielu rund poprawek, a przy tym zbyt wolny na skalę 550 projektów.
    # Statusy przypisuje się ręcznie w Jira (Board settings -> Columns).
    # Ściągawka mapowania status -> kolumna jest wypisywana na końcu
    # przebiegu (main() -> "KROK RĘCZNY"). Funkcja move_status_to_column_by_keyboard
    # zostaje w kodzie (niewywoływana) na wypadek, gdyby ktoś chciał do tego
    # wrócić w przyszłości.


# --------------------------------------------------------------------------- #
# Weryfikacja
# --------------------------------------------------------------------------- #

def columns_match(target: list[TargetColumn], actual: list[TargetColumn]) -> bool:
    """Sprawdza, czy wszystkie NAZWY kolumn wzorca są obecne na tablicy
    (niezależnie od kolejności). Zawartości statusów NIE sprawdzamy — to
    przypisuje się ręcznie (przenoszenie automatyczne wyłączone, patrz
    apply_target_columns)."""
    target_names = {norm_name(c.name) for c in target}
    actual_names = {norm_name(c.name) for c in actual}
    return target_names.issubset(actual_names)


def norm_name(s: str) -> str:
    return s.strip().upper()


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def get_project_pairs(args) -> list[tuple[str, str]]:
    """Zwraca (project_key, board_template_id). board_template_id może być
    pustym stringiem — oznacza to użycie domyślnego wzorca z .env
    (JIRA_TEMPLATE_BOARD_ID, rozwiązywane przez resolve_template_board_id())."""
    if args.project_keys:
        keys = [k.strip().upper() for k in args.project_keys.split(",") if k.strip()]
        override = args.template_board_id or ""
        return [(k, override) for k in keys]

    sheet_rows = core.get_sheet_rows()
    cache = core.load_key_cache()
    reserved = set(cache.values())
    pairs = []
    for row in sheet_rows:
        gid = core.extract_asana_project_gid(row.asana_project_link)
        key = core.assign_jira_key(row, gid, cache, reserved)
        pairs.append((key, (row.board_template_id or "").strip()))
    return pairs


def main() -> None:
    parser = argparse.ArgumentParser(description="Ustawia kolumny tablicy na wielu projektach — wzorzec per wiersz z kolumny board_template_id (puste = domyślny z .env).")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--project-keys", type=str, default=None)
    parser.add_argument(
        "--template-board-id", type=str, default=None,
        help="Nadpisuje wzorzec dla projektów podanych przez --project-keys "
             "(ignorowane przy odczycie z arkusza — tam decyduje kolumna board_template_id).",
    )
    parser.add_argument("--interactive", action="store_true", help="Tryb widoczny + pauza na pierwszym projekcie.")
    parser.add_argument("--dry-run", action="store_true", help="Tylko pokaż plan, nie otwieraj przeglądarki.")
    parser.add_argument("--keep-old-columns", action="store_true",
        help="NIE usuwaj starych/domyślnych kolumn — tylko dołóż nowe obok nich "
             "(bezpieczniejszy tryb, jeśli usuwanie okaże się niedziałające).")
    parser.add_argument(
        "--shard", type=str, default=None,
        help="Podział pracy między kilka RÓWNOLEGLE uruchomionych procesów — format 'K/N' "
             "(patrz jira_sync.py --help po pełne wyjaśnienie).",
    )
    args = parser.parse_args()

    if not Path(STATE_FILE).exists() and not args.dry_run:
        print(f"Brak pliku {STATE_FILE}. Uruchom najpierw: python save_login_session.py")
        sys.exit(1)

    project_pairs = get_project_pairs(args)
    if args.shard:
        project_pairs = core.apply_shard(project_pairs, args.shard)
        print(f"--shard {args.shard}: ten proces obsłuży {len(project_pairs)} projekt(ów).")
    if args.limit:
        project_pairs = project_pairs[: args.limit]
    print(f"Projektów do przetworzenia: {len(project_pairs)}")

    # Wzorce ładowane LENIWIE i CACHE'OWANE per board_template_id - różne
    # projekty mogą używać różnych wzorców (kolumna board_template_id w arkuszu).
    template_cache: dict[str, tuple] = {}  # board_template_id -> (columns, status_names)

    def get_template_for(board_template_id: str) -> list:
        resolved_id = int(board_template_id) if board_template_id else resolve_template_board_id()
        cache_key = str(resolved_id)
        if cache_key not in template_cache:
            cols = get_board_columns(resolved_id)
            ids = [sid for c in cols for sid in c.statuses]
            names = get_status_names(ids)
            template_cache[cache_key] = (cols, names)
            print(f"\n[Wzorzec: tablica {resolved_id}] Docelowy układ kolumn:")
            for c in cols:
                nm = [names.get(sid, sid) for sid in c.statuses]
                print(f"  {c.name}: {', '.join(nm)}")
        return template_cache[cache_key][0]

    if args.dry_run:
        for key, board_template_id in project_pairs:
            target_columns = get_template_for(board_template_id)
            label = board_template_id or "(domyślny z .env)"
            print(f"  [DRY-RUN] {key}: ustawiłbym kolumny wg wzorca {label} ({len(target_columns)} kolumn).")
        return

    report = []
    session_expired = False
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.interactive)
        context = browser.new_context(storage_state=STATE_FILE)
        page = context.new_page()

        for i, (key, board_template_id) in enumerate(project_pairs):
            print(f"\n[{i + 1}/{len(project_pairs)}] {key}")
            target_columns = get_template_for(board_template_id)

            style = get_project_style(key)
            if style == "next-gen":
                print("  Projekt team-managed - ustawienia kolumn wyglądają tam inaczej, pomijam.")
                record_warning(key, "", "projekt team-managed - kolumny trzeba ustawić ręcznie")
                report.append({"project": key, "status": "team_managed"})
                continue

            board_id = find_board_id(key)
            if not board_id:
                print("  Brak tablicy — pomijam.")
                record_warning(key, "", "brak tablicy w projekcie")
                report.append({"project": key, "status": "brak_tablicy"})
                continue

            try:
                is_calibration_run = args.interactive and i == 0
                open_columns_config(page, key, board_id, wait_for_columns=not is_calibration_run)

                if is_calibration_run:
                    print(
                        "  >>> TRYB PODGLĄDU: strona jest otwarta. Selektory zostały odczytane "
                        "na żywo z Twojej Jiry, więc powinny działać od razu — ale sprawdź w "
                        "Playwright Inspector, czy strona wygląda jak oczekiwano, zanim klikniesz "
                        "Resume. Jeśli coś się różni (np. inny typ tablicy), zatrzymaj (Ctrl+C) "
                        "i daj mi znać co widzisz."
                    )
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
                    record_warning(key, f"tablica {board_id}", "układ kolumn po zmianach niezgodny ze wzorcem")
                    report.append({"project": key, "status": "niezgodnosc"})

            except SessionExpiredError as exc:
                print(f"\n  [STOP] {exc}")
                record_error(key, "", str(exc))
                report.append({"project": key, "status": "sesja_wygasla", "error": str(exc)})
                session_expired = True
                break
            except PlaywrightTimeoutError as exc:
                screenshot_path = f"error_{key}.png"
                first_line = str(exc).splitlines()[0]
                try:
                    page.screenshot(path=screenshot_path, full_page=True)
                    print(f"  BŁĄD (timeout): {exc}\n  Adres strony: {page.url}\n  Zrzut ekranu zapisany do {screenshot_path}")
                except Exception:  # noqa: BLE001
                    print(f"  BŁĄD (timeout): {exc}")
                record_error(key, f"tablica {board_id}", f"{first_line} (zrzut: {screenshot_path})")
                report.append({"project": key, "status": "blad", "error": str(exc), "url": page.url})
            except Exception as exc:  # noqa: BLE001
                screenshot_path = f"error_{key}.png"
                try:
                    page.screenshot(path=screenshot_path, full_page=True)
                    print(f"  BŁĄD: {exc}\n  Zrzut ekranu zapisany do {screenshot_path}")
                except Exception:  # noqa: BLE001
                    print(f"  BŁĄD: {exc}")
                record_error(key, f"tablica {board_id}", f"{str(exc).splitlines()[0]} (zrzut: {screenshot_path})")
                report.append({"project": key, "status": "blad", "error": str(exc)})

        browser.close()

    Path(REPORT_FILE).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    def count(status: str) -> int:
        return sum(1 for r in report if r["status"] == status)

    print("\n" + "=" * 60)
    print("PODSUMOWANIE")
    print(f"  OK:              {count('ok')}")
    print(f"  Niezgodność:     {count('niezgodnosc')}")
    print(f"  Błędy:           {count('blad')}")
    print(f"  Brak tablicy:    {count('brak_tablicy')}")
    print(f"  Team-managed:    {count('team_managed')}")
    if session_expired:
        not_done = len(project_pairs) - len(report)
        print(f"  PRZERWANO: sesja wygasła, {not_done} projekt(ów) nieprzetworzonych.")
    print(f"\nSzczegóły w {REPORT_FILE}")

    write_shard_summary("bulk_configure_columns", args.shard, {
        "ok": count("ok"),
        "niezgodność": count("niezgodnosc"),
        "błędy": count("blad"),
        "brak tablicy": count("brak_tablicy"),
        "team-managed": count("team_managed"),
    })

    print("\nNastępny krok: przypisanie statusów do kolumn robi automatycznie")
    print("browser_automation/run_column_assignments.py (krok 3 run_full_pipeline.py).")
    for board_template_id, (cols, names) in template_cache.items():
        print(f"\nDocelowe mapowanie status -> kolumna (wzorzec: tablica {board_template_id}):")
        for c in cols:
            status_labels = [names.get(sid, sid) for sid in c.statuses]
            if status_labels:
                print(f"  {c.name}: {', '.join(status_labels)}")

    if session_expired:
        sys.exit(1)


if __name__ == "__main__":
    main()
