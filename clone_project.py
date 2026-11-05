#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
clone_project.py — kopia projektu Jiry (company-managed) z WŁASNYMI kopiami schematów.

Samodzielny: nie importuje innych skryptów tego pakietu i nie korzysta z projektu
wzorcowego ani innych ustawień z .env - czyta z .env tylko JIRA_BASE_URL,
JIRA_EMAIL i JIRA_API_TOKEN. Wszystko inne bierze z projektu ŹRÓDŁOWEGO.

Co robi, w kolejności:
  1. Kopiuje KAŻDY schemat źródła pod nazwą opartą na nowym projekcie,
     np. "XYZ Permission Scheme" (--name-base: klucz albo nazwa nowego projektu):
       uprawnienia, powiadomienia, bezpieczeństwo zgłoszeń, workflow, typy zadań,
       ekrany typów zadań, field scheme (albo schemat konfiguracji pól w instancjach
       sprzed Field Schemes), priorytety. Uprawnień, których Jira nie przyjmuje przy
       tworzeniu schematu (np. Product Discovery, COPY_PROJECT), nie da się skopiować.
     Schemat o takiej nazwie, który już istnieje (np. z przerwanego uruchomienia),
     jest używany ponownie zamiast tworzenia drugiego.
  2. Tworzy projekt (typ, lead, URL, kategoria, domyślne przypisanie jak w źródle;
     opis tylko podany w --description) od razu ze schematami uprawnień, powiadomień
     i bezpieczeństwa, a potem podpina kopie pozostałych schematów.
  3. Kopiuje komponenty, wersje i konteksty pól niestandardowych ograniczone do
     projektu źródłowego (nowy projekt jest do nich dopisywany). Do ról dodaje osoby
     i grupy z --roles-file (wybór w panelu) - albo, z --roles-from-source, jak w źródle.
  4. Kopiuje tablice: filtr (JQL z podmienionym projektem, te same udostępnienia),
     tablicę i układ kolumn z przypisaniem statusów (API GreenHopper, tokenem API -
     bez przeglądarki).

Współdzielone ze źródłem (nie są schematami, kopie zmieniałyby konfigurację obu
projektów niezależnie od siebie tylko przy ich edycji): same workflow, ekrany,
schematy ekranów i konfiguracje pól - nowe schematy wskazują na te same obiekty.
Nie kopiuje zgłoszeń, torów (swimlanes), szybkich filtrów ani wyglądu kart tablic.

Użycie:
  python clone_project.py --source ABC --target-key XYZ --target-name "Nowy projekt" --dry-run
  python clone_project.py --source ABC --target-key XYZ --target-name "Nowy projekt"
  python clone_project.py --source ABC --target-key XYZ --target-name "Nowy projekt" --name-base name
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import dotenv_values

ENV = dotenv_values(Path(__file__).resolve().parent / ".env")
BASE = (ENV.get("JIRA_BASE_URL") or "").rstrip("/")
AUTH = (ENV.get("JIRA_EMAIL") or "", ENV.get("JIRA_API_TOKEN") or "")
HEADERS = {"Accept": "application/json", "X-Atlassian-Token": "no-check"}
TIMEOUT = 60
DRY = False
WARNINGS: list = []

LABELS = {
    "permission": "Permission Scheme",
    "notification": "Notification Scheme",
    "security": "Issue Security Scheme",
    "workflow": "Workflow Scheme",
    "issuetype": "Issue Type Scheme",
    "screen": "Issue Type Screen Scheme",
    "fieldconfig": "Field Configuration Scheme",
    "fieldscheme": "Field Scheme",
    "priority": "Priority Scheme",
}
ADDON_ROLE = "atlassian-addons-project-access"  # zarządzana przez Jirę (aplikacje)


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

def call(method: str, path: str, ok_404: bool = False, **kw):
    """Zapytanie do Jiry z ponawianiem 429/5xx. Zwraca JSON (albo None przy 204/404)."""
    url = path if path.startswith("http") else f"{BASE}{path}"
    for attempt in range(5):
        r = requests.request(method, url, auth=AUTH, headers=HEADERS, timeout=TIMEOUT, **kw)
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 4:
            time.sleep(float(r.headers.get("Retry-After") or 2 ** (attempt + 1)))
            continue
        if r.status_code == 404 and ok_404:
            return None
        if r.status_code >= 300:
            raise RuntimeError(f"{method} {path}: {r.status_code} {r.text[:400]}")
        return r.json() if r.content and "json" in r.headers.get("content-type", "") else None
    return None


def get(path, **params):
    return call("GET", path, params=params)


def paged(path, key="values", **params):
    start = 0
    while True:
        d = get(path, startAt=start, maxResults=50, **params)
        vals = d.get(key) or []
        yield from vals
        if d.get("isLast", True) or not vals or len(vals) < 1:
            return
        start += len(vals)


