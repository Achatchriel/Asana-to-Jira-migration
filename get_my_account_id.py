#!/usr/bin/env python3
"""
get_my_account_id.py

Pomocniczy skrypt: wypisuje accountId konta powiązanego z JIRA_EMAIL / JIRA_API_TOKEN
z pliku .env. Przydatne do uzupełnienia DEFAULT_LEAD_ACCOUNT_ID albo kolumny
lead_account_id w arkuszu.

Użycie:
    python get_my_account_id.py                  # własne konto
    python get_my_account_id.py inny@email.com    # wyszukaj konto po adresie e-mail
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


def main() -> None:
    if len(sys.argv) > 1:
        query = sys.argv[1]
        url = f"{JIRA_BASE_URL}/rest/api/3/user/search"
        resp = requests.get(url, auth=AUTH, params={"query": query}, timeout=30)
        resp.raise_for_status()
        users = resp.json()
        if not users:
            print(f"Nie znaleziono użytkownika pasującego do '{query}'.")
            return
        for u in users:
            print(f"{u['displayName']:30} accountId: {u['accountId']}  ({u.get('emailAddress', 'brak e-maila w wyniku')})")
    else:
        url = f"{JIRA_BASE_URL}/rest/api/3/myself"
        resp = requests.get(url, auth=AUTH, timeout=30)
        resp.raise_for_status()
        me = resp.json()
        print(f"{me['displayName']:30} accountId: {me['accountId']}")


if __name__ == "__main__":
    main()
