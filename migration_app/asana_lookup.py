#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
asana_lookup.py — TYLKO ODCZYT. Projekt Asany z linku dla karty „Migracja projektu”:
nazwa, sekcje (w kolejności z Asany) i liczba zadań w każdej (otwarte / ukończone).
Wynik: JSON na stdout. Z .env czyta tylko ASANA_TOKEN.

  python migration_app/asana_lookup.py "https://app.asana.com/1/123/project/456/board"
"""

import json
import re
import sys
from pathlib import Path

import requests
from dotenv import dotenv_values

ENV = dotenv_values(Path(__file__).resolve().parent.parent / ".env")
API = "https://app.asana.com/api/1.0"
HEADERS = {"Authorization": f"Bearer {ENV.get('ASANA_TOKEN') or ''}"}


def get_all(path: str, **params) -> list:
    out, offset = [], None
    while True:
        if offset:
            params["offset"] = offset
        r = requests.get(f"{API}{path}", headers=HEADERS, params={**params, "limit": 100}, timeout=60)
        if r.status_code >= 300:
            raise SystemExit(f"Asana {path}: {r.status_code} {r.text[:200]}")
        payload = r.json()
        out += payload.get("data", [])
        offset = (payload.get("next_page") or {}).get("offset")
        if not offset:
            return out


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("Podaj link do projektu Asany.")
    m = re.search(r"asana\.com/(?:0/(\d+)|1/\d+/project/(\d+))", sys.argv[1])
    if not m:
        sys.exit("Nie rozpoznano linku do projektu Asany.")
    gid = m.group(1) or m.group(2)
    r = requests.get(f"{API}/projects/{gid}", headers=HEADERS, params={"opt_fields": "name"}, timeout=30)
    if r.status_code >= 300:
        sys.exit(f"Projekt Asany {gid}: {r.status_code} - nie istnieje albo token nie ma do niego dostępu.")
    name = r.json()["data"]["name"]
    sections = [s["name"].strip() for s in get_all(f"/projects/{gid}/sections", opt_fields="name")]
    counts = {s: {"open": 0, "completed": 0} for s in sections}
    tasks = get_all(f"/projects/{gid}/tasks", opt_fields="completed,memberships.section.name,memberships.project.gid")
    for t in tasks:
        sec = next(((m.get("section") or {}).get("name", "").strip() for m in t.get("memberships") or []
                    if (m.get("project") or {}).get("gid") == gid), "") or "(no section)"
        counts.setdefault(sec, {"open": 0, "completed": 0})["completed" if t.get("completed") else "open"] += 1
    json.dump({"gid": gid, "name": name, "tasks": len(tasks),
               "sections": [{"name": s, **c} for s, c in counts.items()]}, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