def write(method, path, label, **kw):
    """Zapis do Jiry - w trybie --dry-run tylko komunikat."""
    if DRY:
        print(f"   [DRY-RUN] {label}")
        return None
    return call(method, path, **kw)


def warn(msg):
    WARNINGS.append(msg)
    print(f"   [OSTRZEŻENIE] {msg}")


# --------------------------------------------------------------------------- #
# Schematy źródła
# --------------------------------------------------------------------------- #

def association(endpoint: str, key: str, project_id: str):
    d = get(f"/rest/api/3/{endpoint}", projectId=project_id)
    v = (d.get("values") or [{}])[0]
    return (v.get(key) or {}) or None


def source_schemes(src_key: str, src_id: str) -> dict:
    out = {}
    for kind, path in (("permission", "permissionscheme"), ("notification", "notificationscheme"),
                       ("security", "issuesecuritylevelscheme")):
        out[kind] = call("GET", f"/rest/api/3/project/{src_key}/{path}", ok_404=True)
    out["workflow"] = association("workflowscheme/project", "workflowScheme", src_id)
    out["issuetype"] = association("issuetypescheme/project", "issueTypeScheme", src_id)
    out["screen"] = association("issuetypescreenscheme/project", "issueTypeScreenScheme", src_id)
    # Instancje po migracji na „Field Schemes” nie pozwalają tworzyć schematów konfiguracji pól -
    # wtedy kopiowany jest field scheme projektu.
    fs = call("GET", "/rest/api/3/config/fieldschemes/projects", ok_404=True, params={"projectId": src_id})
    fs_id = next((v.get("schemeId") for v in (fs or {}).get("values", [])), None)
    if fs_id is not None:
        out["fieldscheme"] = get(f"/rest/api/3/config/fieldschemes/{fs_id}")
        out["fieldconfig"] = None
    else:
        out["fieldconfig"] = association("fieldconfigurationscheme/project", "fieldConfigurationScheme", src_id)
    out["priority"] = next((s for s in paged("/rest/api/3/priorityscheme", expand="projects,priorities")
                            if any(str(p.get("id")) == str(src_id) for p in (s.get("projects") or {}).get("values", []))), None)
    return out


def existing_by_name(kind: str, name: str):
    """ID istniejącego schematu o tej nazwie (ponowne uruchomienie) albo None."""
    if kind == "permission":
        items = get("/rest/api/3/permissionscheme").get("permissionSchemes", [])
    elif kind == "security":
        items = get("/rest/api/3/issuesecurityschemes").get("issueSecuritySchemes", [])
    else:
        path = {"notification": "/rest/api/3/notificationscheme", "workflow": "/rest/api/3/workflowscheme",
                "fieldscheme": "/rest/api/3/config/fieldschemes",
                "issuetype": "/rest/api/3/issuetypescheme", "screen": "/rest/api/3/issuetypescreenscheme",
                "fieldconfig": "/rest/api/3/fieldconfigurationscheme", "priority": "/rest/api/3/priorityscheme"}[kind]
        items = paged(path)
    return next((str(s["id"]) for s in items if s.get("name") == name), None)


def holder_body(h: dict) -> dict:
    """Odbiorca uprawnienia/poziomu w formacie POST. "value" (np. groupId) ma pierwszeństwo
    przed "parameter" (dla grup to nazwa, którą Atlassian wycofuje)."""
    out = {"type": h["type"]}
    if h.get("value") is not None:
        out["value"] = h["value"]
    elif h.get("parameter") is not None:
        out["parameter"] = h["parameter"]
    return out


