#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
create_epics_stories_from_sheet.py — tworzy Epiki i Story w Jirze z arkusza „Zakres MVP”.

Źródło: plik .xlsx (w Google Sheets: Plik -> Pobierz -> Microsoft Excel).

  Zakładka „Lista Epiców” -> EPIKI
      kolumna D  = tytuł Epika (np. "1. Front e-commerce (serwis klienta) - Strona główna";
                   gdy pusta — składana z B + " - " + C)
      kolumna E  = opis Epika

  Zakładka „Biznesowy zakres MVP” -> STORY
      kolumna D  = tytuł Story (Funkcja)
      kolumna F  = opis Story (Opis kontekstowy)
      kolumna E  = Status (MVP / POST-MVP) -> KOMPONENT STORY (brakujące komponenty są
                   zakładane w projekcie). Epiki komponentu nie dostają.
      kolumna G  = Uwagi -> dopisywane na końcu opisu (przy pustym F opis = same Uwagi)
  Puste F i G (Story) / puste E (Epik) = pusty opis w Jirze (pole nie jest wysyłane).
      Epik nadrzędny = B + " - " + C, dopasowany do tytułu Epika z „Lista Epiców”
      (bez względu na wielkość liter, spacje i rodzaj myślnika).

Bezpieczne przy wielokrotnym uruchomieniu:
  - przed utworzeniem sprawdza, czy w projekcie jest już Epik o tym samym tytule
    (i Story o tym samym tytule pod tym Epikiem) — wtedy go używa, nie dubluje;
  - zapisuje mapowanie w epics_stories_state.json.

Użycie:
    python create_epics_stories_from_sheet.py --file "Zakres MVP.xlsx" --project KLUCZ --dry-run
    python create_epics_stories_from_sheet.py --file "Zakres MVP.xlsx" --project KLUCZ
    python create_epics_stories_from_sheet.py ... --only-epics          # tylko Epiki
    python create_epics_stories_from_sheet.py ... --status MVP          # tylko Story ze statusem MVP
    python create_epics_stories_from_sheet.py ... --limit 5             # test: 5 pierwszych Story + ich Epiki
