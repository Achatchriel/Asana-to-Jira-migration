#!/usr/bin/env python3
"""
jira_asana_sync.py — ETAP 3: kierunek zwrotny (Jira -> Asana)

Osobny skrypt (nie tryb w asana_jira_sync.py), bo kierunek zwrotny ma inną
naturę: zamiast iterować po zadaniach Asany i szukać ich odpowiednika w
Jirze, iteruje po JUŻ POWIĄZANYCH parach z task_sync_state.json (ten sam
plik, którego używa asana_jira_sync.py — WSPÓLNA mapa powiązań) i sprawdza,
co zmieniło się w Jirze od ostatniej synchronizacji.

Rozstrzyganie konfliktów: prosto, "kto zmienił później wygrywa" — porównanie
znacznika czasu ostatniej modyfikacji w Jirze (updated) z tym zapisanym w
stanie. Jeśli zadanie było w międzyczasie ZMIENIONE TAKŻE w Asanie (inny
modified_at niż zapisany), ten skrypt i tak nadpisze Asanę stanem z Jiry —
nie ma tu pełnego mergowania pól. Jeśli obie strony mogą się zmieniać
jednocześnie i to jest problemem, uruchamiaj oba kierunki naprzemiennie
(najpierw asana_jira_sync.py, potem ten skrypt), nie równolegle.

Synchronizuje: tytuł, opis (z konwersją ADF -> HTML), termin, przypisanego
(przez ODWRÓCONY user_map.json), oraz NOWE komentarze i załączniki z Jiry.
Status NIE jest synchronizowany zwrotnie (Asana nie ma bezpośredniego
odpowiednika "kolumny/statusu" 1:1 - sekcje to inna koncepcja niż workflow
Jiry; zostaw to jako ręczne albo daj znać, jeśli chcesz to też dopisać).

Wymaga tych samych zmiennych w .env co asana_jira_sync.py, plus tego samego
task_sync_state.json i user_map.json (odczytywane, nie tworzone od zera).

Użycie:
    python jira_asana_sync.py --dry-run --limit 1 --project-keys KLUCZ
    python jira_asana_sync.py --limit 1 --project-keys KLUCZ
    python jira_asana_sync.py
"""
import argparse
import os
import sys
import time
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core  # reużywamy odczytu arkusza i cache'u kluczy
from asana_jira_sync import (  # reużywamy konwersji, stanu i konfiguracji - bez duplikacji
    ASANA_API,
    ASANA_HEADERS,
    JIRA_AUTH,
    JIRA_BASE_URL,
    REQUEST_TIMEOUT,
    adf_to_html,
    load_state,
    load_user_map,
    request_with_retry,
    save_state,
)
from notify import setup_file_logging, send_slack_summary


