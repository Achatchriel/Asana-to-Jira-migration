#!/usr/bin/env python3
"""
jira_sync.py

Tworzy w Jira projekty odpowiadające WYBRANYM projektom z Asany, przy czym
lista wyboru pochodzi z arkusza Google Sheets (arkusz jest źródłem prawdy —
tworzone są TYLKO projekty tam wymienione, a nie wszystkie projekty z Asany).

Logika działania:
1. Pobierz wiersze z arkusza Google (kolumna "project_name" jest wymagana,
   reszta opcjonalna).
2. Pobierz listę projektów z workspace'u Asana (żeby wzbogacić opis/notatki
   danego projektu — jeśli nie znajdzie dopasowania po nazwie, i tak
   przetwarza wiersz dalej, tylko bez dodatkowego opisu).
3. Dla każdego wiersza z arkusza wygeneruj (albo odczytaj, jeśli podany
   ręcznie) klucz projektu Jira. Klucze są cache'owane lokalnie w pliku
   JSON, więc ta sama nazwa projektu zawsze dostaje ten sam klucz przy
   kolejnych uruchomieniach.
4. Sprawdź, czy projekt o danym kluczu już istnieje w Jira:
   - jeśli tak -> pomiń (idempotentność, można uruchamiać wielokrotnie),
   - jeśli nie -> utwórz nowy projekt (kanban, company-managed).
5. Jeśli w konfiguracji ustawiono JIRA_TEMPLATE_PROJECT_KEY, po utworzeniu
   nowego projektu skopiuj do niego konfigurację wzorcowego projektu:
   schemat uprawnień, powiadomień, bezpieczeństwa, workflow, schemat ekranów
   (layout) oraz konfigurację pól. Działa tylko dla projektów
   company-managed (klasycznych) i wymaga uprawnień administratora Jira.
6. Wypisz podsumowanie: utworzone, pominięte, błędy, wiersze arkusza bez
   dopasowania w Asanie.

Autoryzacja:
- Jira: email + API token (Basic Auth), Jira Cloud REST API v3.
- Asana: Personal Access Token (Bearer).
- Google Sheets: konto serwisowe (service account JSON) + Google Sheets API.

Konfiguracja: patrz plik .env.example. Skopiuj go do .env i uzupełnij dane.

Użycie:
    python jira_sync.py --dry-run      # tylko podgląd, nic nie tworzy
    python jira_sync.py                # faktycznie tworzy projekty w Jira
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

import requests
from dotenv import load_dotenv

# Google Sheets
from google.oauth2 import service_account
from googleapiclient.discovery import build

from notify import setup_file_logging, send_slack_summary


# --------------------------------------------------------------------------- #
# Konfiguracja
# --------------------------------------------------------------------------- #

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
# override=True: wartości z .env mają pierwszeństwo nad zmiennymi już ustawionymi
# w systemie (np. przez Google Cloud SDK) — inaczej pusta/zła zmienna systemowa
# GOOGLE_APPLICATION_CREDENTIALS wygrałaby z .env.
# Jawna ścieżka (zamiast "zgadywania" przez python-dotenv) = działa niezależnie
# od katalogu, z którego uruchamiasz skrypt, i niezależnie od tego, czy ten plik
# jest importowany przez inny skrypt (np. z podfolderu browser_automation/).

JIRA_BASE_URL = os.getenv("JIRA_BASE_URL", "").rstrip("/")
JIRA_EMAIL = os.getenv("JIRA_EMAIL", "")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "")

ASANA_TOKEN = os.getenv("ASANA_TOKEN", "")
ASANA_WORKSPACE_GID = os.getenv("ASANA_WORKSPACE_GID", "")

GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")
GOOGLE_SHEET_RANGE = os.getenv("GOOGLE_SHEET_RANGE", "Arkusz1!A:F")

# Domyślne wartości używane, gdy w arkuszu brakuje danej kolumny dla wiersza
DEFAULT_PROJECT_TYPE_KEY = os.getenv("DEFAULT_PROJECT_TYPE_KEY", "software")
# Domyślnie: Kanban company-managed (klasyczny, zarządzany centralnie).
DEFAULT_TEMPLATE_KEY = os.getenv(
    "DEFAULT_TEMPLATE_KEY",
    "com.pyxis.greenhopper.jira:gh-kanban-template",
)

# Domyślny lead, użyty gdy w arkuszu brak lead_account_id dla danego wiersza.
DEFAULT_LEAD_ACCOUNT_ID = os.getenv("DEFAULT_LEAD_ACCOUNT_ID", "").strip()

# Grupa, która ma zostać dodana jako deweloperzy do każdego nowo tworzonego projektu.
# Puste = nie dodawaj żadnej grupy.
DEVELOPER_GROUP_NAME = os.getenv("DEVELOPER_GROUP_NAME", "").strip()
# Konkretni użytkownicy (accountId, po przecinku) dodawani do TEJ SAMEJ roli co
# powyżej - niezależnie od grupy, można użyć jednego, drugiego, albo obu naraz.
DEVELOPER_ACCOUNT_IDS = os.getenv("DEVELOPER_ACCOUNT_IDS", "").strip()
# Nazwa roli projektowej, do której trafi ta grupa (w standardowej Jirze to "Developers",
# ale nazwa roli może się różnić, jeśli ktoś ją zmienił/dodał własną).
DEVELOPER_ROLE_NAME = os.getenv("DEVELOPER_ROLE_NAME", "Developers").strip()

# accountId użytkownika(ów), którzy mają zostać dodani jako administratorzy do
# każdego nowo tworzonego projektu. Puste = nie dodawaj nikogo. Znajdź accountId
# przez get_my_account_id.py (dla siebie) albo get_group_members.py (dla wielu).
ADMIN_ACCOUNT_ID = os.getenv("ADMIN_ACCOUNT_ID", "").strip()  # jeden LUB kilka accountId po przecinku
# Nazwa roli projektowej dla administratorów (w standardowej Jirze to "Administrators").
ADMIN_ROLE_NAME = os.getenv("ADMIN_ROLE_NAME", "Administrators").strip()
# Jeśli True, lead KAŻDEGO projektu (row.lead_account_id albo DEFAULT_LEAD_ACCOUNT_ID)
# zostaje automatycznie dodany do roli administratora W TYM SAMYM projekcie —
# niezależnie od globalnej listy ADMIN_ACCOUNT_ID (która jest ta sama dla
# wszystkich projektów; lead różni się per projekt).
ADD_LEAD_AS_ADMIN = os.getenv("ADD_LEAD_AS_ADMIN", "false").strip().lower() == "true"

# Plik, w którym zapisywane jest mapowanie nazwa projektu -> wygenerowany klucz Jira,
# żeby przy kolejnych uruchomieniach nazwa zawsze dostawała ten sam klucz.
PROJECT_KEY_CACHE_FILE = Path(os.getenv("PROJECT_KEY_CACHE_FILE", "./project_key_cache.json"))

# Klucz istniejącego w Jira projektu, z którego skopiowana zostanie konfiguracja
# (workflow, schemat ekranów/layout, konfiguracja pól, uprawnienia, powiadomienia)
# do każdego nowo tworzonego projektu. Puste = nie kopiuj konfiguracji, użyj domyślnej.
JIRA_TEMPLATE_PROJECT_KEY = os.getenv("JIRA_TEMPLATE_PROJECT_KEY", "").strip()

MAX_JIRA_KEY_LEN = 10  # limit Jira dla project key
MIN_JIRA_KEY_LEN = 6   # minimalna długość klucza (wymóg własny, nie Jiry)

REQUIRED_ENV_VARS = [
    "JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN",
    "ASANA_TOKEN", "ASANA_WORKSPACE_GID",
    "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_SHEET_ID",
]


def check_config() -> None:
    missing = [name for name in REQUIRED_ENV_VARS if not os.getenv(name)]
    if missing:
        print("Brakuje zmiennych środowiskowych w pliku .env:")
        for name in missing:
            print(f"  - {name}")
        print("\nSkopiuj .env.example do .env i uzupełnij dane.")
        sys.exit(1)


# --------------------------------------------------------------------------- #
# Struktury danych
# --------------------------------------------------------------------------- #

@dataclass
class AsanaProject:
    gid: str
    name: str
    notes: str = ""


@dataclass
class SheetRow:
    project_name: str
    jira_key: str = ""  # opcjonalne — jeśli puste, zostanie wygenerowane
    project_type_key: str = DEFAULT_PROJECT_TYPE_KEY
    template_key: str = DEFAULT_TEMPLATE_KEY
    lead_account_id: str = ""
    description: str = ""
    board_template_id: str = ""  # opcjonalne — ID boardu wzorcowego dla UKŁADU KOLUMN
    # (używane przez bulk_configure_columns.py). Puste = domyślny JIRA_TEMPLATE_BOARD_ID.
    asana_project_link: str = ""  # opcjonalny bezpośredni link do projektu w Asanie —
    # jeśli podany, używany ZAMIAST dopasowania po nazwie (dużo pewniejsze:
    # omija literówki, duplikaty nazw, zmiany nazwy w Asanie).
    sheet_row_number: int = 0  # numer wiersza W ARKUSZU (1-indeksowany, z nagłówkiem
    # jako wiersz 1) — potrzebny do zapisania wygenerowanego jira_key z powrotem.


def extract_asana_project_gid(url: str) -> str:
    """Wyciąga GID projektu z linku do Asany. Obsługuje oba formaty adresów:
      https://app.asana.com/0/<project_gid>/list
      https://app.asana.com/0/<project_gid>/board
      https://app.asana.com/1/<workspace_gid>/project/<project_gid>/list
      https://app.asana.com/1/<workspace_gid>/project/<project_gid>/board/...
    Zwraca pusty string, jeśli nie rozpoznano formatu (np. pole w arkuszu
    jest puste, albo to nie jest link do Asany)."""
    if not url or not url.strip():
        return ""
    match = re.search(r"asana\.com/(?:0/(\d+)|1/\d+/project/(\d+))", url.strip())
    if not match:
        return ""
    return match.group(1) or match.group(2)


@dataclass
class ProjectToCreate:
    name: str
    jira_key: str
    project_type_key: str
    template_key: str
    lead_account_id: str
    description: str
    matched_in_asana: bool
    sheet_row_number: int = 0
    key_was_generated: bool = False  # True = klucz NIE był podany w arkuszu, wygenerowany automatycznie


# --------------------------------------------------------------------------- #
# Asana
# --------------------------------------------------------------------------- #

def get_asana_projects() -> list[AsanaProject]:
    """Pobiera listę projektów z workspace'u Asana (do wzbogacenia opisów)."""
    url = f"https://app.asana.com/api/1.0/workspaces/{ASANA_WORKSPACE_GID}/projects"
    headers = {"Authorization": f"Bearer {ASANA_TOKEN}"}
    params = {"opt_fields": "name,notes", "limit": 100}

    projects: list[AsanaProject] = []
    while url:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        payload = resp.json()

        for item in payload.get("data", []):
            projects.append(
                AsanaProject(gid=item["gid"], name=item["name"], notes=item.get("notes", ""))
            )

        next_page = payload.get("next_page")
        if next_page and next_page.get("uri"):
            url = next_page["uri"]
            params = {}
        else:
            url = None

    return projects