def build_copy(kind: str, src: dict, name: str):
    """(treść POST, funkcja dokończenia po utworzeniu albo None) dla kopii schematu."""
    sid = str(src["id"]) if src and src.get("id") is not None else None
    desc = f"Kopia „{src.get('name')}” dla projektu" if src else "Utworzony dla projektu"
    if kind == "permission":
        d = get(f"/rest/api/3/permissionscheme/{sid}", expand="permissions")
        perms = [{"holder": holder_body(p["holder"]), "permission": p["permission"]} for p in d.get("permissions", [])]
        return "/rest/api/3/permissionscheme", {"name": name, "description": desc, "permissions": perms}, None
    if kind == "notification":
        d = get(f"/rest/api/3/notificationscheme/{sid}", expand="all")
        events = []
        for ev in d.get("notificationSchemeEvents", []):
            notes = [{k: n[k] for k in ("notificationType", "parameter") if n.get(k) is not None}
                     for n in ev.get("notifications", [])]
            if notes:
                events.append({"event": {"id": str(ev["event"]["id"])}, "notifications": notes})
        return "/rest/api/3/notificationscheme", {"name": name, "description": desc, "notificationSchemeEvents": events}, None
    if kind == "security":
        d = get(f"/rest/api/3/issuesecurityschemes/{sid}")
        members = list(paged("/rest/api/3/issuesecurityschemes/level/member", schemeId=sid))
        levels = []
        for lv in d.get("levels", []):
            ms = [holder_body(m["holder"]) for m in members if str(m.get("issueSecurityLevelId")) == str(lv["id"])]
            levels.append({"name": lv["name"], "description": lv.get("description") or "",
                           "isDefault": str(d.get("defaultSecurityLevelId")) == str(lv["id"]), "members": ms})
        return "/rest/api/3/issuesecurityschemes", {"name": name, "description": desc, "levels": levels}, None
    if kind == "workflow":
        d = get(f"/rest/api/3/workflowscheme/{sid}")
        body = {"name": name, "description": desc, "defaultWorkflow": d.get("defaultWorkflow"),
                "issueTypeMappings": d.get("issueTypeMappings") or {}}
        return "/rest/api/3/workflowscheme", body, None
    if kind == "issuetype":
        d = next(iter(get("/rest/api/3/issuetypescheme", id=sid).get("values", [])), {})
        ids = [m["issueTypeId"] for m in paged("/rest/api/3/issuetypescheme/mapping", issueTypeSchemeId=sid)]
        body = {"name": name, "description": desc, "issueTypeIds": ids}
        if d.get("defaultIssueTypeId"):
            body["defaultIssueTypeId"] = d["defaultIssueTypeId"]
        return "/rest/api/3/issuetypescheme", body, None
    if kind == "screen":
        maps = [{"issueTypeId": m["issueTypeId"], "screenSchemeId": str(m["screenSchemeId"])}
                for m in paged("/rest/api/3/issuetypescreenscheme/mapping", issueTypeScreenSchemeId=sid)]
        return "/rest/api/3/issuetypescreenscheme", {"name": name, "description": desc, "issueTypeMappings": maps}, None
    if kind == "fieldconfig":
        if sid:
            maps = [{"issueTypeId": m["issueTypeId"], "fieldConfigurationId": str(m["fieldConfigurationId"])}
                    for m in paged("/rest/api/3/fieldconfigurationscheme/mapping", fieldConfigurationSchemeId=sid)]
        else:  # źródło na domyślnej konfiguracji pól - kopia mapuje wszystko na nią
            default = next((c for c in paged("/rest/api/3/fieldconfiguration", isDefault="true")), None)
            maps = [{"issueTypeId": "default", "fieldConfigurationId": str(default["id"])}] if default else []

        def finish(new_id):
            if maps:
                call("PUT", f"/rest/api/3/fieldconfigurationscheme/{new_id}/mapping", json={"mappings": maps})
        return "/rest/api/3/fieldconfigurationscheme", {"name": name, "description": desc}, finish
    if kind == "fieldscheme":
        return f"/rest/api/3/config/fieldschemes/{sid}/clone", {"name": name, "description": desc}, None
    if kind == "priority":
        d = src  # Jira nie udostępnia odczytu pojedynczego schematu (405) - dane z listy (expand=priorities)
        prios = [str(p["id"]) for p in (d.get("priorities") or {}).get("values", [])]
        body = {"name": name, "description": desc, "defaultPriorityId": int(d.get("defaultPriorityId")),
                "priorityIds": [int(p) for p in prios]}
        return "/rest/api/3/priorityscheme", body, None
    raise ValueError(kind)


def post_scheme(kind: str, path: str, body: dict):
    """POST kopii. Schemat uprawnień: Jira zwraca przy odczycie uprawnienia, których nie przyjmuje
    przy tworzeniu (np. COPY_PROJECT) - takie są usuwane po kolei z komunikatem."""
    skipped = []
    while True:
        try:
            res = call("POST", path, json=body)
            if skipped:
                warn(f"{LABELS[kind]}: pominięto uprawnienia, których Jira nie przyjmuje przy tworzeniu: {', '.join(skipped)}")
            return res
        except RuntimeError as exc:
            m = re.search(r"Unrecognized permission: ([A-Z_]+)", str(exc))
            if kind != "permission" or not m or len(skipped) > 50:
                raise
            skipped.append(m.group(1))
            body["permissions"] = [p for p in body["permissions"] if p["permission"] != m.group(1)]


