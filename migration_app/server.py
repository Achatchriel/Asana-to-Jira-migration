#!/usr/bin/env python3
"""
server.py — lokalny panel migracji Asana → Jira.

Uruchomienie (z głównego folderu jiraSync):
    .venv/bin/python migration_app/server.py            # http://127.0.0.1:8765
    .venv/bin/python migration_app/server.py --port 9000

Tylko biblioteka standardowa - nic nie trzeba doinstalowywać. Serwer NIE
importuje skryptów jiraSync: każde narzędzie (także odczyt arkusza i
sprawdzanie statusów) działa jako osobny podproces, więc zawsze widzi
aktualny .env, a logika skryptów zostaje nietknięta. Nasłuchuje wyłącznie
na 127.0.0.1.
"""

import argparse
import fnmatch
import json
import mimetypes
import re
import shutil
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

import catalog  # noqa: E402
from runner import Runner, python_executable  # noqa: E402

STATIC_DIR = APP_DIR / "static"
CHECKS_DIR = APP_DIR / "checks"
BACKUP_DIR = APP_DIR / "backups"
CHECKS_DIR.mkdir(exist_ok=True)
BACKUP_DIR.mkdir(exist_ok=True)

ENV_FILE = ROOT / ".env"
STATE_FILE = ROOT / "task_sync_state.json"
SECRET_RE = re.compile(r"TOKEN|SECRET|PASSWORD|API_KEY|WEBHOOK", re.I)
MASK = "••••••"

# Pliki konfiguracyjne edytowalne z panelu (wzorce względem ROOT).
EDITABLE = [
    "section_status_map.json", "section_status_map.*.json", "section_status_profiles/*.json",
    "user_map.json", "asana_users_not_in_jira.json", "wt_user_map.json", "sync_exclude.txt", "protected_projects.txt",
    "transition_groups.yaml", "jira_schema.yaml", "workflows.yaml",
]
# Pliki, które każda migracja ma (albo będzie mieć) - widoczne w panelu, zanim powstaną,
# z treścią startową przy pierwszym zapisie.
STANDARD_FILES = {
    "section_status_map.json": "{\n}\n",
    "user_map.json": "{\n}\n",
    "asana_users_not_in_jira.json": "{\n}\n",
    "sync_exclude.txt": "# Projekty wyłączone z synchronizacji (np. na czas naprawy) - jeden klucz Jiry w linii.\n",
    "protected_projects.txt": "# Projekty wyłączone ze WSZYSTKICH narzędzi (np. już zarchiwizowane) - jeden klucz Jiry w linii.\n",
}
REQUIRED_ENV = ["JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "ASANA_TOKEN", "ASANA_WORKSPACE_GID",
                "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_SHEET_ID"]
CACHE_DIR = APP_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)
# Raporty i logi do podglądu.
REPORT_GLOBS = ["logs/*", "pipeline_summaries/*", "repair_reports/**/*", "*.csv", "*_report*.json",
                "duplicate_scan_shard_*.json", "column_assignment_report*.json", "epic_candidates.json"]

runner = Runner()


# --------------------------------------------------------------------------- #
# Pomocnicze: pliki, stan, .env
# --------------------------------------------------------------------------- #

def read_key_list(name: str) -> list:
    path = ROOT / name
    if not path.exists():
        return []
    keys = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            keys.append(line.upper())
    return keys


def backup(path: Path) -> None:
    if path.exists():
        stamp = time.strftime("%Y%m%d_%H%M%S")
        rel = str(path.relative_to(ROOT)).replace("/", "__")
        shutil.copy2(path, BACKUP_DIR / f"{rel}.{stamp}")


def safe_path(rel: str, patterns: list) -> Path:
    path = (ROOT / rel).resolve()
    if ROOT not in path.parents:
        raise PermissionError("Ścieżka poza folderem projektu.")
    rel_norm = str(path.relative_to(ROOT))
    if not any(fnmatch.fnmatch(rel_norm, p) for p in patterns):
        raise PermissionError("Ten plik nie jest dostępny z panelu.")
    return path


_state_cache = {"mtime": None, "counts": {}}
_state_lock = threading.Lock()


def state_counts() -> dict:
    """{klucz projektu: liczba wpisów} z task_sync_state.json (cache po mtime - plik ma ~30 MB)."""
    if not STATE_FILE.exists():
        return {}
    with _state_lock:
        mtime = STATE_FILE.stat().st_mtime
        if _state_cache["mtime"] != mtime:
            try:
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                _state_cache["counts"] = {k: len(v) for k, v in data.items() if isinstance(v, dict)}
                _state_cache["mtime"] = mtime
            except json.JSONDecodeError:
                # Zapis w trakcie albo uszkodzony plik - zostawiamy poprzedni wynik.
                pass
        return _state_cache["counts"]