"""
import argparse
import json
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import openpyxl
import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core

API = "/rest/api/3"
STATE_FILE = Path(__file__).resolve().parent / "epics_stories_state.json"


# ------------------------------------------------------------------ pomocnicze

def call(method, path, **kw):
    for attempt in range(5):
        r = requests.request(method, f"{core.JIRA_BASE_URL}{path}", auth=core.jira_auth(), timeout=60,
                             headers={"Content-Type": "application/json", "Accept": "application/json"}, **kw)
        if r.status_code != 429:
            return r
        time.sleep(int(r.headers.get("Retry-After", 2 ** attempt)))
    return r


def norm(text):
    """Klucz porównania tytułów: NFC, małe litery, jeden rodzaj myślnika, pojedyncze spacje."""
    t = unicodedata.normalize("NFC", str(text or ""))
    t = re.sub(r"[‐-―−]", "-", t)
    t = re.sub(r"\s*-\s*", " - ", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def cell(row, letter):
    v = row[openpyxl.utils.column_index_from_string(letter) - 1].value if len(row) >= \
        openpyxl.utils.column_index_from_string(letter) else None
    if v is None:
        return ""
    if isinstance(v, str) and v.startswith("="):
        return ""          # formuła bez zapisanej wartości
    return str(v).strip()


def adf(text):
    """Zwykły tekst -> Atlassian Document Format (akapit na linię, pusta linia = odstęp)."""
    blocks = []
    for para in re.split(r"\n\s*\n", (text or "").strip()):
        lines = [l.rstrip() for l in para.split("\n")]
        content = []
        for i, l in enumerate(lines):
            if i:
                content.append({"type": "hardBreak"})
            if l:
                content.append({"type": "text", "text": l})
        if content:
            blocks.append({"type": "paragraph", "content": content})
    return {"type": "doc", "version": 1, "content": blocks or [{"type": "paragraph", "content": []}]}


def find_sheet(wb, wanted):
    for name in wb.sheetnames:
        if norm(name) == norm(wanted):
            return wb[name]
    raise SystemExit(f"[BŁĄD] Brak zakładki '{wanted}'. Są: {', '.join(wb.sheetnames)}")


# ------------------------------------------------------------------ odczyt arkusza

def read_epics(ws):
    epics, order = {}, []
    for i, row in enumerate(ws.iter_rows(min_row=2), start=2):
        title = cell(row, "D") or " - ".join(x for x in (cell(row, "B"), cell(row, "C")) if x)
        if not title or norm(title) in ("jira", "epic"):
            continue
        k = norm(title)
        if k in epics:
            print(f"  [UWAGA] Lista Epiców, wiersz {i}: powtórzony Epik '{title}' — pomijam")
            continue
        epics[k] = {"title": title, "description": cell(row, "E"), "row": i,
                    "area": cell(row, "B"), "name": cell(row, "C")}
        order.append(k)
    return epics, order


def read_stories(ws):
    stories = []
    for i, row in enumerate(ws.iter_rows(min_row=2), start=2):
        title = cell(row, "D")
        if not title:
            continue
        epic_title = " - ".join(x for x in (cell(row, "B"), cell(row, "C")) if x)
        stories.append({"row": i, "title": title, "description": cell(row, "F"),
                        "status": cell(row, "E"), "notes": cell(row, "G"),
                        "epic_key": norm(epic_title), "epic_title": epic_title,
                        "area": cell(row, "B"), "name": cell(row, "C")})
    return stories


# ------------------------------------------------------------------ Jira

def issue_type_id(project, name):
    for it in project.get("issueTypes", []):
        if it["name"].lower() == name.lower():
            return it["id"]
    raise SystemExit(f"[BŁĄD] Projekt nie ma typu '{name}'. Dostępne: "
                     f"{', '.join(t['name'] for t in project.get('issueTypes', []))}")


def required_extra_fields(project_key, type_id):
    """Pola wymagane przy tworzeniu poza standardowymi (np. legacy 'Epic Name')."""
    r = call("GET", f"{API}/issue/createmeta/{project_key}/issuetypes/{type_id}", params={"maxResults": 200})
    if r.status_code >= 300:
        return []
    std = {"summary", "issuetype", "project", "reporter", "description", "parent"}
    return [(f["fieldId"], f["name"]) for f in r.json().get("fields", r.json().get("values", []))
            if f.get("required") and not f.get("hasDefaultValue") and f["fieldId"] not in std]


def search(jql, fields=("summary",)):
    out, token = [], None
    while True:
        body = {"jql": jql, "maxResults": 100, "fields": list(fields)}
        if token:
            body["nextPageToken"] = token
        r = call("POST", f"{API}/search/jql", json=body)
        r.raise_for_status()
        d = r.json()
        out += d.get("issues", [])
        token = d.get("nextPageToken")
        if not token:
            return out


def create_issue(fields):
    r = call("POST", f"{API}/issue", json={"fields": fields})
    if r.status_code >= 300:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:500]}")
    return r.json()["key"]


def ensure_component(project_key, name, cache):
    """ID komponentu o tej nazwie w projekcie; zakłada go, jeśli nie istnieje."""
    k = name.strip().lower()
    if k in cache:
        return cache[k]
    r = call("POST", f"{API}/component", json={"name": name.strip(), "project": project_key})
    if r.status_code >= 300:
        print(f"     [BŁĄD] Tworzenie komponentu '{name}': HTTP {r.status_code} {r.text[:200]}")
        cache[k] = None
        return None
    cache[k] = r.json()["id"]
    print(f"     [UTWORZONO KOMPONENT] '{name}' ({cache[k]})")
    return cache[k]


def ensure_issue_component(key, cid, name):
    """Sprawdza, czy zgłoszenie ma komponent; jeśli nie — dopisuje go (PUT).
    Zwraca True, gdy komponent jest ustawiony."""
    r = call("GET", f"{API}/issue/{key}", params={"fields": "components"})
    if r.status_code >= 300:
        print(f"     [BŁĄD] {key}: odczyt komponentów HTTP {r.status_code}")
        return False
    have = [c["id"] for c in (r.json()["fields"].get("components") or [])]
    if str(cid) in [str(h) for h in have]:
        return True
    u = call("PUT", f"{API}/issue/{key}",
             json={"fields": {"components": [{"id": str(h)} for h in have] + [{"id": str(cid)}]}})
    if u.status_code >= 300:
        print(f"     [BŁĄD] {key}: Jira nie przyjęła komponentu '{name}': HTTP {u.status_code} {u.text[:300]}")
        return False
    print(f"     [POPRAWIONO] {key}: ustawiono komponent '{name}'")
    return True


def ensure_description(key, text, dry_run):
    """Jeśli zgłoszenie nie ma opisu, a arkusz go podaje — uzupełnia (istniejącego opisu nie nadpisuje)."""
    if not (text or "").strip():
        return True
    r = call("GET", f"{API}/issue/{key}", params={"fields": "description"})
    if r.status_code >= 300:
        print(f"     [BŁĄD] {key}: odczyt opisu HTTP {r.status_code}")
        return False
    if r.json()["fields"].get("description"):
        return True
    if dry_run:
        print(f"     [DRY-RUN] {key}: uzupełniłbym pusty opis ({len(text)} zn.)")
        return True
    u = call("PUT", f"{API}/issue/{key}", json={"fields": {"description": adf(text)}})
    if u.status_code >= 300:
        print(f"     [BŁĄD] {key}: Jira nie przyjęła opisu: HTTP {u.status_code} {u.text[:300]}")
        return False
    print(f"     [POPRAWIONO] {key}: uzupełniono opis")
    return True


def strip_components(key, names_lower, dry_run):
    """Usuwa ze zgłoszenia komponenty o podanych nazwach (pozostałe zostawia)."""
    r = call("GET", f"{API}/issue/{key}", params={"fields": "components"})
    if r.status_code >= 300:
        return
    have = r.json()["fields"].get("components") or []
    keep = [c for c in have if c.get("name", "").strip().lower() not in names_lower]
    if len(keep) == len(have):
        return
    gone = [c["name"] for c in have if c not in keep]
    if dry_run:
        print(f"     [DRY-RUN] {key}: usunąłbym komponent(y) {', '.join(gone)}")
        return
    u = call("PUT", f"{API}/issue/{key}", json={"fields": {"components": [{"id": c["id"]} for c in keep]}})
    if u.status_code >= 300:
        print(f"     [BŁĄD] {key}: usuwanie komponentów HTTP {u.status_code} {u.text[:200]}")
    else:
        print(f"     [POPRAWIONO] {key}: usunięto komponent(y) {', '.join(gone)}")


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


# ------------------------------------------------------------------ main

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--file", required=True, help="Plik .xlsx")
    p.add_argument("--project", required=True, help="Klucz projektu w Jirze")
    p.add_argument("--epic-sheet", default="Lista Epiców")
    p.add_argument("--story-sheet", default="Biznesowy zakres MVP")
    p.add_argument("--story-type", default="Story")
    p.add_argument("--status", help="Tylko Story o tym statusie (np. MVP)")
    p.add_argument("--no-status-component", action="store_true",
                   help="Nie ustawiaj komponentów ze statusu (MVP / POST-MVP) w ogóle")
    p.add_argument("--strip-epic-components", action="store_true",
                   help="Usuń komponenty-statusy (MVP / POST-MVP) z ISTNIEJĄCYCH Epików, "
                        "np. ustawione przez starszą wersję skryptu")
    p.add_argument("--only-epics", action="store_true")
    p.add_argument("--limit", type=int, help="Test: tylko N pierwszych Story (i tylko ich Epiki); "
                                             "kolejne uruchomienie bez --limit dokończy resztę bez duplikatów")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    wb = openpyxl.load_workbook(a.file, data_only=True, read_only=True)
    epics, order = read_epics(find_sheet(wb, a.epic_sheet))
    stories = read_stories(find_sheet(wb, a.story_sheet))
    if a.status:
        stories = [s for s in stories if s["status"].lower() == a.status.lower()]
    print(f"Arkusz: {len(epics)} Epików, {len(stories)} Story")

    # Story bez Epika w „Lista Epiców”
    # Dopasowanie zapasowe: obszar w obu zakładkach bywa zapisany różnie, np.
    # "2. Hub ofertowy / Backoffice (panel administracyjny)" vs "2. Hub ofertowy / Backoffice".
    # Wtedy łączymy po numerze obszaru ("2.") + nazwie Epika (kolumna C),
    # a gdy numeru brak — po samej nazwie Epika, o ile jest jednoznaczna.
    def area_no(area):
        m = re.match(r"\s*(\d+)\s*[.)]", area or "")
        return m.group(1) if m else None
    by_no_name = defaultdict(list)
    by_name = defaultdict(list)
    for k, e in epics.items():
        by_no_name[(area_no(e["area"]), norm(e["name"]))].append(k)
        by_name[norm(e["name"])].append(k)
    remapped = Counter()
    for s in stories:
        if s["epic_key"] in epics or not s["name"]:
            continue
        cand = by_no_name.get((area_no(s["area"]), norm(s["name"]))) or []
        if len(cand) != 1 and area_no(s["area"]) is None:
            cand = by_name.get(norm(s["name"])) or []
        if len(cand) == 1:
            remapped[(s["epic_title"], epics[cand[0]]["title"])] += 1
            s["epic_key"] = cand[0]
    if remapped:
        print(f"\n[INFO] Dopasowano po numerze obszaru i nazwie Epika ({sum(remapped.values())} Story):")
        for (src, dst), n in sorted(remapped.items()):
            print(f"    '{src}'  ->  '{dst}'  ({n})")

    orphans = [s for s in stories if s["epic_key"] not in epics]
    if orphans:
        print(f"\n[UWAGA] {len(orphans)} Story wskazuje Epika, którego nie ma w '{a.epic_sheet}' — zostaną pominięte:")
        for title, n in Counter(s["epic_title"] for s in orphans).most_common():
            print(f"    '{title}'  ({n} Story)")
    stories = [s for s in stories if s["epic_key"] in epics]
    # Komponenty Epika = statusy wszystkich jego Story z arkusza (np. MVP, POST-MVP)
    epic_comps = defaultdict(set)
    for st in stories:
        if st["status"]:
            epic_comps[st["epic_key"]].add(st["status"].strip())
    if a.limit:
        stories = stories[:a.limit]
        used = {s["epic_key"] for s in stories}
        order = [k for k in order if k in used]
        print(f"\n[TEST] --limit {a.limit}: {len(stories)} Story w {len(order)} Epikach")

    project = call("GET", f"{API}/project/{a.project}").json()
    if "id" not in project:
        raise SystemExit(f"[BŁĄD] Projekt {a.project}: {project}")
    epic_type = issue_type_id(project, "Epic")
    story_type = issue_type_id(project, a.story_type)
    epic_extra = required_extra_fields(a.project, epic_type)
    story_extra = required_extra_fields(a.project, story_type)
    for label, extra in (("Epik", epic_extra), (a.story_type, story_extra)):
        if extra:
            print(f"[INFO] {label}: pola wymagane przy tworzeniu: {', '.join(n for _, n in extra)}")

    components = {c["name"].strip().lower(): c["id"]
                  for c in (call("GET", f"{API}/project/{a.project}/components").json() or [])}
    status_names = {c.lower() for cs in epic_comps.values() for c in cs}
    mixed = [epics[k]["title"] for k in order if len(epic_comps.get(k, ())) > 1]
    if False and mixed:
        print(f"[INFO] {len(mixed)} Epików ma Story o różnych statusach — dostaną kilka komponentów:")
        for t in mixed:
            k_ = norm(t)
            print(f"    {t}: {', '.join(sorted(epic_comps[k_]))}")
    if not a.no_status_component:
        needed = sorted({st["status"].strip() for st in stories if st["status"]})
        new = [c for c in needed if c.lower() not in components]
        print(f"[INFO] Komponenty ze statusu: {', '.join(needed) or '-'}"
              + (f"  (do utworzenia: {', '.join(new)})" if new else "  (wszystkie istnieją)"))

    state = load_state()
    pstate = state.setdefault(a.project, {"epics": {}, "stories": {}})

    # istniejące Epiki w projekcie (po tytule) — żeby nie dublować
    existing_epics = {norm(i["fields"]["summary"]): i["key"]
                      for i in search(f'project = "{a.project}" AND issuetype = Epic')}

    print(f"\n=== EPIKI ({len(order)}) ===")
    epic_keys, created_e, errors = {}, 0, []
    for k in order:
        e = epics[k]
        key = pstate["epics"].get(k) or existing_epics.get(k)
        want_comps = []          # Epiki nie dostają komponentów
        if key and a.strip_epic_components:
            strip_components(key, status_names, a.dry_run)
        if key:
            epic_keys[k] = key
            print(f"  [ISTNIEJE] {key}  {e['title']}")
            pstate["epics"][k] = key
            if not ensure_description(key, e["description"], a.dry_run):
                errors.append(f"{key}: nie udało się uzupełnić opisu")
            if not a.dry_run:
                for comp in want_comps:
                    cid = ensure_component(a.project, comp, components)
                    if cid and not ensure_issue_component(key, cid, comp):
                        errors.append(f"{key}: nie udało się ustawić komponentu '{comp}'")
            continue
        if a.dry_run:
            print(f"  [DRY-RUN] Epik: {e['title']}  (opis: {len(e['description'])} zn.)"
                  + (f"  [{', '.join(want_comps)}]" if want_comps else ""))
            epic_keys[k] = None
            continue
        fields = {"project": {"key": a.project}, "issuetype": {"id": epic_type},
                  "summary": e["title"][:255]}
        if e["description"].strip():
            fields["description"] = adf(e["description"])
        for fid, fname in epic_extra:
            if "epic name" in fname.lower() or "nazwa epiki" in fname.lower():
                fields[fid] = e["title"][:255]
        cids = [c for c in (ensure_component(a.project, comp, components) for comp in want_comps) if c]
        if cids:
            fields["components"] = [{"id": c} for c in cids]
        try:
            key = create_issue(fields)
        except RuntimeError as exc:
            errors.append(f"Epik '{e['title']}' (wiersz {e['row']}): {exc}")
            print(f"  [BŁĄD] {e['title']}: {exc}")
            continue
        epic_keys[k] = key
        pstate["epics"][k] = key
        save_state(state)
        created_e += 1
        if not ensure_description(key, e["description"], False):
            errors.append(f"{key}: opis nie został zapisany")
        for comp, cid in zip(want_comps, cids):
            if not ensure_issue_component(key, cid, comp):
                errors.append(f"{key}: komponent '{comp}' nie został zapisany")
        print(f"  [UTWORZONO] {key}  {e['title']}")

    created_s = skipped_s = 0
    if not a.only_epics:
        print(f"\n=== STORY ({len(stories)}) ===")
        by_epic = defaultdict(list)
        for s in stories:
            by_epic[s["epic_key"]].append(s)
        for k in order:
            group = by_epic.get(k)
            if not group:
                continue
            ekey = epic_keys.get(k)
            print(f"\n  -- {epics[k]['title']}  [{ekey or 'nowy'}] — {len(group)} Story")
            existing = {}
            if ekey:
                existing = {norm(i["fields"]["summary"]): i["key"]
                            for i in search(f'parent = {ekey}')}
            seen = Counter()
            for s in group:
                seen[norm(s["title"])] += 1
                skey = f"{k}||{norm(s['title'])}||{seen[norm(s['title'])]}"
                key = pstate["stories"].get(skey) or (existing.get(norm(s["title"]))
                                                      if seen[norm(s["title"])] == 1 else None)
                desc = s["description"]
                if s["notes"]:
                    desc = f"{desc}\n\nUwagi: {s['notes']}" if desc.strip() else f"Uwagi: {s['notes']}"
                comp = s["status"].strip() if s["status"] and not a.no_status_component else ""
                labels = [comp] if comp else []
                if key:
                    pstate["stories"][skey] = key
                    skipped_s += 1
                    print(f"     [ISTNIEJE] {key}  {s['title']}")
                    if not ensure_description(key, desc, a.dry_run):
                        errors.append(f"{key}: nie udało się uzupełnić opisu")
                    if comp and not a.dry_run:
                        cid = ensure_component(a.project, comp, components)
                        if cid and not ensure_issue_component(key, cid, comp):
                            errors.append(f"{key}: nie udało się ustawić komponentu '{comp}'")
                    continue
                if a.dry_run or not ekey:
                    print(f"     [DRY-RUN] Story: {s['title']}" + (f"  [{', '.join(labels)}]" if labels else ""))
                    continue
                fields = {"project": {"key": a.project}, "issuetype": {"id": story_type},
                          "summary": s["title"][:255], "parent": {"key": ekey}}
                if desc.strip():
                    fields["description"] = adf(desc)
                if comp:
                    cid = ensure_component(a.project, comp, components)
                    if cid:
                        fields["components"] = [{"id": cid}]
                    else:
                        errors.append(f"Story '{s['title']}': nie udało się ustawić komponentu '{comp}'")
                try:
                    key = create_issue(fields)
                except RuntimeError as exc:
                    errors.append(f"Story '{s['title']}' (wiersz {s['row']}): {exc}")
                    print(f"     [BŁĄD] {s['title']}: {exc}")
                    continue
                pstate["stories"][skey] = key
                save_state(state)
                created_s += 1
                if not ensure_description(key, desc, False):
                    errors.append(f"{key}: opis nie został zapisany")
                if comp and fields.get("components") and not ensure_issue_component(key, fields["components"][0]["id"], comp):
                    errors.append(f"{key}: komponent '{comp}' nie został zapisany")
                print(f"     [UTWORZONO] {key}  {s['title']}" + (f"  [{', '.join(labels)}]" if labels else ""))

    if not a.dry_run:
        save_state(state)
    print("\n" + "=" * 60)
    print(f"Epiki: utworzone {created_e}, istniejące {sum(1 for k in order if epic_keys.get(k)) - created_e}, "
          f"błędy {sum(1 for k in order if k not in epic_keys)}")
    if not a.only_epics:
        print(f"Story: utworzone {created_s}, istniejące {skipped_s}, bez Epika (pominięte) {len(orphans)}")
    if errors:
        print(f"BŁĘDY ({len(errors)}):")
        for e in errors:
            print(f"  - {e}")
    print("[DRY-RUN] Nic nie zostało utworzone." if a.dry_run else "Zakończono.")


if __name__ == "__main__":
    main()
