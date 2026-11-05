#!/usr/bin/env python3
"""
board_columns.py — kolumny tablic i przypisanie statusów do kolumn PRZEZ API (kroki 2-3
run_full_pipeline.py). Zastępuje bulk_configure_columns.py i run_column_assignments.py:
bez przeglądarki, bez Playwright i bez zapisanej sesji logowania - wystarczy token API z .env.

Dla każdego projektu (z arkusza albo --project-keys):
  1. tablica wzorcowa: kolumna board_template_id wiersza arkusza (albo --template-board-id),
     inaczej JIRA_TEMPLATE_BOARD_ID, inaczej jedyna tablica projektu wzorcowego
     (JIRA_TEMPLATE_PROJECT_KEY) - przy kilku tablicach skrypt przerywa zamiast zgadywać;
  2. tablica projektu: ta, której lokalizacją jest projekt (pierwsza z nich);
  3. docelowy układ = kolumny wzorca (nazwy, limity min/max, kolumna backlogu Kanban) ze
     statusami wzorca. Statusy dopasowywane po ID, a gdy workflow projektu go nie ma - po
     nazwie; status, którego projekt nie zna, jest pomijany z ostrzeżeniem;
  4. zapis jednym wywołaniem API GreenHopper (tego samego, którego używa ekran ustawień
     kolumn w Jirze), potem odczyt kontrolny. Tablica już zgodna ze wzorcem jest pomijana.

  python board_columns.py --dry-run                       # podgląd: obecny i docelowy układ
  python board_columns.py --project-keys ABC,DEF          # zapis dla wybranych projektów
  python board_columns.py --check                         # krok 3: tylko kontrola zgodności
  python board_columns.py --shard 1/3                     # część arkusza (równolegle)
"""

import argparse
import os
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core  # noqa: E402  - arkusz, klucze projektów, shard
from pipeline_summary import record_error, record_warning, write_shard_summary  # noqa: E402

BASE = core.JIRA_BASE_URL
AUTH = core.jira_auth()
HEADERS = {"Accept": "application/json", "X-Atlassian-Token": "no-check"}
TEMPLATE_BOARD_ID = os.getenv("JIRA_TEMPLATE_BOARD_ID", "").strip()
TEMPLATE_PROJECT_KEY = os.getenv("JIRA_TEMPLATE_PROJECT_KEY", "").strip()
SUMMARY_NAME = Path(__file__).stem


def call(method: str, path: str, **kw):
    for attempt in range(5):
        r = requests.request(method, f"{BASE}{path}", auth=AUTH, headers=HEADERS, timeout=60, **kw)
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 4:
            time.sleep(float(r.headers.get("Retry-After") or 2 ** (attempt + 1)))
            continue
        if r.status_code >= 300:
            raise RuntimeError(f"{method} {path}: {r.status_code} {r.text[:300]}")
        return r.json() if r.content and "json" in r.headers.get("content-type", "") else None


def project_boards(key: str) -> list:
    return call("GET", "/rest/agile/1.0/board", params={"projectKeyOrId": key}).get("values", [])


def target_board(key: str) -> "dict | None":
    """Tablica, której lokalizacją jest TEN projekt (API zwraca też tablice innych projektów,
    które tylko obejmują go filtrem)."""
    boards = project_boards(key)
    own = [b for b in boards if (b.get("location") or {}).get("projectKey", "").upper() == key.upper()]
    return (own or boards or [None])[0]


_default_template: list = []


def default_template_board() -> int:
    if TEMPLATE_BOARD_ID:
        return int(TEMPLATE_BOARD_ID)
    if _default_template:
        return _default_template[0]
    if not TEMPLATE_PROJECT_KEY:
        sys.exit("Brak wzorca kolumn: ustaw JIRA_TEMPLATE_BOARD_ID albo JIRA_TEMPLATE_PROJECT_KEY w .env "
                 "(albo board_template_id w arkuszu).")
    boards = project_boards(TEMPLATE_PROJECT_KEY)
    if len(boards) != 1:
        print(f"Projekt wzorcowy {TEMPLATE_PROJECT_KEY} ma {len(boards)} tablic - nie zgaduję, która jest wzorcem. "
              f"Ustaw JIRA_TEMPLATE_BOARD_ID w .env. Kandydaci:")
        for b in boards:
            print(f"  - id {b['id']}: {b['name']}")
        sys.exit(1)
    _default_template.append(boards[0]["id"])
    return boards[0]["id"]


def edit_model(board_id) -> dict:
    return call("GET", "/rest/greenhopper/1.0/rapidviewconfig/editmodel.json", params={"rapidViewId": board_id})["rapidListConfig"]


def all_statuses(cfg: dict) -> dict:
    """{id: status} wszystkich statusów workflow tablicy (przypisanych i nieprzypisanych)."""
    out = {str(s["id"]): s for c in cfg.get("mappedColumns", []) for s in c.get("mappedStatuses", [])}
    out.update({str(s["id"]): s for s in cfg.get("unmappedStatuses", [])})
    return out