def parse_env_lines(text: str) -> list:
    """[(klucz, wartość, komentarz nad linią)] z pliku .env/.env.example."""
    out, comment = [], []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            comment.append(s.lstrip("# ").strip())
            continue
        m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", s)
        if m:
            val = m.group(2).strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            out.append((m.group(1), val, " ".join(c for c in comment if c)))
        comment = [] if not s or m else comment
    return out


def env_entries() -> dict:
    current = {k: v for k, v, _ in parse_env_lines(ENV_FILE.read_text(encoding="utf-8"))} if ENV_FILE.exists() else {}
    known = {}
    for example in (ROOT / ".env.example", ROOT / "env.example"):
        if example.exists():
            for k, _, help_text in parse_env_lines(example.read_text(encoding="utf-8")):
                known.setdefault(k, help_text)
    keys = list(dict.fromkeys(list(known) + list(current)))
    entries = []
    for k in keys:
        v = current.get(k)
        secret = bool(SECRET_RE.search(k))
        entries.append({"key": k, "set": bool(v), "secret": secret, "help": known.get(k, ""),
                        "value": (MASK + v[-4:] if (secret and v and len(v) > 8) else (MASK if secret and v else v or ""))})
    return {"exists": ENV_FILE.exists(), "entries": entries}


def new_env_lines() -> list:
    """Treść nowego .env: .env.example z komentarzami, bez przykładowych wartości wymaganych pól."""
    example = ROOT / ".env.example"
    lines = example.read_text(encoding="utf-8").splitlines() if example.exists() else []
    for i, line in enumerate(lines):
        m = re.match(r"([A-Z_][A-Z0-9_]*)=", line)
        if m and m.group(1) in ("JIRA_BASE_URL", "JIRA_EMAIL"):
            lines[i] = f"{m.group(1)}="
    return lines


def save_env(values: dict) -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else new_env_lines()
    seen = set()
    for i, line in enumerate(lines):
        m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if m and m.group(1) in values:
            k = m.group(1)
            seen.add(k)
            lines[i] = f"{k}={values[k]}"
    for k, v in values.items():
        if k not in seen:
            lines.append(f"{k}={v}")
    backup(ENV_FILE)
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ENV_FILE.chmod(0o600)


def env_values() -> dict:
    return {k: v for k, v, _ in parse_env_lines(ENV_FILE.read_text(encoding="utf-8"))} if ENV_FILE.exists() else {}


def required_env_missing() -> list:
    current = env_values()
    return [k for k in REQUIRED_ENV if not current.get(k)]


# --------------------------------------------------------------------------- #
# Projekty z arkusza
# --------------------------------------------------------------------------- #

SHEET_CACHE = APP_DIR / "cache" / "sheet.json"
_projects_cache = {"at": 0, "rows": None, "error": None}
_projects_lock = threading.Lock()


def load_sheet_rows(refresh: bool) -> tuple:
    """Wiersze arkusza z cache (migration_app/cache/sheet.json); odczyt arkusza
    tylko przy refresh albo gdy cache jeszcze nie istnieje - trwa nawet kilka minut."""
    with _projects_lock:
        # Cache mógł zostać odświeżony przez narzędzie „Wczytaj arkusz” (osobny proces).
        if not refresh and SHEET_CACHE.exists() and SHEET_CACHE.stat().st_mtime != _projects_cache.get("mtime"):
            try:
                data = json.loads(SHEET_CACHE.read_text(encoding="utf-8"))
                _projects_cache.update(rows=data["rows"], error=None, at=data.get("loaded_at", 0),
                                       mtime=SHEET_CACHE.stat().st_mtime)
            except (json.JSONDecodeError, KeyError):
                pass
        if refresh:
            try:
                out = subprocess.run([python_executable(), str(APP_DIR / "sheet_projects.py")], cwd=ROOT,
                                     capture_output=True, text=True, timeout=900)
                if out.returncode != 0:
                    raise RuntimeError((out.stdout + out.stderr).strip()[-1500:] or f"kod wyjścia {out.returncode}")
                data = json.loads(SHEET_CACHE.read_text(encoding="utf-8"))
                _projects_cache.update(rows=data["rows"], error=None, at=data["loaded_at"],
                                       mtime=SHEET_CACHE.stat().st_mtime)
            except Exception as exc:  # noqa: BLE001
                _projects_cache.update(error=str(exc), at=time.time())
        return _projects_cache["rows"], _projects_cache["error"], _projects_cache["at"]


