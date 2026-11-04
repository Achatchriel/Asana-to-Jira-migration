#!/usr/bin/env python3
"""
status_source.py — TYLKO ODCZYT. Źródła do mapowania sekcji Asany na statusy Jiry
(strona „Mapowanie statusów” w panelu). Wynik: migration_app/cache/statuses.json

  python migration_app/status_source.py [--project KLUCZ]

  sections: każda nazwa sekcji ze wszystkich projektów arkusza (cache panelu,
            migration_app/cache/sheet.json) z listą projektów i liczbą zadań
  statuses: statusy projektu Jiry (domyślnie JIRA_TEMPLATE_PROJECT_KEY) z kategorią
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = Path(__file__).resolve().parent / "cache"
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import asana_jira_sync as sync  # noqa: E402
core = sync.core


def project_sections(gid: str) -> list:
    """[(nazwa sekcji, liczba zadań)] - zadania liczone przez memberships, jedno stronicowanie."""
    url = f"{sync.ASANA_API}/projects/{gid}/sections"
    resp = sync.request_with_retry("GET", url, headers=sync.ASANA_HEADERS, params={"opt_fields": "name"},
                                   timeout=sync.REQUEST_TIMEOUT)
    resp.raise_for_status()
    names = [s["name"].strip() for s in resp.json().get("data", [])]
    counts = dict.fromkeys(names, 0)
    for t in sync.get_asana_tasks(gid):
        sec = sync.get_task_section_name(t, gid)
        if sec:
            counts[sec] = counts.get(sec, 0) + 1
    return list(counts.items())


def main() -> None:
    parser = argparse.ArgumentParser(description="Sekcje Asany i statusy Jiry do mapowania (tylko odczyt).")
    parser.add_argument("--project", default=None, help="Projekt Jiry ze statusami (domyślnie JIRA_TEMPLATE_PROJECT_KEY)")
    args = parser.parse_args()
    key = (args.project or os.getenv("JIRA_TEMPLATE_PROJECT_KEY", "")).strip().upper()
    if not key:
        sys.exit("Brak projektu ze statusami: ustaw JIRA_TEMPLATE_PROJECT_KEY albo podaj --project.")

    resp = sync.request_with_retry("GET", f"{sync.JIRA_BASE_URL}/rest/api/3/project/{key}/statuses",
                                   auth=sync.JIRA_AUTH, timeout=sync.REQUEST_TIMEOUT)
    resp.raise_for_status()
    statuses = {}
    for it in resp.json():
        for s in it.get("statuses", []):
            statuses.setdefault(s["name"], (s.get("statusCategory") or {}).get("key"))
    print(f"Statusy projektu {key}: {len(statuses)}", file=sys.stderr)

    sheet = CACHE_DIR / "sheet.json"
    if not sheet.exists():
        sys.exit("Najpierw wczytaj arkusz (Start → krok 3 albo Projekty → Odśwież z arkusza).")
    rows = [r for r in json.loads(sheet.read_text(encoding="utf-8"))["rows"] if r.get("asana_link")]
    protected = core.load_protected_projects()
    rows = [r for r in rows if r["jira_key"] not in protected]

    sections, errors = {}, []

    def one(row):
        gid = core.extract_asana_project_gid(row["asana_link"])
        if not gid:
            return row, None, "nierozpoznany link"
        try:
            return row, project_sections(gid), None
        except Exception as exc:  # noqa: BLE001
            return row, None, str(exc)[:200]

    with ThreadPoolExecutor(max_workers=6) as pool:
        for i, (row, secs, err) in enumerate(pool.map(one, rows), 1):
            if err:
                errors.append({"project": row["jira_key"] or row["name"], "error": err})
            for name, n in secs or []:
                s = sections.setdefault(name, {"name": name, "projects": [], "tasks": 0})
                s["projects"].append(row["jira_key"] or row["name"])
                s["tasks"] += n
            if i % 25 == 0:
                print(f"  {i}/{len(rows)} projektów…", file=sys.stderr)

    data = {"loaded_at": time.time(), "status_project": key,
            "statuses": [{"name": n, "category": c} for n, c in sorted(statuses.items(), key=lambda x: x[0].lower())],
            "sections": sorted(sections.values(), key=lambda s: (-len(s["projects"]), s["name"].lower())),
            "projects_scanned": len(rows), "errors": errors}
    CACHE_DIR.mkdir(exist_ok=True)
    (CACHE_DIR / "statuses.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"Sekcji: {len(sections)} z {len(rows)} projektów, błędów: {len(errors)}", file=sys.stderr)


if __name__ == "__main__":
    main()
