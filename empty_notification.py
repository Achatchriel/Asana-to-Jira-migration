# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
import os
import time
import requests
from requests.auth import HTTPBasicAuth

# --- KONFIGURACJA ---
JIRA_URL = os.environ["JIRA_BASE_URL"]
EMAIL = os.environ["JIRA_EMAIL"]
API_TOKEN = os.environ["JIRA_API_TOKEN"]
NEW_NOTIFICATION_SCHEME_ID = "10482" 

RAW_PROJECT_KEYS = """
PROJ1
PROJ2
"""

def parse_project_keys(raw_text):
    cleaned_text = raw_text.replace(',', ' ').replace('\n', ' ')
    keys = [k.strip().upper() for k in cleaned_text.split(' ') if k.strip()]
    return list(set(keys))

headers = {
    "Accept": "application/json",
    "Content-Type": "application/json"
}
auth = HTTPBasicAuth(EMAIL, API_TOKEN)

def update_notification_schemes():
    project_keys = parse_project_keys(RAW_PROJECT_KEYS)
    
    if not project_keys:
        print("Brak podanych kluczy projektów.")
        return

    print(f"Rozpoczynanie aktualizacji dla {len(project_keys)} projektów...")
    
    success_count = 0
    error_count = 0

    for project_key in project_keys:
        # Prawidłowy endpoint Jira Cloud API v3 do aktualizacji projektu
        url = f"{JIRA_URL}/rest/api/3/project/{project_key}"
        
        # Prawidłowa struktura payloadu: 'notificationScheme' przyjmuje numeryczne ID
        payload = {
            "notificationScheme": int(NEW_NOTIFICATION_SCHEME_ID)
        }
        
        res = requests.put(url, json=payload, headers=headers, auth=auth)
        
        if res.status_code in [200, 204]:
            print(f"[OK] Zmieniono dla: {project_key}")
            success_count += 1
        elif res.status_code == 429:
            print(f"[RATE LIMIT] Osiągnięto limit zapytań dla {project_key}. Czekam 5 sekund...")
            time.sleep(5)
            res = requests.put(url, json=payload, headers=headers, auth=auth)
            if res.status_code in [200, 204]:
                print(f"[OK] Zmieniono dla: {project_key} (po odczekaniu)")
                success_count += 1
            else:
                print(f"[ERROR] Błąd dla {project_key}: {res.status_code} - {res.text}")
                error_count += 1
        else:
            print(f"[ERROR] Błąd dla {project_key}: {res.status_code} - {res.text}")
            error_count += 1

        # Odczekanie chwilę pomiędzy zapytaniami
        time.sleep(0.1)

    print(f"\n--- PODSUMOWANIE ---")
    print(f"Pomyślnie zaktualizowano: {success_count}")
    print(f"Błędy: {error_count}")

if __name__ == "__main__":
    update_notification_schemes()
