#!/usr/bin/env python3
"""
asana_jira_sync.py — ETAP 1 (z 3)

Ten etap celowo obejmuje TYLKO:
  - Jednokierunkową synchronizację Asana -> Jira (na razie bez powrotu).
  - Podstawowe pola: tytuł, opis, przypisany, status (przez sekcję Asany),
    termin.
  - BEZ komentarzy i załączników (te dojdą w Etapie 2 i 3, po potwierdzeniu,
    że ten rdzeń działa poprawnie u Ciebie).

Dlaczego etapami: przy 550 projektach i braku możliwości przetestowania tego
z mojej strony (brak dostępu sieciowego do api.asana.com / *.atlassian.net
z mojego środowiska) lepiej najpierw zweryfikować rdzeń na 1 projekcie, niż
od razu pisać całość na ślepo.

Mapowanie projektów: reużywa project_key_cache.json (ten sam plik, którego
używa jira_sync.py) — każda para (nazwa projektu Asana -> klucz Jira) już
tam jest, więc NIE trzeba konfigurować Item Routing ręcznie.

Mapowanie zadań (żeby nie duplikować przy kolejnych uruchomieniach): osobny
lokalny plik task_sync_state.json, budowany i utrzymywany przez ten skrypt:
{
  "<klucz Jira projektu>": {
    "<asana_task_gid>": {
      "jira_key": "PROJ-123",
      "asana_modified_at": "2026-01-01T12:00:00.000Z"
    }
  }
}

Wymaga w .env (te same zmienne co jira_sync.py):
  JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN, ASANA_TOKEN, ASANA_WORKSPACE_GID

Użycie:
  python asana_jira_sync.py --dry-run --limit 1 --project-keys KLUCZ
  python asana_jira_sync.py --limit 1 --project-keys KLUCZ   # faktyczny zapis
  python asana_jira_sync.py                                   # wszystkie z arkusza
"""
import argparse
import fcntl
import json
import os
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

JIRA_BASE_URL = os.environ["JIRA_BASE_URL"].rstrip("/")
JIRA_EMAIL = os.environ["JIRA_EMAIL"]
JIRA_API_TOKEN = os.environ["JIRA_API_TOKEN"]
JIRA_AUTH = (JIRA_EMAIL, JIRA_API_TOKEN)

ASANA_TOKEN = os.environ["ASANA_TOKEN"]
ASANA_WORKSPACE_GID = os.environ["ASANA_WORKSPACE_GID"]
ASANA_HEADERS = {"Authorization": f"Bearer {ASANA_TOKEN}"}
ASANA_API = "https://app.asana.com/api/1.0"

STATE_FILE = Path("task_sync_state.json")
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
RETRYABLE_STATUS_CODES = {500, 502, 503, 504}
MAX_RETRIES = 4
RETRY_BASE_DELAY = 2  # sekundy; rośnie: 2, 4, 8, 16...


