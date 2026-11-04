#!/usr/bin/env python3
"""
copy_project.py

Tworzy NOWY projekt w Jirze z DOKŁADNIE taką samą konfiguracją jak istniejący
projekt źródłowy — workflow, ekrany, pola, typy zadań (reużywa mechanizmu z
jira_sync.py), ORAZ wszystkie tablice źródłowego projektu wraz z ich układem
kolumn i przypisaniem statusów (NOWA logika, patrz niżej).

KOPIUJE ZE ŹRÓDŁA także ustawienia projektu (nic z .env): leada (domyślnie),
kategorię, domyślne przypisanie, URL, opis (gdy nie podano własnego) i komponenty.
Członków ról NIE kopiuje (dodaje się ich ręcznie); opcjonalnie --copy-role-members. Schemat uprawnień i powiadomień jest po
utworzeniu sprawdzany i w razie różnicy ustawiany jak w źródle.

Jak działa kopiowanie TABLIC (część, której nie ma nigdzie indziej):
  1. GET /rest/agile/1.0/board?projectKeyOrId=ŹRÓDŁO — WSZYSTKIE tablice
     (nie tylko pierwsza, jak w find_board_id() używanym gdzie indziej).
  2. Dla każdej: pobiera jej filtr (JQL), podmienia klucz projektu w JQL na
     nowy, tworzy nowy filtr (POST /rest/api/3/filter) i nową tablicę na tym
     filtrze (POST /rest/agile/1.0/board) — udokumentowane w Jira Cloud REST
     API, ale BEZ możliwości ustawienia kolumn tą samą drogą (ograniczenie
     API, potwierdzone w dokumentacji Atlassiana).
     UDOSTĘPNIENIA FILTRA: nowy filtr dostaje te same udostępnienia i prawa
     edycji co źródłowy (projekt źródłowy podmieniony na nowy) i zawsze jest
     udostępniony nowemu projektowi. Tablica jest widoczna tylko dla osób,
     które widzą jej filtr - wcześniej nowe filtry były prywatne, więc zespół
     nie widział skopiowanych tablic.
  3. Nazwy kolumn: przez Playwright, tak jak bulk_configure_columns.py —
     ale kopiuje DOKŁADNE nazwy z tablicy ŹRÓDŁOWEJ, nie ze sztywnego wzorca.
  4. Przypisanie statusów do kolumn: przez odkryty wcześniej mechanizm
     GraphQL (odczyt) + PUT na wewnętrzne API GreenHopper (zapis) — ten sam,
     który stoi za run_column_assignments.py, tu wbudowany bezpośrednio,
     żeby nie zależeć od dokładnej, potencjalnie już zmienionej wersji tamtego
     pliku. Wymaga zapisanej sesji przeglądarki (auth_state.json — patrz
     browser_automation/save_login_session.py).

UWAGA: to używa TEGO SAMEGO nieoficjalnego, wewnętrznego API co
run_column_assignments.py — może przestać działać po aktualizacji Jiry
(patrz komentarze w assign_statuses_to_columns.js). Jeśli tak się stanie,
trzeba świeżo przechwycić CLOUD_ID/GRAPHQL_QUERY_HASH/X_EXPERIMENTAL_API
przez DevTools -> Network, dokładnie jak opisano w README (sekcja 11b).

Użycie:
    python copy_project.py --source ABC --target-key XYZ --target-name "Nowy Projekt" --lead-account-id 5b10a...
    python copy_project.py --source ABC --target-key XYZ --target-name "Nowy Projekt" --lead-account-id 5b10a... --dry-run
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core
from jira_sync import (
    JIRA_BASE_URL,
    ProjectToCreate,
    add_users_to_project_role,
    apply_template_schemes,
    create_jira_project,
    get_project_id,
    get_role_id_by_name,
    get_template_schemes,
    jira_auth,
    jira_project_exists,
)

# Sesję przeglądarki narzędzie logowania potrafi zapisać w folderze głównym albo w
# browser_automation/ (zależnie od tego, skąd zostało uruchomione). Bierzemy ŚWIEŻSZY
# z istniejących plików - wcześniej czytany był stały, nieaktualny plik, co dawało
# przekierowanie na logowanie mimo odświeżonej sesji.
_AUTH_CANDIDATES = [Path(__file__).resolve().parent / "browser_automation" / "auth_state.json",
                    Path(__file__).resolve().parent / "auth_state.json"]
STATE_FILE = max((p for p in _AUTH_CANDIDATES if p.exists()), key=lambda p: p.stat().st_mtime,
                 default=_AUTH_CANDIDATES[0])
TIMEOUT_MS = 15000

SELECTORS = {
    # Selektory zweryfikowane w PRAWDZIWYM, działającym bulk_configure_columns.py
    # (używanym z powodzeniem przy migracji 550 projektów) - moje wcześniejsze
    # wersje były błędnym zgadywaniem, nie "dezaktualizacją".
    "column": '[data-testid="column.header.data.test.id"] > [data-testid="column.data.test.id"]',
    "column_title": '[data-testid="column.header.title.data.test.id"]',
    "add_column_button": '[data-testid="platform-board-kit.ui.column.column-create.button.styled-button"]',
}

# Te same stałe co assign_statuses_to_columns.js - jeśli Jira zmieni frontend,
# trzeba je świeżo przechwycić przez DevTools -> Network (patrz README 11b).
CLOUD_ID = os.getenv("CLOUD_ID", "").strip() or None  # opcjonalnie na stałe z .env; jeśli puste, ustawiane automatycznie z URL po zalogowaniu, patrz resolve_cloud_id()
# Skrót "zapisanego zapytania" interfejsu Jiry - Atlassian zmienia go przy aktualizacjach
# frontendu. Nowy przechwycisz w DevTools -> Network (ustawienia kolumn dowolnej tablicy,
# zapytanie .../gateway/api/graphql/pq/<SKRÓT>?operation=...) i wpisujesz do .env.
GRAPHQL_QUERY_HASH = (os.getenv("GRAPHQL_QUERY_HASH", "").strip()
                      or "ee2eca73f267aac1af28737f7ac9a48bb0c4aa424206b5946ceb79d2a4cfeb47")
GRAPHQL_OPERATION = os.getenv("GRAPHQL_OPERATION", "").strip() or "ColumnsSettingsPageNewContentQuery"
CATEGORY_MAP = {
    "new": {"id": "2", "colorName": "blue-gray"},
    "indeterminate": {"id": "4", "colorName": "yellow"},
    "done": {"id": "3", "colorName": "green"},
}


# --------------------------------------------------------------------------- #
# Odkrywanie tablic i kopiowanie ich (filtr + tablica) - część NOWA
# --------------------------------------------------------------------------- #

def get_project_boards(project_key: str, extra_board_ids: "list | None" = None) -> list:
    """WSZYSTKIE tablice odnoszące się do danego projektu.

    Jeśli podano extra_board_ids: pomija KOSZTOWNE automatyczne przeszukanie
    całej instancji i używa WYŁĄCZNIE podanych ID — sensowne, gdy już wiesz,
    których tablic potrzebujesz (np. z poprzedniego uruchomienia, które
    pokazało Ci ich nazwy/ID), zamiast czekać kilka minut na ponowne
    zgadywanie tego, co już wiesz.

    Bez extra_board_ids: automatyczne wykrywanie (wolniejsze, ale nie
    wymaga znajomości ID z góry).

    UWAGA (potwierdzone na żywo + udokumentowany problem na forum Atlassiana):
    parametr projectKeyOrId w GET /rest/agile/1.0/board filtruje po
    "lokalizacji" tablicy (do jakiego projektu była przypisana PRZY
    TWORZENIU), NIE po tym, czy jej filtr JQL faktycznie odnosi się do tego
    projektu. Tablice utworzone inaczej niż "z poziomu projektu" (np. z
    gotowego, współdzielonego filtra) mogą nie mieć tej lokalizacji, mimo że
    realnie dotyczą projektu — i wtedy projectKeyOrId je pomija. Dlatego przy
    automatycznym wykrywaniu pobieramy WSZYSTKIE tablice w całej instancji i
    sami sprawdzamy, których filtr JQL wspomina klucz projektu źródłowego."""
    if extra_board_ids:
        print(f"   Pomijam automatyczne wykrywanie — używam WYŁĄCZNIE podanych --board-ids ({len(extra_board_ids)}).")
        boards = []
        for bid in extra_board_ids:
            resp = requests.get(f"{JIRA_BASE_URL}/rest/agile/1.0/board/{bid}", auth=jira_auth(), timeout=30)
            if resp.status_code == 200:
                boards.append(resp.json())
                print(f"   OK: {resp.json().get('name')} (id={bid})")
            else:
                print(f"   [OSTRZEŻENIE] --board-ids {bid}: nie znaleziono lub brak dostępu ({resp.status_code}).")
        return boards

    print(f"   Pobieram listę WSZYSTKICH tablic w instancji (może chwilę potrwać)...")
    all_boards: list = []
    start_at = 0
    while True:
        resp = requests.get(
            f"{JIRA_BASE_URL}/rest/agile/1.0/board",
            auth=jira_auth(), params={"startAt": start_at, "maxResults": 50}, timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        all_boards.extend(payload.get("values", []))
        print(f"   ...pobrano {len(all_boards)} tablic dotąd" + ("" if payload.get("isLast", True) else " (kontynuuję)"))
        if payload.get("isLast", True):
            break
        start_at += 50
    print(f"   Łącznie w instancji: {len(all_boards)} tablic. Sprawdzam, które dotyczą '{project_key}'...")
    print(f"   (Wskazówka: jeśli już znasz ID potrzebnych tablic, użyj --board-ids, żeby pominąć to przeszukiwanie następnym razem.)")

    matching = []
    pattern = re.compile(rf'\bproject\s*(=|in)\s*[("]?[^)"\n]*\b{re.escape(project_key)}\b', re.IGNORECASE)
    checked_slow_path = 0
    for i, board in enumerate(all_boards, start=1):
        # Szybka ścieżka: lokalizacja LUB nazwa tablicy bezpośrednio wskazuje
        # projekt (tanie, bez dodatkowego zapytania o filtr).
        location_key = (board.get("location") or {}).get("projectKey", "")
        if location_key.upper() == project_key.upper() or project_key.upper() in board.get("name", "").upper():
            matching.append(board)
            continue
        # Wolniejsza, ale rzetelna ścieżka: sprawdź faktyczny JQL filtra tablicy
        # (tylko dla tablic, których nazwa/lokalizacja NIC nie sugerowały).
        checked_slow_path += 1
        if checked_slow_path % 20 == 0:
            print(f"   ...sprawdzono filtry {checked_slow_path} tablic (pozycja {i}/{len(all_boards)})")
        try:
            _, jql, _ = get_board_filter_jql(board["id"])
            if pattern.search(jql):
                matching.append(board)
        except Exception:  # noqa: BLE001
            continue  # brak dostępu do filtra tej tablicy - pomijamy ją cicho
    print(f"   Zakończono sprawdzanie. Dopasowano {len(matching)} tablic(e) do '{project_key}'.")
    print(f"   ID znalezionych tablic (zapisz je, żeby użyć --board-ids przy kolejnym uruchomieniu): "
          f"{','.join(str(b['id']) for b in matching)}")

    return matching


def get_board_filter_jql(board_id) -> tuple:
    """Zwraca (filter_id, jql, filter_name) danej tablicy."""
    resp = requests.get(
        f"{JIRA_BASE_URL}/rest/agile/1.0/board/{board_id}/configuration", auth=jira_auth(), timeout=30
    )
    resp.raise_for_status()
    filter_id = str(resp.json()["filter"]["id"])
    resp2 = requests.get(f"{JIRA_BASE_URL}/rest/api/3/filter/{filter_id}", auth=jira_auth(), timeout=30)
    resp2.raise_for_status()
    data = resp2.json()
    return filter_id, data["jql"], data.get("name", "")


def adapt_jql_to_new_project(jql: str, source_key: str, source_name: str, target_key: str) -> str:
    """Podmienia odniesienie do projektu źródłowego w JQL filtra tablicy na
    klucz projektu docelowego — obsługuje JQL odwołujący się PO KLUCZU
    ('project = ABC') ORAZ po NAZWIE projektu ('project = "Nazwa Projektu"',
    z dowolnymi spacjami/znakami w nazwie) — niektóre instancje Jiry (jak
    ta) zapisują filtry po nazwie, nie po kluczu. Podmienia zawsze NA KLUCZ
    (nie na nazwę) w wyniku — klucz jest jednoznaczny i zawsze poprawny,
    niezależnie jak było w oryginale."""
    # Wariant 1: po kluczu (z/bez cudzysłowów, wielkość liter bez znaczenia).
    # \b na końcu jest KLUCZOWE - bez tego wzorzec dopasowywałby się jako
    # PREFIKS dłuższego identyfikatora (np. source_key='ROM' dopasowałby się
    # wewnątrz 'ROMPREFIX'), zostawiając resztę oryginalnego tekstu doklejoną
    # do nowego klucza (potwierdzony ręcznie błąd: 'MERGprofix').
    key_pattern = re.compile(rf'project\s*=\s*"?{re.escape(source_key)}\b"?', re.IGNORECASE)
    new_jql, count = key_pattern.subn(f"project = {target_key}", jql)
    if count > 0:
        return new_jql

    # Wariant 2: po nazwie projektu - cudzysłów jest OPCJONALNY (JQL wymaga
    # go tylko, gdy nazwa zawiera spacje/znaki specjalne - nazwa bez spacji,
    # jak w potwierdzonym ręcznie przypadku 'Romprofix', bywa zapisana BEZ
    # cudzysłowu). \b na końcu z tego samego powodu co w wariancie 1 -
    # zapobiega dopasowaniu jako prefiks dłuższego, niepowiązanego słowa.
    if source_name:
        name_pattern = re.compile(rf'project\s*=\s*"?{re.escape(source_name)}\b"?', re.IGNORECASE)
        new_jql, count = name_pattern.subn(f"project = {target_key}", jql)
        if count > 0:
            return new_jql

    raise ValueError(
        f"Nie udało się automatycznie podmienić odniesienia do projektu w JQL: '{jql}'. "
        f"Szukano klucza '{source_key}' i nazwy '{source_name}', żadne nie pasowało "
        f"dosłownie (np. filtr może używać ID projektu albo bardziej złożonego wyrażenia). "
        f"Popraw ręcznie filtr tej tablicy w Jirze po utworzeniu."
    )


def find_filter_by_name_exhaustive(name: str) -> "str | None":
    """Przeszukuje WSZYSTKIE filtry widoczne dla tego konta (bez polegania
    na parametrze filterName, którego dokładne zachowanie wyszukiwania -
    zakres widoczności, dopasowanie częściowe vs dokładne - okazało się
    niepewne w praktyce). Używane TYLKO gdy mamy już PEWNOŚĆ, że filtr o tej
    nazwie istnieje (bo próba utworzenia go dała błąd 'already exists') -
    więc wolniejsze, wyczerpujące przeszukanie jest uzasadnione."""
    start_at = 0
    while True:
        resp = requests.get(
            f"{JIRA_BASE_URL}/rest/api/3/filter/search",
            auth=jira_auth(), params={"startAt": start_at, "maxResults": 50}, timeout=30,
        )
        if resp.status_code != 200:
            return None
        payload = resp.json()
        for f in payload.get("values", []):
            if f.get("name") == name:
                return str(f["id"])
        if payload.get("isLast", True):
            return None
        start_at += 50


def get_or_create_filter(name: str, jql: str) -> tuple:
    """Zwraca (filter_id, was_reused: bool). Próbuje utworzyć NAJPIERW -
    jeśli Jira odpowie 'nazwa już zajęta', dopiero WTEDY (mając już
    pewność, że filtr istnieje) szuka go wyczerpująco, żeby go ponownie
    użyć zamiast się poddawać. Odwrotna kolejność (najpierw szukaj) okazała
    się zawodna - parametr filterName w wyszukiwaniu nie gwarantował
    znalezienia świeżo utworzonych/istniejących filtrów w praktyce."""
    resp = requests.post(
        f"{JIRA_BASE_URL}/rest/api/3/filter",
        auth=jira_auth(),
        json={"name": name, "jql": jql},
        timeout=30,
    )
    if resp.status_code < 300:
        return str(resp.json()["id"]), False

    try:
        error_body = resp.json()
    except ValueError:
        error_body = {}
    is_name_conflict = "filterName" in (error_body.get("errors") or {})

    if is_name_conflict:
        existing_id = find_filter_by_name_exhaustive(name)
        if existing_id:
            return existing_id, True

    raise RuntimeError(f"Błąd tworzenia filtra '{name}': {resp.status_code} {resp.text}")


def get_filter_permissions(filter_id: str) -> tuple:
    """(sharePermissions, editPermissions) filtra, w formacie zwracanym przez Jirę."""
    resp = requests.get(f"{JIRA_BASE_URL}/rest/api/3/filter/{filter_id}", auth=jira_auth(), timeout=30,
                        params={"expand": "sharePermissions,editPermissions"})
    resp.raise_for_status()
    data = resp.json()
    return data.get("sharePermissions") or [], data.get("editPermissions") or []


def _perm_signature(perm: dict) -> tuple:
    return (perm.get("type"), str((perm.get("project") or {}).get("id", "")),
            str((perm.get("role") or {}).get("id", "")), (perm.get("group") or {}).get("groupId") or
            (perm.get("group") or {}).get("name", ""), (perm.get("user") or {}).get("accountId", ""))


def map_filter_permissions(perms: list, source_project_id: str, target_project_id: str,
                           ensure_target: bool = False) -> list:
    """Kopia udostępnień z projektem źródłowym podmienionym na docelowy.
    Pozostałe (grupy, użytkownicy, inne projekty, wszyscy zalogowani) bez zmian.
    Pomija wpisy 'project-unknown' (projekt, którego konto nie widzi)."""
    out = []
    for perm in perms:
        if perm.get("type") == "project-unknown":
            continue
        clean = {"type": perm["type"]}
        if perm.get("project"):
            pid = str(perm["project"].get("id"))
            clean["project"] = {"id": target_project_id if pid == str(source_project_id) else pid}
        if perm.get("role"):
            clean["role"] = {"id": str(perm["role"].get("id"))}
        if perm.get("group"):
            g = perm["group"]
            clean["group"] = {"groupId": g["groupId"]} if g.get("groupId") else {"name": g.get("name")}
        if perm.get("user"):
            clean["user"] = {"accountId": perm["user"].get("accountId")}
        if _perm_signature(clean) not in {_perm_signature(x) for x in out}:
            out.append(clean)
    if ensure_target and not any(p["type"] == "project" and p.get("project", {}).get("id") == target_project_id
                                 and not p.get("role") for p in out):
        out.append({"type": "project", "project": {"id": target_project_id}})
    return out


def apply_filter_permissions(filter_id: str, name: str, jql: str, share: list, edit: list) -> None:
    """Ustawia udostępnienia i prawa edycji filtra, dokładając do tego, co już ma
    (ponowne uruchomienie albo filtr znaleziony z poprzedniej próby)."""
    current_share, current_edit = get_filter_permissions(filter_id)

    def merged(current, wanted):
        result = map_filter_permissions(current, "", "")  # oczyszczone, bez podmiany
        for perm in wanted:
            if _perm_signature(perm) not in {_perm_signature(x) for x in result}:
                result.append(perm)
        return result

    body = {"name": name, "jql": jql,
            "sharePermissions": merged(current_share, share), "editPermissions": merged(current_edit, edit)}
    resp = requests.put(f"{JIRA_BASE_URL}/rest/api/3/filter/{filter_id}", auth=jira_auth(), json=body, timeout=30,
                        params={"expand": "sharePermissions,editPermissions"})
    if resp.status_code >= 300:
        raise RuntimeError(f"{resp.status_code} {resp.text[:300]}")


def find_board_by_name_exhaustive(project_key: str, name: str) -> "int | None":
    """Przeszukuje WSZYSTKIE tablice w instancji po dokładnej nazwie -
    używane TYLKO gdy mamy już PEWNOŚĆ, że tablica istnieje (błąd 'already
    exists' przy próbie utworzenia) - nie polega na projectKeyOrId (który,
    tak jak przy tablicach źródłowych, filtruje po "lokalizacji" tablicy,
    zawodnej dla tablic bez poprawnie ustawionej lokalizacji)."""
    start_at = 0
    while True:
        resp = requests.get(
            f"{JIRA_BASE_URL}/rest/agile/1.0/board",
            auth=jira_auth(), params={"startAt": start_at, "maxResults": 50}, timeout=30,
        )
        if resp.status_code != 200:
            return None
        payload = resp.json()
        for b in payload.get("values", []):
            if b.get("name") == name:
                return b["id"]
        if payload.get("isLast", True):
            return None
        start_at += 50


def get_or_create_board(project_key: str, name: str, board_type: str, filter_id: str) -> tuple:
    """Zwraca (board_id, was_reused: bool). WAŻNE: podaje 'location' przy
    tworzeniu — bez tego Jira tworzy tablicę BEZ powiązania z żadnym
    projektem, co potem blokuje dostęp do jej ustawień (komunikat "Connect
    this board to a project or user") - odkryte na żywo przez zrzut ekranu
    z realnego błędu, nie przez samą dokumentację."""
    resp = requests.post(
        f"{JIRA_BASE_URL}/rest/agile/1.0/board",
        auth=jira_auth(),
        json={
            "name": name, "type": board_type, "filterId": int(filter_id),
            "location": {"type": "project", "projectKeyOrId": project_key},
        },
        timeout=30,
    )
    if resp.status_code < 300:
        return resp.json()["id"], False

    try:
        error_body = resp.json()
    except ValueError:
        error_body = {}
    error_text = json.dumps(error_body).lower()
    is_name_conflict = "already exist" in error_text or "name" in (error_body.get("errors") or {})

    if is_name_conflict:
        existing_id = find_board_by_name_exhaustive(project_key, name)
        if existing_id:
            return existing_id, True

    raise RuntimeError(f"Błąd tworzenia tablicy '{name}': {resp.status_code} {resp.text}")


def get_board_columns_rest(board_id) -> list:
    """[{"name": ..., "status_ids": [...]}, ...] przez publiczne, udokumentowane API."""
    resp = requests.get(
        f"{JIRA_BASE_URL}/rest/agile/1.0/board/{board_id}/configuration", auth=jira_auth(), timeout=30
    )
    resp.raise_for_status()
    columns = resp.json().get("columnConfig", {}).get("columns", [])
    result = []
    for col in columns:
        status_ids = [s["self"].rsplit("/", 1)[-1] for s in col.get("statuses", [])]
        result.append({"name": col.get("name", ""), "status_ids": status_ids})
    return result


def get_status_names(status_ids: list) -> dict:
    names = {}
    for sid in status_ids:
        resp = requests.get(f"{JIRA_BASE_URL}/rest/api/3/status/{sid}", auth=jira_auth(), timeout=30)
        if resp.status_code == 200:
            names[sid] = resp.json()["name"]
    return names


# --------------------------------------------------------------------------- #
# Playwright: nazwy kolumn (jawnie skopiowane ze źródłowej tablicy)
# --------------------------------------------------------------------------- #

def _save_diagnostics(page, board_id, label: str) -> tuple:
    """Zapisuje zrzut ekranu + HTML strony w momencie niepowodzenia — żeby
    dało się naprawić selektor NA PODSTAWIE realnej strony, zamiast
    zgadywać kolejny z rzędu."""
    diag_dir = Path(__file__).resolve().parent / "diagnostics"
    diag_dir.mkdir(exist_ok=True)
    safe_name = f"board_{board_id}_{label}"
    screenshot_path = diag_dir / f"{safe_name}.png"
    html_path = diag_dir / f"{safe_name}.html"
    page.screenshot(path=str(screenshot_path), full_page=True)
    html_path.write_text(page.content(), encoding="utf-8")
    return screenshot_path, html_path


def ensure_columns_exist(page, project_key: str, board_id, target_column_names: list) -> None:
    url = f"{JIRA_BASE_URL}/jira/software/c/projects/{project_key}/boards/{board_id}/settings/columns"
    page.goto(url, wait_until="domcontentloaded")
    if "id.atlassian.com/login" in page.url:
        raise RuntimeError("Sesja przeglądarki wygasła — uruchom ponownie python browser_automation/save_login_session.py")

    try:
        page.wait_for_selector(SELECTORS["column"], timeout=TIMEOUT_MS)
    except Exception:  # noqa: BLE001
        # Jira przekierowuje na logowanie z opóźnieniem (skryptem strony), więc wygasła sesja
        # wychodzi dopiero tutaj, a nie przy sprawdzeniu zaraz po otwarciu strony.
        if "id.atlassian.com" in page.url or "login" in page.url.lower():
            raise RuntimeError("Sesja przeglądarki wygasła (przekierowanie na stronę logowania). "
                               "Uruchom: python browser_automation/save_login_session.py i powtórz kopiowanie.")
        screenshot_path, html_path = _save_diagnostics(page, board_id, "columns_load")
        raise RuntimeError(
            f"Nie znaleziono oczekiwanych elementów kolumn na stronie (selektor mógł się "
            f"zdezaktualizować — Jira regularnie zmienia frontend). Zapisano diagnostykę: "
            f"{screenshot_path.name} (zrzut ekranu) i {html_path.name} (HTML strony) w folderze "
            f"'diagnostics/'. Aktualny URL strony: {page.url} | Tytuł: {page.title()}. "
            f"Prześlij zrzut ekranu, żeby dało się poprawnie zaktualizować selektor."
        )

    existing_titles = {t.strip().upper() for t in page.locator(SELECTORS["column_title"]).all_inner_texts()}
    for name in target_column_names:
        if name.strip().upper() in existing_titles:
            continue
        try:
            # Dokładna technika z działającego bulk_configure_columns.py:
            # przewiń w prawo (przycisk bywa poza widokiem po dodaniu kilku
            # kolumn), scroll_into_view_if_needed, klik, POCZEKAJ, i znajdź
            # pole przez ':focus' (Jira sama je fokusuje po kliknięciu -
            # nie ma osobnego, stabilnego selektora samego pola).
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
        except Exception:  # noqa: BLE001
            screenshot_path, html_path = _save_diagnostics(page, board_id, f"add_column_{name.replace(' ', '_')}")
            raise RuntimeError(
                f"Nie udało się kliknąć/wypełnić przy dodawaniu kolumny '{name}'. Zapisano "
                f"diagnostykę: {screenshot_path.name} i {html_path.name} w folderze 'diagnostics/'. "
                f"Prześlij zrzut ekranu."
            )


# --------------------------------------------------------------------------- #
# Playwright + GraphQL/GreenHopper: przypisanie statusów (jawnie skopiowane)
# --------------------------------------------------------------------------- #

def resolve_cloud_id(page) -> str:
    """Wyciąga cloudId ze strony (Jira osadza go w konfiguracji frontendu)."""
    cloud_id = page.evaluate(
        "() => window?.__SPA_STATE__?.cloudId || document.querySelector('meta[name=\"ajs-cloud-id\"]')?.content"
    )
    if not cloud_id:
        raise RuntimeError(
            "Nie udało się automatycznie ustalić cloudId ze strony — sprawdź ręcznie przez DevTools "
            "(Network -> dowolne zapytanie GraphQL -> parametr cloudId w URL) i wpisz na stałe jako CLOUD_ID."
        )
    return cloud_id


def assign_statuses_via_browser(page, board_id, mapping: dict) -> None:
    """mapping: {"Nazwa kolumny": ["Status1", "Status2"], ...} - wykonuje
    dokładnie tę samą logikę co assign_statuses_to_columns.js, wbudowaną
    tutaj bezpośrednio."""
    js = """
    async ([boardId, mapping, cloudId, hash, operation, categoryMap]) => {
        const ari = `ari:cloud:jira-software:${cloudId}:board/${boardId}`;
        const variables = encodeURIComponent(JSON.stringify({ boardId: ari, cloudId }));
        const url = `/gateway/api/graphql/pq/${hash}?operation=${operation}&variables=${variables}`;
        const xsrfMatch = document.cookie.match(/(?:^|; )atlassian\\.xsrf\\.token=([^;]*)/);
        const xsrfToken = xsrfMatch ? decodeURIComponent(xsrfMatch[1]) : "";

        // Odczyt z ponowieniami: Jira bywa chwilowo zwraca PUSTĄ odpowiedź (albo nie-JSON) przy
        // wielu zapytaniach z rzędu - wtedy krótka przerwa i ponowienie.
        let json = null, lastInfo = "";
        for (let attempt = 1; attempt <= 4 && json === null; attempt++) {
            const resp = await fetch(url, {
                headers: {
                    Accept: "application/graphql-response+json, application/json, multipart/mixed;deferSpec=20220824",
                    "Content-Type": "application/json",
                    "atl-xsrf-token": xsrfToken,
                },
            });
            const text = await resp.text();
            if (!resp.ok && resp.status !== 429 && resp.status < 500) {
                throw new Error(`GraphQL ${resp.status}: ${text.slice(0, 300)}`);
            }
            try {
                if (resp.ok && text.trim()) json = JSON.parse(text);
            } catch (e) { /* nie-JSON - ponowienie */ }
            if (json === null) {
                lastInfo = `HTTP ${resp.status}, typ: ${resp.headers.get("content-type") || "-"}` +
                           (resp.redirected ? `, przekierowanie na ${resp.url}` : "") +
                           `, treść: ${text.trim() ? JSON.stringify(text.slice(0, 200)) : "(pusta)"}`;
                if (attempt < 4) await new Promise(r => setTimeout(r, 2000 * attempt));
            }
        }
        if (json === null) {
            const hint = /login|id[.]atlassian/i.test(lastInfo)
                ? " -> sesja przeglądarki wygasła: python browser_automation/save_login_session.py"
                : " -> Jira odpowiada pustą treścią mimo ponowień (przeciążenie albo ograniczenie zapytań): odczekaj kilka minut i ponów";
            throw new Error("Brak poprawnej odpowiedzi GraphQL po 4 próbach" + hint + ". Ostatnia: " + lastInfo);
        }
        const config = json?.data?.boardScope?.columnsConfig;
        if (!config) {
            const raw = JSON.stringify(json).slice(0, 400);
            const hint = /PersistedQueryNotFound|persisted/i.test(raw)
                ? " -> Jira nie zna już skrótu zapytania: przechwyć nowy GRAPHQL_QUERY_HASH (README 11b)"
                : (/unauthori|login|forbidden|401|403/i.test(raw)
                   ? " -> sesja przeglądarki wygasła: python browser_automation/save_login_session.py" : "");
            throw new Error("Nieoczekiwany kształt odpowiedzi GraphQL" + hint + ". Odpowiedź Jiry: " + raw);
        }

        const allStatusesById = new Map();
        for (const col of config.columns) for (const cs of col.columnStatus) allStatusesById.set(cs.status.id, cs.status);
        for (const s of config.unmappedStatuses) allStatusesById.set(s.id, s);

        const nameToId = new Map();
        for (const s of allStatusesById.values()) nameToId.set(s.name.trim().toLowerCase(), s.id);

        function toMapped(status) {
            const cat = categoryMap[status.category] || { id: "1", colorName: "gray" };
            return {
                id: status.id, name: status.name,
                iconUrl: "https://" + location.host + "/images/icons/statuses/generic.png",
                statusCategory: { id: cat.id, key: status.category, colorName: cat.colorName },
                issueCount: status.issueMetaData?.issueCount ?? 0,
                isPresentInWorkflow: status.isPresentInWorkflow ?? true,
            };
        }

        const assigned = new Set();
        const newMappedColumns = config.columns.map((col) => {
            const wanted = mapping[col.name] || mapping[col.name.trim()];
            if (!wanted) {
                col.columnStatus.forEach((cs) => assigned.add(cs.status.id));
                return { id: col.id, name: col.name, min: col.minCardCount ?? "", max: col.maxCardCount ?? "",
                          mappedStatuses: col.columnStatus.map((cs) => toMapped(cs.status)),
                          isKanPlanColumn: col.isKanPlanColumn, issueCount: 0 };
            }
            const statuses = [];
            for (const name of wanted) {
                const id = nameToId.get(name.trim().toLowerCase());
                if (!id) continue;
                statuses.push(toMapped(allStatusesById.get(id)));
                assigned.add(id);
            }
            return { id: col.id, name: col.name, min: col.minCardCount ?? "", max: col.maxCardCount ?? "",
                      mappedStatuses: statuses, isKanPlanColumn: col.isKanPlanColumn, issueCount: 0 };
        });

        const newUnmapped = [...allStatusesById.values()].filter((s) => !assigned.has(s.id)).map(toMapped);

        const payload = {
            rapidViewId: Number(boardId), canEdit: true,
            currentStatisticsField: { typeId: "issueCount", fieldId: "", id: config.constraintsStatisticsField.currentId,
                                       name: "Issue count", isValid: true, isEnabled: true, renderer: "number" },
            showDaysInColumn: false, statisticsFieldValidation: { isValid: true },
            statisticsFields: config.constraintsStatisticsField.availableConstraints.map((c) => ({
                typeId: c.id.replace(/_$/, ""), id: c.id, name: c.name, isValid: true,
                isEnabled: c.id === config.constraintsStatisticsField.currentId, renderer: "number",
            })),
            mappedColumns: newMappedColumns, unmappedStatuses: newUnmapped,
            workflow: {
                isSimplifiedWorkflowFeatureEnabled: true,
                usingSimplifiedWorkflow: config.workflow.usingSimplifiedWorkflow,
                canSimplifyWorkflow: config.workflow.canSimplifyWorkflow,
                userCanSimplifyWorkflow: config.workflow.userCanSimplifyWorkflow,
            },
        };

        const putResp = await fetch("/rest/greenhopper/1.0/rapidviewconfig/columns", {
            method: "PUT",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify(payload),
        });
        if (!putResp.ok) throw new Error(`PUT ${putResp.status}: ${await putResp.text()}`);
        return { status: putResp.status };
    }
    """
    result = page.evaluate(js, [board_id, mapping, CLOUD_ID, GRAPHQL_QUERY_HASH, GRAPHQL_OPERATION, CATEGORY_MAP])
    if result.get("status") != 200:
        raise RuntimeError(f"Nieoczekiwany status przypisania statusów: {result}")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def ensure_project_schemes(source_key: str, target_key: str) -> None:
    """Schemat uprawnień i powiadomień nowego projektu = jak w źródle. Bez tego (np. gdy Jira
    nie przyjęła schematu przy tworzeniu) projekt zostawał na "Default software scheme",
    w którym zespół nie miał m.in. uprawnienia Work on issues - worklogi z Tempo znikały."""
    for kind, label in (("permissionscheme", "uprawnień"), ("notificationscheme", "powiadomień")):
        def scheme(key):
            r = requests.get(f"{JIRA_BASE_URL}/rest/api/3/project/{key}/{kind}", auth=jira_auth(), timeout=30)
            return (str(r.json().get("id")), r.json().get("name", "?")) if r.status_code == 200 else (None, None)
        src_id, src_name = scheme(source_key)
        dst_id, dst_name = scheme(target_key)
        if not src_id:
            print(f"   [OSTRZEŻENIE] Nie udało się odczytać schematu {label} źródła {source_key}.")
            continue
        if src_name and src_name.startswith("Default"):
            print(f"   [OSTRZEŻENIE] Źródło {source_key} ma domyślny schemat {label} '{src_name}' - "
                  f"sprawdź, czy to właściwy projekt wzorcowy.")
        if dst_id == src_id:
            print(f"   Schemat {label}: '{dst_name}' (jak w źródle).")
            continue
        r = requests.put(f"{JIRA_BASE_URL}/rest/api/3/project/{target_key}/{kind}", auth=jira_auth(),
                         json={"id": int(src_id)}, timeout=30)
        if r.status_code < 300:
            print(f"   Schemat {label}: było '{dst_name}', ustawiono '{src_name}' (jak w źródle).")
        else:
            print(f"   [OSTRZEŻENIE] Nie udało się ustawić schematu {label} '{src_name}': {r.status_code} {r.text[:150]}")


ADDON_ROLE = "atlassian-addons-project-access"  # zarządzana przez Jirę (aplikacje), nie kopiujemy


def copy_project_settings(source_key: str, target_key: str, keep_description: bool) -> None:
    """Kategoria, domyślne przypisanie, URL i opis projektu jak w źródle."""
    src = requests.get(f"{JIRA_BASE_URL}/rest/api/3/project/{source_key}", auth=jira_auth(),
                       params={"expand": "description,url"}, timeout=30).json()
    body = {}
    if (src.get("projectCategory") or {}).get("id"):
        body["categoryId"] = int(src["projectCategory"]["id"])
    if src.get("assigneeType"):
        body["assigneeType"] = src["assigneeType"]
    if src.get("url"):
        body["url"] = src["url"]
    if src.get("description") and not keep_description:
        body["description"] = src["description"]
    if not body:
        print("   Brak dodatkowych ustawień do skopiowania.")
        return
    r = requests.put(f"{JIRA_BASE_URL}/rest/api/3/project/{target_key}", auth=jira_auth(), json=body, timeout=30)
    if r.status_code < 300:
        print(f"   Ustawienia projektu jak w źródle: {', '.join(body)}"
              + (f" (kategoria: {src['projectCategory']['name']})" if "categoryId" in body else ""))
    else:
        print(f"   [OSTRZEŻENIE] Nie udało się skopiować ustawień projektu: {r.status_code} {r.text[:150]}")


def copy_role_members(source_key: str, target_key: str) -> None:
    """Każda rola projektu: te same osoby i grupy co w źródle (dodawane, nic nie jest usuwane)."""
    roles = requests.get(f"{JIRA_BASE_URL}/rest/api/3/project/{source_key}/role", auth=jira_auth(), timeout=30).json()
    for name, url in sorted(roles.items()):
        if name == ADDON_ROLE:
            continue
        role_id = url.rstrip("/").rsplit("/", 1)[-1]
        actors = requests.get(f"{JIRA_BASE_URL}/rest/api/3/project/{source_key}/role/{role_id}",
                              auth=jira_auth(), timeout=30).json().get("actors", [])
        users = [a["actorUser"]["accountId"] for a in actors if a.get("actorUser", {}).get("accountId")]
        groups = [a["actorGroup"]["groupId"] for a in actors if a.get("actorGroup", {}).get("groupId")]
        target_url = f"{JIRA_BASE_URL}/rest/api/3/project/{target_key}/role/{role_id}"
        added, failed = 0, []
        for field, ids in (("user", users), ("groupId", groups)):
            if not ids:
                continue
            r = requests.post(target_url, auth=jira_auth(), json={field: ids}, timeout=30)
            if r.status_code < 300:
                added += len(ids)
                continue
            for one in ids:  # np. nieaktywne konto blokuje całą paczkę - pojedynczo
                r1 = requests.post(target_url, auth=jira_auth(), json={field: [one]}, timeout=30)
                if r1.status_code < 300 or "already" in r1.text.lower():
                    added += 1
                else:
                    failed.append(one)
        if users or groups:
            print(f"   Rola '{name}': {added} z {len(users) + len(groups)} (osoby: {len(users)}, grupy: {len(groups)})"
                  + (f"; nie dodano: {', '.join(failed[:5])}" if failed else ""))


def copy_components(source_key: str, target_key: str) -> None:
    """Komponenty jak w źródle (nazwa, opis, lead, domyślne przypisanie); istniejące są pomijane."""
    src = requests.get(f"{JIRA_BASE_URL}/rest/api/3/project/{source_key}/components", auth=jira_auth(), timeout=30).json()
    have = {c["name"] for c in requests.get(f"{JIRA_BASE_URL}/rest/api/3/project/{target_key}/components",
                                            auth=jira_auth(), timeout=30).json()}
    created = 0
    for c in src:
        if c["name"] in have:
            continue
        body = {"project": target_key, "name": c["name"]}
        for k in ("description", "assigneeType"):
            if c.get(k):
                body[k] = c[k]
        if (c.get("lead") or {}).get("accountId"):
            body["leadAccountId"] = c["lead"]["accountId"]
        r = requests.post(f"{JIRA_BASE_URL}/rest/api/3/component", auth=jira_auth(), json=body, timeout=30)
        if r.status_code >= 300 and "leadAccountId" in body:  # lead bez dostępu do projektu - bez leada
            body.pop("leadAccountId")
            r = requests.post(f"{JIRA_BASE_URL}/rest/api/3/component", auth=jira_auth(), json=body, timeout=30)
        if r.status_code < 300:
            created += 1
        else:
            print(f"   [OSTRZEŻENIE] Komponent '{c['name']}': {r.status_code} {r.text[:120]}")
    print(f"   Komponenty: utworzono {created}, już były {len(have & {c['name'] for c in src})}, w źródle {len(src)}.")


def _paged(url: str, params: "dict | None" = None, key: str = "values"):
    start = 0
    while True:
        r = requests.get(url, auth=jira_auth(), params={**(params or {}), "startAt": start, "maxResults": 50}, timeout=60)
        if r.status_code != 200:
            return
        d = r.json()
        vals = d.get(key) or []
        yield from vals
        if d.get("isLast", True) or not vals:
            return
        start += len(vals)


def project_scoped_contexts(source_key: str) -> list:
    """[(field_id, field_name, context_id)] pól niestandardowych, których kontekst jest ograniczony
    do wybranych projektów, w tym źródłowego (konteksty globalne pomijane - działają wszędzie)."""
    src_id = str(requests.get(f"{JIRA_BASE_URL}/rest/api/3/project/{source_key}", auth=jira_auth(), timeout=30).json()["id"])
    found = []
    fields = list(_paged(f"{JIRA_BASE_URL}/rest/api/3/field/search", {"type": "custom"}))
    for n, f in enumerate(fields, 1):
        if n % 100 == 0:
            print(f"   ...sprawdzono {n}/{len(fields)} pól", flush=True)
        for m in _paged(f"{JIRA_BASE_URL}/rest/api/3/field/{f['id']}/context/projectmapping"):
            if not m.get("isGlobalContext") and str(m.get("projectId")) == src_id:
                found.append((f["id"], f.get("name", f["id"]), str(m["contextId"])))
    return found


def copy_field_contexts(source_key: str, target_key: str, dry_run: bool = False) -> None:
    """Nowy projekt dostaje te same pola niestandardowe co źródło: do kontekstów ograniczonych
    do projektu źródłowego dopisywany jest nowy projekt. Bez tego pole może być na ekranie,
    a mimo to niewidoczne w nowym projekcie."""
    found = project_scoped_contexts(source_key)
    if dry_run:
        print(f"   [DRY-RUN] Pól z kontekstem ograniczonym do projektu źródłowego: {len(found)}"
              + (": " + ", ".join(sorted({n for _, n, _ in found})[:15]) if found else ""))
        return
    tgt_id = str(requests.get(f"{JIRA_BASE_URL}/rest/api/3/project/{target_key}", auth=jira_auth(), timeout=30).json()["id"])
    added, already, failed = 0, 0, []
    for field_id, name, ctx in found:
        mapped = {str(m.get("projectId")): str(m["contextId"])
                  for m in _paged(f"{JIRA_BASE_URL}/rest/api/3/field/{field_id}/context/projectmapping")
                  if not m.get("isGlobalContext")}
        if tgt_id in mapped:
            if mapped[tgt_id] == ctx:
                already += 1
            else:
                failed.append(f"{name} (projekt ma już inny kontekst tego pola)")
            continue
        r = requests.put(f"{JIRA_BASE_URL}/rest/api/3/field/{field_id}/context/{ctx}/project", auth=jira_auth(),
                         json={"projectIds": [tgt_id]}, timeout=30)
        if r.status_code < 300:
            added += 1
        else:
            failed.append(f"{name} ({r.status_code} {r.text[:80]})")
    print(f"   Pola niestandardowe: dodano projekt do {added} kontekstów, już było {already}, "
          f"pól z kontekstem projektowym w źródle {len(found)}.")
    for f in failed[:20]:
        print(f"   [OSTRZEŻENIE] Kontekst pola nie został rozszerzony: {f}")


def ensure_admin_access(target_key: str, lead_account_id: "str | None") -> None:
    """Dodaje ZALOGOWANE konto (to samo, którego API_TOKEN jest w .env I
    którego sesja przeglądarki jest w auth_state.json — potwierdzone, że to
    ta sama osoba) do roli Administratorów nowego projektu. Bez tego konto
    może nie mieć uprawnienia 'Browse Projects' mimo bycia globalnym
    administratorem Jiry — co objawia się komunikatem "Connect this board
    to a project or user" przy próbie otwarcia ustawień tablicy przez
    przeglądarkę (odkryte na żywo, nie przez samą dokumentację). Dodaje też
    leada, jeśli podano i jeszcze nie jest w tej roli."""
    me_resp = requests.get(f"{JIRA_BASE_URL}/rest/api/3/myself", auth=jira_auth(), timeout=30)
    me_resp.raise_for_status()
    my_account_id = me_resp.json()["accountId"]

    admin_role_id = get_role_id_by_name("Administrators")
    if not admin_role_id:
        print("   [OSTRZEŻENIE] Nie znaleziono roli 'Administrators' — pomijam ten krok.")
        return

    account_ids = [my_account_id]
    if lead_account_id and lead_account_id not in account_ids:
        account_ids.append(lead_account_id)

    add_users_to_project_role(target_key, admin_role_id, account_ids)
    print(f"   Dodano do roli Administratorów: {', '.join(account_ids)} "
          f"(w tym konto używane przez ten skrypt — potrzebne do przeglądarkowej konfiguracji kolumn).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Klucz istniejącego projektu źródłowego (np. ABC).")
    parser.add_argument("--target-key", required=True, help="Klucz nowego projektu (np. XYZ).")
    parser.add_argument("--target-name", default="", help="Nazwa nowego projektu (wymagane, chyba że --skip-project-creation).")
    parser.add_argument("--lead-account-id", default="", help="accountId leada nowego projektu (domyślnie: lead projektu źródłowego).")
    parser.add_argument("--description", default="", help="Opis nowego projektu (opcjonalnie).")
    parser.add_argument("--copy-role-members", action="store_true",
                        help="Kopiuj też członków ról ze źródła (domyślnie nie - dodawani ręcznie).")
    parser.add_argument("--dry-run", action="store_true", help="Tylko pokaż plan, nic nie twórz.")
    parser.add_argument(
        "--board-ids", type=str, default=None,
        help="ID tablic (po przecinku, np. '123,456') do UŻYCIA BEZPOŚREDNIO — jeśli podane, "
             "POMIJA całe automatyczne (i wolne przy dużej instancji) wykrywanie tablic. "
             "Podpowiedź: pierwsze uruchomienie bez tej flagi wypisze na końcu ID znalezionych "
             "tablic — zapisz je, żeby kolejne uruchomienia (np. po naprawieniu innego błędu) "
             "były natychmiastowe.",
    )
    parser.add_argument(
        "--skip-project-creation", action="store_true",
        help="Zakłada, że projekt docelowy JUŻ ISTNIEJE (np. z poprzedniej, przerwanej próby) — "
             "pomija kroki 2, 5, 6 (sprawdzenie/utworzenie projektu, przypisanie schematów) i "
             "przechodzi od razu do tablic/kolumn/statusów. --target-name i --lead-account-id "
             "są wtedy ignorowane (niepotrzebne, projekt już ma te dane).",
    )
    args = parser.parse_args()

    if not args.skip_project_creation and not args.lead_account_id:
        _src = requests.get(f"{JIRA_BASE_URL}/rest/api/3/project/{args.source.strip().upper()}",
                            auth=jira_auth(), timeout=30)
        if _src.status_code == 200 and (_src.json().get("lead") or {}).get("accountId"):
            args.lead_account_id = _src.json()["lead"]["accountId"]
            print(f"Lead nowego projektu jak w źródle: {_src.json()['lead'].get('displayName')}")
    if not args.skip_project_creation and (not args.target_name or not args.lead_account_id):
        print("[BŁĄD] --target-name jest wymagane (a --lead-account-id, gdy źródło nie ma leada), "
              "chyba że użyjesz --skip-project-creation.")
        sys.exit(1)

    source_key = args.source.strip().upper()
    target_key = args.target_key.strip().upper()

    print(f"1. Sprawdzam projekt źródłowy '{source_key}'...")
    if not jira_project_exists(source_key):
        print(f"[BŁĄD] Projekt źródłowy '{source_key}' nie istnieje.")
        sys.exit(1)
    source_project_resp = requests.get(f"{JIRA_BASE_URL}/rest/api/3/project/{source_key}", auth=jira_auth(), timeout=30)
    source_project_resp.raise_for_status()
    source_name_full = source_project_resp.json().get("name", "")
    print(f"   Nazwa projektu źródłowego: '{source_name_full}' (przyda się do dopasowania JQL po nazwie).")

    if args.skip_project_creation:
        print(f"2. --skip-project-creation: sprawdzam, czy '{target_key}' FAKTYCZNIE już istnieje...")
        if not jira_project_exists(target_key):
            print(f"[BŁĄD] --skip-project-creation podane, ale projekt '{target_key}' NIE istnieje. "
                  f"Uruchom bez tej flagi, żeby go najpierw utworzyć.")
            sys.exit(1)
        print("   OK, kontynuuję od tablic (pomijam tworzenie projektu i przypisywanie schematów).")
    else:
        print(f"2. Sprawdzam, czy '{target_key}' już nie istnieje...")
        if jira_project_exists(target_key):
            print(f"[BŁĄD] Projekt docelowy '{target_key}' już istnieje — wybierz inny klucz, "
                  f"albo użyj --skip-project-creation, żeby kontynuować pracę na nim.")
            sys.exit(1)

    print(f"3. Odkrywam tablice projektu źródłowego...")
    extra_ids = [int(x.strip()) for x in args.board_ids.split(",")] if args.board_ids else None
    source_boards = get_project_boards(source_key, extra_board_ids=extra_ids)
    print(f"   Znaleziono {len(source_boards)} tablic(e): {[b['name'] for b in source_boards]}")
    if not source_boards:
        print("   [OSTRZEŻENIE] Projekt źródłowy nie ma żadnych tablic — nowy projekt też nie dostanie żadnej.")

    if not args.skip_project_creation:
        print(f"4. Odczytuję schematy (workflow/ekrany/pola/typy zadań) z '{source_key}'...")
        schemes = get_template_schemes(source_key)

    if args.dry_run:
        print("\n[DRY-RUN] Utworzyłbym:")
        print(f"  Projekt {target_key} ('{args.target_name}'), lead={args.lead_account_id}")
        print(f"  Ze schematami skopiowanymi z {source_key}")
        for b in source_boards:
            print(f"  Tablicę '{b['name']}' (typ: {b.get('type', '?')})")
        try:
            copy_field_contexts(source_key, target_key, dry_run=True)
        except Exception as exc:  # noqa: BLE001
            print(f"   [OSTRZEŻENIE] Podgląd kontekstów pól: {exc}")
        return

    if not args.skip_project_creation:
        print(f"\n5. Tworzę projekt '{target_key}'...")
        new_project = ProjectToCreate(
            name=args.target_name, jira_key=target_key, project_type_key="software",
            template_key="com.pyxis.greenhopper.jira:gh-simplified-kanban-classic",
            lead_account_id=args.lead_account_id, description=args.description,
            matched_in_asana=False, key_was_generated=False,
        )
        result = create_jira_project(new_project, schemes)
        new_project_id = str(result["id"])
        print(f"   Utworzono projekt {target_key} (id={new_project_id}).")

        print(f"6. Przypisuję schematy workflow/ekranów/pól/typów zadań...")
        scheme_errors = apply_template_schemes(new_project_id, schemes)
        if scheme_errors:
            for err in scheme_errors:
                print(f"   [OSTRZEŻENIE] {err}")
        else:
            print("   OK.")
    else:
        print("4-6. Pominięte (--skip-project-creation) — projekt i schematy już gotowe z poprzedniej próby.")

    print(f"6a. Sprawdzam schemat uprawnień i powiadomień '{target_key}' względem '{source_key}'...")
    try:
        ensure_project_schemes(source_key, target_key)
    except Exception as exc:  # noqa: BLE001
        print(f"   [OSTRZEŻENIE] Kontrola schematów nie powiodła się: {exc}")

    print(f"6b. Kopiuję ustawienia projektu, komponenty i konteksty pól niestandardowych z '{source_key}'"
          + (" oraz członków ról..." if args.copy_role_members else " (członków ról dodaj ręcznie)..."))
    steps = [lambda: copy_project_settings(source_key, target_key, keep_description=bool(args.description)),
             lambda: copy_components(source_key, target_key),
             lambda: copy_field_contexts(source_key, target_key)]
    if args.copy_role_members:
        steps.append(lambda: copy_role_members(source_key, target_key))
    for step in steps:
        try:
            step()
        except Exception as exc:  # noqa: BLE001
            print(f"   [OSTRZEŻENIE] {exc}")

    print(f"6c. Zapewniam dostęp administratora do '{target_key}' dla konta używanego przez ten skrypt...")
    try:
        ensure_admin_access(target_key, args.lead_account_id or None)
    except Exception as exc:  # noqa: BLE001
        print(f"   [OSTRZEŻENIE] Nie udało się dodać roli Administratora: {exc}")

    if not source_boards:
        print("\nZakończono (bez tablic — projekt źródłowy ich nie miał).")
        return

    print(f"\n7. Tworzę {len(source_boards)} tablic(e) i kopiuję ich konfigurację...")
    if not STATE_FILE.exists():
        print(f"[BŁĄD] Brak {STATE_FILE} — uruchom najpierw: python browser_automation/save_login_session.py")
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        import datetime as _dt
        print(f"[sesja] {STATE_FILE} (zapisana {_dt.datetime.fromtimestamp(STATE_FILE.stat().st_mtime):%Y-%m-%d %H:%M})")
        context = browser.new_context(storage_state=str(STATE_FILE))
        page = context.new_page()
        page.goto(f"{JIRA_BASE_URL}/jira/your-work", wait_until="domcontentloaded")

        global CLOUD_ID
        if CLOUD_ID:
            print(f"   cloudId (z .env, pomijam automatyczne wykrywanie): {CLOUD_ID}")
        else:
            CLOUD_ID = resolve_cloud_id(page)
            print(f"   cloudId: {CLOUD_ID}")

        for board in source_boards:
            print(f"\n   Tablica źródłowa: '{board['name']}' (id={board['id']}, typ={board.get('type')})")
            try:
                filter_id, jql, filter_name = get_board_filter_jql(board["id"])
                new_jql = adapt_jql_to_new_project(jql, source_key, source_name_full, target_key)
                new_filter_name = f"{target_key} - {board['name']}"
                new_filter_id, filter_reused = get_or_create_filter(new_filter_name, new_jql)
                print(f"     {'Znaleziono istniejący' if filter_reused else 'Utworzono'} filtr '{new_filter_name}' (jql: {new_jql})")
                try:
                    src_share, src_edit = get_filter_permissions(filter_id)
                    src_pid, tgt_pid = get_project_id(source_key), get_project_id(target_key)
                    share = map_filter_permissions(src_share, src_pid, tgt_pid, ensure_target=True)
                    edit = map_filter_permissions(src_edit, src_pid, tgt_pid)
                    apply_filter_permissions(new_filter_id, new_filter_name, new_jql, share, edit)
                    print(f"     Udostępnienia filtra: {len(share)} (w tym projekt {target_key}), prawa edycji: {len(edit)}.")
                except Exception as exc:  # noqa: BLE001
                    print(f"     [OSTRZEŻENIE] Nie udało się ustawić udostępnień filtra - tablica może być "
                          f"niewidoczna dla zespołu ({exc}). Popraw: python fix_board_filter_permissions.py "
                          f"--project-keys {target_key} --apply")

                new_board_name = board["name"].replace(source_key, target_key)
                new_board_id, board_reused = get_or_create_board(target_key, new_board_name, board.get("type", "kanban"), new_filter_id)
                print(f"     {'Znaleziono istniejącą' if board_reused else 'Utworzono'} tablicę '{new_board_name}' (id={new_board_id})")

                source_columns = get_board_columns_rest(board["id"])
                column_names = [c["name"] for c in source_columns]
                ensure_columns_exist(page, target_key, new_board_id, column_names)
                print(f"     Skopiowano {len(column_names)} nazw kolumn.")

                all_status_ids = [sid for c in source_columns for sid in c["status_ids"]]
                status_names = get_status_names(all_status_ids)
                mapping = {c["name"]: [status_names.get(sid, sid) for sid in c["status_ids"]] for c in source_columns if c["status_ids"]}
                assign_statuses_via_browser(page, new_board_id, mapping)
                print(f"     Przypisano statusy do kolumn.")

            except Exception as exc:  # noqa: BLE001
                print(f"     [BŁĄD] Nie udało się w pełni skopiować tej tablicy: {exc}")

        browser.close()

    print(f"\nZakończono. Nowy projekt: {target_key}")


if __name__ == "__main__":
    main()
