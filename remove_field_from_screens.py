#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
remove_field_from_screens.py — zdejmuje pole z ekranów Jiry (pole i jego dane zostają).

build_jira_schema.py tylko DODAJE pola do ekranów; ten skrypt robi odwrotność.
Samo pole NIE jest usuwane — wartości w zgłoszeniach zostają, pole przestaje
być widoczne na wskazanych ekranach.

Użycie:
    python remove_field_from_screens.py --field "Nr zgłoszenia u Dostawcy" --schema jira_schema_klient.yaml --dry-run
    python remove_field_from_screens.py --field "Nr zgłoszenia u Dostawcy" --schema jira_schema_klient.yaml
    python remove_field_from_screens.py --field customfield_11972 --all-screens --dry-run

Zakres ekranów:
    --schema PLIK    — ekrany z sekcji 'screens' w YAML (zalecane)
    --all-screens    — WSZYSTKIE ekrany w Jirze (ostrożnie: też innych projektów)
"""
import argparse
import sys
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core

API = "/rest/api/3"


def call(method, path, **kw):
    return requests.request(method, f"{core.JIRA_BASE_URL}{path}", auth=core.jira_auth(), timeout=60,
                            headers={"Accept": "application/json"}, **kw)


def paged(path, **params):
    out, start = [], 0
    while True:
        r = call("GET", path, params={"startAt": start, "maxResults": 100, **params})
        r.raise_for_status()
        data = r.json()
        vals = data.get("values", [])
        out += vals
        if data.get("isLast", True) or not vals:
            return out
        start += len(vals)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--field", required=True, help="Nazwa pola albo ID (customfield_XXXXX)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--schema", help="YAML — tylko ekrany z sekcji 'screens'")
    g.add_argument("--all-screens", action="store_true", help="Wszystkie ekrany w Jirze")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    want = a.field.strip().lower()
    fields = call("GET", f"{API}/field").json()
    matches = [f for f in fields if f["id"].lower() == want or f["name"].strip().lower() == want]
    if not matches:
        print(f"[BŁĄD] Pole '{a.field}' nie istnieje w Jirze")
        sys.exit(1)
    if len(matches) > 1:
        print(f"[BŁĄD] Kilka pól o nazwie '{a.field}': "
              f"{', '.join(f['id'] for f in matches)} — podaj ID w --field")
        sys.exit(1)
    fid, fname = matches[0]["id"], matches[0]["name"]
    print(f"Pole: '{fname}' ({fid})")

    screens = paged(f"{API}/screens")
    if a.schema:
        names = {s["name"] for s in yaml.safe_load(Path(a.schema).read_text(encoding="utf-8")).get("screens") or []}
        screens = [s for s in screens if s["name"] in names]
        missing = names - {s["name"] for s in screens}
        for n in sorted(missing):
            print(f"  [POMIJAM] ekranu '{n}' nie ma w Jirze")
    print(f"Ekranów do sprawdzenia: {len(screens)}\n")

    removed = errors = 0
    for s in screens:
        tabs = call("GET", f"{API}/screens/{s['id']}/tabs").json()
        for t in tabs:
            on_tab = call("GET", f"{API}/screens/{s['id']}/tabs/{t['id']}/fields").json()
            if not any(f["id"] == fid for f in on_tab):
                continue
            if a.dry_run:
                print(f"  [DRY-RUN] Usunąłbym z '{s['name']}' / '{t['name']}'")
                removed += 1
                continue
            r = call("DELETE", f"{API}/screens/{s['id']}/tabs/{t['id']}/fields/{fid}")
            if r.status_code >= 300:
                errors += 1
                print(f"  [BŁĄD] '{s['name']}' / '{t['name']}': HTTP {r.status_code} {r.text[:200]}")
            else:
                removed += 1
                print(f"  [USUNIĘTO] '{s['name']}' / '{t['name']}'")

    verb = "Do usunięcia" if a.dry_run else "Usunięto"
    print(f"\n{verb}: {removed}" + (f", błędów: {errors}" if errors else ""))
    if not removed:
        print("Pola nie ma na żadnym z tych ekranów.")


if __name__ == "__main__":
    main()
