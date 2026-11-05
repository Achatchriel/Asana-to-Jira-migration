#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
compare_project_totals.py

Pełne porównanie projektu Asana <-> Jira, WSZYSTKIE poziomy zagnieżdżenia
naraz, po IDENTYFIKATORACH (nie po tytułach - zduplikowane nazwy typu
"2000 Best Practice Audit" nie mylą wyniku).

Strona Asany: zadania projektu + rekurencyjnie wszystkie ich podzadania,
każde zadanie liczone RAZ (podzadania dodane dodatkowo do projektu nie są
liczone podwójnie).
Strona Jiry: wszystkie zgłoszenia projektu, z podziałem na typy.
Powiązania: task_sync_state.json (asana_gid -> jira_key).

Raport per projekt:
  - liczby: Asana (wg poziomu) vs Jira (wg typu),
  - w Asanie, nie zmigrowane (brak wpisu w stanie) - z poziomem zagnieżdżenia,
  - w stanie, ale zgłoszenia nie ma w Jirze,
  - w stanie jako usunięte w Jirze (deleted_in_jira - celowo pomijane),
  - w Jirze, bez powiązania w stanie (ręcznie dodane albo duplikaty),
  - w stanie, ale zadania nie ma już w Asanie (usunięte w Asanie).
Rozbieżności zapisywane do compare_project_totals_report.csv.

Tylko odczyt - niczego nie zmienia.

Użycie:
    python compare_project_totals.py PROJ5
    python compare_project_totals.py PROJ5,PROJ11,CMANBE
    python compare_project_totals.py --all                   # wszystkie projekty z arkusza
    python compare_project_totals.py --all --shard 1/3       # równolegle (osobny CSV na shard)
    python compare_project_totals.py --all --attachments     # dodatkowo załączniki (tylko pliki,
                                                             # bo tylko takie przenosi synchronizacja)
    python compare_project_totals.py PROJ5 --details       # listy konkretnych zadań na konsoli
    python compare_project_totals.py --all --top-level       # tylko zadania najwyższego poziomu (szybko:
                                                             # bez schodzenia w podzadania)
