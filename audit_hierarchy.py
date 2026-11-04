#!/usr/bin/env python3
"""
audit_hierarchy.py

Audyt i naprawa hierarchii JUŻ zmigrowanych zadań: czy każde zgłoszenie
w Jirze stoi tam, gdzie wynika to z drzewa zadań w Asanie.

Główny cel: podzadania utworzone jako SAMODZIELNE Taski. Starsze wersje
asana_jira_sync.py przetwarzały listę zadań projektu w kolejności Asany,
a ta zawiera też podzadania dodane do projektu. Gdy podzadanie stało na
liście przed rodzicem, powstawało jako samodzielny Task, a rodzic potem
zastawał istniejący wpis i go nie przepinał.

Oczekiwane miejsce wyliczane z drzewa Asany od góry (te same reguły, co
asana_jira_sync.py):
  - zadanie najwyższego poziomu -> Task (albo Epic, jeśli już nim jest),
  - dziecko Epika               -> Task pod Epikiem,
  - dziecko Taska               -> Sub-task pod tym Taskiem,
  - dziecko Sub-taska           -> Sub-task pod rodzicem tego Sub-taska (spłaszczenie).
Reguły dają poprawny wynik także dla napraw z revert_false_epics.py
i resolve_deep_chains.py, więc ich nie zgłasza.

Statusy:
  OK
  SAMODZIELNY_TASK     - powinien być Sub-taskiem, jest samodzielnym Taskiem -> --apply
                         (jego ewentualne Sub-taski przenoszone razem z nim pod nowego rodzica)
  ZLY_RODZIC_SUBTASK   - Sub-task pod złym rodzicem                          -> --apply
  TASK_BEZ_EPIKA       - powinien być Taskiem pod Epikiem, nie ma rodzica    -> --apply
  SUBTASK_POD_EPIKIEM  - powinien być Taskiem pod Epikiem, jest Sub-taskiem pod nim -> --apply
                         (Jira to dopuszcza, ale gubi poziom Task; powstawało, gdy flaga
                         is_epic Epika była nadpisana, a jego dzieci istniały już wcześniej)
  WYMAGA_PRZEGLADU     - każda inna niezgodność (typ, nietypowy rodzic)      -> ręcznie
  RODZIC_NIEZMIGROWANY - rodzica z Asany nie ma w Jirze (niezmigrowany - dociągnie go
                         synchronizacja - albo oznaczony jako usunięty)
Zadania niezmigrowane w ogóle nie są tu poprawiane - to robi asana_jira_sync.py.

Użycie:
    python audit_hierarchy.py --project-keys MCPSIG2,AKMPUN        # raport
    python audit_hierarchy.py                                       # wszystkie projekty z arkusza
    python audit_hierarchy.py --shard 1/3                           # równolegle
    python audit_hierarchy.py --project-keys MCPSIG2 --apply --only-keys MCPSIG2-123   # test na jednym
    python audit_hierarchy.py --project-keys MCPSIG2 --apply        # naprawa
    python audit_hierarchy.py --full                                # też projekty bez podzadań na liście projektu

Po naprawie: python refresh_state_flags.py --project-keys ... --apply
"""
import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core  # noqa: E402
import convert_to_epic as cte  # noqa: E402
import revert_false_epics as rfe  # noqa: E402
from asana_jira_sync import (  # noqa: E402
    ASANA_API, JIRA_AUTH, JIRA_BASE_URL, REQUEST_TIMEOUT, load_state, request_with_retry,
)
from compare_project_totals import _asana_list, collect_asana  # noqa: E402

