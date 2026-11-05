#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
diagnose_attachment_gap.py

Dla JEDNEGO projektu: dla KAŻDEGO śledzonego zadania/podzadania (z
task_sync_state.json - dokładne powiązanie gid Asany <-> klucz Jiry,
nie zgadywanie po tytule) porównuje ZESTAW nazw załączników w Jirze i
w Asanie. Pokazuje TYLKO te zadania, gdzie się nie zgadzają.

Działa jednocześnie dla zadań głównych I podzadań - oba typy są w tym
samym task_sync_state.json, pod tym samym kluczem projektu.

Użycie:
    python diagnose_attachment_gap.py PROJEKT_JIRA
"""
import json
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core

STATE_FILE = Path(__file__).resolve().parent / "task_sync_state.json"

jira_key = sys.argv[1]

if not STATE_FILE.exists():
    print("[BŁĄD] Brak task_sync_state.json.")
    sys.exit(1)
state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
project_state = state.get(jira_key, {})
if not project_state:
    print(f"[BŁĄD] Brak śledzonych zadań dla '{jira_key}' w task_sync_state.json.")
    sys.exit(1)
print(f"Śledzonych zadań/podzadań dla {jira_key}: {len(project_state)}")

headers = {"Authorization": f"Bearer {core.ASANA_TOKEN}"}


def get_jira_attachment_names(jira_issue_key):
    resp = requests.get(
        f"{core.JIRA_BASE_URL}/rest/api/3/issue/{jira_issue_key}",
        auth=core.jira_auth(), params={"fields": "attachment"}, timeout=30,
    )
    if resp.status_code != 200:
        return None
    return sorted(a.get("filename", "") for a in resp.json().get("fields", {}).get("attachment") or [])


def get_asana_attachment_names(task_gid):
    resp = requests.get(
        f"https://app.asana.com/api/1.0/tasks/{task_gid}/attachments",
        headers=headers, params={"opt_fields": "name"}, timeout=30,
    )
    if resp.status_code != 200:
        return None
    return sorted(a.get("name", "") for a in resp.json().get("data", []))


mismatches = []
checked = 0
for i, (gid, entry) in enumerate(project_state.items(), 1):
    jira_issue_key = entry.get("jira_key")
    if not jira_issue_key:
        continue
    jira_names = get_jira_attachment_names(jira_issue_key)
    asana_names = get_asana_attachment_names(gid)
    checked += 1
    if jira_names is None or asana_names is None:
        continue
    if jira_names != asana_names:
        only_jira = sorted(set(jira_names) - set(asana_names))
        only_asana = sorted(set(asana_names) - set(jira_names))
        mismatches.append((jira_issue_key, gid, only_jira, only_asana))
    if i % 100 == 0:
        print(f"  ...{i}/{len(project_state)} sprawdzonych")

print(f"\nSprawdzonych: {checked}")
print("\n" + "=" * 60)
print(f"ZADANIA/PODZADANIA Z NIEZGODNYMI ZAŁĄCZNIKAMI: {len(mismatches)}")
print("=" * 60)
for jira_issue_key, gid, only_jira, only_asana in mismatches:
    print(f"\n  {jira_issue_key} (gid Asany: {gid}):")
    if only_jira:
        print(f"    tylko w Jirze: {only_jira}")
    if only_asana:
        print(f"    tylko w Asanie (brakuje w Jirze): {only_asana}")
