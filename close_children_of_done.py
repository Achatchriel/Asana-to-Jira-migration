#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
close_children_of_done.py

Zamyka w Jirze wszystko, co leży pod zakończonym zadaniem.

Źródło prawdy (--source):
  asana (domyślnie) - zadanie UKOŃCZONE W ASANIE: ono samo i całe jego drzewo
          w Asanie (podzadania, podzadania podzadań, każdy poziom) mają być
          zakończone w Jirze. Drzewo brane z Asany, więc działa także dla
          poziomów spłaszczonych w Jirze (podzadanie podzadania jest w Jirze
          rodzeństwem swojego rodzica z Asany, a i tak zostanie zamknięte).
          Wymaga projektu w arkuszu (link do Asany).
  jira  - hierarchia w Jirze: wszystko pod zgłoszeniem w kategorii Done.

Docelowy status: ten sam, który synchronizacja nadaje ukończonym zadaniom
(albo --done-status). Przejście najkrótszą ścieżką przez workflow.

--also-asana oznacza te same zadania jako ukończone także w Asanie. Przy
projekcie nadal synchronizowanym jest to potrzebne, inaczej synchronizacja
może cofnąć zmianę (zadania z listy projektu dostają status według sekcji).
W trybie asana obejmuje też podzadania jeszcze niezmigrowane do Jiry.

Użycie:
    python close_children_of_done.py --project-keys PROJ2                       # raport
    python close_children_of_done.py --project-keys PROJ2 --apply --only-keys PROJ2-140
    python close_children_of_done.py --project-keys PROJ2 --apply --also-asana
    python close_children_of_done.py --project-keys PROJ2 --source jira          # według hierarchii Jiry
