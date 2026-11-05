#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
diagnose_jira_asana_gap.py

Dla JEDNEGO projektu: pobiera WSZYSTKIE tytuły zadań z Jiry i z Asany,
porównuje jako ZBIORY (nie liczby) - pokazuje, które tytuły są w Jirze,
ale NIE MA ich wcale w Asanie (niezależnie od tego, czy to duplikat, czy
unikalne zadanie-sierota po tym, jak odpowiednik zniknął z Asany).

Użycie:
    python diagnose_jira_asana_gap.py PROJEKT_JIRA
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

print("\nPobieram tytuły zadań z Jiry...")
jira_titles = []
next_page_token = None
while True:
    body = {"jql": f"project = {jira_key}", "maxResults": 100, "fields": ["summary"]}
    if next_page_token:
        body["nextPageToken"] = next_page_token
    resp = requests.post(f"{core.JIRA_BASE_URL}/rest/api/3/search/jql", auth=core.jira_auth(), json=body)
    resp.raise_for_status()
    data = resp.json()
    for issue in data.get("issues", []):
        jira_titles.append((issue["key"], issue.get("fields", {}).get("summary", "")))
    next_page_token = data.get("nextPageToken")
    if not next_page_token:
        break
print(f"  Jira: {len(jira_titles)} zadań")

print("Pobieram tytuły zadań z Asany...")
headers = {"Authorization": f"Bearer {core.ASANA_TOKEN}"}
asana_titles = []
offset = None
while True:
    params = {"opt_fields": "name", "limit": 100}
    if offset:
        params["offset"] = offset
    resp = requests.get(f"https://app.asana.com/api/1.0/projects/{gid}/tasks", headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    asana_titles.extend(t["name"] for t in payload.get("data", []))
    offset = payload.get("next_page", {}).get("offset") if payload.get("next_page") else None
    if not offset:
        break
print(f"  Asana: {len(asana_titles)} zadań")

asana_title_counts = Counter(asana_titles)
jira_title_counts = Counter(t for _, t in jira_titles)

print("\n" + "=" * 60)
print("TYTUŁY W JIRZE, KTÓRYCH NIE MA W OGÓLE W ASANIE:")
print("=" * 60)
orphan_count = 0
for jira_key_id, title in jira_titles:
    if asana_title_counts.get(title, 0) == 0:
        orphan_count += 1
        print(f"  {jira_key_id}: '{title}'")

print(f"\nŁącznie takich zadań (unikalny tytuł, zero w Asanie): {orphan_count}")

print("\n" + "=" * 60)
print("TYTUŁY, GDZIE JIRA MA WIĘCEJ KOPII NIŻ ASANA (prawdziwe duplikaty):")
print("=" * 60)
dup_excess = 0
for title, jira_count in jira_title_counts.items():
    asana_count = asana_title_counts.get(title, 0)
    if jira_count > asana_count and asana_count > 0:
        excess = jira_count - asana_count
        dup_excess += excess
        print(f"  '{title}': Jira={jira_count}, Asana={asana_count} (nadmiar: {excess})")

print(f"\nŁącznie nadmiarowych kopii (tytuł ISTNIEJE w obu, ale więcej w Jirze): {dup_excess}")
print(f"\nSUMA: {orphan_count} + {dup_excess} = {orphan_count + dup_excess} (powinno odpowiadać różnicy z count_migrated_tasks.py)")