def layout(cfg: dict) -> list:
    """[(nazwa kolumny, posortowane ID statusów)] - do porównań."""
    return [(c["name"], sorted(str(s["id"]) for s in c.get("mappedStatuses", []))) for c in cfg.get("mappedColumns", [])]


def describe(cfg_or_cols: list, names: dict) -> str:
    return "; ".join(f"{n}: {', '.join(names.get(i, i) for i in ids) or '-'}" for n, ids in cfg_or_cols)


def plan(template: dict, target: dict) -> "tuple[dict, list, list, list]":
    """(treść zapisu, docelowy układ, ostrzeżenia, powody blokady) dla tablicy projektu wg wzorca.
    Blokada: workflow projektu nie pasuje do wzorca - zapis zostawiłby kolumny bez statusów
    albo zdjąłby z tablicy statusy, które są na niej teraz."""
    available = all_statuses(target)
    by_name = {s["name"].strip().lower(): sid for sid, s in available.items()}
    strip = lambda s: {k: s[k] for k in ("id", "name", "statusCategory", "isPresentInWorkflow") if k in s}  # noqa: E731
    warnings, used, cols = [], set(), []
    target_ids = [c.get("id") for c in target.get("mappedColumns", [])]
    for i, c in enumerate(template.get("mappedColumns", [])):
        statuses = []
        for s in c.get("mappedStatuses", []):
            sid = str(s["id"]) if str(s["id"]) in available else by_name.get(s["name"].strip().lower())
            if not sid:
                warnings.append(f"status „{s['name']}” (kolumna „{c['name']}”) nie istnieje w workflow projektu - pominięty")
                continue
            if sid not in used:
                used.add(sid)
                statuses.append(strip(available[sid]))
        col = {"name": c["name"], "min": c.get("min", ""), "max": c.get("max", ""),
               "mappedStatuses": statuses, "isKanPlanColumn": c.get("isKanPlanColumn", False)}
        if i < len(target_ids) and target_ids[i] is not None:
            col["id"] = target_ids[i]
        cols.append(col)
    missing = [w for w in warnings]
    current = {str(s["id"]) for c in target.get("mappedColumns", []) for s in c.get("mappedStatuses", [])}
    # Zdjęcie statusu z tablicy blokuje zapis tylko wtedy, gdy wzorzec go w ogóle nie zna (własny status
    # workflow projektu). Status znany wzorcowi, ale u niego bez kolumny (np. „Backlog”), to zwykła zmiana.
    template_names = {s["name"].strip().lower() for s in all_statuses(template).values()}
    dropped = sorted(available[sid]["name"] for sid in current - used
                     if sid not in all_statuses(template) and available[sid]["name"].strip().lower() not in template_names)
    blockers = []
    if missing:
        blockers.append(f"{len(missing)} statusów wzorca nie ma w workflow projektu")
    if dropped:
        blockers.append(f"statusy spoza wzorca zniknęłyby z tablicy: {', '.join(dropped)}")
    body = {**{k: template[k] for k in ("currentStatisticsField", "statisticsFields", "showDaysInColumn",
                                        "statisticsFieldValidation") if k in template},
            "workflow": target.get("workflow", template.get("workflow")),
            "rapidViewId": target["rapidViewId"], "canEdit": True, "mappedColumns": cols,
            "unmappedStatuses": [strip(s) for sid, s in available.items() if sid not in used]}
    return body, [(c["name"], sorted(str(s["id"]) for s in c["mappedStatuses"])) for c in cols], warnings, blockers


def sheet_templates() -> dict:
    """{klucz: board_template_id} z arkusza - z cache panelu (migration_app/cache/sheet.json),
    a bez niego z arkusza (odczyt bywa wolny)."""
    cache_file = Path(__file__).resolve().parent / "migration_app" / "cache" / "sheet.json"
    if cache_file.exists():
        import json
        return {r["jira_key"]: (r.get("board_template_id") or "").strip()
                for r in json.loads(cache_file.read_text(encoding="utf-8"))["rows"] if r.get("jira_key")}
    cache = core.load_key_cache()
    reserved = set(cache.values())
    return {core.assign_jira_key(r, core.extract_asana_project_gid(r.asana_project_link), cache, reserved):
            (r.board_template_id or "").strip() for r in core.get_sheet_rows()}