def get_asana_project_by_gid(gid: str) -> "AsanaProject | None":
    """Pobiera pojedynczy projekt Asany bezpośrednio po GID — używane, gdy
    wiersz arkusza ma podany asana_project_link (dużo pewniejsze niż
    dopasowanie po nazwie)."""
    url = f"https://app.asana.com/api/1.0/projects/{gid}"
    headers = {"Authorization": f"Bearer {ASANA_TOKEN}"}
    resp = requests.get(url, headers=headers, params={"opt_fields": "name,notes"}, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()["data"]
    return AsanaProject(gid=data["gid"], name=data["name"], notes=data.get("notes", ""))


# --------------------------------------------------------------------------- #
# Google Sheets
# --------------------------------------------------------------------------- #

SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]  # odczyt + zapis (zwrotny zapis jira_key)


def _list_sheet_tab_names(service) -> list[str]:
    """Pomocnicze: lista nazw zakładek w arkuszu, do komunikatu błędu przy złym GOOGLE_SHEET_RANGE."""
    try:
        meta = service.spreadsheets().get(spreadsheetId=GOOGLE_SHEET_ID, fields="sheets.properties.title").execute()
        return [s["properties"]["title"] for s in meta.get("sheets", [])]
    except Exception:  # noqa: BLE001
        return []


