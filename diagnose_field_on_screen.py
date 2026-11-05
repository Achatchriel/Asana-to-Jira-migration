#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
diagnose_field_on_screen.py — TYLKO ODCZYT, niczego nie zmienia.

Sprawdza, dlaczego pole nie jest widoczne w zgłoszeniu danego typu:
  1. czy pole istnieje (i czy nie ma kilku pól o tej samej nazwie),
  2. kontekst pola — czy obejmuje ten projekt i typ zgłoszenia,
  3. na jakich ekranach pole jest,
  4. jakie ekrany projekt NAPRAWDĘ używa dla tego typu (create/edit/view)
     — przez Work Type Screen Scheme projektu -> Screen Scheme -> ekrany,
  5. (opcjonalnie) czy pole jest edytowalne w konkretnym zgłoszeniu.

Użycie:
    python diagnose_field_on_screen.py --field "Wersja wdrożenia" --project ABC --issue-type Deployment
    python diagnose_field_on_screen.py --field "Wersja wdrożenia" --issue ABC-12
"""
import argparse
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core


def get(path, **params):
    r = requests.get(f"{core.JIRA_BASE_URL}{path}", auth=core.jira_auth(),
                     params=params, timeout=60)
    return r


def paged(path, **params):
    out, start = [], 0
    while True:
        r = get(path, startAt=start, maxResults=100, **params)
        if r.status_code >= 300:
            return out
        data = r.json()
        vals = data.get("values", data if isinstance(data, list) else [])
        out += vals
        if isinstance(data, list) or data.get("isLast", True) or not vals:
            return out
        start += len(vals)


def screen_fields(screen_id):
    """{fieldId: 'zakładka'} dla ekranu."""
    res = {}
    r = get(f"/rest/api/3/screens/{screen_id}/tabs")
    if r.status_code >= 300:
        return res
    for tab in r.json():
        rf = get(f"/rest/api/3/screens/{screen_id}/tabs/{tab['id']}/fields")
        if rf.status_code < 300:
            for f in rf.json():
                res[f["id"]] = tab["name"]
    return res


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--field", required=True, help="Nazwa pola albo ID (customfield_XXXXX)")
    p.add_argument("--project", help="Klucz projektu")
    p.add_argument("--issue-type", default="Deployment")
    p.add_argument("--issue", help="Klucz zgłoszenia (wtedy projekt i typ brane z niego)")
    a = p.parse_args()

    project_key, it_name = a.project, a.issue_type
    if a.issue:
        r = get(f"/rest/api/3/issue/{a.issue}", fields="project,issuetype")
        r.raise_for_status()
        f = r.json()["fields"]
        project_key, it_name = f["project"]["key"], f["issuetype"]["name"]
        print(f"Zgłoszenie {a.issue}: projekt {project_key}, typ '{it_name}' ({f['issuetype']['id']})")

    # 1. pole
    print(f"\n=== 1. POLE '{a.field}' ===")
    import unicodedata
    norm = lambda t: unicodedata.normalize("NFC", (t or "").strip().lower())
    # Dwa źródła: /field i /field/search (build_jira_schema.py używa tego
    # drugiego) — potrafią zwracać nazwy różnie (np. tłumaczenia pól).
    all_fields = {f["id"]: f for f in get("/rest/api/3/field").json()}
    for f in paged("/rest/api/3/field/search"):
        all_fields.setdefault(f["id"], f)
        if norm(f["name"]) != norm(all_fields[f["id"]]["name"]):
            all_fields[f["id"]]["_alt_name"] = f["name"]
    all_fields = list(all_fields.values())
    want = norm(a.field)
    matches = [f for f in all_fields if f["id"].lower() == want or norm(f["name"]) == want
               or norm(f.get("_alt_name")) == want]
    if not matches:
        print("  [BRAK] Takie pole nie istnieje w Jirze -> uruchom --only custom_fields")
        similar = [f for f in all_fields if want.split()[0] in norm(f["name"])]
        for f in similar[:10]:
            print(f"     podobne: '{f['name']}' -> {f['id']}")
        return
    for f in matches:
        if f.get("_alt_name"):
            print(f"  [UWAGA] {f['id']}: /field zwraca nazwę '{f['name']}', /field/search — '{f['_alt_name']}'")
        print(f"  '{f['name']}' -> {f['id']}  typ: {(f.get('schema') or {}).get('custom') or (f.get('schema') or {}).get('type')}")
    if len(matches) > 1:
        print("  [UWAGA] Kilka pól o tej nazwie — na ekranie może być INNE niż to, którego szukasz.")

    # projekt i typ
    project = it = None
    if project_key:
        r = get(f"/rest/api/3/project/{project_key}")
        r.raise_for_status()
        project = r.json()
        its = [t for t in project.get("issueTypes", []) if t["name"].lower() == it_name.lower()]
        if not its:
            print(f"\n  [BRAK] Projekt {project_key} nie ma typu '{it_name}'. "
                  f"Dostępne: {', '.join(t['name'] for t in project.get('issueTypes', []))}")
            return
        it = its[0]

    for f in matches:
        fid = f["id"]
        print(f"\n=== 2. KONTEKST {fid} ===")
        if not fid.startswith("customfield_"):
            print("  pole systemowe — kontekst nie dotyczy")
        else:
            ctxs = paged(f"/rest/api/3/field/{fid}/context")
            if not ctxs:
                print("  [BRAK KONTEKSTU] Pole nie ma żadnego kontekstu — nie pokaże się nigdzie.")
            ok_any = False
            for c in ctxs:
                desc = f"  '{c['name']}' ({c['id']}): "
                desc += "wszystkie projekty" if c.get("isGlobalContext") else "wybrane projekty"
                desc += ", wszystkie typy" if c.get("isAnyIssueType") else ", wybrane typy"
                print(desc)
                proj_ok = c.get("isGlobalContext")
                type_ok = c.get("isAnyIssueType")
                if project and not proj_ok:
                    pm = paged(f"/rest/api/3/field/{fid}/context/projectmapping", contextId=c["id"])
                    proj_ok = any(m.get("projectId") == project["id"] for m in pm)
                if it and not type_ok:
                    tm = paged(f"/rest/api/3/field/{fid}/context/issuetypemapping", contextId=c["id"])
                    type_ok = any(m.get("issueTypeId") == it["id"] for m in tm)
                if project and it:
                    print(f"     obejmuje {project_key}/{it_name}: projekt {'TAK' if proj_ok else 'NIE'}, "
                          f"typ {'TAK' if type_ok else 'NIE'}")
                    ok_any = ok_any or (proj_ok and type_ok)
            if project and it and ctxs and not ok_any:
                print(f"  [PROBLEM] Żaden kontekst nie obejmuje {project_key}/{it_name} — pole będzie ukryte.")

        print(f"\n=== 3. EKRANY Z POLEM {fid} ===")
        r = get(f"/rest/api/3/field/{fid}/screens", maxResults=100)
        if r.status_code < 300:
            scr = r.json().get("values", [])
            for s in scr:
                print(f"  '{s['name']}' ({s['id']}) — zakładka '{(s.get('tab') or {}).get('name', '?')}'")
            if not scr:
                print("  [BRAK] Pole nie jest na żadnym ekranie -> uruchom --only screens i sprawdź log")
        else:
            print(f"  (nie udało się pobrać: HTTP {r.status_code})")

    if not (project and it):
        print("\n(Podaj --project i --issue-type albo --issue, żeby sprawdzić ekrany projektu.)")
        return

    # 4. ekrany używane przez projekt
    print(f"\n=== 4. EKRANY PROJEKTU {project_key} DLA '{it_name}' ===")
    r = get("/rest/api/3/issuetypescreenscheme/project", projectId=project["id"])
    vals = r.json().get("values", []) if r.status_code < 300 else []
    if not vals:
        print("  (nie udało się ustalić Work Type Screen Scheme — projekt team-managed?)")
        return
    itss = vals[0]["issueTypeScreenScheme"]
    print(f"  Work Type Screen Scheme: '{itss['name']}' ({itss['id']})")
    maps = paged("/rest/api/3/issuetypescreenscheme/mapping", issueTypeScreenSchemeId=itss["id"])
    by_type = {m["issueTypeId"]: m["screenSchemeId"] for m in maps}
    ss_id = by_type.get(it["id"]) or by_type.get("default")
    src = "własne mapowanie" if it["id"] in by_type else "mapowanie DOMYŚLNE (typ nie ma własnego wiersza!)"
    ss = paged("/rest/api/3/screenscheme", id=ss_id)
    if not ss:
        print(f"  screen scheme {ss_id} — nie udało się pobrać")
        return
    ss = ss[0]
    print(f"  Screen Scheme: '{ss['name']}' ({ss['id']}) — {src}")
    screens = ss.get("screens", {})
    default = screens.get("default")
    field_ids = {f["id"] for f in matches}
    for op in ("create", "edit", "view"):
        sid = screens.get(op, default)
        sname = get(f"/rest/api/3/screens", id=sid).json().get("values", [{}])
        sname = sname[0].get("name", sid) if sname else sid
        on = {fid: tab for fid, tab in screen_fields(sid).items() if fid in field_ids}
        status = (f"JEST (zakładka '{list(on.values())[0]}')" if on else "[BRAK]")
        print(f"  {op:6} -> '{sname}' ({sid}): {status}")

    # 5. konkretne zgłoszenie
    editable, on_screen = None, {}
    if a.issue:
        print(f"\n=== 5. EDYCJA {a.issue} ===")
        r = get(f"/rest/api/3/issue/{a.issue}/editmeta")
        if r.status_code < 300:
            editable = r.json().get("fields", {})
            for fid in field_ids:
                print(f"  {fid}: {'edytowalne' if fid in editable else '[NIEDOSTĘPNE w edycji]'}")
            ri = get(f"/rest/api/3/issue/{a.issue}", fields="status," + ",".join(field_ids))
            if ri.status_code < 300:
                fi = ri.json()["fields"]
                print(f"  status zgłoszenia: '{fi['status']['name']}'")
                for fid in field_ids:
                    print(f"  wartość {fid}: {fi.get(fid)!r}")
            print(f"  edytowalnych pól w tym zgłoszeniu: {len(editable)}")
            print("    " + ", ".join(sorted(v.get("name", k) for k, v in editable.items())))
            # Porównanie z ekranem edycji: które pola z ekranu Jira odrzuca
            edit_sid = screens.get("edit", default)
            on_screen = screen_fields(edit_sid)
            names = {f["id"]: f["name"] for f in all_fields}
            missing = [fid for fid in on_screen if fid not in editable]
            print(f"  pól na ekranie edycji: {len(on_screen)}, z tego NIEedytowalnych: {len(missing)}")
            for fid in missing:
                print(f"    - {names.get(fid, fid)} ({fid}) [zakładka '{on_screen[fid]}']")
            if not editable:
                print("  [PROBLEM] Żadne pole nie jest edytowalne -> brak uprawnienia 'Edit work items' "
                      "albo status ma właściwość jira.issue.editable=false (np. status zamknięty).")
        else:
            print(f"  (editmeta: HTTP {r.status_code})")

    # 6. field configuration — pole może być UKRYTE mimo obecności na ekranie
    print(f"\n=== 6. FIELD CONFIGURATION PROJEKTU {project_key} DLA '{it_name}' ===")
    r = get("/rest/api/3/fieldconfigurationscheme/project", projectId=project["id"])
    vals = r.json().get("values", []) if r.status_code < 300 else []
    fcs = (vals[0].get("fieldConfigurationScheme") if vals else None)
    fc_id, fc_name = None, None
    if fcs:
        print(f"  Field Configuration Scheme: '{fcs['name']}' ({fcs['id']})")
        maps = paged("/rest/api/3/fieldconfigurationscheme/mapping", fieldConfigurationSchemeId=fcs["id"])
        by_type = {m["issueTypeId"]: m["fieldConfigurationId"] for m in maps}
        fc_id = by_type.get(it["id"]) or by_type.get("default")
    else:
        print("  Field Configuration Scheme: domyślny systemowy")
    cfgs = paged("/rest/api/3/fieldconfiguration")
    for c in cfgs:
        if (fc_id and str(c["id"]) == str(fc_id)) or (not fc_id and c.get("isDefault")):
            fc_id, fc_name = c["id"], c["name"]
    if not fc_id:
        print("  (nie udało się ustalić field configuration)")
        return
    print(f"  Field Configuration: '{fc_name}' ({fc_id})")
    items = {i["id"]: i for i in paged(f"/rest/api/3/fieldconfiguration/{fc_id}/fields")}
    print(f"  wpisów w konfiguracji: {len(items)}")
    for fid in field_ids:
        i = items.get(fid)
        if not i:
            print(f"  {fid}: brak wpisu (domyślnie widoczne)")
        elif i.get("isHidden"):
            print(f"  {fid}: [UKRYTE] — to dlatego pola nie widać. Odkryj je: Ustawienia -> "
                  f"Work items -> Field configurations -> '{fc_name}' -> '{fid}' -> Show")
        else:
            print(f"  {fid}: widoczne{' (wymagane)' if i.get('isRequired') else ''}")


    # 7. porównanie wszystkich pól z ekranu edycji — co odróżnia odrzucone od działających
    if editable is None or not on_screen:
        return
    print("\n=== 7. POLA Z EKRANU EDYCJI: edytowalne vs odrzucone ===")
    names = {f["id"]: f["name"] for f in all_fields}
    schema_of = {f["id"]: (f.get("schema") or {}).get("custom", (f.get("schema") or {}).get("type", "")) for f in all_fields}
    print(f"  {'pole':45} {'edyt.':6} {'w FC':5} {'kontekst':32} typ")
    for fid in sorted(on_screen, key=lambda x: (x in editable, x)):
        ctx = "-"
        if fid.startswith("customfield_"):
            cs = paged(f"/rest/api/3/field/{fid}/context")
            if not cs:
                ctx = "BRAK"
            else:
                parts = []
                for c in cs:
                    pr = "global" if c.get("isGlobalContext") else "proj"
                    ty = "wszystkie" if c.get("isAnyIssueType") else "wybrane"
                    if not c.get("isGlobalContext"):
                        pm = paged(f"/rest/api/3/field/{fid}/context/projectmapping", contextId=c["id"])
                        if any(m.get("projectId") == project["id"] for m in pm):
                            pr = "proj✓"
                    if not c.get("isAnyIssueType"):
                        tm = paged(f"/rest/api/3/field/{fid}/context/issuetypemapping", contextId=c["id"])
                        if any(m.get("issueTypeId") == it["id"] for m in tm):
                            ty = "typ✓"
                    parts.append(f"{pr}/{ty}")
                ctx = ",".join(parts)
        in_fc = "tak" if fid in items else "NIE"
        if fid in items and items[fid].get("isHidden"):
            in_fc = "UKR"
        nm = f"{names.get(fid, fid)} ({fid})"
        print(f"  {nm[:45]:45} {'TAK' if fid in editable else 'nie':6} {in_fc:5} {ctx[:32]:32} {str(schema_of.get(fid, ''))[-30:]}")


if __name__ == "__main__":
    main()