REPORT_FILE = Path(__file__).resolve().parent / "audit_hierarchy_report.csv"
# Kolejność napraw MA znaczenie:
#  1. SUBTASK_POD_EPIKIEM najpierw - te zgłoszenia stają się Taskami, pod które
#     kroki 2-3 przenoszą Sub-taski (Sub-task nie może wisieć pod Sub-taskiem),
#  2. ZLY_RODZIC_SUBTASK przed SAMODZIELNY_TASK - wyprowadza dzieci z zadań, które
#     za chwilę staną się Sub-taskami (Jira nie zamieni w Sub-task zgłoszenia z dziećmi).
FIXABLE = ("SUBTASK_POD_EPIKIEM", "ZLY_RODZIC_SUBTASK", "SAMODZIELNY_TASK", "TASK_BEZ_EPIKA")
LEVEL_NAME = {1: "Epic", 0: "Task", -1: "Sub-task"}


def has_nested_in_project(project_gid: str) -> bool:
    """Tanie sprawdzenie (1 zapytanie na 100 zadań): czy lista projektu zawiera
    podzadania, których rodzic też jest w projekcie. Tylko wtedy mógł wystąpić
    błąd kolejności."""
    tasks = _asana_list(f"{ASANA_API}/projects/{project_gid}/tasks", "parent.gid")
    in_project = {t["gid"] for t in tasks}
    return any((t.get("parent") or {}).get("gid") in in_project for t in tasks)


def collect_jira_tree(project_key: str) -> dict:
    """{klucz: {"level", "type", "parent", "summary"}} wszystkich zgłoszeń projektu."""
    issues, token = {}, None
    while True:
        body = {"jql": f'project = "{project_key}" ORDER BY key',
                "fields": ["issuetype", "parent", "summary"], "maxResults": 100}
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
            parent = i["fields"].get("parent")
            issues[i["key"]] = {"level": level, "type": it.get("name", ""),
                                "parent": parent["key"] if parent else None,
                                "summary": i["fields"].get("summary") or ""}
        token = data.get("nextPageToken")
        if not token:
            return issues


def audit_project(project_key: str, asana: dict, jira: dict, pstate: dict) -> list:
    """Zwraca listę wyników: dict(gid, key, name, status, exp_level, exp_parent,
    level, parent, children)."""
    key_of = {g: e["jira_key"] for g, e in pstate.items() if not e.get("deleted_in_jira")}
    children_of = {}
    for k, j in jira.items():
        if j["parent"]:
            children_of.setdefault(j["parent"], []).append(k)

    expected = {}  # gid -> (level, parent_key)  - oczekiwane miejsce w Jirze
    results = []
    for gid in sorted(asana, key=lambda g: asana[g]["depth"]):
        a = asana[gid]
        key = key_of.get(gid)
        if not key or key not in jira:
            continue  # niezmigrowane / brak w Jirze - poza zakresem tego audytu
        live = jira[key]
        parent_gid = a["parent"]
        if parent_gid is None:
            exp = (live["level"] if live["level"] in (0, 1) else 0, None)
        elif parent_gid not in expected:
            results.append(_res(gid, key, a, live, "RODZIC_NIEZMIGROWANY", None, children_of))
            continue
        else:
            p_level, p_parent = expected[parent_gid]
            p_key = key_of[parent_gid]
            if p_level == 1:
                exp = (0, p_key)
            elif p_level == 0:
                exp = (-1, p_key)
            else:
                exp = (-1, p_parent)
        expected[gid] = exp

        exp_level, exp_parent = exp
        if live["level"] == exp_level and live["parent"] == exp_parent:
            status = "OK"
        elif exp_level == -1 and live["level"] == 0 and live["parent"] is None:
            status = "SAMODZIELNY_TASK"
        elif exp_level == -1 and live["level"] == -1:
            status = "ZLY_RODZIC_SUBTASK"
        elif exp_level == 0 and live["level"] == 0 and live["parent"] is None and exp_parent:
            status = "TASK_BEZ_EPIKA"
        elif exp_level == 0 and live["level"] == -1 and live["parent"] == exp_parent:
            status = "SUBTASK_POD_EPIKIEM"
        else:
            status = "WYMAGA_PRZEGLADU"
        results.append(_res(gid, key, a, live, status, exp, children_of))
    return results