def request_with_retry(method: str, url: str, **kwargs) -> requests.Response:
    """Wrapper na requests.request() z automatycznym ponawianiem błędów 5xx.
    Błędy 4xx (np. 400 walidacji) NIE są ponawiane - to prawdziwe błędy
    żądania, ponowna próba i tak zwróci to samo."""
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

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    """Zapisuje stan bezpiecznie przy RÓWNOLEGŁYCH uruchomieniach (np. kilka
    instancji z różnymi --project-keys naraz):
    1. Blokada pliku (fcntl.flock) na czas odczytu+zapisu — inne procesy
       czekają, więc nie ma wyścigu przy jednoczesnym zapisie.
    2. SCALENIE z tym, co jest AKTUALNIE na dysku (per klucz projektu Jira),
       zamiast bezwarunkowego nadpisania całości — dzięki temu proces A nie
       kasuje postępu procesu B, jeśli oba pracowały na RÓŻNYCH projektach.

    Bezpieczne dla równoległych instancji pracujących na RÓŻNYCH projektach.
    NIE naprawia sytuacji, gdyby dwie instancje pracowały na TYM SAMYM
    projekcie jednocześnie — tego po prostu nie rób."""
    STATE_FILE.touch(exist_ok=True)
    with open(STATE_FILE, "r+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            content = f.read()
            on_disk = json.loads(content) if content.strip() else {}
            on_disk.update(state)
            f.seek(0)
            f.truncate()
            json.dump(on_disk, f, indent=2, ensure_ascii=False)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def load_user_map() -> dict:
    """Asana user gid -> Jira accountId. Musisz to uzupełnić ręcznie (lub przez
    osobny skrypt dopasowujący po adresie e-mail — daj znać, jeśli chcesz,
    żebym taki dopisał). Bez wpisu w tej mapie zadania importują się jako
    nieprzypisane (assignee=None), zamiast się wywalać."""
    if USER_MAP_FILE.exists():
        return json.loads(USER_MAP_FILE.read_text(encoding="utf-8"))
    return {}


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
        resp = requests.get(url, headers=ASANA_HEADERS, params=params, timeout=REQUEST_TIMEOUT)
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
        "modified_at", "memberships.section.name", "memberships.project.gid",
    ]
    params = {"opt_fields": ",".join(fields), "limit": 100}
    tasks = []
    offset = None
    while True:
        if offset:
            params["offset"] = offset
        resp = requests.get(url, headers=ASANA_HEADERS, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
        tasks.extend(payload.get("data", []))
        offset = payload.get("next_page", {}).get("offset") if payload.get("next_page") else None
        if not offset:
            return tasks


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
            return section["name"]
    return None


def get_asana_comments(task_gid: str) -> list[dict]:
    """Zwraca TYLKO prawdziwe komentarze (nie zdarzenia systemowe typu
    'zmienił termin', 'przypisał zadanie' itp.)."""
    url = f"{ASANA_API}/tasks/{task_gid}/stories"
    params = {"opt_fields": "html_text,text,created_at,created_by.name,resource_subtype"}
    resp = requests.get(url, headers=ASANA_HEADERS, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    stories = resp.json().get("data", [])
    return [s for s in stories if s.get("resource_subtype") == "comment_added"]


def get_asana_attachments(task_gid: str) -> list[dict]:
    url = f"{ASANA_API}/tasks/{task_gid}/attachments"
    params = {"opt_fields": "name,download_url"}
    resp = requests.get(url, headers=ASANA_HEADERS, params=params, timeout=REQUEST_TIMEOUT)
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
        return {"type": "doc", "version": 1, "content": self.doc or [{"type": "paragraph", "content": []}]}


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


def get_status_id_by_name(project_key: str, status_name: str) -> Optional[str]:
    """Znajduje ID statusu o danej nazwie wśród statusów dostępnych w projekcie
    (case-insensitive). Zwraca None, jeśli nie znaleziono — wtedy status
    pomijamy zamiast zgadywać."""
    url = f"{JIRA_BASE_URL}/rest/api/3/project/{project_key}/statuses"
    resp = requests.get(url, auth=JIRA_AUTH, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    norm_target = status_name.strip().lower()
    for issue_type in resp.json():
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
        if not info.get("required") or field_id in ("summary", "issuetype", "project"):
            continue  # obsługiwane już wprost w create_jira_issue

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


def _safe_summary_and_description(summary: str, description_html: str) -> tuple[str, str]:
    """Jeśli tytuł z Asany przekracza limit Jiry (255 znaków), skraca go
    bezpiecznie (z wielokropkiem) i dokłada PEŁNY, oryginalny tytuł na
    początku opisu — żeby nic nie zginęło, tylko było przesunięte."""
    if len(summary) <= JIRA_SUMMARY_MAX_LEN:
        return summary, description_html
    truncated = summary[: JIRA_SUMMARY_MAX_LEN - 1].rstrip() + "…"
    prefix = f"<body><strong>Pełny tytuł:</strong> {summary}<br><br>"
    if description_html and description_html.strip().startswith("<body>"):
        merged = prefix + description_html[len("<body>"):]
    else:
        merged = prefix + (description_html or "") + "</body>"
    print(f"      [INFO] Tytuł dłuższy niż {JIRA_SUMMARY_MAX_LEN} znaków — skrócony, pełna wersja w opisie.")
    return truncated, merged


def create_jira_issue(project_key: str, summary: str, description_html: str,
                       assignee_account_id: Optional[str], due_on: Optional[str]) -> str:
    summary, description_html = _safe_summary_and_description(summary, description_html)
    url = f"{JIRA_BASE_URL}/rest/api/3/issue"
    fields = {
        "project": {"key": project_key},
        "summary": summary,
        "issuetype": {"name": "Task"},
        "description": html_to_adf(description_html),
    }
    if assignee_account_id:
        fields["assignee"] = {"accountId": assignee_account_id}
    if due_on:
        fields["duedate"] = due_on
    fields.update(get_required_field_defaults(project_key, "Task"))

    resp = request_with_retry("POST", url, auth=JIRA_AUTH, json={"fields": fields}, timeout=REQUEST_TIMEOUT)

    if resp.status_code >= 300 and assignee_account_id and _is_assignee_error(resp):
        # Oryginalny assignee nie może być przypisany w tym projekcie (np. nie
        # jest jego członkiem) - spróbuj z leadem PROJEKTU zamiast się poddawać.
        lead_id = get_project_lead_account_id(project_key)
        if lead_id and lead_id != assignee_account_id:
            fields["assignee"] = {"accountId": lead_id}
            resp = request_with_retry("POST", url, auth=JIRA_AUTH, json={"fields": fields}, timeout=REQUEST_TIMEOUT)
            if resp.status_code < 300:
                print(f"      [INFO] Assignee z Asany nie mógł być przypisany w '{project_key}' — użyto leada projektu.")

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

    if resp.status_code >= 300 and assignee_account_id and _is_assignee_error(resp):
        project_key = issue_key.split("-")[0]
        lead_id = get_project_lead_account_id(project_key)
        if lead_id and lead_id != assignee_account_id:
            fields["assignee"] = {"accountId": lead_id}
            resp = request_with_retry("PUT", url, auth=JIRA_AUTH, json={"fields": fields}, timeout=REQUEST_TIMEOUT)
            if resp.status_code < 300:
                print(f"      [INFO] Assignee z Asany nie mógł być przypisany w '{project_key}' — użyto leada projektu.")

    if resp.status_code >= 300:
        raise RuntimeError(f"Błąd aktualizacji {issue_key}: {resp.status_code} {resp.text}")


def get_current_status_id(issue_key: str) -> Optional[str]:
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}"
    resp = requests.get(url, auth=JIRA_AUTH, params={"fields": "status"}, timeout=REQUEST_TIMEOUT)
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


def transition_jira_issue_to_status(issue_key: str, status_id: str) -> bool:
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
    for trans_id in path:
        resp = request_with_retry(
            "POST", url, auth=JIRA_AUTH, json={"transition": {"id": trans_id}}, timeout=REQUEST_TIMEOUT
        )
        if resp.status_code >= 300:
            print(f"      [DEBUG] {issue_key}: Jira odrzuciła przejście {trans_id}: {resp.status_code} {resp.text}")
            return False  # zadanie zostaje na statusie osiągniętym do tego kroku - patrz niżej
    return True


def post_jira_comment(issue_key: str, author_name: str, created_at: str, html_text: str) -> None:
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

    resp = request_with_retry("POST", url, auth=JIRA_AUTH, json={"body": body_adf}, timeout=REQUEST_TIMEOUT)
    if resp.status_code >= 300:
        raise RuntimeError(f"Błąd dodawania komentarza do {issue_key}: {resp.status_code} {resp.text}")


def upload_jira_attachment(issue_key: str, filename: str, content: bytes) -> None:
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/attachments"
    headers = {"X-Atlassian-Token": "no-check"}  # wymagane przez Jirę dla uploadu załączników
    files = {"file": (filename, content)}
    resp = request_with_retry("POST", url, auth=JIRA_AUTH, headers=headers, files=files, timeout=REQUEST_TIMEOUT)
    if resp.status_code >= 300:
        raise RuntimeError(f"Błąd wgrywania załącznika '{filename}' do {issue_key}: {resp.status_code} {resp.text}")


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
            post_jira_comment(jira_key, author, c.get("created_at", ""), c.get("html_text") or c.get("text", ""))
            synced.add(c["gid"])
        added += 1
    if not dry_run:
        task_state["synced_comment_gids"] = sorted(synced)
    return added


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
            upload_jira_attachment(jira_key, a.get("name") or "attachment", file_resp.content)
            synced.add(a["gid"])
            added += 1
        except Exception as exc:  # noqa: BLE001
            print(f"      [OSTRZEŻENIE] Nie udało się przenieść załącznika '{a.get('name')}': {exc}")
    if not dry_run:
        task_state["synced_attachment_gids"] = sorted(synced)
    return added


def resolve_target_status_id(project_key: str, section_name: Optional[str],
                              section_status_map: dict, unmapped_sections: set) -> Optional[str]:
    """Zamienia nazwę sekcji Asany na ID statusu Jira, przez tabelę
    section_status_map (NIE przez bezpośrednie dopasowanie nazwy sekcji do
    nazwy statusu — te zwykle się nie pokrywają, sekcje bywają etapami/
    kamieniami milowymi). Brak wpisu w tabeli = zadanie zostaje przy
    domyślnym statusie startowym (zwraca None), a sekcja trafia do
    unmapped_sections, żeby na końcu pokazać, czego jeszcze brakuje w mapie."""
    if not section_name:
        return None
    target_status_name = section_status_map.get(section_name)
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

    asana_gid = None
    gid_from_link = core.extract_asana_project_gid(asana_project_link)
    if gid_from_link:
        # Link podany wprost - pewniejsze niż dopasowanie po nazwie (omija
        # literówki, duplikaty nazw, zmiany nazwy w Asanie).
        asana_gid = gid_from_link
    else:
        asana_gid = find_asana_project_gid(asana_project_name)

    if not asana_gid:
        print(f"  [OSTRZEŻENIE] Nie znaleziono projektu '{asana_project_name}' w Asanie — pomijam.")
        return {"created": 0, "updated": 0, "skipped": 0, "errors": 0, "comments": 0, "attachments": 0, "status_ok": 0, "status_failed": 0, "unmapped_sections": set()}

    tasks = get_asana_tasks(asana_gid)
    print(f"  Zadań w Asanie: {len(tasks)}")

    stats = {"created": 0, "updated": 0, "skipped": 0, "errors": 0, "comments": 0, "attachments": 0, "status_ok": 0, "status_failed": 0}
    unmapped_sections: set = set()

    for task in tasks:
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

        try:
            if existing:
                target_key = existing["jira_key"]
                fields_changed = existing.get("asana_modified_at") != modified_at
                if fields_changed:
                    if dry_run:
                        print(f"    [DRY-RUN] Zaktualizowałbym {target_key}: '{name}'")
                    else:
                        update_jira_issue(target_key, name, notes, jira_account_id, due_on)
                        project_state[gid]["asana_modified_at"] = modified_at
                    stats["updated"] += 1
                else:
                    stats["skipped"] += 1

                # Status - ZAWSZE sprawdzany, niezależnie od tego, czy modified_at
                # się zmienił: przeniesienie zadania między sekcjami w Asanie nie
                # zawsze aktualizuje ten znacznik, więc poleganie wyłącznie na nim
                # gubiło zmiany statusu (to był dokładnie zgłoszony problem).
                if not dry_run:
                    section_name = get_task_section_name(task, asana_gid)
                    status_id = resolve_target_status_id(jira_key, section_name, section_status_map, unmapped_sections)
                    if os.getenv("DEBUG_SYNC"):
                        current_status_now = get_current_status_id(target_key)
                        print(
                            f"      [DEBUG] {target_key}: sekcja='{section_name}' -> "
                            f"docelowy status_id={status_id!r}, obecny status_id={current_status_now!r}"
                        )
                    if status_id:
                        moved = transition_jira_issue_to_status(target_key, status_id)
                        if not moved:
                            print(
                                f"      [OSTRZEŻENIE] {target_key}: nie udało się przejść do statusu "
                                f"odpowiadającego sekcji '{section_name}' (cel nieosiągalny z bieżącego "
                                f"statusu w tym workflow, albo Jira odrzuciła przejście - np. walidator "
                                f"pola wymagany przy tym przejściu)."
                            )
                            stats["status_failed"] += 1
                        else:
                            stats["status_ok"] += 1
            else:
                # Nowe zadanie
                if dry_run:
                    print(f"    [DRY-RUN] Utworzyłbym w {jira_key}: '{name}'")
                    target_key = None  # nic nie utworzone naprawdę - komentarzy/załączników nie sprawdzamy niżej
                else:
                    target_key = create_jira_issue(jira_key, name, notes, jira_account_id, due_on)
                    section_name = get_task_section_name(task, asana_gid)
                    status_id = resolve_target_status_id(jira_key, section_name, section_status_map, unmapped_sections)
                    if status_id:
                        moved = transition_jira_issue_to_status(target_key, status_id)
                        if not moved:
                            print(
                                f"      [OSTRZEŻENIE] {target_key}: nie udało się przejść do statusu "
                                f"odpowiadającego sekcji '{section_name}' (cel nieosiągalny z bieżącego "
                                f"statusu w tym workflow, albo Jira odrzuciła przejście)."
                            )
                            stats["status_failed"] += 1
                        else:
                            stats["status_ok"] += 1
                    project_state[gid] = {"jira_key": target_key, "asana_modified_at": modified_at}
                    print(f"    Utworzono {target_key}: '{name}'")
                stats["created"] += 1

            # Komentarze i załączniki - ZAWSZE sprawdzane (niezależnie od tego,
            # czy pola zadania się zmieniły), bo nowy komentarz/załącznik nie
            # zawsze zmienia modified_at samego zadania w sposób, na który
            # można polegać.
            if target_key:
                task_state = project_state.setdefault(gid, {"jira_key": target_key, "asana_modified_at": modified_at})
                added_comments = sync_comments(gid, target_key, task_state, dry_run)
                added_attachments = sync_attachments(gid, target_key, task_state, dry_run)
                stats["comments"] += added_comments
                stats["attachments"] += added_attachments

        except Exception as exc:  # noqa: BLE001
            print(f"    [BŁĄD] '{name}': {exc}")
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

def get_project_pairs(args) -> list[tuple[str, str, str]]:
    """Zwraca listę (nazwa_projektu_Asana, klucz_Jira, link_do_Asany) —
    z project_key_cache.json, tak jak jira_sync.py, więc żadne dodatkowe
    mapowanie nie jest potrzebne. link_do_Asany może być pustym stringiem
    (wtedy sync_project() dopasowuje po nazwie, tak jak dotąd)."""
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

    return pairs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--project-keys", type=str, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.dry_run:
        setup_file_logging("asana_jira_sync")

    pairs = get_project_pairs(args)
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

    totals = {"created": 0, "updated": 0, "skipped": 0, "errors": 0, "comments": 0, "attachments": 0, "status_ok": 0, "status_failed": 0}
    all_unmapped_sections: set = set()
    for i, (asana_name, jira_key, asana_link) in enumerate(pairs):
        print(f"\n[{i + 1}/{len(pairs)}] {asana_name} -> {jira_key}")
        stats = sync_project(asana_name, jira_key, state, user_map, section_status_map, args.dry_run, asana_link)
        for k in totals:
            totals[k] += stats.get(k, 0)
        all_unmapped_sections |= stats.get("unmapped_sections", set())

        if not args.dry_run:
            save_state(state)  # zapisuj po każdym projekcie, nie tylko na końcu

    print("\n" + "=" * 60)
    print("PODSUMOWANIE")
    print(f"  Utworzono:      {totals['created']}")
    print(f"  Zaktualizowano: {totals['updated']}")
    print(f"  Pominięto:      {totals['skipped']} (bez zmian od ostatniego syncu)")
    print(f"  Błędy:          {totals['errors']}")
    print(f"  Komentarzy:     {totals['comments']}")
    print(f"  Załączników:    {totals['attachments']}")
    print(f"  Statusy OK:     {totals['status_ok']}")
    print(f"  Statusy błąd:   {totals['status_failed']}")
    if all_unmapped_sections:
        print(f"\nSekcje BEZ wpisu w section_status_map.json (zadania zostały przy statusie domyślnym):")
        for name in sorted(all_unmapped_sections):
            print(f'  "{name}"')

    if not args.dry_run:
        send_slack_summary(
            f"*asana_jira_sync.py* zakończony.\n"
            f"Utworzone: {totals['created']} | Zaktualizowane: {totals['updated']} | "
            f"Komentarze: {totals['comments']} | Załączniki: {totals['attachments']} | "
            f"Błędy: {totals['errors']}"
        )


if __name__ == "__main__":
    main()
