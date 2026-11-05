#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
atlassian_suspend_to_deactivate.py

Znajduje konta użytkowników w organizacji Atlassian o statusie "suspended"
i dezaktywuje je (deactivated) przy pomocy Lifecycle API.

WYMAGANIA:
- Musisz być Org Adminem.
- Potrzebujesz "Organization API key" (Bearer token) wygenerowanego
  przez konto Org Admina w Atlassian Administration -> Settings -> API keys.
  (To NIE jest zwykły "API token" do Jira/Confluence - to osobny klucz
  do api.atlassian.com/admin i api.atlassian.com/users.)
- ID Twojej organizacji (Org ID) - widoczne w Atlassian Administration -> Settings.
- Dezaktywacja działa TYLKO na kontach zarządzanych (managed accounts).

UŻYCIE:
    python atlassian_suspend_to_deactivate.py --dry-run
    python atlassian_suspend_to_deactivate.py --execute
    python atlassian_suspend_to_deactivate.py --execute --message "Offboarding - konto nieaktywne"

Zmienne środowiskowe (zamiast wpisywania w kodzie):
    ATLASSIAN_ORG_ID
    ATLASSIAN_API_KEY
"""

import argparse
import os
import sys
import time
import requests

API_BASE = "https://api.atlassian.com"


def get_directories(org_id: str, headers: dict) -> list:
    """Pobiera listę katalogów (directories) w organizacji."""
    url = f"{API_BASE}/admin/v2/orgs/{org_id}/directories"
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json().get("data", [])


def get_users_in_directory(org_id: str, directory_id: str, headers: dict):
    """Generator zwracający kolejne strony użytkowników z danego katalogu."""
    url = f"{API_BASE}/admin/v2/orgs/{org_id}/directories/{directory_id}/users"
    params = {"limit": 100}

    while url:
        resp = requests.get(url, headers=headers, params=params)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 5))
            print(f"  Rate limit, czekam {retry_after}s...")
            time.sleep(retry_after)
            continue
        resp.raise_for_status()
        payload = resp.json()

        for user in payload.get("data", []):
            yield user

        # Paginacja - jeśli API zwraca link do następnej strony
        next_link = payload.get("links", {}).get("next")
        if next_link:
            url = next_link
            params = None  # link "next" zwykle zawiera już parametry
        else:
            url = None


def deactivate_user(account_id: str, headers: dict, message: str) -> bool:
    """Dezaktywuje konto (Lifecycle API). Zwraca True jeśli się udało."""
    url = f"{API_BASE}/users/{account_id}/manage/lifecycle/disable"
    resp = requests.post(url, headers=headers, json={"message": message})

    if resp.status_code == 429:
        retry_after = int(resp.headers.get("Retry-After", 5))
        print(f"  Rate limit, czekam {retry_after}s...")
        time.sleep(retry_after)
        return deactivate_user(account_id, headers, message)

    if resp.ok:
        return True

    print(f"  BŁĄD ({resp.status_code}): {resp.text}")
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-id", default=os.environ.get("ATLASSIAN_ORG_ID"),
                         help="ID organizacji Atlassian (lub zmienna ATLASSIAN_ORG_ID)")
    parser.add_argument("--api-key", default=os.environ.get("ATLASSIAN_API_KEY"),
                         help="Organization API key (lub zmienna ATLASSIAN_API_KEY)")
    parser.add_argument("--message", default="Konto dezaktywowane automatycznie (było suspended)",
                         help="Wiadomość zapisana przy dezaktywacji konta")
    parser.add_argument("--dry-run", action="store_true",
                         help="Tylko pokaż, które konta zostałyby zdezaktywowane")
    parser.add_argument("--execute", action="store_true",
                         help="Faktycznie wykonaj dezaktywację")
    args = parser.parse_args()

    if not args.org_id or not args.api_key:
        sys.exit("Podaj --org-id i --api-key (albo ustaw ATLASSIAN_ORG_ID / ATLASSIAN_API_KEY).")

    # Usuwamy przypadkowe białe znaki/nowe linie - częsta przyczyna błędu 401
    args.org_id = args.org_id.strip()
    args.api_key = args.api_key.strip()

    if not args.dry_run and not args.execute:
        sys.exit("Wybierz --dry-run (podgląd) albo --execute (wykonanie zmian).")

    headers = {
        "Authorization": f"Bearer {args.api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    print("Pobieram katalogi organizacji...")
    directories = get_directories(args.org_id, headers)
    if not directories:
        sys.exit("Nie znaleziono żadnego katalogu (directory) w organizacji.")

    suspended_users = []
    for directory in directories:
        dir_id = directory["directoryId"]
        dir_name = directory.get("name", dir_id)
        print(f"Skanuję katalog: {dir_name}")

        for user in get_users_in_directory(args.org_id, dir_id, headers):
            status = (user.get("accountStatus") or "").lower()
            if status == "suspended":
                suspended_users.append({
                    "account_id": user.get("accountId"),
                    "name": user.get("name") or user.get("nickname"),
                    "email": user.get("email"),
                    "claim_status": (user.get("claimStatus") or "").lower(),
                })

    if not suspended_users:
        print("Nie znaleziono żadnych kont ze statusem 'suspended'.")
        return

    print(f"\nZnaleziono {len(suspended_users)} kont ze statusem suspended:\n")
    for u in suspended_users:
        tag = "" if u["claim_status"] == "managed" else "  [NIEZARZĄDZANE - dezaktywacja może się nie udać]"
        print(f"  - {u['name']} <{u['email']}> ({u['account_id']}){tag}")

    if args.dry_run:
        print("\n[DRY RUN] Nic nie zostało zmienione. Uruchom z --execute, żeby dezaktywować te konta.")
        return

    print("\nDezaktywuję konta...")
    success, failed = 0, 0
    for u in suspended_users:
        print(f"  Dezaktywuję {u['email']} ({u['account_id']})...")
        if deactivate_user(u["account_id"], headers, args.message):
            success += 1
        else:
            failed += 1
        time.sleep(0.3)  # lekki throttling, żeby nie trafić w rate limit

    print(f"\nGotowe. Zdezaktywowano: {success}, błędy: {failed}.")


if __name__ == "__main__":
    main()
