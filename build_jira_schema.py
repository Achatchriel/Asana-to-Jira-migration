#!/usr/bin/env python3
"""
build_jira_schema.py

Buduje konfigurację Jiry OD ZERA na podstawie deklaratywnego schematu YAML:
custom fields -> statusy -> workflow -> workflow scheme -> ekrany ->
screen scheme -> issue type screen scheme -> (opcjonalnie) podpięcie do projektów.

IDEMPOTENTNY: przed utworzeniem każdego elementu sprawdza, czy obiekt o tej
nazwie już istnieje - jeśli tak, używa go ponownie zamiast tworzyć duplikat.
Mapowanie nazwa -> ID zapisywane w jira_schema_state.json (dla podglądu
i debugowania; skrypt i tak sprawdza stan faktyczny w Jirze przy każdym
uruchomieniu, więc skasowanie tego pliku niczego nie psuje).

Użycie:
    python build_jira_schema.py --schema jira_schema.yaml --dry-run
    python build_jira_schema.py --schema jira_schema.yaml
    python build_jira_schema.py --schema jira_schema.yaml --only custom_fields
"""
import argparse
import json
import sys
import time
import uuid
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core
from notify import setup_file_logging, send_slack_summary

STATE_FILE = Path(__file__).resolve().parent / "jira_schema_state.json"

# Mapowanie przyjaznych nazw typów na pełne klucze Jiry
FIELD_TYPE_MAP = {
    "textfield": ("com.atlassian.jira.plugin.system.customfieldtypes:textfield",
                  "com.atlassian.jira.plugin.system.customfieldtypes:textsearcher"),
    "textarea": ("com.atlassian.jira.plugin.system.customfieldtypes:textarea",
                 "com.atlassian.jira.plugin.system.customfieldtypes:textsearcher"),
    "select": ("com.atlassian.jira.plugin.system.customfieldtypes:select",
               "com.atlassian.jira.plugin.system.customfieldtypes:multiselectsearcher"),
    "multiselect": ("com.atlassian.jira.plugin.system.customfieldtypes:multiselect",
                    "com.atlassian.jira.plugin.system.customfieldtypes:multiselectsearcher"),
    "datepicker": ("com.atlassian.jira.plugin.system.customfieldtypes:datepicker",
                   "com.atlassian.jira.plugin.system.customfieldtypes:daterange"),
    "datetime": ("com.atlassian.jira.plugin.system.customfieldtypes:datetime",
                 "com.atlassian.jira.plugin.system.customfieldtypes:datetimerange"),
    "float": ("com.atlassian.jira.plugin.system.customfieldtypes:float",
              "com.atlassian.jira.plugin.system.customfieldtypes:exactnumber"),
    "userpicker": ("com.atlassian.jira.plugin.system.customfieldtypes:userpicker",
                   "com.atlassian.jira.plugin.system.customfieldtypes:userpickergroupsearcher"),
    "multiuserpicker": ("com.atlassian.jira.plugin.system.customfieldtypes:multiuserpicker",
                        "com.atlassian.jira.plugin.system.customfieldtypes:userpickergroupsearcher"),
    "labels": ("com.atlassian.jira.plugin.system.customfieldtypes:labels",
               "com.atlassian.jira.plugin.system.customfieldtypes:labelsearcher"),
    "url": ("com.atlassian.jira.plugin.system.customfieldtypes:url",
            "com.atlassian.jira.plugin.system.customfieldtypes:exacttextsearcher"),
    # Aliasy skrócone (używane w yaml)
    "date": ("com.atlassian.jira.plugin.system.customfieldtypes:datepicker",
             "com.atlassian.jira.plugin.system.customfieldtypes:daterange"),
    "user": ("com.atlassian.jira.plugin.system.customfieldtypes:userpicker",
             "com.atlassian.jira.plugin.system.customfieldtypes:userpickergroupsearcher"),
    "issuelink": ("com.atlassian.jira.plugin.system.customfieldtypes:issuelink",
                  "com.atlassian.jira.plugin.system.customfieldtypes:issueidsearcher"),
    "issuelist": ("com.atlassian.jira.plugin.system.customfieldtypes:multiissuelist",
                  "com.atlassian.jira.plugin.system.customfieldtypes:issueidsearcher"),
}

STATUS_CATEGORIES = {"TODO", "IN_PROGRESS", "DONE"}

# Układ diagramu workflow (piksele w edytorze Jiry). Orientacja PIONOWA, jak
# w diagramach KlientX: główny ciąg z góry na dół, gałęzie boczne obok.
LAYOUT_X0, LAYOUT_Y0 = 300.0, 150.0
LAYOUT_SIBLING_GAP = 260.0            # odstęp w poziomie między statusami w jednym wierszu
LAYOUT_LAYER_GAP = 110.0              # odstęp w pionie między kolejnymi wierszami (krokami)
LAYOUT_BAND_LAYERS = 16               # po tylu wierszach łańcuch zawija się do pasa obok (w prawo); niżej zawijanie rwało ciąg Story
LAYOUT_BAND_GAP = 1.0                 # pusta kolumna między pasami

# Jira zapisuje pozycję LEWEGO GÓRNEGO rogu prostokąta statusu, a jego
# szerokość zależy od długości nazwy (potwierdzone na Workflow-Epic: prostokąty
# o tym samym x miały wyrównane lewe krawędzie, więc strzałki szły zygzakiem).
# Szerokość szacujemy z nazwy, żeby w pionie wyrównać ŚRODKI, jak w KlientX.
def estimate_box_width(label: str) -> float:
    return 54.0 + 7.6 * len(label or "")


# Numery portów (punktów zaczepienia strzałki na krawędzi prostokąta). API ich
# nie dokumentuje — ustal je przez diagnose_workflow_ports.py i wpisz tutaj,
# np. {"top": 1, "bottom": 5, "left": 7, "right": 3}. Puste = porty przejść
# zostają bez zmian (przy tworzeniu: 0, czyli wszystkie strzałki górą).
LAYOUT_PORTS: dict = {}


def choose_ports(src_xy, dst_xy, src_w: float, dst_w: float):
    """(fromPort, toPort) dla strzałki, jak w KlientX: w dół — z dołu do góry;
    w górę (powrót, np. Done -> In Progress) — pętla po prawej stronie;
    w tym samym wierszu — z boku na bok. None, gdy LAYOUT_PORTS nie ustawione."""
    if not LAYOUT_PORTS:
        return None
    P = LAYOUT_PORTS
    (sx, sy), (dx, dy) = src_xy, dst_xy
    if abs(dy - sy) < 1:                      # ten sam wiersz
        if sx + src_w / 2 < dx + dst_w / 2:
            return P["right"], P["left"]
        return P["left"], P["right"]
    if dy > sy:                               # w dół
        return P["bottom"], P["top"]
    return P["right"], P["right"]             # w górę — pętla po prawej


def compute_workflow_layout(statuses: list, initial, edges: list, labels: dict = None) -> tuple:
    """Warstwowy układ diagramu workflow (bez zewnętrznych bibliotek).

    statuses: identyfikatory statusów w kolejności wyjściowej
    initial:  status początkowy (górny wiersz)
    edges:    lista (skąd, dokąd) z przejść KIERUNKOWYCH — globalne ('ANY')
              pomijamy, bo prowadzą z każdego miejsca i tylko zaciemniają układ.

    Wiersz ("warstwa") = najkrótsza odległość od statusu początkowego. Statusy
    nieosiągalne zwykłymi przejściami (np. 'Anulowane' — tylko przez przejście
    globalne) stają obok statusu, do którego prowadzą. Kolejność w wierszu ustala
    heurystyka barycentrum (kilka przebiegów w obie strony), żeby strzałki
    mniej się krzyżowały. Zwraca ({status: (x, y)}, {kolumna: [statusy]})."""
    from collections import defaultdict, deque

    known = set(statuses)
    succ, pred = defaultdict(set), defaultdict(set)
    for a, b in edges:
        if a in known and b in known and a != b:
            succ[a].add(b)
            pred[b].add(a)

    first_seen = {s: i for i, s in enumerate(statuses)}
    layer = {}

    def bfs(seed, start):
        layer[seed] = start
        queue = deque([seed])
        while queue:
            n = queue.popleft()
            for m in sorted(succ[n], key=first_seen.get):
                if m not in layer:
                    layer[m] = layer[n] + 1
                    queue.append(m)

    if initial in known:
        bfs(initial, 0)
    # Nieosiągalne ze startu, ale z przejściami (np. 'Anulowane' -> 'Ponownie
    # otwarte', dostępne tylko przejściem globalnym): stawiamy o kolumnę przed
    # statusem, do którego prowadzą. Powtarzamy, bo takie statusy bywają łańcuchem.
    changed = True
    while changed:
        changed = False
        for s in statuses:
            if s not in layer and any(n in layer for n in succ[s]):
                layer[s] = max(1, min(layer[n] for n in succ[s] if n in layer) - 1)
                changed = True
    # Całe podgrafy bez połączenia ze startem (np. niekompletny workflow):
    # układamy osobno od kolumny 1, zaczynając od statusu bez poprzedników.
    rest = [s for s in statuses if s not in layer and (succ[s] or pred[s])]
    while rest:
        rest_set = set(rest)
        seed = next((s for s in rest if not (pred[s] & rest_set)), rest[0])
        bfs(seed, 1)
        rest = [s for s in rest if s not in layer]
    # Statusy bez żadnych przejść kierunkowych — do ostatniej kolumny (ale nie
    # otwieramy dla nich nowego pasa: wtedy dokładamy je do ostatniej istniejącej).
    last = max(layer.values(), default=-1) + 1
    if last > 0 and last % LAYOUT_BAND_LAYERS == 0:
        last -= 1
    for s in statuses:
        layer.setdefault(s, last)

    columns = defaultdict(list)
    for s in statuses:
        columns[layer[s]].append(s)

    # Długie łańcuchy zawijamy w pasy po LAYOUT_BAND_LAYERS wierszy (kolejny pas
    # na prawo), żeby diagram nie miał kilku tysięcy px wysokości.
    def band_of(L):
        return L // LAYOUT_BAND_LAYERS
    band_height = defaultdict(int)
    for L, col in columns.items():
        band_height[band_of(L)] = max(band_height[band_of(L)], len(col))
    band_top, acc = {}, 0.0
    for b in sorted(band_height):
        band_top[b] = acc
        acc += band_height[b] + LAYOUT_BAND_GAP

    def y_positions():
        # Wyrównanie do lewej (nie środkowanie): główny ciąg w jednej pionowej
        # linii, gałęzie boczne w prawo — tak wyglądają diagramy KlientX.
        pos = {}
        for L, col in columns.items():
            off = band_top[band_of(L)]
            for i, s in enumerate(col):
                pos[s] = i + off
        return pos

    def sweep(keys, neighbour_side):
        for L in keys:
            pos = y_positions()
            def barycenter(s):
                ns = [pos[n] for n in (succ[s] | pred[s]) if neighbour_side(layer[n], L)]
                return sum(ns) / len(ns) if ns else pos[s]
            columns[L].sort(key=barycenter)

    order = sorted(columns)
    for _ in range(4):
        sweep(order[1:], lambda nl, L: nl < L)          # z góry: patrz na poprzednie wiersze
        sweep(order[-2::-1], lambda nl, L: nl > L)      # z dołu: patrz na następne

    pos = y_positions()   # pozycja w obrębie wiersza (oś pozioma), z przesunięciem pasa
    labels = labels or {}
    # Środek statusu wyliczony z układu, zamieniony na lewy górny róg (tego chce Jira)
    coords = {s: (LAYOUT_X0 + pos[s] * LAYOUT_SIBLING_GAP - estimate_box_width(labels.get(s, s)) / 2,
                  LAYOUT_Y0 + (layer[s] % LAYOUT_BAND_LAYERS) * LAYOUT_LAYER_GAP)
              for s in statuses}
    return coords, dict(sorted(columns.items()))


