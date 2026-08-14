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
(przez ODWRÓCONY user_map.json), status -> sekcja (przez ODWRÓCONĄ
section_status_map.json - patrz uwaga niżej), oraz NOWE komentarze i
załączniki z Jiry.

UWAGA o statusie -> sekcji: section_status_map.json mapuje WIELE sekcji
Asany na JEDEN status Jiry (kierunek w przód), więc odwrócenie tego jest z
natury NIEJEDNOZNACZNE, jeśli więcej niż jedna sekcja wskazuje na ten sam
status - w takim wypadku bierzemy PIERWSZĄ pasującą sekcję z pliku (kolejność
zależy od tego, jak plik jest zapisany). Jeśli to problem w Twoim przypadku,
uporządkuj plik tak, żeby preferowana sekcja była pierwsza dla danego statusu.

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
    load_section_status_map,
    load_state,
    load_user_map,
    request_with_retry,
    save_state,
)
from notify import setup_file_logging, send_slack_summary


def get_jira_issue(issue_key: str) -> Optional[dict]:
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}"
    params = {"fields": "summary,description,duedate,assignee,updated,comment,status"}
    resp = requests.get(url, auth=JIRA_AUTH, params=params, timeout=REQUEST_TIMEOUT)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def build_reversed_section_map(section_status_map: dict) -> dict:
    """Odwraca section_status_map.json (sekcja Asany -> status Jiry, lub lista
    statusów) na (nazwa statusu Jiry, znormalizowana -> LISTA kandydatów -
    nazw sekcji Asany, w kolejności występowania w pliku). Wiele sekcji może
    mapować na ten sam status (typowe przy współdzielonym pliku dla różnych
    szablonów projektów) - w takim wypadku sprawdzimy PO KOLEI, która z nich
    faktycznie istnieje w KONKRETNYM projekcie Asany (patrz resolve_target_section).

    Wartość może być JEDNYM statusem (string) ALBO LISTĄ statusów - przydatne,
    gdy jedna sekcja (np. "In progress" w prostym, kilkusekcyjnym projekcie)
    powinna sensownie odpowiadać kilku różnym, bardziej szczegółowym statusom
    Jiry (np. "In progress" ORAZ "Feedback Required")."""
    reversed_map: dict[str, list[str]] = {}
    for section_name, raw_value in section_status_map.items():
        status_names = raw_value if isinstance(raw_value, list) else [raw_value]
        for status_name in status_names:
            if not status_name:
                continue
            key = status_name.strip().lower()
            reversed_map.setdefault(key, []).append(section_name)
    return reversed_map


_asana_sections_cache: dict[str, list[dict]] = {}


