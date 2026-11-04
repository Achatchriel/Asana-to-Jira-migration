#!/usr/bin/env python3
"""
copy_project_cross_instance.py

Kopiuje projekt Z JEGO ZADANIAMI między DWIEMA RÓŻNYMI instancjami Jiry
(np. dwie osobne firmy/organizacje, gdzie jesteś adminem w obu). To INNY
scenariusz niż copy_project.py (który kopiuje WEWNĄTRZ jednej instancji,
klonując ID schematów - te ID nic nie znaczą w innej instancji).

KIERUNEK: ŹRÓDŁO to INNA instancja (podaj jej dane osobno poniżej), CEL to
TA SAMA instancja Jiry, z której korzystają WSZYSTKIE pozostałe skrypty w
tym projekcie (JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN w .env) - nie
trzeba ich zmieniać, zostają takie, jak są.

WYMAGANE zmienne w .env (DODATKOWE, dla instancji ŹRÓDŁOWEJ):
    SOURCE_JIRA_BASE_URL=https://inna-instancja.atlassian.net
    SOURCE_JIRA_EMAIL=...
    SOURCE_JIRA_API_TOKEN=...

Mapowanie statusów - dwa sposoby (można łączyć):
  1. Według KATEGORII statusu w źródle (najprostsze):
       --done-status Done --other-status Misc
     zgłoszenia w kategorii "Done" w źródle (np. Done, Closed, Resolved) -> Done,
     wszystkie pozostałe -> Misc.
  2. Według NAZWY, plik JSON:  --status-map status_map.json
       {"Nazwa statusu w źródle": "Nazwa statusu w celu", ...}
     Wpis z pliku ma pierwszeństwo przed regułą kategorii.
PODZIAŁ NA KILKA PROJEKTÓW: --only-statuses / --exclude-statuses (nazwy statusów
ŹRÓDŁA, po przecinku) wybierają część zgłoszeń; skrypt uruchamia się wtedy raz na
każdy projekt docelowy. Zasady, żeby nie rozrywać hierarchii:
  - Task/Bug/Story itd. -> według WŁASNEGO statusu,
  - Sub-task -> razem ze swoim rodzicem, niezależnie od własnego statusu,
  - Epic z dziećmi -> do każdego projektu, do którego trafia choć jedno jego
    dziecko (bez pustych duplikatów w pozostałych),
  - Epic bez dzieci -> według własnego statusu.
Przy filtrze stan kopiowania jest prowadzony osobno dla pary źródło>cel, więc
ten sam Epik może trafić do dwóch projektów bez konfliktu.

UPRAWNIENIA: projekt docelowy dostaje te same grupy i role co projekty z
migracji (jira_sync.py, te same zmienne .env): DEVELOPER_GROUP_NAME i
DEVELOPER_ACCOUNT_IDS do ról z DEVELOPER_ROLE_NAME, ADMIN_ACCOUNT_ID (oraz
lead przy ADD_LEAD_AS_ADMIN=true) do ADMIN_ROLE_NAME. Dodatkowe grupy:
--extra-group "Nazwa grupy=Nazwa roli" (można powtarzać). Wykonywane PRZED
kopiowaniem zgłoszeń - członkowie ról mogą wtedy zostać przypisani.
Pominięcie: --skip-access.

LUDZIE: zgłaszający (reporter) i przypisany (assignee) są przenoszeni, jeśli
ta sama osoba ma konto w instancji docelowej. Dopasowanie po kolei:
  1. --user-map plik.json {"accountId albo email ze źródła": "accountId u nas"},
  2. ten sam accountId (to samo konto Atlassian w obu instancjach - najpewniejsze),
  3. ten sam email (jeśli instancja go udostępnia).
Niedopasowane osoby: pole zostaje puste / domyślne, lista w podsumowaniu.
Reporter wymaga uprawnienia "Modify Reporter" w projekcie docelowym - bez niego
zgłoszenia powstają z kontem API jako zgłaszającym (ostrzeżenie raz, bez błędu).
Twórcy (creator) i autorów komentarzy nie da się ustawić przez API.
Żeby informacja nie przepadła (zwłaszcza dla osób bez konta u nas), każde
zgłoszenie dostaje na początku opisu notkę o pochodzeniu: oryginalny klucz,
data utworzenia, zgłaszający i przypisany po imieniu i nazwisku.
Wyłączenie: --no-origin-note. Tekst notki: ORIGIN_NOTE_TEMPLATE niżej.

Statusy docelowe muszą istnieć w workflow projektu docelowego - skrypt
sprawdza to PRZED skopiowaniem pierwszego zgłoszenia i zatrzymuje się, jeśli
ich brak. Przejście do statusu idzie najkrótszą ścieżką przez workflow
(ta sama logika co asana_jira_sync.py), nie tylko jednym krokiem.

Użycie:
    python copy_project_cross_instance.py --source-key ABC --target-key XYZ \\
        --target-name "Nowy Projekt" --target-lead-account-id ACCID \\
        --done-status Done --other-status Misc --dry-run

    python copy_project_cross_instance.py --source-key ABC --target-key XYZ \\
        --target-name "Nowy Projekt" --target-lead-account-id ACCID \\
        --status-map status_map.json

Bezpieczne przy wielokrotnym uruchomieniu - stan zapisywany trwale i atomowo
do cross_instance_copy_state.json (source_issue_key -> target_issue_key),
żeby nie duplikować zadań/komentarzy/załączników przy ponownym uruchomieniu.

HIERARCHIA: typ i rodzic są zachowywane.
  - Epic        -> Epic,
  - Story/Bug/… -> ten sam typ, jeśli istnieje w projekcie docelowym, inaczej Task;
                   dziecko Epika dostaje tego samego (skopiowanego) Epika jako rodzica,
  - Sub-task    -> Sub-task pod skopiowanym rodzicem.
Zgłoszenia tworzone są poziomami (Epiki, potem standardowe, potem Sub-taski),
więc rodzic zawsze istnieje, zanim powstanie dziecko.
"""
import argparse
from datetime import datetime
import json
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright  # noqa: F401  (używane przez copy_boards/copy_project)

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core
import copy_project as cp  # funkcje strony DOCELOWEJ (FIRMA) - reużyte wprost, bez duplikacji
import convert_to_epic as cte  # typy zgłoszeń w projekcie docelowym (Sub-task, pole Epic Name)
import asana_jira_sync as ajs  # przejścia statusów przez graf workflow - ta sama, sprawdzona logika

STATE_FILE = Path(__file__).resolve().parent / "cross_instance_copy_state.json"

# ŹRÓDŁO = inna instancja (dane osobno, poniżej). CEL = FIRMA, czyli
# core.JIRA_BASE_URL / core.jira_auth() - te same, których używają
# WSZYSTKIE pozostałe skrypty w tym projekcie, bez zmian.
SOURCE_BASE_URL = core.os.getenv("SOURCE_JIRA_BASE_URL", "").rstrip("/")
SOURCE_EMAIL = core.os.getenv("SOURCE_JIRA_EMAIL", "")
SOURCE_API_TOKEN = core.os.getenv("SOURCE_JIRA_API_TOKEN", "")


def source_auth():
    return (SOURCE_EMAIL, SOURCE_API_TOKEN)



def request_with_retry(method, url, max_retries=4, **kwargs):
    """Ta sama logika ponawiania co w asana_jira_sync.py - 429/5xx/błędy
    sieciowe, z rosnącym opóźnieniem, zamiast od razu przerywać."""
    for attempt in range(max_retries):
        try:
            resp = requests.request(method, url, timeout=kwargs.pop("timeout", 30), **kwargs)
        except requests.exceptions.RequestException as exc:
            if attempt == max_retries - 1:
                raise
            wait = 2 ** attempt
            print(f"      [PONAWIAM {attempt+1}/{max_retries}] Błąd sieci: {exc} — czekam {wait}s...")
            time.sleep(wait)
            continue
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 2 ** attempt))
            print(f"      [PONAWIAM {attempt+1}/{max_retries}] Rate limit (429) — czekam {wait}s...")
            time.sleep(wait)
            continue
        if resp.status_code >= 500 and attempt < max_retries - 1:
            wait = 2 ** attempt
            print(f"      [PONAWIAM {attempt+1}/{max_retries}] Błąd {resp.status_code} — czekam {wait}s...")
            time.sleep(wait)
            continue
        return resp
    return resp


