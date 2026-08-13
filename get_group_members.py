#!/usr/bin/env python3
"""
get_group_members.py

Wypisuje accountId, imię i nazwisko oraz e-mail (jeśli widoczny) wszystkich
członków podanej grupy w Jirze. Przydatne np. do budowania user_map.json
ręcznie, albo do masowego dodawania konkretnych osób gdzieś indziej.

Użycie:
    python get_group_members.py "Nazwa grupy"
    python get_group_members.py "Nazwa grupy" --json wynik.json
"""
import json
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


def find_group_id(group_name: str) -> "str | None":
    """Szuka groupId po dokładnej nazwie (case-insensitive) przez
    /rest/api/3/group/bulk z paginacją — NIE przez groups/picker, które
    ogranicza wyniki do podpowiedzi."""
    url = f"{JIRA_BASE_URL}/rest/api/3/group/bulk"
    start_at = 0
    max_results = 50
    norm_target = group_name.strip().lower()
    while True:
        resp = requests.get(
            url, auth=AUTH, params={"startAt": start_at, "maxResults": max_results}, timeout=30
        )
        resp.raise_for_status()
        payload = resp.json()
        for g in payload.get("values", []):
            if g["name"].strip().lower() == norm_target:
                return g["groupId"]
        if payload.get("isLast", True):
            return None
        start_at += max_results


def get_group_members(group_id: str) -> list:
    url = f"{JIRA_BASE_URL}/rest/api/3/group/member"
    members = []
    start_at = 0
    max_results = 50
    while True:
        resp = requests.get(
            url, auth=AUTH,
            params={"groupId": group_id, "startAt": start_at, "maxResults": max_results, "includeInactiveUsers": False},
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        members.extend(payload.get("values", []))
        if payload.get("isLast", True):
            return members
        start_at += max_results


def main() -> None:
    if len(sys.argv) < 2:
        print('Użycie: python get_group_members.py "Nazwa grupy" [--json plik.json]')
        sys.exit(1)

    group_name = sys.argv[1]
    json_out = None
    if "--json" in sys.argv:
        idx = sys.argv.index("--json")
        if idx + 1 < len(sys.argv):
            json_out = sys.argv[idx + 1]

    print(f"Szukam grupy '{group_name}'...")
    group_id = find_group_id(group_name)
    if not group_id:
        print(f"Nie znaleziono grupy o nazwie '{group_name}'. Sprawdź dokładną pisownię przez: python list_groups.py")
        sys.exit(1)

    members = get_group_members(group_id)
    print(f"\nCzłonkowie grupy '{group_name}' ({len(members)}):\n")
    for m in members:
        print(f"  accountId: {m.get('accountId')}")
        print(f"    displayName: {m.get('displayName')}")
        print(f"    email:       {m.get('emailAddress', '(niewidoczny)')}")
        print()

    if json_out:
        data = [
            {
                "accountId": m.get("accountId"),
                "displayName": m.get("displayName"),
                "emailAddress": m.get("emailAddress"),
            }
            for m in members
        ]
        Path(json_out).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Zapisano też do: {json_out}")


if __name__ == "__main__":
    main()
