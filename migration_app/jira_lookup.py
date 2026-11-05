#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
jira_lookup.py — TYLKO ODCZYT. Małe zapytania do Jiry dla panelu (karta „Kopia projektu”).
Wynik: JSON na stdout. Z .env czyta tylko JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN.

  python migration_app/jira_lookup.py allroles              # wszystkie role projektowe w Jirze
  python migration_app/jira_lookup.py roles --project ABC   # role projektu z osobami i grupami + wszystkie role
  python migration_app/jira_lookup.py statuses --project ABC  # statusy projektu z kategorią
  python migration_app/jira_lookup.py users --query jan     # wyszukiwanie osób
  python migration_app/jira_lookup.py groups --query staff  # wyszukiwanie grup
"""

import argparse
import json
import sys
from pathlib import Path

import requests
from dotenv import dotenv_values

ENV = dotenv_values(Path(__file__).resolve().parent.parent / ".env")
BASE = (ENV.get("JIRA_BASE_URL") or "").rstrip("/")
AUTH = (ENV.get("JIRA_EMAIL") or "", ENV.get("JIRA_API_TOKEN") or "")
ADDON_ROLE = "atlassian-addons-project-access"


def get(path, **params):
    r = requests.get(f"{BASE}{path}", auth=AUTH, params=params, timeout=30)
    if r.status_code >= 300:
        raise SystemExit(f"{path}: {r.status_code} {r.text[:300]}")
    return r.json()


def roles(project: str) -> dict:
    out = []
    for name, url in sorted(get(f"/rest/api/3/project/{project}/role").items()):
        if name == ADDON_ROLE:
            continue
        rid = url.rstrip("/").rsplit("/", 1)[-1]
        actors = get(f"/rest/api/3/project/{project}/role/{rid}").get("actors", [])
        out.append({"id": rid, "name": name,
                    "users": [{"accountId": a["actorUser"]["accountId"], "name": a.get("displayName")}
                              for a in actors if (a.get("actorUser") or {}).get("accountId")],
                    "groups": [{"groupId": a["actorGroup"]["groupId"], "name": a["actorGroup"].get("name") or a.get("displayName")}
                               for a in actors if (a.get("actorGroup") or {}).get("groupId")]})
    all_roles = [{"id": str(r["id"]), "name": r["name"]} for r in get("/rest/api/3/role") if r["name"] != ADDON_ROLE]
    return {"project": project.upper(), "roles": out, "all_roles": sorted(all_roles, key=lambda r: r["name"].lower())}


def all_roles() -> list:
    return sorted(({"id": str(r["id"]), "name": r["name"], "description": r.get("description") or ""}
                   for r in get("/rest/api/3/role") if r["name"] != ADDON_ROLE), key=lambda r: r["name"].lower())


def statuses(project: str) -> list:
    """Statusy projektu (unikalne nazwy z kategorią) - cel mapowania sekcji Asany."""
    out = {}
    for it in get(f"/rest/api/3/project/{project}/statuses"):
        for s in it.get("statuses", []):
            out.setdefault(s["name"], (s.get("statusCategory") or {}).get("key"))
    return [{"name": n, "category": c} for n, c in sorted(out.items(), key=lambda x: x[0].lower())]


def users(query: str) -> list:
    return [{"accountId": u["accountId"], "name": u.get("displayName"), "email": u.get("emailAddress"),
             "active": u.get("active", True)}
            for u in get("/rest/api/3/user/search", query=query, maxResults=20) if u.get("accountType") == "atlassian"]


def groups(query: str) -> list:
    """Filtr po fragmencie nazwy na pełnej liście grup (wyszukiwarka Jiry dopasowuje tylko początek nazwy)."""
    q, found, start = query.lower(), [], 0
    while True:
        page = get("/rest/api/3/group/bulk", startAt=start, maxResults=50)
        found += [{"groupId": g.get("groupId"), "name": g["name"]} for g in page.get("values", []) if q in g["name"].lower()]
        if page.get("isLast", True):
            return sorted(found, key=lambda g: g["name"].lower())[:30]
        start += 50


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["allroles", "roles", "statuses", "users", "groups"])
    ap.add_argument("--project", default="")
    ap.add_argument("--query", default="")
    a = ap.parse_args()
    if not (BASE and all(AUTH)):
        sys.exit("Brak dostępu do Jiry w .env.")
    data = (all_roles() if a.cmd == "allroles" else roles(a.project) if a.cmd == "roles"
            else statuses(a.project) if a.cmd == "statuses"
            else users(a.query) if a.cmd == "users" else groups(a.query))
    json.dump(data, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