def copy_schemes(srcs: dict, base: str) -> dict:
    """{rodzaj: id nowego schematu} - tworzy kopie albo używa istniejących o tej nazwie."""
    new = {}
    for kind, label in LABELS.items():
        src = srcs.get(kind)
        if kind == "fieldconfig" and srcs.get("fieldscheme"):
            continue  # zastąpiony przez field scheme
        if not src and kind != "fieldconfig":
            print(f"   {label}: źródło go nie używa - pomijam.")
            continue
        name = f"{base} {label}"
        found = existing_by_name(kind, name)
        if found:
            print(f"   {label}: „{name}” już istnieje (id {found}) - używam go.")
            new[kind] = found
            continue
        try:
            path, body, finish = build_copy(kind, src, name)
        except Exception as exc:  # noqa: BLE001
            warn(f"{label}: nie udało się odczytać schematu źródła „{(src or {}).get('name')}”: {exc}")
            continue
        what = f"{label}: „{name}” jako kopia „{(src or {}).get('name') or 'domyślnej konfiguracji'}”"
        if DRY:
            print(f"   [DRY-RUN] {what}")
            new[kind] = f"(nowy: {name})"
            continue
        try:
            res = post_scheme(kind, path, body)
            new_id = str(res.get("id") or res.get("issueTypeSchemeId") or res.get("issueTypeScreenSchemeId"))
            if finish:
                finish(new_id)
            new[kind] = new_id
            print(f"   {what} (id {new_id})")
        except Exception as exc:  # noqa: BLE001
            warn(f"{label}: tworzenie kopii nie powiodło się: {exc}")
    return new


# --------------------------------------------------------------------------- #
# Projekt
# --------------------------------------------------------------------------- #

def create_project(src: dict, key: str, name: str, lead: str, schemes: dict, description: str) -> str:
    body = {"key": key, "name": name, "projectTypeKey": src["projectTypeKey"],
            "leadAccountId": lead, "assigneeType": src.get("assigneeType") or "UNASSIGNED"}
    if description:  # opis tylko podany wprost - opisu źródła nie kopiujemy
        body["description"] = description
    if src.get("url"):
        body["url"] = src["url"]
    if (src.get("projectCategory") or {}).get("id"):
        body["categoryId"] = int(src["projectCategory"]["id"])
    for kind, field in (("permission", "permissionScheme"), ("notification", "notificationScheme"),
                        ("security", "issueSecurityScheme")):
        if schemes.get(kind):
            body[field] = schemes[kind] if DRY else int(schemes[kind])
    res = write("POST", "/rest/api/3/project", f"Projekt {key} „{name}” (typ {body['projectTypeKey']}, lead {lead})", json=body)
    if res:
        print(f"   Utworzono projekt {key} (id {res['id']}).")
        return str(res["id"])
    return "(nowy)"


def assign_schemes(project_id: str, schemes: dict, existing_target: bool) -> None:
    pairs = [("issuetype", "issuetypescheme/project", "issueTypeSchemeId"),
             ("workflow", "workflowscheme/project", "workflowSchemeId"),
             ("screen", "issuetypescreenscheme/project", "issueTypeScreenSchemeId"),
             ("fieldconfig", "fieldconfigurationscheme/project", "fieldConfigurationSchemeId")]
    if existing_target:  # projekt istniał - uprawnienia i powiadomienia przez aktualizację projektu
        body = {field: (int(schemes[kind]) if not DRY else 0)
                for kind, field in (("permission", "permissionScheme"), ("notification", "notificationScheme"))
                if schemes.get(kind)}
        if body:
            try:
                write("PUT", f"/rest/api/3/project/{project_id}", "podpięcie schematów uprawnień i powiadomień", json=body)
                if not DRY:
                    print(f"   Podpięto {', '.join(LABELS[k] for k in ('permission', 'notification') if schemes.get(k))}.")
            except Exception as exc:  # noqa: BLE001
                warn(f"podpięcie schematów uprawnień/powiadomień: {exc}")
        if schemes.get("security"):
            warn("projekt już istniał - schemat bezpieczeństwa podepnij ręcznie (Jira robi to zadaniem asynchronicznym).")
    for kind, endpoint, field in pairs:
        if not schemes.get(kind):
            continue
        try:
            write("PUT", f"/rest/api/3/{endpoint}", f"podpięcie {LABELS[kind]}",
                  json={field: schemes[kind], "projectId": project_id})
            if not DRY:
                print(f"   Podpięto {LABELS[kind]} (id {schemes[kind]}).")
        except Exception as exc:  # noqa: BLE001
            warn(f"podpięcie {LABELS[kind]}: {exc}")
    if schemes.get("fieldscheme"):
        try:
            write("PUT", "/rest/api/3/config/fieldschemes/projects", f"podpięcie {LABELS['fieldscheme']}",
                  json={str(schemes["fieldscheme"]): {"projectIds": [int(project_id) if not DRY else 0]}})
            if not DRY:
                print(f"   Podpięto {LABELS['fieldscheme']} (id {schemes['fieldscheme']}).")
        except Exception as exc:  # noqa: BLE001
            warn(f"podpięcie {LABELS['fieldscheme']}: {exc}")
    if schemes.get("priority"):
        try:
            write("PUT", f"/rest/api/3/priorityscheme/{schemes['priority']}", f"podpięcie {LABELS['priority']}",
                  json={"projects": {"add": {"ids": [int(project_id) if not DRY else 0]}},
                        "mappings": {"in": {} if DRY else priority_mapping(project_id, schemes["priority"]), "out": {}}})
            if not DRY:
                print(f"   Podpięto {LABELS['priority']} (id {schemes['priority']}).")
        except Exception as exc:  # noqa: BLE001
            warn(f"podpięcie {LABELS['priority']}: {exc}")


