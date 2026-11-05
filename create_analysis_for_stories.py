#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
create_analysis_for_stories.py — dla każdego Story tworzy zadanie typu „Analysis”
o tej samej nazwie i łączy je ze Story relacją „jest powiązane z” (Relates).

Źródło listy Story (jedno z dwóch):
  --file  eksport CSV z Jiry (kolumny „Klucz zgłoszenia”/„Issue key” i
          „Podsumowanie”/„Summary”; przecinek, średnik albo tabulator)
  --jql   zapytanie, np. 'project = PROJ AND issuetype = Story'

Każde Analysis jest łączone z KONKRETNYM Story z listy (po kluczu), więc
powtarzające się nazwy (np. kilka „Publikacja”, „Draft”, „CTA”) nie mieszają się.

Bezpieczne przy wielokrotnym uruchomieniu: jeśli Story jest już powiązane z
zadaniem typu Analysis o tej samej nazwie, nic nie jest tworzone. Dodatkowo
stan w analysis_for_stories_state.json (Story -> Analysis).

Użycie:
    python create_analysis_for_stories.py --project PROJ --file storki.csv --dry-run
    python create_analysis_for_stories.py --project PROJ --file storki.csv --limit 3
    python create_analysis_for_stories.py --project PROJ --file storki.csv
    python create_analysis_for_stories.py --project PROJ --jql 'project = PROJ AND issuetype = Story'

Opcje:
    --prefix "{AN} "      przedrostek nazwy (domyślnie "{AN} "; --prefix "" = bez)
    --issue-type Analysis typ tworzonych zadań
    --link-type Relates   typ relacji (nazwa albo opis, np. "jest powiązane z")
    (domyślnie)           Analysis trafia pod ten sam Epik co Story — także już
                          istniejące Analysis są przepinane, jeśli mają inny Epik
    --no-epic             nie ustawiaj Epika
