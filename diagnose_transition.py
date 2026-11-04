#!/usr/bin/env python3
"""
diagnose_transition.py

Tworzy TYMCZASOWE zadanie testowe w podanym projekcie, sprawdza DOKŁADNIE
to samo, co widzi asana_jira_sync.py (GET /issue/{key}/transitions), i
pokazuje surową listę dostępnych przejść - żeby jednoznacznie ustalić, czy
przejście do "To clarify" faktycznie jest widoczne dla konta używanego
przez nasze skrypty, czy nie (bez polegania na nawigacji w UI Jiry, gdzie
różne widoki różnie pokazują tę informację).

Na końcu USUWA testowe zadanie - nie zostawia śmieci.

Użycie:
    python diagnose_transition.py CTMGES
"""
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core
from asana_jira_sync import get_required_field_defaults  # te same pola wymagane, co przy prawdziwym tworzeniu

project_key = sys.argv[1] if len(sys.argv) > 1 else "CTMGES"

print(f"1. Sprawdzam wymagane pola dla '{project_key}' (tak jak robi to prawdziwa synchronizacja)...")
extra_fields = get_required_field_defaults(project_key, "Task")
if extra_fields:
    print(f"   Dodatkowe pola wymagane, uzupełnione automatycznie: {list(extra_fields.keys())}")

print(f"\n2. Tworzę tymczasowe zadanie testowe w '{project_key}'...")
resp = requests.post(
    f"{core.JIRA_BASE_URL}/rest/api/3/issue",
    auth=core.jira_auth(),
    json={"fields": {
        "project": {"key": project_key},
        "summary": "[TYMCZASOWE - diagnostyka przejść, można usunąć]",
        "issuetype": {"name": "Task"},
        **extra_fields,
    }},
)
if resp.status_code >= 300:
    print(f"   [BŁĄD] {resp.status_code}: {resp.text}")
    print("   (Jeśli to nadal 400 z powodu innego brakującego pola - ten konkretny projekt ma")
    print("    pole wymagane z WIELOMA dozwolonymi wartościami, którego nie da się zgadnąć")
    print("    automatycznie. Napisz do mnie dokładną treść błędu powyżej.)")
    sys.exit(1)
issue_key = resp.json()["key"]
print(f"   Utworzono: {issue_key}")

print(f"\n3. Sprawdzam dostępne przejścia (DOKŁADNIE ten sam endpoint co asana_jira_sync.py)...")
resp2 = requests.get(f"{core.JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/transitions", auth=core.jira_auth())
resp2.raise_for_status()
transitions = resp2.json().get("transitions", [])

print(f"\n   Znaleziono {len(transitions)} dostępnych przejść dla konta z .env:")
found_to_clarify = False
for t in transitions:
    target_name = t["to"]["name"]
    target_id = t["to"]["id"]
    marker = " <-- TO CLARIFY!" if "clarify" in target_name.lower() else ""
    if marker:
        found_to_clarify = True
    print(f"     id={t['id']:4} '{t['name']}' -> '{target_name}' (id={target_id}){marker}")

print("\n" + "=" * 60)
if found_to_clarify:
    print("WNIOSEK: przejście do 'To clarify' JEST widoczne dla tego konta przez API.")
    print("         Problem leży więc w NASZYM kodzie budowania grafu (build_workflow_graph),")
    print("         nie w uprawnieniach/warunkach w Jirze.")
else:
    print("WNIOSEK: przejście do 'To clarify' NIE POJAWIA SIĘ w liście dla tego konta.")
    print("         To potwierdza: przejście ma jakiś warunek (Condition) blokujący je dla")
    print("         konta z .env, mimo że jest widoczne w diagramie administracyjnym.")

print(f"\n4. Sprzątam - usuwam tymczasowe zadanie {issue_key}...")
requests.delete(f"{core.JIRA_BASE_URL}/rest/api/3/issue/{issue_key}", auth=core.jira_auth())
print("   Gotowe.")
