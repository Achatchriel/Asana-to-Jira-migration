#!/usr/bin/env python3
"""
associate_fields_to_field_scheme.py — dodaje pola z ekranów do FIELD SCHEME projektu.

Po co: Jira Cloud przechodzi z "field configurations" na "field schemes".
Pole, które jest na ekranie, ale NIE jest powiązane z field scheme projektu,
jest niewidoczne i nieedytowalne ("Find your field": Field scheme association).
Tworzenie pól i ekranów przez build_jira_schema.py tego powiązania nie robi.

Co robi:
  1. ustala field scheme projektu (--project),
  2. zbiera ID wszystkich pól ze wskazanych ekranów (domyślnie: wszystkie
     ekrany z sekcji 'screens' w YAML-u),
  3. dodaje do field scheme te, których tam brakuje.
Pola systemowe (summary, assignee…) są pomijane — są zawsze dostępne.

Użycie:
    python associate_fields_to_field_scheme.py --schema jira_schema_klient.yaml --project PROJT --dry-run
    python associate_fields_to_field_scheme.py --schema jira_schema_klient.yaml --project PROJT
    python associate_fields_to_field_scheme.py --project PROJT --fields customfield_11977,customfield_11973

UWAGA: jeśli projekt używa wspólnego 'Default Field Scheme', pola staną się
dostępne we wszystkich projektach z tym schematem (pokażą się tylko tam,
gdzie są na ekranach).
"""
import argparse
import json
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
    return requests.request(method, f"{core.JIRA_BASE_URL}{path}", auth=core.jira_auth(),
                            headers={"Content-Type": "application/json", "Accept": "application/json"},
                            timeout=60, **kw)


def paged(path, **params):
    out, start = [], 0
    while True:
        r = call("GET", path, params={"startAt": start, "maxResults": 100, **params})
        if r.status_code >= 300:
            raise RuntimeError(f"GET {path} -> HTTP {r.status_code}: {r.text[:500]}")
        data = r.json()
        vals = data.get("values", data if isinstance(data, list) else [])
        out += vals
        if isinstance(data, list) or data.get("isLast", True) or not vals:
            return out
        start += len(vals)


def find_key(obj, *names):
    """Pierwsza wartość pod którymś z kluczy (szuka też w zagnieżdżeniach)."""
    if isinstance(obj, dict):
        for n in names:
            if n in obj and not isinstance(obj[n], (dict, list)):
                return obj[n]
        for v in obj.values():
            found = find_key(v, *names)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_key(v, *names)
            if found is not None:
                return found
    return None


def project_field_scheme(project_id):
    r = call("GET", f"{API}/config/fieldschemes/projects", params={"projectId": project_id})
    if r.status_code >= 300:
        raise RuntimeError(f"Field scheme projektu: HTTP {r.status_code}: {r.text[:500]}")
    data = r.json()
    scheme_id = find_key(data, "fieldSchemeId", "schemeId")
    if scheme_id is None:
        scheme_id = find_key(find_key_obj(data, "fieldScheme", "scheme"), "id")
    if scheme_id is None:
        raise RuntimeError("Nie rozpoznano odpowiedzi — surowy JSON:\n" + json.dumps(data, indent=1)[:2000])
    name = None
    try:
        for s in paged(f"{API}/config/fieldschemes"):
            if str(s.get("id")) == str(scheme_id):
                name = s.get("name")
    except RuntimeError:
        pass
    return str(scheme_id), name


def find_key_obj(obj, *names):
    if isinstance(obj, dict):
        for n in names:
            if isinstance(obj.get(n), dict):
                return obj[n]
        for v in obj.values():
            f = find_key_obj(v, *names)
            if f is not None:
                return f
    elif isinstance(obj, list):
        for v in obj:
            f = find_key_obj(v, *names)
            if f is not None:
                return f
    return None