def start_point_layout(coords: dict, initial) -> dict:
    """Punkt START nad i na lewo od statusu początkowego (jak w KlientX). Bez
    tego Jira zostawiała start tam, gdzie był w starym układzie — w Epicu na
    prawym końcu diagramu, z linią przez cały ekran. coords = lewe górne rogi."""
    x, y = coords.get(initial, (LAYOUT_X0, LAYOUT_Y0))
    return {"x": x - 140.0, "y": y - 50.0}


_INITIAL_PLACEHOLDERS = {"początek", "start", "__initial__"}
_RULE_KEYS = ("actions", "conditions", "validators", "triggers", "properties")


def _spec_edges(spec: dict) -> list:
    edges = []
    for t in spec.get("transitions") or []:
        frm = t.get("from", "ANY")
        if frm == "ANY":
            continue
        for f in (frm if isinstance(frm, list) else [frm]):
            edges.append((f, t["to"]))
    return edges


def spec_layout(spec: dict, statuses: list, initial: str) -> tuple:
    """Układ diagramu: z YAML ('layout' + 'start_layout' — pozycje z diagramu
    KlientX), a gdy go brak lub jest niepełny — wyliczony automatycznie.
    Zwraca ({status: (x, y)} jako lewe górne rogi, startPointLayout)."""
    lay = spec.get("layout") or {}
    if lay and all(st in lay for st in statuses):
        coords = {st: (float(lay[st][0]), float(lay[st][1])) for st in statuses}
        sl = spec.get("start_layout")
        start = {"x": float(sl[0]), "y": float(sl[1])} if sl else start_point_layout(coords, initial)
        return coords, start
    coords, _ = compute_workflow_layout(statuses, initial, _spec_edges(spec))
    return coords, start_point_layout(coords, initial)


def build_transitions(spec: dict, initial: str, ref_of: dict, coords: dict, keep_rules: dict = None) -> list:
    """Lista przejść do workflows/create i workflows/update.

    ref_of:     nazwa statusu -> statusReference
    keep_rules: {(nazwa przejścia małymi literami, toStatusReference): przejście z Jiry}
                — reguły (akcje, warunki, walidatory...) przenoszone z istniejącego
                przejścia o tej samej nazwie i celu, żeby aktualizacja ich nie kasowała.
    'id' z YAML (identyfikator z KlientX) jest zachowywany; pozostałe dostają wolne numery."""
    used = {"1"} | {str(t["id"]) for t in spec.get("transitions") or [] if t.get("id")}
    free = (i for i in range(1001, 100000) if str(i) not in used)
    out = [{"name": "Create", "description": "", "id": "1", "type": "INITIAL",
            "toStatusReference": ref_of[initial],
            "actions": [], "triggers": [], "validators": [], "conditions": None}]
    for t in spec.get("transitions") or []:
        frm = t.get("from", "ANY")
        froms = None if frm == "ANY" else (frm if isinstance(frm, list) else [frm])
        if froms and all(f.strip().lower() in _INITIAL_PLACEHOLDERS for f in froms):
            continue  # "Początek" -> INITIAL, generowane wyżej
        to = t["to"]
        entry = {"name": t["name"], "description": t.get("description", ""),
                 "id": str(t["id"]) if t.get("id") else str(next(free)),
                 "type": "GLOBAL" if froms is None else "DIRECTED",
                 "toStatusReference": ref_of[to],
                 "actions": [], "triggers": [], "validators": [], "conditions": None}
        if froms is not None:
            links = []
            for f in froms:
                ports = choose_ports(coords[f], coords[to], estimate_box_width(f), estimate_box_width(to))
                fp, tp = ports if ports else (0, 0)
                links.append({"fromStatusReference": ref_of[f], "fromPort": fp, "toPort": tp})
            entry["links"] = links
        old = (keep_rules or {}).get((t["name"].strip().lower(), ref_of[to]))
        if old:
            for k in _RULE_KEYS:
                if old.get(k):
                    entry[k] = old[k]
        out.append(entry)
    return out

# Obsługa limitu zapytań Jiry (HTTP 429)
RATE_LIMIT_RETRIES = 6
WRITE_PAUSE = 0.3  # sekundy po każdym zapisie (POST/PUT/DELETE)

# Polskie nazwy pól SYSTEMOWYCH -> stabilne klucze Jiry.
# Nazwy zwracane przez /rest/api/3/field zależą od języka konta wywołującego
# API, więc schemat zbudowany z polskiego UI nie dopasuje się, gdy skrypt
# działa na koncie angielskim (i odwrotnie). Klucze systemowe ("summary",
# "description", ...) są niezmienne niezależnie od języka - dopasowujemy
# po nich, gdy dopasowanie po nazwie zawiedzie.
SYSTEM_FIELD_KEYS = {
    "podsumowanie": "summary",
    "opis": "description",
    "priorytet": "priority",
    "etykiety": "labels",
    "termin": "duedate",
    "osoba przypisana": "assignee",
    "osoba zgłaszająca": "reporter",
    "załącznik": "attachment",
    "komponenty": "components",
    "wersje do poprawienia": "fixVersions",
    # "wersja bazowa" — w KlientX to własne pole tekstowe, nie Fix Version/s
    "ma wpływ na wersje": "versions",
    "wersje, których dotyczy": "versions",
    "środowisko": "environment",
    "environment.": "environment",
    "połączone zgłoszenia": "issuelinks",
    "powiązane zgłoszenia": "issuelinks",
    "rejestrowanie czasu": "timetracking",
    "element nadrzędny": "parent",
    "typ zgłoszenia": "issuetype",
    "rozwiązanie": "resolution",
    "poprawka": "fixVersions",
}

# Pola dodawane przez Jira Software / Advanced Roadmaps. W odróżnieniu od
# pól tworzonych przez użytkownika, Jira TŁUMACZY ich nazwy ("Epic Name" /
# "Nazwa epiki"), a ich ID to customfield_NNNNN różne w każdej instancji -
# więc ani nazwa, ani klucz nie nadają się do dopasowania. Stały jest
# natomiast typ schematu (schema.custom), i po nim je znajdujemy.
APP_FIELD_TYPES = {
    "com.pyxis.greenhopper.jira:gh-epic-label": {
        "nazwa epiki", "epic name"},
    "com.pyxis.greenhopper.jira:gh-epic-link": {
        "łącze epiku", "lacze epiku", "epic link"},
    "com.pyxis.greenhopper.jira:gh-sprint": {
        "sprint"},
    "com.pyxis.greenhopper.jira:gh-lexo-rank": {
        "ranga", "rank"},
    "com.atlassian.jpo:jpo-custom-field-parent": {
        "parent link", "łącze elementu nadrzędnego"},
    "com.atlassian.jpo:jpo-custom-field-baseline-start": {
        "target start", "docelowa data rozpoczęcia"},
    "com.atlassian.jpo:jpo-custom-field-baseline-end": {
        "target end", "docelowa data zakończenia"},
    "com.atlassian.jira.ext.charting:firstresponsedate": {
        "data pierwszej odpowiedzi", "date of first response"},
}