def source_get_project_name(source_key):
    resp = request_with_retry("GET", f"{SOURCE_BASE_URL}/rest/api/3/project/{source_key}", auth=source_auth())
    resp.raise_for_status()
    return resp.json().get("name", "")


def source_get_project_boards(project_key, extra_board_ids=None):
    """Odpowiednik cp.get_project_boards(), ale czytający ze ŹRÓDŁOWEJ
    instancji zamiast z celu (FIRMA)."""
    if extra_board_ids:
        print(f"   Pomijam automatyczne wykrywanie — używam WYŁĄCZNIE podanych --source-board-ids ({len(extra_board_ids)}).")
        boards = []
        for bid in extra_board_ids:
            resp = request_with_retry("GET", f"{SOURCE_BASE_URL}/rest/agile/1.0/board/{bid}", auth=source_auth())
            if resp.status_code == 200:
                boards.append(resp.json())
                print(f"   OK: {resp.json().get('name')} (id={bid})")
            else:
                print(f"   [OSTRZEŻENIE] --source-board-ids {bid}: nie znaleziono lub brak dostępu ({resp.status_code}).")
        return boards

    print(f"   Pobieram listę WSZYSTKICH tablic w instancji źródłowej (może chwilę potrwać)...")
    all_boards = []
    start_at = 0
    while True:
        resp = request_with_retry(
            "GET", f"{SOURCE_BASE_URL}/rest/agile/1.0/board", auth=source_auth(),
            params={"startAt": start_at, "maxResults": 50},
        )
        resp.raise_for_status()
        payload = resp.json()
        all_boards.extend(payload.get("values", []))
        if payload.get("isLast", True):
            break
        start_at += 50
    print(f"   Łącznie w instancji źródłowej: {len(all_boards)} tablic. Sprawdzam, które dotyczą '{project_key}'...")

    matching = []
    pattern = re.compile(rf'\bproject\s*(=|in)\s*[("]?[^)"\n]*\b{re.escape(project_key)}\b', re.IGNORECASE)
    for board in all_boards:
        location_key = (board.get("location") or {}).get("projectKey", "")
        if location_key.upper() == project_key.upper() or project_key.upper() in board.get("name", "").upper():
            matching.append(board)
            continue
        try:
            _, jql, _ = source_get_board_filter_jql(board["id"])
            if pattern.search(jql):
                matching.append(board)
        except Exception:  # noqa: BLE001
            continue
    print(f"   Dopasowano {len(matching)} tablic(e) do '{project_key}'. "
          f"ID (zapisz do --source-board-ids na przyszłość): {','.join(str(b['id']) for b in matching)}")
    return matching


def source_get_board_filter_jql(board_id):
    resp = request_with_retry("GET", f"{SOURCE_BASE_URL}/rest/agile/1.0/board/{board_id}/configuration", auth=source_auth())
    resp.raise_for_status()
    filter_id = str(resp.json()["filter"]["id"])
    resp2 = request_with_retry("GET", f"{SOURCE_BASE_URL}/rest/api/3/filter/{filter_id}", auth=source_auth())
    resp2.raise_for_status()
    data = resp2.json()
    return filter_id, data["jql"], data.get("name", "")


def source_get_board_columns_rest(board_id):
    resp = request_with_retry("GET", f"{SOURCE_BASE_URL}/rest/agile/1.0/board/{board_id}/configuration", auth=source_auth())
    resp.raise_for_status()
    columns = resp.json().get("columnConfig", {}).get("columns", [])
    result = []
    for col in columns:
        status_ids = [s["self"].rsplit("/", 1)[-1] for s in col.get("statuses", [])]
        result.append({"name": col.get("name", ""), "status_ids": status_ids})
    return result


def source_get_status_names(status_ids):
    names = {}
    for sid in status_ids:
        resp = request_with_retry("GET", f"{SOURCE_BASE_URL}/rest/api/3/status/{sid}", auth=source_auth())
        if resp.status_code == 200:
            names[sid] = resp.json()["name"]
    return names


def copy_boards(source_key, target_key, source_name_full, status_map, extra_board_ids=None):
    """Kopiuje WSZYSTKIE tablice projektu źródłowego (z innej instancji) do
    projektu docelowego w FIRMA - filtr, tablicę, nazwy kolumn i
    przypisanie statusów. Reużywa gotowych funkcji strony docelowej z
    copy_project.py (get_or_create_filter, get_or_create_board,
    ensure_columns_exist, assign_statuses_via_browser) - te już poprawnie
    celują w FIRMA, bez zmian."""
    print(f"\nSzukam tablic projektu źródłowego '{source_key}'...")
    source_boards = source_get_project_boards(source_key, extra_board_ids)
    if not source_boards:
        print("  Brak tablic do skopiowania.")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(cp.STATE_FILE))
        page = context.new_page()
        page.goto(f"{core.JIRA_BASE_URL}/jira/your-work", wait_until="domcontentloaded")

        if not cp.CLOUD_ID:
            cp.CLOUD_ID = cp.resolve_cloud_id(page)
        print(f"   cloudId (FIRMA): {cp.CLOUD_ID}")

        for board in source_boards:
            print(f"\n   Tablica źródłowa: '{board['name']}' (id={board['id']}, typ={board.get('type')})")
            try:
                filter_id, jql, filter_name = source_get_board_filter_jql(board["id"])
                new_jql = cp.adapt_jql_to_new_project(jql, source_key, source_name_full, target_key)
                new_filter_name = f"{target_key} - {board['name']}"
                new_filter_id, filter_reused = cp.get_or_create_filter(new_filter_name, new_jql)
                print(f"     {'Znaleziono istniejący' if filter_reused else 'Utworzono'} filtr '{new_filter_name}' (jql: {new_jql})")

                new_board_name = board["name"].replace(source_key, target_key)
                new_board_id, board_reused = cp.get_or_create_board(target_key, new_board_name, board.get("type", "kanban"), new_filter_id)
                print(f"     {'Znaleziono istniejącą' if board_reused else 'Utworzono'} tablicę '{new_board_name}' (id={new_board_id})")

                source_columns = source_get_board_columns_rest(board["id"])
                column_names = [c["name"] for c in source_columns]
                cp.ensure_columns_exist(page, target_key, new_board_id, column_names)
                print(f"     Skopiowano {len(column_names)} nazw kolumn.")

                all_status_ids = [sid for c in source_columns for sid in c["status_ids"]]
                source_status_names = source_get_status_names(all_status_ids)
                mapping = {}
                unmapped_seen = set()
                for c in source_columns:
                    if not c["status_ids"]:
                        continue
                    target_names = []
                    for sid in c["status_ids"]:
                        src_name = source_status_names.get(sid, sid)
                        tgt_name = status_map.get(src_name)
                        if tgt_name:
                            target_names.append(tgt_name)
                        else:
                            unmapped_seen.add(src_name)
                    if target_names:
                        mapping[c["name"]] = target_names
                if unmapped_seen:
                    print(f"     [OSTRZEŻENIE] Statusy źródłowe bez wpisu w mapowaniu (pominięte przy przypisywaniu do kolumn): {sorted(unmapped_seen)}")

                cp.assign_statuses_via_browser(page, new_board_id, mapping)
                print(f"     Przypisano statusy do kolumn.")
            except Exception as exc:  # noqa: BLE001
                print(f"     [BŁĄD] Nie udało się w pełni skopiować tej tablicy: {exc}")

        browser.close()



    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def load_state():
    """Brakowało tej funkcji - main() ją wywoływał, więc skrypt padał z
    NameError tuż po utworzeniu projektu, zanim skopiował pierwsze zadanie."""
    if not STATE_FILE.exists():
        return {}
    content = STATE_FILE.read_text(encoding="utf-8")
    return json.loads(content) if content.strip() else {}


def save_state(state):
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_FILE)


