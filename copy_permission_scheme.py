#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
copy_permission_scheme.py

Kopiuje schemat uprawnień Jiry (wszystkie uprawnienia i ich odbiorców: role
projektowe, grupy, osoby, itd.) pod NOWĄ nazwą i opcjonalnie podpina kopię do
projektu. Oryginalny schemat zostaje nietknięty.

Użycie:
    python copy_permission_scheme.py --source-id 10131 --name "ITW: permission scheme" --dry-run
    python copy_permission_scheme.py --source-id 10131 --name "ITW: permission scheme" --assign ITW
"""
import argparse
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
BASE = os.environ["JIRA_BASE_URL"].rstrip("/")
AUTH = (os.environ["JIRA_EMAIL"], os.environ["JIRA_API_TOKEN"])


def holder_for_create(h: dict) -> dict:
    """Odbiorca uprawnienia w formacie do utworzenia (bez pól tylko do odczytu)."""
    out = {"type": h["type"]}
    value = h.get("value") or h.get("parameter")
    if value:
        out["value"] = value
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source-id", required=True, help="ID schematu źródłowego (np. 10131)")
    ap.add_argument("--name", required=True, help="Nazwa nowego schematu")
    ap.add_argument("--description", default=None)
    ap.add_argument("--assign", default=None, help="Klucz projektu, do którego podpiąć kopię")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    r = requests.get(f"{BASE}/rest/api/3/permissionscheme/{args.source_id}", auth=AUTH,
                     params={"expand": "permissions"}, timeout=30)
    r.raise_for_status()
    src = r.json()
    perms = [{"holder": holder_for_create(p["holder"]), "permission": p["permission"]}
             for p in src.get("permissions", [])]
    print(f"Źródło: {src['id']} '{src['name']}' - uprawnień: {len(perms)}")

    existing = requests.get(f"{BASE}/rest/api/3/permissionscheme", auth=AUTH, timeout=30).json()
    if any(s["name"] == args.name for s in existing.get("permissionSchemes", [])):
        print(f"[BŁĄD] Schemat o nazwie '{args.name}' już istnieje. Wybierz inną nazwę. Nic nie zmieniono.")
        sys.exit(1)

    if args.dry_run:
        print(f"[DRY-RUN] Utworzyłbym '{args.name}' z {len(perms)} uprawnieniami"
              + (f" i podpiął do {args.assign}." if args.assign else "."))
        return

    # Część uprawnień Jira zwraca w schemacie, ale nie przyjmuje przy tworzeniu przez API
    # (np. COPY_PROJECT). Takie pomijamy i wypisujemy - można je dodać ręcznie w kopii.
    import re
    skipped = []
    for _ in range(20):
        body = {"name": args.name, "permissions": perms,
                "description": args.description or f"Kopia '{src['name']}'"}
        r = requests.post(f"{BASE}/rest/api/3/permissionscheme", auth=AUTH, json=body, timeout=60)
        m = re.search(r"Unrecognized permission: ([A-Z_]+)", r.text) if r.status_code == 400 else None
        if not m:
            break
        bad = m.group(1)
        skipped.append((bad, sum(1 for p in perms if p["permission"] == bad)))
        perms = [p for p in perms if p["permission"] != bad]
    if r.status_code >= 300:
        print(f"[BŁĄD] Utworzenie nie powiodło się: {r.status_code} {r.text[:400]}")
        sys.exit(1)
    if skipped:
        print("Pominięte uprawnienia (Jira nie przyjmuje ich przez API, dodaj ręcznie w kopii, jeśli potrzebne): "
              + ", ".join(f"{k} ({n} wpisów)" for k, n in skipped))
    new = r.json()
    check = requests.get(f"{BASE}/rest/api/3/permissionscheme/{new['id']}", auth=AUTH,
                         params={"expand": "permissions"}, timeout=30).json()
    print(f"Utworzono: {new['id']} '{new['name']}' - uprawnień: {len(check.get('permissions', []))} "
          f"(źródło: {len(perms)})")

    if args.assign:
        r = requests.put(f"{BASE}/rest/api/3/project/{args.assign}/permissionscheme", auth=AUTH,
                         json={"id": int(new["id"])}, timeout=30)
        print(f"Podpięcie do {args.assign}: " + ("OK" if r.status_code < 300 else f"{r.status_code} {r.text[:200]}"))


if __name__ == "__main__":
    main()
