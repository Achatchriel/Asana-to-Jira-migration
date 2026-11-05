#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
people_source.py — TYLKO ODCZYT. Źródła do mapowania osób Asana → Jira w panelu.

  python migration_app/people_source.py groups
      -> migration_app/cache/jira_groups.json   (wszystkie grupy Jiry)

  python migration_app/people_source.py load --groups "Firma: Staff,Firma: Developers"
      -> migration_app/cache/people.json
         asana: członkowie workspace'u (gid, imię, e-mail, gość, aktywny)
         jira:  członkowie wybranych grup (accountId, imię, e-mail, grupy)
         suggestions: propozycja dopasowania dla każdej osoby z Asany
                      (e-mail → ta sama część przed @ → imię i nazwisko → wzorzec imie.nazwisko@)

Nic nie zapisuje do Jiry, Asany ani user_map.json - mapę zapisuje dopiero panel.
"""

import argparse
import json
import os
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = Path(__file__).resolve().parent / "cache"
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import asana_jira_sync as sync  # noqa: E402
from generate_user_map import candidate_emails_from_name  # noqa: E402


def jira_get(path: str, **params):
    resp = sync.request_with_retry("GET", f"{sync.JIRA_BASE_URL}{path}", auth=sync.JIRA_AUTH,
                                   params=params, timeout=sync.REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def jira_groups() -> list:
    groups, start = [], 0
    while True:
        page = jira_get("/rest/api/3/group/bulk", startAt=start, maxResults=50)
        groups += [{"name": g["name"], "id": g.get("groupId")} for g in page.get("values", [])]
        if page.get("isLast", True):
            return sorted(groups, key=lambda g: g["name"].lower())
        start += 50


def group_members(name: str) -> list:
    out, start = [], 0
    while True:
        page = jira_get("/rest/api/3/group/member", groupname=name, startAt=start, maxResults=50,
                        includeInactiveUsers="false")
        out += [u for u in page.get("values", []) if u.get("accountType") == "atlassian"]
        if page.get("isLast", True):
            return out
        start += 50


def asana_members() -> list:
    url = f"{sync.ASANA_API}/workspaces/{sync.ASANA_WORKSPACE_GID}/workspace_memberships"
    params = {"opt_fields": "user.name,user.email,user.gid,is_guest,is_active", "limit": 100}
    out, offset = [], None
    while True:
        if offset:
            params["offset"] = offset
        resp = sync.request_with_retry("GET", url, headers=sync.ASANA_HEADERS, params=params,
                                       timeout=sync.REQUEST_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
        for m in payload.get("data", []):
            u = m.get("user") or {}
            if u.get("gid"):
                out.append({"gid": u["gid"], "name": u.get("name") or "", "email": (u.get("email") or "").lower(),
                            "guest": bool(m.get("is_guest")), "active": bool(m.get("is_active", True))})
        offset = (payload.get("next_page") or {}).get("offset")
        if not offset:
            return sorted(out, key=lambda u: u["name"].lower())


def norm_name(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return " ".join(s.replace(".", " ").replace("-", " ").split())


def suggest(asana: list, jira: list) -> dict:
    by_email = {u["email"]: u for u in jira if u["email"]}
    by_local = {}
    for u in jira:
        if u["email"]:
            by_local.setdefault(u["email"].split("@")[0], []).append(u)
    by_name = {}
    for u in jira:
        by_name.setdefault(norm_name(u["name"]), []).append(u)
    out = {}
    for a in asana:
        hit, reason = None, None
        if a["email"] and a["email"] in by_email:
            hit, reason = by_email[a["email"]], "e-mail"
        elif a["email"] and len(by_local.get(a["email"].split("@")[0], [])) == 1:
            hit, reason = by_local[a["email"].split("@")[0]][0], "e-mail (inna domena)"
        elif len(by_name.get(norm_name(a["name"]), [])) == 1:
            hit, reason = by_name[norm_name(a["name"])][0], "imię i nazwisko"
        else:
            for cand in candidate_emails_from_name(a["name"]):
                if cand in by_email:
                    hit, reason = by_email[cand], "wzorzec imie.nazwisko@"
                    break
        if hit:
            out[a["gid"]] = {"account_id": hit["account_id"], "reason": reason}
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Źródła do mapowania osób (tylko odczyt).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("groups")
    load = sub.add_parser("load")
    load.add_argument("--groups", required=True, help="Nazwy grup Jiry po przecinku")
    args = parser.parse_args()
    CACHE_DIR.mkdir(exist_ok=True)

    if args.cmd == "groups":
        groups = jira_groups()
        (CACHE_DIR / "jira_groups.json").write_text(json.dumps({"groups": groups, "loaded_at": time.time()},
                                                               ensure_ascii=False), encoding="utf-8")
        print(f"Grup Jiry: {len(groups)}", file=sys.stderr)
        return

    names = [g.strip() for g in args.groups.split(",") if g.strip()]
    jira = {}
    for name in names:
        members = group_members(name)
        print(f"Jira, grupa „{name}”: {len(members)} osób", file=sys.stderr)
        for u in members:
            j = jira.setdefault(u["accountId"], {"account_id": u["accountId"], "name": u.get("displayName") or "",
                                                 "email": (u.get("emailAddress") or "").lower(), "groups": []})
            j["groups"].append(name)
    jira_list = sorted(jira.values(), key=lambda u: u["name"].lower())
    asana = asana_members()
    print(f"Asana: {len(asana)} osób w workspace", file=sys.stderr)
    data = {"loaded_at": time.time(), "groups": names, "asana": asana, "jira": jira_list,
            "jira_emails_visible": any(u["email"] for u in jira_list),
            "suggestions": suggest(asana, jira_list)}
    (CACHE_DIR / "people.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"Propozycje dopasowań: {len(data['suggestions'])} z {len(asana)}", file=sys.stderr)


if __name__ == "__main__":
    main()