def get_sheet_rows() -> list[SheetRow]:
    """
    Pobiera wiersze z arkusza Google — to jest lista WYBRANYCH projektów z Asany,
    dla których mają zostać utworzone projekty w Jira.

    Oczekiwane nagłówki w pierwszym wierszu (wielkość liter bez znaczenia):
        project_name | jira_key | project_type_key | template_key | lead_account_id | description

    Wymagana jest kolumna "project_name" ALBO "asana_project_link" (przynajmniej
    jedna z nich musi być wypełniona) — jeśli podano tylko link, nazwa projektu
    zostanie pobrana automatycznie z Asany. Wszystkie pozostałe kolumny są
    opcjonalne:
    - jira_key: jeśli podana, zostanie użyta wprost; jeśli pusta/brak kolumny —
      klucz zostanie wygenerowany automatycznie z nazwy projektu.
    - project_type_key / template_key: użyte zostaną wartości domyślne z .env,
      jeśli puste.
    - lead_account_id / description: opcjonalne, description może zostać
      uzupełnione notatkami z Asany, jeśli projekt zostanie tam znaleziony.
    """
    creds = service_account.Credentials.from_service_account_file(
        GOOGLE_APPLICATION_CREDENTIALS, scopes=SHEETS_SCOPES
    )
    service = build("sheets", "v4", credentials=creds)

    try:
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=GOOGLE_SHEET_ID, range=GOOGLE_SHEET_RANGE)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        available = _list_sheet_tab_names(service)
        hint = (
            f" Dostępne zakładki w arkuszu: {', '.join(available)}."
            if available else ""
        )
        raise RuntimeError(
            f"Nie udało się odczytać zakresu '{GOOGLE_SHEET_RANGE}' z arkusza. "
            f"Sprawdź, czy nazwa zakładki w GOOGLE_SHEET_RANGE (w .env) zgadza się "
            f"z rzeczywistą nazwą zakładki w arkuszu.{hint}"
        ) from exc

    values = result.get("values", [])
    if not values:
        return []

    header = [h.strip().lower() for h in values[0]]

    def col(row: list[str], name: str) -> str:
        if name not in header:
            return ""
        idx = header.index(name)
        return row[idx].strip() if idx < len(row) else ""

    rows: list[SheetRow] = []
    for i, raw_row in enumerate(values[1:], start=2):  # wiersz 1 to nagłówek
        name = col(raw_row, "project_name")
        asana_link = col(raw_row, "asana_project_link")

        if not name and asana_link:
            # Brak nazwy, ale jest link - pobierz nazwę bezpośrednio z Asany,
            # żeby nie wymagać ręcznego wpisywania jej, skoro link i tak
            # jednoznacznie wskazuje właściwy projekt.
            gid = extract_asana_project_gid(asana_link)
            if gid:
                try:
                    asana_project = get_asana_project_by_gid(gid)
                    if asana_project:
                        name = asana_project.name
                except Exception as exc:  # noqa: BLE001
                    print(f"  [OSTRZEŻENIE] Wiersz {i}: nie udało się pobrać nazwy projektu z linku ({exc}) — pomijam wiersz.")

        if not name:
            continue  # pomijamy wiersze bez nazwy I bez działającego linku
        rows.append(
            SheetRow(
                project_name=name,
                jira_key=col(raw_row, "jira_key").upper(),
                project_type_key=col(raw_row, "project_type_key") or DEFAULT_PROJECT_TYPE_KEY,
                template_key=col(raw_row, "template_key") or DEFAULT_TEMPLATE_KEY,
                lead_account_id=col(raw_row, "lead_account_id"),
                description=col(raw_row, "description"),
                board_template_id=col(raw_row, "board_template_id"),
                asana_project_link=col(raw_row, "asana_project_link"),
                sheet_row_number=i,
            )
        )
    return rows


def _col_index_to_letter(idx: int) -> str:
    """Zamienia indeks kolumny (0-indeksowany) na literę arkusza (0->A, 1->B, ..., 25->Z, 26->AA...)."""
    idx += 1
    letters = ""
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def write_jira_keys_to_sheet(updates: dict[int, str]) -> None:
    """Zapisuje wygenerowane klucze Jira z powrotem do arkusza (kolumna
    'jira_key'), żeby były widoczne dla całego zespołu, nie tylko w lokalnym
    project_key_cache.json. updates: {numer_wiersza_w_arkuszu: klucz}.

    Wymaga:
    - pełnego zakresu SHEETS_SCOPES (odczyt + zapis) — już ustawione powyżej,
    - żeby konto serwisowe (GOOGLE_APPLICATION_CREDENTIALS) miało w arkuszu
      uprawnienia EDYTORA, nie tylko przeglądającego (udostępnij arkusz na
      adres e-mail konta serwisowego, tak jak przy odczycie, ale z dostępem
      \"Editor\"),
    - żeby kolumna 'jira_key' JUŻ ISTNIAŁA w nagłówku arkusza."""
    if not updates:
        return

    creds = service_account.Credentials.from_service_account_file(
        GOOGLE_APPLICATION_CREDENTIALS, scopes=SHEETS_SCOPES
    )
    service = build("sheets", "v4", credentials=creds)

    tab_name = GOOGLE_SHEET_RANGE.split("!")[0]
    header_result = (
        service.spreadsheets().values().get(spreadsheetId=GOOGLE_SHEET_ID, range=f"{tab_name}!1:1").execute()
    )
    header = [h.strip().lower() for h in header_result.get("values", [[]])[0]]
    if "jira_key" not in header:
        print(
            "  [OSTRZEŻENIE] Kolumna 'jira_key' nie istnieje w arkuszu — nie mogę "
            "zapisać wygenerowanych kluczy z powrotem. Dodaj tę kolumnę w nagłówku."
        )
        return
    col_letter = _col_index_to_letter(header.index("jira_key"))

    data = [
        {"range": f"{tab_name}!{col_letter}{row_num}", "values": [[key]]}
        for row_num, key in updates.items()
    ]
    try:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=GOOGLE_SHEET_ID, body={"valueInputOption": "RAW", "data": data}
        ).execute()
        print(f"  Zapisano {len(updates)} wygenerowany(ch) klucz(y) z powrotem do arkusza.")
    except Exception as exc:  # noqa: BLE001
        print(
            f"  [OSTRZEŻENIE] Nie udało się zapisać kluczy do arkusza: {exc}\n"
            f"  (sprawdź, czy konto serwisowe ma uprawnienia Edytora do arkusza, "
            f"nie tylko Przeglądającego)"
        )