def project_list(args) -> list:
    """[(klucz projektu, board_template_id albo "")]."""
    if args.project_keys:
        keys = [k.strip().upper() for k in args.project_keys.split(",") if k.strip()]
        if args.template_board_id:
            return [(k, args.template_board_id) for k in keys]
        return [(k, sheet_templates().get(k, "")) for k in keys]
    cache = core.load_key_cache()
    reserved = set(cache.values())
    out = []
    for row in core.get_sheet_rows():
        gid = core.extract_asana_project_gid(row.asana_project_link)
        out.append((core.assign_jira_key(row, gid, cache, reserved), (row.board_template_id or "").strip()))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Kolumny tablic i statusy w kolumnach przez API (bez przeglądarki).")
    ap.add_argument("--project-keys", default=None, help="Klucze projektów po przecinku (domyślnie: arkusz)")
    ap.add_argument("--template-board-id", default=None, help="Wzorzec dla projektów z --project-keys")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--shard", default=None, help="Format K/N - ten proces bierze K-tą część projektów")
    ap.add_argument("--dry-run", action="store_true", help="Pokaż obecny i docelowy układ, nic nie zapisuj")
    ap.add_argument("--check", action="store_true", help="Tylko kontrola zgodności ze wzorcem (krok 3)")
    ap.add_argument("--force", action="store_true",
                    help="Zapisz także wtedy, gdy workflow projektu nie pasuje do wzorca (kolumny bez statusów, "
                         "statusy znikające z tablicy)")
    ap.add_argument("--keep-old-columns", action="store_true", help=argparse.SUPPRESS)  # zgodność ze starym krokiem 2
    args = ap.parse_args()
    core.check_config()

    projects = project_list(args)
    protected = core.load_protected_projects()
    projects = [p for p in projects if p[0].upper() not in protected]
    projects = core.apply_shard(projects, args.shard)
    if args.limit:
        projects = projects[: args.limit]
    mode = "KONTROLA" if args.check else ("PODGLĄD" if args.dry_run else "ZAPIS")
    print(f"Projektów: {len(projects)} ({mode}; kolumny i statusy przez API, bez przeglądarki)")

    templates: dict = {}
    counts = {"zapisane": 0, "zgodne": 0, "do zmiany": 0, "niezgodne": 0, "pominięte": 0, "błędy": 0}
    for n, (key, tpl_id) in enumerate(projects, 1):
        print(f"\n[{n}/{len(projects)}] {key}")
        try:
            proj = call("GET", f"/rest/api/3/project/{key}")
            if proj.get("style") == "next-gen":
                print("   team-managed - kolumny ustawia się w projekcie, pomijam")
                record_warning(key, "", "projekt team-managed - kolumny trzeba ustawić ręcznie")
                counts["pominięte"] += 1
                continue
            board = target_board(key)
            if not board:
                print("   brak tablicy w projekcie - pomijam")
                record_warning(key, "", "brak tablicy w projekcie")
                counts["pominięte"] += 1
                continue
            tid = int(tpl_id) if tpl_id else default_template_board()
            if tid not in templates:
                templates[tid] = edit_model(tid)
                names = {sid: s["name"] for sid, s in all_statuses(templates[tid]).items()}
                print(f"   wzorzec: tablica {tid} - {describe(layout(templates[tid]), names)}")
            target = edit_model(board["id"])
            names = {sid: s["name"] for sid, s in all_statuses(target).items()}
            body, want, warnings, blockers = plan(templates[tid], target)
            for w in warnings:
                print(f"   [OSTRZEŻENIE] {w}")
                record_warning(key, f"tablica {board['id']}", w)
            if layout(target) == want:
                print(f"   tablica {board['id']} „{board['name']}”: zgodna ze wzorcem")
                counts["zgodne"] += 1
                continue
            if args.check:
                print(f"   [NIEZGODNA] tablica {board['id']}: {describe(layout(target), names)}")
                record_warning(key, f"tablica {board['id']}", "kolumny/statusy niezgodne ze wzorcem")
                counts["niezgodne"] += 1
                continue
            if blockers and not args.force:
                msg = f"workflow projektu nie pasuje do wzorca {tid} ({'; '.join(blockers)}) - pomijam; " \
                      f"ustaw właściwy board_template_id w arkuszu albo użyj --force"
                print(f"   [POMINIĘTO] tablica {board['id']} „{board['name']}”: {msg}")
                record_warning(key, f"tablica {board['id']}", msg)
                counts["niepasujący wzorzec"] = counts.get("niepasujący wzorzec", 0) + 1
                continue
            if args.dry_run:
                print(f"   tablica {board['id']} „{board['name']}” - obecnie: {describe(layout(target), names)}")
                print(f"   [DRY-RUN] ustawiłbym: {describe(want, names)}")
                counts["do zmiany"] += 1
                continue
            call("PUT", "/rest/greenhopper/1.0/rapidviewconfig/columns", json=body)
            after = layout(edit_model(board["id"]))
            if after == want:
                print(f"   tablica {board['id']}: zapisano {len(want)} kolumn, {sum(len(i) for _, i in want)} statusów")
                counts["zapisane"] += 1
            else:
                print(f"   [OSTRZEŻENIE] tablica {board['id']}: po zapisie układ różni się od wzorca: {describe(after, names)}")
                record_warning(key, f"tablica {board['id']}", "po zapisie układ różni się od wzorca")
                counts["niezgodne"] += 1
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"   [BŁĄD] {exc}")
            record_error(key, "", exc)
            counts["błędy"] += 1

    print("\n" + "=" * 60)
    print("PODSUMOWANIE")
    for k, v in counts.items():
        if v or k in ("zapisane", "zgodne", "błędy"):
            print(f"  {k}: {v}")
    write_shard_summary(SUMMARY_NAME, args.shard, {k: v for k, v in counts.items() if v})
    sys.exit(2 if counts["błędy"] and counts["błędy"] < len(projects) else (1 if counts["błędy"] else 0))


if __name__ == "__main__":
    main()
