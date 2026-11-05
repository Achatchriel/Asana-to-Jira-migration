#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
asana_check.py — TYLKO ODCZYT, niczego nie zmienia (ani w Asanie, ani w Jirze, ani w stanie).

Dla każdego podanego projektu sprawdza, czy statusy i przypisania zgadzają się
między Asaną a Jirą, z użyciem DOKŁADNIE tych samych reguł co synchronizacja
(section_rule / project_section_overrides z asana_jira_sync.py):

  Sekcje     - każda sekcja Asany: liczba zadań (otwarte/ukończone), docelowy
               status z mapy (i skąd: mapa projektu, szablon, mapa główna),
               czy taki status istnieje w projekcie Jiry.
  Osoby      - każdy przypisany w Asanie: czy jest w user_map.json, na kogo
               w Jirze, czy to konto jest aktywne i może być przypisane w projekcie.
  Zgodność   - dla zadań już zmigrowanych (wpis w task_sync_state.json):
               oczekiwany status/przypisany vs faktyczny w Jirze.

Sprawdzane są zadania z listy zadań projektu Asany (w tym podzadania dodane do
projektu); podzadania spoza projektu nie mają sekcji, więc są pomijane.

Uruchamiane przez panel (migration_app/server.py), ale działa też samodzielnie:
  python migration_app/asana_check.py ABC,DEF --out-dir migration_app/checks
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)  # skrypty jiraSync używają ścieżek względnych do plików stanu i map
sys.path.insert(0, str(ROOT))

import asana_jira_sync as sync  # noqa: E402
core = sync.core


def log(msg: str) -> None:
    print(msg, flush=True)