def _res(gid, key, a, live, status, exp, children_of) -> dict:
    return {"gid": gid, "key": key, "name": a["name"], "status": status,
            "exp_level": exp[0] if exp else None, "exp_parent": exp[1] if exp else None,
            "level": live["level"], "parent": live["parent"], "children": children_of.get(key, [])}


def _live(key: str) -> "dict | None":
    return rfe.get_live_issue_state(key)


def apply_fix(r: dict, dry_run: bool) -> str:
    """Naprawa jednego wpisu. Przed zmianą stan sprawdzany ponownie na żywo -
    poprzednie naprawy (np. przeniesienie dzieci) mogły go już zmienić."""
    key, target = r["key"], r["exp_parent"]
    live = _live(key)
    if live is None:
        return "pominięto: zgłoszenia już nie ma"
    if live["parent"] == target and (live["is_subtask"] == (r["exp_level"] == -1)):
        return "już poprawne"

    if r["status"] == "TASK_BEZ_EPIKA":
        rfe.set_parent_only(key, target, dry_run)
        return f"ustawiono rodzica {target}"

    if r["status"] == "SUBTASK_POD_EPIKIEM":
        # ta sama, sprawdzona na żywo ścieżka co w kaskadzie revert_false_epics.py
        task_type = cte.get_non_subtask_issue_type_name(key.split("-")[0])
        cte.convert_subtask_to_task(key, task_type, target, dry_run)
        return f"Sub-task -> {task_type} pod Epikiem {target}"

    if r["status"] == "SAMODZIELNY_TASK":
        # Sub-taski tego zgłoszenia najpierw pod nowego rodzica (Jira nie zamieni
        # w Sub-task zgłoszenia, które ma własne Sub-taski).
        moved = []
        for child in (c for c in _children_live(key)):
            cte.bulk_convert_subtask_and_reparent(child, target, dry_run)
            moved.append(child)
        cte.bulk_convert_subtask_and_reparent(key, target, dry_run)
        return f"Sub-task pod {target}" + (f", przeniesione dzieci: {', '.join(moved)}" if moved else "")

    # ZLY_RODZIC_SUBTASK
    cte.bulk_convert_subtask_and_reparent(key, target, dry_run)
    return f"przepięto pod {target}"


