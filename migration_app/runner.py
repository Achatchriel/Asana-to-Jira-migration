# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
runner.py

Uruchamia skrypty jiraSync jako podprocesy (cwd = główny folder, tak jak
run_full_pipeline.py), zbiera wyjście linia po linii do pliku i pamięci, żeby
panel mógł je pokazywać na żywo, i pozwala wysłać tekst na stdin (skrypty,
które pytają o potwierdzenie).

Historia uruchomień: migration_app/runs/<id>.json (metadane) + <id>.log.
"""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = Path(__file__).resolve().parent / "runs"
RUNS_DIR.mkdir(exist_ok=True)


def python_executable() -> str:
    """Interpreter z .venv projektu (ma requests, google-api, playwright), a gdy
    go nie ma - ten, którym uruchomiono panel."""
    for candidate in (ROOT / ".venv" / "bin" / "python", ROOT / "venv" / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


class Run:
    def __init__(self, run_id: str, tool_id: str, title: str, script: str, argv: list, writes: bool):
        self.id = run_id
        self.tool_id = tool_id
        self.title = title
        self.script = script
        self.argv = argv
        self.writes = writes
        self.started = time.time()
        self.ended = None
        self.returncode = None
        self.status = "running"
        self.lines = []
        self.proc = None
        self.cond = threading.Condition()
        self.log_path = RUNS_DIR / f"{run_id}.log"

    def meta(self) -> dict:
        return {"id": self.id, "tool_id": self.tool_id, "title": self.title, "script": self.script,
                "argv": self.argv, "writes": self.writes, "started": self.started, "ended": self.ended,
                "returncode": self.returncode, "status": self.status, "lines": len(self.lines),
                "command": " ".join(["python", self.script] + [_quote(a) for a in self.argv])}

    def save_meta(self) -> None:
        (RUNS_DIR / f"{self.id}.json").write_text(json.dumps(self.meta(), ensure_ascii=False, indent=1),
                                                  encoding="utf-8")


def _quote(arg: str) -> str:
    return f'"{arg}"' if (" " in arg or not arg) else arg


class Runner:
    def __init__(self):
        self.runs: dict[str, Run] = {}
        self.lock = threading.Lock()
        self._load_history()

    def _load_history(self) -> None:
        for meta_file in sorted(RUNS_DIR.glob("*.json")):
            try:
                m = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            run = Run(m["id"], m["tool_id"], m["title"], m["script"], m["argv"], m.get("writes", False))
            run.started, run.ended, run.returncode = m["started"], m.get("ended"), m.get("returncode")
            # Proces z poprzedniego uruchomienia panelu już nie żyje pod naszą kontrolą.
            run.status = m["status"] if m["status"] != "running" else "lost"
            if run.log_path.exists():
                run.lines = run.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            self.runs[run.id] = run

    def start(self, tool: dict, argv: list, writes: bool) -> Run:
        run_id = time.strftime("%Y%m%d_%H%M%S") + f"_{tool['id']}"
        with self.lock:
            while run_id in self.runs:
                run_id += "_"
            run = Run(run_id, tool["id"], tool["title"], tool["script"], argv, writes)
            self.runs[run_id] = run
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        cmd = [python_executable(), "-u", str(ROOT / tool["script"])] + argv
        run.proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                    bufsize=1, start_new_session=True)
        run.save_meta()
        threading.Thread(target=self._pump, args=(run,), daemon=True).start()
        return run

    def _pump(self, run: Run) -> None:
        with open(run.log_path, "w", encoding="utf-8") as log:
            for line in run.proc.stdout:
                line = line.rstrip("\n")
                log.write(line + "\n")
                log.flush()
                with run.cond:
                    run.lines.append(line)
                    run.cond.notify_all()
        rc = run.proc.wait()
        with run.cond:
            run.returncode = rc
            run.ended = time.time()
            if run.status == "running":
                run.status = "ok" if rc == 0 else ("partial" if rc == 2 else "failed")
            run.cond.notify_all()
        run.save_meta()

    def send_input(self, run_id: str, text: str) -> None:
        run = self.runs[run_id]
        if run.proc is None or run.proc.poll() is not None:
            raise RuntimeError("Proces już się zakończył.")
        run.proc.stdin.write(text + "\n")
        run.proc.stdin.flush()
        with run.cond:
            run.lines.append(f"» {text}")
            run.cond.notify_all()

    def stop(self, run_id: str) -> None:
        run = self.runs[run_id]
        if run.proc is None or run.proc.poll() is not None:
            return
        run.status = "stopped"
        # Cała grupa procesów - pipeline uruchamia własne podprocesy kroków.
        try:
            os.killpg(run.proc.pid, signal.SIGINT)
        except ProcessLookupError:
            return
        threading.Timer(8, self._kill_if_alive, args=(run,)).start()

    @staticmethod
    def _kill_if_alive(run: Run) -> None:
        if run.proc.poll() is None:
            try:
                os.killpg(run.proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def delete(self, run_id: str) -> None:
        run = self.runs.get(run_id)
        if run is None or run.status == "running":
            raise RuntimeError("Nie można usunąć trwającego uruchomienia.")
        del self.runs[run_id]
        for suffix in (".json", ".log"):
            (RUNS_DIR / f"{run_id}{suffix}").unlink(missing_ok=True)

    def list(self) -> list:
        return [r.meta() for r in sorted(self.runs.values(), key=lambda r: r.started, reverse=True)]

    def running_count(self) -> int:
        return sum(1 for r in self.runs.values() if r.status == "running")