def screen_field_ids(screen_names):
    screens = {s["name"]: s["id"] for s in paged(f"{API}/screens")}
    ids = {}
    for name in screen_names:
        sid = screens.get(name)
        if sid is None:
            print(f"  [POMIJAM] ekranu '{name}' nie ma w Jirze")
            continue
        tabs = call("GET", f"{API}/screens/{sid}/tabs").json()
        for t in tabs:
            for f in call("GET", f"{API}/screens/{sid}/tabs/{t['id']}/fields").json():
                ids.setdefault(f["id"], (f.get("name"), name))
    return ids


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", required=True, help="Klucz projektu (np. PROJT)")
    p.add_argument("--schema", help="YAML — bierze pola ze wszystkich ekranów z sekcji 'screens'")
    p.add_argument("--fields", help="Zamiast ekranów: ID pól po przecinku")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--show-raw", help="Pokaż surowy wpis field scheme dla podanych ID pól (diagnostyka)")
    a = p.parse_args()

    proj = call("GET", f"{API}/project/{a.project}").json()
    scheme_id, scheme_name = project_field_scheme(proj["id"])
    print(f"Projekt {a.project} ({proj['id']}) -> field scheme '{scheme_name or '?'}' ({scheme_id})")

    if a.fields:
        all_f = {f["id"]: f["name"] for f in call("GET", f"{API}/field").json()}
        wanted = {fid.strip(): (all_f.get(fid.strip()), "--fields") for fid in a.fields.split(",") if fid.strip()}
    elif a.schema:
        schema = yaml.safe_load(Path(a.schema).read_text(encoding="utf-8"))
        names = [s["name"] for s in schema.get("screens") or []]
        print(f"Ekrany z YAML: {len(names)}")
        wanted = screen_field_ids(names)
    else:
        p.error("podaj --schema albo --fields")

    wanted = {k: v for k, v in wanted.items() if k.startswith("customfield_")}

    current = {}
    for item in paged(f"{API}/config/fieldschemes/{scheme_id}/fields"):
        fid = item.get("fieldId") or item.get("id")
        if fid:
            current[str(fid)] = item
    print(f"Pól już w field scheme: {len(current)}")

    missing = {k: v for k, v in wanted.items() if k not in current}

    # Pola powiązane, ale OGRANICZONE do części typów pracy — dla pozostałych
    # typów są niewidoczne. Szukamy ograniczenia w odpowiedzi (nazwa klucza
    # może się różnić między wersjami API).
    restricted = {}
    for fid, (name, screen) in wanted.items():
        item = current.get(fid)
        if not item:
            continue
        r = None
        for key in ("restrictedToWorkTypes", "workTypeIds", "restrictedWorkTypes"):
            if item.get(key):
                r = item[key]
        if r:
            restricted[fid] = (name, screen, r)

    print(f"Pól niestandardowych z ekranów: {len(wanted)}, brakuje w field scheme: {len(missing)}, "
          f"ograniczonych do wybranych typów: {len(restricted)}")
    for fid, (name, screen) in sorted(missing.items(), key=lambda kv: str(kv[1][0])):
        print(f"  + {name} ({fid})  [np. ekran '{screen}']")
    for fid, (name, screen, r) in sorted(restricted.items(), key=lambda kv: str(kv[1][0])):
        print(f"  ~ {name} ({fid}) tylko dla typów {r} -> zdejmę ograniczenie")
    if a.show_raw:
        for fid in a.show_raw.split(","):
            print(f"\nSurowy wpis {fid}: {json.dumps(current.get(fid.strip()), ensure_ascii=False, indent=1)}")

    todo = list(missing) + list(restricted)
    if not todo:
        print("Nic do zrobienia.")
        return
    if a.dry_run:
        print("[DRY-RUN] Nic nie zmieniono.")
        return

    sid = int(scheme_id) if scheme_id.isdigit() else scheme_id
    ok = failed = 0
    # Paczkami — mniejsze ryzyko, że jeden zły wpis zablokuje resztę
    for i in range(0, len(todo), 20):
        chunk = todo[i:i + 20]
        body = {fid: [{"schemeIds": [sid], "restrictedToWorkTypes": None}] for fid in chunk}
        r = call("PUT", f"{API}/config/fieldschemes/fields", json=body)
        if r.status_code >= 300:
            print(f"[BŁĄD] HTTP {r.status_code}: {r.text[:1500]}")
            print("Wklej ten komunikat — format API field schemes jest nowy i mógł się zmienić.")
            sys.exit(1)
        results = (r.json() if r.text.strip() else {}).get("results")
        if results is None:
            ok += len(chunk)
            continue
        for res in results:
            if res.get("success", True):
                ok += 1
            else:
                failed += 1
                print(f"  [BŁĄD] {res.get('fieldId')}: {res.get('error')}")
    print(f"[OK] Zapisano {ok} pól w field scheme '{scheme_name or scheme_id}'"
          + (f", błędów: {failed}" if failed else "") + ".")


if __name__ == "__main__":
    main()