"""
import argparse
import csv
import json
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core

API = "/rest/api/3"
STATE_FILE = Path(__file__).resolve().parent / "analysis_for_stories_state.json"


def call(method, path, **kw):
    for attempt in range(5):
        r = requests.request(method, f"{core.JIRA_BASE_URL}{path}", auth=core.jira_auth(), timeout=60,
                             headers={"Content-Type": "application/json", "Accept": "application/json"}, **kw)
        if r.status_code != 429:
            return r
        time.sleep(int(r.headers.get("Retry-After", 2 ** attempt)))
    return r


def norm(t):
    return " ".join(str(t or "").split()).lower()


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def read_file(path):
    """Lista (klucz, nazwa) z eksportu CSV z Jiry (przecinek, średnik albo tabulator;
    pola w cudzysłowach, także wielowierszowe). Kolumny rozpoznawane po nagłówku."""
    import io
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp1250", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    first = text.splitlines()[0] if text else ""
    delim = max(",;\t", key=first.count) if first else ","
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]
    head = [norm(h) for h in rows[0]]
    def col(*names):
        for n in names:
            if n in head:
                return head.index(n)
        raise SystemExit(f"[BŁĄD] Brak kolumny {names[0]!r} w nagłówku: {rows[0]}")
    k = col("klucz zgłoszenia", "klucz", "issue key", "key")
    s = col("podsumowanie", "summary")
    return [(r[k].strip(), r[s].strip()) for r in rows[1:] if len(r) > max(k, s) and r[k].strip()]


def search(jql, fields):
    out, token = [], None
    while True:
        body = {"jql": jql, "maxResults": 100, "fields": fields}
        if token:
            body["nextPageToken"] = token
        r = call("POST", f"{API}/search/jql", json=body)
        if r.status_code >= 300:
            raise SystemExit(f"[BŁĄD] JQL: HTTP {r.status_code} {r.text[:300]}")
        d = r.json()
        out += d.get("issues", [])
        token = d.get("nextPageToken")
        if not token:
            return out


def find_link_type(wanted):
    r = call("GET", f"{API}/issueLinkType")
    r.raise_for_status()
    types = r.json().get("issueLinkTypes", [])
    w = norm(wanted)
    for t in types:
        if w in (norm(t["name"]), norm(t.get("inward")), norm(t.get("outward"))):
            return t
    raise SystemExit(f"[BŁĄD] Brak typu relacji '{wanted}'. Dostępne: "
                     + "; ".join(f"{t['name']} ({t.get('outward')} / {t.get('inward')})" for t in types))


def ensure_parent(key, epic_key, dry_run):
    """Ustawia Epik nadrzędny zadania, jeśli jest inny niż epic_key. Zwraca błąd albo None."""
    r = call("GET", f"{API}/issue/{key}", params={"fields": "parent"})
    if r.status_code >= 300:
        return f"{key}: odczyt Epika HTTP {r.status_code}"
    now = ((r.json().get("fields") or {}).get("parent") or {}).get("key")
    if now == epic_key:
        print(f"     Epik OK: {key} jest już pod {epic_key}")
        return None
    if dry_run:
        print(f"     [DRY-RUN] {key}: przepiąłbym pod Epik {epic_key}" + (f" (teraz {now})" if now else ""))
        return None
    u = call("PUT", f"{API}/issue/{key}", json={"fields": {"parent": {"key": epic_key}}})
    if u.status_code >= 300:
        print(f"     [BŁĄD] {key}: ustawienie Epika {epic_key}: HTTP {u.status_code} {u.text[:300]}")
        return f"{key}: ustawienie Epika HTTP {u.status_code} {u.text[:200]}"
    print(f"     [POPRAWIONO] {key}: podpięto pod Epik {epic_key}" + (f" (było {now})" if now else ""))
    return None


def link_issues(link, story_key, analysis_key):
    """Tworzy relację; zwraca opis błędu albo None."""
    lr = call("POST", f"{API}/issueLink", json={
        "type": {"name": link["name"]},
        "inwardIssue": {"key": story_key}, "outwardIssue": {"key": analysis_key}})
    if lr.status_code >= 300:
        print(f"  [BŁĄD] relacja {story_key} <-> {analysis_key}: HTTP {lr.status_code} {lr.text[:300]}")
        return f"{story_key} <-> {analysis_key}: relacja HTTP {lr.status_code} {lr.text[:300]}"
    return None


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", required=True)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", help="Eksport TSV/CSV z kolumnami 'Klucz zgłoszenia' i 'Podsumowanie'")
    src.add_argument("--jql", help="Zapytanie zwracające Story")
    p.add_argument("--prefix", default="{AN} ")
    p.add_argument("--issue-type", default="Analysis")
    p.add_argument("--link-type", default="Relates")
    p.add_argument("--no-epic", action="store_true",
                   help="Nie podpinaj Analysis pod Epik Story (domyślnie podpina)")
    p.add_argument("--limit", type=int, help="Test: tylko N pierwszych Story")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    # Story: klucz, nazwa, Epik, istniejące powiązania
    if a.file:
        listed = read_file(a.file)
        keys = [k for k, _ in listed]
        print(f"Plik: {len(listed)} Story")
        info = {}
        for i in range(0, len(keys), 100):
            chunk = keys[i:i + 100]
            for iss in search(f"key in ({','.join(chunk)})", ["summary", "parent", "issuelinks", "issuetype"]):
                info[iss["key"]] = iss
        missing = [k for k in keys if k not in info]
        if missing:
            print(f"[UWAGA] Nie znaleziono w Jirze ({len(missing)}): {', '.join(missing[:20])}"
                  + (" …" if len(missing) > 20 else ""))
        stories = [(k, info[k]) for k in keys if k in info]
    else:
        stories = [(i["key"], i) for i in search(a.jql, ["summary", "parent", "issuelinks", "issuetype"])]
        print(f"JQL: {len(stories)} zgłoszeń")
    if a.limit:
        stories = stories[:a.limit]

    project = call("GET", f"{API}/project/{a.project}").json()
    types = {t["name"].lower(): t["id"] for t in project.get("issueTypes", [])}
    if a.issue_type.lower() not in types:
        raise SystemExit(f"[BŁĄD] Projekt {a.project} nie ma typu '{a.issue_type}'. Dostępne: "
                         f"{', '.join(t['name'] for t in project.get('issueTypes', []))}\n"
                         f"Dodaj go do Work Type Scheme projektu (oraz ekranów i workflow scheme).")
    type_id = types[a.issue_type.lower()]
    link = find_link_type(a.link_type)
    print(f"Typ: {a.issue_type} ({type_id}), relacja: {link['name']} "
          f"('{link.get('outward')}' / '{link.get('inward')}')")

    state = load_state()
    pstate = state.setdefault(a.project, {})
    created = skipped = 0
    errors, no_epic_stories = [], []

    for key, iss in stories:
        f = iss["fields"]
        summary = (a.prefix + f["summary"])[:255]
        # już istnieje? (stan albo powiązanie z Analysis o tej samej nazwie)
        existing = pstate.get(key)
        if not existing:
            for l in f.get("issuelinks") or []:
                other = l.get("outwardIssue") or l.get("inwardIssue") or {}
                of = other.get("fields") or {}
                if (norm((of.get("issuetype") or {}).get("name")) == norm(a.issue_type)
                        and norm(of.get("summary")) in (norm(summary), norm(f["summary"]))):
                    existing = other.get("key")
                    break
        if existing:
            pstate[key] = existing
            skipped += 1
            linked = any((l.get("outwardIssue") or l.get("inwardIssue") or {}).get("key") == existing
                         for l in f.get("issuelinks") or [])
            print(f"  [ISTNIEJE] {key} <-> {existing}  {summary}" + ("" if linked else "  (brak relacji)"))
            if not a.no_epic:
                want = (f.get("parent") or {}).get("key")
                if not want:
                    no_epic_stories.append(key)
                    print(f"     [UWAGA] Story {key} nie ma Epika — Analysis zostaje bez Epika")
                else:
                    err = ensure_parent(existing, want, a.dry_run)
                    if err:
                        errors.append(err)
            if not linked and not a.dry_run:
                err = link_issues(link, key, existing)
                if err:
                    errors.append(err)
                else:
                    print(f"     [POPRAWIONO] dodano relację {key} <-> {existing}")
            continue
        if a.dry_run:
            print(f"  [DRY-RUN] {key}: utworzyłbym '{summary}'"
                  + (f" pod Epikiem {f['parent']['key']}" if not a.no_epic and f.get("parent") else ""))
            continue

        fields = {"project": {"key": a.project}, "issuetype": {"id": type_id}, "summary": summary}
        if not a.no_epic and not f.get("parent"):
            no_epic_stories.append(key)
        if not a.no_epic and f.get("parent"):
            fields["parent"] = {"key": f["parent"]["key"]}
        r = call("POST", f"{API}/issue", json={"fields": fields})
        if r.status_code >= 300:
            errors.append(f"{key}: tworzenie HTTP {r.status_code} {r.text[:300]}")
            print(f"  [BŁĄD] {key}: {r.text[:300]}")
            continue
        new_key = r.json()["key"]
        pstate[key] = new_key
        save_state(state)
        err = link_issues(link, key, new_key)
        if err:
            errors.append(err)
            continue
        created += 1
        print(f"  [UTWORZONO] {new_key}  {summary}   <-> {key}")

    if not a.dry_run:
        save_state(state)
    print("\n" + "=" * 60)
    print(f"Utworzone: {created}, istniejące: {skipped}, błędy: {len(errors)}")
    if no_epic_stories:
        print(f"Story bez Epika ({len(no_epic_stories)}): {', '.join(no_epic_stories[:30])}"
              + (" …" if len(no_epic_stories) > 30 else ""))
    for e in errors:
        print(f"  - {e}")
    print("[DRY-RUN] Nic nie zostało utworzone." if a.dry_run else "Zakończono.")


if __name__ == "__main__":
    main()