def project_list(refresh: bool = False) -> dict:
    rows, error, at = load_sheet_rows(refresh)
    counts = state_counts()
    protected, excluded = set(read_key_list("protected_projects.txt")), set(read_key_list("sync_exclude.txt"))
    source = "arkusz"
    if rows is None:
        # Bez dostępu do arkusza: przynajmniej to, co wiadomo lokalnie.
        source = "lokalnie (stan + cache kluczy)"
        cache_file = ROOT / "project_key_cache.json"
        names = {v.upper(): k for k, v in json.loads(cache_file.read_text(encoding="utf-8")).items()} if cache_file.exists() else {}
        rows = [{"name": names.get(k, ""), "jira_key": k, "asana_link": "", "board_template_id": "",
                 "sheet_row": None, "key_from_sheet": False} for k in sorted(set(counts) | set(names))]
    out = []
    for r in rows:
        key = r["jira_key"]
        map_file = ROOT / f"section_status_map.{key}.json"
        profile = None
        if map_file.exists():
            try:
                profile = json.loads(map_file.read_text(encoding="utf-8")).get("__szablon__")
            except json.JSONDecodeError:
                profile = "BŁĘDNY JSON"
        check_file = CHECKS_DIR / f"{key}.json"
        check = None
        if check_file.exists():
            try:
                c = json.loads(check_file.read_text(encoding="utf-8"))
                check = {"at": c.get("checked_at"), "errors": len(c.get("errors", [])),
                         "warnings": len(c.get("warnings", [])),
                         "status_bad": (c.get("counts") or {}).get("status_bad"),
                         "assignee_bad": (c.get("counts") or {}).get("assignee_bad")}
            except json.JSONDecodeError:
                pass
        out.append({**r, "state_entries": counts.get(key, 0), "protected": key in protected,
                    "excluded": key in excluded, "has_map": map_file.exists(), "profile": profile,
                    "check": check})
    for key in sorted(protected):
        if not any(p["jira_key"] == key for p in out):
            out.append({"name": "", "jira_key": key, "asana_link": "", "board_template_id": "", "sheet_row": None,
                        "state_entries": counts.get(key, 0), "protected": True, "excluded": key in excluded,
                        "has_map": (ROOT / f"section_status_map.{key}.json").exists(), "profile": None,
                        "check": None})
    return {"projects": out, "source": source, "error": error, "loaded_at": at, "sheet_loaded": source == "arkusz"}


# --------------------------------------------------------------------------- #
# Przegląd
# --------------------------------------------------------------------------- #

def overview() -> dict:
    counts = state_counts()
    sessions = []
    for p in (ROOT / "auth_state.json", ROOT / "browser_automation" / "auth_state.json"):
        if p.exists():
            sessions.append({"path": str(p.relative_to(ROOT)), "age_hours": round((time.time() - p.stat().st_mtime) / 3600, 1)})
    summaries = sorted((ROOT / "pipeline_summaries").glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)[:5] \
        if (ROOT / "pipeline_summaries").exists() else []
    return {
        "root": str(ROOT), "python": python_executable(),
        "jira_base_url": next((v for k, v, _ in parse_env_lines(ENV_FILE.read_text(encoding="utf-8"))
                               if k == "JIRA_BASE_URL"), "").rstrip("/") if ENV_FILE.exists() else "",
        "env_exists": ENV_FILE.exists(), "env_missing": required_env_missing(),
        "state": {"projects": len(counts), "entries": sum(counts.values()),
                  "size_mb": round(STATE_FILE.stat().st_size / 1e6, 1) if STATE_FILE.exists() else 0,
                  "mtime": STATE_FILE.stat().st_mtime if STATE_FILE.exists() else None},
        "protected": read_key_list("protected_projects.txt"), "excluded": read_key_list("sync_exclude.txt"),
        "browser_sessions": sessions, "running": runner.running_count(),
        "user_map_size": len(json.loads((ROOT / "user_map.json").read_text(encoding="utf-8"))) if (ROOT / "user_map.json").exists() else 0,
        "skipped_people": len(json.loads((ROOT / "asana_users_not_in_jira.json").read_text(encoding="utf-8"))) if (ROOT / "asana_users_not_in_jira.json").exists() else 0,
        "recent_summaries": [{"path": str(p.relative_to(ROOT)), "mtime": p.stat().st_mtime} for p in summaries],
        "recent_runs": runner.list()[:6],
    }


