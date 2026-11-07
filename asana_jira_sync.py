#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
asana_jira_sync.py - synchronizacja Asana -> Jira (krok 5 run_full_pipeline.py)

Co synchronizuje (dla każdego projektu z arkusza z wypełnionym jira_key):
  - Zadania: tytuł, opis (HTML -> ADF), przypisany (przez user_map.json),
    termin, status (sekcja Asany -> status Jiry przez section_status_map.json;
    zadanie ukończone w Asanie -> zawsze "Fertig").
  - Hierarchię:
      * zwykłe zadanie -> Task, jego podzadania -> Sub-task,
      * kandydat z epic_candidates.json (lub zgłoszenie, które już jest Epikiem)
        -> Epic, jego podzadania -> Task pod Epikiem, a ich podzadania -> Sub-task,
      * głębsze poziomy są spłaszczane pod najbliższy dopuszczalny poziom
        (top_level_ancestor_key), bo Jira nie zagnieżdża Sub-tasków.
  - Komentarze: jako konto API, z prefiksem [Asana - autor, data]
    (Jira nie pozwala podszywać się pod innego autora).
  - Załączniki: pobierane z Asany i wgrywane do Jiry.

Kierunek zwrotny (Jira -> Asana) robi osobny skrypt: jira_asana_sync.py.

Istniejące zgłoszenia: aktualizowane są tylko pola treści i status. Typ i
rodzic są ustawiane wyłącznie przy tworzeniu - ich późniejsze zmiany robią
narzędzia naprawcze (retrofit_epics.py, revert_false_epics.py,
resolve_deep_chains.py), a refresh_state_flags.py odświeża potem flagi w stanie.

Dopasowanie projektów: po asana_project_link z arkusza. Wiersz bez linku jest
POMIJANY (dopasowanie po nazwie celowo wyłączone - bywało niejednoznaczne
przy zduplikowanych nazwach projektów w Asanie).

Stan (task_sync_state.json) - mapa powiązań, dzięki której kolejne
uruchomienia nie tworzą duplikatów; zapisywany atomowo po każdym zadaniu:
{
  "<klucz projektu Jira>": {
    "<asana_task_gid>": {
      "jira_key": "PROJ-123",
      "asana_modified_at": "...",
      "is_epic": false, "is_subtask": true, "top_level_ancestor_key": "PROJ-100",
      "synced_comment_gids": [...], "synced_attachment_gids": [...],
      "deleted_in_jira": false            # opcjonalnie, patrz mark_deleted_as_skipped.py
    }
  }
}

Wymaga w .env: JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN, ASANA_TOKEN,
ASANA_WORKSPACE_GID oraz zmiennych Google Sheets używanych przez jira_sync.py.

Użycie:
  python asana_jira_sync.py --dry-run --limit 1 --project-keys KLUCZ   # podgląd
  python asana_jira_sync.py --project-keys KLUCZ1,KLUCZ2               # wybrane projekty
  python asana_jira_sync.py --shard 1/3                                # 1. z 3 równoległych procesów
  python asana_jira_sync.py                                            # wszystkie z arkusza

