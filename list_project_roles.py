#!/usr/bin/env python3
"""Pomocniczy skrypt: wypisuje wszystkie dostępne role projektowe w Jirze."""
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)

JIRA_BASE_URL = os.environ["JIRA_BASE_URL"].rstrip("/")
JIRA_EMAIL = os.environ["JIRA_EMAIL"]
JIRA_API_TOKEN = os.environ["JIRA_API_TOKEN"]

resp = requests.get(f"{JIRA_BASE_URL}/rest/api/3/role", auth=(JIRA_EMAIL, JIRA_API_TOKEN), timeout=30)
resp.raise_for_status()

print("Dostępne role projektowe w tej Jirze:")
for role in resp.json():
    print(f"  - '{role['name']}'  (id: {role['id']})")
