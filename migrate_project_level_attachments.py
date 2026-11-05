#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
migrate_project_level_attachments.py

Przenosi pliki przypięte BEZPOŚREDNIO DO PROJEKTU w Asanie (sekcja "Key
Resources" w przeglądzie projektu) do Jiry — jako załączniki dedykowanego
zadania-kontenera "📎 Zasoby projektu (przeniesione z Asany)", tworzonego
w każdym projekcie docelowym, który ma jakiekolwiek takie pliki.

Jira NIE MA odpowiednika "plików przypiętych do projektu" (załączniki są
zawsze przypięte do zadania) - zadanie-kontener to najbliższy, powszechnie
stosowany odpowiednik.

Reużywa DOKŁADNIE tych samych, sprawdzonych funkcji co asana_jira_sync.py:
create_jira_issue, upload_jira_attachment, request_with_retry — więc
zachowuje się identycznie pod względem ponawiania błędów, obsługi ADF itd.

Idempotentne: jeśli zadanie-kontener już istnieje w danym projekcie (po
dokładnym tytule), używa go ponownie zamiast tworzyć duplikat; jeśli dany
plik już był wgrany (śledzone w pliku stanu), pomija go przy kolejnym
uruchomieniu.

Użycie:
    python migrate_project_level_attachments.py --dry-run
    python migrate_project_level_attachments.py
    python migrate_project_level_attachments.py --limit 5    # test na próbce
"""
import argparse
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core  # reużywamy odczytu arkusza, extract_asana_project_gid, jira_project_exists
from asana_jira_sync import (  # reużywamy DOKŁADNIE tych samych, sprawdzonych funkcji
    JIRA_AUTH,
    JIRA_BASE_URL,
    REQUEST_TIMEOUT,
    create_jira_issue,
    request_with_retry,
    upload_jira_attachment,
)

ASANA_TOKEN = os.environ["ASANA_TOKEN"]
ASANA_HEADERS = {"Authorization": f"Bearer {ASANA_TOKEN}"}
ASANA_API = "https://app.asana.com/api/1.0"

CONTAINER_TASK_TITLE = "📎 Zasoby projektu (przeniesione z Asany)"
STATE_FILE = Path(__file__).resolve().parent / "project_attachments_state.json"


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def get_key_resource_attachments(project_gid: str) -> list[dict]:
    resp = requests.get(
        f"{ASANA_API}/attachments", headers=ASANA_HEADERS,
        params={"parent": project_gid, "opt_fields": "name,download_url"}, timeout=30,
    )
    if resp.status_code != 200:
        return []
    return resp.json().get("data", [])


def find_container_issue(project_key: str) -> "str | None":
    """Szuka istniejącego zadania-kontenera po DOKŁADNYM tytule (JQL) -
    idempotentność, żeby ponowne uruchomienie nie tworzyło duplikatu."""
    resp = request_with_retry(
        "GET", f"{JIRA_BASE_URL}/rest/api/3/search/jql", auth=JIRA_AUTH,
        params={"jql": f'project = {project_key} AND summary ~ "{CONTAINER_TASK_TITLE}"', "maxResults": 5},
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code != 200:
        return None
    for issue in resp.json().get("issues", []):
        if issue["fields"]["summary"] == CONTAINER_TASK_TITLE:
            return issue["key"]
    return None


def get_or_create_container_issue(project_key: str, dry_run: bool) -> "str | None":
    existing = find_container_issue(project_key)
    if existing:
        return existing
    if dry_run:
        return None
    return create_jira_issue(
        project_key, CONTAINER_TASK_TITLE,
        "<body>Pliki przeniesione automatycznie z sekcji &quot;Key Resources&quot; "
        "przeglądu projektu w Asanie — nie były przypięte do żadnego konkretnego "
        "zadania, więc zostały zebrane tutaj.</body>",
        assignee_account_id=None, due_on=None,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("Wczytuję arkusz...")
    sheet_rows = core.get_sheet_rows()
    rows_with_link = [r for r in sheet_rows if r.asana_project_link and r.jira_key]
    if args.limit:
        rows_with_link = rows_with_link[: args.limit]

    total = len(rows_with_link)
    print(f"Projektów do sprawdzenia: {total}\n")

    state = load_state()
    totals = {"projects_with_files": 0, "uploaded": 0, "skipped_already_done": 0, "errors": 0}

    for i, row in enumerate(rows_with_link):
        gid = core.extract_asana_project_gid(row.asana_project_link)
        if not gid:
            continue
        jira_key = row.jira_key.strip().upper()
        if not core.jira_project_exists(jira_key):
            continue  # projekt jeszcze nie utworzony w Jirze - pomijamy na razie

        try:
            attachments = get_key_resource_attachments(gid)
        except Exception as exc:  # noqa: BLE001
            print(f"  [{i + 1}/{total}] [OSTRZEŻENIE] {row.project_name}: błąd pobierania listy plików: {exc}")
            totals["errors"] += 1
            continue

        if not attachments:
            continue

        totals["projects_with_files"] += 1
        project_state = state.setdefault(jira_key, {"synced_gids": []})
        synced = set(project_state["synced_gids"])
        new_attachments = [a for a in attachments if a["gid"] not in synced]

        if not new_attachments:
            print(f"  [{i + 1}/{total}] {row.project_name} ({jira_key}): "
                  f"wszystkie {len(attachments)} plik(ów) już wcześniej przeniesione.")
            totals["skipped_already_done"] += len(attachments)
            continue

        print(f"  [{i + 1}/{total}] {row.project_name} ({jira_key}): "
              f"{len(new_attachments)} nowy(ch) plik(ów) do przeniesienia.")

        if args.dry_run:
            for a in new_attachments:
                print(f"      [DRY-RUN] Wgrałbym '{a['name']}' do zadania-kontenera w {jira_key}")
            continue

        try:
            container_key = get_or_create_container_issue(jira_key, args.dry_run)
            if not container_key:
                raise RuntimeError("nie udało się utworzyć/znaleźć zadania-kontenera")
        except Exception as exc:  # noqa: BLE001
            print(f"      [BŁĄD] Nie udało się przygotować zadania-kontenera: {exc}")
            totals["errors"] += 1
            continue

        for a in new_attachments:
            try:
                file_resp = requests.get(a["download_url"], timeout=REQUEST_TIMEOUT)
                file_resp.raise_for_status()
                upload_jira_attachment(container_key, a.get("name") or "plik", file_resp.content)
                synced.add(a["gid"])
                totals["uploaded"] += 1
                print(f"      OK: '{a['name']}' -> {container_key}")
            except Exception as exc:  # noqa: BLE001
                print(f"      [OSTRZEŻENIE] Nie udało się przenieść '{a.get('name')}': {exc}")
                totals["errors"] += 1

        project_state["synced_gids"] = sorted(synced)
        save_state(state)  # zapisuj po każdym projekcie - bezpieczne przy przerwaniu

    print("\n" + "=" * 60)
    print("PODSUMOWANIE")
    print(f"Projektów z plikami: {totals['projects_with_files']}")
    print(f"Wgranych nowych plików: {totals['uploaded']}")
    print(f"Pominiętych (już zrobione wcześniej): {totals['skipped_already_done']}")
    print(f"Błędów: {totals['errors']}")


if __name__ == "__main__":
    main()
