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
    "user_map.json", "wt_user_map.json", "sync_exclude.txt", "protected_projects.txt",
    "transition_groups.yaml", "jira_schema.yaml", "jira_schema_klient.yaml", "workflows.yaml",
]
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


def save_env(values: dict) -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
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


def required_env_missing() -> list:
    required = ["JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "ASANA_TOKEN", "ASANA_WORKSPACE_GID",
                "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_SHEET_ID"]
    current = {k: v for k, v, _ in parse_env_lines(ENV_FILE.read_text(encoding="utf-8"))} if ENV_FILE.exists() else {}
    return [k for k in required if not current.get(k)]


# --------------------------------------------------------------------------- #
# Projekty z arkusza
# --------------------------------------------------------------------------- #

_projects_cache = {"at": 0, "rows": None, "error": None}
_projects_lock = threading.Lock()


def load_sheet_rows(refresh: bool) -> tuple:
    with _projects_lock:
        if refresh or _projects_cache["rows"] is None and _projects_cache["error"] is None:
            try:
                out = subprocess.run([python_executable(), str(APP_DIR / "sheet_projects.py")], cwd=ROOT,
                                     capture_output=True, text=True, timeout=180)
                if out.returncode != 0:
                    raise RuntimeError((out.stdout + out.stderr).strip()[-1500:] or f"kod wyjścia {out.returncode}")
                _projects_cache.update(rows=json.loads(out.stdout)["rows"], error=None, at=time.time())
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
    return {"projects": out, "source": source, "error": error, "loaded_at": at}


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
    for pattern in EDITABLE:
        for p in sorted(ROOT.glob(pattern)):
            if p.is_file() and p not in seen:
                seen.add(p)
                out.append({"path": str(p.relative_to(ROOT)), "size": p.stat().st_size, "mtime": p.stat().st_mtime})
    return out


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
                return self.send_json({"path": q["path"], "content": path.read_text(encoding="utf-8") if path.exists() else ""})
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