def get_asana_sections(project_gid: str) -> list[dict]:
    """Zwraca [{gid, name}, ...] sekcji danego projektu Asany. Cache'owane
    per projekt na czas przebiegu."""
    if project_gid in _asana_sections_cache:
        return _asana_sections_cache[project_gid]
    url = f"{ASANA_API}/projects/{project_gid}/sections"
    resp = requests.get(url, headers=ASANA_HEADERS, params={"opt_fields": "name"}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    sections = resp.json().get("data", [])
    _asana_sections_cache[project_gid] = sections
    return sections


def get_current_asana_section(task_gid: str, project_gid: str) -> Optional[dict]:
    """Zwraca {gid, name} aktualnej sekcji zadania W KONKRETNYM projekcie
    (ten sam problem wieloprojektowości co w asana_jira_sync.py - filtrujemy
    po project_gid, nie bierzemy pierwszego z brzegu membershipu)."""
    url = f"{ASANA_API}/tasks/{task_gid}"
    resp = requests.get(
        url, headers=ASANA_HEADERS,
        params={"opt_fields": "memberships.section.name,memberships.project.gid"},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    for m in resp.json().get("data", {}).get("memberships", []):
        if m.get("project", {}).get("gid") == project_gid:
            return m.get("section")
    return None


def move_asana_task_to_section(task_gid: str, section_gid: str) -> None:
    url = f"{ASANA_API}/sections/{section_gid}/addTask"
    resp = request_with_retry(
        "POST", url, headers=ASANA_HEADERS, json={"data": {"task": task_gid}}, timeout=REQUEST_TIMEOUT
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"Błąd przenoszenia zadania {task_gid} do sekcji {section_gid}: {resp.status_code} {resp.text}")


def resolve_target_section(project_gid: str, candidate_names: list) -> "tuple[dict | None, list]":
    """Próbuje PO KOLEI kandydatów (sekcji), aż znajdzie taką, która
    faktycznie istnieje w TYM KONKRETNYM projekcie Asany. Zwraca
    (znaleziona_sekcja_albo_None, lista_wypróbowanych_nazw) - druga wartość
    do czytelnego komunikatu, gdyby żadna nie pasowała."""
    sections = get_asana_sections(project_gid)
    sections_by_name = {s["name"].strip().lower(): s for s in sections}
    tried = []
    for name in candidate_names:
        tried.append(name)
        match = sections_by_name.get(name.strip().lower())
        if match:
            return match, tried
    return None, tried


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

    if resp.status_code >= 300 and assignee_gid:
        # Przypisany użytkownik mógł nie mieć dostępu do tego projektu w Asanie
        # - spróbuj bez assignee, żeby przynajmniej reszta pól (tytuł, opis,
        # termin) się zaktualizowała, zamiast tracić całą aktualizację.
        data.pop("assignee", None)
        resp = request_with_retry("PUT", url, headers=ASANA_HEADERS, json={"data": data}, timeout=REQUEST_TIMEOUT)
        if resp.status_code < 300:
            print(f"      [INFO] Assignee z Jiry nie mógł być przypisany w Asanie ({asana_gid}) — zaktualizowano bez niego.")

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


def sync_project_back(jira_key: str, state: dict, user_map_reversed: dict, dry_run: bool,
                       asana_project_gid: Optional[str] = None, reversed_section_map: Optional[dict] = None) -> dict:
    stats = {"updated": 0, "comments": 0, "attachments": 0, "errors": 0, "skipped": 0, "status_ok": 0, "status_failed": 0}
    project_state = state.setdefault(jira_key, {})
    if os.getenv("DEBUG_SYNC"):
        print(f"  [DEBUG] {jira_key}: {len(project_state)} zadań śledzonych w stanie, "
              f"asana_project_gid={asana_project_gid!r}, kandydatów w mapie={len(reversed_section_map)}")
    reversed_section_map = reversed_section_map or {}

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

                if os.getenv("DEBUG_SYNC"):
                    print(f"    [DEBUG] {issue_key} -> asana_gid={asana_gid}")
                    print(f"    [DEBUG] jira_updated={jira_updated!r} vs last_known={last_known_updated!r}")
                    print(f"    [DEBUG] summary={summary!r}")
                    print(f"    [DEBUG] description_html={description_html!r}")
                    print(f"    [DEBUG] due_on={due_on!r}, assignee_gid={assignee_gid!r}")

                if dry_run:
                    print(f"    [DRY-RUN] Zaktualizowałbym w Asanie zadanie odpowiadające {issue_key}: '{summary}'")
                else:
                    update_asana_task(asana_gid, summary, description_html, due_on, assignee_gid)
                    task_state["jira_updated"] = jira_updated
                stats["updated"] += 1

            # Status -> sekcja (opcjonalne, tylko jeśli mamy GID projektu Asany
            # i tabelę odwróconą - patrz build_reversed_section_map na górze pliku).
            if asana_project_gid and reversed_section_map:
                status_name = (fields.get("status") or {}).get("name", "")
                candidate_names = reversed_section_map.get(status_name.strip().lower(), [])
                if os.getenv("DEBUG_SYNC"):
                    print(f"    [DEBUG-STATUS] {issue_key}: status_name={status_name!r}, "
                          f"candidate_names={candidate_names!r}, asana_project_gid={asana_project_gid!r}")
                if candidate_names:
                    target_section, tried = resolve_target_section(asana_project_gid, candidate_names)
                    if os.getenv("DEBUG_SYNC"):
                        print(f"    [DEBUG-STATUS] target_section={target_section!r}, tried={tried!r}")
                    if not target_section:
                        tried_str = "', '".join(tried)
                        print(f"    [OSTRZEŻENIE] {issue_key}: status '{status_name}' — żadna z możliwych sekcji "
                              f"('{tried_str}') nie istnieje w tym projekcie Asany.")
                    else:
                        current_section = get_current_asana_section(asana_gid, asana_project_gid)
                        current_name = (current_section or {}).get("name", "")
                        if os.getenv("DEBUG_SYNC"):
                            print(f"    [DEBUG-STATUS] current_section={current_section!r}")
                        if current_name.strip().lower() != target_section["name"].strip().lower():
                            if dry_run:
                                print(f"    [DRY-RUN] Przeniósłbym zadanie odpowiadające {issue_key} "
                                      f"do sekcji '{target_section['name']}' (status: '{status_name}').")
                                stats["status_ok"] += 1
                            else:
                                try:
                                    move_asana_task_to_section(asana_gid, target_section["gid"])
                                    print(f"    Przeniesiono zadanie ({issue_key}) do sekcji '{target_section['name']}'.")
                                    stats["status_ok"] += 1
                                except Exception as exc:  # noqa: BLE001
                                    print(f"    [OSTRZEŻENIE] {issue_key}: nie udało się przenieść do sekcji: {exc}")
                                    stats["status_failed"] += 1
                        elif os.getenv("DEBUG_SYNC"):
                            print(f"    [DEBUG-STATUS] {issue_key}: już we właściwej sekcji, bez zmian.")

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

        if not dry_run:
            # ZAPISZ STAN PO KAŻDYM ZADANIU, nie dopiero po całym projekcie —
            # ten sam błąd, który znaleźliśmy i naprawiliśmy w asana_jira_sync.py:
            # przerwanie w połowie (np. utrata internetu) traciło cały postęp
            # dla danego projektu, co przy restarcie prowadziło do duplikatów.
            save_state(state)

        time.sleep(0.3)

    return stats


def get_project_pairs(args) -> list[tuple[str, str, str]]:
    """Zwraca (klucz_Jira, nazwa_projektu_Asana, link_Asany) — potrzebne, żeby
    ustalić GID projektu Asany (do synchronizacji statusu -> sekcja)."""
    if args.project_keys:
        keys = [k.strip().upper() for k in args.project_keys.split(",") if k.strip()]
        return [(k, "", "") for k in keys]
    sheet_rows = core.get_sheet_rows()
    cache = core.load_key_cache()
    pairs = []
    for row in sheet_rows:
        norm_name = core.normalize(row.project_name)
        key = row.jira_key or cache.get(norm_name)
        if key:
            pairs.append((key, row.project_name, row.asana_project_link))
    return pairs


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
    section_status_map = load_section_status_map()
    reversed_section_map = build_reversed_section_map(section_status_map)
    if not reversed_section_map:
        print(
            "UWAGA: section_status_map.json puste/nie istnieje — status NIE będzie "
            "synchronizowany zwrotnie (tylko tytuł/opis/termin/przypisany/komentarze).\n"
        )

    project_pairs = get_project_pairs(args)
    if args.limit:
        project_pairs = project_pairs[: args.limit]
    print(f"Projektów do synchronizacji (Jira -> Asana): {len(project_pairs)}")
    if args.dry_run:
        print("TRYB DRY-RUN — nic nie zostanie zapisane.\n")

    totals = {"updated": 0, "comments": 0, "attachments": 0, "errors": 0, "skipped": 0, "status_ok": 0, "status_failed": 0}
    for i, (jira_key, project_name, asana_link) in enumerate(project_pairs):
        if jira_key not in state or not state[jira_key]:
            continue  # nic jeszcze nie zsynchronizowane w tę stronę dla tego projektu
        print(f"\n[{i + 1}/{len(project_pairs)}] {jira_key}")

        asana_project_gid = None
        if reversed_section_map:
            asana_project_gid = core.extract_asana_project_gid(asana_link) if asana_link else None
            if not asana_project_gid:
                print("  [OSTRZEŻENIE] Brak asana_project_link — status NIE zostanie zsynchronizowany dla "
                      "tego projektu (dopasowanie po nazwie celowo wyłączone). Uzupełnij link w arkuszu.")

        stats = sync_project_back(jira_key, state, user_map_reversed, args.dry_run, asana_project_gid, reversed_section_map)
        for k in totals:
            totals[k] += stats.get(k, 0)
        if not args.dry_run:
            save_state(state)

    print("\n" + "=" * 60)
    print("PODSUMOWANIE")
    print(f"  Zaktualizowano w Asanie: {totals['updated']}")
    print(f"  Komentarzy:              {totals['comments']}")
    print(f"  Załączników:             {totals['attachments']}")
    print(f"  Statusy OK:              {totals['status_ok']}")
    print(f"  Statusy błąd:            {totals['status_failed']}")
    print(f"  Błędy:                   {totals['errors']}")

    if not args.dry_run:
        send_slack_summary(
            f"*jira_asana_sync.py* (Jira -> Asana) zakończony.\n"
            f"Zaktualizowano: {totals['updated']} | Komentarze: {totals['comments']} | "
            f"Załączniki: {totals['attachments']} | Błędy: {totals['errors']}"
        )


if __name__ == "__main__":
    main()
