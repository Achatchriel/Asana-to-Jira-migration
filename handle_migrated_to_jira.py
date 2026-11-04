#!/usr/bin/env python3
"""
handle_migrated_to_jira.py

Porządkuje zadania, które zespół ręcznie przeniósł do Jiry i oznaczył w Asanie
dopiskiem "(migriert zu Jira)" (także z literówką "mirgiert"). Dla nich źródłem
prawdy jest Jira, a synchronizacja (starsze wersje) utworzyła ich drugie kopie
albo aktualizowała zgłoszenia zespołu treścią z Asany.

Dla każdego takiego zadania i CAŁEGO jego poddrzewa w Asanie:
  - zgłoszenie zespołu = zgłoszenie bez powiązania w stanie, o tytule jak w
    Asanie bez dopisku (dla podzadań: pod zgłoszeniem zespołu rodzica),
  - KOPIA = zgłoszenie, na które dziś wskazuje stan,
  - USUŃ_KOPIĘ: jest zgłoszenie zespołu, a na kopii nikt nie pracował
    -> kopia usuwana, stan wskazuje na zgłoszenie zespołu,
  - PRZEPNIJ: jest zgłoszenie zespołu, kopii brak -> stan na zgłoszenie zespołu,
  - OZNACZ: brak osobnego zgłoszenia zespołu -> zostaje to, na które wskazuje stan,
  - DO_DECYZJI: na kopii też ktoś pracował (dwie "żywe" wersje).
We wszystkich przypadkach wpis w stanie dostaje "jira_master": synchronizacja
już nigdy go nie tworzy ani nie aktualizuje (to samo robi z dopiskiem w tytule).

Użycie:
    python handle_migrated_to_jira.py --project-key PROJ3           # plan
    python handle_migrated_to_jira.py --project-key PROJ3 --apply
"""
import argparse
import csv
import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import asana_jira_sync as ajs  # noqa: E402
from compare_project_totals import collect_asana, collect_jira, sheet_projects  # noqa: E402
from resolve_relink_duplicates import human_activity, jira_get  # noqa: E402