def priority_mapping(project_id: str, new_scheme: str) -> dict:
    """Mapowanie priorytetów obecnego schematu projektu, których nie ma w nowym, na domyślny nowego
    (Jira wymaga go przy zmianie schematu priorytetów projektu)."""
    schemes = list(paged("/rest/api/3/priorityscheme", expand="projects,priorities"))
    new = next(s for s in schemes if str(s["id"]) == str(new_scheme))
    new_ids = {str(p["id"]) for p in (new.get("priorities") or {}).get("values", [])}
    cur = next((s for s in schemes if any(str(p.get("id")) == str(project_id)
                                          for p in (s.get("projects") or {}).get("values", []))), None)
    if cur is None:
        cur = next((s for s in schemes if s.get("isDefault")), None)
    cur_ids = {str(p["id"]) for p in ((cur or {}).get("priorities") or {}).get("values", [])}
    return {pid: int(new["defaultPriorityId"]) for pid in sorted(cur_ids - new_ids)}


def copy_components(src_key: str, dst_key: str) -> None:
    src = get(f"/rest/api/3/project/{src_key}/components")
    have = set() if DRY else {c["name"] for c in get(f"/rest/api/3/project/{dst_key}/components")}
    n = 0
    for c in src:
        if c["name"] in have:
            continue
        body = {"project": dst_key, "name": c["name"], **{k: c[k] for k in ("description", "assigneeType") if c.get(k)}}
        if (c.get("lead") or {}).get("accountId"):
            body["leadAccountId"] = c["lead"]["accountId"]
        if DRY:
            n += 1
            continue
        try:
            call("POST", "/rest/api/3/component", json=body)
        except RuntimeError:
            body.pop("leadAccountId", None)  # lead bez dostępu do projektu
            try:
                call("POST", "/rest/api/3/component", json=body)
            except RuntimeError as exc:
                warn(f"komponent „{c['name']}”: {exc}")
                continue
        n += 1
    print(f"   Komponenty: {'do utworzenia' if DRY else 'utworzono'} {n} z {len(src)}.")


def copy_versions(src_key: str, dst_key: str, dst_id: str) -> None:
    src = list(paged(f"/rest/api/3/project/{src_key}/version", orderBy="sequence"))
    have = set() if DRY else {v["name"] for v in paged(f"/rest/api/3/project/{dst_key}/version")}
    n = 0
    for v in src:
        if v["name"] in have:
            continue
        body = {"projectId": int(dst_id) if not DRY else 0, "name": v["name"],
                **{k: v[k] for k in ("description", "released", "archived", "startDate", "releaseDate") if k in v}}
        if not DRY:
            try:
                call("POST", "/rest/api/3/version", json=body)
            except RuntimeError as exc:
                warn(f"wersja „{v['name']}”: {exc}")
                continue
        n += 1
    print(f"   Wersje: {'do utworzenia' if DRY else 'utworzono'} {n} z {len(src)}.")


def source_role_selection(src_key: str) -> list:
    """Wszystkie role źródła z ich osobami i grupami (gdy nie podano --roles-file)."""
    out = []
    for name, url in sorted(get(f"/rest/api/3/project/{src_key}/role").items()):
        if name == ADDON_ROLE:
            continue
        role_id = url.rstrip("/").rsplit("/", 1)[-1]
        actors = get(f"/rest/api/3/project/{src_key}/role/{role_id}").get("actors", [])
        out.append({"id": role_id, "name": name,
                    "users": [a["actorUser"]["accountId"] for a in actors if (a.get("actorUser") or {}).get("accountId")],
                    "groups": [a["actorGroup"]["groupId"] for a in actors if (a.get("actorGroup") or {}).get("groupId")]})
    return out


def apply_roles(dst_key: str, selection: list) -> None:
    """Dodaje do ról nowego projektu podane osoby (accountId) i grupy (groupId)."""
    for role in selection:
        name, role_id = role.get("name") or role["id"], str(role["id"])
        users, groups = list(role.get("users") or []), list(role.get("groups") or [])
        if not users and not groups:
            continue
        if DRY:
            print(f"   [DRY-RUN] Rola „{name}”: osoby {len(users)}, grupy {len(groups)}")
            continue
        added, failed = 0, []
        for field, ids in (("user", users), ("groupId", groups)):
            for one in ids:  # pojedynczo - nieaktywne konto nie blokuje reszty
                try:
                    call("POST", f"/rest/api/3/project/{dst_key}/role/{role_id}", json={field: [one]})
                    added += 1
                except RuntimeError as exc:
                    if "already" in str(exc).lower():
                        added += 1
                    else:
                        failed.append(one)
        print(f"   Rola „{name}”: {added} z {len(users) + len(groups)}" + (f"; nie dodano: {', '.join(failed[:5])}" if failed else ""))