# --------------------------------------------------------------------------- #
# Generowanie kluczy projektów Jira
# --------------------------------------------------------------------------- #

def load_key_cache() -> dict[str, str]:
    if PROJECT_KEY_CACHE_FILE.exists():
        try:
            return json.loads(PROJECT_KEY_CACHE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            print(f"Ostrzeżenie: nie udało się odczytać {PROJECT_KEY_CACHE_FILE}, zaczynam od pustego cache.")
    return {}


def save_key_cache(cache: dict[str, str]) -> None:
    PROJECT_KEY_CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def _base_key_from_name(name: str) -> str:
    """Buduje bazowy klucz z nazwy projektu, np. 'Kampania Q4' -> 'KMPQ4'.
    Wynik ma co najmniej MIN_JIRA_KEY_LEN znaków (dopełniany kolejnymi
    literami słów z nazwy, a w ostateczności literą 'X')."""
    words = re.findall(r"[A-Za-z0-9]+", name)
    if not words:
        base = "PROJ"
    elif len(words) == 1:
        base = words[0].upper()
    else:
        # inicjały wszystkich słów, np. "Redesign Strony Głównej" -> "RSG"
        initials = "".join(w[0] for w in words).upper()
        base = initials if len(initials) >= 2 else words[0].upper()

    base = re.sub(r"[^A-Z0-9]", "", base) or "PROJ"
    if not base[0].isalpha():
        base = "P" + base

    # Dopełnij do MIN_JIRA_KEY_LEN, jeśli za krótki — najpierw kolejnymi
    # literami ze słów nazwy (żeby klucz pozostał czytelny), a jeśli i tego
    # zabraknie, literą 'X'.
    if len(base) < MIN_JIRA_KEY_LEN:
        extra_letters = re.sub(r"[^A-Z0-9]", "", "".join(words).upper())
        for ch in extra_letters:
            if len(base) >= MIN_JIRA_KEY_LEN:
                break
            if ch not in base:  # unikaj oczywistych powtórzeń tej samej litery
                base += ch
        while len(base) < MIN_JIRA_KEY_LEN:
            base += "X"

    base = base[:MAX_JIRA_KEY_LEN]
    return base


def key_candidates(name: str) -> Iterator[str]:
    """Generuje kolejne kandydatury klucza: bazowy, potem z sufiksem liczbowym
    (dopełniane do MIN_JIRA_KEY_LEN, żeby sufiks nie skracał klucza poniżej minimum)."""
    base = _base_key_from_name(name)  # już w zakresie [MIN_JIRA_KEY_LEN, MAX_JIRA_KEY_LEN]
    yield base
    suffix = 2
    while True:
        s = str(suffix)
        max_base_len = MAX_JIRA_KEY_LEN - len(s)
        candidate_base = base[:max_base_len] if len(base) > max_base_len else base
        candidate = candidate_base + s
        if len(candidate) < MIN_JIRA_KEY_LEN:
            candidate += "X" * (MIN_JIRA_KEY_LEN - len(candidate))
        yield candidate[:MAX_JIRA_KEY_LEN]
        suffix += 1


def normalize(name: str) -> str:
    return " ".join(name.strip().lower().split())


def assign_jira_key(
    row: SheetRow, cache: dict[str, str], reserved_keys: set[str]
) -> str:
    """
    Zwraca klucz Jira dla danego wiersza:
    1. jeśli podano ręcznie w arkuszu (jira_key) — użyj go (szanujemy wybór użytkownika),
    2. jeśli nazwa jest już w cache — użyj zapamiętanego klucza (idempotentność),
    3. w przeciwnym razie wygeneruj nowy klucz, unikalny zarówno w obrębie tego
       przebiegu/cache, JAK I względem projektów FAKTYCZNIE ISTNIEJĄCYCH w Jira
       (sprawdzane na żywo przez REST API) — bo wygenerowany klucz mógłby
       przypadkiem pokrywać się z jakimś niepowiązanym, wcześniej istniejącym
       projektem, którego nie ma w naszym cache. Bez tej weryfikacji skrypt
       błędnie uznałby taki projekt za "już utworzony" i pominąłby tworzenie.
    """
    if row.jira_key:
        reserved_keys.add(row.jira_key)
        return row.jira_key

    norm_name = normalize(row.project_name)
    if norm_name in cache:
        key = cache[norm_name]
        reserved_keys.add(key)
        return key

    for candidate in key_candidates(row.project_name):
        if candidate in reserved_keys:
            continue
        if jira_project_exists(candidate):
            # Klucz zajęty przez projekt spoza naszego cache - to kolizja,
            # nie "już utworzone przez nas". Zarezerwuj, żeby nie próbować
            # go ponownie dla innego wiersza w tym samym przebiegu, i idź dalej.
            reserved_keys.add(candidate)
            continue
        cache[norm_name] = candidate
        reserved_keys.add(candidate)
        return candidate

    raise RuntimeError(f"Nie udało się wygenerować unikalnego klucza dla '{row.project_name}'")


# --------------------------------------------------------------------------- #
# Jira
# --------------------------------------------------------------------------- #

def jira_auth() -> tuple[str, str]:
    return (JIRA_EMAIL, JIRA_API_TOKEN)


def get_role_id_by_name(role_name: str) -> Optional[str]:
    """Znajduje ID roli projektowej po nazwie (role są globalne, wspólne dla całej Jiry)."""
    url = f"{JIRA_BASE_URL}/rest/api/3/role"
    resp = requests.get(url, auth=jira_auth(), timeout=30)
    resp.raise_for_status()
    for role in resp.json():
        if role.get("name", "").strip().lower() == role_name.strip().lower():
            return str(role["id"])
    return None


def add_group_to_project_role(project_key: str, role_id: str, group_name: str) -> None:
    """Dodaje grupę do roli projektowej (nie nadpisuje istniejących aktorów - dokłada)."""
    url = f"{JIRA_BASE_URL}/rest/api/3/project/{project_key}/role/{role_id}"
    resp = requests.post(
        url,
        auth=jira_auth(),
        headers={"Content-Type": "application/json"},
        json={"group": [group_name]},
        timeout=30,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"Błąd dodawania grupy '{group_name}' do roli: {_format_jira_error(resp)}")


def add_users_to_project_role(project_key: str, role_id: str, account_ids: list[str]) -> None:
    """Dodaje jednego LUB WIELU użytkowników (po accountId) do roli projektowej
    jednym żądaniem — Jira natywnie przyjmuje listę w polu 'user'."""
    if not account_ids:
        return
    url = f"{JIRA_BASE_URL}/rest/api/3/project/{project_key}/role/{role_id}"
    resp = requests.post(
        url,
        auth=jira_auth(),
        headers={"Content-Type": "application/json"},
        json={"user": account_ids},
        timeout=30,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"Błąd dodawania użytkownika(ów) do roli: {_format_jira_error(resp)}")


def jira_project_exists(key: str) -> bool:
    url = f"{JIRA_BASE_URL}/rest/api/3/project/{key}"
    resp = requests.get(url, auth=jira_auth(), timeout=30)
    if resp.status_code == 200:
        return True
    if resp.status_code == 404:
        return False
    resp.raise_for_status()
    return False


def get_jira_project_description(key: str) -> str:
    url = f"{JIRA_BASE_URL}/rest/api/3/project/{key}"
    resp = requests.get(url, auth=jira_auth(), timeout=30)
    resp.raise_for_status()
    return resp.json().get("description", "") or ""


def update_jira_project_description(key: str, description: str) -> None:
    url = f"{JIRA_BASE_URL}/rest/api/3/project/{key}"
    resp = requests.put(url, auth=jira_auth(), json={"description": description}, timeout=30)
    if resp.status_code >= 300:
        raise RuntimeError(f"Błąd aktualizacji opisu projektu {key}: {resp.status_code} {resp.text}")


def create_jira_project(project: ProjectToCreate, template_schemes: Optional[TemplateSchemes] = None) -> dict:
    """Tworzy nowy projekt w Jira. Jeśli podano template_schemes, ustawia od razu
    te same schematy uprawnień/powiadomień/bezpieczeństwa co projekt wzorcowy
    (schematy workflow/layout/pól są przypisywane osobno, PO utworzeniu —
    patrz apply_template_schemes)."""
    url = f"{JIRA_BASE_URL}/rest/api/3/project"

    payload = {
        "key": project.jira_key,
        "name": project.name,
        "projectTypeKey": project.project_type_key,
        "leadAccountId": project.lead_account_id or None,
        "description": project.description,
    }
    if project.template_key:
        payload["projectTemplateKey"] = project.template_key

    if template_schemes:
        if template_schemes.permission_scheme_id:
            payload["permissionScheme"] = int(template_schemes.permission_scheme_id)
        if template_schemes.notification_scheme_id:
            payload["notificationScheme"] = int(template_schemes.notification_scheme_id)
        if template_schemes.issue_security_scheme_id:
            payload["issueSecurityScheme"] = int(template_schemes.issue_security_scheme_id)

    payload = {k: v for k, v in payload.items() if v is not None}

    resp = requests.post(
        url,
        auth=jira_auth(),
        headers={"Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"Błąd tworzenia projektu {project.jira_key}: {_format_jira_error(resp)}")
    return resp.json()


def _format_jira_error(resp: requests.Response) -> str:
    """Zamienia odpowiedź błędu Jiry na czytelny komunikat (zamiast surowego JSON-a)."""
    try:
        payload = resp.json()
    except ValueError:
        return f"{resp.status_code} {resp.text}"

    parts = list(payload.get("errorMessages", []))
    for field_name, msg in payload.get("errors", {}).items():
        parts.append(f"{field_name}: {msg}")

    return f"{resp.status_code} " + ("; ".join(parts) if parts else resp.text)


# --------------------------------------------------------------------------- #
# Kopiowanie konfiguracji z projektu wzorcowego (workflow, layout, pola, ...)
# --------------------------------------------------------------------------- #
#
# Jira nie ma jednego endpointu "utwórz projekt jako kopię innego". Zamiast tego:
# 1. Odczytujemy z projektu wzorcowego ID poszczególnych schematów.
# 2. Schemat uprawnień / powiadomień / bezpieczeństwa można przekazać już przy
#    tworzeniu projektu (POST /rest/api/3/project).
# 3. Schemat workflow / schemat ekranów (issue type screen scheme) / konfigurację
#    pól (field configuration scheme) / schemat typów zadań trzeba przypisać
#    OSOBNYM zapytaniem PUT, i to tylko dopóki w nowym projekcie nie ma żadnych
#    zadań (co jest spełnione tuż po utworzeniu).
#
# Wszystkie te operacje wymagają uprawnień administratora Jira (Administer Jira
# global permission) i działają wyłącznie dla projektów company-managed (klasycznych).

@dataclass
class TemplateSchemes:
    permission_scheme_id: Optional[str] = None
    notification_scheme_id: Optional[str] = None
    issue_security_scheme_id: Optional[str] = None
    workflow_scheme_id: Optional[str] = None
    issue_type_screen_scheme_id: Optional[str] = None
    field_configuration_scheme_id: Optional[str] = None
    issue_type_scheme_id: Optional[str] = None


def get_project_id(key_or_id: str) -> str:
    url = f"{JIRA_BASE_URL}/rest/api/3/project/{key_or_id}"
    resp = requests.get(url, auth=jira_auth(), timeout=30)
    resp.raise_for_status()
    return str(resp.json()["id"])


def _get_direct_project_scheme_id(project_key: str, subpath: str) -> Optional[str]:
    """Dla schematów, które Jira zwraca bezpośrednio pod /project/{key}/{subpath}
    (permissionscheme, notificationscheme, issuesecuritylevelscheme)."""
    url = f"{JIRA_BASE_URL}/rest/api/3/project/{project_key}/{subpath}"
    resp = requests.get(url, auth=jira_auth(), timeout=30)
    if resp.status_code == 404:
        return None  # projekt nie ma własnego schematu tego typu (używa domyślnego)
    resp.raise_for_status()
    scheme_id = resp.json().get("id")
    return str(scheme_id) if scheme_id is not None else None


def _get_scheme_id_via_project_association(
    endpoint: str, scheme_key: str, project_id: str
) -> Optional[str]:
    """Dla schematów odpytywanych przez /{endpoint}?projectId=... zwracających
    listę {<scheme_key>: {id, ...}, projectIds: [...]}."""
    url = f"{JIRA_BASE_URL}/rest/api/3/{endpoint}"
    resp = requests.get(url, auth=jira_auth(), params={"projectId": project_id}, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    for value in resp.json().get("values", []):
        project_ids = [str(pid) for pid in value.get("projectIds", [])]
        if str(project_id) in project_ids:
            return str(value[scheme_key]["id"])
    return None  # projekt korzysta ze schematu domyślnego (bez własnego ID)


def get_template_schemes(template_project_key: str) -> TemplateSchemes:
    """Odczytuje ID schematów z projektu wzorcowego."""
    print(f"Odczytuję konfigurację z projektu wzorcowego '{template_project_key}'...")
    template_project_id = get_project_id(template_project_key)

    schemes = TemplateSchemes(
        permission_scheme_id=_get_direct_project_scheme_id(template_project_key, "permissionscheme"),
        notification_scheme_id=_get_direct_project_scheme_id(template_project_key, "notificationscheme"),
        issue_security_scheme_id=_get_direct_project_scheme_id(template_project_key, "issuesecuritylevelscheme"),
        workflow_scheme_id=_get_scheme_id_via_project_association(
            "workflowscheme/project", "workflowScheme", template_project_id
        ),
        issue_type_screen_scheme_id=_get_scheme_id_via_project_association(
            "issuetypescreenscheme/project", "issueTypeScreenScheme", template_project_id
        ),
        field_configuration_scheme_id=_get_scheme_id_via_project_association(
            "fieldconfigurationscheme/project", "fieldConfigurationScheme", template_project_id
        ),
        issue_type_scheme_id=_get_scheme_id_via_project_association(
            "issuetypescheme/project", "issueTypeScheme", template_project_id
        ),
    )
    print(f"  permission_scheme_id:        {schemes.permission_scheme_id or '(domyślny)'}")
    print(f"  notification_scheme_id:      {schemes.notification_scheme_id or '(domyślny)'}")
    print(f"  issue_security_scheme_id:    {schemes.issue_security_scheme_id or '(domyślny)'}")
    print(f"  workflow_scheme_id:          {schemes.workflow_scheme_id or '(domyślny)'}")
    print(f"  issue_type_screen_scheme_id: {schemes.issue_type_screen_scheme_id or '(domyślny)'}")
    print(f"  field_configuration_scheme_id: {schemes.field_configuration_scheme_id or '(domyślny)'}")
    print(f"  issue_type_scheme_id:         {schemes.issue_type_scheme_id or '(domyślny)'}")
    return schemes


def _assign_scheme_to_project(endpoint: str, id_field: str, scheme_id: str, project_id: str) -> None:
    url = f"{JIRA_BASE_URL}/rest/api/3/{endpoint}"
    body = {id_field: str(scheme_id), "projectId": str(project_id)}
    resp = requests.put(
        url, auth=jira_auth(), headers={"Content-Type": "application/json"}, json=body, timeout=30
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"Błąd przypisania schematu ({endpoint}): {resp.status_code} {resp.text}")


def apply_template_schemes(new_project_id: str, schemes: TemplateSchemes) -> list[str]:
    """
    Przypisuje do nowo utworzonego projektu schematy skopiowane z projektu
    wzorcowego (workflow, layout ekranów, konfiguracja pól, typy zadań).
    Schematy uprawnień/powiadomień/bezpieczeństwa są ustawiane już przy
    tworzeniu projektu (patrz create_jira_project), więc tutaj ich nie dotykamy.

    Zwraca listę komunikatów o błędach (puста lista = wszystko OK). Każdy
    schemat próbujemy przypisać niezależnie, żeby błąd jednego nie blokował
    pozostałych.
    """
    errors: list[str] = []
    assignments = [
        ("workflowscheme/project", "workflowSchemeId", schemes.workflow_scheme_id, "workflow"),
        ("issuetypescreenscheme/project", "issueTypeScreenSchemeId", schemes.issue_type_screen_scheme_id, "schemat ekranów (layout)"),
        ("fieldconfigurationscheme/project", "fieldConfigurationSchemeId", schemes.field_configuration_scheme_id, "konfiguracja pól"),
        ("issuetypescheme/project", "issueTypeSchemeId", schemes.issue_type_scheme_id, "schemat typów zadań"),
    ]
    for endpoint, id_field, scheme_id, label in assignments:
        if not scheme_id:
            continue  # projekt wzorcowy używa domyślnego schematu tego typu — nic do skopiowania
        try:
            _assign_scheme_to_project(endpoint, id_field, scheme_id, new_project_id)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}: {exc}")
    return errors


# --------------------------------------------------------------------------- #
# Budowanie listy projektów do utworzenia (arkusz = źródło prawdy)
# --------------------------------------------------------------------------- #

def build_projects_to_create(sheet_rows: list[SheetRow]) -> list[ProjectToCreate]:
    cache = load_key_cache()
    reserved_keys = set(cache.values())

    projects: list[ProjectToCreate] = []
    skipped_no_asana_access: list[str] = []
    for row in sheet_rows:
        asana_match = None
        gid = extract_asana_project_gid(row.asana_project_link)
        if gid:
            try:
                asana_match = get_asana_project_by_gid(gid)
                if not asana_match:
                    print(f"  [OSTRZEŻENIE] Link do Asany dla '{row.project_name}' wskazuje na "
                          f"nieistniejący/niedostępny projekt (gid={gid}) — POMIJAM CAŁY WIERSZ "
                          f"(projekt NIE zostanie utworzony w Jirze).")
                    skipped_no_asana_access.append(row.project_name)
                    continue
            except Exception as exc:  # noqa: BLE001
                print(f"  [OSTRZEŻENIE] Nie udało się pobrać projektu Asany po linku dla "
                      f"'{row.project_name}': {exc} — POMIJAM CAŁY WIERSZ "
                      f"(projekt NIE zostanie utworzony w Jirze).")
                skipped_no_asana_access.append(row.project_name)
                continue
        else:
            print(f"  [OSTRZEŻENIE] Brak asana_project_link dla '{row.project_name}' — POMIJAM CAŁY "
                  f"WIERSZ (projekt NIE zostanie utworzony w Jirze; dopasowanie po nazwie zostało "
                  f"celowo wyłączone, bo bywa niejednoznaczne przy zduplikowanych nazwach projektów w Asanie).")
            skipped_no_asana_access.append(row.project_name)
            continue

        description = row.description or (asana_match.notes if asana_match else "")
        key = assign_jira_key(row, cache, reserved_keys)
        lead_account_id = row.lead_account_id or DEFAULT_LEAD_ACCOUNT_ID

        projects.append(
            ProjectToCreate(
                name=row.project_name,
                jira_key=key,
                project_type_key=row.project_type_key,
                template_key=row.template_key,
                lead_account_id=lead_account_id,
                description=description,
                matched_in_asana=asana_match is not None,
                sheet_row_number=row.sheet_row_number,
                key_was_generated=not row.jira_key,  # w arkuszu było puste = wygenerowaliśmy
            )
        )

    if skipped_no_asana_access:
        print(f"\n[PODSUMOWANIE] Pominięto {len(skipped_no_asana_access)} wiersz(y) bez dostępu do "
              f"Asany — te projekty NIE zostały utworzone w Jirze:")
        for name in skipped_no_asana_access:
            print(f"  - {name}")

    save_key_cache(cache)
    return projects


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Tworzy w Jira projekty na podstawie listy wybranej w arkuszu Google (wzbogaconej danymi z Asany)."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Tylko pokaż, co zostałoby utworzone, bez wysyłania zapytań tworzących projekty.",
    )
    args = parser.parse_args()

    if not args.dry_run:
        setup_file_logging("jira_sync")

    check_config()

    template_schemes: Optional[TemplateSchemes] = None
    if JIRA_TEMPLATE_PROJECT_KEY:
        template_schemes = get_template_schemes(JIRA_TEMPLATE_PROJECT_KEY)
        print()

    developer_role_id: Optional[str] = None
    if DEVELOPER_GROUP_NAME or DEVELOPER_ACCOUNT_IDS:
        developer_role_id = get_role_id_by_name(DEVELOPER_ROLE_NAME)
        if developer_role_id:
            if DEVELOPER_GROUP_NAME:
                print(f"Grupa '{DEVELOPER_GROUP_NAME}' będzie dodawana do roli '{DEVELOPER_ROLE_NAME}' (id {developer_role_id}) w każdym projekcie.\n")
            if DEVELOPER_ACCOUNT_IDS:
                dev_count = len([a for a in DEVELOPER_ACCOUNT_IDS.split(",") if a.strip()])
                label = f"{dev_count} użytkowników" if dev_count > 1 else "1 użytkownik"
                print(f"{label} (DEVELOPER_ACCOUNT_IDS) będzie dodawanych do roli '{DEVELOPER_ROLE_NAME}' w każdym projekcie.\n")
        else:
            print(f"UWAGA: nie znaleziono roli '{DEVELOPER_ROLE_NAME}' w Jirze — nic NIE zostanie dodane do żadnego projektu.\n")

    admin_role_id: Optional[str] = None
    if ADMIN_ACCOUNT_ID or ADD_LEAD_AS_ADMIN:
        admin_role_id = get_role_id_by_name(ADMIN_ROLE_NAME)
        if admin_role_id:
            if ADMIN_ACCOUNT_ID:
                admin_count = len([a for a in ADMIN_ACCOUNT_ID.split(",") if a.strip()])
                label = f"{admin_count} użytkowników" if admin_count > 1 else f"Użytkownik {ADMIN_ACCOUNT_ID}"
                print(f"{label} będzie dodawany do roli '{ADMIN_ROLE_NAME}' (id {admin_role_id}) w każdym projekcie.\n")
            if ADD_LEAD_AS_ADMIN:
                print(f"Lead każdego projektu będzie dodatkowo dodawany do roli '{ADMIN_ROLE_NAME}' w SWOIM projekcie.\n")
        else:
            print(f"UWAGA: nie znaleziono roli '{ADMIN_ROLE_NAME}' w Jirze — nikt NIE zostanie dodany do żadnego projektu.\n")

    print("Pobieram wybrane projekty z arkusza Google...")
    sheet_rows = get_sheet_rows()
    print(f"  Znaleziono {len(sheet_rows)} wiersz(y) w arkuszu.")
    if not sheet_rows:
        print("Arkusz jest pusty lub brak kolumny 'project_name' — nic do zrobienia.")
        return

    projects = build_projects_to_create(sheet_rows)

    not_in_asana = [p for p in projects if not p.matched_in_asana]
    if not_in_asana:
        print(f"\nUwaga: {len(not_in_asana)} wiersz(y) z arkusza nie znaleziono w Asanie "
              f"(projekt zostanie i tak utworzony, tylko bez opisu z notatek Asany):")
        for p in not_in_asana:
            print(f"  - {p.name}")

    created, skipped, updated, errors, warnings = [], [], [], [], []
    sheet_key_updates: dict[int, str] = {}  # numer_wiersza -> nowo wygenerowany klucz

    print(f"\nPrzetwarzam {len(projects)} projekt(ów)...")
    for p in projects:
        if p.key_was_generated and p.sheet_row_number and not args.dry_run:
            sheet_key_updates[p.sheet_row_number] = p.jira_key

        try:
            if not p.lead_account_id:
                raise RuntimeError(
                    "brak project lead — uzupełnij kolumnę 'lead_account_id' dla tego "
                    "wiersza w arkuszu, albo ustaw DEFAULT_LEAD_ACCOUNT_ID w .env "
                    "(uruchom get_my_account_id.py, żeby znaleźć accountId)"
                )

            if jira_project_exists(p.jira_key):
                if p.description:
                    try:
                        current_desc = get_jira_project_description(p.jira_key)
                        if current_desc.strip() != p.description.strip():
                            if args.dry_run:
                                print(f"  [DRY-RUN] Zaktualizowałbym opis projektu {p.jira_key}.")
                            else:
                                update_jira_project_description(p.jira_key, p.description)
                                updated.append(p)
                                print(f"  [ZAKTUALIZOWANO OPIS] {p.jira_key} ('{p.name}')")
                        else:
                            skipped.append(p)
                            print(f"  [POMINIĘTO] {p.jira_key} ('{p.name}') — projekt już istnieje, opis bez zmian.")
                    except Exception as exc:  # noqa: BLE001
                        skipped.append(p)
                        print(f"  [OSTRZEŻENIE] {p.jira_key} — nie udało się sprawdzić/zaktualizować opisu: {exc}")
                else:
                    skipped.append(p)
                    print(f"  [POMINIĘTO] {p.jira_key} ('{p.name}') — projekt już istnieje w Jira.")
                continue

            if args.dry_run:
                extra = " (+ konfiguracja z projektu wzorcowego)" if template_schemes else ""
                print(f"  [DRY-RUN] Utworzyłbym projekt {p.jira_key} ('{p.name}'){extra}.")
                continue

            result = create_jira_project(p, template_schemes)
            created.append(p)
            print(f"  [UTWORZONO] {p.jira_key} — '{p.name}'")

            if developer_role_id:
                if DEVELOPER_GROUP_NAME:
                    try:
                        add_group_to_project_role(p.jira_key, developer_role_id, DEVELOPER_GROUP_NAME)
                        print(f"    Dodano grupę '{DEVELOPER_GROUP_NAME}' do roli '{DEVELOPER_ROLE_NAME}'.")
                    except Exception as exc:  # noqa: BLE001
                        print(f"    [OSTRZEŻENIE] Nie udało się dodać grupy deweloperów: {exc}")
                        warnings.append((p, f"grupa deweloperów: {exc}"))
                if DEVELOPER_ACCOUNT_IDS:
                    try:
                        dev_account_ids = [a.strip() for a in DEVELOPER_ACCOUNT_IDS.split(",") if a.strip()]
                        add_users_to_project_role(p.jira_key, developer_role_id, dev_account_ids)
                        print(f"    Dodano {len(dev_account_ids)} użytkownika(ów) do roli '{DEVELOPER_ROLE_NAME}'.")
                    except Exception as exc:  # noqa: BLE001
                        print(f"    [OSTRZEŻENIE] Nie udało się dodać użytkowników-deweloperów: {exc}")
                        warnings.append((p, f"użytkownicy-deweloperzy: {exc}"))

            if admin_role_id:
                try:
                    admin_account_ids = [a.strip() for a in ADMIN_ACCOUNT_ID.split(",") if a.strip()]
                    if ADD_LEAD_AS_ADMIN and p.lead_account_id and p.lead_account_id not in admin_account_ids:
                        admin_account_ids.append(p.lead_account_id)
                    add_users_to_project_role(p.jira_key, admin_role_id, admin_account_ids)
                    label = f"{len(admin_account_ids)} użytkowników" if len(admin_account_ids) > 1 else "użytkownika"
                    print(f"    Dodano {label} do roli '{ADMIN_ROLE_NAME}'.")
                except Exception as exc:  # noqa: BLE001
                    print(f"    [OSTRZEŻENIE] Nie udało się dodać administratora(ów): {exc}")
                    warnings.append((p, f"administrator: {exc}"))

            if template_schemes:
                scheme_errors = apply_template_schemes(str(result["id"]), template_schemes)
                if scheme_errors:
                    for err in scheme_errors:
                        print(f"    [OSTRZEŻENIE] Nie udało się skopiować: {err}")
                        warnings.append((p, err))
                else:
                    print(f"    Skopiowano konfigurację z projektu wzorcowego '{JIRA_TEMPLATE_PROJECT_KEY}'.")

        except Exception as exc:  # noqa: BLE001
            errors.append((p, str(exc)))
            print(f"  [BŁĄD] {p.jira_key} ('{p.name}'): {exc}")

    # ------------------------------------------------------------------- #
    # Podsumowanie
    # ------------------------------------------------------------------- #
    print("\n" + "=" * 60)
    print("PODSUMOWANIE")
    print("=" * 60)
    print(f"Utworzone projekty:      {len(created)}")
    print(f"Zaktualizowany opis:     {len(updated)}")
    print(f"Pominięte (istniejące):  {len(skipped)}")
    print(f"Błędy:                   {len(errors)}")
    if warnings:
        print(f"Ostrzeżenia (częściowo skopiowana konfiguracja): {len(warnings)}")
        for p, msg in warnings:
            print(f"  - {p.jira_key} ('{p.name}'): {msg}")
    print(f"Bez dopasowania w Asanie: {len(not_in_asana)}")
    print(f"\nMapowanie nazwa -> klucz zapisane w: {PROJECT_KEY_CACHE_FILE}")

    if sheet_key_updates:
        print(f"\nZapisuję {len(sheet_key_updates)} wygenerowany(ch) klucz(y) z powrotem do arkusza...")
        write_jira_keys_to_sheet(sheet_key_updates)

    if not args.dry_run:
        send_slack_summary(
            f"*jira_sync.py* zakończony.\n"
            f"Utworzone: {len(created)} | Zaktualizowany opis: {len(updated)} | "
            f"Pominięte: {len(skipped)} | Błędy: {len(errors)}"
        )

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