def _children_live(key: str) -> list:
    return [k for k, _, _ in rfe.get_children(key)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project-keys", default=None, help="Klucze po przecinku (domyślnie: wszystkie z arkusza).")
    parser.add_argument("--shard", default=None, help="Format K/N - ten proces bierze K-tą część projektów.")
    parser.add_argument("--full", action="store_true",
                        help="Sprawdzaj też projekty bez podzadań na liście projektu (dłużej).")
    parser.add_argument("--apply", action="store_true",
                        help="Napraw SUBTASK_POD_EPIKIEM, ZLY_RODZIC_SUBTASK, SAMODZIELNY_TASK, TASK_BEZ_EPIKA.")
    parser.add_argument("--only-keys", default=None, help="Z --apply: napraw tylko te klucze Jiry (po przecinku).")
    parser.add_argument("--dry-run", action="store_true", help="Z --apply: pokaż operacje bez zapisu.")
    args = parser.parse_args()

    rows_by_key = {r.jira_key: r for r in core.get_sheet_rows() if r.jira_key and r.asana_project_link}
    keys = ([k.strip().upper() for k in args.project_keys.split(",") if k.strip()]
            if args.project_keys else sorted(rows_by_key))
    keys = core.refuse_if_protected(core.apply_shard(keys, args.shard), "audit_hierarchy")
    only = {k.strip().upper() for k in args.only_keys.split(",")} if args.only_keys else None
    state = load_state()

    all_results, skipped_projects = [], 0
    for n, pk in enumerate(keys, 1):
        row = rows_by_key.get(pk)
        if not row or pk not in state:
            continue
        gid = core.extract_asana_project_gid(row.asana_project_link)
        print(f"\n[{n}/{len(keys)}] {pk}")
        try:
            if not args.full and not has_nested_in_project(gid):
                print("  brak podzadań na liście projektu - błąd kolejności niemożliwy, pomijam (użyj --full)")
                skipped_projects += 1
                continue
            asana = collect_asana(gid)
            jira = collect_jira_tree(pk)
        except Exception as exc:  # noqa: BLE001
            print(f"  [BŁĄD] nie udało się pobrać danych: {exc}")
            continue
        pstate = {g: e for g, e in state[pk].items() if isinstance(e, dict) and e.get("jira_key")}
        results = audit_project(pk, asana, jira, pstate)
        for r in results:
            r["project"] = pk
        c = Counter(r["status"] for r in results)
        print("  " + ", ".join(f"{s}: {v}" for s, v in sorted(c.items())))
        for r in [r for r in results if r["status"] not in ("OK", "RODZIC_NIEZMIGROWANY")][:10]:
            print(f"    {r['key']} [{r['status']}] jest: {LEVEL_NAME.get(r['level'])} pod {r['parent']}, "
                  f"powinien: {LEVEL_NAME.get(r['exp_level'])} pod {r['exp_parent']}"
                  + (f", dzieci: {len(r['children'])}" if r["children"] else "") + f" | {r['name'][:50]}")
        all_results.extend(results)

    total = Counter(r["status"] for r in all_results)
    print("\n" + "=" * 70)
    print("PODSUMOWANIE" + (f" (pominięte projekty bez podzadań na liście: {skipped_projects})" if skipped_projects else ""))
    for s, v in sorted(total.items()):
        print(f"  {s}: {v}")
    per_project = Counter(r["project"] for r in all_results if r["status"] in FIXABLE + ("WYMAGA_PRZEGLADU",))
    if per_project:
        print("  Projekty z niezgodnościami: " + ", ".join(f"{p} ({v})" for p, v in per_project.most_common()))
    print("=" * 70)

    with open(REPORT_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["projekt", "jira_key", "status", "jest_typ", "jest_rodzic", "powinien_typ",
                    "powinien_rodzic", "dzieci_w_jirze", "asana_gid", "nazwa"])
        for r in all_results:
            if r["status"] == "OK":
                continue
            w.writerow([r["project"], r["key"], r["status"], LEVEL_NAME.get(r["level"]), r["parent"],
                        LEVEL_NAME.get(r["exp_level"]), r["exp_parent"], ";".join(r["children"]), r["gid"], r["name"]])
    print(f"Pełna lista niezgodności: {REPORT_FILE}")

    if not args.apply:
        print("\n[TRYB RAPORTU] Nic nie zmieniono. Sprawdź raport, przetestuj na jednym kluczu "
              "(--apply --only-keys KLUCZ), potem --apply.")
        return

    todo = [r for status in FIXABLE for r in all_results
            if r["status"] == status and (only is None or r["key"] in only)]
    print(f"\nNAPRAWA{' [DRY-RUN]' if args.dry_run else ''}: {len(todo)} zgłoszeń")
    fixed, failed = 0, []
    for i, r in enumerate(todo, 1):
        try:
            print(f"  [{i}/{len(todo)}] {r['key']} ({r['status']}): {apply_fix(r, args.dry_run)}")
            fixed += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  [{i}/{len(todo)}] {r['key']} [BŁĄD] {exc}")
            failed.append((r["key"], str(exc)))
    print(f"\nNaprawiono: {fixed}, błędy: {len(failed)}")
    for k, e in failed:
        print(f"  {k}: {e[:200]}")
    if fixed and not args.dry_run:
        touched = sorted({r["project"] for r in todo})
        print(f"\nTeraz odśwież flagi w stanie:\n  python refresh_state_flags.py --project-keys {','.join(touched)} --apply")


if __name__ == "__main__":
    main()
