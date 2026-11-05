#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
find_duplicate_comments_attachments.py

Wykrywa zdublowane KOMENTARZE i ZAŁĄCZNIKI wewnątrz POJEDYNCZYCH zadań -
to INNY problem niż zdublowane CAŁE zadania (resolve_duplicates.py).
Zadanie może mieć tylko JEDEN klucz Jiry (żaden duplikat całego zadania),
a mimo to mieć te same komentarze/załączniki dodane WIELOKROTNIE do
TEGO SAMEGO zadania - bo task_sync_state.json śledzi OSOBNO, które
konkretne komentarze/załączniki już zsynchronizowano; jeśli ten fragment
stanu się zgubił (ten sam mechanizm co przy całych zadaniach), kolejny
przebieg mógł dodać te same komentarze/załączniki po raz drugi.

Wykrywanie komentarzy: każdy nasz komentarz zaczyna się od unikalnego
"[Asana — autor, data]" (patrz post_jira_comment w asana_jira_sync.py) -
jeśli TEN SAM prefiks występuje więcej niż raz w JEDNYM zadaniu, to
duplikat.

Wykrywanie załączników: te same nazwy plików więcej niż raz w JEDNYM
zadaniu.

WYŁĄCZNIE wykrywanie - NIC nie usuwa. Usuwanie pojedynczych komentarzy/
załączników to osobna, delikatniejsza operacja niż usuwanie całych zadań.

Użycie:
    python find_duplicate_comments_attachments.py                # wszystkie projekty
    python find_duplicate_comments_attachments.py --project AWGUSL
    python find_duplicate_comments_attachments.py --shard 1/5     # jak w resolve_duplicates.py
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core
from jira_sync import apply_shard

COMMENT_PREFIX_RE = re.compile(r"^\[Asana.*?\]$")


def extract_comment_signature(comment_body: dict) -> str:
    """Wyciąga tekst prefiksu '[Asana — autor, data]' z ADF komentarza -
    to pierwszy akapit, pierwszy fragment tekstu. Zwraca pusty string,
    jeśli nie rozpoznano formatu (np. komentarz dodany ręcznie przez
    kogoś w Jirze, nie przez naszą synchronizację - pomijamy takie)."""
    try:
        first_paragraph = comment_body["content"][0]
        text = first_paragraph["content"][0]["text"]
        return text if COMMENT_PREFIX_RE.match(text) else ""
    except (KeyError, IndexError, TypeError):
        return ""


def find_duplicates_in_project(project_key: str) -> tuple:
    """Zwraca (duplicate_comments, duplicate_attachments) dla projektu.
    duplicate_comments: [(issue_key, signature, count), ...]
    duplicate_attachments: [(issue_key, filename, count), ...]"""
    duplicate_comments = []
    duplicate_attachments = []
    next_page_token = None
    while True:
        body = {"jql": f"project = {project_key}", "maxResults": 100, "fields": ["comment", "attachment"]}
        if next_page_token:
            body["nextPageToken"] = next_page_token
        resp = requests.post(
            f"{core.JIRA_BASE_URL}/rest/api/3/search/jql",
            auth=core.jira_auth(),
            json=body,
        )
        if resp.status_code != 200:
            print(f"   [OSTRZEŻENIE] Błąd stronicowania dla {project_key}: {resp.status_code} {resp.text[:150]}")
            break
        data = resp.json()
        for issue in data.get("issues", []):
            key = issue["key"]
            fields = issue.get("fields", {})

            comments = fields.get("comment", {}).get("comments", []) or []
            sig_counts = defaultdict(int)
            for c in comments:
                sig = extract_comment_signature(c.get("body", {}))
                if sig:
                    sig_counts[sig] += 1
            for sig, count in sig_counts.items():
                if count > 1:
                    duplicate_comments.append((key, sig, count))

            attachments = fields.get("attachment") or []
            filename_counts = defaultdict(int)
            for a in attachments:
                filename_counts[a.get("filename", "")] += 1
            for filename, count in filename_counts.items():
                if count > 1:
                    duplicate_attachments.append((key, filename, count))

        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            break
    return duplicate_comments, duplicate_attachments


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", help="Sprawdź tylko ten jeden klucz projektu.")
    parser.add_argument("--shard", type=str, default=None, help="Format 'K/N' - przetwórz tylko tę część projektów.")
    args = parser.parse_args()

    if args.project:
        project_keys = [args.project]
    else:
        print("Wczytuję listę projektów z arkusza...")
        sheet_rows = core.get_sheet_rows()
        project_keys = sorted(set(row.jira_key for row in sheet_rows if row.jira_key))
        print(f"Znaleziono {len(project_keys)} unikalnych kluczy projektów Jiry.")

    if args.shard:
        before = len(project_keys)
        project_keys = apply_shard(project_keys, args.shard)
        print(f"--shard {args.shard}: ten proces obsłuży {len(project_keys)} z {before} projektów.")

    print(f"\nSprawdzam duplikaty komentarzy/załączników w {len(project_keys)} projekt(ach) (to może potrwać kilka-kilkanaście minut)...\n")

    all_dup_comments = []
    all_dup_attachments = []
    for i, project_key in enumerate(project_keys, 1):
        dup_comments, dup_attachments = find_duplicates_in_project(project_key)
        if dup_comments or dup_attachments:
            print(f"[{i}/{len(project_keys)}] {project_key}: {len(dup_comments)} zdublowanych komentarzy, {len(dup_attachments)} zdublowanych załączników")
        for issue_key, sig, count in dup_comments:
            all_dup_comments.append((project_key, issue_key, sig, count))
        for issue_key, filename, count in dup_attachments:
            all_dup_attachments.append((project_key, issue_key, filename, count))

    total_extra_comments = sum(count - 1 for *_, count in all_dup_comments)
    total_extra_attachments = sum(count - 1 for *_, count in all_dup_attachments)

    print("\n" + "=" * 60)
    print("PODSUMOWANIE")
    print(f"  Zadań ze zdublowanymi komentarzami: {len(all_dup_comments)}  ->  ~{total_extra_comments} zbędnych kopii komentarzy")
    print(f"  Zadań ze zdublowanymi załącznikami: {len(all_dup_attachments)}  ->  ~{total_extra_attachments} zbędnych kopii załączników")
    print("=" * 60)

    if all_dup_comments:
        print(f"\nZdublowane komentarze (pierwsze 20 z {len(all_dup_comments)}):")
        for project_key, issue_key, sig, count in all_dup_comments[:20]:
            print(f"  [{project_key}] {issue_key}: '{sig}' występuje {count}x")

    if all_dup_attachments:
        print(f"\nZdublowane załączniki (pierwsze 20 z {len(all_dup_attachments)}):")
        for project_key, issue_key, filename, count in all_dup_attachments[:20]:
            print(f"  [{project_key}] {issue_key}: '{filename}' występuje {count}x")

    report_path = Path(__file__).resolve().parent / "duplicate_comments_attachments_report.json"
    report_path.write_text(json.dumps({
        "duplicate_comments": [
            {"project": p, "issue": i, "signature": s, "count": c} for p, i, s, c in all_dup_comments
        ],
        "duplicate_attachments": [
            {"project": p, "issue": i, "filename": f, "count": c} for p, i, f, c in all_dup_attachments
        ],
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nPełny raport (bez obcinania) zapisany do: {report_path}")
    print("\nTo narzędzie TYLKO wykrywa - nic nie usuwa. Usuwanie pojedynczych")
    print("komentarzy/załączników wymaga osobnego, ostrożniejszego podejścia.")


if __name__ == "__main__":
    main()