"""
import argparse
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import asana_jira_sync as ajs  # noqa: E402
from compare_project_totals import collect_asana, sheet_projects  # noqa: E402


def project_issues(project_key: str) -> dict:
    issues, token = {}, None
    while True:
        body = {"jql": f'project = "{project_key}" ORDER BY key',
                "fields": ["summary", "status", "parent", "issuetype"], "maxResults": 100}
        if token:
            body["nextPageToken"] = token
        resp = ajs.request_with_retry("POST", f"{ajs.JIRA_BASE_URL}/rest/api/3/search/jql", auth=ajs.JIRA_AUTH,
                                      json=body, timeout=ajs.REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        for i in data.get("issues", []):
            f = i["fields"]
            st = f.get("status") or {}
            issues[i["key"]] = {"summary": f.get("summary") or "", "status": st.get("name", ""),
                                "done": (st.get("statusCategory") or {}).get("key") == "done",
                                "parent": (f.get("parent") or {}).get("key"),
                                "type": (f.get("issuetype") or {}).get("name", "")}
        token = data.get("nextPageToken")
        if not token:
            return issues


def done_ancestor_jira(key: str, issues: dict) -> "str | None":
    """Najbliższy zakończony przodek w Jirze (w obrębie projektu)."""
    seen = {key}
    parent = issues[key]["parent"]
    while parent and parent in issues and parent not in seen:
        if issues[parent]["done"]:
            return parent
        seen.add(parent)
        parent = issues[parent]["parent"]
    return None


def completed_root_asana(gid: str, tree: dict) -> "str | None":
    """Najwyższy ukończony w Asanie przodek zadania (albo ono samo), inaczej None."""
    root, current, seen = None, gid, set()
    while current and current in tree and current not in seen:
        if tree[current].get("completed"):
            root = current
        seen.add(current)
        current = tree[current]["parent"]
    return root


def complete_in_asana(gid: str) -> None:
    resp = ajs.request_with_retry("PUT", f"{ajs.ASANA_API}/tasks/{gid}", headers=ajs.ASANA_HEADERS,
                                  json={"data": {"completed": True}}, timeout=ajs.REQUEST_TIMEOUT)
    if resp.status_code >= 300:
        raise RuntimeError(f"Asana {resp.status_code}: {resp.text[:200]}")


def plan_asana(project: str, asana_gid: str, issues: dict, state: dict) -> "tuple[dict, list, dict]":
    """(jira_key -> etykieta grupy, gid-y do ukończenia w Asanie, opisy grup)."""
    tree = collect_asana(asana_gid, with_completed=True)
    key_of = {g: e["jira_key"] for g, e in state.get(project, {}).items()
              if isinstance(e, dict) and e.get("jira_key") and not e.get("deleted_in_jira")}
    todo, asana_todo, groups = {}, [], {}
    for gid, t in tree.items():
        root = completed_root_asana(gid, tree)
        if not root:
            continue
        groups.setdefault(root, f"Asana: '{tree[root]['name'][:50]}'" + (f" ({key_of[root]})" if root in key_of else ""))
        if not t.get("completed"):
            asana_todo.append((gid, root))
        key = key_of.get(gid)
        if key and key in issues and not issues[key]["done"]:
            todo[key] = root
    return todo, asana_todo, groups


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project-keys", required=True, help="Klucze projektów po przecinku")
    parser.add_argument("--source", choices=["asana", "jira"], default="asana",
                        help="Co decyduje o 'zakończonym': ukończone w Asanie (domyślnie) albo status w Jirze")
    parser.add_argument("--done-status", default=None,
                        help="Nazwa statusu docelowego (domyślnie: jak dla ukończonych zadań w synchronizacji)")
    parser.add_argument("--apply", action="store_true", help="Wprowadź zmiany (domyślnie tylko raport)")
    parser.add_argument("--also-asana", action="store_true", help="Oznacz te same zadania jako ukończone także w Asanie")
    parser.add_argument("--only-keys", default=None, help="Z --apply: zmień tylko te zgłoszenia Jiry (po przecinku)")
    args = parser.parse_args()
    only = {k.strip().upper() for k in args.only_keys.split(",")} if args.only_keys else None
    state = ajs.load_state()
    single, multi = sheet_projects()

    import jira_sync as _core
    for project in _core.refuse_if_protected([k.strip().upper() for k in args.project_keys.split(",") if k.strip()],
                                              "close_children_of_done"):
        print(f"\n{'=' * 70}\n{project}\n{'=' * 70}")
        check = ajs.request_with_retry("GET", f"{ajs.JIRA_BASE_URL}/rest/api/3/project/{project}",
                                       auth=ajs.JIRA_AUTH, timeout=ajs.REQUEST_TIMEOUT)
        if check.status_code != 200:
            print(f"[BŁĄD] Projekt '{project}' nie istnieje albo brak do niego dostępu ({check.status_code}). "
                  f"Sprawdź klucz - pomijam.")
            continue
        if args.source == "asana" and project not in single:
            why = "kilka różnych projektów Asany w arkuszu" if project in multi else "brak w arkuszu linku do Asany"
            print(f"[BŁĄD] {project}: {why} - tryb asana wymaga jednego projektu Asany. Użyj --source jira albo popraw arkusz.")
            continue
        target_id = (ajs.get_status_id_by_name(project, args.done_status) if args.done_status
                     else ajs.resolve_target_status_id(project, None, ajs.load_section_status_map(), set(), completed=True))
        if not target_id:
            print(f"[BŁĄD] Nie znaleziono statusu docelowego w {project} - pomijam projekt.")
            continue

        issues = project_issues(project)
        gid_of = {e["jira_key"]: g for g, e in state.get(project, {}).items() if isinstance(e, dict) and e.get("jira_key")}
        if args.source == "asana":
            todo, asana_todo, groups = plan_asana(project, single[project], issues, state)
        else:
            todo = {k: a for k, a in ((k, done_ancestor_jira(k, issues)) for k, i in issues.items() if not i["done"]) if a}
            groups = {a: f"Jira: {a} [{issues[a]['status']}] '{issues[a]['summary'][:50]}'" for a in set(todo.values())}
            asana_todo = [(gid_of[k], a) for k, a in todo.items() if k in gid_of]

        by_group = defaultdict(list)
        for k, g in todo.items():
            by_group[g].append(k)
        not_migrated = len({g for g, _ in asana_todo} - set(gid_of.values())) if args.source == "asana" else 0
        print(f"Zgłoszeń w Jirze: {len(issues)} | do zamknięcia w Jirze: {len(todo)}"
              + (f" | do ukończenia w Asanie: {len(asana_todo)}" if args.source == "asana" else "")
              + (f" (w tym niezmigrowanych jeszcze do Jiry: {not_migrated})" if not_migrated else ""))
        for g, keys in sorted(by_group.items(), key=lambda x: -len(x[1])):
            print(f"  {groups[g]}: {len(keys)} do zamknięcia")
            for k in sorted(keys, key=lambda x: int(x.split('-')[-1]))[:5]:
                print(f"      {k} [{issues[k]['type']}, {issues[k]['status']}] {issues[k]['summary'][:60]}")
            if len(keys) > 5:
                print(f"      ... i jeszcze {len(keys) - 5}")
        in_sheet = project in single or project in multi
        if in_sheet and not args.also_asana and todo:
            print(f"\n  [UWAGA] {project} jest w arkuszu (nadal synchronizowany). Bez --also-asana część zmian może "
                  f"zostać cofnięta przy następnej synchronizacji.")

        if not args.apply or not (todo or (args.also_asana and asana_todo)):
            if todo:
                print("\n[TRYB RAPORTU] Nic nie zmieniono. Test: --apply --only-keys KLUCZ, potem --apply"
                      + (" --also-asana" if in_sheet else "") + ".")
            continue

        keys = [k for k in todo if only is None or k in only]
        closed, failed = set(), []
        for n, k in enumerate(sorted(keys, key=lambda x: int(x.split('-')[-1])), 1):
            try:
                if ajs.transition_jira_issue_to_status(k, target_id):
                    closed.add(k)
                    print(f"  [{n}/{len(keys)}] {k}: zakończone")
                else:
                    failed.append(k)
                    print(f"  [{n}/{len(keys)}] {k}: [BŁĄD] status docelowy nieosiągalny w workflow")
            except Exception as exc:  # noqa: BLE001
                failed.append(k)
                print(f"  [{n}/{len(keys)}] {k}: [BŁĄD] {exc}")

        asana_done = 0
        if args.also_asana:
            key_by_gid = {g: k for k, g in gid_of.items()}
            for gid, _ in asana_todo:
                key = key_by_gid.get(gid)
                # Ukończ w Asanie tylko to, co jest już zamknięte w Jirze albo jeszcze nie
                # zmigrowane (synchronizacja utworzy je wtedy od razu jako zakończone).
                # Nieudana zmiana w Jirze = Asana zostaje bez zmian, żeby systemy się nie rozjechały.
                if only is not None and key not in only:
                    continue
                if key and key in todo and key not in closed:
                    continue
                try:
                    complete_in_asana(gid)
                    asana_done += 1
                except Exception as exc:  # noqa: BLE001
                    print(f"      [OSTRZEŻENIE] Asana {gid}" + (f" ({key})" if key else "") + f": {exc}")
        print(f"\nZakończono w Jirze: {len(closed)}, błędy: {len(failed)}"
              + (f" | ukończono w Asanie: {asana_done}" if args.also_asana else ""))
        if failed:
            print(f"  Nieudane (np. przejście wymaga wypełnienia pola): {', '.join(failed[:20])}")


if __name__ == "__main__":
    main()