PLAN_FILE = Path(__file__).resolve().parent / "handle_migrated_to_jira_plan.csv"


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def clean_title(title: str) -> str:
    """Tytuł bez dopisku: "Agenda JF (migriert zu Jira)" -> "Agenda JF"."""
    t = ajs.MIGRATED_TITLE_RE.sub("", title or "")
    t = re.sub(r"[\(\[]\s*[\)\]]", "", t)  # puste nawiasy po usunięciu dopisku
    return re.sub(r"\s+", " ", t).strip(" -–:")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project-key", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--yes", action="store_true", help="Bez pytania o potwierdzenie usunięcia")
    parser.add_argument("--plan-file", default=None, help="Plik planu (domyślnie handle_migrated_to_jira_plan.csv)")
    args = parser.parse_args()
    key = args.project_key.strip().upper()
    import jira_sync as _core
    if not _core.refuse_if_protected([key], "handle_migrated_to_jira"):
        return
    global PLAN_FILE
    if args.plan_file:
        PLAN_FILE = Path(args.plan_file)
        PLAN_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not hasattr(ajs, "MIGRATED_TITLE_RE"):
        print("[BŁĄD] Starsza wersja asana_jira_sync.py (brak reguły 'migriert zu Jira'). Podmień ją. Nic nie zmieniono.")
        sys.exit(1)

    single, _ = sheet_projects()
    if key not in single:
        print(f"[BŁĄD] {key}: brak w arkuszu linku do Asany.")
        sys.exit(1)
    print(f"Pobieram Asanę i Jirę dla {key}...")
    asana = collect_asana(single[key])
    jira = collect_jira(key)
    state = ajs.load_state()
    pstate = {g: e for g, e in state.get(key, {}).items() if isinstance(e, dict)}
    linked = {e.get("jira_key") for e in pstate.values() if e.get("jira_key")}
    unlinked = {k for k in jira if k not in linked}

    children = defaultdict(list)
    for g, a in asana.items():
        if a["parent"]:
            children[a["parent"]].append(g)
    roots = [g for g, a in asana.items() if ajs.MIGRATED_TITLE_RE.search(a["name"])]
    subtree = []
    for r in roots:
        stack = [r]
        while stack:
            g = stack.pop()
            if g not in subtree:
                subtree.append(g)
                stack.extend(children[g])
    subtree.sort(key=lambda g: asana[g]["depth"])
    print(f"Zadań z dopiskiem 'migriert zu Jira': {len(roots)}, razem z poddrzewami: {len(subtree)}")

    me = jira_get("/rest/api/3/myself")["accountId"]
    team_of, plan, taken = {}, [], set()
    for g in subtree:
        a = asana[g]
        entry = pstate.get(g) or {}
        copy = entry.get("jira_key") if entry.get("jira_key") in jira else None
        want = norm(clean_title(a["name"]))
        cands = [k for k in unlinked - taken if norm(jira[k]["summary"]) == want]
        parent_team = team_of.get(a["parent"])
        if parent_team and len(cands) > 1:
            cands = [k for k in cands if jira[k]["parent"] == parent_team] or cands
        team = cands[0] if len(cands) == 1 else None
        row = {"gid": g, "title": a["name"], "copy": copy or "", "team": team or "", "action": "", "why": ""}
        if team:
            taken.add(team)
            team_of[g] = team
            if copy:
                activity = human_activity(copy, me)
                if activity:
                    row["action"], row["why"] = "DO_DECYZJI", "; ".join(activity[:3])
                else:
                    row["action"] = "USUŃ_KOPIĘ"
            else:
                row["action"] = "PRZEPNIJ"
        else:
            team_of[g] = copy
            row["action"] = "OZNACZ"
            if len(cands) > 1:
                row["why"] = f"kilka zgłoszeń zespołu o tym tytule: {', '.join(sorted(cands)[:5])}"
        plan.append(row)

    with open(PLAN_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["akcja", "kopia_synchronizacji", "zgloszenie_zespolu", "asana_gid", "tytul", "uwagi"])
        for r in plan:
            w.writerow([r["action"], r["copy"], r["team"], r["gid"], r["title"], r["why"]])
    counts = defaultdict(int)
    for r in plan:
        counts[r["action"]] += 1
    print("  " + ", ".join(f"{a}: {n}" for a, n in sorted(counts.items())))
    for r in plan[:15]:
        print(f"    {r['action']:<11} kopia {r['copy'] or '-':<14} zespół {r['team'] or '-':<14} | {r['title'][:55]}")
    rest = sorted(unlinked - taken, key=lambda k: int(k.rsplit("-", 1)[-1]))
    if rest:
        print(f"  Zgłoszenia bez powiązania, niezwiązane z dopiskiem (zostają nietknięte): {', '.join(rest)}")
    print(f"Pełny plan: {PLAN_FILE}")
    if not args.apply:
        print("\n[TRYB PLANU] Nic nie zmieniono. Uruchom z --apply.")
        return

    to_delete = [r for r in plan if r["action"] == "USUŃ_KOPIĘ"]
    if to_delete and not args.yes:
        if input(f"\nUsunąć NIEODWRACALNIE {len(to_delete)} kopii w {key}? Wpisz TAK: ").strip() != "TAK":
            print("Przerwano, nic nie zmieniono.")
            return
    with ajs.project_lock(key) as acquired:
        if not acquired:
            print(f"[BŁĄD] {key} jest przetwarzany przez inny proces.")
            sys.exit(1)
        backup = ajs.STATE_FILE.with_name(f"{ajs.STATE_FILE.stem}.backup_{datetime.now():%Y%m%d_%H%M%S}_migriert_{key}.json")
        shutil.copy2(ajs.STATE_FILE, backup)
        print(f"Kopia stanu: {backup}")
        state = ajs.load_state()
        project_state = state.setdefault(key, {})
        # 1. stan: wskazuje na zgłoszenie zespołu (albo zostaje), zawsze jira_master
        for r in plan:
            if r["action"] == "DO_DECYZJI":
                continue
            target = r["team"] or r["copy"]
            entry = dict(project_state.get(r["gid"]) or {})
            if target:
                entry["jira_key"] = target
            entry["jira_master"] = True
            entry.pop("deleted_in_jira", None)
            project_state[r["gid"]] = entry
        ajs.save_state({key: project_state})
        # 2. kopie: od najgłębszego poziomu
        deleted, failed = 0, []
        for r in sorted(to_delete, key=lambda r: jira[r["copy"]]["level"]):
            resp = ajs.request_with_retry("DELETE", f"{ajs.JIRA_BASE_URL}/rest/api/3/issue/{r['copy']}",
                                          auth=ajs.JIRA_AUTH, params={"deleteSubtasks": "false"},
                                          timeout=ajs.REQUEST_TIMEOUT)
            if resp.status_code in (200, 204):
                deleted += 1
                print(f"  usunięto kopię {r['copy']} (zostaje zgłoszenie zespołu {r['team']}) | {r['title'][:50]}")
            else:
                failed.append(r["copy"])
                print(f"  [BŁĄD] {r['copy']}: {resp.status_code} {resp.text[:150]}")
    print(f"\nOznaczono jako prowadzone w Jirze: {sum(1 for r in plan if r['action'] != 'DO_DECYZJI')}, "
          f"usunięto kopii: {deleted}, błędy: {len(failed)}")


if __name__ == "__main__":
    main()
