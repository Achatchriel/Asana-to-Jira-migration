#!/usr/bin/env python3
"""
Pomocniczy skrypt: wypisuje WSZYSTKIE grupy Atlassiana (z paginacją),
opcjonalnie filtrując po fragmencie nazwy.

Użycie:
    python list_groups.py            # wszystkie grupy
    python list_groups.py dev        # tylko te zawierające "dev" w nazwie
"""
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)

JIRA_BASE_URL = os.environ["JIRA_BASE_URL"].rstrip("/")
JIRA_EMAIL = os.environ["JIRA_EMAIL"]
JIRA_API_TOKEN = os.environ["JIRA_API_TOKEN"]
AUTH = (JIRA_EMAIL, JIRA_API_TOKEN)

filter_text = sys.argv[1].lower() if len(sys.argv) > 1 else ""

all_groups = []
start_at = 0
max_results = 50

while True:
    resp = requests.get(
        f"{JIRA_BASE_URL}/rest/api/3/group/bulk",
        auth=AUTH,
        params={"startAt": start_at, "maxResults": max_results},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    values = payload.get("values", [])
    all_groups.extend(values)

    if payload.get("isLast", True) or not values:
        break
    start_at += max_results

if filter_text:
    all_groups = [g for g in all_groups if filter_text in g["name"].lower()]

print(f"Znaleziono {len(all_groups)} grup(y):")
for g in sorted(all_groups, key=lambda g: g["name"].lower()):
    print(f"  - '{g['name']}'")
