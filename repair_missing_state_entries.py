#!/usr/bin/env python3
"""
repair_missing_state_entries.py

NAPRAWIA PRAWDZIWĄ PRZYCZYNĘ powracających duplikatów: task_sync_state.json
może mieć WIĘCEJ zadań w Asanie niż śledzonych wpisów (np. FMPGEU: 136 zadań
w Asanie, tylko 56 wpisów w stanie) - każde NIEŚLEDZONE zadanie wygląda dla
asana_jira_sync.py jak "nowe", więc przy KAŻDYM przebiegu tworzone jest
PONOWNIE, mimo że jego odpowiednik w Jirze już dawno istnieje (np. zachowany
przez resolve_duplicates.py). Sprzątanie duplikatów w Jirze (resolve_duplicates.py)
NIE naprawia stanu - to osobny krok, właśnie ten skrypt.

Metoda: dla KAŻDEGO zadania Asany, którego gid NIE jest jeszcze w stanie,
szuka w Jirze zadania o DOKŁADNIE takim samym tytule. Jeśli znajdzie
DOKŁADNIE JEDNO pasujące - dodaje nowy wpis do stanu (gid -> ten klucz Jiry).
Jeśli znajdzie ZERO albo WIĘCEJ NIŻ JEDEN pasujący - NIE zgaduje, zgłasza
do ręcznego przejrzenia.

DOMYŚLNIE tryb dry-run (tylko raport, nic nie zapisuje do stanu).

Użycie:
    python repair_missing_state_entries.py --project KLUCZ              # raport
    python repair_missing_state_entries.py --project KLUCZ --apply      # faktyczny zapis do stanu
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core

STATE_FILE = Path(__file__).resolve().parent / "task_sync_state.json"


def fetch_asana_tasks(project_gid):
    """Zwraca listę (gid, name, modified_at) wszystkich zadań w projekcie Asany."""
    headers = {"Authorization": f"Bearer {core.ASANA_TOKEN}"}
    url = f"https://app.asana.com/api/1.0/projects/{project_gid}/tasks"
    tasks = []
    offset = None
    while True:
        params = {"opt_fields": "name,modified_at", "limit": 100}
        if offset:
            params["offset"] = offset
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        tasks.extend((t["gid"], t["name"], t.get("modified_at")) for t in payload.get("data", []))
        offset = payload.get("next_page", {}).get("offset") if payload.get("next_page") else None
        if not offset:
            return tasks


def fetch_asana_comment_and_attachment_gids(task_gid):
    """Zwraca (lista_gid_komentarzy, lista_gid_zalacznikow) dla JEDNEGO
    zadania Asany - potrzebne, żeby oznaczyć je jako 'już zsynchronizowane'
    w naprawionym wpisie stanu. Bez tego następna synchronizacja uznałaby
    WSZYSTKIE komentarze/załączniki za nowe i dodała je ponownie - zamieniając
    naprawę duplikatów CAŁYCH zadań w nowy problem duplikatów komentarzy."""
    headers = {"Authorization": f"Bearer {core.ASANA_TOKEN}"}
    comment_gids = []
    resp = requests.get(
        f"https://app.asana.com/api/1.0/tasks/{task_gid}/stories",
        headers=headers, params={"opt_fields": "gid,type"}, timeout=30,
    )
    if resp.status_code == 200:
        comment_gids = [s["gid"] for s in resp.json().get("data", []) if s.get("type") == "comment"]

    attachment_gids = []
    resp = requests.get(
        f"https://app.asana.com/api/1.0/tasks/{task_gid}/attachments",
        headers=headers, params={"opt_fields": "gid"}, timeout=30,
    )
    if resp.status_code == 200:
        attachment_gids = [a["gid"] for a in resp.json().get("data", [])]

    return comment_gids, attachment_gids


def fetch_jira_titles(project_key):
    """Zwraca {tytuł: [klucz1, klucz2, ...]} dla WSZYSTKICH zadań w projekcie Jiry."""
    by_title = defaultdict(list)
    next_page_token = None
    while True:
        body = {"jql": f"project = {project_key}", "maxResults": 100, "fields": ["summary"]}
        if next_page_token:
            body["nextPageToken"] = next_page_token
        resp = requests.post(f"{core.JIRA_BASE_URL}/rest/api/3/search/jql", auth=core.jira_auth(), json=body)
        resp.raise_for_status()
        data = resp.json()
        for issue in data.get("issues", []):
            by_title[issue.get("fields", {}).get("summary", "")].append(issue["key"])
        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            return by_title


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Klucz projektu Jiry.")
    parser.add_argument("--apply", action="store_true", help="Faktycznie zapisz brakujące wpisy do stanu (domyślnie tylko raport).")
    args = parser.parse_args()

    print(f"Szukam wiersza w arkuszu dla {args.project}...")
    sheet_rows = core.get_sheet_rows()
    row = next((r for r in sheet_rows if r.jira_key == args.project), None)
    if not row:
        print(f"[BŁĄD] Nie znaleziono wiersza z jira_key={args.project} w arkuszu.")
        sys.exit(1)
    gid = core.extract_asana_project_gid(row.asana_project_link)

    print("Pobieram zadania z Asany...")
    asana_tasks = fetch_asana_tasks(gid)
    print(f"  {len(asana_tasks)} zadań w Asanie.")

    print("Pobieram zadania z Jiry...")
    jira_by_title = fetch_jira_titles(args.project)
    print(f"  {sum(len(v) for v in jira_by_title.values())} zadań w Jirze.")

    state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
    project_state = state.setdefault(args.project, {})
    already_tracked = set(project_state.keys())

    missing = [(g, name, mod) for g, name, mod in asana_tasks if g not in already_tracked]
    print(f"\nZadań w Asanie BEZ wpisu w stanie: {len(missing)}")

    to_repair = []       # [(gid, name, modified_at, jira_key), ...]
    ambiguous = []       # [(gid, name, matching_keys), ...]
    no_match = []        # [(gid, name), ...]

    for g, name, mod in missing:
        candidates = jira_by_title.get(name, [])
        if len(candidates) == 1:
            to_repair.append((g, name, mod, candidates[0]))
        elif len(candidates) == 0:
            no_match.append((g, name))
        else:
            ambiguous.append((g, name, candidates))

    print(f"  -> Jednoznacznie dopasowanych do istniejącego zadania w Jirze: {len(to_repair)}")
    print(f"  -> Niejednoznacznych (kilka zadań w Jirze o tym samym tytule): {len(ambiguous)}")
    print(f"  -> Bez żadnego dopasowania w Jirze (naprawdę nowe zadanie): {len(no_match)}")

    if to_repair:
        print(f"\nPrzykłady (pierwsze 15 z {len(to_repair)}) do naprawienia:")
        for g, name, mod, key in to_repair[:15]:
            print(f"  '{name}' (gid={g}) -> {key}")

    if ambiguous:
        print(f"\nNiejednoznaczne (pierwsze 10) - POMIJANE, nie zgadujemy:")
        for g, name, keys in ambiguous[:10]:
            print(f"  '{name}' (gid={g}) -> pasuje do {keys}")

    if not args.apply:
        print(f"\n[DRY-RUN] Nic nie zapisano. Uruchom z --apply, żeby dodać {len(to_repair)} brakujących wpisów do stanu.")
        return

    if not to_repair:
        print("\nBrak wpisów do naprawienia.")
        return

    print(f"\nPobieram komentarze/załączniki dla {len(to_repair)} zadań (żeby oznaczyć jako już zsynchronizowane)...")
    for i, (g, name, mod, key) in enumerate(to_repair, 1):
        comment_gids, attachment_gids = fetch_asana_comment_and_attachment_gids(g)
        project_state[g] = {
            "jira_key": key,
            "asana_modified_at": mod,
            "synced_comment_gids": sorted(comment_gids),
            "synced_attachment_gids": sorted(attachment_gids),
        }
        if i % 10 == 0 or i == len(to_repair):
            print(f"  [{i}/{len(to_repair)}]")

    tmp_path = STATE_FILE.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(STATE_FILE)
    print(f"\nZapisano {len(to_repair)} nowych wpisów do task_sync_state.json (z pełnym oznaczeniem komentarzy/załączników jako już zsynchronizowanych).")


if __name__ == "__main__":
    main()
