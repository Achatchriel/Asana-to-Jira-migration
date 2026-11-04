#!/usr/bin/env python3
"""
list_asana_members.py

Wypisuje gid, imię i nazwisko oraz e-mail (jeśli widoczny) członków workspace'u
Asany — analogicznie do get_group_members.py dla Jiry. Pozwala odfiltrować
gości od pełnoprawnych członków.

Użycie:
    python list_asana_members.py                    # wszyscy (pełni członkowie + goście, oznaczeni)
    python list_asana_members.py --only-members      # tylko pełni członkowie (bez gości)
    python list_asana_members.py --only-guests       # tylko goście
    python list_asana_members.py --json wynik.json   # dodatkowo zapisz do pliku
"""
import argparse
import json
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)

ASANA_TOKEN = os.environ["ASANA_TOKEN"]
ASANA_WORKSPACE_GID = os.environ["ASANA_WORKSPACE_GID"]
ASANA_HEADERS = {"Authorization": f"Bearer {ASANA_TOKEN}"}
ASANA_API = "https://app.asana.com/api/1.0"


def get_workspace_memberships() -> list[dict]:
    """Pobiera WSZYSTKICH członków workspace'u (przez workspace_memberships,
    nie /users - tylko ten endpoint ujawnia pole 'is_guest' i 'is_active')."""
    url = f"{ASANA_API}/workspaces/{ASANA_WORKSPACE_GID}/workspace_memberships"
    params = {"opt_fields": "user.name,user.email,user.gid,is_guest,is_active", "limit": 100}
    memberships = []
    offset = None
    while True:
        if offset:
            params["offset"] = offset
        resp = requests.get(url, headers=ASANA_HEADERS, params=params, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        memberships.extend(payload.get("data", []))
        offset = payload.get("next_page", {}).get("offset") if payload.get("next_page") else None
        if not offset:
            return memberships


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only-members", action="store_true", help="Tylko pełnoprawni członkowie (bez gości).")
    parser.add_argument("--only-guests", action="store_true", help="Tylko goście.")
    parser.add_argument("--include-inactive", action="store_true", help="Uwzględnij też nieaktywne konta (domyślnie pomijane).")
    parser.add_argument("--json", type=str, default=None, help="Dodatkowo zapisz wynik do pliku JSON.")
    args = parser.parse_args()

    if args.only_members and args.only_guests:
        print("Błąd: --only-members i --only-guests wykluczają się wzajemnie.")
        return

    print("Pobieram członków workspace'u Asany...")
    memberships = get_workspace_memberships()

    rows = []
    for m in memberships:
        if not args.include_inactive and not m.get("is_active", True):
            continue
        is_guest = bool(m.get("is_guest"))
        if args.only_members and is_guest:
            continue
        if args.only_guests and not is_guest:
            continue
        user = m.get("user") or {}
        if not user.get("gid"):
            continue
        rows.append({
            "gid": user.get("gid"),
            "name": user.get("name"),
            "email": user.get("email"),
            "is_guest": is_guest,
        })

    label = "gości" if args.only_guests else ("pełnoprawnych członków" if args.only_members else "osób (członkowie + goście)")
    print(f"\nZnaleziono {len(rows)} {label}:\n")
    for r in rows:
        guest_tag = " [GOŚĆ]" if r["is_guest"] else ""
        print(f"  gid: {r['gid']}{guest_tag}")
        print(f"    name:  {r['name']}")
        print(f"    email: {r['email'] or '(niewidoczny)'}")
        print()

    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Zapisano też do: {args.json}")


if __name__ == "__main__":
    main()
