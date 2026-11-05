#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
diagnose_field_config.py

Jednorazowy skrypt diagnostyczny - pokazuje DOKŁADNY, surowy kształt
odpowiedzi JSON z /rest/api/3/fieldconfigurationscheme/project, żeby
precyzyjnie naprawić błąd KeyError w jira_sync.py (zamiast zgadywać).

Uruchom z głównego folderu jiraSync/:
    python diagnose_field_config.py OPS
"""
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)

JIRA_BASE_URL = os.environ["JIRA_BASE_URL"].rstrip("/")
JIRA_AUTH = (os.environ["JIRA_EMAIL"], os.environ["JIRA_API_TOKEN"])

project_key = sys.argv[1] if len(sys.argv) > 1 else "OPS"

# Najpierw project_id
resp = requests.get(f"{JIRA_BASE_URL}/rest/api/3/project/{project_key}", auth=JIRA_AUTH, timeout=30)
resp.raise_for_status()
project_id = resp.json()["id"]
print(f"project_id dla '{project_key}': {project_id}\n")

resp2 = requests.get(
    f"{JIRA_BASE_URL}/rest/api/3/fieldconfigurationscheme/project",
    auth=JIRA_AUTH, params={"projectId": project_id}, timeout=30,
)
print(f"Status: {resp2.status_code}\n")
print("SUROWA ODPOWIEDŹ JSON:")
print(json.dumps(resp2.json(), indent=2, ensure_ascii=False))