def list_reports() -> list:
    seen, out = set(), []
    for pattern in REPORT_GLOBS:
        for p in ROOT.glob(pattern):
            if p.is_file() and p not in seen:
                seen.add(p)
                st = p.stat()
                out.append({"path": str(p.relative_to(ROOT)), "size": st.st_size, "mtime": st.st_mtime})
    return sorted(out, key=lambda r: r["mtime"], reverse=True)


def list_editable() -> list:
    seen, out = set(), []
    for name in STANDARD_FILES:
        p = ROOT / name
        seen.add(p)
        out.append({"path": name, "size": p.stat().st_size if p.exists() else 0,
                    "mtime": p.stat().st_mtime if p.exists() else None, "missing": not p.exists()})
    for pattern in EDITABLE:
        for p in sorted(ROOT.glob(pattern)):
            if p.is_file() and p not in seen:
                seen.add(p)
                out.append({"path": str(p.relative_to(ROOT)), "size": p.stat().st_size, "mtime": p.stat().st_mtime})
    return out


def set_person_skipped(gid: str, skip: bool, name: str = "", email: str = "") -> dict:
    """Osoba z Asany, która świadomie NIE będzie miała konta w Jirze. Lista w
    asana_users_not_in_jira.json (osobno od user_map.json, bo generate_user_map.py
    nadpisuje tamten plik). Oznaczenie usuwa też wpis osoby z user_map.json - inaczej
    synchronizacja dalej próbowałaby ją przypisać (i wstawiała leada przy błędzie)."""
    if not re.fullmatch(r"\d+", gid):
        raise ValueError("Niepoprawny gid osoby z Asany.")
    skip_file, map_file = ROOT / "asana_users_not_in_jira.json", ROOT / "user_map.json"
    skipped = json.loads(skip_file.read_text(encoding="utf-8")) if skip_file.exists() else {}
    removed = None
    if skip:
        skipped[gid] = {"name": name, "email": email, "marked_at": time.strftime("%Y-%m-%d")}
        user_map = json.loads(map_file.read_text(encoding="utf-8")) if map_file.exists() else {}
        if gid in user_map:
            removed = user_map.pop(gid)
            backup(map_file)
            map_file.write_text(json.dumps(user_map, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    else:
        skipped.pop(gid, None)
    backup(skip_file)
    skip_file.write_text(json.dumps(skipped, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"skipped": gid in skipped, "removed_from_user_map": removed}


def run_people_source(*args, timeout=600) -> None:
    out = subprocess.run([python_executable(), str(APP_DIR / "people_source.py"), *args], cwd=ROOT,
                         capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError((out.stdout + out.stderr).strip()[-1500:] or f"kod wyjścia {out.returncode}")


def read_json_file(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def people_state() -> dict:
    return {"source": read_json_file(APP_DIR / "cache" / "people.json", None),
            "user_map": read_json_file(ROOT / "user_map.json", {}),
            "not_in_jira": read_json_file(ROOT / "asana_users_not_in_jira.json", {})}


def save_people_mapping(mapping: dict, not_in_jira: dict, keep_unlisted: bool) -> dict:
    """Zapisuje user_map.json i asana_users_not_in_jira.json z mapowania z panelu.
    keep_unlisted: zostaw wpisy osób, których nie ma na wczytanej liście z Asany."""
    source = read_json_file(APP_DIR / "cache" / "people.json", None) or {}
    listed = {a["gid"] for a in source.get("asana", [])}
    for gid, acc in mapping.items():
        if not re.fullmatch(r"\d+", gid) or not isinstance(acc, str) or not acc:
            raise ValueError(f"Niepoprawny wpis mapy: {gid} → {acc!r}")
    overlap = set(mapping) & set(not_in_jira)
    if overlap:
        raise ValueError(f"{len(overlap)} osób jest jednocześnie zmapowanych i oznaczonych „nie w Jirze”.")
    old_map = read_json_file(ROOT / "user_map.json", {})
    old_skip = read_json_file(ROOT / "asana_users_not_in_jira.json", {})
    new_map = {g: a for g, a in old_map.items() if keep_unlisted and g not in listed and g not in not_in_jira}
    new_map.update(mapping)
    new_skip = {g: v for g, v in old_skip.items() if keep_unlisted and g not in listed and g not in new_map}
    new_skip.update({g: {**v, "marked_at": (old_skip.get(g) or {}).get("marked_at") or time.strftime("%Y-%m-%d")}
                     for g, v in not_in_jira.items()})
    for path, data in ((ROOT / "user_map.json", new_map), (ROOT / "asana_users_not_in_jira.json", new_skip)):
        backup(path)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"user_map": len(new_map), "not_in_jira": len(new_skip),
            "removed": len(set(old_map) - set(new_map)), "added": len(set(new_map) - set(old_map)),
            "changed": sum(1 for g in new_map if g in old_map and old_map[g] != new_map[g])}


def google_key_path() -> Path:
    raw = env_values().get("GOOGLE_APPLICATION_CREDENTIALS") or "./service_account.json"
    return (ROOT / raw).resolve() if not Path(raw).is_absolute() else Path(raw)


def save_google_key(text: str) -> dict:
    """Zapisuje wklejony klucz konta serwisowego do ./service_account.json (poza repo - .gitignore)."""
    try:
        info = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"To nie jest poprawny JSON: {exc}") from exc
    if info.get("type") != "service_account" or not info.get("client_email") or not info.get("private_key"):
        raise ValueError("To nie jest klucz konta serwisowego (brak type=service_account, client_email lub private_key).")
    path = ROOT / "service_account.json"
    backup(path)
    path.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    save_env({"GOOGLE_APPLICATION_CREDENTIALS": "./service_account.json"})
    return {"client_email": info["client_email"]}


def status_map_step(status_map: dict) -> dict:
    """Mapa statusów jest gotowa, gdy ma wpisy, jawny status dla ukończonych i wszystkie
    wskazane statusy istnieją w projekcie wzorcowym (lista z ostatniego odczytu źródeł)."""
    sections = {k: v for k, v in status_map.items() if not k.startswith("__")}
    if not sections:
        return {"done": False, "detail": "Brak mapy statusów."}
    source = read_json_file(CACHE_DIR / "statuses.json", None)
    known = {s["name"] for s in source["statuses"]} if source else None
    used = [v.get("status") if isinstance(v, dict) else (v[0] if isinstance(v, list) and v else v) for v in status_map.values()]
    invalid = sorted({u for u in used if isinstance(u, str) and u and known is not None and u not in known})
    done_status = status_map.get("__ukonczone__")
    problems = []
    if not done_status:
        problems.append("brak statusu dla ukończonych zadań")
    if invalid:
        problems.append(f"statusy spoza projektu {source['status_project']}: {', '.join(invalid[:5])}")
    detail = f"section_status_map.json: {len(sections)} sekcji" + (f", ukończone → {done_status}" if done_status else "")
    return {"done": not problems, "detail": detail + ("; " + "; ".join(problems) if problems else "")}


def setup_status() -> dict:
    """Kroki pierwszej migracji ze stanem wykrytym z plików - zakładka Start."""
    env = env_values()
    missing = required_env_missing()
    check = read_json_file(CACHE_DIR / "setup_check.json", None)
    env_mtime = ENV_FILE.stat().st_mtime if ENV_FILE.exists() else 0
    check_fresh = bool(check) and check["checked_at"] >= env_mtime
    checks_ok = check_fresh and all(check[k]["ok"] for k in ("jira", "asana", "google"))
    sheet = read_json_file(SHEET_CACHE, None)
    user_map = read_json_file(ROOT / "user_map.json", {})
    skipped = read_json_file(ROOT / "asana_users_not_in_jira.json", {})
    status_map = read_json_file(ROOT / "section_status_map.json", {})
    session = ROOT / "auth_state.json"
    runs = runner.list()
    trial = next((r for r in runs if r["tool_id"] in ("full_pipeline", "asana_jira_sync") and "--dry-run" in r["argv"]
                  and r["status"] in ("ok", "partial")), None)
    counts = state_counts()
    google_email = None
    try:
        google_email = json.loads(google_key_path().read_text(encoding="utf-8")).get("client_email")
    except (OSError, json.JSONDecodeError):
        pass
    steps = [
        {"id": "env", "done": not missing, "detail": "Brakuje: " + ", ".join(missing) if missing else "Wymagane pola uzupełnione."},
        {"id": "check", "done": checks_ok,
         "detail": "Wszystkie połączenia działają." if checks_ok else
         ("Wynik sprzed ostatniej zmiany .env - sprawdź ponownie." if check and not check_fresh else
          "Nie sprawdzono." if not check else "Są błędy - zobacz szczegóły.")},
        {"id": "sheet", "done": bool(sheet and sheet.get("rows")),
         "detail": f"{len(sheet['rows'])} projektów, wczytano {time.strftime('%Y-%m-%d %H:%M', time.localtime(sheet['loaded_at']))}" if sheet else "Nie wczytano."},
        {"id": "people", "done": bool(user_map or skipped),
         "detail": f"user_map.json: {len(user_map)} osób, nie w Jirze: {len(skipped)}" if (user_map or skipped) else "Brak mapy osób."},
        {"id": "statuses", **status_map_step(status_map)},
        {"id": "session", "done": session.exists(),
         "detail": f"Sesja sprzed {round((time.time() - session.stat().st_mtime) / 3600, 1)} h" if session.exists() else "Brak zapisanej sesji."},
        {"id": "trial", "done": bool(trial), "detail": f"Ostatni podgląd: {trial['command']}" if trial else "Jeszcze nie uruchomiono."},
        {"id": "migrate", "done": bool(counts), "detail": f"{len(counts)} projektów, {sum(counts.values())} zadań w stanie" if counts else "Jeszcze nic nie zmigrowano."},
        {"id": "verify", "done": any(CHECKS_DIR.glob("*.json")), "detail": f"{len(list(CHECKS_DIR.glob('*.json')))} sprawdzonych projektów"},
    ]
    return {"steps": steps, "check": check, "check_fresh": check_fresh, "google_email": google_email,
            "env": {k: env.get(k, "") for k in ("JIRA_BASE_URL", "JIRA_EMAIL", "ASANA_WORKSPACE_GID", "GOOGLE_SHEET_ID",
                                               "GOOGLE_SHEET_RANGE", "JIRA_TEMPLATE_PROJECT_KEY", "JIRA_TEMPLATE_BOARD_ID",
                                               "DEFAULT_LEAD_ACCOUNT_ID")},
            "secrets_set": {k: bool(env.get(k)) for k in ("JIRA_API_TOKEN", "ASANA_TOKEN")}}


def save_status_map(entries: dict, keep_unlisted: bool) -> dict:
    """Zapis mapy głównej section_status_map.json z panelu. entries: {sekcja: status | None}."""
    source = read_json_file(CACHE_DIR / "statuses.json", None) or {}
    listed = {s["name"] for s in source.get("sections", [])} | {"__ukonczone__"}
    path = ROOT / "section_status_map.json"
    old = read_json_file(path, {})
    new = {k: v for k, v in old.items() if keep_unlisted and k not in listed}
    for k, v in entries.items():
        if isinstance(v, str) and v.strip():
            new[k] = v.strip()
        elif isinstance(v, dict) and v.get("pomin"):
            new[k] = {"pomin": True}
    backup(path)
    path.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"entries": len(new), "added": len(set(new) - set(old)), "removed": len(set(old) - set(new)),
            "changed": sum(1 for k in new if k in old and old[k] != new[k])}


def update_status_map(project_key: str, section: str, value) -> dict:
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", project_key):
        raise ValueError("Niepoprawny klucz projektu.")
    path = ROOT / f"section_status_map.{project_key}.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if value in (None, ""):
        data.pop(section, None)
    else:
        data[section] = value
    backup(path)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

class Handler(BaseHTTPRequestHandler):
    server_version = "jiraSyncPanel/1.0"

    def log_message(self, fmt, *args):  # ciszej w konsoli: tylko błędy
        if args and str(args[1]).startswith(("4", "5")):
            super().log_message(fmt, *args)

    # -- odpowiedzi
    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status, message):
        self.send_json({"error": message}, status)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def send_static(self, rel: str):
        path = (STATIC_DIR / (rel or "index.html")).resolve()
        if STATIC_DIR not in path.parents or not path.is_file():
            return self.send_error_json(404, "Nie znaleziono")
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", (mimetypes.guess_type(path.name)[0] or "application/octet-stream") + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    # -- routing
    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method):
        # Ochrona przed żądaniami z innych stron otwartych w przeglądarce.
        origin = self.headers.get("Origin")
        if method != "GET" and origin and not re.match(r"https?://(127\.0\.0\.1|localhost)(:\d+)?$", origin):
            return self.send_error_json(403, "Niedozwolone źródło żądania.")
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        parts = [p for p in url.path.split("/") if p]
        try:
            if not parts or parts[0] != "api":
                if method == "GET":
                    return self.send_static("/".join(parts[1:]) if parts[:1] == ["static"] else "")
                return self.send_error_json(404, "Nie znaleziono")
            return self.route(method, parts[1:], q)
        except PermissionError as exc:
            self.send_error_json(403, str(exc))
        except (ValueError, KeyError) as exc:
            self.send_error_json(400, str(exc))
        except Exception as exc:  # noqa: BLE001
            self.send_error_json(500, f"{type(exc).__name__}: {exc}")

    def route(self, method, p, q):
        if p == ["catalog"] and method == "GET":
            return self.send_json(catalog.public_catalog())
        if p == ["overview"] and method == "GET":
            return self.send_json(overview())
        if p == ["projects"] and method == "GET":
            return self.send_json(project_list(refresh=q.get("refresh") == "1"))

        # -- uruchomienia
        if p == ["preview"] and method == "POST":
            body = self.read_json()
            tool = catalog.TOOLS_BY_ID[body["tool_id"]]
            argv = catalog.build_argv(tool, body.get("values") or {})
            return self.send_json({"argv": argv, "writes": catalog.run_writes(tool, argv),
                                   "command": " ".join(["python", tool["script"]] + argv)})
        if p == ["runs"] and method == "GET":
            return self.send_json({"runs": runner.list()})
        if p == ["runs"] and method == "POST":
            body = self.read_json()
            tool = catalog.TOOLS_BY_ID[body["tool_id"]]
            argv = catalog.build_argv(tool, body.get("values") or {})
            writes = catalog.run_writes(tool, argv)
            if writes and not body.get("confirm"):
                return self.send_json({"error": "Uruchomienie zmienia dane - wymaga potwierdzenia.",
                                       "needs_confirm": True, "argv": argv}, 409)
            run = runner.start(tool, argv, writes)
            return self.send_json(run.meta(), 201)
        if len(p) >= 2 and p[0] == "runs":
            run = runner.runs.get(p[1])
            if run is None:
                return self.send_error_json(404, "Nie ma takiego uruchomienia.")
            if len(p) == 2 and method == "GET":
                start = int(q.get("from", 0))
                wait = float(q.get("wait", 0))
                with run.cond:
                    if wait and len(run.lines) <= start and run.status == "running":
                        run.cond.wait(timeout=min(wait, 25))
                    lines = run.lines[start:]
                return self.send_json({**run.meta(), "from": start, "new_lines": lines})
            if len(p) == 2 and method == "DELETE":
                runner.delete(p[1])
                return self.send_json({"ok": True})
            if p[2:] == ["input"] and method == "POST":
                runner.send_input(p[1], self.read_json().get("text", ""))
                return self.send_json({"ok": True})
            if p[2:] == ["stop"] and method == "POST":
                runner.stop(p[1])
                return self.send_json({"ok": True})

        # -- sprawdzenia statusów/przypisań
        if p == ["checks"] and method == "GET":
            out = []
            for f in sorted(CHECKS_DIR.glob("*.json")):
                try:
                    c = json.loads(f.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                out.append({"jira_key": c.get("jira_key"), "asana_name": c.get("asana_name"),
                            "checked_at": c.get("checked_at"), "errors": c.get("errors"),
                            "warnings": c.get("warnings"), "counts": c.get("counts")})
            return self.send_json({"checks": out})
        if len(p) == 2 and p[0] == "checks" and method == "GET":
            f = CHECKS_DIR / f"{p[1].upper()}.json"
            if not re.fullmatch(r"[A-Z0-9_]+", p[1].upper()) or not f.exists():
                return self.send_error_json(404, "Brak wyniku sprawdzenia dla tego projektu.")
            return self.send_json(json.loads(f.read_text(encoding="utf-8")))
        # -- karta „Kopia projektu”: odczyty z Jiry i wybór ról
        if len(p) == 2 and p[0] == "jira" and method == "GET" and p[1] in ("roles", "users", "groups"):
            arg = ["--project", q.get("project", "")] if p[1] == "roles" else ["--query", q.get("q", "")]
            if not arg[1].strip():
                raise ValueError("Podaj projekt albo tekst do wyszukania.")
            out = subprocess.run([python_executable(), str(APP_DIR / "jira_lookup.py"), p[1], *arg], cwd=ROOT,
                                 capture_output=True, text=True, timeout=120)
            if out.returncode != 0:
                raise RuntimeError((out.stdout + out.stderr).strip()[-800:])
            return self.send_json(json.loads(out.stdout))
        if p == ["clone", "roles"] and method == "POST":
            body = self.read_json()
            key = str(body.get("target_key", "")).upper()
            if not re.fullmatch(r"[A-Z][A-Z0-9]{1,9}", key):
                raise ValueError("Niepoprawny klucz nowego projektu.")
            roles = [{"id": str(r["id"]), "name": str(r.get("name") or r["id"]),
                      "users": [str(u) for u in r.get("users") or []], "groups": [str(g) for g in r.get("groups") or []]}
                     for r in body.get("roles") or [] if r.get("users") or r.get("groups")]
            path = CACHE_DIR / f"clone_roles_{key}.json"
            path.write_text(json.dumps(roles, ensure_ascii=False, indent=1), encoding="utf-8")
            return self.send_json({"path": str(path.relative_to(ROOT)), "roles": len(roles)})
        if p == ["setup"] and method == "GET":
            return self.send_json(setup_status())
        if p == ["setup", "check"] and method == "POST":
            out = subprocess.run([python_executable(), str(APP_DIR / "setup_check.py")], cwd=ROOT,
                                 capture_output=True, text=True, timeout=180)
            if out.returncode != 0:
                raise RuntimeError((out.stdout + out.stderr).strip()[-1500:])
            return self.send_json(setup_status())
        if p == ["setup", "google-key"] and method == "POST":
            return self.send_json(save_google_key(self.read_json().get("key", "")))
        if p == ["statusmap"] and method == "GET":
            return self.send_json({"source": read_json_file(CACHE_DIR / "statuses.json", None),
                                   "map": read_json_file(ROOT / "section_status_map.json", {})})
        if p == ["statusmap"] and method == "PUT":
            body = self.read_json()
            return self.send_json(save_status_map(body.get("entries") or {}, bool(body.get("keep_unlisted"))))
        if p == ["people"] and method == "GET":
            return self.send_json(people_state())
        if p == ["people", "groups"] and method == "GET":
            cache = APP_DIR / "cache" / "jira_groups.json"
            if q.get("refresh") == "1" or not cache.exists():
                run_people_source("groups", timeout=180)
            return self.send_json(read_json_file(cache, {"groups": []}))
        if p == ["people", "load"] and method == "POST":
            groups = [g for g in (self.read_json().get("groups") or []) if isinstance(g, str) and g.strip()]
            if not groups:
                raise ValueError("Wybierz przynajmniej jedną grupę Jiry.")
            run_people_source("load", "--groups", ",".join(groups))
            return self.send_json(people_state())
        if p == ["people", "mapping"] and method == "PUT":
            body = self.read_json()
            return self.send_json(save_people_mapping(body.get("mapping") or {}, body.get("not_in_jira") or {},
                                                      bool(body.get("keep_unlisted"))))
        if p == ["people-skip"] and method == "POST":
            body = self.read_json()
            return self.send_json(set_person_skipped(str(body["gid"]), bool(body.get("skip")),
                                                     body.get("name") or "", body.get("email") or ""))
        if len(p) == 2 and p[0] == "status-map" and method == "POST":
            body = self.read_json()
            return self.send_json({"map": update_status_map(p[1].upper(), body["section"], body.get("value"))})

        # -- konfiguracja
        if p == ["env"] and method == "GET":
            return self.send_json(env_entries())
        if p == ["env"] and method == "PUT":
            values = {k: str(v) for k, v in (self.read_json().get("values") or {}).items()
                      if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k) and not str(v).startswith(MASK)
                      and "\n" not in str(v)}
            save_env(values)
            return self.send_json({"saved": sorted(values)})
        if p == ["files"] and method == "GET":
            if "path" in q:
                path = safe_path(q["path"], EDITABLE)
                content = path.read_text(encoding="utf-8") if path.exists() else STANDARD_FILES.get(q["path"], "")
                return self.send_json({"path": q["path"], "content": content, "missing": not path.exists()})
            return self.send_json({"files": list_editable()})
        if p == ["files"] and method == "PUT":
            body = self.read_json()
            path = safe_path(body["path"], EDITABLE)
            content = body["content"]
            if path.suffix == ".json":
                json.loads(content)  # ValueError -> 400, nie zapisujemy zepsutego JSON-a
            backup(path)
            path.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
            return self.send_json({"ok": True})

        # -- raporty
        if p == ["reports"] and method == "GET":
            if "path" in q:
                path = safe_path(q["path"], REPORT_GLOBS)
                size = path.stat().st_size
                with open(path, "rb") as f:
                    if size > 1_500_000:
                        f.seek(size - 1_500_000)
                    text = f.read().decode("utf-8", errors="replace")
                return self.send_json({"path": q["path"], "size": size, "truncated": size > 1_500_000, "content": text})
            return self.send_json({"reports": list_reports()})

        return self.send_error_json(404, "Nieznany adres API.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Panel migracji Asana → Jira")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    httpd.daemon_threads = True
    print(f"Panel migracji: http://127.0.0.1:{args.port}  (folder: {ROOT})")
    print("Zatrzymanie: Ctrl+C. Trwające uruchomienia narzędzi zostaną przerwane razem z panelem.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for run in list(runner.runs.values()):
            if run.status == "running":
                print(f"Przerywam: {run.title} ({run.id})")
                runner.stop(run.id)


if __name__ == "__main__":
    main()