def copy_field_contexts(src_id: str, dst_id: str) -> None:
    """Pola niestandardowe z kontekstem ograniczonym do projektu źródłowego: dopisuje nowy projekt."""
    found = []
    fields = list(paged("/rest/api/3/field/search", type="custom"))
    for n, f in enumerate(fields, 1):
        if n % 200 == 0:
            print(f"   ...sprawdzono {n}/{len(fields)} pól", flush=True)
        for m in paged(f"/rest/api/3/field/{f['id']}/context/projectmapping"):
            if not m.get("isGlobalContext") and str(m.get("projectId")) == str(src_id):
                found.append((f["id"], f.get("name", f["id"]), str(m["contextId"])))
    if DRY:
        print(f"   [DRY-RUN] Kontekstów pól ograniczonych do źródła: {len(found)}"
              + (": " + ", ".join(sorted({x[1] for x in found})[:12]) if found else ""))
        return
    added = 0
    for field_id, name, ctx in found:
        try:
            call("PUT", f"/rest/api/3/field/{field_id}/context/{ctx}/project", json={"projectIds": [dst_id]})
            added += 1
        except RuntimeError as exc:
            warn(f"kontekst pola „{name}”: {exc}")
    print(f"   Pola niestandardowe: dopisano projekt do {added} z {len(found)} kontekstów.")


# --------------------------------------------------------------------------- #
# Tablice
# --------------------------------------------------------------------------- #

def adapt_jql(jql: str, src: dict, dst_key: str) -> str:
    """Podmienia odwołania do projektu źródłowego (klucz, nazwa, ID) na klucz nowego."""
    out = jql
    for ident in (src["key"], src["name"], str(src["id"])):
        out = re.sub(rf'(project\s*(?:=|in\s*\(|!=)\s*[^)]*?)(?<![\w-])"?{re.escape(ident)}"?(?![\w-])',
                     lambda m: m.group(1) + dst_key, out, flags=re.IGNORECASE)
    return out


def map_share(perms: list, src_id: str, dst_id: str) -> list:
    out = []
    for p in perms:
        if p.get("type") == "project-unknown":
            continue
        c = {"type": p["type"]}
        if p.get("project"):
            pid = str(p["project"]["id"])
            c["project"] = {"id": dst_id if pid == str(src_id) else pid}
        if p.get("role"):
            c["role"] = {"id": str(p["role"]["id"])}
        if p.get("group"):
            c["group"] = {"groupId": p["group"]["groupId"]} if p["group"].get("groupId") else {"name": p["group"].get("name")}
        if p.get("user"):
            c["user"] = {"accountId": p["user"]["accountId"]}
        if c not in out:
            out.append(c)
    return out


def rename(text: str, src: dict, dst_key: str, dst_name: str) -> str:
    new = text.replace(src["name"], dst_name)
    new = re.sub(rf'(?<![\w-]){re.escape(src["key"])}(?![\w-])', dst_key, new)
    return new if new != text else f"{dst_key} {text}"


def copy_boards(src: dict, dst_key: str, dst_name: str, dst_id: str, board_ids: list) -> None:
    boards = ([get(f"/rest/agile/1.0/board/{b}") for b in board_ids] if board_ids
              else list(paged("/rest/agile/1.0/board", projectKeyOrId=src["key"])))
    print(f"   Tablic do skopiowania: {len(boards)}" + ("" if boards else " (podaj --board-ids, jeśli tablica nie jest przypisana do projektu)"))
    for b in boards:
        cfg = get(f"/rest/agile/1.0/board/{b['id']}/configuration")
        flt = get(f"/rest/api/3/filter/{cfg['filter']['id']}", expand="sharePermissions,editPermissions")
        board_name = rename(b["name"], src, dst_key, dst_name)
        jql = adapt_jql(flt["jql"], src, dst_key)
        if jql == flt["jql"]:
            warn(f"tablica „{b['name']}”: JQL filtra nie odwołuje się wprost do projektu ({flt['jql'][:120]}) - sprawdź filtr kopii.")
        share = map_share(flt.get("sharePermissions") or [], src["id"], dst_id)
        if not any(p["type"] == "project" and p.get("project", {}).get("id") == dst_id and "role" not in p for p in share):
            share.append({"type": "project", "project": {"id": dst_id}})
        edit = map_share(flt.get("editPermissions") or [], src["id"], dst_id)
        if DRY:
            print(f"   [DRY-RUN] Tablica „{board_name}” ({b['type']}), filtr: {jql}")
            continue
        try:
            f_new = call("POST", "/rest/api/3/filter", json={"name": f"Filtr: {board_name}", "jql": jql,
                                                              "sharePermissions": share, "editPermissions": edit})
            b_new = call("POST", "/rest/agile/1.0/board", json={"name": board_name, "type": b["type"],
                                                                 "filterId": int(f_new["id"]),
                                                                 "location": {"type": "project", "projectKeyOrId": dst_key}})
        except RuntimeError as exc:
            warn(f"tablica „{board_name}”: {exc}")
            continue
        print(f"   Tablica „{board_name}” (id {b_new['id']}), filtr {f_new['id']}.")
        copy_columns(b["id"], b_new["id"])


