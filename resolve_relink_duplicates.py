#!/usr/bin/env python3
"""
resolve_relink_duplicates.py

Rozwiązuje pary DUPLIKAT z raportu relink_state.py: jedno zadanie z Asany ma
w Jirze dwa zgłoszenia - starsze (zwykle bez powiązania w stanie) i nowsze
(utworzone ponownie przez synchronizację, bo wpis w stanie zginął).

Dla każdego zadania z Asany (wszystkie jego kopie RAZEM - bywa ich więcej niż dwie):
  - ZOSTAJE najstarsze zgłoszenie (data utworzenia): ma historię i ewentualną
    pracę zespołu. Wpis w stanie jest na nie przepinany (z linkiem do Asany
    i listą komentarzy/załączników z Asany, które już w nim są),
  - USUWANE są pozostałe kopie, ale tylko gdy nikt poza kontem synchronizacji
    nie dodał w nich komentarza ani nie zmienił niczego w historii. Inaczej
    całe zadanie trafia do raportu jako DO_DECYZJI, nietknięte.
  - podzadania usuwanej kopii, których nie ma na liście do usunięcia, są
    PRZENOSZONE pod zachowywane zgłoszenie (albo pod jego rodzica, jeśli samo
    jest Sub-taskiem) - ich wpisy w stanie się nie zmieniają.
Pary NADMIAROWA_KOPIA (kolejna kopia szablonu, której treść pasuje do kilku
już powiązanych zadań): zgłoszenie jest usuwane bez żadnej zmiany w stanie, po
tym samym sprawdzeniu pracy zespołu i podzadań.
--include-orphans: dodatkowo usuwa zgłoszenia BRAK_W_ASANIE (bez odpowiednika,
np. stare kopie zadań przemianowanych w Asanie na "... (migriert zu Jira)").
Tylko gdy raport relink_state.py potwierdza, że KAŻDE zadanie z Asany ma już
swoje zgłoszenie (zero NIEZMIGROWANE, NIEJEDNOZNACZNE i niezapisanych dopasowań) -
wtedy takie zgłoszenie na pewno jest nadmiarem.
Kopia (nadmiarowa albo osierocona), która ma podzadania powiązane z Asaną,
trafia do DO_DECYZJI - nie ma jednoznacznego miejsca, dokąd je przenieść.
Usuwanie od najgłębszego poziomu w górę (Sub-taski przed Taskami).
Usunięcie w Jira Cloud jest NIEODWRACALNE - --apply pyta o potwierdzenie.

Użycie:
    python resolve_relink_duplicates.py --project-key PROJ3                 # plan (nic nie zmienia)
    python resolve_relink_duplicates.py --project-key PROJ3 --apply --limit 3   # test na 3 parach
    python resolve_relink_duplicates.py --project-key PROJ3 --apply
"""
import argparse
import csv
import shutil
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import asana_jira_sync as ajs  # noqa: E402
import convert_to_epic as cte  # noqa: E402  - sprawdzone przepinanie podzadań (Bulk Move)
from relink_state import REPORT_FILE as RELINK_REPORT, already_synced  # noqa: E402

PLAN_FILE = Path(__file__).resolve().parent / "resolve_relink_duplicates_plan.csv"