def get_jira_issue(issue_key: str) -> Optional[dict]:
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}"
    params = {"fields": "summary,description,duedate,assignee,updated,comment"}
    resp = requests.get(url, auth=JIRA_AUTH, params=params, timeout=REQUEST_TIMEOUT)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def get_jira_new_comments(issue_key: str, since_comment_ids: set) -> list[dict]:
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/comment"
    resp = requests.get(url, auth=JIRA_AUTH, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    comments = resp.json().get("comments", [])
    return [c for c in comments if c["id"] not in since_comment_ids]


def get_jira_attachments(issue_key: str) -> list[dict]:
    issue = get_jira_issue(issue_key)
    if not issue:
        return []
    return issue.get("fields", {}).get("attachment", []) or []


def adf_comment_to_html(body) -> str:
    """Komentarz z Jiry bywa zwykłym stringiem (stare dane) albo ADF-em."""
    if isinstance(body, dict):
        return adf_to_html(body)
    return f"<body>{body or ''}</body>"


def update_asana_task(asana_gid: str, name: str, html_notes: str, due_on: Optional[str],
                       assignee_gid: Optional[str]) -> None:
    url = f"{ASANA_API}/tasks/{asana_gid}"
    data: dict = {"name": name, "html_notes": html_notes}
    if due_on:
        data["due_on"] = due_on
    if assignee_gid:
        data["assignee"] = assignee_gid
    resp = request_with_retry("PUT", url, headers=ASANA_HEADERS, json={"data": data}, timeout=REQUEST_TIMEOUT)
    if resp.status_code >= 300:
        raise RuntimeError(f"Błąd aktualizacji zadania Asany {asana_gid}: {resp.status_code} {resp.text}")


def post_asana_comment(asana_gid: str, html_text: str) -> None:
    url = f"{ASANA_API}/tasks/{asana_gid}/stories"
    resp = request_with_retry(
        "POST", url, headers=ASANA_HEADERS, json={"data": {"html_text": html_text}}, timeout=REQUEST_TIMEOUT
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"Błąd dodawania komentarza w Asanie ({asana_gid}): {resp.status_code} {resp.text}")


def upload_asana_attachment(asana_gid: str, filename: str, content: bytes) -> None:
    url = f"{ASANA_API}/tasks/{asana_gid}/attachments"
    files = {"file": (filename, content)}
    resp = request_with_retry("POST", url, headers=ASANA_HEADERS, files=files, timeout=REQUEST_TIMEOUT)
    if resp.status_code >= 300:
        raise RuntimeError(f"Błąd wgrywania załącznika w Asanie ({asana_gid}): {resp.status_code} {resp.text}")


def sync_project_back(jira_key: str, project_state: dict, user_map_reversed: dict, dry_run: bool) -> dict:
    stats = {"updated": 0, "comments": 0, "attachments": 0, "errors": 0, "skipped": 0}

    for asana_gid, task_state in project_state.items():
        issue_key = task_state.get("jira_key")
        if not issue_key:
            continue

        try:
            issue = get_jira_issue(issue_key)
            if not issue:
                continue

            fields = issue["fields"]
            jira_updated = fields.get("updated")
            last_known_updated = task_state.get("jira_updated")

            if jira_updated != last_known_updated:
                summary = fields.get("summary") or ""
                description_html = adf_to_html(fields.get("description") or {})
                due_on = fields.get("duedate")
                assignee = fields.get("assignee") or {}
                assignee_gid = user_map_reversed.get(assignee.get("accountId")) if assignee.get("accountId") else None

                if dry_run:
                    print(f"    [DRY-RUN] Zaktualizowałbym w Asanie zadanie odpowiadające {issue_key}: '{summary}'")
                else:
                    update_asana_task(asana_gid, summary, description_html, due_on, assignee_gid)
                    task_state["jira_updated"] = jira_updated
                stats["updated"] += 1

            # Nowe komentarze
            synced_comment_ids = set(task_state.get("synced_from_jira_comment_ids", []))
            new_comments = get_jira_new_comments(issue_key, synced_comment_ids)
            for c in new_comments:
                author = (c.get("author") or {}).get("displayName", "")
                html_body = adf_comment_to_html(c.get("body"))
                prefix = f"<body>[Jira — {author}]<br>" if author else "<body>"
                combined = prefix + html_body.replace("<body>", "").replace("</body>", "") + "</body>"
                if dry_run:
                    print(f"    [DRY-RUN] Dodałbym w Asanie komentarz od {author}")
                else:
                    post_asana_comment(asana_gid, combined)
                    synced_comment_ids.add(c["id"])
                stats["comments"] += 1
            if not dry_run:
                task_state["synced_from_jira_comment_ids"] = sorted(synced_comment_ids)

            # Nowe załączniki
            synced_attachment_ids = set(task_state.get("synced_from_jira_attachment_ids", []))
            for att in get_jira_attachments(issue_key):
                if att["id"] in synced_attachment_ids:
                    continue
                if dry_run:
                    print(f"    [DRY-RUN] Wgrałbym w Asanie załącznik '{att.get('filename')}'")
                    stats["attachments"] += 1
                    continue
                try:
                    file_resp = requests.get(att["content"], auth=JIRA_AUTH, timeout=REQUEST_TIMEOUT)
                    file_resp.raise_for_status()
                    upload_asana_attachment(asana_gid, att.get("filename") or "attachment", file_resp.content)
                    synced_attachment_ids.add(att["id"])
                    stats["attachments"] += 1
                except Exception as exc:  # noqa: BLE001
                    print(f"      [OSTRZEŻENIE] Nie udało się przenieść załącznika '{att.get('filename')}': {exc}")
            if not dry_run:
                task_state["synced_from_jira_attachment_ids"] = sorted(synced_attachment_ids)

        except Exception as exc:  # noqa: BLE001
            print(f"    [BŁĄD] {issue_key}: {exc}")
            stats["errors"] += 1

        time.sleep(0.3)

    return stats


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
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.dry_run:
        setup_file_logging("jira_asana_sync")

    state = load_state()
    user_map = load_user_map()  # Asana gid -> Jira accountId
    user_map_reversed = {v: k for k, v in user_map.items()}  # Jira accountId -> Asana gid

    project_keys = get_project_keys(args)
    if args.limit:
        project_keys = project_keys[: args.limit]
    print(f"Projektów do synchronizacji (Jira -> Asana): {len(project_keys)}")
    if args.dry_run:
        print("TRYB DRY-RUN — nic nie zostanie zapisane.\n")

    totals = {"updated": 0, "comments": 0, "attachments": 0, "errors": 0, "skipped": 0}
    for i, jira_key in enumerate(project_keys):
        if jira_key not in state or not state[jira_key]:
            continue  # nic jeszcze nie zsynchronizowane w tę stronę dla tego projektu
        print(f"\n[{i + 1}/{len(project_keys)}] {jira_key}")
        stats = sync_project_back(jira_key, state[jira_key], user_map_reversed, args.dry_run)
        for k in totals:
            totals[k] += stats.get(k, 0)
        if not args.dry_run:
            save_state(state)

    print("\n" + "=" * 60)
    print("PODSUMOWANIE")
    print(f"  Zaktualizowano w Asanie: {totals['updated']}")
    print(f"  Komentarzy:              {totals['comments']}")
    print(f"  Załączników:             {totals['attachments']}")
    print(f"  Błędy:                   {totals['errors']}")

    if not args.dry_run:
        send_slack_summary(
            f"*jira_asana_sync.py* (Jira -> Asana) zakończony.\n"
            f"Zaktualizowano: {totals['updated']} | Komentarze: {totals['comments']} | "
            f"Załączniki: {totals['attachments']} | Błędy: {totals['errors']}"
        )


if __name__ == "__main__":
    main()