def copy_columns(src_board: int, dst_board: int) -> None:
    """Układ kolumn i przypisanie statusów - API GreenHopper (to samo, którego używa
    interfejs Jiry), uwierzytelnione tokenem API."""
    try:
        s = get("/rest/greenhopper/1.0/rapidviewconfig/editmodel.json", rapidViewId=src_board)["rapidListConfig"]
        d = get("/rest/greenhopper/1.0/rapidviewconfig/editmodel.json", rapidViewId=dst_board)["rapidListConfig"]
    except Exception as exc:  # noqa: BLE001
        warn(f"kolumny tablicy {dst_board}: odczyt konfiguracji nie powiódł się ({exc})")
        return
    dst_ids = [c.get("id") for c in d.get("mappedColumns", [])]
    strip = lambda st: {k: st[k] for k in ("id", "name", "statusCategory", "isPresentInWorkflow") if k in st}  # noqa: E731
    cols = []
    for i, c in enumerate(s.get("mappedColumns", [])):
        col = {"name": c["name"], "min": c.get("min", ""), "max": c.get("max", ""),
               "mappedStatuses": [strip(st) for st in c.get("mappedStatuses", [])],
               "isKanPlanColumn": c.get("isKanPlanColumn", False)}
        if i < len(dst_ids) and dst_ids[i] is not None:
            col["id"] = dst_ids[i]
        cols.append(col)
    body = {**{k: s[k] for k in ("currentStatisticsField", "statisticsFields", "showDaysInColumn",
                                 "statisticsFieldValidation", "workflow") if k in s},
            "rapidViewId": int(dst_board), "canEdit": True, "mappedColumns": cols,
            "unmappedStatuses": [strip(st) for st in s.get("unmappedStatuses", [])]}
    try:
        call("PUT", "/rest/greenhopper/1.0/rapidviewconfig/columns", json=body)
    except RuntimeError as exc:
        warn(f"kolumny tablicy {dst_board}: zapis nie powiódł się ({exc}). Ustaw je ręcznie albo starszym copy_project.py (przez przeglądarkę).")
        return
    # Weryfikacja: odczyt nowej tablicy i porównanie kolumn oraz przypisanych statusów ze źródłem.
    after = get("/rest/greenhopper/1.0/rapidviewconfig/editmodel.json", rapidViewId=dst_board)["rapidListConfig"]
    want = [(c["name"], sorted(str(st["id"]) for st in c["mappedStatuses"])) for c in cols]
    got = [(c["name"], sorted(str(st["id"]) for st in c.get("mappedStatuses", []))) for c in after.get("mappedColumns", [])]
    if want == got:
        mapped = sum(len(ids) for _, ids in got)
        print(f"      Kolumny i statusy zgodne ze źródłem: {len(got)} kolumn, {mapped} przypisanych statusów.")
        for col_name, _ in got[:20]:
            names = [st["name"] for c in after["mappedColumns"] if c["name"] == col_name for st in c.get("mappedStatuses", [])]
            print(f"        {col_name}: {', '.join(names) or '(brak statusów)'}")
    else:
        diff = [f"{w[0]}: {w[1]} ≠ {g[1] if g else 'brak'}" for w, g in zip(want, got + [None] * len(want)) if w != g]
        warn(f"kolumny tablicy {dst_board} różnią się od źródła po zapisie: {'; '.join(diff[:5])}")


# --------------------------------------------------------------------------- #