Przy --all na konsoli są tylko liczby per projekt (listy w CSV); --details
wypisuje też konkretne zadania. Klucz z kilkoma różnymi projektami Asany w
arkuszu jest pomijany i zgłaszany (zwykle pomyłka w arkuszu).
"""
import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core  # noqa: E402
from asana_jira_sync import (  # noqa: E402
    ASANA_API, ASANA_HEADERS, JIRA_AUTH, JIRA_BASE_URL, REQUEST_TIMEOUT,
    load_state, request_with_retry,
)

REPORT_FILE = Path(__file__).resolve().parent / "compare_project_totals_report.csv"
SHOW = 25  # ile pozycji każdej kategorii wypisać na konsolę (reszta w CSV)


def _asana_list(url: str, fields: str) -> list:
    params = {"opt_fields": fields, "limit": 100}
    out, offset = [], None
    while True:
        if offset:
            params["offset"] = offset
        resp = request_with_retry("GET", url, headers=ASANA_HEADERS, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
        out.extend(payload.get("data", []))
        offset = (payload.get("next_page") or {}).get("offset")
        if not offset:
            return out


def collect_asana(project_gid: str, with_completed: bool = False, with_details: bool = False) -> dict:
    """{gid: {"name", "depth", "parent"[, "completed"][, "notes", "due", "assignee"]}} - każde
    zadanie raz, z głębokością (0 = najwyższy poziom). Podzadania pobierane rekurencyjnie.
    with_details: także opis, termin i przypisany (do porównywania treści)."""
    extra = ",completed" if (with_completed or with_details) else ""
    if with_details:
        extra += ",notes,due_on,assignee.gid"

    def entry(task: dict, depth: int, parent) -> dict:
        e = {"name": task.get("name") or "", "depth": depth, "parent": parent}
        if with_completed or with_details:
            e["completed"] = bool(task.get("completed"))
        if with_details:
            e["notes"] = task.get("notes") or ""
            e["due"] = task.get("due_on")
            e["assignee"] = (task.get("assignee") or {}).get("gid")
        return e

    def walk(queue: list, found: dict, report: bool) -> None:
        done = 0
        while queue:
            gid = queue.pop()
            for sub in _asana_list(f"{ASANA_API}/tasks/{gid}/subtasks", "name" + extra):
                if sub["gid"] in found:
                    continue
                found[sub["gid"]] = entry(sub, found[gid]["depth"] + 1, gid)
                queue.append(sub["gid"])
            done += 1
            if report and done % 200 == 0:
                print(f"    Asana: sprawdzono {done} zadań, znaleziono {len(found)}...")

    project_tasks = _asana_list(f"{ASANA_API}/projects/{project_gid}/tasks", "name,parent.gid" + extra)
    found = {}
    queue = []
    for t in project_tasks:
        if (t.get("parent") or {}).get("gid"):
            # Podzadanie dodane do projektu. Nawet gdy jego bezpośredni rodzic NIE jest
            # na liście projektu, wyżej w drzewie może być przodek, który jest - wtedy
            # rekurencja niżej znajdzie je na właściwej głębokości. Za najwyższy poziom
            # uznajemy je dopiero, gdy rekurencja do niego nie dotrze (pętla na końcu).
            continue
        found[t["gid"]] = entry(t, 0, None)
        queue.append(t["gid"])
    walk(queue, found, report=True)
    # Podzadanie dodane do projektu, do którego rekurencja nie dotarła (żaden jego
    # przodek nie jest w projekcie) - liczymy je jako najwyższy poziom, razem z
    # jego własnym poddrzewem.
    for t in project_tasks:
        if t["gid"] in found:
            continue  # znalezione przez rekurencję albo poddrzewo wcześniejszej sieroty
        found[t["gid"]] = entry(t, 0, None)
        walk([t["gid"]], found, report=False)
    return found


def adf_plain(node) -> str:
    """Tekst z dokumentu ADF (opis/komentarz Jiry)."""
    if isinstance(node, dict):
        return (node.get("text") or "") + " ".join(adf_plain(c) for c in node.get("content") or [])
    if isinstance(node, list):
        return " ".join(adf_plain(c) for c in node)
    return ""


CROSS_INSTANCE_STATE = Path(__file__).resolve().parent / "cross_instance_copy_state.json"
_REPORTED_FOREIGN: set = set()


def cross_instance_copies(project_key: str) -> set:
    """Klucze zgłoszeń w projekcie, które skopiowano z INNEJ instancji Jiry
    (copy_project_cross_instance.py). Nie pochodzą z Asany, więc narzędzia
    porównujące Jirę z Asaną muszą je pomijać - inaczej wyglądałyby na nadmiarowe
    i np. resolve_relink_duplicates.py --include-orphans by je usunął."""
    if not CROSS_INSTANCE_STATE.exists():
        return set()
    data = json.loads(CROSS_INSTANCE_STATE.read_text(encoding="utf-8") or "{}")
    prefix = f"{project_key}-"
    return {e["target_key"] for entries in data.values() if isinstance(entries, dict)
            for e in entries.values() if isinstance(e, dict) and str(e.get("target_key", "")).startswith(prefix)}


def collect_jira(project_key: str, with_attachments: bool = False, with_details: bool = False) -> dict:
    """{klucz: {"type", "level", "summary", "parent"[, "attachments"][, "desc", "due", "assignee", "done"]}}
    dla wszystkich zgłoszeń projektu. with_details: opis, termin, przypisany, czy zakończone."""
    issues, token = {}, None
    fields = (["issuetype", "summary", "parent"] + (["attachment"] if with_attachments else [])
              + (["description", "duedate", "assignee", "status"] if with_details else []))
    while True:
        body = {"jql": f'project = "{project_key}" ORDER BY key', "fields": fields, "maxResults": 100}
        if token:
            body["nextPageToken"] = token
        resp = request_with_retry("POST", f"{JIRA_BASE_URL}/rest/api/3/search/jql", auth=JIRA_AUTH,
                                  json=body, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        for i in data.get("issues", []):
            it = i["fields"]["issuetype"]
            level = it.get("hierarchyLevel")
            if level is None:
                level = -1 if it.get("subtask") else (1 if it.get("name") == "Epic" else 0)
            issues[i["key"]] = {"type": it.get("name", ""), "level": level, "summary": i["fields"].get("summary") or "",
                                "parent": (i["fields"].get("parent") or {}).get("key")}
            if with_attachments:
                issues[i["key"]]["attachments"] = len(i["fields"].get("attachment") or [])
            if with_details:
                f = i["fields"]
                issues[i["key"]].update({
                    "desc": adf_plain(f.get("description")), "due": f.get("duedate"),
                    "assignee": (f.get("assignee") or {}).get("accountId"),
                    "done": ((f.get("status") or {}).get("statusCategory") or {}).get("key") == "done"})
        token = data.get("nextPageToken")
        if not token:
            foreign = cross_instance_copies(project_key) & set(issues)
            if foreign and project_key not in _REPORTED_FOREIGN:
                _REPORTED_FOREIGN.add(project_key)
                print(f"  Pomijam {len(foreign)} zgłoszeń skopiowanych z innej instancji Jiry "
                      f"(cross_instance_copy_state.json) - nie pochodzą z Asany.")
            return {k: v for k, v in issues.items() if k not in foreign}


def level_name(level: int) -> str:
    return {1: "Epic", 0: "Task", -1: "Sub-task"}.get(level, f"poziom {level}")


def asana_attachment_counts(task_gids) -> tuple:
    """(pliki do pobrania, linki zewnętrzne) - synchronizacja przenosi tylko pliki."""
    files = links = 0
    for gid in task_gids:
        for a in _asana_list(f"{ASANA_API}/tasks/{gid}/attachments", "download_url"):
            if a.get("download_url"):
                files += 1
            else:
                links += 1
    return files, links


def compare(project_key: str, asana_gid: str, state: dict, rows: list,
            details: bool = True, attachments: bool = False) -> dict:
    print(f"\n{'=' * 70}\n{project_key}\n{'=' * 70}")
    print("  Pobieram Asanę (z podzadaniami, rekurencyjnie)...")
    asana = collect_asana(asana_gid)
    print("  Pobieram Jirę...")
    jira = collect_jira(project_key, with_attachments=attachments)
    show = SHOW if details else 0
    pstate = {g: e for g, e in state.get(project_key, {}).items() if isinstance(e, dict) and e.get("jira_key")}

    depth_counts = Counter(a["depth"] for a in asana.values())
    jira_counts = Counter(level_name(j["level"]) for j in jira.values())
    print("\n  LICZBY")
    print(f"    Asana razem: {len(asana)}  ("
          + ", ".join(f"poziom {d}: {n}" for d, n in sorted(depth_counts.items())) + ")")
    print(f"    Jira  razem: {len(jira)}  ("
          + ", ".join(f"{t}: {n}" for t, n in sorted(jira_counts.items())) + ")")

    deleted = {g for g, e in pstate.items() if e.get("deleted_in_jira")}
    live_state = {g: e for g, e in pstate.items() if g not in deleted}
    mapped_keys = {e["jira_key"] for e in live_state.values()}

    not_migrated = [g for g in asana if g not in pstate]
    missing_in_jira = [g for g, e in live_state.items() if e["jira_key"] not in jira]
    jira_unmapped = [k for k in jira if k not in mapped_keys]
    gone_in_asana = [g for g in live_state if g not in asana]

    def section(title, items, fmt, category):
        if items or details:
            print(f"\n  {title}: {len(items)}" if details else f"  {title}: {len(items)}")
        for it in items[:show]:
            print("    " + fmt(it))
        if show and len(items) > show:
            print(f"    ... i jeszcze {len(items) - show} (w {REPORT_FILE.name})")
        for it in items:
            rows.append([project_key, category, *row_for(it, category)])

    def row_for(it, category):
        if category in ("niezmigrowane", "usuniete_w_jirze"):
            a = asana.get(it, {})
            return [it, (pstate.get(it) or {}).get("jira_key", ""), a.get("depth", ""), a.get("name", "")]
        if category in ("brak_w_jirze", "brak_w_asanie"):
            return [it, live_state[it]["jira_key"], asana.get(it, {}).get("depth", ""), asana.get(it, {}).get("name", "")]
        return ["", it, level_name(jira[it]["level"]), jira[it]["summary"]]

    by_depth = Counter(asana[g]["depth"] for g in not_migrated)
    section("W Asanie, NIE zmigrowane" + (f" (wg poziomu: {dict(sorted(by_depth.items()))})" if by_depth else ""),
            not_migrated, lambda g: f"gid {g} poziom {asana[g]['depth']}: {asana[g]['name'][:70]}", "niezmigrowane")
    section("W stanie, ale zgłoszenia NIE MA w Jirze", missing_in_jira,
            lambda g: f"{live_state[g]['jira_key']} (gid {g}): {asana.get(g, {}).get('name', '?')[:60]}", "brak_w_jirze")
    section("Oznaczone jako usunięte w Jirze (deleted_in_jira, celowo pomijane)", sorted(deleted),
            lambda g: f"{pstate[g]['jira_key']} (gid {g}): {asana.get(g, {}).get('name', '?')[:60]}", "usuniete_w_jirze")
    section("W Jirze, BEZ powiązania w stanie (ręcznie dodane albo duplikaty)", jira_unmapped,
            lambda k: f"{k} [{level_name(jira[k]['level'])}]: {jira[k]['summary'][:60]}", "tylko_w_jirze")
    section("W stanie, ale zadania NIE MA już w Asanie", gone_in_asana,
            lambda g: f"{live_state[g]['jira_key']} (gid {g})", "brak_w_asanie")

    ok = not (not_migrated or missing_in_jira or jira_unmapped or gone_in_asana)
    expected = len(asana) - len(not_migrated) - len(deleted)
    result = {"key": project_key, "ok": ok, "asana": len(asana), "jira": len(jira), "expected": expected,
              "not_migrated": len(not_migrated), "missing_in_jira": len(missing_in_jira), "deleted": len(deleted),
              "jira_unmapped": len(jira_unmapped), "gone_in_asana": len(gone_in_asana),
              "not_migrated_by_depth": dict(sorted(by_depth.items()))}
    if attachments:
        result["att_jira"] = sum(j.get("attachments", 0) for j in jira.values())
        result["att_asana"], result["att_links"] = asana_attachment_counts(asana)
        result["att_ok"] = result["att_jira"] == result["att_asana"]
        print(f"  Załączniki: Jira={result['att_jira']}, Asana (pliki)={result['att_asana']}"
              + (f", linki zewnętrzne (nie przenoszone)={result['att_links']}" if result["att_links"] else "")
              + f" -> {'OK' if result['att_ok'] else 'ROZBIEŻNOŚĆ'}")
    print(f"\n  WYNIK: {'ZGODNE' if ok else 'ROZBIEŻNOŚCI'}"
          f" | oczekiwane w Jirze (zmigrowane i nieusunięte): {expected}, jest: {len(jira)}")
    return result


def compare_top_level(project_key: str, asana_gid: str, state: dict, rows: list, details: bool = True) -> dict:
    """Szybkie porównanie samych zadań najwyższego poziomu: zadania z listy projektu
    w Asanie bez rodzica vs zgłoszenia w Jirze bez rodzica (Epiki i Taski najwyższego
    poziomu). Jedno zapytanie do Asany na 100 zadań - bez schodzenia w podzadania."""
    print(f"\n{'=' * 70}\n{project_key} (tylko najwyższy poziom)\n{'=' * 70}")
    listed = _asana_list(f"{ASANA_API}/projects/{asana_gid}/tasks", "name,parent.gid")
    top = {t["gid"]: t.get("name") or "" for t in listed if not (t.get("parent") or {}).get("gid")}
    jira = collect_jira(project_key)
    jira_top = {k for k, j in jira.items() if not j.get("parent")}
    pstate = {g: e for g, e in state.get(project_key, {}).items() if isinstance(e, dict) and e.get("jira_key")}
    key_of = {g: e["jira_key"] for g, e in pstate.items() if not e.get("deleted_in_jira")}
    gid_of = {k: g for g, k in key_of.items()}

    not_migrated = [g for g in top if g not in pstate]
    deleted = [g for g in top if pstate.get(g, {}).get("deleted_in_jira")]
    missing_in_jira = [g for g in top if g in key_of and key_of[g] not in jira]
    not_top_in_jira = [g for g in top if key_of.get(g) in jira and key_of[g] not in jira_top]
    extra = [k for k in jira_top if gid_of.get(k) not in top]
    # Zgłoszenie bez rodzica w Jirze, które w Asanie jest podzadaniem: najczęściej zadanie z
    # kilku projektów Asany albo samodzielny Task z błędu kolejności (audit_hierarchy.py).
    extra_mapped = [k for k in extra if k in gid_of]
    extra_unmapped = [k for k in extra if k not in gid_of]

    print(f"  Asana (najwyższy poziom): {len(top)} | Jira (bez rodzica): {len(jira_top)}")
    show = SHOW if details else 0
    for title, items, fmt, cat in (
        ("NIE zmigrowane", not_migrated, lambda g: f"gid {g}: {top[g][:70]}", "niezmigrowane"),
        ("W stanie, ale zgłoszenia NIE MA w Jirze", missing_in_jira, lambda g: f"{key_of[g]}: {top[g][:60]}", "brak_w_jirze"),
        ("Oznaczone jako usunięte w Jirze", deleted, lambda g: f"{pstate[g]['jira_key']}: {top[g][:60]}", "usuniete_w_jirze"),
        ("W Jirze NIE na najwyższym poziomie (ma rodzica)", not_top_in_jira,
         lambda g: f"{key_of[g]} pod {jira[key_of[g]]['parent']}: {top[g][:50]}", "nie_najwyzszy_w_jirze"),
        ("W Jirze bez rodzica, a w Asanie to podzadanie (sprawdź audit_hierarchy.py)", extra_mapped,
         lambda k: f"{k}: {jira[k]['summary'][:60]}", "podzadanie_jako_glowne"),
        ("W Jirze bez rodzica i bez powiązania (ręcznie dodane albo duplikaty)", extra_unmapped,
         lambda k: f"{k}: {jira[k]['summary'][:60]}", "tylko_w_jirze"),
    ):
        if items or details:
            print(f"  {title}: {len(items)}")
        for it in items[:show]:
            print("    " + fmt(it))
        if show and len(items) > show:
            print(f"    ... i jeszcze {len(items) - show} (w {REPORT_FILE.name})")
        for it in items:
            if cat in ("tylko_w_jirze", "podzadanie_jako_glowne"):
                rows.append([project_key, cat, gid_of.get(it, ""), it, level_name(jira[it]["level"]), jira[it]["summary"]])
            else:
                rows.append([project_key, cat, it, key_of.get(it, pstate.get(it, {}).get("jira_key", "")), 0, top[it]])

    ok = not (not_migrated or missing_in_jira or not_top_in_jira or extra)
    print(f"\n  WYNIK: {'ZGODNE' if ok else 'ROZBIEŻNOŚCI'}")
    return {"key": project_key, "ok": ok, "asana": len(top), "jira": len(jira_top), "expected": len(top) - len(deleted),
            "not_migrated": len(not_migrated), "missing_in_jira": len(missing_in_jira), "deleted": len(deleted),
            "jira_unmapped": len(extra), "gone_in_asana": len(not_top_in_jira), "not_migrated_by_depth": {}}


def sheet_projects() -> "tuple[dict, dict]":
    """({klucz: gid Asany}, {klucz: lista gid}) - drugi słownik: klucze z KILKOMA
    różnymi projektami Asany w arkuszu (do ręcznej weryfikacji)."""
    gids = {}
    for r in core.get_sheet_rows():
        if r.jira_key and r.asana_project_link:
            gid = core.extract_asana_project_gid(r.asana_project_link)
            if gid:
                gids.setdefault(r.jira_key.upper(), set()).add(gid)
    single = {k: next(iter(v)) for k, v in gids.items() if len(v) == 1}
    multi = {k: sorted(v) for k, v in gids.items() if len(v) > 1}
    return single, multi


def run_comparison(keys: list, attachments: bool = False, details: bool = True,
                   report_file: Path = REPORT_FILE, top_level: bool = False) -> list:
    """Porównanie wielu projektów - wspólne dla tego skryptu i count_migrated_tasks.py."""
    single, multi = sheet_projects()
    state = load_state()
    rows, results, skipped = [], [], []
    for n, key in enumerate(keys, 1):
        if key in multi:
            skipped.append((key, f"{len(multi[key])} różne projekty Asany w arkuszu: {multi[key]}"))
            print(f"\n[{n}/{len(keys)}] [POMINIĘTO] {key}: {skipped[-1][1]}")
            continue
        if key not in single:
            skipped.append((key, "brak wiersza z jira_key i asana_project_link w arkuszu"))
            print(f"\n[{n}/{len(keys)}] [POMINIĘTO] {key}: {skipped[-1][1]}")
            continue
        print(f"\n[{n}/{len(keys)}]", end="")
        try:
            if top_level:
                results.append(compare_top_level(key, single[key], state, rows, details=details))
            else:
                results.append(compare(key, single[key], state, rows, details=details, attachments=attachments))
        except Exception as exc:  # noqa: BLE001
            skipped.append((key, f"błąd: {exc}"))
            print(f"  [BŁĄD] {key}: {exc}")

    with open(report_file, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["projekt", "kategoria", "asana_gid", "jira_key", "poziom_lub_typ", "nazwa"])
        w.writerows(rows)

    bad = [r for r in results if not r["ok"]]
    print(f"\n{'=' * 70}\nPODSUMOWANIE ({len(results)} projektów{', tylko najwyższy poziom' if top_level else ''})")
    print(f"  Asana razem: {sum(r['asana'] for r in results)} | Jira razem: {sum(r['jira'] for r in results)}")
    print(f"  Zgodnych: {len(results) - len(bad)} | z rozbieżnościami: {len(bad)}"
          + (f" | pominiętych: {len(skipped)}" if skipped else ""))
    if attachments:
        att_bad = [r for r in results if not r["att_ok"]]
        print(f"  Załączniki: Jira={sum(r['att_jira'] for r in results)}, Asana (pliki)={sum(r['att_asana'] for r in results)}"
              f" | projektów z rozbieżnością załączników: {len(att_bad)}")
    if bad:
        print("\n  Rozbieżności (najwięcej niezmigrowanych najpierw):")
        for r in sorted(bad, key=lambda r: (-r["not_migrated"], -r["missing_in_jira"])):
            labels = (("not_migrated", "niezmigrowane"), ("missing_in_jira", "brak w Jirze"),
                      ("jira_unmapped", "w Jirze bez odpowiednika" if top_level else "w Jirze bez powiązania"),
                      ("gone_in_asana", "w Jirze nie na najwyższym poziomie" if top_level else "brak w Asanie"))
            parts = [f"{label} {r[k]}" for k, label in labels if r[k]]
            depth = f" (wg poziomu: {r['not_migrated_by_depth']})" if r["not_migrated"] else ""
            print(f"    {r['key']}: Asana={r['asana']}, Jira={r['jira']} | {', '.join(parts)}{depth}")
    for key, why in skipped:
        print(f"  [POMINIĘTO] {key}: {why}")
    print(f"Szczegóły (konkretne zadania): {report_file}")
    _report_to_pipeline(results, bad, skipped, top_level)
    return results


def _report_to_pipeline(results: list, bad: list, skipped: list, top_level: bool) -> None:
    """Podsumowanie dla run_full_pipeline.py (krok weryfikacji -> Slack). Tylko gdy
    skrypt jest uruchomiony przez orkiestrator, żeby nie zostawiać plików przy
    samodzielnym użyciu."""
    try:
        import pipeline_summary as ps
    except ImportError:
        return
    if not ps.is_pipeline_child():
        return
    for r in bad:
        parts = [f"{label} {r[k]}" for k, label in (("not_migrated", "niezmigrowane"), ("missing_in_jira", "brak w Jirze"),
                                                     ("jira_unmapped", "bez powiązania"), ("gone_in_asana", "brak w Asanie"))
                 if r[k]]
        ps.record_warning(r["key"], f"Asana={r['asana']}, Jira={r['jira']}", ", ".join(parts))
    for key, why in skipped:
        (ps.record_error if why.startswith("błąd") else ps.record_warning)(key, "porównanie", why)
    ps.write_shard_summary("compare_project_totals", None, {
        "projekty": len(results), "zgodne": len(results) - len(bad), "z_rozbieznosciami": len(bad),
        "niezmigrowane": sum(r["not_migrated"] for r in results),
        "bez_powiazania": sum(r["jira_unmapped"] for r in results),
        "brak_w_jirze": sum(r["missing_in_jira"] for r in results)})


def main() -> None:
    # Wypisywanie linia po linii: przy przechwyconym/przekierowanym wyjściu Python buforuje
    # blokami i postęp ("Pobieram Asanę", "sprawdzono 200 zadań") pojawiał się dopiero po
    # kilku minutach, wyglądając na zawieszenie.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project_keys", nargs="?", default=None, help="Klucz(e) projektu Jiry, po przecinku")
    parser.add_argument("--all", action="store_true", help="Wszystkie projekty z arkusza")
    parser.add_argument("--shard", default=None, help="Z --all: format K/N, ten proces bierze K-tą część")
    parser.add_argument("--attachments", action="store_true", help="Porównaj też załączniki (wolniej)")
    parser.add_argument("--top-level", action="store_true",
                        help="Tylko zadania najwyższego poziomu (szybko, bez schodzenia w podzadania)")
    parser.add_argument("--details", action="store_true", help="Wypisz konkretne zadania (domyślnie tak bez --all)")
    args = parser.parse_args()
    if args.all:
        keys = sorted(set(sheet_projects()[0]) | set(sheet_projects()[1]))
        keys = core.apply_shard(keys, args.shard)
    elif args.project_keys:
        keys = [k.strip().upper() for k in args.project_keys.split(",") if k.strip()]
    else:
        parser.error("podaj klucz(e) projektu albo --all")
    report = REPORT_FILE if not args.shard else REPORT_FILE.with_name(
        f"{REPORT_FILE.stem}_{args.shard.replace('/', '-')}{REPORT_FILE.suffix}")
    if args.top_level and args.attachments:
        parser.error("--top-level nie obsługuje --attachments")
    run_comparison(keys, attachments=args.attachments, details=args.details or not args.all, report_file=report,
                   top_level=args.top_level)

if __name__ == "__main__":
    main()