def jira_get(path: str, **params) -> dict:
    r = ajs.request_with_retry("GET", f"{ajs.JIRA_BASE_URL}{path}", auth=ajs.JIRA_AUTH, params=params,
                               timeout=ajs.REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


def issue_info(key: str) -> "dict | None":
    try:
        f = jira_get(f"/rest/api/3/issue/{key}", fields="created,issuetype,parent,summary,subtasks")["fields"]
    except Exception:  # noqa: BLE001
        return None
    it = f.get("issuetype") or {}
    level = it.get("hierarchyLevel")
    if level is None:
        level = -1 if it.get("subtask") else (1 if it.get("name") == "Epic" else 0)
    return {"created": f.get("created") or "", "level": level, "parent": (f.get("parent") or {}).get("key"),
            "summary": f.get("summary") or "", "subtasks": [s["key"] for s in f.get("subtasks") or []]}


def human_activity(key: str, me: str) -> list:
    """Opisy aktywności innych osób niż konto synchronizacji (komentarze, historia)."""
    found = []
    start = 0
    while True:
        data = jira_get(f"/rest/api/3/issue/{key}/comment", startAt=start, maxResults=100)
        for c in data.get("comments", []):
            author = (c.get("author") or {})
            if author.get("accountId") and author["accountId"] != me:
                found.append(f"komentarz: {author.get('displayName', '?')}")
        start += len(data.get("comments", []))
        if not data.get("comments") or start >= data.get("total", 0):
            break
    start = 0
    while True:
        data = jira_get(f"/rest/api/3/issue/{key}/changelog", startAt=start, maxResults=100)
        for h in data.get("values", []):
            author = (h.get("author") or {})
            if author.get("accountId") and author["accountId"] != me:
                fields = ", ".join(i.get("field", "?") for i in h.get("items", []))
                found.append(f"zmiana ({fields}): {author.get('displayName', '?')}")
        start += len(data.get("values", []))
        if data.get("isLast", True) or not data.get("values"):
            break
    return found


def require_current_sync_module() -> None:
    """Narzędzie korzysta z funkcji z najnowszego asana_jira_sync.py - starsza wersja
    w folderze wyłożyłaby się w trakcie pracy, więc sprawdzamy to na starcie."""
    missing = [n for n in ("ensure_asana_link", "load_sync_exclude", "_STATE_SNAPSHOT") if not hasattr(ajs, n)]
    if missing:
        print(f"[BŁĄD] W folderze jest starsza wersja asana_jira_sync.py (brak: {', '.join(missing)}). "
              f"Podmień ją na najnowszą i uruchom ponownie. Nic nie zostało zmienione.")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project-key", required=True)
    parser.add_argument("--apply", action="store_true", help="Przepnij stan i usuń duplikaty (domyślnie tylko plan)")
    parser.add_argument("--limit", type=int, default=None, help="Z --apply: tylko pierwsze N par (test)")
    parser.add_argument("--yes", action="store_true", help="Bez pytania o potwierdzenie usunięcia")
    parser.add_argument("--relink-report", default=None, help="Raport relink_state.py (domyślnie relink_state_report.csv)")
    parser.add_argument("--plan-file", default=None, help="Plik planu (domyślnie resolve_relink_duplicates_plan.csv)")
    parser.add_argument("--include-orphans", action="store_true",
                        help="Usuń też BRAK_W_ASANIE (tylko gdy każde zadanie z Asany ma już zgłoszenie)")
    args = parser.parse_args()
    key = args.project_key.strip().upper()
    import jira_sync as _core
    if not _core.refuse_if_protected([key], "resolve_relink_duplicates"):
        return
    require_current_sync_module()
    global RELINK_REPORT, PLAN_FILE
    if args.relink_report:
        RELINK_REPORT = Path(args.relink_report)
    if args.plan_file:
        PLAN_FILE = Path(args.plan_file)
        PLAN_FILE.parent.mkdir(parents=True, exist_ok=True)

    all_rows = [r for r in csv.DictReader(open(RELINK_REPORT, encoding="utf-8"))
                if (r["jira_key"] or "").startswith(f"{key}-") or r["kategoria"] == "NIEZMIGROWANE"]
    wanted = {"DUPLIKAT", "NADMIAROWA_KOPIA"}
    if args.include_orphans:
        blocking = [c for c in ("NIEZMIGROWANE", "NIEJEDNOZNACZNE", "PEWNE", "PODOBNE", "KOLEJNOSC", "TRESC", "LINK")
                    if any(r["kategoria"] == c for r in all_rows)]
        if blocking:
            print(f"[BŁĄD] --include-orphans wymaga, żeby każde zadanie z Asany miało już zgłoszenie, a raport "
                  f"relink_state.py ma jeszcze: {', '.join(blocking)}. Najpierw je rozwiąż. Nic nie zmieniono.")
            sys.exit(1)
        wanted.add("BRAK_W_ASANIE")
    rows = [r for r in all_rows if r["kategoria"] in wanted and r["jira_key"].startswith(f"{key}-")]
    if not rows:
        print(f"Brak par DUPLIKAT dla {key} w {RELINK_REPORT.name} - uruchom najpierw relink_state.py --project-key {key}")
        return
    me = jira_get("/rest/api/3/myself")["accountId"]
    state = ajs.load_state()
    pstate = state.get(key, {})

    print(f"Sprawdzam {len(rows)} wierszy raportu (historia i komentarze zgłoszeń)...")
    plan, groups = [], {}
    referenced = {e.get("jira_key") for e in pstate.values() if isinstance(e, dict)}
    for r in rows:
        if r["kategoria"] in ("NADMIAROWA_KOPIA", "BRAK_W_ASANIE"):
            extra = r["jira_key"]
            info = issue_info(extra)
            if extra in referenced or not info:
                plan.append({"gid": "", "keep": "", "removes": [], "action": "POMINIĘTE",
                             "why": "zgłoszenie jest już powiązane albo nie istnieje", "title": r["asana_tytul"]})
            else:
                plan.append({"gid": "", "keep": r["drugie_zgloszenie_lub_rodzic"], "removes": [extra], "action": "",
                             "why": "", "title": r["asana_tytul"] or info["summary"], "info": {extra: info},
                             "extra_copy": True, "orphan": r["kategoria"] == "BRAK_W_ASANIE"})
            continue
        g = groups.setdefault(r["asana_gid"], {"linked": r["drugie_zgloszenie_lub_rodzic"], "copies": set(),
                                                "title": r["asana_tytul"]})
        g["copies"].add(r["jira_key"])

    for n, (gid, g) in enumerate(groups.items(), 1):
        if (pstate.get(gid) or {}).get("jira_key") != g["linked"]:
            plan.append({"gid": gid, "keep": "", "removes": [], "action": "POMINIĘTE",
                         "why": "stan już nie wskazuje na tę parę (zmieniony od raportu)", "title": g["title"]})
            continue
        keys = sorted(g["copies"] | {g["linked"]})
        info = {k: issue_info(k) for k in keys}
        if any(v is None for v in info.values()):
            plan.append({"gid": gid, "keep": "", "removes": [], "action": "POMINIĘTE",
                         "why": "któreś ze zgłoszeń już nie istnieje", "title": g["title"]})
            continue
        keep = min(keys, key=lambda k: (info[k]["created"], int(k.rsplit("-", 1)[-1])))
        plan.append({"gid": gid, "keep": keep, "removes": [k for k in keys if k != keep], "action": "",
                     "why": "", "title": g["title"], "info": info})
        if n % 25 == 0:
            print(f"  ...{n}/{len(groups)}")

    # Blokada: zgłoszeń skopiowanych z innej instancji Jiry nigdy nie usuwamy
    from compare_project_totals import cross_instance_copies
    foreign = cross_instance_copies(key)
    for p in plan:
        hit = [k for k in p["removes"] if k in foreign]
        if hit and not p["action"]:
            p["action"], p["why"] = "POMINIĘTE", f"skopiowane z innej instancji Jiry: {', '.join(hit)}"
    removable = {k for p in plan for k in p["removes"]}
    for p in plan:
        if p["action"]:
            continue
        activity = [a for k in p["removes"] for a in human_activity(k, me)]
        if activity:
            p["action"] = "DO_DECYZJI"
            p["why"] = "; ".join(activity[:3])
            continue
        if p.get("extra_copy"):
            foreign = [c for k in p["removes"] for c in p["info"][k]["subtasks"] if c not in removable]
            if foreign:
                p["action"] = "DO_DECYZJI"
                p["why"] = f"ma podzadania powiązane z Asaną: {', '.join(foreign[:5])}"
                continue
            p["move_children"] = []
            p["action"] = "USUŃ_OSIEROCONE" if p.get("orphan") else "USUŃ_NADMIAROWĄ"
            continue
        keep_info = p["info"].get(p["keep"]) or issue_info(p["keep"])
        anchor = p["keep"] if not keep_info or keep_info["level"] >= 0 else keep_info["parent"]
        p["move_children"] = [(c, anchor) for k in p["removes"] for c in p["info"][k]["subtasks"] if c not in removable]
        p["action"] = "USUŃ_KOPIE" + (" + PRZEPNIJ_STAN" if p["keep"] != pstate[p["gid"]]["jira_key"] else "")
        if p["move_children"]:
            p["action"] += " + PRZENIEŚ_PODZADANIA"
            p["why"] = f"{len(p['move_children'])} podzadań pod {anchor}: " + ", ".join(c for c, _ in p["move_children"][:5])

    with open(PLAN_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["akcja", "zostaje", "usuwane", "asana_gid", "tytul", "uwagi"])
        for p in plan:
            w.writerow([p["action"], p["keep"], ";".join(p["removes"]), p["gid"], p["title"], p["why"]])
    todo = [p for p in plan if p["action"].startswith("USUŃ")]
    print(f"\nZadań: {len(plan)} | do usunięcia kopii: {len(todo)} "
          f"(zgłoszeń: {sum(len(p['removes']) for p in todo)}, z przepięciem stanu: {sum('PRZEPNIJ' in p['action'] for p in todo)}, "
          f"nadmiarowe kopie: {sum(p['action'].startswith('USUŃ_NADMIAROWĄ') for p in todo)}, "
          f"osierocone: {sum(p['action'] == 'USUŃ_OSIEROCONE' for p in todo)}, "
          f"z przeniesieniem podzadań: {sum('PRZENIEŚ' in p['action'] for p in todo)}) | "
          f"do decyzji: {sum(p['action'] == 'DO_DECYZJI' for p in plan)} | pominięte: {sum(p['action'] == 'POMINIĘTE' for p in plan)}")
    for p in [p for p in plan if len(p["removes"]) > 1][:5]:
        print(f"  kilka kopii: zostaje {p['keep']}, usuwane {', '.join(p['removes'])} | {p['title'][:50]}")
    for p in [p for p in plan if p["action"] == "DO_DECYZJI"][:10]:
        print(f"  DO_DECYZJI: {', '.join(p['removes'])} vs {p['keep']} | {p['why'][:100]}")
    print(f"Pełny plan: {PLAN_FILE}")
    if not args.apply:
        print("\n[TRYB PLANU] Nic nie zmieniono. Test: --apply --limit 3, potem --apply.")
        return

    todo = todo[: args.limit] if args.limit else todo
    if args.limit:
        print(f"[--limit] Tylko {len(todo)} pierwszych zadań.")
    if not args.yes:
        answer = input(f"\nUsunąć NIEODWRACALNIE {len(todo)} zgłoszeń w {key}? Wpisz TAK: ").strip()
        if answer != "TAK":
            print("Przerwano, nic nie zmieniono.")
            return

    with ajs.project_lock(key) as acquired:
        if not acquired:
            print(f"[BŁĄD] {key} jest przetwarzany przez inny proces - przerwij go i spróbuj ponownie.")
            sys.exit(1)
        backup = ajs.STATE_FILE.with_name(f"{ajs.STATE_FILE.stem}.backup_{datetime.now():%Y%m%d_%H%M%S}_dupl_{key}.json")
        shutil.copy2(ajs.STATE_FILE, backup)
        print(f"Kopia stanu: {backup}")
        state = ajs.load_state()
        project_state = state.setdefault(key, {})
        # 1. przepięcie stanu na zachowywane zgłoszenia (zanim cokolwiek zniknie)
        for p in todo:
            if p.get("extra_copy") or project_state.get(p["gid"], {}).get("jira_key") == p["keep"]:
                continue
            k = p["info"][p["keep"]]
            comments, atts = already_synced(p["gid"], p["keep"])
            entry = {"jira_key": p["keep"], "asana_modified_at": None, "synced_comment_gids": comments,
                     "synced_attachment_gids": atts, "is_epic": k["level"] >= 1, "is_subtask": k["level"] < 0}
            if k["level"] < 0 and k["parent"]:
                entry["top_level_ancestor_key"] = k["parent"]
            project_state[p["gid"]] = entry
            ajs.ensure_asana_link(p["gid"], p["keep"], p["title"], entry, False)
        ajs.save_state({key: project_state})
        # 2. podzadania tylko w usuwanych kopiach -> pod zachowywane zgłoszenie
        moved, failed = 0, []
        for p in todo:
            for child, anchor in p.get("move_children", []):
                try:
                    cte.bulk_convert_subtask_and_reparent(child, anchor, False)
                    moved += 1
                except Exception as exc:  # noqa: BLE001
                    failed.append((child, f"przeniesienie: {exc}"))
                    print(f"  [BŁĄD] przeniesienie {child} pod {anchor}: {exc}")
        blocked = {p["gid"] or p["removes"][0] for p in todo
                   if any(c in {f[0] for f in failed} for c, _ in p.get("move_children", []))}
        # 3. usuwanie od najgłębszego poziomu (kopie, których podzadań nie udało się przenieść, zostają)
        deleted = 0
        removals = [(k, p) for p in todo if (p["gid"] or p["removes"][0]) not in blocked for k in p["removes"]]
        for k, p in sorted(removals, key=lambda x: x[1]["info"][x[0]]["level"]):
            resp = ajs.request_with_retry("DELETE", f"{ajs.JIRA_BASE_URL}/rest/api/3/issue/{k}",
                                          auth=ajs.JIRA_AUTH, params={"deleteSubtasks": "false"},
                                          timeout=ajs.REQUEST_TIMEOUT)
            if resp.status_code in (200, 204):
                deleted += 1
                print(f"  usunięto {k}" + (f" (zostaje {p['keep']})" if p["keep"] else " (bez odpowiednika w Asanie)")
                      + f" | {p['title'][:60]}")
            else:
                failed.append((k, f"{resp.status_code} {resp.text[:150]}"))
                print(f"  [BŁĄD] {k}: {resp.status_code} {resp.text[:150]}")
        print(f"\nPrzeniesiono podzadań: {moved}")
    print(f"\nUsunięto: {deleted}, błędy: {len(failed)}. Stan wskazuje na zachowane zgłoszenia.")


if __name__ == "__main__":
    main()