def main() -> None:
    global DRY
    ap = argparse.ArgumentParser(description="Kopia projektu Jiry z własnymi kopiami schematów (bez projektu wzorcowego).")
    ap.add_argument("--source", required=True, help="Klucz projektu źródłowego")
    ap.add_argument("--target-key", required=True, help="Klucz nowego projektu")
    ap.add_argument("--target-name", required=True, help="Nazwa nowego projektu")
    ap.add_argument("--lead-account-id", default="", help="Lead nowego projektu (domyślnie lead źródła)")
    ap.add_argument("--description", default="", help="Opis nowego projektu (domyślnie pusty)")
    ap.add_argument("--name-base", choices=["key", "name"], default="key",
                    help="Początek nazw kopii schematów: klucz (XYZ Permission Scheme) albo nazwa nowego projektu")
    ap.add_argument("--existing-target", action="store_true",
                    help="Projekt docelowy już istnieje (np. po przerwanym uruchomieniu) - podepnij kopie i dokończ")
    ap.add_argument("--roles-file", default="",
                    help="JSON: kto z jaką rolą - [{\"id\": ID roli, \"name\": ..., \"users\": [accountId], \"groups\": [groupId]}] "
                         "(panel zapisuje go w karcie „Kopia projektu”). Bez pliku nikt nie jest dodawany do ról")
    ap.add_argument("--roles-from-source", action="store_true", help="Zamiast pliku: role i członkowie jak w projekcie źródłowym")
    ap.add_argument("--no-roles", action="store_true", help="Nie dodawaj nikogo do ról (domyślne bez --roles-file)")
    ap.add_argument("--no-versions", action="store_true", help="Nie kopiuj wersji")
    ap.add_argument("--no-components", action="store_true", help="Nie kopiuj komponentów")
    ap.add_argument("--no-field-contexts", action="store_true", help="Nie dopisuj projektu do kontekstów pól (szybciej)")
    ap.add_argument("--no-boards", action="store_true", help="Nie kopiuj tablic")
    ap.add_argument("--board-ids", default="", help="ID tablic źródła po przecinku (zamiast tablic przypisanych do projektu)")
    ap.add_argument("--dry-run", action="store_true", help="Pokaż plan, nic nie zapisuj")
    a = ap.parse_args()
    DRY = a.dry_run
    if not (BASE and all(AUTH)):
        sys.exit("Brak JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN w .env.")
    key = a.target_key.strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9]{1,9}", key):
        sys.exit(f"Niepoprawny klucz projektu: {key} (2-10 znaków, wielkie litery i cyfry, zaczyna się literą).")

    print(f"==> Projekt źródłowy {a.source}")
    src = get(f"/rest/api/3/project/{a.source}", expand="description,lead,url")
    if src.get("style") == "next-gen":
        sys.exit("Projekt źródłowy jest team-managed - jego konfiguracji nie da się skopiować schematami.")
    print(f"   {src['name']} (typ {src['projectTypeKey']}, lead {(src.get('lead') or {}).get('displayName')})")
    target = call("GET", f"/rest/api/3/project/{key}", ok_404=True)
    if target and not a.existing_target:
        sys.exit(f"Projekt {key} już istnieje. Użyj --existing-target, żeby podpiąć do niego kopie schematów.")
    if not target and a.existing_target:
        sys.exit(f"Projekt {key} nie istnieje - uruchom bez --existing-target.")

    base = key if a.name_base == "key" else a.target_name.strip()
    print(f"\n==> Schematy (nazwy: „{base} …”)")
    srcs = source_schemes(src["key"], src["id"])
    for kind, s in srcs.items():
        print(f"   źródło - {LABELS[kind]}: {(s or {}).get('name') or '(domyślny / brak)'}")
    schemes = copy_schemes(srcs, base)

    print(f"\n==> Projekt {key}")
    lead = a.lead_account_id or (src.get("lead") or {}).get("accountId")
    if target:
        dst_id = str(target["id"])
        print(f"   Istnieje (id {dst_id}) - podpinam kopie schematów.")
    else:
        dst_id = create_project(src, key, a.target_name.strip(), lead, schemes, a.description)
    assign_schemes(dst_id, schemes, bool(target))

    print("\n==> Ustawienia projektu")
    if not a.no_components:
        copy_components(src["key"], key)
    if not a.no_versions:
        copy_versions(src["key"], key, dst_id)
    if a.roles_file and not a.no_roles:
        selection = json.loads(Path(a.roles_file).read_text(encoding="utf-8"))
        print(f"   Role według wyboru ({a.roles_file}): {len(selection)}")
        apply_roles(key, selection)
    elif a.roles_from_source and not a.no_roles:
        print("   Role i członkowie jak w projekcie źródłowym.")
        apply_roles(key, source_role_selection(src["key"]))
    else:
        print("   Role: nikogo nie dodaję (podaj --roles-file albo --roles-from-source).")
    if not a.no_field_contexts:
        copy_field_contexts(src["id"], dst_id)

    if not a.no_boards:
        print("\n==> Tablice")
        copy_boards(src, key, a.target_name.strip(), dst_id, [b.strip() for b in a.board_ids.split(",") if b.strip()])

    print("\n" + "=" * 60)
    print(f"{'PODGLĄD - nic nie zapisano' if DRY else 'GOTOWE'}: {a.source} → {key}")
    if not DRY:
        print(f"   {BASE}/jira/software/c/projects/{key}")
    if WARNINGS:
        print(f"   Ostrzeżeń: {len(WARNINGS)}")
        for w in WARNINGS:
            print(f"    - {w}")
    sys.exit(2 if WARNINGS and not DRY else 0)


if __name__ == "__main__":
    main()