class Builder:
    def __init__(self, schema: dict, dry_run: bool, include_incomplete: bool = False):
        self.schema = schema
        self.dry_run = dry_run
        self.include_incomplete = include_incomplete
        self.only_workflows = None  # filtr dla --only workflow_layout (--workflow)
        self.prefix = schema.get("prefix", "").strip()
        self.state = self._load_state()
        self.errors = []
        self.warnings = []

    # ---------------------------------------------------------------- pomocnicze

    def _load_state(self) -> dict:
        state = {"custom_fields": {}, "statuses": {}, "workflows": {},
                 "screens": {}, "screen_schemes": {}, "issue_type_screen_schemes": {},
                 "workflow_schemes": {}, "issue_type_schemes": {}}
        if STATE_FILE.exists():
            try:
                # Uzupełnienie brakujących kluczy w starszych plikach stanu
                state.update(json.loads(STATE_FILE.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001
                pass
        return state

    def _save_state(self) -> None:
        if not self.dry_run:
            STATE_FILE.write_text(json.dumps(self.state, indent=2, ensure_ascii=False),
                                  encoding="utf-8")

    def _prefixed(self, name: str) -> str:
        """Dodaje prefiks do nazw obiektów konfiguracyjnych (nie do custom fields)."""
        return f"{self.prefix} {name}".strip() if self.prefix else name

    def _req(self, method: str, path: str, **kw) -> requests.Response:
        """Wysyła zapytanie do Jiry z obsługą limitu zapytań (HTTP 429).

        Jira zwraca 429 przy zbyt szybkiej serii zapytań - a dodawanie pól do
        ekranów to dziesiątki wywołań pod rząd. Ponawiamy, respektując nagłówek
        Retry-After, a gdy go brak - z rosnącym opóźnieniem. Dodatkowo krótka
        pauza po każdym zapisie, żeby w ogóle nie dobijać do limitu.
        """
        url = f"{core.JIRA_BASE_URL}{path}"
        headers = kw.pop("headers", {})
        headers.setdefault("Content-Type", "application/json")

        delay = 2
        for attempt in range(1, RATE_LIMIT_RETRIES + 1):
            resp = requests.request(method, url, auth=core.jira_auth(),
                                    headers=headers, timeout=60, **kw)
            if resp.status_code != 429:
                if method != "GET":
                    time.sleep(WRITE_PAUSE)
                return resp
            if attempt == RATE_LIMIT_RETRIES:
                return resp
            wait = delay
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    wait = max(wait, int(float(retry_after)))
                except ValueError:
                    pass
            print(f"      [LIMIT] HTTP 429 (próba {attempt}/{RATE_LIMIT_RETRIES}) — "
                  f"czekam {wait}s...")
            time.sleep(wait)
            delay = min(delay * 2, 60)
        return resp

    def _get_paginated(self, path: str, params: dict = None) -> list:
        """Pobiera wszystkie strony z paginowanego endpointu (values + isLast)."""
        results, start_at = [], 0
        params = dict(params or {})
        while True:
            params.update({"startAt": start_at, "maxResults": 50})
            resp = self._req("GET", path, params=params)
            resp.raise_for_status()
            data = resp.json()
            values = data.get("values", data if isinstance(data, list) else [])
            results.extend(values)
            if data.get("isLast", True) or not values:
                return results
            start_at += len(values)

    def _fail(self, what: str, resp: requests.Response) -> None:
        msg = f"{what}: HTTP {resp.status_code} {resp.text[:300]}"
        print(f"  [BŁĄD] {msg}")
        self.errors.append(msg)

    # ---------------------------------------------------------------- custom fields

    def build_custom_fields(self) -> None:
        fields = self.schema.get("custom_fields") or []
        if not fields:
            return
        print(f"\n=== CUSTOM FIELDS ({len(fields)}) ===")

        existing = {f["name"]: f["id"] for f in self._get_paginated("/rest/api/3/field/search")}

        for spec in fields:
            name, ftype = spec["name"], spec["type"]
            if ftype not in FIELD_TYPE_MAP:
                self.errors.append(f"Nieznany typ pola '{ftype}' dla '{name}'")
                print(f"  [BŁĄD] Nieznany typ pola '{ftype}' dla '{name}'. "
                      f"Dostępne: {', '.join(sorted(FIELD_TYPE_MAP))}")
                continue

            if name in existing:
                field_id = existing[name]
                print(f"  [ISTNIEJE] '{name}' -> {field_id}")
                self.state["custom_fields"][name] = field_id
                self._ensure_field_options(field_id, name, spec)
                continue

            if self.dry_run:
                print(f"  [DRY-RUN] Utworzyłbym pole '{name}' (typ: {ftype})")
                continue

            type_key, searcher_key = FIELD_TYPE_MAP[ftype]
            resp = self._req("POST", "/rest/api/3/field", json={
                "name": name,
                "description": spec.get("description", ""),
                "type": type_key,
                "searcherKey": searcher_key,
            })
            if resp.status_code >= 300:
                # issuelink / issuelist mogą być niedostępne przez API w niektórych planach Jiry
                if ftype in ("issuelink", "issuelist") and resp.status_code in (400, 403):
                    print(f"  [OSTRZEŻENIE] Pole '{name}' (typ: {ftype}) — Jira odrzuciła tworzenie "
                          f"przez API (HTTP {resp.status_code}). Utwórz ręcznie przez "
                          f"⚙ → Work items → Fields → Create field → Issue Picker.")
                    self.warnings.append(f"Pole '{name}' wymaga ręcznego utworzenia (Issue Picker)")
                else:
                    self._fail(f"Tworzenie pola '{name}'", resp)
                continue
            field_id = resp.json()["id"]
            print(f"  [UTWORZONO] '{name}' -> {field_id}")
            self.state["custom_fields"][name] = field_id
            self._ensure_field_options(field_id, name, spec)

    def _ensure_field_options(self, field_id: str, name: str, spec: dict) -> None:
        """Dla pól typu select/multiselect: tworzy kontekst (jeśli brak) i opcje."""
        options = spec.get("options")
        if not options or self.dry_run:
            return

        contexts = self._get_paginated(f"/rest/api/3/field/{field_id}/context")
        if contexts:
            context_id = contexts[0]["id"]
        else:
            resp = self._req("POST", f"/rest/api/3/field/{field_id}/context",
                             json={"name": f"Kontekst globalny: {name}"})
            if resp.status_code >= 300:
                self._fail(f"Tworzenie kontekstu dla '{name}'", resp)
                return
            context_id = resp.json()["id"]

        existing_opts = {o["value"] for o in self._get_paginated(
            f"/rest/api/3/field/{field_id}/context/{context_id}/option")}
        missing = [o for o in options if o not in existing_opts]
        if not missing:
            return

        resp = self._req("POST", f"/rest/api/3/field/{field_id}/context/{context_id}/option",
                         json={"options": [{"value": v, "disabled": False} for v in missing]})
        if resp.status_code >= 300:
            self._fail(f"Dodawanie opcji do '{name}'", resp)
        else:
            print(f"      dodano {len(missing)} opcji: {', '.join(missing)}")

    # ---------------------------------------------------------------- statusy

    def resolve_statuses(self) -> dict:
        """Zwraca mapę {nazwa_statusu: {'id': ..., 'ref': uuid, 'new': bool}}.

        WAŻNE: Jira wymaga globalnie unikalnych nazw statusów. Jeśli status
        o danej nazwie już istnieje, MUSIMY podać jego ID w payloadzie
        tworzenia workflow - inaczej API odrzuca z "Status name must be
        unique" (potwierdzony, udokumentowany błąd tego API)."""
        specs = self.schema.get("statuses") or []
        if not specs:
            return {}
        print(f"\n=== STATUSY ({len(specs)}) ===")

        # Lista istniejących statusów. Używamy /rest/api/3/status (zwraca
        # WSZYSTKIE statusy jedną tablicą) zamiast /statuses/search — ten
        # drugi potrafi nie zwrócić części statusów mimo paginacji, co
        # kończyło się błędem 400 "Status name already in use" przy
        # tworzeniu workflow (potwierdzone na tej instancji).
        # Klucz porównania jest znormalizowany, bo Jira odrzuca duplikaty
        # ignorując wielkość liter i białe znaki na brzegach.
        existing_numeric = {}  # name.lower() -> numeryczne id (z /rest/api/3/status)
        existing_ref = {}      # name.lower() -> UUID statusReference (z /statuses/search)
        existing_scope = {}    # name.lower() -> scope type ("GLOBAL"/"PROJECT"/None)
        existing_category = {} # name.lower() -> statusCategory PRAWDZIWA z Jiry (nie zgadnięta z yaml)

        # Mapowanie statusCategory.key (stary /rest/api/3/status) -> nasze STATUS_CATEGORIES
        _CATEGORY_KEY_MAP = {"new": "TODO", "indeterminate": "IN_PROGRESS", "done": "DONE"}

        def _extract_category(raw):
            """statusCategory ma RÓŻNY kształt w różnych API Jiry:
            - /rest/api/3/status: obiekt {"key": "new"/"indeterminate"/"done", ...}
            - /rest/api/3/statuses/search: zwykły string "TODO"/"IN_PROGRESS"/"DONE"
            Obsługujemy oba, żeby uniknąć 'str'/'dict' object has no attribute 'get'."""
            if isinstance(raw, str):
                return raw if raw in STATUS_CATEGORIES else None
            if isinstance(raw, dict):
                return _CATEGORY_KEY_MAP.get(raw.get("key"))
            return None

        # /rest/api/3/status — zwraca numeryczne id (np. "6", "11168") + statusCategory
        # KLUCZOWE: /rest/api/3/status zwraca RÓWNIEŻ statusy projektów
        # team-managed (scope PROJECT) — mogą mieć tę samą nazwę co globalne
        # (potwierdzone: "Done" = 10001 globalny ORAZ 10233 projektowy;
        # "To Do" 10000/10231; "In Progress" 3/10232). Wcześniej "ostatni
        # wygrywał", więc do workflow globalnego trafiało id statusu
        # projektowego → "Status does not exist or is not visible to the
        # workflow". Pomijamy więc PROJECT, a poniżej globalne id z
        # /statuses/search NADPISUJĄ to, co tu zebraliśmy.
        name_ids = {}  # key -> set(id) — do wykrywania duplikatów nazw
        resp = self._req("GET", "/rest/api/3/status")
        if resp.status_code < 300:
            for s in resp.json():
                key = s["name"].strip().lower()
                name_ids.setdefault(key, set()).add(s["id"])
                if (s.get("scope") or {}).get("type") == "PROJECT":
                    continue
                existing_numeric[key] = s["id"]
                real_cat = _extract_category(s.get("statusCategory"))
                if real_cat:
                    existing_category[key] = real_cat
        else:
            self._fail("Pobieranie listy statusów", resp)

        # /rest/api/3/statuses/search — zwraca UUID "id" (statusReference) i scope.
        # BEZ projectId: zwraca tylko GLOBAL scope statusy.
        # Z projectId: zwraca też PROJECT scope dla danego projektu.
        # Pobieramy bez projectId (GLOBAL) + osobno dla wszystkich projektów ze schematu
        # (PROJECT scope) żeby mieć pełny obraz.
        # UWAGA KLUCZOWA: część statusów (legacy, sprzed wprowadzenia UUID w Jirze)
        # NIE MA prawdziwego UUID — /statuses/search zwraca dla nich "id" jako zwykłą
        # liczbę (np. "11168"), identyczną z numerycznym id z /rest/api/3/status.
        # Jira akceptuje taką liczbę jako statusReference W PAYLOADZIE TYLKO gdy razem
        # z nią wysłane jest też pole "id" (integer) — bez "id" odrzuca błędem
        # "The reference X is not a UUID.". Dlatego: gdy "id" zwrócone przez
        # /statuses/search jest czysto numeryczne, zapisujemy je RÓWNIEŻ do
        # existing_numeric (nie tylko existing_ref), żeby trafiło do payloadu jako "id".
        def _fetch_statuses_search(extra_params=None):
            for s in self._get_paginated("/rest/api/3/statuses/search", params=extra_params or {}):
                key = s["name"].strip().lower()
                scope_type = (s.get("scope") or {}).get("type")
                status_id_field = s.get("id", "")
                if status_id_field:
                    name_ids.setdefault(key, set()).add(status_id_field)
                if scope_type == "PROJECT":
                    continue  # workflow globalny i tak tego statusu nie zobaczy
                # GLOBAL z /statuses/search jest źródłem prawdy — NADPISUJEMY
                existing_scope[key] = scope_type
                if status_id_field:
                    existing_ref[key] = status_id_field
                    if status_id_field.isdigit():
                        existing_numeric[key] = status_id_field
                real_cat = _extract_category(s.get("statusCategory"))
                if real_cat:
                    existing_category[key] = real_cat

        _fetch_statuses_search()  # GLOBAL scope

        # Pobierz też PROJECT scope — szukamy po nazwie każdego statusu ze schematu
        # który nie został jeszcze znaleziony w /status ani w GLOBAL /statuses/search.
        # Używamy searchString żeby nie pobierać wszystkich statusów wszystkich projektów.
        specs_names = [s["name"] for s in specs]
        missing_keys = {n.strip().lower() for n in specs_names
                        if n.strip().lower() not in existing_numeric
                        and n.strip().lower() not in existing_ref}
        for name in specs_names:
            key = name.strip().lower()
            if key in missing_keys:
                for s in self._get_paginated("/rest/api/3/statuses/search",
                                             params={"searchString": name}):
                    skey = s["name"].strip().lower()
                    scope_type = (s.get("scope") or {}).get("type")
                    status_id_field = s.get("id", "")
                    if status_id_field:
                        name_ids.setdefault(skey, set()).add(status_id_field)
                    if scope_type == "PROJECT":
                        continue  # niewidoczny dla workflow globalnego
                    existing_scope.setdefault(skey, scope_type)
                    if status_id_field:
                        existing_ref.setdefault(skey, status_id_field)
                        # Legacy status bez prawdziwego UUID — "id" jest numeryczne.
                        if status_id_field.isdigit():
                            existing_numeric.setdefault(skey, status_id_field)
                    real_cat = _extract_category(s.get("statusCategory"))
                    if real_cat:
                        existing_category.setdefault(skey, real_cat)

        # Ostrzeżenie o nazwach, które w Jirze wskazują na KILKA statusów —
        # to one powodowały "not visible to the workflow".
        for name in specs_names:
            key = name.strip().lower()
            ids = name_ids.get(key, set())
            if len(ids) > 1:
                print(f"  [UWAGA] '{name}' istnieje w Jirze {len(ids)}x (id: {', '.join(sorted(ids))}) "
                      f"— używam globalnego: {existing_numeric.get(key) or existing_ref.get(key) or 'BRAK'}")

        result = {}
        for spec in specs:
            name, cat = spec["name"], spec.get("category", "TODO")
            if cat not in STATUS_CATEGORIES:
                self.errors.append(f"Nieznana kategoria '{cat}' dla statusu '{name}'")
                print(f"  [BŁĄD] Nieznana kategoria '{cat}' dla '{name}'. "
                      f"Dozwolone: {', '.join(sorted(STATUS_CATEGORIES))}")
                continue
            key = name.strip().lower()
            numeric_id = existing_numeric.get(key)
            uuid_ref = existing_ref.get(key)
            scope_type = existing_scope.get(key, "?")

            # Status istnieje jeśli znaleziony w /status (GLOBAL) LUB /statuses/search
            # (PROJECT scope — /status ich nie zwraca, ale /statuses/search tak).
            status_exists = bool(numeric_id or uuid_ref)

            if status_exists:
                # KLUCZOWE (potwierdzone przez Atlassian Community): statusReference
                # to WYŁĄCZNIE lokalny alias ważny w ramach TEGO requestu — walidator
                # Jiry wymaga żeby był w formacie prawdziwego UUID, niezależnie czy
                # status jest nowy czy istniejący. To pole "id" (numeryczne) wskazuje
                # na PRAWDZIWY, istniejący status. Dlatego: jeśli uuid_ref, który mamy,
                # jest tylko liczbą (bez prawdziwego UUID) — generujemy świeże, poprawne
                # UUID. Jeśli już mamy prawdziwy UUID (PROJECT-scope, ze /statuses/search)
                # zostawiamy go — też jest poprawnym formatem.
                if not uuid_ref or str(uuid_ref).isdigit():
                    uuid_ref = str(uuid.uuid4())
                effective_id = numeric_id  # None dla PROJECT scope (nie przekazujemy id do payloadu)
                # KLUCZOWE: użyj PRAWDZIWEJ kategorii z Jiry, nie zgadniętej z yaml —
                # niezgodność statusCategory dla istniejącego statusu powoduje
                # "Status does not exist or is not visible to the workflow." przy tworzeniu.
                real_cat = existing_category.get(key)
                effective_cat = real_cat or cat
                if real_cat and real_cat != cat:
                    print(f"  [UWAGA] '{name}': kategoria w yaml to '{cat}', ale Jira ma "
                          f"'{real_cat}' — używam prawdziwej (z Jiry).")
                print(f"  [ISTNIEJE] '{name}' -> {numeric_id or '(PROJECT)'} "
                      f"(scope: {scope_type}, kategoria: {effective_cat}, ref: {str(uuid_ref)[:8]}...)")
                result[name] = {"id": effective_id, "ref": uuid_ref, "new": False,
                                "category": effective_cat, "scope": scope_type}
                if numeric_id:
                    self.state["statuses"][name] = numeric_id
            else:
                print(f"  [NOWY] '{name}' (zostanie utworzony razem z workflow)")
                result[name] = {"id": None, "ref": str(uuid.uuid4()), "new": True, "category": cat}
        return result

    # ---------------------------------------------------------------- workflow

    def _workflow_specs(self) -> list:
        """Zbiera WSZYSTKIE definicje workflow ze schematu. Obsługuje:
          workflows:            — lista (zalecany format)
          workflow:             — pojedynczy (stary format)
          _pozostale_workflow:  — lista (stary format z merge_jira_sources.py)
        Wcześniej budowany był TYLKO 'workflow:', a reszta była po cichu
        ignorowana."""
        specs = []
        if self.schema.get("workflow"):
            specs.append(self.schema["workflow"])
        specs.extend(self.schema.get("workflows") or [])
        specs.extend(self.schema.get("_pozostale_workflow") or [])
        return specs

    def build_workflows(self, status_map: dict) -> None:
        specs = self._workflow_specs()
        if not specs:
            return
        # Lista istniejących workflow pobierana RAZ dla wszystkich (a nie per workflow).
        existing_names = set()
        for w in self._get_paginated("/rest/api/3/workflow/search"):
            found = w.get("id", {}).get("name") or w.get("name") or ""
            existing_names.add(found.strip().lower())

        print(f"\n=== WORKFLOWS ({len(specs)}) ===")
        for spec in specs:
            try:
                self.build_workflow(spec, status_map, existing_names)
            except Exception as exc:  # noqa: BLE001 — jeden zepsuty workflow nie blokuje reszty
                msg = f"Workflow '{spec.get('name', '?')}': {exc}"
                print(f"  [BŁĄD] {msg}")
                self.errors.append(msg)

    def build_workflow(self, spec: dict, status_map: dict, existing_names: set) -> None:
        wf_name = self._prefixed(spec["name"])
        print(f"\n=== WORKFLOW: {wf_name} ===")

        # Porównanie z pełną listą pobraną lokalnie, zamiast ufać filtrowi
        # workflowName po stronie API - ten potrafi nic nie zwrócić mimo
        # istniejącego workflow (wtedy powstawałby duplikat).
        if wf_name.strip().lower() in existing_names:
            print(f"  [ISTNIEJE] Workflow '{wf_name}' — pomijam tworzenie "
                  f"(skrypt NIE aktualizuje istniejących workflow).")
            self.state["workflows"][wf_name] = wf_name
            return

        # Workflow oznaczony jako niekompletny (brakujące przejścia) — NIE tworzymy
        # go domyślnie, bo skrypt nie aktualizuje istniejących workflow: utworzenie
        # teraz zablokowałoby późniejsze uzupełnienie przejść przez ten skrypt.
        if spec.get("incomplete") and not self.include_incomplete:
            print(f"  [POMINIĘTO] '{wf_name}' ma 'incomplete: true' (brakujące przejścia). "
                  f"Uzupełnij yaml albo uruchom z --include-incomplete.")
            self.warnings.append(f"Workflow '{wf_name}' pominięty — oznaczony jako niekompletny")
            return

        wf_statuses = spec.get("statuses") or []
        missing = [s for s in wf_statuses if s not in status_map]
        if missing:
            self.errors.append(f"Workflow odwołuje się do niezdefiniowanych statusów: {missing}")
            print(f"  [BŁĄD] Statusy użyte w workflow, ale nieobecne w sekcji 'statuses': {missing}")
            return

        if self.dry_run:
            print(f"  [DRY-RUN] Utworzyłbym workflow '{wf_name}' "
                  f"({len(wf_statuses)} statusów, {len(spec.get('transitions') or [])} przejść)")
            return

        # Statusy do zadeklarowania w payloadzie — Jira wymaga zawsze:
        # id (integer! — nie string) + name + statusCategory + statusReference
        # Uwaga: /rest/api/3/status zwraca id jako string, ale workflows/create
        # wymaga integera — stąd konwersja int().
        payload_statuses = []
        for name in wf_statuses:
            info = status_map[name]
            entry = {
                "name": name,
                "description": "",
                "statusCategory": info["category"],
                "statusReference": info["ref"],
            }
            if info["id"]:
                entry["id"] = int(info["id"])
            payload_statuses.append(entry)

        # Status początkowy (INITIAL) — z jawnego 'initial_status' (kolejność listy
        # 'statuses' w yaml bywa przypadkowa: w Story pierwszy był 'Closed').
        initial_status_name = spec.get("initial_status")
        if not initial_status_name:
            initial_status_name = wf_statuses[0]
            print(f"  [UWAGA] Brak 'initial_status' w schemacie workflow '{wf_name}' — "
                  f"używam pierwszego z listy ('{initial_status_name}'). Dodaj jawnie "
                  f"'initial_status: <nazwa>' do schematu.")
        elif initial_status_name not in status_map:
            self.errors.append(f"Workflow '{wf_name}': initial_status '{initial_status_name}' "
                                f"nie istnieje w sekcji 'statuses'")
            print(f"  [BŁĄD] initial_status '{initial_status_name}' nieobecny w sekcji 'statuses'")
            return
        else:
            print(f"  [INFO] Status początkowy (INITIAL): '{initial_status_name}'")

        bad = sorted({f for t in spec.get("transitions") or [] if t.get("from", "ANY") != "ANY"
                      for f in (t["from"] if isinstance(t["from"], list) else [t["from"]])
                      if f not in status_map and f.strip().lower() not in _INITIAL_PLACEHOLDERS}
                     | {t["to"] for t in spec.get("transitions") or [] if t["to"] not in status_map})
        if bad:
            self.errors.append(f"Workflow '{wf_name}': przejścia odwołują się do nieznanych statusów {bad}")
            print(f"  [BŁĄD] Przejścia odwołują się do nieznanych statusów: {bad}")
            return

        coords, start = spec_layout(spec, wf_statuses, initial_status_name)
        wf_status_layout = [{"statusReference": status_map[n]["ref"],
                             "layout": {"x": coords[n][0], "y": coords[n][1]}} for n in wf_statuses]
        ref_of = {n: status_map[n]["ref"] for n in status_map}
        transitions = build_transitions(spec, initial_status_name, ref_of, coords)

        print(f"  [DEBUG] Statusy w payloadzie ({len(payload_statuses)}):")
        for s in payload_statuses:
            scope = status_map[s["name"]].get("scope", "?")
            print(f"    [scope={scope}] {s}")

        resp = self._req("POST", "/rest/api/3/workflows/create", json={
            "scope": {"type": "GLOBAL"},
            "statuses": payload_statuses,
            "workflows": [{
                "name": wf_name,
                "description": spec.get("description", ""),
                "statuses": wf_status_layout,
                "transitions": transitions,
                "startPointLayout": start,
            }],
        })
        if resp.status_code >= 300:
            self._fail(f"Tworzenie workflow '{wf_name}'", resp)
            return
        print(f"  [UTWORZONO] Workflow '{wf_name}'")
        self.state["workflows"][wf_name] = wf_name
        existing_names.add(wf_name.strip().lower())

    # ---------------------------------------------------------------- ekrany

    def build_screens(self) -> None:
        specs = self.schema.get("screens") or []
        if not specs:
            return
        print(f"\n=== EKRANY ({len(specs)}) ===")

        existing = {s["name"]: s["id"] for s in self._get_paginated("/rest/api/3/screens")}
        # Indeksy pól: po nazwie (jak zwraca API, w języku konta) ORAZ po
        # kluczu systemowym - ten drugi pozwala dopasować pole opisane w
        # schemacie po polsku, gdy API odpowiada po angielsku.
        api_fields = self._get_paginated("/rest/api/3/field/search")
        all_fields = {f["name"]: f["id"] for f in api_fields}
        by_key = {f.get("key", f["id"]): f["id"] for f in api_fields}
        by_key.update({f["id"]: f["id"] for f in api_fields})
        # Indeks po typie schematu - dla pól z Jira Software / Advanced
        # Roadmaps, których nazwa jest tłumaczona, a ID zmienne per instancja.
        by_schema = {}
        for f in api_fields:
            custom_type = (f.get("schema") or {}).get("custom")
            if custom_type:
                by_schema.setdefault(custom_type, f["id"])

        for spec in specs:
            name = self._prefixed(spec["name"])
            if name in existing:
                screen_id = existing[name]
                print(f"  [ISTNIEJE] '{name}' -> {screen_id}")
            elif self.dry_run:
                print(f"  [DRY-RUN] Utworzyłbym ekran '{name}' "
                      f"({len(spec.get('tabs') or [])} zakładek)")
                continue
            else:
                resp = self._req("POST", "/rest/api/3/screens", json={
                    "name": name, "description": spec.get("description", ""),
                })
                if resp.status_code >= 300:
                    self._fail(f"Tworzenie ekranu '{name}'", resp)
                    continue
                screen_id = resp.json()["id"]
                print(f"  [UTWORZONO] '{name}' -> {screen_id}")
                self._rename_default_tab(screen_id, spec)

            self.state["screens"][name] = screen_id
            self._build_screen_tabs(screen_id, name, spec, all_fields, by_key, by_schema)
            self._order_screen_tabs(screen_id, name, spec)

    def _rename_default_tab(self, screen_id, spec: dict) -> None:
        """Jira tworzy nowy ekran z pustą zakładką 'Field Tab'. Jeśli schemat
        jej nie przewiduje (KlientX czasem zostawia tę nazwę — wtedy zostaje),
        zmieniamy jej nazwę na pierwszą zakładkę ze schematu, żeby nie
        zostawała pusta zakładka na początku ekranu."""
        wanted = [t["name"] for t in spec.get("tabs") or []]
        if not wanted:
            return
        resp = self._req("GET", f"/rest/api/3/screens/{screen_id}/tabs")
        if resp.status_code >= 300:
            return
        tabs = resp.json()
        if len(tabs) != 1 or tabs[0]["name"] in wanted:
            return
        r = self._req("PUT", f"/rest/api/3/screens/{screen_id}/tabs/{tabs[0]['id']}",
                      json={"name": wanted[0]})
        if r.status_code < 300:
            print(f"      zakładka domyślna '{tabs[0]['name']}' -> '{wanted[0]}'")

    def _resolve_field_id(self, field_name: str, all_fields: dict, by_key: dict,
                          by_schema: dict = None):
        """Ustala ID pola, próbując po kolei:
          1. dokładna nazwa z API,
          2. nazwa bez względu na wielkość liter,
          3. klucz systemowy z tłumaczenia PL -> klucz (np. "Opis" ->
             "description"), gdy schemat jest po polsku, a API odpowiada
             po angielsku (albo odwrotnie),
          4. pole utworzone w tym przebiegu (stan lokalny)."""
        # Bezpośrednie ID pola (np. 'customfield_11909') — gdy w Jirze jest
        # kilka pól o tej samej nazwie i trzeba wskazać konkretne.
        if field_name.strip() in by_key:
            return by_key[field_name.strip()]
        if field_name in all_fields:
            return all_fields[field_name]

        low = field_name.strip().lower()
        for name, fid in all_fields.items():
            if name.strip().lower() == low:
                return fid

        key = SYSTEM_FIELD_KEYS.get(low) or SYSTEM_FIELD_KEYS.get(low.rstrip("."))
        if key and key in by_key:
            return by_key[key]

        # Pola Jira Software / Advanced Roadmaps - po typie schematu
        for custom_type, names in APP_FIELD_TYPES.items():
            if low in names and by_schema and custom_type in by_schema:
                return by_schema[custom_type]

        return self.state["custom_fields"].get(field_name)

    def _build_screen_tabs(self, screen_id, screen_name: str, spec: dict,
                           all_fields: dict, by_key: dict, by_schema: dict) -> None:
        resp = self._req("GET", f"/rest/api/3/screens/{screen_id}/tabs")
        if resp.status_code >= 300:
            self._fail(f"Pobieranie zakładek ekranu '{screen_name}'", resp)
            return
        existing_tabs = {t["name"]: t["id"] for t in resp.json()}

        for tab_spec in spec.get("tabs") or []:
            tab_name = tab_spec["name"]
            if tab_name in existing_tabs:
                tab_id = existing_tabs[tab_name]
            elif self.dry_run:
                print(f"      [DRY-RUN] Dodałbym zakładkę '{tab_name}' "
                      f"({len(tab_spec.get('fields') or [])} pól)")
                tab_id = None
            else:
                r = self._req("POST", f"/rest/api/3/screens/{screen_id}/tabs",
                              json={"name": tab_name})
                if r.status_code >= 300:
                    self._fail(f"Tworzenie zakładki '{tab_name}' w '{screen_name}'", r)
                    continue
                tab_id = r.json()["id"]
                print(f"      zakładka '{tab_name}' -> {tab_id}")

            on_tab_names, on_tab_ids = set(), set()
            if tab_id is not None:
                rf = self._req("GET", f"/rest/api/3/screens/{screen_id}/tabs/{tab_id}/fields")
                if rf.status_code < 300:
                    on_tab_names = {f["name"] for f in rf.json()}
                    on_tab_ids = {f["id"] for f in rf.json()}

            for field_name in tab_spec.get("fields") or []:
                if field_name in on_tab_names:
                    continue
                field_id = self._resolve_field_id(field_name, all_fields, by_key, by_schema)
                if not field_id:
                    self.errors.append(
                        f"Pole '{field_name}' (ekran '{screen_name}') nie istnieje w Jirze")
                    print(f"      [BŁĄD] Pole '{field_name}' nie istnieje — pomijam.")
                    continue
                if field_id in on_tab_ids:
                    continue
                if self.dry_run:
                    print(f"      [DRY-RUN] Dodałbym pole '{field_name}' -> '{tab_name}'")
                    continue
                r = self._req("POST", f"/rest/api/3/screens/{screen_id}/tabs/{tab_id}/fields",
                              json={"fieldId": field_id})
                if r.status_code < 300:
                    print(f"      + '{field_name}' -> '{tab_name}'")
                if r.status_code >= 300 and "already" not in r.text.lower():
                    self._fail(f"Dodawanie '{field_name}' do '{screen_name}/{tab_name}'", r)

    def _order_screen_tabs(self, screen_id, screen_name: str, spec: dict) -> None:
        """Ustawia kolejność zakładek jak w YAML (Jira dopisuje nowe zakładki na końcu,
        a eksport PDF podawał je alfabetycznie). Zakładki spoza YAML zostają na końcu."""
        wanted = [t["name"] for t in spec.get("tabs") or []]
        resp = self._req("GET", f"/rest/api/3/screens/{screen_id}/tabs")
        if resp.status_code >= 300:
            return
        current = [t for t in resp.json()]
        names_now = [t["name"] for t in current]
        target = [n for n in wanted if n in names_now]
        if names_now[:len(target)] == target:
            return
        if self.dry_run:
            print(f"      [DRY-RUN] Kolejność zakładek: {names_now} -> {target + [n for n in names_now if n not in target]}")
            return
        for pos, name in enumerate(target):
            tab = next(t for t in current if t["name"] == name)
            if names_now.index(name) == pos:
                continue
            r = self._req("POST", f"/rest/api/3/screens/{screen_id}/tabs/{tab['id']}/move/{pos}")
            if r.status_code >= 300:
                self._fail(f"Przestawienie zakładki '{name}' w '{screen_name}'", r)
                return
            names_now.remove(name)
            names_now.insert(pos, name)
        print(f"      kolejność zakładek: {', '.join(names_now)}")

    # ---------------------------------------------------------------- screen scheme

    def build_screen_schemes(self) -> None:
        """Buduje WSZYSTKIE screen schemes. Obsługuje 'screen_schemes:' (lista)
        i stary 'screen_scheme:' (pojedynczy). Operacje: default (wymagany
        przez Jirę), create/edit/view (opcjonalne nadpisania)."""
        specs = list(self.schema.get("screen_schemes") or [])
        if self.schema.get("screen_scheme"):
            specs.insert(0, self.schema["screen_scheme"])
        if not specs:
            return
        print(f"\n=== SCREEN SCHEMES ({len(specs)}) ===")

        existing = {s["name"]: s["id"] for s in self._get_paginated("/rest/api/3/screenscheme")}
        # Ekrany szukane w Jirze, nie tylko w stanie lokalnym — żeby działało
        # też przy '--only screen_scheme' na świeżym jira_schema_state.json.
        screens_by_name = {s["name"]: s["id"] for s in self._get_paginated("/rest/api/3/screens")}

        for spec in specs:
            name = self._prefixed(spec["name"])
            if name in existing:
                print(f"  [ISTNIEJE] '{name}' -> {existing[name]} (nie aktualizuję)")
                self.state["screen_schemes"][name] = existing[name]
                continue
            if self.dry_run:
                ops = ", ".join(f"{op}='{spec[op]}'" for op in ("default", "create", "edit", "view") if spec.get(op))
                print(f"  [DRY-RUN] Utworzyłbym '{name}': {ops}")
                continue

            screens, ok = {}, True
            for op in ("default", "create", "edit", "view"):
                if not spec.get(op):
                    continue
                screen_name = self._prefixed(spec[op])
                sid = screens_by_name.get(screen_name) or self.state["screens"].get(screen_name)
                if not sid:
                    self.errors.append(f"Screen scheme '{name}': brak ekranu '{spec[op]}' ({op})")
                    print(f"  [BŁĄD] '{name}': nie ma ekranu '{spec[op]}' dla operacji '{op}'")
                    ok = False
                    break
                screens[op] = int(sid)
            if not ok:
                continue
            if "default" not in screens:
                self.errors.append(f"Screen scheme '{name}': brak wymaganego 'default'")
                print(f"  [BŁĄD] '{name}': Jira wymaga ekranu 'default'")
                continue

            resp = self._req("POST", "/rest/api/3/screenscheme", json={
                "name": name, "description": spec.get("description", ""), "screens": screens,
            })
            if resp.status_code >= 300:
                self._fail(f"Tworzenie screen scheme '{name}'", resp)
                continue
            sid = resp.json()["id"]
            print(f"  [UTWORZONO] '{name}' -> {sid}")
            self.state["screen_schemes"][name] = sid

    # ---------------------------------------------------------------- typy zgłoszeń

    @staticmethod
    def _norm_type(name: str) -> str:
        """'Sub-Task' / 'Sub-task' / 'Subtask' -> 'subtask'."""
        return "".join(ch for ch in name.lower() if ch.isalnum())

    def _load_issue_types(self, force: bool = False) -> None:
        """Indeksy typów: globalne (norm_name -> [(id, name)]) i team-managed
        (norm_name -> [id projektu]) — te drugie tylko do czytelnych ostrzeżeń."""
        if getattr(self, "_issue_type_index", None) is not None and not force:
            return
        self._issue_type_index, self._issue_type_tm_index = {}, {}
        r = self._req("GET", "/rest/api/3/issuetype")
        if r.status_code >= 300:
            self._fail("Pobieranie typów zgłoszeń", r)
            return
        for t in r.json():
            key = self._norm_type(t["name"])
            if t.get("scope"):
                proj = ((t["scope"].get("project") or {}).get("id")) or "?"
                self._issue_type_tm_index.setdefault(key, []).append(str(proj))
            else:
                self._issue_type_index.setdefault(key, []).append((t["id"], t["name"]))

    def build_issue_types(self) -> None:
        """Tworzy GLOBALNE typy zgłoszeń z sekcji 'issue_types', których brakuje.
        Istniejących nie zmienia. Uwaga: nowy typ trzeba jeszcze dodać do
        issue type scheme projektu, żeby dało się go w nim używać."""
        specs = self.schema.get("issue_types") or []
        if not specs:
            return
        print(f"\n=== TYPY ZGŁOSZEŃ ({len(specs)}) ===")
        self._load_issue_types(force=True)
        created = False
        for spec in specs:
            name = spec["name"]
            key = self._norm_type(name)
            if key in self._issue_type_index:
                ids = ", ".join(i for i, _ in self._issue_type_index[key])
                print(f"  [ISTNIEJE] '{name}' -> {ids}")
                continue
            if key in self._issue_type_tm_index:
                print(f"  [INFO] '{name}' istnieje tylko w projekcie team-managed "
                      f"({', '.join(self._issue_type_tm_index[key])}) — tworzę osobny, globalny")
            is_subtask = spec.get("type", "standard") == "subtask"
            if self.dry_run:
                print(f"  [DRY-RUN] Utworzyłbym typ '{name}' ({'podzadanie' if is_subtask else 'standardowy'})")
                continue
            resp = self._req("POST", "/rest/api/3/issuetype", json={
                "name": name, "description": spec.get("description", ""),
                "hierarchyLevel": -1 if is_subtask else 0,
            })
            if resp.status_code >= 300:
                self._fail(f"Tworzenie typu zgłoszenia '{name}'", resp)
                continue
            print(f"  [UTWORZONO] '{name}' -> {resp.json().get('id')}")
            created = True
        if created:
            self._issue_type_index = None  # kolejne kroki mają zobaczyć nowe typy

    def build_issue_type_scheme(self) -> None:
        """Issue type scheme (w UI: Work type scheme) — lista typów dostępnych
        w projekcie. Osobny dla KlientX, żeby nie dokładać typów do schematu
        współdzielonego z innymi projektami. Jeśli istnieje, DOKŁADA brakujące
        typy (niczego nie usuwa)."""
        spec = self.schema.get("issue_type_scheme")
        if not spec:
            return
        name = self._prefixed(spec["name"])
        print(f"\n=== ISSUE TYPE SCHEME: {name} ===")

        type_ids = self._resolve_issue_types(spec.get("issue_types") or [], f"Issue type scheme '{name}'")
        default_ids = self._resolve_issue_types([spec["default"]], f"Issue type scheme '{name}' (domyślny)") \
            if spec.get("default") else []
        default_id = default_ids[0] if default_ids else None
        if default_id and default_id not in type_ids:
            type_ids.insert(0, default_id)

        existing = {s["name"]: s["id"] for s in self._get_paginated("/rest/api/3/issuetypescheme")}
        if self.dry_run:
            print(f"  [DRY-RUN] {'Uzupełniłbym' if name in existing else 'Utworzyłbym'} '{name}': "
                  f"{len(type_ids)} typów, domyślny={spec.get('default')}")
            return

        if name in existing:
            its_id = existing[name]
            self.state.setdefault("issue_type_schemes", {})[name] = its_id
            current = self._get_paginated("/rest/api/3/issuetypescheme/mapping",
                                          params={"issueTypeSchemeId": its_id})
            have = {str(c["issueTypeId"]) for c in current}
            missing = [t for t in type_ids if t not in have]
            if not missing:
                print(f"  [ISTNIEJE] -> {its_id}, wszystkie typy są")
                return
            r = self._req("PUT", f"/rest/api/3/issuetypescheme/{its_id}/issuetype",
                          json={"issueTypeIds": missing})
            if r.status_code >= 300:
                self._fail(f"Dokładanie typów do '{name}'", r)
            else:
                print(f"  [ISTNIEJE] -> {its_id}, dołożono {len(missing)} typów")
            return

        if not type_ids:
            self.errors.append(f"Issue type scheme '{name}': żaden typ nie został znaleziony")
            print("  [BŁĄD] Brak typów do dodania")
            return
        body = {"name": name, "description": spec.get("description", ""), "issueTypeIds": type_ids}
        if default_id:
            body["defaultIssueTypeId"] = default_id
        resp = self._req("POST", "/rest/api/3/issuetypescheme", json=body)
        if resp.status_code >= 300:
            self._fail(f"Tworzenie issue type scheme '{name}'", resp)
            return
        its_id = resp.json().get("issueTypeSchemeId") or resp.json().get("id")
        print(f"  [UTWORZONO] -> {its_id} ({len(type_ids)} typów)")
        self.state.setdefault("issue_type_schemes", {})[name] = its_id

    def _resolve_issue_types(self, names: list, context: str) -> list:
        """Nazwy typów zgłoszeń -> ID. Pomija typy team-managed (mają 'scope'
        — schematy globalne ich nie widzą; ten sam problem co ze statusem
        'Done' 10233). Jedna nazwa może pasować do KILKU typów (np. 'Sub-Task'
        do regularnego 'Sub-task' i legacy 'Subtask' 10059) — mapujemy wszystkie."""
        self._load_issue_types()
        ids = []
        for n in names:
            found = self._issue_type_index.get(self._norm_type(n))
            if not found:
                tm = self._issue_type_tm_index.get(self._norm_type(n))
                where = (f" (istnieje tylko w projekcie team-managed, id projektu: {', '.join(tm)})"
                         if tm else "")
                msg = (f"{context}: typ zgłoszenia '{n}' nie istnieje globalnie{where} — pomijam. "
                       f"Dodaj go do sekcji 'issue_types' i uruchom --only issue_types")
                print(f"  [UWAGA] {msg}")
                self.warnings.append(msg)
                continue
            if len(found) > 1:
                print(f"  [INFO] '{n}' pasuje do {len(found)} typów: "
                      + ", ".join(f"{i}='{nm}'" for i, nm in found) + " — mapuję wszystkie")
            ids.extend(i for i, _ in found)
        return list(dict.fromkeys(ids))

    # ---------------------------------------------------------------- ITSS

    def build_issue_type_screen_scheme(self) -> None:
        """Work type screen scheme: typ zgłoszenia -> screen scheme.
        Jeśli ITSS już istnieje, DOKŁADA brakujące mapowania (np. Acceptance/
        Question, gdy ich screen schemes powstaną później). Istniejących
        mapowań nie zmienia."""
        spec = self.schema.get("issue_type_screen_scheme")
        if not spec:
            return
        name = self._prefixed(spec["name"])
        print(f"\n=== ISSUE TYPE SCREEN SCHEME: {name} ===")

        existing = {s["name"]: s["id"]
                    for s in self._get_paginated("/rest/api/3/issuetypescreenscheme")}
        ss_by_name = {s["name"]: s["id"] for s in self._get_paginated("/rest/api/3/screenscheme")}

        def ss_id(n):
            n = self._prefixed(n)
            return ss_by_name.get(n) or self.state["screen_schemes"].get(n)

        mappings, seen = [], set()
        for m in spec.get("mappings") or []:
            sid = ss_id(m["screen_scheme"])
            if not sid and not self.dry_run:
                msg = (f"ITSS '{name}': screen scheme '{m['screen_scheme']}' nie istnieje — "
                       f"typy {m['issue_types']} dostaną domyślny")
                print(f"  [UWAGA] {msg}")
                self.warnings.append(msg)
                continue
            for it in self._resolve_issue_types(m["issue_types"], f"ITSS '{name}'"):
                if it not in seen:
                    seen.add(it)
                    mappings.append({"issueTypeId": it, "screenSchemeId": str(sid)})

        if self.dry_run:
            print(f"  [DRY-RUN] {'Uzupełniłbym' if name in existing else 'Utworzyłbym'} '{name}': "
                  f"domyślny='{spec.get('default_screen_scheme')}', {len(mappings)} mapowań typów")
            return

        if name in existing:
            itss_id = existing[name]
            self.state["issue_type_screen_schemes"][name] = itss_id
            current = self._get_paginated("/rest/api/3/issuetypescreenscheme/mapping",
                                          params={"issueTypeScreenSchemeId": itss_id})
            have = {c["issueTypeId"] for c in current}
            missing = [m for m in mappings if m["issueTypeId"] not in have]
            if not missing:
                print(f"  [ISTNIEJE] -> {itss_id}, wszystkie mapowania są")
                return
            r = self._req("PUT", f"/rest/api/3/issuetypescreenscheme/{itss_id}/mapping",
                          json={"issueTypeMappings": missing})
            if r.status_code >= 300:
                self._fail(f"Dokładanie mapowań do ITSS '{name}'", r)
            else:
                print(f"  [ISTNIEJE] -> {itss_id}, dołożono {len(missing)} mapowań")
            return

        default_id = ss_id(spec["default_screen_scheme"])
        if not default_id:
            self.errors.append(f"ITSS '{name}': brak domyślnego screen scheme '{spec['default_screen_scheme']}'")
            print(f"  [BŁĄD] Nie ma domyślnego screen scheme '{spec['default_screen_scheme']}'")
            return

        resp = self._req("POST", "/rest/api/3/issuetypescreenscheme", json={
            "name": name, "description": spec.get("description", ""),
            # "default" = typy bez własnego wpisu (wymagane przez Jirę)
            "issueTypeMappings": [{"issueTypeId": "default", "screenSchemeId": str(default_id)}] + mappings,
        })
        if resp.status_code >= 300:
            self._fail(f"Tworzenie ITSS '{name}'", resp)
            return
        itss_id = resp.json()["id"]
        print(f"  [UTWORZONO] -> {itss_id} ({len(mappings)} mapowań + domyślne)")
        self.state["issue_type_screen_schemes"][name] = itss_id

    # ---------------------------------------------------------------- workflow scheme

    def build_workflow_scheme(self) -> None:
        """Workflow scheme: typ zgłoszenia -> workflow. Mapowania na workflow,
        których jeszcze nie ma w Jirze (np. 'incomplete' Bug/Task), są pomijane
        z ostrzeżeniem — takie typy dostają domyślny workflow. Gdy scheme już
        istnieje, DOKŁADA brakujące mapowania."""
        spec = self.schema.get("workflow_scheme")
        if not spec:
            return
        name = self._prefixed(spec["name"])
        print(f"\n=== WORKFLOW SCHEME: {name} ===")

        existing = {s["name"]: s["id"] for s in self._get_paginated("/rest/api/3/workflowscheme")}
        wf_names = {}
        for w in self._get_paginated("/rest/api/3/workflow/search"):
            n = w.get("id", {}).get("name") or w.get("name") or ""
            wf_names[n.strip().lower()] = n

        def wf(n):
            return wf_names.get(self._prefixed(n).strip().lower())

        mappings = {}
        for m in spec.get("mappings") or []:
            wname = wf(m["workflow"])
            if not wname and not self.dry_run:
                msg = (f"Workflow scheme '{name}': workflow '{m['workflow']}' nie istnieje w Jirze — "
                       f"typy {m['issue_types']} dostaną domyślny (uzupełnij później)")
                print(f"  [UWAGA] {msg}")
                self.warnings.append(msg)
                continue
            for it in self._resolve_issue_types(m["issue_types"], f"Workflow scheme '{name}'"):
                mappings.setdefault(it, wname or m["workflow"])

        default_wf = None
        if spec.get("default_workflow"):
            default_wf = wf(spec["default_workflow"])
            if not default_wf and not self.dry_run:
                print(f"  [UWAGA] Domyślny workflow '{spec['default_workflow']}' nie istnieje — "
                      f"Jira użyje systemowego 'jira'")

        if self.dry_run:
            print(f"  [DRY-RUN] {'Uzupełniłbym' if name in existing else 'Utworzyłbym'} '{name}': "
                  f"{len(mappings)} mapowań typów, domyślny={spec.get('default_workflow') or '(systemowy)'}")
            return

        if name in existing:
            ws_id = existing[name]
            self.state["workflow_schemes"][name] = ws_id
            r = self._req("GET", f"/rest/api/3/workflowscheme/{ws_id}")
            have = (r.json().get("issueTypeMappings") or {}) if r.status_code < 300 else {}
            added = 0
            for it, wname in mappings.items():
                if it in have:
                    continue
                r = self._req("PUT", f"/rest/api/3/workflowscheme/{ws_id}/issuetype/{it}",
                              json={"issueType": it, "workflow": wname, "updateDraftIfNeeded": False})
                if r.status_code >= 300:
                    # Aktywny scheme (podpięty do projektu) wymaga szkicu i publikacji
                    # z migracją statusów — tego skrypt celowo nie robi.
                    self._fail(f"Dokładanie typu {it} -> '{wname}' do '{name}' "
                               f"(jeśli scheme jest już podpięty do projektu, zrób to w UI)", r)
                else:
                    added += 1
            print(f"  [ISTNIEJE] -> {ws_id}, dołożono {added} mapowań")
            return

        body = {"name": name, "description": spec.get("description", ""),
                "issueTypeMappings": mappings}
        if default_wf:
            body["defaultWorkflow"] = default_wf
        resp = self._req("POST", "/rest/api/3/workflowscheme", json=body)
        if resp.status_code >= 300:
            self._fail(f"Tworzenie workflow scheme '{name}'", resp)
            return
        ws_id = resp.json()["id"]
        print(f"  [UTWORZONO] -> {ws_id} ({len(mappings)} mapowań typów)")
        self.state["workflow_schemes"][name] = ws_id

    # ---------------------------------------------------------------- podpięcie

    def apply_to_projects(self) -> None:
        keys = self.schema.get("apply_to_projects") or []
        if not keys:
            return
        print(f"\n=== PODPIĘCIE DO PROJEKTÓW ({len(keys)}) ===")

        itss_name = self._prefixed((self.schema.get("issue_type_screen_scheme") or {}).get("name", ""))
        ws_name = self._prefixed((self.schema.get("workflow_scheme") or {}).get("name", ""))
        itss_id = self.state["issue_type_screen_schemes"].get(itss_name)
        ws_id = self.state["workflow_schemes"].get(ws_name)

        for key in keys:
            if self.dry_run:
                print(f"  [DRY-RUN] Podpiąłbym schematy do {key}")
                continue
            r = self._req("GET", f"/rest/api/3/project/{key}")
            if r.status_code >= 300:
                self._fail(f"Pobieranie projektu {key}", r)
                continue
            project_id = r.json()["id"]

            if itss_id:
                r = self._req("PUT", f"/rest/api/3/issuetypescreenscheme/project",
                              json={"issueTypeScreenSchemeId": str(itss_id),
                                    "projectId": str(project_id)})
                print(f"  {key}: ITSS {'OK' if r.status_code < 300 else 'BŁĄD'}")
                if r.status_code >= 300:
                    self._fail(f"Podpięcie ITSS do {key}", r)

            if ws_id:
                # Podpięcie workflow scheme uruchamia ASYNCHRONICZNĄ migrację
                # statusów istniejących zadań - stąd osobny endpoint i zwracany taskId.
                r = self._req("PUT", "/rest/api/3/workflowscheme/project",
                              json={"workflowSchemeId": str(ws_id), "projectId": str(project_id)})
                print(f"  {key}: workflow scheme {'OK' if r.status_code < 300 else 'BŁĄD'}")
                if r.status_code >= 300:
                    self._fail(f"Podpięcie workflow scheme do {key}", r)

    # ---------------------------------------------------------------- run

    # ---------------------------------------------------------------- aktualizacja workflow

    def _lookup_global_status(self, name: str):
        """Globalny status o danej nazwie (do dodania do istniejącego workflow)."""
        for st in self._get_paginated("/rest/api/3/statuses/search", params={"searchString": name}):
            if st["name"].strip().lower() == name.strip().lower() and \
                    (st.get("scope") or {}).get("type") != "PROJECT":
                return st
        return None

    def update_workflows(self) -> None:
        """Aktualizuje ISTNIEJĄCE workflow do stanu z YAML: przejścia (nazwy, źródła,
        cele, id), układ diagramu i START. Uruchamiane tylko przez
        --only workflow_update (najpierw z --dry-run = walidacja Jiry).

        - Statusy są dopasowywane po nazwie; brakujące są dodawane (status globalny).
        - Usunięcia statusu NIE robi — wymagałoby przeniesienia zgłoszeń do innego
          statusu; taki workflow jest pomijany z komunikatem.
        - Reguły (akcje, warunki, walidatory) istniejącego przejścia o tej samej
          nazwie i celu są przenoszone, żeby aktualizacja ich nie skasowała."""
        specs = self._workflow_specs()
        if self.only_workflows:
            wanted = {n.strip().lower() for n in self.only_workflows}
            specs = [sp for sp in specs if self._prefixed(sp["name"]).strip().lower() in wanted]
        print(f"\n=== AKTUALIZACJA WORKFLOW ({len(specs)}) ===")
        keep_status = ("id", "name", "statusCategory", "statusReference", "description")

        for spec in specs:
            name = self._prefixed(spec["name"])
            print(f"\n  --- {name} ---")
            if spec.get("incomplete") and not self.include_incomplete:
                print("    [POMINIĘTO] 'incomplete: true' — uzupełnij YAML albo użyj --include-incomplete")
                continue
            r = self._req("POST", "/rest/api/3/workflows", json={"workflowNames": [name]})
            if r.status_code >= 300:
                self._fail(f"Pobieranie workflow '{name}'", r)
                continue
            data = r.json()
            wf = next((w for w in data.get("workflows", []) if w.get("name") == name), None)
            if not wf:
                print("    [POMINIĘTO] nie istnieje w Jirze — utwórz go krokiem statuses_workflow")
                continue

            top = {st["statusReference"]: st for st in data.get("statuses", [])}
            in_wf = [st["statusReference"] for st in wf.get("statuses", [])]
            by_lower = {top[r_]["name"].strip().lower(): r_ for r_ in in_wf if r_ in top}
            desired = spec.get("statuses") or []
            ref_of, new_top, missing = {}, [], []
            for n in desired:
                r_ = by_lower.get(n.strip().lower())
                if r_:
                    ref_of[n] = r_
                    continue
                g = self._lookup_global_status(n)
                if not g:
                    missing.append(n)
                    continue
                ref = str(uuid.uuid4())
                ref_of[n] = ref
                cat = g.get("statusCategory")
                cat = cat if isinstance(cat, str) else {"new": "TODO", "indeterminate": "IN_PROGRESS",
                                                        "done": "DONE"}.get((cat or {}).get("key"), "TODO")
                new_top.append({"id": int(g["id"]) if str(g["id"]).isdigit() else g["id"],
                                "name": g["name"], "statusCategory": cat, "statusReference": ref,
                                "description": g.get("description", "")})
            if missing:
                self.errors.append(f"Workflow '{name}': brak w Jirze statusów {missing}")
                print(f"    [BŁĄD] Statusy nieistniejące w Jirze: {missing} — pomijam workflow")
                continue
            desired_lower = {n.strip().lower() for n in desired}
            removed = [top[r_]["name"] for r_ in in_wf if r_ in top and top[r_]["name"].strip().lower() not in desired_lower]
            if removed:
                self.errors.append(f"Workflow '{name}': YAML usuwa statusy {removed} — wymaga ręcznej migracji")
                print(f"    [BŁĄD] YAML nie zawiera statusów obecnych w workflow: {removed}. Usunięcie "
                      f"wymaga przeniesienia zgłoszeń — zrób to w edytorze albo dopisz je do YAML. Pomijam.")
                continue

            initial = spec.get("initial_status") or desired[0]
            coords, start = spec_layout(spec, desired, initial)
            keep_rules = {(t["name"].strip().lower(), t.get("toStatusReference")): t
                          for t in wf.get("transitions", []) if any(t.get(k) for k in _RULE_KEYS)}
            transitions = build_transitions(spec, initial, ref_of, coords, keep_rules)

            # Podsumowanie zmian przejść (po nazwie, źródle i celu)
            def link_set(trs, name_of):
                out = set()
                for t in trs:
                    if t.get("type") == "INITIAL":
                        continue
                    to = name_of(t.get("toStatusReference"))
                    for l in (t.get("links") or [{"fromStatusReference": None}]):
                        f = name_of(l.get("fromStatusReference")) if l.get("fromStatusReference") else "ANY"
                        out.add((t["name"], f, to))
                return out
            name_now = lambda r_: (top.get(r_) or {}).get("name", r_)
            name_new = {v: k for k, v in ref_of.items()}.get
            before = {(n, f.lower(), t.lower()) for n, f, t in link_set(wf.get("transitions", []), name_now)}
            after = {(n, f.lower(), t.lower()) for n, f, t in link_set(transitions, name_new)}
            add, rem = sorted(after - before), sorted(before - after)
            print(f"    przejścia: +{len(add)} / -{len(rem)} (strzałki z nazwą), statusy dodane: "
                  f"{[st['name'] for st in new_top] or 'brak'}")
            for n, f, t in add:
                print(f"      + {n}: {f} -> {t}")
            for n, f, t in rem:
                print(f"      - {n}: {f} -> {t}")

            wf_payload = {
                "id": wf["id"], "version": wf["version"], "description": wf.get("description", ""),
                "statuses": [{"statusReference": ref_of[n],
                              "layout": {"x": coords[n][0], "y": coords[n][1]},
                              "properties": (next((st.get("properties") for st in wf.get("statuses", [])
                                                   if st["statusReference"] == ref_of[n]), None) or {})}
                             for n in desired],
                "transitions": transitions,
                "startPointLayout": start,
            }
            if wf.get("loopedTransitionContainerLayout"):
                wf_payload["loopedTransitionContainerLayout"] = wf["loopedTransitionContainerLayout"]
            payload = {"statuses": [{k: top[r_][k] for k in keep_status if k in top[r_]} for r_ in in_wf if r_ in top]
                                   + new_top,
                       "workflows": [wf_payload]}
            self._send_workflow_update(name, payload, "przejścia i układ")

    # ---------------------------------------------------------------- układ diagramów

    @staticmethod
    def _with_ports(t: dict, coords: dict, ref_name: dict) -> dict:
        """Ustawia fromPort/toPort w linkach przejścia kierunkowego wg układu
        (choose_ports). Bez LAYOUT_PORTS — przejście bez zmian."""
        if t.get("type") != "DIRECTED" or not t.get("links") or not LAYOUT_PORTS:
            return t
        dst = t.get("toStatusReference")
        new_links = []
        for link in t["links"]:
            src = link.get("fromStatusReference")
            ports = None
            if src in coords and dst in coords and src != dst:
                ports = choose_ports(coords[src], coords[dst],
                                     estimate_box_width(ref_name.get(src, "")),
                                     estimate_box_width(ref_name.get(dst, "")))
            new_links.append({**link, "fromPort": ports[0], "toPort": ports[1]} if ports else link)
        return {**t, "links": new_links}

    def relayout_workflows(self) -> None:
        """Przelicza układ diagramu ISTNIEJĄCYCH workflow (tylko pozycje statusów —
        statusy, przejścia i reguły zostają bez zmian). Graf przejść brany jest
        z samej Jiry, nie z YAML, więc działa też dla workflow uzupełnionych
        ręcznie w edytorze. Uruchamiane wyłącznie przez --only workflow_layout
        (nie w pełnym przebiegu), żeby nie nadpisywać ręcznych poprawek układu.

        --dry-run: payload jest sprawdzany endpointem walidacji Jiry
        (/workflows/update/validation) — nic nie zostaje zmienione."""
        specs = self._workflow_specs()
        names = [self._prefixed(s["name"]) for s in specs]
        spec_by_name = {self._prefixed(s["name"]).strip().lower(): s for s in specs}
        if self.only_workflows:
            wanted = {n.strip().lower() for n in self.only_workflows}
            names = [n for n in names if n.strip().lower() in wanted]
        print(f"\n=== UKŁAD DIAGRAMÓW ({len(names)}) ===")

        keep_status = ("id", "name", "statusCategory", "statusReference", "description")
        keep_wf_status = ("statusReference", "layout", "properties", "deprecated")
        keep_transition = ("id", "name", "description", "type", "toStatusReference", "links",
                           "actions", "conditions", "validators", "triggers", "properties")

        for name in names:
            print(f"\n  --- {name} ---")
            r = self._req("POST", "/rest/api/3/workflows", json={"workflowNames": [name]})
            if r.status_code >= 300:
                self._fail(f"Pobieranie workflow '{name}'", r)
                continue
            data = r.json()
            wf = next((w for w in data.get("workflows", []) if w.get("name") == name), None)
            if not wf:
                print(f"  [POMINIĘTO] '{name}' nie istnieje w Jirze")
                continue

            ref_name = {s.get("statusReference"): s.get("name") for s in data.get("statuses", [])}
            refs = [s["statusReference"] for s in wf.get("statuses", [])]
            initial, edges = None, []
            for t in wf.get("transitions", []):
                if t.get("type") == "INITIAL":
                    initial = t.get("toStatusReference")
                elif t.get("type") == "DIRECTED":
                    for link in t.get("links") or []:
                        edges.append((link.get("fromStatusReference"), t.get("toStatusReference")))
            spec = spec_by_name.get(name.strip().lower(), {})
            lay = {k.strip().lower(): v for k, v in (spec.get("layout") or {}).items()}
            if lay and all((ref_name.get(r_) or "").strip().lower() in lay for r_ in refs):
                coords = {r_: tuple(map(float, lay[ref_name[r_].strip().lower()])) for r_ in refs}
                sl = spec.get("start_layout")
                start = {"x": float(sl[0]), "y": float(sl[1])} if sl else start_point_layout(coords, initial)
                print("    układ z YAML (pozycje z diagramu KlientX)")
            else:
                coords, cols = compute_workflow_layout(refs, initial, edges, labels=ref_name)
                start = start_point_layout(coords, initial)
                for L, col in cols.items():
                    print(f"    wiersz {L}: " + " | ".join(ref_name.get(r_, r_) for r_ in col))

            wf_payload = {
                "id": wf["id"],
                "version": wf["version"],
                "description": wf.get("description", ""),
                "statuses": [
                    {**{k: s[k] for k in keep_wf_status if k in s},
                     "layout": {"x": coords[s["statusReference"]][0],
                                "y": coords[s["statusReference"]][1]}}
                    for s in wf.get("statuses", [])
                ],
                "transitions": [self._with_ports({k: t[k] for k in keep_transition if k in t},
                                                 coords, ref_name)
                                for t in wf.get("transitions", [])],
            }
            # START zawsze przy statusie początkowym (stary bywał na końcu diagramu)
            wf_payload["startPointLayout"] = start
            if wf.get("loopedTransitionContainerLayout"):
                wf_payload["loopedTransitionContainerLayout"] = wf["loopedTransitionContainerLayout"]
            payload = {
                "statuses": [{k: s[k] for k in keep_status if k in s}
                             for s in data.get("statuses", [])],
                "workflows": [wf_payload],
            }

            self._send_workflow_update(name, payload, "układ")

    def _send_workflow_update(self, name: str, payload: dict, what: str) -> bool:
        """--dry-run: sprawdza payload endpointem walidacji Jiry (nic nie zapisuje);
        inaczej zapisuje przez /workflows/update. Zwraca True przy powodzeniu."""
        if self.dry_run:
            v = self._req("POST", "/rest/api/3/workflows/update/validation", json={
                "payload": payload, "validationOptions": {"levels": ["ERROR", "WARNING"]}})
            if v.status_code >= 300:
                self._fail(f"Walidacja ({what}) '{name}'", v)
                return False
            problems = v.json().get("errors") or []
            for p in problems:
                print(f"    [{p.get('level', '?')}] {p.get('message')}")
            if problems:
                self.warnings.append(f"{what.capitalize()} '{name}': walidacja zgłosiła {len(problems)} uwag")
                return False
            print(f"    [DRY-RUN] Walidacja Jiry OK — {what} można zapisać")
            return True
        u = self._req("POST", "/rest/api/3/workflows/update", json=payload)
        if u.status_code >= 300:
            self._fail(f"Zapis ({what}) '{name}'", u)
            return False
        print(f"    [ZAKTUALIZOWANO] {what}: '{name}'")
        return True

    def run(self, only=None) -> None:
        """only: None (pełny przebieg), nazwa kroku albo lista nazw.
        Kroki zawsze wykonują się w kolejności z listy poniżej."""
        if isinstance(only, str):
            only = [only]
        only = set(only or [])
        steps = [
            ("custom_fields", self.build_custom_fields),
            ("issue_types", self.build_issue_types),
            ("issue_type_scheme", self.build_issue_type_scheme),
            ("statuses_workflow", lambda: self.build_workflows(self.resolve_statuses())),
            ("screens", self.build_screens),
            ("screen_scheme", self.build_screen_schemes),
            ("issue_type_screen_scheme", self.build_issue_type_screen_scheme),
            ("workflow_scheme", self.build_workflow_scheme),
            ("apply", self.apply_to_projects),
            # Tylko na wyraźne żądanie (--only workflow_layout) — nie w pełnym
            # przebiegu, bo nadpisałby ręczne poprawki układu w edytorze.
            ("workflow_layout", self.relayout_workflows),
            ("workflow_update", self.update_workflows),
        ]
        on_demand_only = {"workflow_layout", "workflow_update"}
        for key, fn in steps:
            if only and key not in only:
                continue
            if not only and key in on_demand_only:
                continue
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                # Izolacja błędów per krok - jeden nieudany etap nie przerywa reszty
                print(f"  [BŁĄD] Krok '{key}': {exc}")
                self.errors.append(f"Krok '{key}': {exc}")
            self._save_state()


STEP_NAMES = ["custom_fields", "issue_types", "issue_type_scheme", "statuses_workflow",
              "screens", "screen_scheme", "issue_type_screen_scheme", "workflow_scheme",
              "apply", "workflow_layout", "workflow_update"]


def main() -> None:
    setup_file_logging("build_jira_schema")
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--schema", default="jira_schema.yaml", help="Ścieżka do pliku YAML.")
    p.add_argument("--dry-run", action="store_true", help="Pokaż co by się stało, nic nie zmieniaj.")
    p.add_argument("--only", default=None,
                   help="Uruchom tylko wybrane kroki, po przecinku (wykonywane w stałej "
                        "kolejności): " + ", ".join(STEP_NAMES))
    p.add_argument("--include-incomplete", action="store_true",
                   help="Twórz też workflow oznaczone 'incomplete: true' (brakujące przejścia). "
                        "UWAGA: skrypt nie aktualizuje istniejących workflow — późniejsze "
                        "uzupełnienie przejść trzeba będzie zrobić ręcznie w Jirze.")
    p.add_argument("--workflow", default=None,
                   help="Dla --only workflow_layout / workflow_update: nazwy workflow po przecinku "
                        "(np. do testu na jednym). Domyślnie: wszystkie z YAML.")
    args = p.parse_args()
    only = None
    if args.only:
        only = [k.strip() for k in args.only.split(",") if k.strip()]
        bad = [k for k in only if k not in STEP_NAMES]
        if bad:
            p.error(f"nieznany krok w --only: {', '.join(bad)} "
                    f"(dostępne: {', '.join(STEP_NAMES)})")

    schema_path = Path(args.schema)
    if not schema_path.exists():
        print(f"[BŁĄD] Nie znaleziono pliku schematu: {schema_path}")
        sys.exit(1)
    schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))

    print(f"Schemat: {schema_path}")
    if args.dry_run:
        print("[TRYB DRY-RUN — nic nie zostanie zmienione]")

    builder = Builder(schema, args.dry_run, include_incomplete=args.include_incomplete)
    builder.only_workflows = ([w.strip() for w in args.workflow.split(",") if w.strip()]
                              if args.workflow else None)
    builder.run(only)

    print("\n" + "=" * 60)
    if builder.warnings:
        print(f"OSTRZEŻENIA ({len(builder.warnings)}) — wymagają działania ręcznego:")
        for w in builder.warnings:
            print(f"  ⚠ {w}")
    if builder.errors:
        print(f"ZAKOŃCZONO Z BŁĘDAMI ({len(builder.errors)}):")
        for e in builder.errors:
            print(f"  - {e}")
    else:
        print("ZAKOŃCZONO BEZ BŁĘDÓW" + (f" ({len(builder.warnings)} ostrzeżeń)" if builder.warnings else ""))
    print(f"Stan zapisany w: {STATE_FILE.name}")
    print("=" * 60)

    if not args.dry_run:
        parts = []
        if builder.errors:
            parts.append(f"{len(builder.errors)} błędów")
        if builder.warnings:
            parts.append(f"{len(builder.warnings)} ostrzeżeń (wymagają działania ręcznego)")
        send_slack_summary(
            f"build_jira_schema.py: zakończono"
            + (f" z {', '.join(parts)}." if parts else " bez błędów.")
        )
    sys.exit(1 if builder.errors else 0)


if __name__ == "__main__":
    main()