def load_status_map(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data:
        raise ValueError(f"Plik mapowania statusów '{path}' musi być niepustym obiektem JSON {{źródło: cel}}.")
    return data


def fetch_source_issues(source_key):
    """Zwraca listę zadań źródłowych: klucz, tytuł, opis, nazwa statusu,
    komentarze (id, autor, data, tekst), załączniki (id, nazwa, url)."""
    issues = []
    next_page_token = None
    while True:
        body = {
            "jql": f"project = {source_key}",
            "maxResults": 100,
            "fields": ["summary", "description", "status", "comment", "attachment", "issuetype", "parent",
                       "reporter", "assignee", "created", "labels", "priority"],
        }
        if next_page_token:
            body["nextPageToken"] = next_page_token
        resp = request_with_retry("POST", f"{SOURCE_BASE_URL}/rest/api/3/search/jql", auth=source_auth(), json=body)
        resp.raise_for_status()
        data = resp.json()
        for issue in data.get("issues", []):
            fields = issue.get("fields", {})
            comment_field = fields.get("comment", {}) or {}
            comments = comment_field.get("comments", []) or []
            if (comment_field.get("total") or 0) > len(comments):
                # wyniki wyszukiwania mogą zawierać tylko część długiego wątku
                comments = source_get_all_comments(issue["key"])
            it = fields.get("issuetype") or {}
            level = it.get("hierarchyLevel")
            if level is None:
                level = -1 if it.get("subtask") else (1 if it.get("name") == "Epic" else 0)
            issues.append({
                "key": issue["key"],
                "type_name": it.get("name", "Task"),
                "level": level,
                "parent_key": (fields.get("parent") or {}).get("key"),
                "reporter": _person(fields.get("reporter")),
                "assignee": _person(fields.get("assignee")),
                "created": (fields.get("created") or "")[:10],
                "summary": fields.get("summary", ""),
                "description": fields.get("description"),
                "labels": fields.get("labels") or [],
                "priority": (fields.get("priority") or {}).get("name"),
                "status_name": (fields.get("status") or {}).get("name", ""),
                "status_category": ((fields.get("status") or {}).get("statusCategory") or {}).get("key", ""),
                "comments": [
                    {
                        "id": c["id"],
                        "author": (c.get("author") or {}).get("displayName", "?"),
                        "created": c.get("created", ""),
                        "body": c.get("body"),
                    }
                    for c in comments
                ],
                "attachments": [
                    {"id": a["id"], "filename": a.get("filename", "plik"), "content_url": a.get("content")}
                    for a in (fields.get("attachment") or [])
                ],
            })
        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            return issues


ORIGIN_NOTE_TEMPLATE = ("Übernommen aus {source} · Original: {key} · erstellt am: {created} · "
                        "Melder: {reporter} · zugewiesen an: {assignee}")
COMMENT_PREFIX_TEMPLATE = "[Übernommen aus {source} ({key}) · {author}, {created}]"
NO_REPORTER = "keiner"
NO_ASSIGNEE = "nicht zugewiesen"
EMPTY_COMMENT = "(leerer Kommentar)"


def origin_note_paragraph(issue):
    """Akapit ADF z informacją o pochodzeniu zgłoszenia (kursywą)."""
    text = ORIGIN_NOTE_TEMPLATE.format(
        source=SOURCE_BASE_URL.replace("https://", ""), key=issue["key"], created=issue.get("created") or "?",
        reporter=(issue.get("reporter") or {}).get("displayName", NO_REPORTER),
        assignee=(issue.get("assignee") or {}).get("displayName", NO_ASSIGNEE),
    )
    return {"type": "paragraph", "content": [{"type": "text", "text": text, "marks": [{"type": "em"}]}]}


def with_origin_note(description, note):
    """Opis ADF z notką na początku. Brak opisu -> sama notka."""
    if not note:
        return description
    if isinstance(description, dict) and description.get("type") == "doc":
        return {**description, "content": [note, *(description.get("content") or [])]}
    return {"type": "doc", "version": 1, "content": [note]}


def _person(user):
    if not user or not user.get("accountId"):
        return None
    return {"accountId": user["accountId"], "displayName": user.get("displayName", "?"),
            "email": (user.get("emailAddress") or "").strip().lower()}


_USER_CACHE = {}
_UNMATCHED_USERS = {}


def resolve_target_user(person, user_map):
    """accountId tej samej osoby w instancji docelowej albo None."""
    if not person:
        return None
    src_id = person["accountId"]
    if src_id in _USER_CACHE:
        return _USER_CACHE[src_id]
    result = user_map.get(src_id) or (user_map.get(person["email"]) if person["email"] else None)
    if not result:
        resp = request_with_retry("GET", f"{core.JIRA_BASE_URL}/rest/api/3/user", auth=core.jira_auth(),
                                  params={"accountId": src_id})
        if resp.status_code == 200 and resp.json().get("active", True):
            result = src_id  # to samo konto Atlassian w obu instancjach
    if not result and person["email"]:
        resp = request_with_retry("GET", f"{core.JIRA_BASE_URL}/rest/api/3/user/search", auth=core.jira_auth(),
                                  params={"query": person["email"]})
        if resp.status_code == 200:
            hits = [u for u in resp.json() if (u.get("emailAddress") or "").lower() == person["email"]]
            if len(hits) == 1:
                result = hits[0]["accountId"]
    if not result:
        _UNMATCHED_USERS[src_id] = person["displayName"]
    _USER_CACHE[src_id] = result
    return result


def source_get_all_comments(issue_key):
    comments, start = [], 0
    while True:
        resp = request_with_retry("GET", f"{SOURCE_BASE_URL}/rest/api/3/issue/{issue_key}/comment",
                                  auth=source_auth(), params={"startAt": start, "maxResults": 100})
        resp.raise_for_status()
        data = resp.json()
        comments.extend(data.get("comments", []))
        start += len(data.get("comments", []))
        if not data.get("comments") or start >= data.get("total", 0):
            return comments


_TARGET_TYPES_CACHE = {}


def target_issue_types(project_key, level):
    """Typy zgłoszeń danego poziomu (1 = Epic, 0 = standardowe, -1 = Sub-task)
    w projekcie docelowym, buforowane."""
    cache_key = (project_key, level)
    if cache_key not in _TARGET_TYPES_CACHE:
        proj = request_with_retry("GET", f"{core.JIRA_BASE_URL}/rest/api/3/project/{project_key}", auth=core.jira_auth())
        proj.raise_for_status()
        resp = request_with_retry("GET", f"{core.JIRA_BASE_URL}/rest/api/3/issuetype/project", auth=core.jira_auth(),
                                  params={"projectId": proj.json()["id"], "level": level})
        resp.raise_for_status()
        _TARGET_TYPES_CACHE[cache_key] = resp.json()
    return _TARGET_TYPES_CACHE[cache_key]


def resolve_target_type(project_key, issue, mapped_parent):
    """Zwraca (issuetype dla API, rodzic dla API, opis typu do logu)."""
    if issue["level"] >= 1:
        epics = target_issue_types(project_key, 1)
        epic = next((t for t in epics if t["name"].lower() == "epic"), epics[0] if epics else None)
        if epic:
            return {"id": epic["id"]}, None, epic["name"]
        print(f"      [OSTRZEŻENIE] Brak typu Epic w {project_key} - {issue['key']} powstanie jako Task.")
    if issue["level"] < 0:
        if mapped_parent:
            return {"id": cte.get_regular_subtask_type_id(project_key)}, mapped_parent, "Sub-task"
        print(f"      [OSTRZEŻENIE] Rodzic {issue['parent_key']} sub-taska {issue['key']} nie został "
              f"skopiowany - {issue['key']} powstanie jako samodzielny Task.")
        mapped_parent = None
    standard = target_issue_types(project_key, 0)
    same = next((t for t in standard if t["name"].lower() == issue["type_name"].lower()), None)
    chosen = same or next((t for t in standard if t["name"].lower() == "task"), standard[0] if standard else None)
    issuetype = {"id": chosen["id"]} if chosen else {"name": "Task"}
    return issuetype, mapped_parent, (chosen or {}).get("name", "Task")


def _norm_title(t):
    return re.sub(r"\s+", " ", t or "").strip().casefold()


def existing_target_titles(project_key):
    """{znormalizowany tytuł: [klucze]} zgłoszeń już istniejących w projekcie docelowym."""
    titles, token = {}, None
    while True:
        body = {"jql": f'project = "{project_key}" ORDER BY key', "fields": ["summary"], "maxResults": 100}
        if token:
            body["nextPageToken"] = token
        r = request_with_retry("POST", f"{core.JIRA_BASE_URL}/rest/api/3/search/jql", auth=core.jira_auth(),
                               json=body)
        if r.status_code != 200:
            return titles
        data = r.json()
        for i in data.get("issues", []):
            titles.setdefault(_norm_title(i["fields"].get("summary")), []).append(i["key"])
        token = data.get("nextPageToken")
        if not token:
            return titles


def select_for_split(issues, only=None, exclude=None):
    """Zgłoszenia dla jednego projektu docelowego (patrz PODZIAŁ w docstringu)."""
    if not only and not exclude:
        return issues
    only = {s.strip().casefold() for s in only or [] if s.strip()}
    exclude = {s.strip().casefold() for s in exclude or [] if s.strip()}

    def own_match(i):
        st = (i["status_name"] or "").casefold()
        return (st in only) if only else (st not in exclude)

    by_key = {i["key"]: i for i in issues}
    chosen = {i["key"] for i in issues if i["level"] == 0 and own_match(i)}
    for i in issues:  # Sub-taski za rodzicem; bez rodzica w projekcie - według własnego statusu
        if i["level"] < 0:
            if i["parent_key"] in by_key:
                if i["parent_key"] in chosen:
                    chosen.add(i["key"])
            elif own_match(i):
                chosen.add(i["key"])
    for i in issues:  # Epiki: tam, gdzie ich dzieci; bez dzieci - według własnego statusu
        if i["level"] < 1:
            continue
        kids = [c for c in issues if c["parent_key"] == i["key"]]
        if (any(c["key"] in chosen for c in kids)) if kids else own_match(i):
            chosen.add(i["key"])
    return [i for i in issues if i["key"] in chosen]


def target_status_for(issue, status_map, done_status, other_status):
    """Nazwa statusu docelowego: najpierw jawny wpis z --status-map, potem
    reguła kategorii (--done-status / --other-status), inaczej None (domyślny)."""
    if issue["status_name"] in status_map:
        return status_map[issue["status_name"]]
    if issue["status_category"] == "done" and done_status:
        return done_status
    if issue["status_category"] != "done" and other_status:
        return other_status
    return None


def access_plan(lead_account_id, extra_groups):
    """Lista (rola, 'group'|'user', wartości) - ta sama konfiguracja co jira_sync.py."""
    plan = []
    dev_roles = [r.strip() for r in core.DEVELOPER_ROLE_NAME.split(",") if r.strip()]
    dev_users = [a.strip() for a in core.DEVELOPER_ACCOUNT_IDS.split(",") if a.strip()]
    for role in dev_roles:
        if core.DEVELOPER_GROUP_NAME:
            plan.append((role, "group", [core.DEVELOPER_GROUP_NAME]))
        if dev_users:
            plan.append((role, "user", dev_users))
    admins = [a.strip() for a in core.ADMIN_ACCOUNT_ID.split(",") if a.strip()]
    if core.ADD_LEAD_AS_ADMIN and lead_account_id and lead_account_id not in admins:
        admins.append(lead_account_id)
    if admins:
        plan.append((core.ADMIN_ROLE_NAME, "user", admins))
    for spec in extra_groups or []:
        group, _, role = spec.partition("=")
        if group.strip() and role.strip():
            plan.append((role.strip(), "group", [group.strip()]))
    return plan


def apply_project_access(project_key, plan):
    """Dodaje grupy/użytkowników do ról projektu. Już obecni nie są błędem
    (projekt mógł powstać z ustawieniami wzorca albo skrypt jest uruchamiany ponownie)."""
    warnings, role_ids = [], {}
    for role, kind, values in plan:
        if role not in role_ids:
            role_ids[role] = core.get_role_id_by_name(role)
        rid = role_ids[role]
        if not rid:
            warnings.append(f"rola '{role}' nie istnieje w Jirze")
            print(f"    [OSTRZEŻENIE] Rola '{role}' nie istnieje - pomijam.")
            continue
        try:
            if kind == "group":
                for g in values:
                    core.add_group_to_project_role(project_key, rid, g)
                print(f"    Grupa {', '.join(values)} -> rola '{role}'")
            else:
                core.add_users_to_project_role(project_key, rid, values)
                print(f"    {len(values)} użytkownik(ów) -> rola '{role}'")
        except Exception as exc:  # noqa: BLE001
            if "already" in str(exc).lower():
                print(f"    {', '.join(values) if kind == 'group' else str(len(values)) + ' użytk.'} już w roli '{role}'")
                continue
            warnings.append(f"{kind} {values} -> {role}: {exc}")
            print(f"    [OSTRZEŻENIE] {kind} {values} -> rola '{role}': {exc}")
    return warnings


def target_project_exists(key):
    r = request_with_retry("GET", f"{core.JIRA_BASE_URL}/rest/api/3/project/{key}", auth=core.jira_auth())
    return r.status_code == 200


def create_target_project(key, name, lead_account_id, project_type_key="software"):
    if target_project_exists(key):
        print(f"    Projekt '{key}' już istnieje w instancji docelowej - używam go (lead i nazwa bez zmian).")
        return
    if not name or not lead_account_id:
        raise RuntimeError(f"Projekt '{key}' nie istnieje - do jego utworzenia potrzebne są --target-name "
                           f"i --target-lead-account-id.")
    resp = request_with_retry(
        "POST", f"{core.JIRA_BASE_URL}/rest/api/3/project", auth=core.jira_auth(),
        headers={"Content-Type": "application/json"},
        json={
            "key": key,
            "name": name,
            "projectTypeKey": project_type_key,
            "leadAccountId": lead_account_id,
            "assigneeType": "PROJECT_LEAD",
        },
    )
    if resp.status_code == 400 and "already exists" in resp.text.lower():
        print(f"    Projekt '{key}' już istnieje w instancji docelowej - używam go.")
        return
    if resp.status_code >= 300:
        raise RuntimeError(f"Błąd tworzenia projektu docelowego: {resp.status_code} {resp.text[:300]}")
    print(f"    Utworzono projekt docelowy '{key}'.")


def get_target_status_id_by_name(project_key, status_name):
    resp = request_with_retry("GET", f"{core.JIRA_BASE_URL}/rest/api/3/project/{project_key}/statuses", auth=core.jira_auth())
    resp.raise_for_status()
    for issue_type in resp.json():
        for status in issue_type.get("statuses", []):
            if status.get("name", "").strip().lower() == status_name.strip().lower():
                return status["id"]
    return None


_FIELD_DISABLED = {"reporter": False, "assignee": False}


def sanitize_adf(node):
    """Kopia opisu ADF bez elementów wskazujących na INNĄ instancję Jiry (Jira je
    odrzuca przy tworzeniu): wzmianki, osadzone pliki/obrazki, karty linków, emoji,
    statusy, daty -> zwykły tekst. Reszta struktury (akapity, listy, formatowanie)
    zostaje bez zmian."""
    def text(t, marks=None):
        n = {"type": "text", "text": t or " "}
        if marks:
            n["marks"] = marks
        return n

    def walk(n):
        if isinstance(n, list):
            out = []
            for c in n:
                r = walk(c)
                if r is None:
                    continue
                out.extend(r if isinstance(r, list) else [r])
            return out
        if not isinstance(n, dict):
            return n
        t, attrs = n.get("type"), n.get("attrs") or {}
        if t == "mention":
            return text(attrs.get("text") or "@?")
        if t in ("emoji",):
            return text(attrs.get("text") or attrs.get("shortName") or "")
        if t == "status":
            return text(f"[{attrs.get('text', '')}]")
        if t == "date":
            try:
                return text(datetime.fromtimestamp(int(attrs.get("timestamp")) / 1000).strftime("%Y-%m-%d"))
            except Exception:  # noqa: BLE001
                return text("[Datum]")
        if t in ("inlineCard", "blockCard", "embedCard"):
            url = attrs.get("url") or ""
            node = text(url or "[Link]", [{"type": "link", "attrs": {"href": url}}] if url else None)
            return node if t == "inlineCard" else {"type": "paragraph", "content": [node]}
        if t in ("media", "mediaInline"):
            label = attrs.get("alt") or attrs.get("fileName") or "Datei"
            return text(f"[Anhang: {label}]")
        if t in ("mediaSingle", "mediaGroup"):
            inner = walk(n.get("content") or [])
            return {"type": "paragraph", "content": inner or [text("[Anhang]")]}
        new = {k: v for k, v in n.items() if k != "content"}
        if "content" in n:
            new["content"] = walk(n["content"])
        return new

    return walk(node) if node else node


_PROJECT_PRIORITIES: dict = {}
_MISSING_PRIORITIES = set()


def project_priorities(project_key):
    """Priorytety DOZWOLONE w projekcie (schemat priorytetów projektu), z metadanych
    tworzenia - lista globalna /priority zawiera też takie, których projekt nie przyjmie."""
    if project_key not in _PROJECT_PRIORITIES:
        allowed = {}
        r = request_with_retry("GET", f"{core.JIRA_BASE_URL}/rest/api/3/issue/createmeta", auth=core.jira_auth(),
                               params={"projectKeys": project_key, "expand": "projects.issuetypes.fields"})
        if r.status_code == 200:
            for proj in r.json().get("projects") or []:
                for it in proj.get("issuetypes") or []:
                    for opt in ((it.get("fields") or {}).get("priority") or {}).get("allowedValues") or []:
                        allowed[opt["name"].casefold()] = opt["id"]
        _PROJECT_PRIORITIES[project_key] = allowed
    return _PROJECT_PRIORITIES[project_key]


def target_priority(name, project_key):
    """{"id"} priorytetu dozwolonego w projekcie albo None (zapamiętuje brakujące)."""
    if not name:
        return None
    pid = project_priorities(project_key).get(name.casefold())
    if not pid:
        _MISSING_PRIORITIES.add(name)
        return None
    return {"id": pid}


def create_target_issue(project_key, summary, description, issuetype=None, parent_key=None, is_epic=False,
                        reporter_id=None, assignee_id=None, origin_note=None, labels=None, priority_name=None):
    clean_summary = summary.replace("\n", " ").strip() or "(bez tytułu)"
    body_fields = {
        "project": {"key": project_key},
        "summary": clean_summary,
        "issuetype": issuetype or {"name": "Task"},
    }
    if parent_key:
        body_fields["parent"] = {"key": parent_key}
    full_description = with_origin_note(description, origin_note)
    if full_description:
        body_fields["description"] = full_description
    if reporter_id and not _FIELD_DISABLED["reporter"]:
        body_fields["reporter"] = {"accountId": reporter_id}
    if assignee_id and not _FIELD_DISABLED["assignee"]:
        body_fields["assignee"] = {"accountId": assignee_id}
    if labels:
        body_fields["labels"] = [l.replace(" ", "_") for l in labels]  # etykiety w Jirze nie mają spacji
    prio = target_priority(priority_name, project_key)
    if prio:
        body_fields["priority"] = prio

    def post(fields):
        return request_with_retry(
            "POST", f"{core.JIRA_BASE_URL}/rest/api/3/issue", auth=core.jira_auth(),
            headers={"Content-Type": "application/json"}, json={"fields": fields},
        )

    resp = post(body_fields)
    for field in ("reporter", "assignee", "labels", "priority"):
        if resp.status_code >= 300 and field in body_fields and f'"{field}"' in resp.text:
            # brak uprawnienia (Modify Reporter / Assignable User) albo pola na ekranie
            body_fields.pop(field)
            if field == "reporter":
                _FIELD_DISABLED["reporter"] = True  # dotyczy całego projektu - nie próbujemy dalej
                print(f"      [OSTRZEŻENIE] Jira nie przyjmuje zgłaszającego ({resp.text[:150]}). Brak uprawnienia "
                      f"'Modify Reporter'? Dalsze zgłoszenia powstaną z kontem API jako zgłaszającym.")
            elif field == "assignee":
                print(f"      [OSTRZEŻENIE] Osoby nie da się przypisać w tym projekcie ({resp.text[:120]}) - bez przypisania.")
            else:
                print(f"      [OSTRZEŻENIE] Pole '{field}' odrzucone przez Jirę ({resp.text[:120]}) - utworzono bez niego.")
            resp = post(body_fields)
    if resp.status_code >= 300 and is_epic and "epic name" in resp.text.lower():
        # starsze projekty company-managed wymagają pola "Epic Name" przy tworzeniu Epika
        body_fields[cte.get_epic_name_field_id()] = clean_summary[:255]
        resp = post(body_fields)
    if resp.status_code >= 300 and description:
        # opis z innej instancji bywa odrzucany (obrazki/wzmianki z jej ID) - najpierw
        # oczyszczony opis (te elementy jako tekst), dopiero potem sama notka
        body_fields["description"] = with_origin_note(sanitize_adf(description), origin_note)
        retry = post(body_fields)
        if retry.status_code < 300:
            print("      [INFO] Opis oczyszczony (obrazki/wzmianki z innej instancji zamienione na tekst).")
            return retry.json()["key"]
        fallback = with_origin_note(None, origin_note)
        if fallback:
            body_fields["description"] = fallback
        else:
            body_fields.pop("description", None)
        retry = post(body_fields)
        if retry.status_code < 300:
            print(f"      [OSTRZEŻENIE] Opis odrzucony przez Jirę ({resp.status_code}) - utworzono bez oryginalnego opisu.")
            return retry.json()["key"]
    if resp.status_code >= 300:
        raise RuntimeError(f"Błąd tworzenia zadania: {resp.status_code} {resp.text[:300]}")
    return resp.json()["key"]


def refresh_existing_issue(target_key, issue, origin_note):
    """Uzupełnia już skopiowane zgłoszenie: etykiety (dopisywane), priorytet oraz opis -
    ten ostatni TYLKO, gdy w celu jest dziś sama notka o pochodzeniu (opis był odrzucony).
    Status, przypisany i inne pola bez zmian. Zwraca listę zmienionych pól."""
    r = request_with_retry("GET", f"{core.JIRA_BASE_URL}/rest/api/3/issue/{target_key}", auth=core.jira_auth(),
                           params={"fields": "description,labels,priority"})
    r.raise_for_status()
    f = r.json()["fields"]
    update, changed = {}, []
    want_labels = sorted(set(f.get("labels") or []) | {l.replace(" ", "_") for l in issue.get("labels") or []})
    if want_labels != sorted(f.get("labels") or []):
        update["labels"] = want_labels
        changed.append("etykiety")
    prio = target_priority(issue.get("priority"), target_key.rsplit("-", 1)[0])
    if prio and (f.get("priority") or {}).get("id") != prio["id"]:
        update["priority"] = prio
        changed.append("priorytet")
    blocks = (f.get("description") or {}).get("content") or []
    only_note = len(blocks) <= 1 and issue.get("description")
    attempts = []
    if only_note:
        attempts = [with_origin_note(issue["description"], origin_note),
                    with_origin_note(sanitize_adf(issue["description"]), origin_note)]
    def put(fields):
        return request_with_retry("PUT", f"{core.JIRA_BASE_URL}/rest/api/3/issue/{target_key}",
                                  auth=core.jira_auth(), params={"notifyUsers": "false"}, json={"fields": fields})

    for desc in attempts or [None]:
        body = dict(update)
        if desc:
            body["description"] = desc
        if not body:
            return []
        resp = put(body)
        for field in ("priority", "labels"):
            # pole odrzucone (schemat, ekran) - zapisz resztę bez niego
            if resp.status_code >= 300 and field in body and f'"{field}"' in resp.text:
                body.pop(field)
                update.pop(field, None)
                changed = [c for c in changed if c != {"priority": "priorytet", "labels": "etykiety"}[field]]
                resp = put(body) if body else resp
        if resp.status_code < 300:
            return changed + (["opis"] if desc else [])
    # opis nie przeszedł w żadnej postaci - zapisz przynajmniej resztę
    if update:
        resp = put(update)
        if resp.status_code < 300:
            return changed
    raise RuntimeError(f"nie udało się uzupełnić {target_key}: {resp.status_code} {resp.text[:150]}")


def transition_target_issue(target_key, status_id):
    resp = request_with_retry("GET", f"{core.JIRA_BASE_URL}/rest/api/3/issue/{target_key}/transitions", auth=core.jira_auth())
    resp.raise_for_status()
    transition_id = None
    for t in resp.json().get("transitions", []):
        if t.get("to", {}).get("id") == status_id:
            transition_id = t["id"]
            break
    if not transition_id:
        print(f"      [OSTRZEŻENIE] Brak dostępnego przejścia do statusu docelowego dla {target_key} - zostaje przy domyślnym.")
        return
    resp = request_with_retry(
        "POST", f"{core.JIRA_BASE_URL}/rest/api/3/issue/{target_key}/transitions", auth=core.jira_auth(),
        headers={"Content-Type": "application/json"}, json={"transition": {"id": transition_id}},
    )
    if resp.status_code >= 300:
        print(f"      [OSTRZEŻENIE] Nie udało się zmienić statusu {target_key}: {resp.status_code} {resp.text[:150]}")


def copy_comments(source_key, target_key, comments, synced_ids):
    """Dodaje komentarze źródłowe na docelowym zadaniu, z prefiksem
    COMMENT_PREFIX_TEMPLATE ('[Übernommen aus ... · autor, data]') (Jira nie pozwala podszyć się
    pod innego autora przez REST API, tak samo jak przy synchronizacji
    z Asany - patrz asana_jira_sync.py). Pomija już zsynchronizowane
    (po id komentarza źródłowego), idempotentnie."""
    new_synced = list(synced_ids)
    for c in comments:
        if c["id"] in synced_ids:
            continue
        prefix = COMMENT_PREFIX_TEMPLATE.format(source=SOURCE_BASE_URL.replace("https://", ""), key=source_key,
                                                author=c["author"], created=(c["created"] or "")[:16].replace("T", " "))
        text_body = _adf_to_plain_text(c["body"])
        resp = request_with_retry(
            "POST", f"{core.JIRA_BASE_URL}/rest/api/3/issue/{target_key}/comment", auth=core.jira_auth(),
            headers={"Content-Type": "application/json"},
            json={"body": {
                "type": "doc", "version": 1,
                "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": prefix, "marks": [{"type": "em"}]}]},
                    {"type": "paragraph", "content": [{"type": "text", "text": text_body or EMPTY_COMMENT}]},
                ],
            }},
        )
        if resp.status_code >= 300:
            print(f"      [OSTRZEŻENIE] Nie udało się skopiować komentarza {c['id']}: {resp.status_code} {resp.text[:150]}")
            continue
        new_synced.append(c["id"])
    return new_synced


