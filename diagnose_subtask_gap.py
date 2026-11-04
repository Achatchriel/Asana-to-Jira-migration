#!/usr/bin/env python3
"""
diagnose_subtask_gap.py

Dla JEDNEGO projektu: pobiera WSZYSTKIE tytuły podzadań z Jiry i z Asany,
porównuje jako ZBIORY - pokazuje, które konkretne podzadania są w Jirze,
ale nie ma ich w Asanie (i odwrotnie). To odpowiednik
diagnose_jira_asana_gap.py, ale dla podzadań - wymaga dodatkowo przejścia
przez KAŻDE zadanie główne w Asanie (nie ma zbiorczego zapytania "podaj
wszystkie podzadania całego projektu").

Użycie:
    python diagnose_subtask_gap.py PROJEKT_JIRA
"""
import sys
from pathlib import Path
from collections import Counter

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core

jira_key = sys.argv[1]

print(f"Szukam wiersza w arkuszu dla {jira_key}...")
sheet_rows = core.get_sheet_rows()
row = next((r for r in sheet_rows if r.jira_key == jira_key), None)
if not row:
    print(f"[BŁĄD] Nie znaleziono wiersza z jira_key={jira_key} w arkuszu.")
    sys.exit(1)

gid = core.extract_asana_project_gid(row.asana_project_link)
print(f"Projekt Asany: gid={gid}")

print("\nPobieram podzadania z Jiry...")
jira_subtasks = []
next_page_token = None
while True:
    body = {
        "jql": f"project = {jira_key} AND issuetype in subTaskIssueTypes()",
        "maxResults": 100, "fields": ["summary"],
    }
    if next_page_token:
        body["nextPageToken"] = next_page_token
    resp = requests.post(f"{core.JIRA_BASE_URL}/rest/api/3/search/jql", auth=core.jira_auth(), json=body)
    resp.raise_for_status()
    data = resp.json()
    for issue in data.get("issues", []):
        jira_subtasks.append((issue["key"], issue.get("fields", {}).get("summary", "").strip()))
    next_page_token = data.get("nextPageToken")
    if not next_page_token:
        break
print(f"  Jira: {len(jira_subtasks)} podzadań")

print("Pobieram zadania główne z Asany, i dla każdego jego podzadania...")
headers = {"Authorization": f"Bearer {core.ASANA_TOKEN}"}

top_level_gids = []
offset = None
while True:
    params = {"opt_fields": "gid", "limit": 100}
    if offset:
        params["offset"] = offset
    resp = requests.get(f"https://app.asana.com/api/1.0/projects/{gid}/tasks", headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    top_level_gids.extend(t["gid"] for t in payload.get("data", []))
    offset = payload.get("next_page", {}).get("offset") if payload.get("next_page") else None
    if not offset:
        break
print(f"  {len(top_level_gids)} zadań głównych — pobieram ich podzadania...")

asana_subtasks = []
for i, task_gid in enumerate(top_level_gids, 1):
    sub_offset = None
    while True:
        params = {"opt_fields": "name", "limit": 100}
        if sub_offset:
            params["offset"] = sub_offset
        resp = requests.get(f"https://app.asana.com/api/1.0/tasks/{task_gid}/subtasks", headers=headers, params=params, timeout=30)
        if resp.status_code != 200:
            break
        payload = resp.json()
        for s in payload.get("data", []):
            asana_subtasks.append((task_gid, s.get("name", "").strip()))
        sub_offset = payload.get("next_page", {}).get("offset") if payload.get("next_page") else None
        if not sub_offset:
            break
    if i % 50 == 0:
        print(f"    ...{i}/{len(top_level_gids)} zadań głównych sprawdzonych")
print(f"  Asana: {len(asana_subtasks)} podzadań")

jira_title_counts = Counter(t for _, t in jira_subtasks)
asana_title_counts = Counter(t for _, t in asana_subtasks)

print("\n" + "=" * 60)
print("PODZADANIA W JIRZE, KTÓRYCH NIE MA (ALBO JEST MNIEJ) W ASANIE:")
print("=" * 60)
extra_in_jira = 0
seen_reported = Counter()
for key, title in jira_subtasks:
    allowed = asana_title_counts.get(title, 0)
    if seen_reported[title] >= allowed:
        extra_in_jira += 1
        print(f"  {key}: '{title}'")
    seen_reported[title] += 1
print(f"\nŁącznie: {extra_in_jira} podzadań w Jirze bez odpowiednika w Asanie")

print("\n" + "=" * 60)
print("PODZADANIA W ASANIE, KTÓRYCH NIE MA (ALBO JEST MNIEJ) W JIRZE:")
print("=" * 60)
missing_in_jira = 0
seen_reported2 = Counter()
for parent_gid, title in asana_subtasks:
    allowed = jira_title_counts.get(title, 0)
    if seen_reported2[title] >= allowed:
        missing_in_jira += 1
        print(f"  (rodzic gid={parent_gid}): '{title}'")
    seen_reported2[title] += 1
print(f"\nŁącznie: {missing_in_jira} podzadań w Asanie bez odpowiednika w Jirze")