def asana_get_all(url: str, params: dict) -> list:
    params = {**params, "limit": 100}
    out, offset = [], None
    while True:
        if offset:
            params["offset"] = offset
        resp = sync.request_with_retry("GET", url, headers=sync.ASANA_HEADERS, params=params,
                                       timeout=sync.REQUEST_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
        out.extend(payload.get("data", []))
        offset = (payload.get("next_page") or {}).get("offset")
        if not offset:
            return out


def jira_get(path: str, **params):
    resp = sync.request_with_retry("GET", f"{sync.JIRA_BASE_URL}{path}", auth=sync.JIRA_AUTH,
                                   params=params, timeout=sync.REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def jira_issues(project_key: str) -> dict:
    issues, token = {}, None
    while True:
        body = {"jql": f'project = "{project_key}" ORDER BY key',
                "fields": ["status", "assignee", "summary"], "maxResults": 100}
        if token:
            body["nextPageToken"] = token
        resp = sync.request_with_retry("POST", f"{sync.JIRA_BASE_URL}/rest/api/3/search/jql",
                                       auth=sync.JIRA_AUTH, json=body, timeout=sync.REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        for i in data.get("issues", []):
            f = i["fields"]
            issues[i["key"]] = {"status": (f.get("status") or {}).get("name"),
                                "assignee": (f.get("assignee") or {}).get("accountId"),
                                "assignee_name": (f.get("assignee") or {}).get("displayName")}
        token = data.get("nextPageToken")
        if not token:
            return issues


def jira_users(account_ids: list) -> dict:
    users = {}
    ids = sorted(set(a for a in account_ids if a))
    for i in range(0, len(ids), 90):
        chunk = ids[i:i + 90]
        resp = sync.request_with_retry("GET", f"{sync.JIRA_BASE_URL}/rest/api/3/user/bulk",
                                       auth=sync.JIRA_AUTH, timeout=sync.REQUEST_TIMEOUT,
                                       params=[("accountId", a) for a in chunk] + [("maxResults", 100)])
        if resp.ok:
            for u in resp.json().get("values", []):
                users[u["accountId"]] = {"name": u.get("displayName"), "active": u.get("active", True)}
    return users


def assignable_ids(project_key: str) -> "set | None":
    ids, start = set(), 0
    while True:
        resp = sync.request_with_retry("GET", f"{sync.JIRA_BASE_URL}/rest/api/3/user/assignable/search",
                                       auth=sync.JIRA_AUTH, timeout=sync.REQUEST_TIMEOUT,
                                       params={"project": project_key, "startAt": start, "maxResults": 1000})
        if not resp.ok:
            return None
        page = resp.json()
        ids |= {u["accountId"] for u in page}
        if len(page) < 1000:
            return ids
        start += len(page)


def assignable_holders(project_key: str) -> "dict | None":
    """Komu schemat uprawnień projektu daje ASSIGNABLE_USER (role, grupy, osoby)."""
    try:
        scheme = jira_get(f"/rest/api/3/project/{project_key}/permissionscheme")
        full = jira_get(f"/rest/api/3/permissionscheme/{scheme['id']}", expand="permissions,group,projectRole,user")
    except Exception:  # noqa: BLE001 - brak uprawnień admina do odczytu schematu
        return None
    holders = []
    for p in full.get("permissions", []):
        if p["permission"] != "ASSIGNABLE_USER":
            continue
        h = p["holder"]
        name = ((h.get("projectRole") or {}).get("name") or (h.get("group") or {}).get("name")
                or (h.get("user") or {}).get("displayName") or h.get("parameter") or h["type"])
        holders.append({"type": h["type"], "name": name})
    return {"scheme": full.get("name"), "scheme_id": scheme["id"], "holders": holders}


def map_sources(project_key: str) -> tuple:
    """(nazwa szablonu albo None, wpisy z pliku projektu, wpisy szablonu)."""
    path = sync.SECTION_STATUS_MAP_FILE.with_name(f"{sync.SECTION_STATUS_MAP_FILE.stem}.{project_key}.json")
    own = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    profile = own.pop(sync.PROFILE_KEY, None)
    profile_entries = {}
    if profile:
        ppath = sync.SECTION_STATUS_MAP_FILE.parent / "section_status_profiles" / f"{profile}.json"
        if ppath.exists():
            profile_entries = json.loads(ppath.read_text(encoding="utf-8"))
    return profile, own, profile_entries


def check_project(project_key: str, asana_name: str, asana_link: str, state: dict,
                  global_map: dict, user_map: dict, not_in_jira: dict) -> dict:
    result = {"jira_key": project_key, "asana_name": asana_name, "asana_link": asana_link,
              "checked_at": time.time(), "errors": [], "warnings": []}
    gid = core.extract_asana_project_gid(asana_link)
    if not gid:
        result["errors"].append("Brak (albo nierozpoznany) link do projektu Asany w arkuszu.")
        return result
    result["asana_gid"] = gid

    log(f"[{project_key}] Asana: sekcje i zadania…")
    sections = asana_get_all(f"{sync.ASANA_API}/projects/{gid}/sections", {"opt_fields": "name"})
    tasks = asana_get_all(f"{sync.ASANA_API}/projects/{gid}/tasks", {"opt_fields": ",".join([
        "name", "completed", "assignee.gid", "assignee.name", "assignee.email",
        "memberships.section.name", "memberships.project.gid", "parent.gid"])})
    log(f"[{project_key}]   {len(sections)} sekcji, {len(tasks)} zadań")

    log(f"[{project_key}] Jira: statusy projektu, zgłoszenia, osoby…")
    statuses = jira_get(f"/rest/api/3/project/{project_key}/statuses")
    status_names = sorted({s["name"] for it in statuses for s in it.get("statuses", [])}, key=str.lower)
    status_lower = {s.lower() for s in status_names}
    issues = jira_issues(project_key)
    log(f"[{project_key}]   {len(status_names)} statusów, {len(issues)} zgłoszeń")
    result["jira_statuses"] = status_names

    sync._PROJECT_OVERRIDES.pop(project_key, None)  # świeży odczyt mapy projektu
    try:
        overrides = sync.project_section_overrides(project_key)
    except RuntimeError as exc:
        result["errors"].append(str(exc))
        overrides = {}
    profile, own, profile_entries = map_sources(project_key)
    result["map"] = {"profile": profile, "project_file": bool(own) or profile is not None}
    done_name = overrides.get(sync.COMPLETED_KEY) or global_map.get(sync.COMPLETED_KEY) or "Fertig"
    result["done_status"] = {"name": done_name, "exists": done_name.lower() in status_lower,
                             "source": "projekt" if sync.COMPLETED_KEY in own else
                             (f"szablon {profile}" if sync.COMPLETED_KEY in profile_entries else
                              "główna" if sync.COMPLETED_KEY in global_map else "domyślny")}

    # ---------------------------------------------------------------- sekcje
    by_section = defaultdict(lambda: {"open": 0, "completed": 0})
    task_section = {}
    for t in tasks:
        sec = sync.get_task_section_name(t, gid)
        task_section[t["gid"]] = sec
        by_section[sec or "(no section)"]["completed" if t.get("completed") else "open"] += 1
    ordered = [s["name"].strip() for s in sections]
    ordered += [s for s in by_section if s not in ordered]
    section_rows = []
    for name in ordered:
        rule = sync.section_rule(project_key, name, global_map)
        if name in own:
            source = "projekt"
        elif name in profile_entries:
            source = f"szablon {profile}"
        elif name in global_map:
            source = "główna"
        else:
            source = None
        status = rule["status"]
        section_rows.append({
            "name": name, **by_section.get(name, {"open": 0, "completed": 0}),
            "status": status, "source": source, "skip": rule["skip"], "labels": rule["labels"],
            "components": rule["components"], "resolution": rule["resolution"],
            "over_completed": rule["over_completed"],
            "status_exists": (status.lower() in status_lower) if status else None,
        })
    result["sections"] = section_rows

    # ---------------------------------------------------------------- osoby
    people = {}
    for t in tasks:
        a = t.get("assignee") or {}
        if not a.get("gid"):
            continue
        p = people.setdefault(a["gid"], {"gid": a["gid"], "name": a.get("name"), "email": a.get("email"),
                                         "tasks": 0, "open": 0})
        p["tasks"] += 1
        p["open"] += 0 if t.get("completed") else 1
    unassigned = sum(1 for t in tasks if not (t.get("assignee") or {}).get("gid"))
    mapped_ids = [user_map.get(g) for g in people]
    users = jira_users(mapped_ids)
    assignable = assignable_ids(project_key)
    if assignable is None:
        result["warnings"].append("Nie udało się pobrać listy osób, które można przypisać w projekcie.")
    lead_info = jira_get(f"/rest/api/3/project/{project_key}").get("lead") or {}
    lead = lead_info.get("accountId")
    result["project_lead"] = lead_info.get("displayName") or lead
    for g, p in people.items():
        acc = user_map.get(g)
        p["jira_account_id"] = acc
        p["jira_name"] = (users.get(acc) or {}).get("name") if acc else None
        p["jira_active"] = (users.get(acc) or {}).get("active") if acc else None
        p["assignable"] = (acc in assignable) if (acc and assignable is not None) else None
        p["not_in_jira"] = g in not_in_jira
        if p["not_in_jira"] and acc:
            p["problem"] = "oznaczona „nie w Jirze”, ale jest w user_map.json"
        elif p["not_in_jira"]:
            p["problem"] = None
        elif not acc:
            p["problem"] = "brak w user_map.json"
        elif acc not in users:
            p["problem"] = "konto Jiry nie znalezione"
        elif not p["jira_active"]:
            p["problem"] = "konto Jiry nieaktywne"
        elif p["assignable"] is False:
            # Mapowanie jest poprawne - Jira nie pozwala przypisać tego konta: żadna jego rola
            # w projekcie nie ma "Assignable User" w schemacie uprawnień (lead też go nie daje).
            p["problem"] = "zmapowany, ale bez uprawnienia do przypisania w projekcie" + \
                (" (jest leadem projektu)" if acc == lead else "")
        else:
            p["problem"] = None
    result["people"] = sorted(people.values(), key=lambda p: (p["problem"] is None, p["not_in_jira"], -p["tasks"]))
    if any(p["assignable"] is False for p in people.values()):
        result["assignable_permission"] = assignable_holders(project_key)
    result["unassigned_tasks"] = unassigned

    # ---------------------------------------------------------------- zgodność
    project_state = state.get(project_key, {}) if isinstance(state.get(project_key), dict) else {}
    mismatches = []
    counts = {"tasks": len(tasks), "migrated": 0, "not_migrated": 0, "jira_master": 0, "skipped": 0,
              "missing_in_jira": 0, "status_ok": 0, "status_bad": 0, "status_unmapped": 0,
              "assignee_ok": 0, "assignee_default": 0, "assignee_not_in_jira": 0, "assignee_unassignable_empty": 0, "assignee_kept": 0, "assignee_bad": 0}
    for t in tasks:
        entry = project_state.get(t["gid"])
        sec = task_section[t["gid"]]
        rule = sync.section_rule(project_key, sec, global_map)
        if sync.is_jira_master(t.get("name"), entry):
            counts["jira_master"] += 1
            continue
        if rule["skip"]:
            counts["skipped"] += 1
            continue
        if not entry or not entry.get("jira_key"):
            counts["not_migrated"] += 1
            continue
        counts["migrated"] += 1
        key = entry["jira_key"]
        issue = issues.get(key)
        row = {"jira_key": key, "asana_gid": t["gid"], "title": t.get("name"), "section": sec,
               "completed": bool(t.get("completed"))}
        if issue is None:
            counts["missing_in_jira"] += 1
            mismatches.append({**row, "kind": "brak w Jirze"})
            continue

        completed = bool(t.get("completed")) and not rule["over_completed"]
        if completed and done_name.lower() in status_lower:
            expected = done_name
        else:
            expected = rule["status"]
        if not expected:
            counts["status_unmapped"] += 1
        elif (issue["status"] or "").lower() == expected.lower():
            counts["status_ok"] += 1
        else:
            counts["status_bad"] += 1
            mismatches.append({**row, "kind": "status", "expected": expected, "actual": issue["status"]})

        a_gid = (t.get("assignee") or {}).get("gid")
        want = user_map.get(a_gid) if a_gid else None
        if want == issue["assignee"]:
            counts["assignee_ok"] += 1
        elif a_gid in not_in_jira and not want:
            # Osoba świadomie bez konta w Jirze: zadanie dostaje leada albo przejmuje je ktoś inny.
            counts["assignee_not_in_jira"] += 1
        elif not a_gid and lead and issue["assignee"] == lead:
            # Bez osoby w Asanie Jira przypisuje domyślnie leada projektu - to nie jest różnica.
            counts["assignee_default"] += 1
        elif a_gid and people[a_gid]["assignable"] is False:
            # Osoby nie da się przypisać: nowe zadanie powstaje bez przypisanego, a w już
            # utworzonych zostaje to, co jest (np. lead wstawiony zastępczo) - ustalenie z PM.
            counts["assignee_unassignable_empty" if not issue["assignee"] else "assignee_kept"] += 1
        elif a_gid and people[a_gid]["problem"]:
            # Zadanie ma kogoś innego - zwykle leada wstawionego zastępczo przez starszą
            # wersję synchronizacji (przed zmianą na "bez przypisanego").
            counts["assignee_bad"] += 1
            mismatches.append({**row, "kind": "brak uprawnienia w Jirze" if people[a_gid]["assignable"] is False else "problem z mapowaniem",
                               "expected": "(nieprzypisany) — " + f"{(t['assignee'] or {}).get('name') or a_gid}: {people[a_gid]['problem']}"
                               if people[a_gid]["assignable"] is False else
                               f"{(t['assignee'] or {}).get('name') or a_gid} — {people[a_gid]['problem']}",
                               "actual": issue["assignee_name"]})
        elif a_gid and not want:
            counts["assignee_bad"] += 1
            mismatches.append({**row, "kind": "przypisany", "expected": f"(niezmapowany) {(t['assignee'] or {}).get('name') or a_gid}",
                               "actual": issue["assignee_name"]})
        else:
            counts["assignee_bad"] += 1
            mismatches.append({**row, "kind": "przypisany",
                               "expected": (users.get(want) or {}).get("name") or want or "(nieprzypisany)",
                               "actual": issue["assignee_name"] or "(nieprzypisany)"})
    result["counts"] = counts
    result["mismatches"] = mismatches

    unmapped = [s for s in section_rows if not s["status"] and not s["skip"] and (s["open"] or s["completed"])]
    missing = [s for s in section_rows if s["status"] and s["status_exists"] is False]
    if unmapped:
        result["warnings"].append(f"{len(unmapped)} sekcji z zadaniami bez wpisu w mapie (zadania zostaną w statusie startowym).")
    if missing:
        result["warnings"].append(f"{len(missing)} sekcji mapowanych na status, którego nie ma w projekcie Jiry.")
    if not result["done_status"]["exists"]:
        result["warnings"].append(f"Status dla ukończonych „{done_name}” nie istnieje w projekcie.")
    bad_people = [p for p in result["people"] if p["problem"]]
    if bad_people:
        result["warnings"].append(f"{len(bad_people)} osób z Asany nie zostanie poprawnie przypisanych.")
    log(f"[{project_key}] Gotowe: {counts['status_bad']} różnic statusu, {counts['assignee_bad']} różnic przypisania, "
        f"{len(result['warnings'])} ostrzeżeń")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Sprawdzenie statusów i przypisań Asana ↔ Jira (tylko odczyt).")
    parser.add_argument("project_keys", help="Klucze projektów Jiry po przecinku")
    parser.add_argument("--out-dir", default=str(Path(__file__).resolve().parent / "checks"))
    parser.add_argument("--fresh-sheet", action="store_true",
                        help="Czytaj arkusz od nowa zamiast z cache panelu (migration_app/cache/sheet.json).")
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    wanted = [k.strip().upper() for k in args.project_keys.split(",") if k.strip()]
    rows = {}
    sheet_cache = Path(__file__).resolve().parent / "cache" / "sheet.json"
    if sheet_cache.exists() and not args.fresh_sheet:
        data = json.loads(sheet_cache.read_text(encoding="utf-8"))
        log(f"Arkusz Google z cache panelu (wczytany {time.strftime('%Y-%m-%d %H:%M', time.localtime(data['loaded_at']))})…")
        for r in data["rows"]:
            if r["jira_key"]:
                rows[r["jira_key"]] = core.SheetRow(project_name=r["name"], jira_key=r["jira_key"],
                                                    asana_project_link=r["asana_link"])
    else:
        log("Arkusz Google (może potrwać kilka minut)…")
        cache = core.load_key_cache()
        for row in core.get_sheet_rows():
            key = (row.jira_key or cache.get(core.normalize(row.project_name)) or "").upper()
            if key:
                rows[key] = row
    log("Stan synchronizacji, mapy, user_map…")
    state = json.loads(sync.STATE_FILE.read_text(encoding="utf-8")) if sync.STATE_FILE.exists() else {}
    global_map = sync.load_section_status_map()
    user_map = sync.load_user_map()
    not_in_jira_file = ROOT / "asana_users_not_in_jira.json"
    not_in_jira = json.loads(not_in_jira_file.read_text(encoding="utf-8")) if not_in_jira_file.exists() else {}

    failed = 0
    for key in wanted:
        row = rows.get(key)
        try:
            if row is None:
                res = {"jira_key": key, "checked_at": time.time(),
                       "errors": ["Projekt nie występuje w arkuszu (albo jest w protected_projects.txt)."],
                       "warnings": []}
            else:
                res = check_project(key, row.project_name, row.asana_project_link, state, global_map, user_map, not_in_jira)
        except Exception as exc:  # noqa: BLE001
            res = {"jira_key": key, "checked_at": time.time(), "errors": [f"{type(exc).__name__}: {exc}"],
                   "warnings": []}
        if res["errors"]:
            failed += 1
            for e in res["errors"]:
                log(f"[{key}] BŁĄD: {e}")
        (out_dir / f"{key}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    sys.exit(1 if failed == len(wanted) else (2 if failed else 0))


if __name__ == "__main__":
    main()