def _adf_to_plain_text(adf_body):
    """Bardzo uproszczone wyciągnięcie tekstu z ADF - wystarczające dla
    typowych, prostych komentarzy. Złożone formatowanie (tabele, listy
    zagnieżdżone) nie jest w pełni odwzorowane - to świadomy kompromis."""
    if not adf_body or not isinstance(adf_body, dict):
        return ""
    parts = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "text":
                parts.append(node.get("text", ""))
            for child in node.get("content", []) or []:
                walk(child)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(adf_body)
    return " ".join(parts)


def copy_attachments(source_key, target_key, attachments, synced_ids):
    new_synced = list(synced_ids)
    for a in attachments:
        if a["id"] in synced_ids:
            continue
        if not a["content_url"]:
            continue
        download = request_with_retry("GET", a["content_url"], auth=source_auth())
        if download.status_code >= 300:
            print(f"      [OSTRZEŻENIE] Nie udało się pobrać załącznika {a['filename']}: {download.status_code}")
            continue
        upload = request_with_retry(
            "POST", f"{core.JIRA_BASE_URL}/rest/api/3/issue/{target_key}/attachments", auth=core.jira_auth(),
            headers={"X-Atlassian-Token": "no-check"},
            files={"file": (a["filename"], download.content)},
        )
        if upload.status_code >= 300:
            print(f"      [OSTRZEŻENIE] Nie udało się wysłać załącznika {a['filename']}: {upload.status_code} {upload.text[:150]}")
            continue
        new_synced.append(a["id"])
    return new_synced


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-key", required=True)
    parser.add_argument("--target-key", required=True)
    parser.add_argument("--target-name", default=None,
                        help="Nazwa projektu docelowego - potrzebna tylko, gdy projekt jeszcze nie istnieje.")
    parser.add_argument("--target-lead-account-id", default=None,
                        help="Lead projektu docelowego - potrzebny tylko, gdy projekt jeszcze nie istnieje.")
    parser.add_argument("--update-status", action="store_true",
                        help="Dla już skopiowanych: ustaw status według obecnego statusu w źródle (gdy się zmienił).")
    parser.add_argument("--skip-existing-titles", action="store_true",
                        help="Nie twórz zgłoszeń, których tytuł już istnieje w projekcie docelowym (niepustym).")
    parser.add_argument("--refresh-existing", action="store_true",
                        help="Uzupełnij już skopiowane zgłoszenia: etykiety, priorytet, opis (gdy jest tylko notka).")
    parser.add_argument("--only-statuses", default=None,
                        help="Tylko zgłoszenia w tych statusach ŹRÓDŁA (po przecinku) - podział na kilka projektów.")
    parser.add_argument("--exclude-statuses", default=None,
                        help="Wszystkie zgłoszenia OPRÓCZ tych statusów źródła (po przecinku).")
    parser.add_argument("--status-map", default=None, help="Plik JSON {źródłowy status: docelowy status} (ma pierwszeństwo).")
    parser.add_argument("--extra-group", action="append", default=[],
                        help='Dodatkowa grupa do roli projektu, format "Nazwa grupy=Nazwa roli" (można powtarzać).')
    parser.add_argument("--no-origin-note", action="store_true",
                        help="Nie dodawaj na początku opisu notki o pochodzeniu (klucz, data, zgłaszający, przypisany).")
    parser.add_argument("--skip-access", action="store_true", help="Nie dodawaj grup/użytkowników do ról projektu.")
    parser.add_argument("--user-map", default=None,
                        help="Plik JSON {accountId albo email ze źródła: accountId u nas} - dla osób, których nie da się dopasować automatycznie.")
    parser.add_argument("--done-status", default=None, help="Status docelowy dla zgłoszeń z kategorii Done w źródle (np. Done).")
    parser.add_argument("--other-status", default=None, help="Status docelowy dla wszystkich pozostałych (np. Misc).")
    parser.add_argument("--skip-boards", action="store_true", help="Pomiń kopiowanie tablic/kolumn/statusów, skopiuj tylko projekt i zadania.")
    parser.add_argument("--source-board-ids", type=str, default=None, help="Lista ID tablic źródłowych po przecinku - pomija wolne automatyczne wykrywanie.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not SOURCE_BASE_URL or not SOURCE_EMAIL or not SOURCE_API_TOKEN:
        print("[BŁĄD] Brak SOURCE_JIRA_BASE_URL / SOURCE_JIRA_EMAIL / SOURCE_JIRA_API_TOKEN w .env.")
        sys.exit(1)

    if not (args.status_map or args.done_status or args.other_status):
        print("[BŁĄD] Podaj --done-status/--other-status albo --status-map.")
        sys.exit(1)
    status_map = load_status_map(args.status_map) if args.status_map else {}
    if status_map:
        print(f"Mapowanie statusów wczytane: {len(status_map)} wpis(ów).")
    if args.done_status or args.other_status:
        print(f"Reguła kategorii: Done -> {args.done_status or '(bez zmiany)'}, "
              f"pozostałe -> {args.other_status or '(bez zmiany)'}")

    # Bez ważnego logowania Jira zwraca w wyszukiwaniu PUSTĄ listę (jak dla anonimowego),
    # co wyglądałoby na "0 zadań w źródle" - sprawdzamy dostęp wprost.
    me = request_with_retry("GET", f"{SOURCE_BASE_URL}/rest/api/3/myself", auth=source_auth())
    if me.status_code != 200:
        print(f"\n[BŁĄD] Instancja źródłowa ({SOURCE_BASE_URL}) odrzuca logowanie: HTTP {me.status_code}. "
              f"Token wygasł/został cofnięty, konto straciło dostęp albo instancja zmieniła adres. "
              f"Wygeneruj nowy token (zwykły, nie 'z zakresami') i podmień SOURCE_JIRA_API_TOKEN w .env. "
              f"Nic nie zostało zmienione.")
        sys.exit(1)
    proj = request_with_retry("GET", f"{SOURCE_BASE_URL}/rest/api/3/project/{args.source_key}", auth=source_auth())
    if proj.status_code != 200:
        print(f"\n[BŁĄD] Projekt {args.source_key} nie jest widoczny w instancji źródłowej (HTTP {proj.status_code}): "
              f"brak uprawnień, zmiana klucza albo projekt usunięty. Nic nie zostało zmienione.")
        sys.exit(1)

    print(f"\nPobieram zadania ze źródła ({args.source_key}, {SOURCE_BASE_URL})...")
    issues = fetch_source_issues(args.source_key)
    print(f"  {len(issues)} zadań.")
    split = bool(args.only_statuses or args.exclude_statuses)
    if split:
        all_statuses = sorted({i["status_name"] for i in issues})
        print(f"  Statusy w źródle: {', '.join(all_statuses)}")
        wanted = [s for s in (args.only_statuses or "").split(",") if s.strip()]
        unknown = [s.strip() for s in wanted if s.strip().casefold() not in {x.casefold() for x in all_statuses}]
        if unknown:
            print(f"  [UWAGA] Statusów z --only-statuses nie ma w źródle: {', '.join(unknown)} "
                  f"(to mogą być nazwy kolumn, a nie statusów - porównaj z listą powyżej).")
        issues = select_for_split(issues, wanted, [s for s in (args.exclude_statuses or "").split(",") if s.strip()])
        print(f"  Wybrane do {args.target_key}: {len(issues)} zgłoszeń "
              f"(Epiki: {sum(i['level'] >= 1 for i in issues)}, standardowe: {sum(i['level'] == 0 for i in issues)}, "
              f"Sub-taski: {sum(i['level'] < 0 for i in issues)})")

    # Już skopiowane (stan kopiowania) - przy kopiowaniu przyrostowym nie są "nowe".
    copied_before = load_state().get(f"{args.source_key}>{args.target_key}" if split else args.source_key, {}) or {}
    if split:
        # Zadanie, które w źródle zmieniło grupę statusów, jest już skopiowane do INNEGO projektu
        # z tego samego źródła - nie tworzymy drugiej kopii (Epiki mogą być w kilku celowo).
        elsewhere = {}
        for skey, entries in load_state().items():
            if skey.startswith(f"{args.source_key}>") and skey != f"{args.source_key}>{args.target_key}":
                for src, e in (entries or {}).items():
                    if isinstance(e, dict) and e.get("target_key"):
                        elsewhere[src] = e["target_key"]
        moved = [i for i in issues if i["key"] in elsewhere and i["key"] not in copied_before and i["level"] < 1]
        if moved:
            print(f"\nPomijam {len(moved)} zadań skopiowanych już do innego projektu (zmieniły grupę statusów w źródle):")
            for i in moved[:15]:
                print(f"    {i['key']} [{i['status_name']}] -> już jest: {elsewhere[i['key']]} | {i['summary'][:50]}")
            if len(moved) > 15:
                print(f"    ... i jeszcze {len(moved) - 15}")
            moved_keys = {i["key"] for i in moved}
            issues = [i for i in issues if i["key"] not in moved_keys]
    if target_project_exists(args.target_key):
        existing = existing_target_titles(args.target_key)
        clash = [i for i in issues if i["key"] not in copied_before and _norm_title(i["summary"]) in existing]
        total_existing = sum(len(v) for v in existing.values())
        print(f"\nProjekt docelowy {args.target_key} ma już {total_existing} zgłoszeń. "
              f"Zadań ze źródła z tytułem, który już tam jest: {len(clash)}")
        for i in clash[:10]:
            print(f"    {i['key']} '{i['summary'][:60]}' -> już jest: {', '.join(existing[_norm_title(i['summary'])][:3])}")
        if len(clash) > 10:
            print(f"    ... i jeszcze {len(clash) - 10}")
        if clash and args.skip_existing_titles:
            clash_keys = {i["key"] for i in clash}
            # dzieci pomijanych też pomijamy (nie miałyby rodzica w celu)
            issues = [i for i in issues if i["key"] not in clash_keys and i.get("parent_key") not in clash_keys]
            print(f"  --skip-existing-titles: pomijam je (i ich podzadania). Do utworzenia: {len(issues)}")
        elif clash:
            print("  Jeśli to te same zadania (przeniesione wcześniej), dodaj --skip-existing-titles, "
                  "żeby ich nie dublować.")

    for i in issues:
        i["target_status"] = target_status_for(i, status_map, args.done_status, args.other_status)
    by_target = {}
    for i in issues:
        by_target.setdefault(i["target_status"] or "(domyślny)", set()).add(i["status_name"])
    print("\nPlan statusów (docelowy <- statusy źródłowe):")
    for tgt, srcs in sorted(by_target.items()):
        n = sum(1 for i in issues if (i["target_status"] or "(domyślny)") == tgt)
        print(f"  {tgt}: {n} zgłoszeń <- {', '.join(sorted(srcs))}")
    user_map = json.loads(Path(args.user_map).read_text(encoding="utf-8")) if args.user_map else {}
    people = {}
    for i in issues:
        for role in ("reporter", "assignee"):
            if i[role]:
                people[i[role]["accountId"]] = i[role]
    matched = {pid: resolve_target_user(p, user_map) for pid, p in people.items()}
    print(f"\nLudzie: {sum(1 for v in matched.values() if v)} z {len(people)} dopasowanych w {core.JIRA_BASE_URL}.")
    if _UNMATCHED_USERS:
        print("  Niedopasowani (pole zostanie puste/domyślne; dodaj do --user-map, jeśli mają konto u nas):")
        for pid, name in sorted(_UNMATCHED_USERS.items(), key=lambda x: x[1]):
            print(f"    {name}  (accountId w źródle: {pid})")

    unmapped_statuses = sorted({i["status_name"] for i in issues if not i["target_status"]})
    if unmapped_statuses:
        print(f"\n[OSTRZEŻENIE] Statusy bez wpisu w mapowaniu (zadania zostaną utworzone bez zmiany statusu): {unmapped_statuses}")

    plan = [] if args.skip_access else access_plan(args.target_lead_account_id, args.extra_group)
    if plan:
        print("\nUprawnienia projektu docelowego (jak w migracji, z .env):")
        for role, kind, values in plan:
            label = f"grupa {', '.join(values)}" if kind == "group" else f"{len(values)} użytkownik(ów)"
            print(f"  rola '{role}' <- {label}")
    elif not args.skip_access:
        print("\n[UWAGA] W .env nie ma DEVELOPER_GROUP_NAME / ADMIN_ACCOUNT_ID - żadne grupy nie zostaną dodane.")

    if args.dry_run:
        by_level = {1: 0, 0: 0, -1: 0}
        for i in issues:
            by_level[max(-1, min(1, i["level"]))] += 1
        orphans = [i["key"] for i in issues if i["level"] < 0 and i["parent_key"] not in {x["key"] for x in issues}]
        project_note = ("użyłbym istniejącego projektu" if target_project_exists(args.target_key)
                        else "utworzyłbym projekt")
        new_issues = [i for i in issues if i["key"] not in copied_before]
        by_level = {1: 0, 0: 0, -1: 0}
        for i in new_issues:
            by_level[max(-1, min(1, i["level"]))] += 1
        status_changes = [i for i in issues if i["key"] in copied_before and i.get("target_status")
                          and copied_before[i["key"]].get("status_applied")
                          and copied_before[i["key"]]["status_applied"] != i["target_status"]]
        status_unknown = [i for i in issues if i["key"] in copied_before and i.get("target_status")
                          and not copied_before[i["key"]].get("status_applied")]
        print(f"\n[DRY-RUN] {project_note[0].upper() + project_note[1:]} '{args.target_key}' w {core.JIRA_BASE_URL}:")
        print(f"  NOWE do utworzenia: {len(new_issues)} (Epiki: {by_level[1]}, standardowe: {by_level[0]}, Sub-taski: {by_level[-1]})")
        for i in new_issues[:15]:
            print(f"      {i['key']} [{i['status_name']}] {i['summary'][:60]}")
        print(f"  Już skopiowane (tylko nowe komentarze/załączniki): {len(issues) - len(new_issues)}")
        if args.update_status:
            print(f"  Zmiana statusu już skopiowanych (--update-status): {len(status_changes)}")
            if status_unknown:
                print(f"  Skopiowane przed --update-status (status zostanie sprawdzony i ustawiony, jeśli się różni): "
                      f"{len(status_unknown)}")
            for i in status_changes[:15]:
                print(f"      {copied_before[i['key']]['target_key']}: -> {i['target_status']}  ({i['key']}: {i['status_name']})")
        if orphans:
            print(f"  [OSTRZEŻENIE] Sub-taski z rodzicem spoza projektu (powstaną jako Taski): {', '.join(orphans[:10])}")
        for name in sorted({i["target_status"] for i in issues if i["target_status"]}):
            try:
                found = ajs.get_status_id_by_name(args.target_key, name)
                print(f"  Status '{name}' w {args.target_key}: {'OK' if found else 'BRAK - dodaj go do workflow przed kopiowaniem'}")
            except Exception:  # noqa: BLE001
                print(f"  Status '{name}': projekt {args.target_key} jeszcze nie istnieje - sprawdzę po jego utworzeniu.")
        print("  Nic nie zrobiono.")
        return

    print(f"\nTworzę projekt docelowy ({args.target_key}, {core.JIRA_BASE_URL})...")
    create_target_project(args.target_key, args.target_name, args.target_lead_account_id)
    access_warnings = []
    if plan:
        print("\nDodaję uprawnienia do projektu (przed kopiowaniem, żeby przypisani byli dozwoleni)...")
        access_warnings = apply_project_access(args.target_key, plan)

    # Statusy docelowe muszą istnieć w workflow projektu - inaczej każde zgłoszenie
    # zostałoby w statusie początkowym. Sprawdzamy PRZED skopiowaniem czegokolwiek.
    status_ids = {}
    for name in sorted({i["target_status"] for i in issues if i["target_status"]}):
        status_ids[name] = ajs.get_status_id_by_name(args.target_key, name)
    missing = [n for n, sid in status_ids.items() if not sid]
    if missing:
        print(f"\n[BŁĄD] W workflow projektu {args.target_key} brak statusów: {', '.join(missing)}. "
              f"Dodaj je do workflow (albo użyj innej nazwy) i uruchom ponownie. Nic nie zostało skopiowane.")
        sys.exit(1)

    state = load_state()
    # przy podziale osobny stan dla pary źródło>cel (ten sam Epik w dwóch projektach)
    project_state = state.setdefault(f"{args.source_key}>{args.target_key}" if split else args.source_key, {})

    legacy = [k for k, e in project_state.items() if isinstance(e, dict) and "level" not in e]
    if legacy:
        print(f"\n[OSTRZEŻENIE] {len(legacy)} zgłoszeń skopiowano wcześniej starszą wersją skryptu "
              f"(bez zachowania typu i rodzica). Nie są poprawiane automatycznie - sprawdź je w {args.target_key}.")

    # Rodzic zawsze przed dzieckiem: Epiki, potem standardowe, na końcu Sub-taski.
    issues.sort(key=lambda i: -i["level"])
    type_counts = {}
    status_failed = []

    created, updated_comments, updated_attachments, errors = 0, 0, 0, []
    refreshed = status_updated = 0
    for i, issue in enumerate(issues, 1):
        try:
            entry = project_state.get(issue["key"])
            if entry:
                target_key = entry["target_key"]
                print(f"[{i}/{len(issues)}] {issue['key']} -> {target_key} (już utworzone, sprawdzam komentarze/załączniki)")
                wanted = issue.get("target_status")
                if args.update_status and wanted and entry.get("status_applied") != wanted:
                    try:
                        if ajs.transition_jira_issue_to_status(target_key, status_ids[wanted]):
                            entry["status_applied"] = wanted
                            status_updated += 1
                            save_state(state)
                            print(f"      status -> {wanted}")
                        else:
                            status_failed.append((target_key, wanted))
                    except Exception as exc:  # noqa: BLE001
                        status_failed.append((target_key, wanted))
                        print(f"      [OSTRZEŻENIE] {target_key}: nie udało się ustawić statusu: {exc}")
                if args.refresh_existing:
                    done = refresh_existing_issue(target_key, issue,
                                                  None if args.no_origin_note else origin_note_paragraph(issue))
                    if done:
                        refreshed += 1
                        print(f"      uzupełniono: {', '.join(done)}")
            else:
                mapped_parent = (project_state.get(issue["parent_key"]) or {}).get("target_key") if issue["parent_key"] else None
                issuetype, parent_for_api, type_label = resolve_target_type(args.target_key, issue, mapped_parent)
                target_key = create_target_issue(args.target_key, issue["summary"], issue["description"],
                                                 issuetype=issuetype, parent_key=parent_for_api,
                                                 is_epic=issue["level"] >= 1 and type_label.lower() == "epic",
                                                 reporter_id=resolve_target_user(issue["reporter"], user_map),
                                                 assignee_id=resolve_target_user(issue["assignee"], user_map),
                                                 origin_note=None if args.no_origin_note else origin_note_paragraph(issue),
                                                 labels=issue.get("labels"), priority_name=issue.get("priority"))
                type_counts[type_label] = type_counts.get(type_label, 0) + 1
                # Zapis stanu NATYCHMIAST po utworzeniu - wcześniej był dopiero po
                # ustawieniu statusu, więc błąd w tym kroku zostawiał zgłoszenie w Jirze
                # bez wpisu w stanie, a ponowne uruchomienie tworzyło duplikat.
                entry = {"target_key": target_key, "synced_comment_ids": [], "synced_attachment_ids": [],
                         "level": issue["level"], "target_parent": parent_for_api}
                project_state[issue["key"]] = entry
                save_state(state)
                mapped_status = issue["target_status"]
                try:
                    if mapped_status and not ajs.transition_jira_issue_to_status(target_key, status_ids[mapped_status]):
                        status_failed.append((target_key, mapped_status))
                        print(f"      [OSTRZEŻENIE] {target_key}: status '{mapped_status}' nieosiągalny w workflow - zostaje domyślny.")
                    elif mapped_status:
                        entry["status_applied"] = mapped_status
                        save_state(state)
                except Exception as exc:  # noqa: BLE001
                    status_failed.append((target_key, mapped_status))
                    print(f"      [OSTRZEŻENIE] {target_key}: nie udało się ustawić statusu: {exc}")
                created += 1
                print(f"[{i}/{len(issues)}] {issue['key']} -> Utworzono {target_key} [{type_label}"
                      + (f" pod {parent_for_api}" if parent_for_api else "")
                      + f"] (status: {issue['status_name']} -> {mapped_status or 'domyślny'})")

            new_comment_ids = copy_comments(issue["key"], target_key, issue["comments"], entry["synced_comment_ids"])
            if len(new_comment_ids) != len(entry["synced_comment_ids"]):
                updated_comments += len(new_comment_ids) - len(entry["synced_comment_ids"])
                entry["synced_comment_ids"] = new_comment_ids
                save_state(state)

            new_attachment_ids = copy_attachments(issue["key"], target_key, issue["attachments"], entry["synced_attachment_ids"])
            if len(new_attachment_ids) != len(entry["synced_attachment_ids"]):
                updated_attachments += len(new_attachment_ids) - len(entry["synced_attachment_ids"])
                entry["synced_attachment_ids"] = new_attachment_ids
                save_state(state)
        except Exception as exc:  # noqa: BLE001
            print(f"[{i}/{len(issues)}] [BŁĄD] {issue['key']}: {exc}")
            errors.append((issue["key"], str(exc)))

    print("\n" + "=" * 60)
    print("PODSUMOWANIE ZADAŃ")
    print(f"  Nowo utworzonych zadań: {created}"
          + (" (" + ", ".join(f"{t}: {n}" for t, n in sorted(type_counts.items())) + ")" if type_counts else ""))
    if access_warnings:
        print(f"  Uprawnienia z ostrzeżeniami: {len(access_warnings)} ({'; '.join(access_warnings)[:200]})")
    if args.refresh_existing:
        print(f"  Uzupełnionych istniejących: {refreshed}")
    if args.update_status:
        print(f"  Zmienionych statusów istniejących: {status_updated}")
    if _MISSING_PRIORITIES:
        print(f"  Priorytety ze źródła nieznane u nas (pole zostało domyślne): {', '.join(sorted(_MISSING_PRIORITIES))}")
    print(f"  Status nieustawiony: {len(status_failed)}"
          + (f" (np. {', '.join(k for k, _ in status_failed[:5])})" if status_failed else ""))
    print(f"  Nowych komentarzy: {updated_comments}")
    print(f"  Nowych załączników: {updated_attachments}")
    print(f"  Błędy: {len(errors)}")
    print("=" * 60)
    if errors:
        for key, err in errors:
            print(f"  {key}: {err}")

    if not args.skip_boards:
        source_name_full = source_get_project_name(args.source_key)
        extra_board_ids = [b.strip() for b in args.source_board_ids.split(",")] if args.source_board_ids else None
        effective_map = {i["status_name"]: i["target_status"] for i in issues if i["target_status"]}
        copy_boards(args.source_key, args.target_key, source_name_full, effective_map, extra_board_ids)

    print(f"\nZakończono. Nowy projekt w FIRMA: {args.target_key}")


if __name__ == "__main__":
    main()