Przy uruchomieniu przez run_full_pipeline.py zapisuje liczby i szczegóły
błędów do pipeline_summaries/ (zbiorcze podsumowanie na Slacku wysyła
orkiestrator). Uruchomiony ręcznie wysyła własne podsumowanie.
"""
import argparse
import fcntl
import json
import os
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core  # reużywamy odczytu arkusza, cache'u kluczy, auth
from notify import setup_file_logging, send_slack_summary
from pipeline_summary import is_pipeline_child, record_error, record_warning, write_shard_summary
from project_lock import project_lock

JIRA_BASE_URL = os.environ["JIRA_BASE_URL"].rstrip("/")
JIRA_EMAIL = os.environ["JIRA_EMAIL"]
JIRA_API_TOKEN = os.environ["JIRA_API_TOKEN"]
JIRA_AUTH = (JIRA_EMAIL, JIRA_API_TOKEN)

ASANA_TOKEN = os.environ["ASANA_TOKEN"]
ASANA_WORKSPACE_GID = os.environ["ASANA_WORKSPACE_GID"]
ASANA_HEADERS = {"Authorization": f"Bearer {ASANA_TOKEN}"}
ASANA_API = "https://app.asana.com/api/1.0"

STATE_FILE = Path("task_sync_state.json")
EPIC_CANDIDATES_FILE = Path("epic_candidates.json")


def load_epic_candidates() -> dict:
    """Wczytuje epic_candidates.json (produkowany przez
    detect_epic_candidates.py) - {jira_key: [gid, ...]} zadań najwyższego
    poziomu, które mają podzadania podzadań i dlatego powinny zostać
    utworzone jako Epic. Plik jest opcjonalny - jeśli go nie ma, po prostu
    nic nie jest traktowane jako kandydat (zachowanie bez zmian)."""
    if not EPIC_CANDIDATES_FILE.exists():
        return {}
    try:
        return json.loads(EPIC_CANDIDATES_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
USER_MAP_FILE = Path("user_map.json")  # Asana user gid -> Jira accountId (ręcznie uzupełniany)
SECTION_STATUS_MAP_FILE = Path("section_status_map.json")  # nazwa sekcji Asany -> nazwa statusu Jira
WORKFLOW_GRAPH_CACHE_FILE = Path("workflow_graph_cache.json")  # trwały cache map workflow (patrz build_workflow_graph)

REQUEST_TIMEOUT = 30

# Retry z rosnącą przerwą dla PRZEJŚCIOWYCH błędów 5xx (np. przeciążenie bazy
# po stronie Jiry: "Too many concurrent connection requests", "Timed out
# acquiring connection semaphore") — te NIE oznaczają błędu w naszym żądaniu,
# tylko chwilową niedostępność zasobów po stronie serwera. Bez retry taki
# błąd zabijał całe zadanie na stałe, mimo że ponowna próba chwilę później
# zwykle się udaje.
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}  # 429 dodane - limit zapytań, warto ponowić
MAX_RETRIES = 4
RETRY_BASE_DELAY = 2  # sekundy; rośnie: 2, 4, 8, 16...


def request_with_retry(method: str, url: str, **kwargs) -> requests.Response:
    """Wrapper na requests.request() z automatycznym ponawianiem błędów 5xx
    ORAZ 429 (limit zapytań przy dużym obciążeniu równoległych procesów).
    Błędy 4xx inne niż 429 (np. 400 walidacji) NIE są ponawiane - to
    prawdziwe błędy żądania, ponowna próba i tak zwróci to samo.

    Dla 429: jeśli odpowiedź zawiera nagłówek 'Retry-After', czekamy
    DOKŁADNIE tyle, ile Jira/Asana każe (to precyzyjna wskazówka od
    serwera) - zamiast zgadywać przez nasz ogólny, rosnący czas."""
    last_exc = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.request(method, url, **kwargs)
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            resp = None

        if resp is not None and resp.status_code not in RETRYABLE_STATUS_CODES:
            return resp

        if attempt < MAX_RETRIES:
            if resp is not None and resp.status_code == 429 and resp.headers.get("Retry-After"):
                try:
                    delay = float(resp.headers["Retry-After"])
                except ValueError:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
            else:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
            reason = f"HTTP {resp.status_code}" if resp is not None else str(last_exc)
            print(f"      [PONAWIAM {attempt + 1}/{MAX_RETRIES}] {reason} — czekam {delay}s...")
            time.sleep(delay)

    return resp  # ostatnia próba - może dalej być błędem, obsłuży to wołający

# Pole niestandardowe "Type" (wspólne dla wszystkich projektów, bo pochodzi ze
# wspólnego schematu pól ze wzorca) jest wymagane przy tworzeniu zadania, ale
# ma kilka możliwych wartości, więc nie da się go uzupełnić automatycznie bez
# jawnej decyzji. Wartość domyślna ustalona z Tobą: NONBILLABLE.
DEFAULT_TYPE_FIELD_ID = os.getenv("DEFAULT_TYPE_FIELD_ID", "customfield_10188")
DEFAULT_TYPE_FIELD_VALUE = os.getenv("DEFAULT_TYPE_FIELD_VALUE", "NONBILLABLE")

_required_fields_cache: dict[tuple[str, str], dict] = {}


# --------------------------------------------------------------------------- #
# Stan lokalny (mapowanie zadań)
# --------------------------------------------------------------------------- #

# Migawka stanu z chwili wczytania (load_state) - save_state porównuje z nią stan
# w pamięci i zapisuje na dysk WYŁĄCZNIE wpisy zmienione przez TEN proces.
_STATE_SNAPSHOT: "dict | None" = None


def _deep_copy(data: dict) -> dict:
    return json.loads(json.dumps(data))


def load_state() -> dict:
    """Wczytuje stan. Blokada (flock, SHARED) na czas odczytu - chroni przed
    odczytaniem pliku w trakcie zapisu przez inny równoległy proces (bez tego
    load_state() mogło trafić na plik w trakcie nadpisywania przez
    save_state() i dostać uszkodzony JSON). Zapamiętuje migawkę dla save_state."""
    global _STATE_SNAPSHOT
    if not STATE_FILE.exists():
        _STATE_SNAPSHOT = {}
        return {}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        try:
            content = f.read()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    data = json.loads(content) if content.strip() else {}
    _STATE_SNAPSHOT = _deep_copy(data)
    return data


def save_state(state: dict) -> None:
    """Zapisuje stan bezpiecznie przy RÓWNOLEGŁYCH procesach.

    BŁĄD WCZEŚNIEJSZEJ WERSJI: scalała na poziomie CAŁYCH projektów, a każdy
    proces przekazywał CAŁY swój stan - także cudze projekty w wersji z chwili
    startu. Proces A przy zapisie swojego projektu nadpisywał więc starą kopią
    projekt, nad którym pracował proces B: wpisy nowo utworzonych zgłoszeń
    znikały ze stanu (zgłoszenia zostawały w Jirze), a następny przebieg
    tworzył je drugi raz.

    Teraz: zapisywane są tylko WPISY (projekt + gid zadania), które ten proces
    zmienił, dodał albo usunął od wczytania stanu (load_state). Wszystko inne
    na dysku zostaje dokładnie takie, jak zapisały je inne procesy. Projekty
    nieobecne w przekazanym słowniku nie są ruszane (można przekazać podzbiór).

    Dalej: blokada pliku na czas odczytu+zapisu i zapis atomowy (plik
    tymczasowy + os.replace) - plik docelowy zawsze w całości stary albo nowy.
    Dwa procesy na TYM SAMYM projekcie naraz nadal są błędem (chroni przed
    tym project_lock)."""
    global _STATE_SNAPSHOT
    base = _STATE_SNAPSHOT if _STATE_SNAPSHOT is not None else {}
    STATE_FILE.touch(exist_ok=True)
    lock_path = STATE_FILE.with_suffix(".lock")
    with open(lock_path, "w") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        try:
            content = STATE_FILE.read_text(encoding="utf-8")
            on_disk = json.loads(content) if content.strip() else {}
            for project, entries in state.items():
                if not isinstance(entries, dict):
                    on_disk[project] = entries
                    continue
                old = base.get(project) or {}
                target = on_disk.setdefault(project, {})
                for gid, entry in entries.items():
                    if old.get(gid) != entry:
                        target[gid] = entry
                for gid in old:
                    if gid not in entries:
                        target.pop(gid, None)  # usunięte świadomie przez ten proces

            tmp_path = STATE_FILE.with_suffix(".json.tmp")
            with open(tmp_path, "w", encoding="utf-8") as tmp_f:
                json.dump(on_disk, tmp_f, indent=2, ensure_ascii=False)
                tmp_f.flush()
                os.fsync(tmp_f.fileno())  # wymuś zapis na dysk przed rename
            os.replace(tmp_path, STATE_FILE)  # atomowe na poziomie systemu plików
            # migawka = to, co ten proces właśnie zapisał (dla kolejnych zapisów)
            for project, entries in state.items():
                base[project] = _deep_copy(entries) if isinstance(entries, dict) else entries
            _STATE_SNAPSHOT = base
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)


def load_user_map() -> dict:
    """Asana user gid -> Jira accountId. Musisz to uzupełnić ręcznie (lub przez
    osobny skrypt dopasowujący po adresie e-mail — daj znać, jeśli chcesz,
    żebym taki dopisał). Bez wpisu w tej mapie zadania importują się jako
    nieprzypisane (assignee=None), zamiast się wywalać."""
    if USER_MAP_FILE.exists():
        return json.loads(USER_MAP_FILE.read_text(encoding="utf-8"))
    return {}


# Zadania, które zespół ręcznie przeniósł do Jiry i oznaczył w Asanie dopiskiem
# (np. "Agenda JF (migriert zu Jira)", także z literówką "mirgiert"). Dla nich
# źródłem prawdy jest Jira: zadanie RAZEM Z PODDRZEWEM jest pomijane (ani
# tworzone, ani aktualizowane). Wzorzec (regex, bez wielkości liter) w .env.
# Pusta wartość w .env = wzorzec domyślny (pusty regex pasowałby do KAŻDEGO tytułu).
MIGRATED_TITLE_RE = re.compile(os.getenv("MIGRATED_TO_JIRA_PATTERN") or r"mi[rg]{1,2}r?iert\s+zu\s+jira", re.IGNORECASE)


def is_jira_master(title: str, entry: "dict | None") -> bool:
    """True = zadanie prowadzone w Jirze: dopisek w tytule z Asany albo wpis w stanie
    oznaczony przez handle_migrated_to_jira.py (dzieci takiego zadania nie mają dopisku)."""
    return bool(MIGRATED_TITLE_RE.search(title or "")) or bool((entry or {}).get("jira_master"))


COMPLETED_KEY = "__ukonczone__"
PROFILE_KEY = "__szablon__"
_PROJECT_OVERRIDES: dict = {}


def project_section_overrides(project_key: str) -> dict:
    """Mapa sekcji TYLKO dla jednego projektu: section_status_map.<KLUCZ>.json obok
    głównej mapy. Jej wpisy mają pierwszeństwo przed section_status_map.json (która
    zostaje nietknięta). Specjalny wpis "__ukonczone__" = status dla zadań
    ukończonych w Asanie (gdy brak - wpis "__ukonczone__" z mapy głównej, a w końcu "Fertig").
    Brak pliku = zachowanie jak dotąd."""
    if project_key not in _PROJECT_OVERRIDES:
        path = SECTION_STATUS_MAP_FILE.with_name(f"{SECTION_STATUS_MAP_FILE.stem}.{project_key}.json")
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        # Wspólny szablon dla grupy projektów: {"__szablon__": "discovery", ...własne wpisy}
        # -> section_status_profiles/discovery.json, wpisy projektu mają pierwszeństwo.
        profile = data.pop(PROFILE_KEY, None)
        if profile:
            ppath = SECTION_STATUS_MAP_FILE.parent / "section_status_profiles" / f"{profile}.json"
            if not ppath.exists():
                raise RuntimeError(f"{path.name}: szablon '{profile}' nie istnieje ({ppath})")
            data = {**json.loads(ppath.read_text(encoding="utf-8")), **data}
        _PROJECT_OVERRIDES[project_key] = data
    return _PROJECT_OVERRIDES[project_key]


def load_section_status_map() -> dict:
    """Nazwa sekcji Asany -> nazwa statusu Jira. Sekcje Asany bywają etapami/
    kamieniami milowymi, a NIE nazwami pasującymi wprost do statusów Jiry
    (np. "Orientierungsphase" zamiast "New In") — stąd osobna, ręcznie
    uzupełniana tabela zamiast prostego dopasowania 1:1 po nazwie. Zbuduj ją
    przez: python list_asana_sections.py "Nazwa projektu". Sekcja bez wpisu
    (albo z pustą wartością) = zadanie zostaje przy domyślnym statusie
    startowym workflow, bez próby przejścia."""
    if SECTION_STATUS_MAP_FILE.exists():
        return json.loads(SECTION_STATUS_MAP_FILE.read_text(encoding="utf-8"))
    return {}


# --------------------------------------------------------------------------- #
# Asana — odczyt
# --------------------------------------------------------------------------- #

def find_asana_project_gid(project_name: str) -> Optional[str]:
    url = f"{ASANA_API}/workspaces/{ASANA_WORKSPACE_GID}/projects"
    params = {"opt_fields": "name", "limit": 100}
    norm_target = core.normalize(project_name)
    offset = None
    while True:
        if offset:
            params["offset"] = offset
        resp = request_with_retry("GET", url, headers=ASANA_HEADERS, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
        for p in payload.get("data", []):
            if core.normalize(p["name"]) == norm_target:
                return p["gid"]
        offset = payload.get("next_page", {}).get("offset") if payload.get("next_page") else None
        if not offset:
            return None


def get_asana_tasks(project_gid: str) -> list[dict]:
    """Pobiera WSZYSTKIE zadania projektu (niezależnie od sekcji/statusu),
    z polami potrzebnymi do synchronizacji podstawowych danych."""
    url = f"{ASANA_API}/projects/{project_gid}/tasks"
    fields = [
        "name", "html_notes", "completed", "due_on", "assignee.gid", "assignee.email",
        "modified_at", "memberships.section.name", "memberships.project.gid", "parent.gid",
    ]
    params = {"opt_fields": ",".join(fields), "limit": 100}
    tasks = []
    offset = None
    while True:
        if offset:
            params["offset"] = offset
        resp = request_with_retry("GET", url, headers=ASANA_HEADERS, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
        tasks.extend(payload.get("data", []))
        offset = payload.get("next_page", {}).get("offset") if payload.get("next_page") else None
        if not offset:
            return tasks


def order_parents_first(tasks: list[dict]) -> "tuple[list[dict], set]":
    """Asana zwraca na liście zadań projektu także PODZADANIA dodane do
    projektu, w dowolnej kolejności. Gdyby podzadanie było przetworzone
    przed rodzicem, powstałoby jako samodzielny Task, a rodzic zastałby już
    istniejący wpis i nie przepiąłby go pod siebie. Sortujemy stabilnie po
    głębokości zagnieżdżenia w obrębie projektu (rodzic zawsze przed dzieckiem).
    Zwraca (posortowana lista, zbiór gid zadań, których rodzic jest w projekcie)."""
    in_project = {t["gid"] for t in tasks}
    parent_of = {}
    for t in tasks:
        parent_gid = (t.get("parent") or {}).get("gid")
        if parent_gid in in_project:
            parent_of[t["gid"]] = parent_gid

    def depth(gid: str) -> int:
        d, seen = 0, {gid}
        while gid in parent_of and parent_of[gid] not in seen:
            gid = parent_of[gid]
            seen.add(gid)
            d += 1
        return d

    # Drugi klucz: zadanie z JAKIMKOLWIEK rodzicem (także spoza listy projektu) po
    # zadaniach bez rodzica. Jego przodek może być w projekcie wyżej w drzewie
    # (zadanie w kilku projektach Asany) - wtedy rekurencja przodka utworzy je we
    # właściwym miejscu, zanim pętla główna dojdzie do niego jako do "sieroty".
    def has_parent(t: dict) -> bool:
        return bool((t.get("parent") or {}).get("gid"))

    ordered = sorted(tasks, key=lambda t: (depth(t["gid"]), has_parent(t)))
    return ordered, set(parent_of) | {t["gid"] for t in tasks if has_parent(t)}


def get_task_section_name(task: dict, project_gid: str) -> Optional[str]:
    """Zwraca nazwę sekcji zadania W KONKRETNYM PROJEKCIE (project_gid) — NIE
    pierwszą z brzegu. Zadanie może należeć jednocześnie do wielu projektów
    w Asanie (np. zadania przykładowe/demo Asany bywają dodane też do innych,
    szablonowych projektów) - branie pierwszego membershipu z listy dawało
    sekcję z ZUPEŁNIE INNEGO projektu, więc status w Jirze nigdy się nie
    zgadzał (to był dokładnie zgłoszony problem)."""
    memberships = task.get("memberships") or []
    for m in memberships:
        project = m.get("project") or {}
        if project.get("gid") != project_gid:
            continue
        section = m.get("section")
        if section and section.get("name"):
            return section["name"].strip()
    return None


def get_asana_subtasks(task_gid: str) -> list[dict]:
    """Pobiera WSZYSTKIE podzadania danego zadania Asany - osobny endpoint,
    bo GET /projects/{gid}/tasks (get_asana_tasks) NIE zwraca podzadań w
    ogóle, tylko zadania najwyższego poziomu (potwierdzone w dokumentacji
    Asany - to był dokładnie zgłoszony problem: "dlaczego podzadania nie
    są migrowane")."""
    url = f"{ASANA_API}/tasks/{task_gid}/subtasks"
    fields = ["name", "html_notes", "completed", "due_on", "assignee.gid", "assignee.email", "modified_at"]
    params = {"opt_fields": ",".join(fields), "limit": 100}
    subtasks = []
    offset = None
    while True:
        if offset:
            params["offset"] = offset
        resp = request_with_retry("GET", url, headers=ASANA_HEADERS, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
        subtasks.extend(payload.get("data", []))
        offset = payload.get("next_page", {}).get("offset") if payload.get("next_page") else None
        if not offset:
            return subtasks


def get_asana_comments(task_gid: str) -> list[dict]:
    """Zwraca TYLKO prawdziwe komentarze (nie zdarzenia systemowe typu
    'zmienił termin', 'przypisał zadanie' itp.)."""
    url = f"{ASANA_API}/tasks/{task_gid}/stories"
    params = {"opt_fields": "html_text,text,created_at,created_by.name,resource_subtype"}
    resp = request_with_retry("GET", url, headers=ASANA_HEADERS, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    stories = resp.json().get("data", [])
    return [s for s in stories if s.get("resource_subtype") == "comment_added"]


def get_asana_attachments(task_gid: str) -> list[dict]:
    url = f"{ASANA_API}/tasks/{task_gid}/attachments"
    params = {"opt_fields": "name,download_url"}
    resp = request_with_retry("GET", url, headers=ASANA_HEADERS, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json().get("data", [])


# --------------------------------------------------------------------------- #
# Jira — zapis
# --------------------------------------------------------------------------- #

def text_to_adf(text: str) -> dict:
    """Konwertuje ZWYKŁY tekst (bez znaczników) na minimalny ADF — używane
    tam, gdzie nie ma bogatego formatowania (np. prefiks komentarza)."""
    if not text:
        return {"type": "doc", "version": 1, "content": []}
    paragraphs = [p for p in text.split("\n\n") if p.strip()] or [text]
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": p}]}
            for p in paragraphs
        ],
    }


class _HtmlToAdfParser(HTMLParser):
    """Parser HTML z Asany (pole html_notes, otoczone <body>...</body>) na
    Atlassian Document Format. Obsługuje: pogrubienie (<strong>/<b>), kursywę
    (<em>/<i>), podkreślenie (<u>), przekreślenie (<s>/<strike>), linki (<a>),
    listy (<ul>/<ol>/<li>), akapity/nowe linie. To NIE jest pełny parser HTML
    - obsługuje podzbiór znaczników, których faktycznie używa Asana."""

    _MARK_TAGS = {"strong": "strong", "b": "strong", "em": "em", "i": "em",
                  "u": "underline", "s": "strike", "strike": "strike"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.doc: list[dict] = []
        self._marks: list[str] = []
        self._link_href: Optional[str] = None
        self._para: list[dict] = []
        self._list_stack: list[dict] = []  # [{"type": "bulletList"/"orderedList", "items": [...]}]
        self._list_item_content: Optional[list] = None

    def _current_text_target(self) -> list:
        if self._list_item_content is not None:
            return self._list_item_content
        return self._para

    def _flush_paragraph(self):
        if self._para:
            self.doc.append({"type": "paragraph", "content": self._para})
            self._para = []

    def handle_starttag(self, tag, attrs):
        if tag in self._MARK_TAGS:
            self._marks.append(self._MARK_TAGS[tag])
        elif tag == "a":
            href = dict(attrs).get("href")
            self._link_href = href
        elif tag in ("ul", "ol"):
            self._flush_paragraph()
            self._list_stack.append({"type": "bulletList" if tag == "ul" else "orderedList", "items": []})
        elif tag == "li":
            self._list_item_content = []
        elif tag == "br":
            self._current_text_target().append({"type": "hardBreak"})

    def handle_endtag(self, tag):
        if tag in self._MARK_TAGS:
            mark = self._MARK_TAGS[tag]
            if mark in self._marks:
                self._marks.remove(mark)
        elif tag == "a":
            self._link_href = None
        elif tag == "li":
            content = self._list_item_content or [{"type": "paragraph", "content": []}]
            if content and content[0].get("type") != "paragraph":
                content = [{"type": "paragraph", "content": content}]
            if self._list_stack:
                self._list_stack[-1]["items"].append({"type": "listItem", "content": content})
            self._list_item_content = None
        elif tag in ("ul", "ol"):
            if self._list_stack:
                finished = self._list_stack.pop()
                node = {"type": finished["type"], "content": finished["items"]}
                if self._list_stack:
                    # zagnieżdżona lista - dołóż do ostatniego elementu listy nadrzędnej
                    self._list_item_content = self._list_item_content or []
                    self._list_item_content.append(node)
                else:
                    self.doc.append(node)
        elif tag == "body":
            self._flush_paragraph()

    def handle_data(self, data):
        if not data:
            return
        target = self._current_text_target()
        node = {"type": "text", "text": data}
        marks = []
        for m in self._marks:
            marks.append({"type": m})
        if self._link_href:
            marks.append({"type": "link", "attrs": {"href": self._link_href}})
        if marks:
            node["marks"] = marks
        target.append(node)

    def get_adf(self) -> dict:
        self._flush_paragraph()
        # Puste akapity (bez treści) bywają odrzucane przez walidator ADF Jiry
        # jako niepoprawna struktura - odfiltruj je, zamiast ryzykować błąd 400
        # przy tworzeniu/aktualizacji zadania.
        content = [node for node in self.doc if node.get("type") != "paragraph" or node.get("content")]
        return {"type": "doc", "version": 1, "content": content or [{"type": "paragraph", "content": [{"type": "text", "text": " "}]}]}


def html_to_adf(html: str) -> dict:
    """Konwertuje html_notes z Asany na ADF. Pusty/brak wejścia -> pusty dokument."""
    if not html or not html.strip():
        return {"type": "doc", "version": 1, "content": []}
    parser = _HtmlToAdfParser()
    try:
        parser.feed(html)
        return parser.get_adf()
    except Exception:  # noqa: BLE001
        # Awaryjnie: zwykły tekst bez znaczników, żeby sync się nie wywrócił
        # przez nietypowy HTML - lepiej stracić formatowanie niż całe zadanie.
        import re
        plain = re.sub(r"<[^>]+>", "", html)
        return text_to_adf(plain)


def _adf_marks_to_html(text: str, marks: list) -> str:
    html = text
    href = None
    for m in marks or []:
        mtype = m.get("type")
        if mtype == "strong":
            html = f"<strong>{html}</strong>"
        elif mtype == "em":
            html = f"<em>{html}</em>"
        elif mtype == "underline":
            html = f"<u>{html}</u>"
        elif mtype == "strike":
            html = f"<s>{html}</s>"
        elif mtype == "link":
            href = m.get("attrs", {}).get("href")
    if href:
        html = f'<a href="{href}">{html}</a>'
    return html


def adf_to_html(adf: dict) -> str:
    """Konwertuje ADF (opis/komentarz z Jiry) na HTML zrozumiały dla pola
    html_notes w Asanie (Asana wymaga całości owiniętej w <body>...</body>).
    Obsługuje ten sam podzbiór znaczników co html_to_adf (symetrycznie)."""
    def render_inline(nodes: list) -> str:
        parts = []
        for n in nodes or []:
            if n.get("type") == "text":
                parts.append(_adf_marks_to_html(n.get("text", ""), n.get("marks", [])))
            elif n.get("type") == "hardBreak":
                parts.append("<br>")
        return "".join(parts)

    def render_block(node: dict) -> str:
        t = node.get("type")
        if t == "paragraph":
            return render_inline(node.get("content", [])) + "\n"
        if t in ("bulletList", "orderedList"):
            tag = "ul" if t == "bulletList" else "ol"
            items = ""
            for item in node.get("content", []):
                inner = "".join(render_block(c) for c in item.get("content", []))
                items += f"<li>{inner.strip()}</li>"
            return f"<{tag}>{items}</{tag}>\n"
        return ""

    if not adf or not adf.get("content"):
        return "<body></body>"
    body = "".join(render_block(n) for n in adf["content"])
    return f"<body>{body}</body>"


_PROJECT_STATUSES_CACHE: dict = {}


def get_status_id_by_name(project_key: str, status_name: str) -> Optional[str]:
    """Znajduje ID statusu o danej nazwie wśród statusów dostępnych w projekcie
    (case-insensitive). Zwraca None, jeśli nie znaleziono — wtedy status
    pomijamy zamiast zgadywać."""
    if project_key not in _PROJECT_STATUSES_CACHE:
        url = f"{JIRA_BASE_URL}/rest/api/3/project/{project_key}/statuses"
        resp = request_with_retry("GET", url, auth=JIRA_AUTH, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        _PROJECT_STATUSES_CACHE[project_key] = resp.json()  # statusy nie zmieniają się w trakcie przebiegu
    norm_target = status_name.strip().lower()
    for issue_type in _PROJECT_STATUSES_CACHE[project_key]:
        for status in issue_type.get("statuses", []):
            if status["name"].strip().lower() == norm_target:
                return status["id"]
    return None


def get_required_field_defaults(project_key: str, issue_type_name: str = "Task") -> dict:
    """Zwraca payload {field_id: wartość} dla WYMAGANYCH pól, które da się
    uzupełnić automatycznie:
      - Pola z DOKŁADNIE JEDNĄ dozwoloną wartością (np. "Department" - różni
        się per projekt, ale zawsze jest jednoznaczna) -> ta wartość.
      - DEFAULT_TYPE_FIELD_ID ("Type") -> DEFAULT_TYPE_FIELD_VALUE (ustalone
        z Tobą: NONBILLABLE).
    Pola, których nie da się tak ustawić (więcej niż jedna opcja, bez
    skonfigurowanej wartości domyślnej), są pomijane z ostrzeżeniem - Jira
    zwróci wtedy czytelny błąd 400 przy tworzeniu, identyczny w formie do
    tego, który już widzieliśmy (łatwo rozpoznać, co jeszcze trzeba dopisać).
    Wynik jest cache'owany per (project_key, issue_type_name), żeby nie pytać
    Jiry o to samo przy każdym zadaniu."""
    cache_key = (project_key, issue_type_name)
    if cache_key in _required_fields_cache:
        return _required_fields_cache[cache_key]

    url = f"{JIRA_BASE_URL}/rest/api/3/issue/createmeta"
    params = {
        "projectKeys": project_key,
        "issuetypeNames": issue_type_name,
        "expand": "projects.issuetypes.fields",
    }
    resp = requests.get(url, auth=JIRA_AUTH, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    projects = resp.json().get("projects", [])
    if not projects or not projects[0].get("issuetypes"):
        _required_fields_cache[cache_key] = {}
        return {}

    fields_meta = projects[0]["issuetypes"][0].get("fields", {})
    defaults: dict = {}
    skipped: list[str] = []

    for field_id, info in fields_meta.items():
        if not info.get("required") or field_id in ("summary", "issuetype", "project", "parent", get_epic_name_field_id()):
            continue  # obsługiwane już wprost w create_jira_issue ("parent" tylko dla podzadań, ale zawsze wymagany właśnie tam - nie generyczny brak wartości domyślnej; "Epic Name" ustawiane jawnie tylko dla Epików)

        if field_id == DEFAULT_TYPE_FIELD_ID:
            defaults[field_id] = {"value": DEFAULT_TYPE_FIELD_VALUE}
            continue

        allowed = info.get("allowedValues")
        if allowed and len(allowed) == 1:
            only = allowed[0]
            defaults[field_id] = {"id": only["id"]}
        elif field_id != "reporter":  # reporter domyślnie = użytkownik API, Jira sama to uzupełnia
            skipped.append(info.get("name", field_id))

    if skipped:
        print(
            f"    [OSTRZEŻENIE] Wymagane pole(a) bez jednoznacznej wartości domyślnej "
            f"w '{project_key}': {', '.join(skipped)} — tworzenie może się nie udać, "
            f"dopisz obsługę tych pól ręcznie."
        )

    _required_fields_cache[cache_key] = defaults
    return defaults


_project_lead_cache: dict[str, Optional[str]] = {}


def get_project_lead_account_id(project_key: str) -> Optional[str]:
    """Zwraca accountId leada DANEGO projektu (nie globalny domyślny) —
    cache'owane, żeby nie odpytywać za każdym razem."""
    if project_key in _project_lead_cache:
        return _project_lead_cache[project_key]
    url = f"{JIRA_BASE_URL}/rest/api/3/project/{project_key}"
    resp = requests.get(url, auth=JIRA_AUTH, params={"fields": "lead"}, timeout=REQUEST_TIMEOUT)
    lead_id = None
    if resp.status_code == 200:
        lead_id = (resp.json().get("lead") or {}).get("accountId")
    _project_lead_cache[project_key] = lead_id
    return lead_id


def _is_assignee_error(resp: requests.Response) -> bool:
    """Sprawdza, czy błąd 400 dotyczy KONKRETNIE pola 'assignee' (np. użytkownik
    nie może być przypisany w tym projekcie — brak uprawnień/nie jest członkiem)."""
    try:
        return "assignee" in resp.json().get("errors", {})
    except Exception:  # noqa: BLE001
        return False


JIRA_SUMMARY_MAX_LEN = 255  # twardy limit pola 'summary' w Jirze
# Twardy limit Jiry dla 'description' to 32767 znaków (w finalnym ADF).
# Bezpieczny próg WYRAŹNIE niższy - konwersja HTML->ADF dokłada narzut
# strukturalny (JSON dla każdego formatowania), więc surowy HTML bliski
# 32767 może po konwersji PRZEKROCZYĆ prawdziwy limit.
JIRA_DESCRIPTION_SAFE_LEN = 25000


def _safe_summary_and_description(summary: str, description_html: str) -> tuple[str, str]:
    """Jeśli tytuł z Asany przekracza limit Jiry (255 znaków), skraca go
    bezpiecznie (z wielokropkiem) i dokłada PEŁNY, oryginalny tytuł na
    początku opisu — żeby nic nie zginęło, tylko było przesunięte.

    Dodatkowo: pole 'summary' w Jirze MUSI być jednoliniowe - Jira odrzuca
    (400 'contains newline characters') tytuły z literalnym znakiem nowej
    linii, co zdarza się w Asanie (np. ktoś przypadkiem wcisnął Enter przy
    nazywaniu zadania - ten sam rodzaj problemu, co wcześniej znaleziony w
    nazwach SEKCJI). Zamieniamy każdy znak nowej linii na spację (nie
    wycinamy - zachowujemy czytelność), potem redukujemy wielokrotne spacje
    do jednej.

    Dodatkowo: pole 'description' w Jirze ma limit 32767 znaków (w
    finalnym, zserializowanym ADF). Konwersja HTML->ADF zwykle ZWIĘKSZA
    liczbę znaków względem surowego HTML (struktura JSON dla formatowania
    dokłada narzut) - dlatego próg bezpieczeństwa tutaj jest wyraźnie
    NIŻSZY niż 32767, żeby zostawić margines na ten narzut."""
    summary = re.sub(r"\s*\n\s*", " ", summary).strip()

    if len(summary) <= JIRA_SUMMARY_MAX_LEN:
        merged = description_html
    else:
        truncated_summary = summary[: JIRA_SUMMARY_MAX_LEN - 1].rstrip() + "…"
        prefix = f"<body><strong>Pełny tytuł:</strong> {summary}<br><br>"
        if description_html and description_html.strip().startswith("<body>"):
            merged = prefix + description_html[len("<body>"):]
        else:
            merged = prefix + (description_html or "") + "</body>"
        print(f"      [INFO] Tytuł dłuższy niż {JIRA_SUMMARY_MAX_LEN} znaków — skrócony, pełna wersja w opisie.")
        summary = truncated_summary

    if len(merged) > JIRA_DESCRIPTION_SAFE_LEN:
        truncated_desc = merged[:JIRA_DESCRIPTION_SAFE_LEN]
        suffix = "<br><br><strong>[OPIS UCIĘTY - przekroczył limit Jiry (32767 znaków). Pełna treść w oryginalnym zadaniu Asany.]</strong></body>"
        if truncated_desc.rstrip().endswith("</body>"):
            truncated_desc = truncated_desc.rstrip()[: -len("</body>")]
        merged = truncated_desc + suffix
        print(f"      [INFO] Opis dłuższy niż limit Jiry — ucięty do {JIRA_DESCRIPTION_SAFE_LEN} znaków.")

    return summary, merged


def _is_adf_error(resp: requests.Response, field_name: str = "description") -> bool:
    """Sprawdza, czy błąd 400 dotyczy KONKRETNIE nieprawidłowej struktury ADF
    w podanym polu (domyślnie 'description', ale też używane dla 'comment') -
    nasz konwerter html_to_adf czasem generuje coś, czego walidator Jiry nie
    akceptuje (np. pusty akapit, niepoprawne zagnieżdżenie znaczników).
    Sprawdzamy szeroko - Jira zwraca RÓŻNE kształty błędu 400 dla tego samego
    problemu:
      1) {"errors": {"description": "opisowy tekst o Atlassian Document Format"}}
      2) {"errorMessages": ["INVALID_INPUT"], "errors": {}} - errors PUSTE,
         żadne pole nie wymienione (potwierdzony ręcznie przypadek:
         update_jira_issue na polu description) - poprzednia wersja tej
         funkcji sprawdzała TYLKO wariant 1, więc wariant 2 nigdy nie był
         wykrywany, mimo że własna dokumentacja funkcji o nim wspominała."""
    if resp.status_code != 400:
        return False
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return False
    if field_name in (data.get("errors") or {}):
        return True
    return "INVALID_INPUT" in (data.get("errorMessages") or [])


def _strip_html_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html or "").strip()


_SUBTASK_TYPE_CACHE: dict[str, str] = {}  # project_key -> nazwa typu podzadania (buforowane)


def get_subtask_issue_type_name(project_key: str) -> str:
    """Zwraca RZECZYWISTĄ nazwę typu zadania oznaczonego jako 'podzadanie'
    w DANYM projekcie - NIE zawsze dosłowne 'Sub-task' (projekty
    "team-managed" często używają 'Subtask' bez myślnika, a niektóre
    organizacje zmieniają nazwę jeszcze inaczej). Sztywne założenie nazwy
    powodowało błąd 'Specify a valid issue type' - potwierdzony ręcznie.
    Buforowane per projekt, żeby nie pytać Jiry osobno dla każdego
    podzadania."""
    if project_key in _SUBTASK_TYPE_CACHE:
        return _SUBTASK_TYPE_CACHE[project_key]

    proj_resp = request_with_retry("GET", f"{JIRA_BASE_URL}/rest/api/3/project/{project_key}", auth=JIRA_AUTH, timeout=REQUEST_TIMEOUT)
    proj_resp.raise_for_status()
    numeric_id = proj_resp.json()["id"]

    resp = request_with_retry(
        "GET", f"{JIRA_BASE_URL}/rest/api/3/issuetype/project", auth=JIRA_AUTH,
        params={"projectId": numeric_id, "level": -1}, timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    types = resp.json()
    name = types[0]["name"] if types else "Sub-task"  # fallback, gdyby projekt naprawdę nie miał żadnego
    _SUBTASK_TYPE_CACHE[project_key] = name
    return name


_EPIC_NAME_FIELD_CACHE: Optional[str] = None


def has_already_migrated_subtasks(parent_asana_gid: str, project_state: dict) -> bool:
    """Sprawdza, czy podzadania danego zadania Asany są JUŻ zmigrowane do
    Jiry (mają wpis w stanie z kluczem Jiry). Używane jako zabezpieczenie
    przed konwersją Task->Epic dla zadań, których podzadania już istnieją
    jako Jira Sub-task - taka konwersja zostawiłaby Epica z podpiętymi
    Sub-taskami (struktura niedozwolona w Jirze). Takie przypadki naprawia
    retrofit_epics.py, który konwertuje całą hierarchię naraz.

    Czyta WYŁĄCZNIE ze stanu (bez zapytań do Asany/Jiry) - lista podzadań
    jest już pobrana przez sync_subtasks, a tu potrzebujemy tylko wiedzieć,
    czy KTÓREKOLWIEK z nich ma już klucz Jiry."""
    try:
        subtasks = get_asana_subtasks(parent_asana_gid)
    except Exception:  # noqa: BLE001
        return False  # nie udało się sprawdzić - nie blokujemy, niech normalna ścieżka zdecyduje
    for sub in subtasks:
        entry = project_state.get(sub["gid"])
        if entry and entry.get("jira_key") and not entry.get("deleted_in_jira"):
            return True
    return False


def get_epic_name_field_id() -> Optional[str]:
    """Znajduje ID pola 'Epic Name' (custom field, różni się per instancja) -
    wymagane przy tworzeniu Epika, ale Jira NIE pilnuje jego wymagalności
    automatycznie przy tworzeniu przez API (znany, udokumentowany błąd),
    więc musimy ustawić je jawnie. Buforowane, żeby nie pytać za każdym
    razem."""
    global _EPIC_NAME_FIELD_CACHE
    if _EPIC_NAME_FIELD_CACHE:
        return _EPIC_NAME_FIELD_CACHE
    resp = request_with_retry("GET", f"{JIRA_BASE_URL}/rest/api/3/field", auth=JIRA_AUTH, timeout=REQUEST_TIMEOUT)
    if resp.status_code != 200:
        return None
    for field in resp.json():
        if field.get("name", "").strip().lower() == "epic name":
            _EPIC_NAME_FIELD_CACHE = field["id"]
            return field["id"]
    return None


def ensure_issue_is_epic(issue_key: str, summary: str, dry_run: bool) -> None:
    """Konwertuje istniejące zadanie (Task) na Epic, jeśli jeszcze nim nie
    jest. Wywoływane w ścieżce UPDATE gdy zadanie jest już w stanie (było
    zmigrowane PRZED wprowadzeniem obsługi Epików), ale teraz jest w
    epic_candidates.json - musi zostać Epikiem, zanim sync_subtasks spróbuje
    tworzyć jego podzadania jako Task (bo Task nie może być rodzicem innego
    Task - tylko Epic może). Bez tego konwersja dostawałaby błąd Jiry
    "parent must be an Epic" przy tworzeniu podzadań. Reużywa tej samej
    logiki i fallbacku co create_jira_issue dla nowych Epików."""
    if dry_run:
        print(f"      [DRY-RUN] Skonwertowałbym {issue_key} na Epic (wykryto jako kandydata).")
        return
    epic_name_field = get_epic_name_field_id()
    fields: dict = {"issuetype": {"name": "Epic"}}
    if epic_name_field:
        fields[epic_name_field] = summary
    resp = request_with_retry("PUT", f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}",
                              auth=JIRA_AUTH, json={"fields": fields}, timeout=REQUEST_TIMEOUT)
    if resp.status_code >= 300 and epic_name_field and epic_name_field in resp.text:
        print(f"      [INFO] Pole 'Epic Name' nie jest skonfigurowane na ekranie edycji projektu — "
              f"konwertuję {issue_key} na Epic bez niego.")
        del fields[epic_name_field]
        resp = request_with_retry("PUT", f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}",
                                  auth=JIRA_AUTH, json={"fields": fields}, timeout=REQUEST_TIMEOUT)
    if resp.status_code >= 300:
        raise RuntimeError(
            f"Nie udało się skonwertować {issue_key} na Epic: {resp.status_code} {resp.text[:200]}"
        )
    print(f"      {issue_key}: skonwertowano na Epic (był już zmigrowany jako Task — "
          f"wykryty jako kandydat przez detect_epic_candidates.py).")


def create_jira_issue(project_key: str, summary: str, description_html: str,
                       assignee_account_id: Optional[str], due_on: Optional[str],
                       parent_key: Optional[str] = None, force_issue_type: Optional[str] = None) -> str:
    """parent_key: jeśli podany, tworzy PODZADANIE (issuetype "Sub-task"),
    powiązane z zadaniem o tym kluczu, zamiast zwykłego "Task" - CHYBA że
    force_issue_type każe inaczej (np. "Task" z parent_key ustawionym na
    Epika - nowoczesne pole "parent" łączy zarówno podzadanie z rodzicem,
    jak i zwykłe zadanie z Epikiem, tym samym mechanizmem).
    force_issue_type: wymusza konkretny typ (np. "Epic" dla zadań z
    wykrytym głębszym zagnieżdżeniem - patrz detect_epic_candidates.py),
    zamiast automatycznego wyboru Task/Sub-task."""
    summary, description_html = _safe_summary_and_description(summary, description_html)
    if force_issue_type:
        issue_type = force_issue_type
    else:
        issue_type = get_subtask_issue_type_name(project_key) if parent_key else "Task"
    url = f"{JIRA_BASE_URL}/rest/api/3/issue"
    fields = {
        "project": {"key": project_key},
        "summary": summary,
        "issuetype": {"name": issue_type},
        "description": html_to_adf(description_html),
    }
    if parent_key:
        fields["parent"] = {"key": parent_key}
    if assignee_account_id:
        fields["assignee"] = {"accountId": assignee_account_id}
    if due_on:
        fields["duedate"] = due_on
    epic_name_field = None
    if issue_type == "Epic":
        epic_name_field = get_epic_name_field_id()
        if epic_name_field:
            fields[epic_name_field] = summary
    fields.update(get_required_field_defaults(project_key, issue_type))

    resp = request_with_retry("POST", url, auth=JIRA_AUTH, json={"fields": fields}, timeout=REQUEST_TIMEOUT)

    if resp.status_code >= 300 and epic_name_field and epic_name_field in resp.text:
        # Pole "Epic Name" istnieje GLOBALNIE w tej instancji Jiry, ale nie
        # jest skonfigurowane na ekranie edycji TEGO KONKRETNEGO projektu
        # (luka w konfiguracji per-projekt, niezależna od typu projektu -
        # potwierdzone ręcznie: dotyczy projektu company-managed, z Epikiem
        # obecnym w jego schemacie typów zadań). Potwierdzony błąd: "Field
        # 'customfield_XXXXX' cannot be set. It is not on the appropriate
        # screen, or unknown." Zamiast tracić całe zadanie, ponawiamy BEZ
        # tego pola.
        print(f"      [INFO] Pole 'Epic Name' nie jest skonfigurowane na ekranie edycji projektu {project_key} — "
              f"tworzę Epika bez niego.")
        del fields[epic_name_field]
        resp = request_with_retry("POST", url, auth=JIRA_AUTH, json={"fields": fields}, timeout=REQUEST_TIMEOUT)

    if resp.status_code >= 300 and _is_adf_error(resp):
        # Nasz konwerter HTML->ADF wygenerował coś, czego Jira nie akceptuje -
        # spróbuj ponownie ZWYKŁYM tekstem (bez formatowania), zamiast tracić
        # całe zadanie z powodu samego opisu.
        print(f"      [INFO] Opis odrzucony jako nieprawidłowy ADF — ponawiam jako zwykły tekst (bez formatowania).")
        fields["description"] = text_to_adf(_strip_html_tags(description_html))
        resp = request_with_retry("POST", url, auth=JIRA_AUTH, json={"fields": fields}, timeout=REQUEST_TIMEOUT)

    if resp.status_code >= 300 and assignee_account_id and _is_assignee_error(resp):
        # Oryginalny assignee nie może być przypisany w tym projekcie (brak
        # uprawnienia "Assignable User" w schemacie uprawnień) - tworzymy zadanie
        # BEZ przypisanego, zamiast podstawiać kogoś innego.
        del fields["assignee"]
        resp = request_with_retry("POST", url, auth=JIRA_AUTH, json={"fields": fields}, timeout=REQUEST_TIMEOUT)
        if resp.status_code < 300:
            print(f"      [INFO] Assignee z Asany nie może być przypisany w '{project_key}' — zadanie bez przypisanego.")
        elif _is_assignee_error(resp):
            # Projekt nie pozwala na nieprzypisane zadania - ostatnia deska ratunku: lead projektu.
            lead_id = get_project_lead_account_id(project_key)
            if lead_id and lead_id != assignee_account_id:
                fields["assignee"] = {"accountId": lead_id}
                resp = request_with_retry("POST", url, auth=JIRA_AUTH, json={"fields": fields}, timeout=REQUEST_TIMEOUT)
                if resp.status_code < 300:
                    print(f"      [INFO] Assignee z Asany nie może być przypisany, a projekt '{project_key}' "
                          f"nie pozwala na nieprzypisane — użyto leada projektu.")

    if resp.status_code >= 300:
        raise RuntimeError(f"Błąd tworzenia zadania Jira: {resp.status_code} {resp.text}")
    return resp.json()["key"]


def update_jira_issue(issue_key: str, summary: str, description_html: str,
                       assignee_account_id: Optional[str], due_on: Optional[str]) -> None:
    summary, description_html = _safe_summary_and_description(summary, description_html)
    if os.getenv("DEBUG_SYNC"):
        print(f"      [DEBUG] Wysyłam do {issue_key} summary={summary!r} (długość={len(summary)})")
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}"
    fields = {
        "summary": summary,
        "description": html_to_adf(description_html),
    }
    if assignee_account_id:
        fields["assignee"] = {"accountId": assignee_account_id}
    if due_on:
        fields["duedate"] = due_on

    resp = request_with_retry("PUT", url, auth=JIRA_AUTH, json={"fields": fields}, timeout=REQUEST_TIMEOUT)

    if resp.status_code >= 300 and _is_adf_error(resp):
        print(f"      [INFO] Opis odrzucony jako nieprawidłowy ADF — ponawiam jako zwykły tekst (bez formatowania).")
        fields["description"] = text_to_adf(_strip_html_tags(description_html))
        resp = request_with_retry("PUT", url, auth=JIRA_AUTH, json={"fields": fields}, timeout=REQUEST_TIMEOUT)

    if resp.status_code >= 300 and assignee_account_id and _is_assignee_error(resp):
        # Osoby z Asany nie da się przypisać - obecny przypisany w Jirze zostaje bez
        # zmian (ustalenie z PM: już utworzonych/przypisanych zadań nie zmieniamy),
        # a tytuł/opis/termin i tak się zapisują.
        project_key = issue_key.split("-")[0]
        del fields["assignee"]
        resp = request_with_retry("PUT", url, auth=JIRA_AUTH, json={"fields": fields}, timeout=REQUEST_TIMEOUT)
        if resp.status_code < 300:
            print(f"      [INFO] Assignee z Asany nie może być przypisany w '{project_key}' — przypisany w {issue_key} bez zmian.")

    if resp.status_code >= 300:
        raise RuntimeError(f"Błąd aktualizacji {issue_key}: {resp.status_code} {resp.text}")


def get_current_status_id(issue_key: str) -> Optional[str]:
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}"
    resp = request_with_retry("GET", url, auth=JIRA_AUTH, params={"fields": "status"}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()["fields"]["status"]["id"]


_workflow_graph_cache: dict[str, dict[str, list]] = {}  # workflow_scheme_key -> {status_id: [(transition_id, target_status_id), ...]}


def _get_transitions_for(issue_key: str) -> list[dict]:
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/transitions"
    resp = requests.get(url, auth=JIRA_AUTH, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json().get("transitions", [])


def get_workflow_scheme_key(project_key: str) -> str:
    """Zwraca ID schematu workflow projektu (albo project_key jako fallback,
    jeśli projekt używa domyślnego schematu bez własnego ID) — używane jako
    KLUCZ CACHE'A mapy workflow. Dzięki temu projekty dzielące ten sam
    schemat (np. wszystkie utworzone z tego samego wzorca) reużywają JEDNĄ
    zbudowaną mapę, zamiast budować ją od nowa dla każdego z osobna."""
    try:
        project_id = core.get_project_id(project_key)
        scheme_id = core._get_scheme_id_via_project_association(
            "workflowscheme/project", "workflowScheme", project_id
        )
        return f"scheme:{scheme_id}" if scheme_id else f"project:{project_key}"
    except Exception:  # noqa: BLE001
        return f"project:{project_key}"


def _load_workflow_graph_cache_from_disk() -> dict:
    if WORKFLOW_GRAPH_CACHE_FILE.exists():
        try:
            return json.loads(WORKFLOW_GRAPH_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save_workflow_graph_cache_to_disk() -> None:
    WORKFLOW_GRAPH_CACHE_FILE.write_text(
        json.dumps(_workflow_graph_cache, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def build_workflow_graph(project_key: str) -> dict[str, list]:
    """Buduje PEŁNY graf przejść workflow — RAZ NA SCHEMAT WORKFLOW (nie na
    projekt!), przez tymczasowe zadania-sondy (jedno na każdy odkrywany
    status, prowadzone tam znaną już ścieżką z grafu zbudowanego do tej pory,
    potem usuwane). Wynik cache'owany w PAMIĘCI na czas przebiegu ORAZ na
    DYSKU (workflow_graph_cache.json) — więc kolejne uruchomienia skryptu w
    ogóle nie muszą tego powtarzać, a w obrębie jednego przebiegu wszystkie
    projekty dzielące ten sam wzorzec (typowy przypadek przy 550 projektach
    z jednego szablonu) reużywają JEDNEJ zbudowanej mapy zamiast budować ją
    osobno dla każdego — to był główny powód, dla którego ten krok był wolny.

    To zastępuje wcześniejsze "zgadywanie pierwszego dostępnego przejścia",
    które POPEŁNIAŁO prawdziwe przejścia w trakcie wędrówki i przy niepowodzeniu
    zostawiało zadanie na przypadkowym, pośrednim statusie zamiast się cofnąć —
    stąd wszystkie zadania lądowały w tym samym miejscu (pierwszy krok z "New")."""
    if not _workflow_graph_cache:
        _workflow_graph_cache.update(_load_workflow_graph_cache_from_disk())

    cache_key = get_workflow_scheme_key(project_key)
    if cache_key in _workflow_graph_cache:
        return _workflow_graph_cache[cache_key]

    print(f"    [Budowanie mapy workflow (schemat: {cache_key}) — jednorazowo, przez tymczasowe zadania w '{project_key}'...]")
    graph: dict[str, list] = {}
    visited: set[str] = set()

    probe_key = create_jira_issue(project_key, "[TYMCZASOWE - badanie workflow, można usunąć]", "", None, None)
    start = get_current_status_id(probe_key)
    _delete_issue(probe_key)

    queue = [start]
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)

        probe_key = create_jira_issue(project_key, "[TYMCZASOWE - badanie workflow, można usunąć]", "", None, None)
        if current != start:
            path_to_current = _find_transition_path(graph, start, current)
            if path_to_current is None:
                _delete_issue(probe_key)
                continue
            url = f"{JIRA_BASE_URL}/rest/api/3/issue/{probe_key}/transitions"
            for trans_id in path_to_current:
                request_with_retry(
                    "POST", url, auth=JIRA_AUTH, json={"transition": {"id": trans_id}}, timeout=REQUEST_TIMEOUT
                )

        transitions = _get_transitions_for(probe_key)
        graph[current] = [(t["id"], t["to"]["id"]) for t in transitions]
        _delete_issue(probe_key)

        for _trans_id, target in graph[current]:
            if target not in visited:
                queue.append(target)

    _workflow_graph_cache[cache_key] = graph
    _save_workflow_graph_cache_to_disk()
    return graph


def _delete_issue(issue_key: str) -> None:
    try:
        resp = requests.delete(f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}", auth=JIRA_AUTH, timeout=REQUEST_TIMEOUT)
        if resp.status_code >= 300:
            print(f"    [OSTRZEŻENIE] Nie udało się usunąć tymczasowego zadania {issue_key} — usuń ręcznie.")
    except Exception:  # noqa: BLE001
        print(f"    [OSTRZEŻENIE] Nie udało się usunąć tymczasowego zadania {issue_key} — usuń ręcznie.")


def _find_transition_path(graph: dict[str, list], start: str, target: str) -> Optional[list]:
    """BFS: zwraca listę ID przejść prowadzącą od start do target, albo None,
    jeśli target jest nieosiągalny w tym grafie."""
    if start == target:
        return []
    visited = {start}
    queue = [(start, [])]
    while queue:
        current, path = queue.pop(0)
        for trans_id, target_status in graph.get(current, []):
            if target_status == target:
                return path + [trans_id]
            if target_status not in visited:
                visited.add(target_status)
                queue.append((target_status, path + [trans_id]))
    return None


def transition_jira_issue_to_status(issue_key: str, status_id: str, final_fields: Optional[dict] = None) -> bool:
    """Przechodzi zadanie do docelowego statusu PRECYZYJNĄ ścieżką (BFS po
    pełnym grafie workflow, patrz build_workflow_graph) — nie zgaduje po
    drodze, więc albo trafia dokładnie tam gdzie trzeba, albo w ogóle nie
    rusza zadania (zamiast zostawiać je na przypadkowym stanie pośrednim)."""
    project_key = issue_key.split("-")[0]
    current_id = get_current_status_id(issue_key)
    if current_id == status_id:
        return True

    graph = build_workflow_graph(project_key)
    path = _find_transition_path(graph, current_id, status_id)
    if path is None:
        print(f"      [DEBUG] {issue_key}: brak ścieżki w grafie workflow z bieżącego statusu (id={current_id}) do celu (id={status_id}).")
        return False  # cel nieosiągalny z bieżącego statusu w tym workflow

    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/transitions"
    for n, trans_id in enumerate(path, 1):
        body = {"transition": {"id": trans_id}}
        if final_fields and n == len(path):
            body["fields"] = final_fields  # np. Resolution = Won't Do na przejściu do Closed
        resp = request_with_retry("POST", url, auth=JIRA_AUTH, json=body, timeout=REQUEST_TIMEOUT)
        if resp.status_code >= 300 and "fields" in body:
            # pole spoza ekranu przejścia - przejście i tak ważniejsze niż pole
            print(f"      [OSTRZEŻENIE] {issue_key}: Jira nie przyjęła pól {list(final_fields)} przy przejściu "
                  f"({resp.status_code} {resp.text[:150]}) - przechodzę bez nich.")
            resp = request_with_retry("POST", url, auth=JIRA_AUTH, json={"transition": {"id": trans_id}},
                                      timeout=REQUEST_TIMEOUT)
        if resp.status_code >= 300:
            print(f"      [DEBUG] {issue_key}: Jira odrzuciła przejście {trans_id}: {resp.status_code} {resp.text}")
            return False  # zadanie zostaje na statusie osiągniętym do tego kroku - patrz niżej
    return True


def post_jira_comment(issue_key: str, author_name: str, created_at: str, html_text: str) -> Optional[str]:
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/comment"
    # Prefiks z autorem/datą z Asany, bo Jira i tak pokaże jako autora konto
    # API (nie da się "podszyć" pod oryginalnego autora bez uprawnień admina
    # i dodatkowej konfiguracji) — więc zachowujemy tę informację w treści.
    prefix = "[Asana"
    if author_name:
        prefix += f" — {author_name}"
    if created_at:
        prefix += f", {created_at}"
    prefix += "]"

    body_adf = html_to_adf(html_text)
    prefix_paragraph = {"type": "paragraph", "content": [{"type": "text", "text": prefix, "marks": [{"type": "em"}]}]}
    body_adf["content"] = [prefix_paragraph] + (body_adf.get("content") or [])

    # Ten sam limit i ta sama logika co przy opisie zadania (JIRA_DESCRIPTION_SAFE_LEN) -
    # komentarze dzielą prawdopodobnie ten sam, wewnętrzny limit długości ADF co opisy.
    # Sprawdzamy PO konwersji na ADF (nie na surowym HTML), bo to serializacja ADF jest
    # tym, co faktycznie liczy się względem limitu Jiry.
    serialized_len = len(json.dumps(body_adf, ensure_ascii=False))
    if serialized_len > JIRA_DESCRIPTION_SAFE_LEN:
        truncation_notice = {
            "type": "paragraph",
            "content": [{"type": "text", "text": "[KOMENTARZ UCIĘTY - przekroczył limit Jiry. Pełna treść w oryginalnym zadaniu Asany.]",
                         "marks": [{"type": "strong"}]}],
        }
        # Ucinamy kolejne akapity/elementy treści, aż zmieścimy się w limicie -
        # zachowujemy prefiks [Asana - autor] i tyle kolejnych elementów, ile się zmieści.
        kept_content = [prefix_paragraph]
        for element in (body_adf.get("content") or [])[1:]:
            candidate = kept_content + [element, truncation_notice]
            candidate_adf = {**body_adf, "content": candidate}
            if len(json.dumps(candidate_adf, ensure_ascii=False)) > JIRA_DESCRIPTION_SAFE_LEN:
                break
            kept_content.append(element)
        body_adf["content"] = kept_content + [truncation_notice]
        print(f"      [INFO] Komentarz dłuższy niż limit Jiry — ucięty (było {serialized_len} znaków ADF).")

    resp = request_with_retry("POST", url, auth=JIRA_AUTH, json={"body": body_adf}, timeout=REQUEST_TIMEOUT)
    if resp.status_code >= 300 and _is_adf_error(resp, field_name="comment"):
        # Ten sam mechanizm awaryjny co przy opisach zadań: jeśli Jira odrzuca
        # naszą wersję z formatowaniem (ADF), ponawiamy jako CZYSTY TEKST bez
        # żadnego formatowania - dużo mniej podatny na strukturalne błędy
        # walidacji, kosztem utraty samego formatowania (pogrubienia, linki
        # itp. - sama TREŚĆ tekstowa zostaje zachowana).
        print(f"      [INFO] Komentarz odrzucony jako nieprawidłowy ADF — ponawiam jako zwykły tekst (bez formatowania).")
        plain_text = _strip_html_tags(html_text)
        fallback_content = [prefix_paragraph]
        if plain_text:  # pomijamy CAŁKOWICIE pusty akapit - to sam w sobie może
            # być osobnym powodem odrzucenia ADF (patrz komentarz w _is_adf_error)
            fallback_content.append({"type": "paragraph", "content": [{"type": "text", "text": plain_text}]})
        fallback_adf = {"type": "doc", "version": 1, "content": fallback_content}
        resp = request_with_retry("POST", url, auth=JIRA_AUTH, json={"body": fallback_adf}, timeout=REQUEST_TIMEOUT)

    if resp.status_code >= 300:
        raise RuntimeError(f"Błąd dodawania komentarza do {issue_key}: {resp.status_code} {resp.text}")
    try:
        return str(resp.json().get("id") or "") or None
    except ValueError:
        return None


def upload_jira_attachment(issue_key: str, filename: str, content: bytes) -> list:
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/attachments"
    headers = {"X-Atlassian-Token": "no-check"}  # wymagane przez Jirę dla uploadu załączników
    files = {"file": (filename, content)}
    resp = request_with_retry("POST", url, auth=JIRA_AUTH, headers=headers, files=files, timeout=REQUEST_TIMEOUT)
    if resp.status_code >= 300:
        raise RuntimeError(f"Błąd wgrywania załącznika '{filename}' do {issue_key}: {resp.status_code} {resp.text}")
    try:
        return [str(a["id"]) for a in resp.json() if a.get("id")]
    except (ValueError, TypeError, AttributeError):
        return []


def remember_for_reverse(task_state: dict, key: str, jira_ids) -> None:
    """Identyfikatory komentarzy/załączników, które TA synchronizacja utworzyła w Jirze,
    trafiają do list „już znane w Asanie” kierunku zwrotnego (jira_asana_sync.py) -
    inaczej wróciłyby do Asany jako duplikaty."""
    ids = [i for i in (jira_ids if isinstance(jira_ids, list) else [jira_ids]) if i]
    if ids:
        task_state[key] = sorted(set(task_state.get(key, [])) | set(ids))


def refresh_reverse_marker(jira_key: str, task_state: dict, dry_run: bool) -> None:
    """Po zapisie do Jiry: aktualny znacznik „updated” zgłoszenia jako punkt odniesienia
    kierunku zwrotnego, żeby zmiana zrobiona przez TĘ synchronizację nie wróciła do
    Asany. Tylko dla zadań, które kierunek zwrotny już zna (jest „jira_updated”) -
    reszta nie płaci dodatkowym zapytaniem."""
    if dry_run or "jira_updated" not in task_state:
        return
    try:
        resp = request_with_retry("GET", f"{JIRA_BASE_URL}/rest/api/3/issue/{jira_key}",
                                  auth=JIRA_AUTH, params={"fields": "updated"}, timeout=REQUEST_TIMEOUT)
        if resp.status_code < 300:
            task_state["jira_updated"] = (resp.json().get("fields") or {}).get("updated")
    except Exception:  # noqa: BLE001 - znacznik jest pomocniczy, brak nie psuje synchronizacji
        pass


def sync_comments(task_gid: str, jira_key: str, task_state: dict, dry_run: bool) -> int:
    """Dodaje w Jirze komentarze z Asany, których jeszcze nie ma (śledzone po
    gid komentarza w task_state['synced_comment_gids']). Zwraca liczbę nowo
    dodanych. GET jest bezpieczny nawet w dry-run — pomijamy tylko sam zapis."""
    synced = set(task_state.get("synced_comment_gids", []))
    comments = get_asana_comments(task_gid)
    added = 0
    for c in comments:
        if c["gid"] in synced:
            continue
        if dry_run:
            print(f"      [DRY-RUN] Dodałbym komentarz od {(c.get('created_by') or {}).get('name', '?')}")
        else:
            author = (c.get("created_by") or {}).get("name", "")
            jira_comment_id = post_jira_comment(jira_key, author, c.get("created_at", ""), c.get("html_text") or c.get("text", ""))
            remember_for_reverse(task_state, "synced_from_jira_comment_ids", jira_comment_id)
            synced.add(c["gid"])
        added += 1
    if not dry_run:
        task_state["synced_comment_gids"] = sorted(synced)
    return added


ASANA_LINK_GLOBAL_ID = "asana:task:{gid}"
_LINKS_ADDED = {"count": 0}


def ensure_asana_link(task_gid: str, jira_key: str, title: str, task_state: dict, dry_run: bool) -> None:
    """Link do zadania Asany w sekcji "Linki" zgłoszenia (remote link). Stały
    globalId "asana:task:<gid>" -> ponowne wywołanie aktualizuje, nie dubluje.
    Dzięki niemu powiązanie Asana <-> Jira da się odtworzyć z samej Jiry, gdyby
    stan je zgubił (relink_state.py). Zakładany raz (flaga asana_link w stanie);
    błąd nie przerywa synchronizacji - spróbuje ponownie przy następnym przebiegu."""
    if task_state.get("asana_link") or dry_run:
        return
    body = {
        "globalId": ASANA_LINK_GLOBAL_ID.format(gid=task_gid),
        "application": {"type": "com.asana", "name": "Asana"},
        "relationship": "Asana",
        "object": {"url": f"https://app.asana.com/0/0/{task_gid}/f", "title": f"Asana: {title}"[:255],
                   "icon": {"url16x16": "https://app.asana.com/favicon.ico", "title": "Asana"}},
    }
    try:
        resp = request_with_retry("POST", f"{JIRA_BASE_URL}/rest/api/3/issue/{jira_key}/remotelink",
                                  auth=JIRA_AUTH, json=body, timeout=REQUEST_TIMEOUT)
        if resp.status_code < 300:
            task_state["asana_link"] = True
            _LINKS_ADDED["count"] += 1
        else:
            record_warning(jira_key.split("-")[0], f"{jira_key}: link do Asany", f"{resp.status_code} {resp.text[:150]}")
    except Exception as exc:  # noqa: BLE001
        record_warning(jira_key.split("-")[0], f"{jira_key}: link do Asany", exc)


def sync_attachments(task_gid: str, jira_key: str, task_state: dict, dry_run: bool) -> int:
    """Wgrywa w Jirze załączniki z Asany, których jeszcze nie ma (śledzone po
    gid załącznika w task_state['synced_attachment_gids']). Zwraca liczbę
    nowo wgranych."""
    synced = set(task_state.get("synced_attachment_gids", []))
    attachments = get_asana_attachments(task_gid)
    added = 0
    for a in attachments:
        if a["gid"] in synced:
            continue
        if dry_run:
            print(f"      [DRY-RUN] Wgrałbym załącznik '{a.get('name')}'")
            added += 1
            continue
        download_url = a.get("download_url")
        if not download_url:
            continue  # np. załącznik z zewnętrznego dysku bez bezpośredniego linku - pomijamy
        try:
            file_resp = requests.get(download_url, timeout=REQUEST_TIMEOUT)
            file_resp.raise_for_status()
            created_ids = upload_jira_attachment(jira_key, a.get("name") or "attachment", file_resp.content)
            remember_for_reverse(task_state, "synced_from_jira_attachment_ids", created_ids)
            synced.add(a["gid"])
            added += 1
        except Exception as exc:  # noqa: BLE001
            print(f"      [OSTRZEŻENIE] Nie udało się przenieść załącznika '{a.get('name')}': {exc}")
            record_warning(jira_key.split("-")[0], f"{jira_key}: załącznik '{a.get('name')}'", exc)
    if not dry_run:
        task_state["synced_attachment_gids"] = sorted(synced)
    return added


# Zadania Asany, których podzadania zostały już zsynchronizowane w BIEŻĄCYM
# przebiegu projektu (czyszczone na początku sync_project). Chroni przed
# podwójnym przechodzeniem tego samego poddrzewa (rekurencja + druga wizyta
# z pętli głównej dla podzadań dodanych też do projektu) i przed pętlami.
_SUBTASKS_SYNCED_THIS_RUN: set = set()
MAX_SUBTASK_DEPTH = 20
# Status wynikający z WŁASNEJ sekcji Asany, dla zadań z listy projektu (gid -> id statusu
# albo None). Ustawiane na początku sync_project. Podzadanie z tej listy ma w Asanie własną
# sekcję, więc jego status ustawia pętla główna, a jego dzieci dziedziczą właśnie ten status.
_OWN_SECTION_STATUS: dict = {}
_OWN_SECTION_FIELDS: dict = {}  # gid -> pola przejścia (np. Resolution) z sekcji zadania


def sync_subtasks(parent_asana_gid: str, parent_jira_key: str, jira_project_key: str,
                  project_state: dict, user_map: dict, dry_run: bool, parent_is_epic: bool = False,
                  depth: int = 0, inherited_status_id: Optional[str] = None,
                  inherited_fields: Optional[dict] = None) -> dict:
    """Synchronizuje WSZYSTKIE podzadania danego zadania Asany jako Jira
    Sub-task, powiązane z parent_jira_key. Wpisy w project_state trzymane
    pod WŁASNYM gid podzadania (ten sam słownik co zwykłe zadania) - dzięki
    temu podzadania dostają dokładnie te same zabezpieczenia przed
    duplikatami (idempotentność, śledzenie komentarzy/załączników), co
    reszta synchronizacji.

    parent_is_epic: gdy True, rodzic (parent_jira_key) jest Epikiem
    (patrz detect_epic_candidates.py) - jego bezpośrednie dzieci muszą być
    zwykłym "Task" (Jira nie pozwala podpiąć Sub-task bezpośrednio pod
    Epika), powiązanym przez to samo pole "parent". CELOWO nie oznaczamy
    takich wpisów jako "is_subtask" - to normalny Task, w pełni zdolny
    mieć WŁASNE podzadania (poziom 3 w Asanie) - tworzy je rekurencja niżej.

    Status podzadania ustalany WYŁĄCZNIE przez checkbox "ukończone" w
    Asanie (Fertig/domyślny) - podzadania nie mają własnej przynależności
    do sekcji/kolumny tablicy, więc mapowanie po sekcji (jak przy zadaniach
    głównych) tu nie ma zastosowania.

    REKURENCJA: po każdym podzadaniu schodzi do JEGO podzadań (dowolnie
    głęboko). Wcześniej głębsze poziomy powstawały tylko wtedy, gdy pośrednie
    podzadanie było dodane też do listy projektu - pozostałe poddrzewa od
    poziomu 2 w dół były pomijane bez śladu. Rodzic w Jirze dla dzieci:
      - podzadanie jest Taskiem (dziecko Epika) -> dzieci pod nim (Sub-task),
      - podzadanie jest Sub-taskiem -> dzieci pod jego rodzicem w Jirze
        (spłaszczenie - Jira nie zagnieżdża Sub-tasków).

    STATUS podzadań (Asana nie przypisuje podzadań do sekcji):
      - ukończone w Asanie -> Fertig,
      - otwarte -> status zadania najwyższego poziomu (inherited_status_id, wynikający
        z jego sekcji; pod ukończonym zadaniem -> Fertig), na każdym poziomie,
      - podzadanie dodane też do listy projektu ma własną sekcję - jego status
        ustawia pętla główna, a jego dzieci dziedziczą status z tej sekcji.
    Ostatnio nadany status jest pamiętany w stanie (status_applied), więc zgłoszenie
    jest przestawiane tylko przy zmianie - bez dodatkowych zapytań przy każdym przebiegu."""
    if parent_asana_gid in _SUBTASKS_SYNCED_THIS_RUN or depth > MAX_SUBTASK_DEPTH:
        return {"created": 0, "created_epics": 0, "created_tasks": 0, "created_subtasks": 0, "updated": 0,
                "updated_epics": 0, "updated_tasks": 0, "updated_subtasks": 0, "comments": 0, "attachments": 0, "errors": 0,
                "status_ok": 0, "status_failed": 0}
    _SUBTASKS_SYNCED_THIS_RUN.add(parent_asana_gid)
    stats = {"created": 0, "created_epics": 0, "created_tasks": 0, "created_subtasks": 0, "updated": 0, "updated_epics": 0, "updated_tasks": 0, "updated_subtasks": 0, "comments": 0, "attachments": 0, "errors": 0,
             "status_ok": 0, "status_failed": 0}
    fertig_id = resolve_target_status_id(jira_project_key, None, {}, set(), completed=True)
    try:
        subtasks = get_asana_subtasks(parent_asana_gid)
    except Exception as exc:  # noqa: BLE001
        print(f"      [OSTRZEŻENIE] Nie udało się pobrać podzadań dla {parent_jira_key}: {exc}")
        record_warning(jira_project_key, f"{parent_jira_key}: pobieranie podzadań z Asany", exc)
        return stats

    for sub in subtasks:
        sub_gid = sub["gid"]
        sub_name = sub.get("name") or "(bez tytułu)"
        sub_notes = sub.get("html_notes") or ""
        sub_due = sub.get("due_on")
        sub_modified = sub.get("modified_at")
        sub_completed = sub.get("completed", False)

        sub_assignee = sub.get("assignee") or {}
        sub_asana_user_gid = sub_assignee.get("gid")
        sub_jira_account_id = user_map.get(sub_asana_user_gid) if sub_asana_user_gid else None

        existing = project_state.get(sub_gid)
        if is_jira_master(sub_name, existing):
            # prowadzone w Jirze - bez tworzenia, aktualizacji i schodzenia w poddrzewo
            _SUBTASKS_SYNCED_THIS_RUN.add(sub_gid)
            stats["skipped_jira_master"] = stats.get("skipped_jira_master", 0) + 1
            continue

        if existing and existing.get("deleted_in_jira"):
            stats["errors"] += 0  # celowo NIE liczone jako błąd - to świadome pominięcie
            continue

        sub_key = None
        try:
            if existing:
                sub_key = existing["jira_key"]
                if not parent_is_epic:
                    existing.setdefault("is_subtask", True)  # dogania stare wpisy sprzed tej poprawki
                    existing.setdefault("top_level_ancestor_key", parent_jira_key)  # jw., dla spłaszczania głębszego zagnieżdżenia
                if existing.get("asana_modified_at") != sub_modified:
                    if dry_run:
                        print(f"        [DRY-RUN] Zaktualizowałbym podzadanie {sub_key}: '{sub_name}'")
                    else:
                        update_jira_issue(sub_key, sub_name, sub_notes, sub_jira_account_id, sub_due)
                        project_state[sub_gid]["asana_modified_at"] = sub_modified
                    stats["updated"] += 1
                    stats["updated_tasks" if parent_is_epic else "updated_subtasks"] += 1
            else:
                if dry_run:
                    print(f"        [DRY-RUN] Utworzyłbym podzadanie w {parent_jira_key}: '{sub_name}'")
                else:
                    force_type = "Task" if parent_is_epic else None
                    sub_key = create_jira_issue(jira_project_key, sub_name, sub_notes, sub_jira_account_id, sub_due, parent_key=parent_jira_key, force_issue_type=force_type)
                    if parent_is_epic:
                        project_state[sub_gid] = {"jira_key": sub_key, "asana_modified_at": sub_modified}
                    else:
                        project_state[sub_gid] = {
                            "jira_key": sub_key, "asana_modified_at": sub_modified, "is_subtask": True,
                            "top_level_ancestor_key": parent_jira_key,
                        }
                    print(f"        Utworzono {'zadanie (pod Epikiem)' if parent_is_epic else 'podzadanie'} {sub_key}: '{sub_name}' (rodzic: {parent_jira_key})")
                stats["created"] += 1
                stats["created_tasks" if parent_is_epic else "created_subtasks"] += 1

            if sub_key:
                sub_task_state = project_state.setdefault(sub_gid, {"jira_key": sub_key, "asana_modified_at": sub_modified})
                stats["comments"] += sync_comments(sub_gid, sub_key, sub_task_state, dry_run)
                stats["attachments"] += sync_attachments(sub_gid, sub_key, sub_task_state, dry_run)
                ensure_asana_link(sub_gid, sub_key, sub_name, sub_task_state, dry_run)
                refresh_reverse_marker(sub_key, sub_task_state, dry_run)
                if sub_gid not in _OWN_SECTION_STATUS:  # z własną sekcją -> ustawia pętla główna
                    target = fertig_id if sub_completed else inherited_status_id
                    if target and not dry_run and sub_task_state.get("status_applied") != target:
                        if transition_jira_issue_to_status(sub_key, target, None if sub_completed else inherited_fields):
                            sub_task_state["status_applied"] = target
                            stats["status_ok"] += 1
                        else:
                            stats["status_failed"] += 1
                            record_warning(jira_project_key, f"{sub_key} '{sub_name}'",
                                           "nie udało się ustawić statusu odziedziczonego po zadaniu głównym")
        except Exception as exc:  # noqa: BLE001
            print(f"        [BŁĄD] podzadanie '{sub_name}': {exc}")
            record_error(jira_project_key, f"podzadanie '{sub_name}' (rodzic {parent_jira_key})", exc)
            stats["errors"] += 1

        # Zejście poziom niżej (patrz REKURENCJA w docstringu). Bez sub_key
        # (dry-run nowego zadania, błąd tworzenia) nie ma pod czym podpiąć dzieci.
        if sub_key:
            entry = project_state.get(sub_gid, {})
            sub_is_subtask = entry.get("is_subtask", not parent_is_epic)
            child_parent = (entry.get("top_level_ancestor_key") or parent_jira_key) if sub_is_subtask else sub_key
            if sub_gid in _OWN_SECTION_STATUS:
                child_status = _OWN_SECTION_STATUS[sub_gid]
                child_fields = _OWN_SECTION_FIELDS.get(sub_gid)
            else:
                child_status = fertig_id if sub_completed else inherited_status_id
                child_fields = None if sub_completed else inherited_fields
            deeper = sync_subtasks(sub_gid, child_parent, jira_project_key, project_state, user_map,
                                   dry_run, parent_is_epic=False, depth=depth + 1, inherited_status_id=child_status,
                                   inherited_fields=child_fields)
            for k, v in deeper.items():
                if isinstance(v, int):
                    stats[k] = stats.get(k, 0) + v

    return stats


def section_rule(project_key: str, section_name: Optional[str], section_status_map: dict) -> dict:
    """Wpis mapy dla sekcji w postaci jednolitej. Wartość w mapie może być:
      - "Status"                 - jak dotąd,
      - ["Status1", "Status2"]   - pierwszy dla Asana->Jira, wszystkie dla kierunku zwrotnego,
      - {"status": "Status",     - rozszerzony wpis (mapy projektów, np. projekty "Discovery"):
         "labels": ["discovery-ready"],  etykiety zależne od sekcji (zdejmowane po wyjściu z sekcji)
         "components": ["Lexware"],      komponenty zależne od sekcji (tworzone w projekcie, jeśli brak)
         "resolution": "Won't Do",       Rozwiązanie ustawiane przy przejściu do statusu
         "nad_ukonczonym": true,         sekcja ma pierwszeństwo przed odhaczeniem w Asanie
         "pomin": true}                  zadania z tej sekcji (z poddrzewem) NIE są migrowane
    """
    overrides = project_section_overrides(project_key)
    raw = None
    if section_name:
        raw = overrides[section_name] if section_name in overrides else section_status_map.get(section_name)
    rule = {"status": None, "labels": [], "components": [], "resolution": None, "over_completed": False, "skip": False}
    if isinstance(raw, dict):
        rule.update(status=raw.get("status"), labels=list(raw.get("labels") or []),
                    components=list(raw.get("components") or []),
                    resolution=raw.get("resolution"), over_completed=bool(raw.get("nad_ukonczonym")),
                    skip=bool(raw.get("pomin")))
    elif isinstance(raw, list):
        rule["status"] = raw[0] if raw else None
    elif raw:
        rule["status"] = raw
    return rule


def managed_section_labels(project_key: str, section_status_map: dict, kind: str = "labels") -> set:
    """Etykiety (albo komponenty, kind="components"), którymi zarządza mapa sekcji projektu -
    tylko te synchronizacja dodaje i zdejmuje."""
    values = set()
    for raw in list(project_section_overrides(project_key).values()) + list(section_status_map.values()):
        if isinstance(raw, dict):
            values.update(raw.get(kind) or [])
    return values


_PROJECT_COMPONENTS: dict = {}


def ensure_components(project_key: str, names: set) -> None:
    """Tworzy w projekcie brakujące komponenty o podanych nazwach (raz na przebieg)."""
    if project_key not in _PROJECT_COMPONENTS:
        r = request_with_retry("GET", f"{JIRA_BASE_URL}/rest/api/3/project/{project_key}/components",
                               auth=JIRA_AUTH, timeout=REQUEST_TIMEOUT)
        _PROJECT_COMPONENTS[project_key] = {c["name"] for c in (r.json() if r is not None and r.status_code == 200 else [])}
    for name in sorted(names - _PROJECT_COMPONENTS[project_key]):
        r = request_with_retry("POST", f"{JIRA_BASE_URL}/rest/api/3/component", auth=JIRA_AUTH,
                               json={"project": project_key, "name": name}, timeout=REQUEST_TIMEOUT)
        if r is not None and r.status_code < 300:
            _PROJECT_COMPONENTS[project_key].add(name)
            print(f"    Utworzono komponent '{name}' w {project_key}.")
        else:
            print(f"    [OSTRZEŻENIE] Nie udało się utworzyć komponentu '{name}' w {project_key}: "
                  f"{getattr(r, 'status_code', '?')} {getattr(r, 'text', '')[:120]}")


def section_final_fields(project_key: str, section_name: Optional[str], section_status_map: dict,
                         completed: bool = False) -> Optional[dict]:
    """Pola do ustawienia na ostatnim kroku przejścia (np. Resolution = Won't Do)."""
    rule = section_rule(project_key, section_name, section_status_map)
    if completed and not rule["over_completed"]:
        return None
    return {"resolution": {"name": rule["resolution"]}} if rule["resolution"] else None


def apply_section_labels(issue_key: str, project_key: str, section_name: Optional[str],
                         section_status_map: dict, completed: bool = False) -> None:
    """Dodaje etykiety przypisane do sekcji i zdejmuje etykiety innych sekcji (tylko zarządzane
    przez mapę - pozostałe etykiety zgłoszenia zostają). Nic nie robi, gdy mapa nie ma etykiet."""
    managed = managed_section_labels(project_key, section_status_map)
    managed_comp = managed_section_labels(project_key, section_status_map, "components")
    if not managed and not managed_comp:
        return
    rule = section_rule(project_key, section_name, section_status_map)
    active = not completed or rule["over_completed"]
    wanted = set(rule["labels"]) if active else set()
    wanted_comp = set(rule["components"]) if active else set()
    if wanted_comp:
        ensure_components(project_key, wanted_comp)
    r = request_with_retry("GET", f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}", auth=JIRA_AUTH,
                           params={"fields": "labels,components"}, timeout=REQUEST_TIMEOUT)
    if r is None or r.status_code != 200:
        return
    fields = r.json()["fields"]
    update = {}
    current = set(fields.get("labels") or [])
    new = (current - managed) | wanted
    if managed and new != current:
        update["labels"] = sorted(new)
    current_comp = {c["name"] for c in fields.get("components") or []}
    new_comp = (current_comp - managed_comp) | (wanted_comp & _PROJECT_COMPONENTS.get(project_key, wanted_comp))
    if managed_comp and new_comp != current_comp:
        update["components"] = [{"name": n} for n in sorted(new_comp)]
    if update:
        request_with_retry("PUT", f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}", auth=JIRA_AUTH,
                           params={"notifyUsers": "false"}, json={"fields": update}, timeout=REQUEST_TIMEOUT)


def resolve_target_status_id(project_key: str, section_name: Optional[str],
                              section_status_map: dict, unmapped_sections: set,
                              completed: bool = False) -> Optional[str]:
    """Zamienia nazwę sekcji Asany na ID statusu Jira, przez tabelę
    section_status_map (NIE przez bezpośrednie dopasowanie nazwy sekcji do
    nazwy statusu — te zwykle się nie pokrywają, sekcje bywają etapami/
    kamieniami milowymi). Brak wpisu w tabeli = zadanie zostaje przy
    domyślnym statusie startowym (zwraca None), a sekcja trafia do
    unmapped_sections, żeby na końcu pokazać, czego jeszcze brakuje w mapie.

    Wartość w section_status_map może być JEDNYM statusem (string) ALBO
    LISTĄ statusów - jeśli lista, w kierunku Asana->Jira bierzemy PIERWSZY
    (kierunek zwrotny w jira_asana_sync.py używa WSZYSTKICH jako kandydatów).

    completed=True (checkbox "ukończone" w Asanie) ZAWSZE nadpisuje mapowanie
    sekcji, wymuszając status "Fertig" — niezależnie od tego, w jakiej
    sekcji/kolumnie zadanie fizycznie leży. Bez tego zadania odhaczone jako
    zrobione, ale nigdy nie przeciągnięte do kolumny "Done" (częsty przypadek
    w praktyce), lądowały w Jirze jako aktywne, pod jakimkolwiek statusem
    odpowiadał ich starej sekcji (np. Misc, New In)."""
    overrides = project_section_overrides(project_key)
    if completed and section_rule(project_key, section_name, section_status_map)["over_completed"]:
        completed = False  # np. "Verworfen": odrzucone zadanie, nawet odhaczone, ma być Closed, nie Done
    if completed:
        done_name = overrides.get(COMPLETED_KEY) or section_status_map.get(COMPLETED_KEY) or "Fertig"
        status_id = get_status_id_by_name(project_key, done_name)
        if status_id:
            return status_id
        print(
            f"    [OSTRZEŻENIE] Zadanie ukończone w Asanie, ale status '{done_name}' nie istnieje "
            f"w projekcie {project_key} - używam mapowania sekcji jako zapasowego."
        )
        # Brak statusu "Fertig" w tym projekcie - spadamy do normalnej
        # logiki opartej o sekcję, zamiast zwracać błąd.

    if not section_name:
        return None
    raw_value = overrides[section_name] if section_name in overrides else section_status_map.get(section_name)
    if not raw_value:
        unmapped_sections.add(section_name)
        return None
    if section_rule(project_key, section_name, section_status_map)["skip"]:
        return None
    target_status_name = section_rule(project_key, section_name, section_status_map)["status"]
    if not target_status_name:
        unmapped_sections.add(section_name)
        return None
    status_id = get_status_id_by_name(project_key, target_status_name)
    if not status_id:
        print(
            f"    [OSTRZEŻENIE] Sekcja '{section_name}' mapowana na status "
            f"'{target_status_name}', ale taki status nie istnieje w projekcie {project_key}."
        )
    return status_id


# --------------------------------------------------------------------------- #
# Główna logika synchronizacji jednego projektu
# --------------------------------------------------------------------------- #

def sync_project(asana_project_name: str, jira_key: str, state: dict,
                  user_map: dict, section_status_map: dict, dry_run: bool,
                  asana_project_link: str = "") -> dict:
    project_state = state.setdefault(jira_key, {})
    epic_candidate_gids = set(load_epic_candidates().get(jira_key, []))
    _SUBTASKS_SYNCED_THIS_RUN.clear()

    asana_gid = core.extract_asana_project_gid(asana_project_link) if asana_project_link else None
    if not asana_gid:
        record_warning(jira_key, asana_project_name, "brak asana_project_link w arkuszu - projekt pominięty")
        print(f"  [OSTRZEŻENIE] Brak asana_project_link dla '{jira_key}' ('{asana_project_name}') — "
              f"pomijam (dopasowanie po nazwie zostało celowo wyłączone, bo bywa niejednoznaczne "
              f"przy zduplikowanych nazwach projektów w Asanie). Uzupełnij link w arkuszu.")
        return {"created": 0, "updated": 0, "skipped": 0, "errors": 0, "comments": 0, "attachments": 0, "status_ok": 0, "status_failed": 0, "unmapped_sections": set()}

    tasks, nested_in_project = order_parents_first(get_asana_tasks(asana_gid))
    print(f"  Zadań w Asanie: {len(tasks)}"
          + (f" (w tym {len(nested_in_project)} podzadań dodanych też do projektu)" if nested_in_project else ""))

    stats = {"created": 0, "created_epics": 0, "created_tasks": 0, "created_subtasks": 0, "updated": 0, "updated_epics": 0, "updated_tasks": 0, "updated_subtasks": 0, "skipped": 0, "skipped_unchanged": 0, "skipped_deleted": 0, "skipped_epic_retrofit": 0, "skipped_nested": 0, "skipped_jira_master": 0, "errors": 0, "comments": 0, "attachments": 0, "status_ok": 0, "status_failed": 0}
    unmapped_sections: set = set()
    jira_master_gids: set = set()
    _OWN_SECTION_STATUS.clear()
    _OWN_SECTION_FIELDS.clear()
    for t in tasks:
        _sec = get_task_section_name(t, asana_gid)
        _OWN_SECTION_STATUS[t["gid"]] = resolve_target_status_id(
            jira_key, _sec, section_status_map, unmapped_sections, completed=t.get("completed", False))
        _OWN_SECTION_FIELDS[t["gid"]] = section_final_fields(jira_key, _sec, section_status_map,
                                                             t.get("completed", False))

    for task_no, task in enumerate(tasks, 1):
        if task_no % 100 == 0:
            # postęp: większość pracy przy istniejących zadaniach (linki, statusy, podzadania) nie wypisuje nic
            print(f"    ...przetworzono {task_no}/{len(tasks)} zadań", flush=True)
        gid = task["gid"]
        name = task.get("name") or "(bez tytułu)"
        notes = task.get("html_notes") or ""
        due_on = task.get("due_on")
        modified_at = task.get("modified_at")
        if os.getenv("DEBUG_SYNC"):
            print(f"      [DEBUG] Z Asany (gid={gid}): name={name!r}")
            print(f"      [DEBUG] memberships surowe: {task.get('memberships')!r}")
            print(f"      [DEBUG] porównuję z asana_gid={asana_gid!r}")

        assignee = task.get("assignee") or {}
        asana_user_gid = assignee.get("gid")
        jira_account_id = user_map.get(asana_user_gid) if asana_user_gid else None

        existing = project_state.get(gid)

        parent_gid = (task.get("parent") or {}).get("gid")
        if parent_gid not in jira_master_gids and section_rule(
                jira_key, get_task_section_name(task, asana_gid), section_status_map)["skip"] and not existing:
            jira_master_gids.add(gid)          # poddrzewo też pomijane
            _SUBTASKS_SYNCED_THIS_RUN.add(gid)
            stats["skipped"] += 1
            stats["skipped_section"] = stats.get("skipped_section", 0) + 1
            continue
        if is_jira_master(name, existing) or parent_gid in jira_master_gids:
            jira_master_gids.add(gid)
            _SUBTASKS_SYNCED_THIS_RUN.add(gid)  # bez schodzenia w poddrzewo
            stats["skipped"] += 1
            stats["skipped_jira_master"] += 1
            continue

        if existing and existing.get("deleted_in_jira"):
            # Zadanie zostało RĘCZNIE usunięte w Jirze (oznaczone przez
            # mark_deleted_as_skipped.py) - traktujemy to jako świadomą,
            # celową decyzję. Pomijamy CAŁKOWICIE: bez próby aktualizacji
            # (dawało błąd 404 w kółko), bez ponownego tworzenia.
            stats["skipped"] += 1
            stats["skipped_deleted"] += 1
            continue

        try:
            if existing:
                target_key = existing["jira_key"]
                fields_changed = existing.get("asana_modified_at") != modified_at
                if fields_changed:
                    if dry_run:
                        print(f"    [DRY-RUN] Zaktualizowałbym {target_key}: '{name}'")
                    else:
                        try:
                            update_jira_issue(target_key, name, notes, jira_account_id, due_on)
                            project_state[gid]["asana_modified_at"] = modified_at
                        except Exception as upd_exc:
                            if "404" in str(upd_exc):
                                # Zadanie usunięte z Jiry - oznaczamy automatycznie, żeby
                                # nie wracało przy kolejnych przebiegach (ani jako zwykłe
                                # zadanie, ani jako Epic-kandydat). Bez tej flagi skrypt
                                # próbowałby aktualizacji / konwersji na Epic w każdym
                                # kolejnym przebiegu i za każdym razem dostawał 404.
                                project_state[gid]["deleted_in_jira"] = True
                                print(f"    [INFO] {target_key} ('{name}'): usunięte z Jiry — "
                                      f"oznaczam jako deleted_in_jira, nie będzie odtwarzane.")
                                stats["skipped"] += 1
                                stats["skipped_deleted"] += 1
                                continue
                            raise
                    stats["updated"] += 1
                    if existing.get("is_epic"):
                        stats["updated_epics"] += 1
                    elif existing.get("is_subtask"):
                        stats["updated_subtasks"] += 1
                    else:
                        stats["updated_tasks"] += 1
                else:
                    stats["skipped"] += 1
                    stats["skipped_unchanged"] += 1
                    if gid in nested_in_project:
                        # podzadanie widoczne też na liście projektu - zwykle obsłużone
                        # chwilę wcześniej razem z rodzicem (sync_subtasks)
                        stats["skipped_nested"] += 1

                # Status - ZAWSZE sprawdzany, niezależnie od tego, czy modified_at
                # się zmienił: przeniesienie zadania między sekcjami w Asanie nie
                # zawsze aktualizuje ten znacznik, więc poleganie wyłącznie na nim
                # gubiło zmiany statusu (to był dokładnie zgłoszony problem).
                if not dry_run:
                    section_name = get_task_section_name(task, asana_gid)
                    status_id = resolve_target_status_id(jira_key, section_name, section_status_map, unmapped_sections, completed=task.get("completed", False))
                    if os.getenv("DEBUG_SYNC"):
                        current_status_now = get_current_status_id(target_key)
                        print(
                            f"      [DEBUG] {target_key}: sekcja='{section_name}' -> "
                            f"docelowy status_id={status_id!r}, obecny status_id={current_status_now!r}"
                        )
                    if status_id:
                        moved = transition_jira_issue_to_status(
                            target_key, status_id,
                            section_final_fields(jira_key, section_name, section_status_map, task.get("completed", False)))
                        apply_section_labels(target_key, jira_key, section_name, section_status_map, task.get("completed", False))
                        if not moved:
                            print(
                                f"      [OSTRZEŻENIE] {target_key}: nie udało się przejść do statusu "
                                f"odpowiadającego sekcji '{section_name}' (cel nieosiągalny z bieżącego "
                                f"statusu w tym workflow, albo Jira odrzuciła przejście - np. walidator "
                                f"pola wymagany przy tym przejściu)."
                            )
                            record_warning(jira_key, f"{target_key} '{name}'",
                                           f"nie udało się ustawić statusu dla sekcji '{section_name}'")
                            stats["status_failed"] += 1
                        else:
                            stats["status_ok"] += 1
            else:
                # Nowe zadanie
                if dry_run:
                    print(f"    [DRY-RUN] Utworzyłbym w {jira_key}: '{name}'")
                    target_key = None  # nic nie utworzone naprawdę - komentarzy/załączników nie sprawdzamy niżej
                else:
                    force_type = "Epic" if gid in epic_candidate_gids else None
                    target_key = create_jira_issue(jira_key, name, notes, jira_account_id, due_on, force_issue_type=force_type)
                    project_state.setdefault(gid, {})["is_epic"] = (force_type == "Epic")  # pamiętamy dla kolejnych przebiegów
                    section_name = get_task_section_name(task, asana_gid)
                    status_id = resolve_target_status_id(jira_key, section_name, section_status_map, unmapped_sections, completed=task.get("completed", False))
                    if status_id:
                        moved = transition_jira_issue_to_status(
                            target_key, status_id,
                            section_final_fields(jira_key, section_name, section_status_map, task.get("completed", False)))
                        apply_section_labels(target_key, jira_key, section_name, section_status_map, task.get("completed", False))
                        if not moved:
                            print(
                                f"      [OSTRZEŻENIE] {target_key}: nie udało się przejść do statusu "
                                f"odpowiadającego sekcji '{section_name}' (cel nieosiągalny z bieżącego "
                                f"statusu w tym workflow, albo Jira odrzuciła przejście)."
                            )
                            record_warning(jira_key, f"{target_key} '{name}'",
                                           f"nie udało się ustawić statusu dla sekcji '{section_name}'")
                            stats["status_failed"] += 1
                        else:
                            stats["status_ok"] += 1
                    # is_epic zachowane w nowym wpisie - wcześniej było tu nadpisywane, przez co
                    # ten sam przebieg niepotrzebnie próbował ponownie konwertować Epika.
                    project_state[gid] = {"jira_key": target_key, "asana_modified_at": modified_at,
                                          "is_epic": force_type == "Epic"}
                    print(f"    Utworzono {target_key}: '{name}'")
                stats["created"] += 1
                stats["created_epics" if gid in epic_candidate_gids else "created_tasks"] += 1

            # Komentarze i załączniki - ZAWSZE sprawdzane (niezależnie od tego,
            # czy pola zadania się zmieniły), bo nowy komentarz/załącznik nie
            # zawsze zmienia modified_at samego zadania w sposób, na który
            # można polegać.
            if target_key:
                task_state = project_state.setdefault(gid, {"jira_key": target_key, "asana_modified_at": modified_at})
                added_comments = sync_comments(gid, target_key, task_state, dry_run)
                added_attachments = sync_attachments(gid, target_key, task_state, dry_run)
                ensure_asana_link(gid, target_key, name, task_state, dry_run)
                refresh_reverse_marker(target_key, task_state, dry_run)
                stats["comments"] += added_comments
                stats["attachments"] += added_attachments

                # Podzadania - ZAWSZE sprawdzane, tak samo jak komentarze/
                # załączniki (nowe/zmienione podzadanie nie zawsze zmienia
                # modified_at samego zadania nadrzędnego).
                # SPŁASZCZANIE: jeśli TO zadanie samo jest podzadaniem
                # (oznaczone "is_subtask"), Jira nie pozwoli przypiąć JEGO
                # własnych podzadań pod NIM (nie obsługuje zagnieżdżania
                # podzadań w podzadaniach - potwierdzony ręcznie błąd:
                # "Parent ... can not be sub-task"). Zamiast całkowicie
                # pomijać te głębiej zagnieżdżone zadania (utrata danych),
                # przypinamy je pod PRAWDZIWYM przodkiem najwyższego poziomu
                # (zapisanym przy tworzeniu TEGO podzadania) - traci się
                # informację "to było zagnieżdżone głębiej", ale treść
                # (tytuł, opis, komentarze, załączniki) przetrwa jako
                # prawdziwe, śledzalne zadanie w Jirze.
                if task_state.get("is_subtask"):
                    flatten_parent_key = task_state.get("top_level_ancestor_key", target_key)
                    # Normalna sytuacja przy głębszym zagnieżdżeniu w Asanie - tylko licznik w podsumowaniu
                    # (wcześniej linia przy każdym przebiegu dla każdego takiego zadania zasłaniała ważne komunikaty).
                    stats["flattened_parents"] = stats.get("flattened_parents", 0) + 1
                    sub_stats = sync_subtasks(gid, flatten_parent_key, jira_key, project_state, user_map, dry_run,
                                              inherited_status_id=_OWN_SECTION_STATUS.get(gid),
                                              inherited_fields=_OWN_SECTION_FIELDS.get(gid))
                    stats["created"] += sub_stats["created"]
                    stats["created_tasks"] += sub_stats["created_tasks"]
                    stats["created_subtasks"] += sub_stats["created_subtasks"]
                    stats["updated"] += sub_stats["updated"]
                    stats["updated_tasks"] += sub_stats["updated_tasks"]
                    stats["updated_subtasks"] += sub_stats["updated_subtasks"]
                    stats["status_ok"] += sub_stats.get("status_ok", 0)
                    stats["status_failed"] += sub_stats.get("status_failed", 0)
                    stats["skipped_jira_master"] += sub_stats.get("skipped_jira_master", 0)
                    stats["comments"] += sub_stats["comments"]
                    stats["attachments"] += sub_stats["attachments"]
                    stats["errors"] += sub_stats["errors"]
                else:
                    # Jeśli zadanie jest kandydatem na Epic (ma podzadania podzadań)
                    # ale nie jest jeszcze oznaczone jako Epic w stanie - zostało
                    # zmigrowane PRZED wprowadzeniem obsługi Epików (np. taski już
                    # zmigrowane, subtaski jeszcze nie). Konwertujemy je teraz, żeby
                    # sync_subtasks mógł tworzyć jego dzieci jako Task (nie Sub-task)
                    # - bez tej konwersji Jira odrzuciłaby tworzenie Task z rodzicem
                    # będącym innym Taskiem (tylko Epic może być rodzicem Taska).
                    is_epic_candidate = gid in epic_candidate_gids
                    if is_epic_candidate and not task_state.get("is_epic"):
                        if has_already_migrated_subtasks(gid, project_state):
                            # Podzadania tego zadania SĄ JUŻ zmigrowane jako Jira
                            # Sub-task. Konwersja rodzica na Epic zostawiłaby je
                            # podpięte pod Epikiem - strukturę, której Jira nie
                            # dopuszcza (Sub-task nie może wisieć bezpośrednio pod
                            # Epikiem). Sama zmiana typu zwykle zostaje odrzucona,
                            # a w gorszym razie przechodzi i osierocałaby podzadania.
                            # Taki przypadek naprawia retrofit_epics.py, który
                            # konwertuje CAŁĄ hierarchię naraz (Task->Epic,
                            # Sub-task->Task, przepięcie wnuków), łącznie z Bulk Move
                            # dla legacy typów podzadań.
                            print(f"    [POMINIĘTO] '{name}' ({target_key}) jest kandydatem na Epic, "
                                  f"ale ma JUŻ ZMIGROWANE podzadania — nie konwertuję, żeby nie "
                                  f"zostawić Epika z podpiętymi Sub-taskami. Uruchom dla tego "
                                  f"projektu: python retrofit_epics.py --project-keys {jira_key}")
                            stats["skipped"] += 1
                            stats["skipped_epic_retrofit"] += 1
                            is_epic_candidate = False  # dalej traktuj jak zwykłe zadanie
                        else:
                            try:
                                ensure_issue_is_epic(target_key, name, dry_run)
                                task_state["is_epic"] = True
                            except Exception as epic_exc:
                                if "404" in str(epic_exc):
                                    project_state[gid]["deleted_in_jira"] = True
                                    print(f"    [INFO] {target_key} ('{name}'): usunięte z Jiry "
                                          f"(wykryte przy próbie konwersji na Epic) — oznaczam jako "
                                          f"deleted_in_jira, nie będzie odtwarzane.")
                                    stats["skipped"] += 1
                                    stats["skipped_deleted"] += 1
                                    continue
                                raise
                    # Epik to kandydat z epic_candidates.json ALBO zgłoszenie, które JUŻ jest
                    # Epikiem w Jirze (flaga is_epic, odświeżana przez refresh_state_flags.py).
                    # Bez drugiego warunku nowe podzadanie Epika spoza listy kandydatów
                    # powstawałoby jako Sub-task bezpośrednio pod Epikiem - Jira to odrzuca.
                    parent_is_epic = is_epic_candidate or bool(task_state.get("is_epic"))
                    sub_stats = sync_subtasks(gid, target_key, jira_key, project_state, user_map, dry_run, parent_is_epic=parent_is_epic,
                                              inherited_status_id=_OWN_SECTION_STATUS.get(gid),
                                              inherited_fields=_OWN_SECTION_FIELDS.get(gid))
                    stats["created"] += sub_stats["created"]
                    stats["created_tasks"] += sub_stats["created_tasks"]
                    stats["created_subtasks"] += sub_stats["created_subtasks"]
                    stats["updated"] += sub_stats["updated"]
                    stats["updated_tasks"] += sub_stats["updated_tasks"]
                    stats["updated_subtasks"] += sub_stats["updated_subtasks"]
                    stats["status_ok"] += sub_stats.get("status_ok", 0)
                    stats["status_failed"] += sub_stats.get("status_failed", 0)
                    stats["skipped_jira_master"] += sub_stats.get("skipped_jira_master", 0)
                    stats["comments"] += sub_stats["comments"]
                    stats["attachments"] += sub_stats["attachments"]
                    stats["errors"] += sub_stats["errors"]

        except Exception as exc:  # noqa: BLE001
            print(f"    [BŁĄD] '{name}': {exc}")
            record_error(jira_key, f"zadanie '{name}'", exc)
            stats["errors"] += 1

        if not dry_run:
            # ZAPISZ STAN PO KAŻDYM ZADANIU, nie dopiero po całym projekcie —
            # jeśli internet padnie w połowie (zdarzyło się!), postęp sprzed
            # awarii zostaje zachowany, więc restart NIE tworzy duplikatów
            # zadań, które już powstały w Jirze przed przerwaniem.
            save_state(state)

        time.sleep(0.3)  # ostrożne tempo względem limitów API obu systemów

    stats["unmapped_sections"] = unmapped_sections
    return stats


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

SYNC_EXCLUDE_FILE = Path(__file__).resolve().parent / "sync_exclude.txt"


def load_sync_exclude() -> set:
    """Klucze projektów wyłączonych z synchronizacji (oba kierunki) - np. na czas
    naprawy stanu. Plik sync_exclude.txt: jeden klucz w linii, '#' = komentarz.
    Projekt zostaje w arkuszu, więc narzędzia diagnostyczne i naprawcze dalej go widzą."""
    if not SYNC_EXCLUDE_FILE.exists():
        return set()
    keys = set()
    for line in SYNC_EXCLUDE_FILE.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            keys.add(line.upper())
    return keys


def drop_excluded(pairs: list, key_index: int) -> list:
    excluded = load_sync_exclude() | core.load_protected_projects()
    skipped = sorted({p[key_index].upper() for p in pairs if p[key_index].upper() in excluded})
    if skipped:
        print(f"[WYKLUCZONE] Pomijam projekty z {SYNC_EXCLUDE_FILE.name}: {', '.join(skipped)} "
              f"(usuń je z pliku, żeby znów synchronizować).")
    return [p for p in pairs if p[key_index].upper() not in excluded]


def get_project_pairs(args) -> list[tuple[str, str, str]]:
    """Zwraca listę (nazwa_projektu_Asana, klucz_Jira, link_do_Asany) z
    arkusza. Klucz Jiry z kolumny jira_key, a gdy jej brak - z
    project_key_cache.json (tak jak jira_sync.py). link_do_Asany może być
    pusty - wtedy sync_project() POMIJA projekt z ostrzeżeniem (dopasowanie
    po nazwie jest celowo wyłączone).

    Z --asana-link (i jednym kluczem w --project-keys) arkusz jest pomijany - projekt
    spoza arkusza, np. z karty „Migracja projektu” w panelu."""
    if getattr(args, "asana_link", None):
        keys = [k.strip().upper() for k in (args.project_keys or "").split(",") if k.strip()]
        if len(keys) != 1:
            raise SystemExit("--asana-link wymaga dokładnie jednego klucza w --project-keys.")
        return drop_excluded([(args.asana_name or keys[0], keys[0], args.asana_link)], 1)
    sheet_rows = core.get_sheet_rows()
    cache = core.load_key_cache()
    pairs = []
    for row in sheet_rows:
        norm_name = core.normalize(row.project_name)
        key = row.jira_key or cache.get(norm_name)
        if key:
            pairs.append((row.project_name, key, row.asana_project_link))

    if args.project_keys:
        wanted = {k.strip().upper() for k in args.project_keys.split(",") if k.strip()}
        pairs = [p for p in pairs if p[1].upper() in wanted]

    return drop_excluded(pairs, 1)


def main() -> None:
    try:
        sys.stdout.reconfigure(line_buffering=True)  # wypisywanie na bieżąco, także przy przechwyconym wyjściu
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--project-keys", type=str, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--asana-link", default=None,
                        help="Link do projektu Asany dla JEDNEGO projektu z --project-keys - bez arkusza")
    parser.add_argument("--asana-name", default=None, help="Nazwa projektu Asany (do komunikatów) przy --asana-link")
    parser.add_argument(
        "--shard", type=str, default=None,
        help="Podział pracy między kilka RÓWNOLEGLE uruchomionych procesów, bez ręcznego "
             "kopiowania kluczy — format 'K/N' (np. '1/3'). Patrz jira_sync.py --help po "
             "pełne wyjaśnienie. Ignorowane razem z --project-keys (tam i tak wybierasz "
             "konkretne klucze ręcznie).",
    )
    args = parser.parse_args()

    if not args.dry_run:
        setup_file_logging("asana_jira_sync")

    pairs = get_project_pairs(args)
    if args.shard:
        pairs = core.apply_shard(pairs, args.shard)
        print(f"--shard {args.shard}: ten proces obsłuży {len(pairs)} projekt(ów).")
    if args.limit:
        pairs = pairs[: args.limit]

    print(f"Projektów do synchronizacji: {len(pairs)}")
    if args.dry_run:
        print("TRYB DRY-RUN — nic nie zostanie zapisane.\n")

    state = load_state()
    user_map = load_user_map()
    if not user_map:
        print(
            "UWAGA: user_map.json jest puste/nie istnieje — zadania będą importowane "
            "bez przypisania (assignee). Daj znać, jeśli chcesz skrypt do automatycznego "
            "dopasowania użytkowników po adresie e-mail.\n"
        )
    section_status_map = load_section_status_map()
    if not section_status_map:
        print(
            "UWAGA: section_status_map.json jest puste/nie istnieje — zadania zostaną przy "
            "domyślnym statusie startowym workflow. Zbuduj mapę przez: "
            "python list_asana_sections.py \"Nazwa projektu\"\n"
        )

    totals = {"created": 0, "created_epics": 0, "created_tasks": 0, "created_subtasks": 0, "updated": 0, "updated_epics": 0, "updated_tasks": 0, "updated_subtasks": 0, "skipped": 0, "skipped_unchanged": 0, "skipped_deleted": 0, "skipped_epic_retrofit": 0, "skipped_nested": 0, "skipped_jira_master": 0, "errors": 0, "comments": 0, "attachments": 0, "status_ok": 0, "status_failed": 0, "flattened_parents": 0, "skipped_section": 0}
    all_unmapped_sections: set = set()
    for i, (asana_name, jira_key, asana_link) in enumerate(pairs):
        print(f"\n[{i + 1}/{len(pairs)}] {asana_name} -> {jira_key}")

        with project_lock(jira_key) as acquired:
            if not acquired:
                print(f"  [POMINIĘTO] {jira_key} jest właśnie przetwarzany przez inny proces "
                      f"(prawdopodobnie równoległe uruchomienie) — pomijam, żeby uniknąć "
                      f"duplikatów zadań. Uruchom ponownie później, żeby dokończyć ten projekt.")
                totals["skipped"] += 1
                continue

            stats = sync_project(asana_name, jira_key, state, user_map, section_status_map, args.dry_run, asana_link)
            for k in totals:
                totals[k] += stats.get(k, 0)
            all_unmapped_sections |= stats.get("unmapped_sections", set())

            if not args.dry_run:
                save_state(state)  # zapisuj po każdym projekcie, nie tylko na końcu

    print("\n" + "=" * 60)
    print("PODSUMOWANIE")
    print(f"  Utworzono:      {totals['created']} (Epiki: {totals['created_epics']}, "
          f"Taski: {totals['created_tasks']}, Sub-taski: {totals['created_subtasks']})")
    print(f"  Zaktualizowano: {totals['updated']} (Epiki: {totals['updated_epics']}, "
          f"Taski: {totals['updated_tasks']}, Sub-taski: {totals['updated_subtasks']})")
    print(f"  Linki do Asany dodane: {_LINKS_ADDED['count']}")
    print(f"  Pominięto:      {totals['skipped']}")
    print(f"    bez zmian od ostatniego syncu:          {totals['skipped_unchanged']}")
    if totals["skipped_nested"]:
        print(f"      w tym podzadania obecne też na liście projektu (obsłużone razem z rodzicem): "
              f"{totals['skipped_nested']}")
    print(f"    usunięte w Jirze (deleted_in_jira):     {totals['skipped_deleted']}")
    print(f"    kandydaci na Epic do retrofit_epics.py: {totals['skipped_epic_retrofit']}")
    print(f"    prowadzone w Jirze ('migriert zu Jira'):   {totals['skipped_jira_master']}")
    if totals.get("skipped_section"):
        print(f"    z sekcji oznaczonych do pominięcia ('pomin'): {totals['skipped_section']}")
    if totals.get("flattened_parents"):
        print(f"  Zadań-podzadań z własnymi podzadaniami (spłaszczone pod przodka): {totals['flattened_parents']}")
    if totals["skipped_deleted"]:
        print("    UWAGA: zadania oznaczone jako usunięte w Jirze nie będą odtwarzane. Jeśli projekt")
        print("    w Jirze utworzono od nowa, a stan został stary, wyczyść wpisy projektu")
        print("    (clean_stale_task_state.py) - inaczej te zadania nigdy nie powstaną.")
    print(f"  Błędy:          {totals['errors']}")
    print(f"  Komentarzy:     {totals['comments']}")
    print(f"  Załączników:    {totals['attachments']}")
    print(f"  Statusy OK:     {totals['status_ok']}")
    print(f"  Statusy błąd:   {totals['status_failed']}")
    if all_unmapped_sections:
        print(f"\nSekcje BEZ wpisu w section_status_map.json (zadania zostały przy statusie domyślnym):")
        for name in sorted(all_unmapped_sections):
            print(f'  "{name}"')

    for section in sorted(all_unmapped_sections):
        record_warning("-", f"sekcja '{section}'", "brak wpisu w section_status_map.json - zadania zostały w statusie domyślnym")
    write_shard_summary("asana_jira_sync", args.shard, {
        "utworzone": totals["created"],
        "utworzone epiki": totals["created_epics"],
        "utworzone taski": totals["created_tasks"],
        "utworzone sub-taski": totals["created_subtasks"],
        "zaktualizowane": totals["updated"],
        "zaktualizowane epiki": totals["updated_epics"],
        "zaktualizowane taski": totals["updated_tasks"],
        "zaktualizowane sub-taski": totals["updated_subtasks"],
        "komentarze": totals["comments"],
        "załączniki": totals["attachments"],
        "linki do asany": _LINKS_ADDED["count"],
        "pominięte": totals["skipped"],
        "pominięte bez zmian": totals["skipped_unchanged"],
        "w tym podzadania z listy projektu": totals["skipped_nested"],
        "pominięte usunięte w jirze": totals["skipped_deleted"],
        "pominięte do retrofit": totals["skipped_epic_retrofit"],
        "pominięte prowadzone w jirze": totals["skipped_jira_master"],
        "statusy_ok": totals["status_ok"],
        "statusy_błąd": totals["status_failed"],
        "błędy": totals["errors"],
        "projekty": len(pairs),
        "sekcje_bez_mapowania": len(all_unmapped_sections),
    })

    if not args.dry_run and not is_pipeline_child():
        send_slack_summary(
            f"*asana_jira_sync.py* zakończony.\n"
            f"Utworzone: {totals['created']} (Epiki: {totals['created_epics']}, Taski: {totals['created_tasks']}, "
            f"Sub-taski: {totals['created_subtasks']}) | Zaktualizowane: {totals['updated']} "
            f"(Epiki: {totals['updated_epics']}, Taski: {totals['updated_tasks']}, "
            f"Sub-taski: {totals['updated_subtasks']}) | "
            f"Komentarze: {totals['comments']} | Załączniki: {totals['attachments']} | "
            f"Błędy: {totals['errors']}"
        )


if __name__ == "__main__":
    main()
