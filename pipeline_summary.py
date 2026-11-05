#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
pipeline_summary.py

Wspólny mechanizm podsumowań dla run_full_pipeline.py.

Każdy skrypt kroku zapisuje swoje liczby ORAZ szczegóły błędów/ostrzeżeń
(jeden plik na shard) do pipeline_summaries/<nazwa_skryptu>_<shard>.json.
Po zakończeniu WSZYSTKICH shardów danego kroku orkiestrator sumuje liczby,
scala listy błędów i wysyła JEDNĄ zbiorczą wiadomość na Slack.

Użycie w skrypcie:
    from pipeline_summary import record_error, record_warning, write_shard_summary
    ...
    except Exception as exc:
        record_error(jira_key, f"zadanie '{name}'", exc)
    ...
    write_shard_summary("nazwa_skryptu", args.shard, {"utworzone": 5, "błędy": 1})

Gdy skrypt jest uruchomiony przez orkiestrator (PIPELINE_RUN=1), NIE
powinien wysyłać własnej, cząstkowej wiadomości - robi to orkiestrator.
"""
import json
import os
from pathlib import Path

SUMMARY_DIR = Path(__file__).resolve().parent / "pipeline_summaries"

# Limit szczegółów zapisywanych przez JEDEN proces - chroni przed wielkimi
# plikami przy masowej awarii (np. 10k identycznych błędów 429). Liczba
# pominiętych jest i tak raportowana.
MAX_DETAILS_PER_SHARD = 500

_ERRORS: list = []
_WARNINGS: list = []
_dropped = {"errors": 0, "warnings": 0}


def is_pipeline_child() -> bool:
    """True, gdy skrypt działa jako podproces run_full_pipeline.py."""
    return os.getenv("PIPELINE_RUN") == "1"


def _record(target: list, kind: str, project, item, message) -> None:
    if len(target) >= MAX_DETAILS_PER_SHARD:
        _dropped[kind] += 1
        return
    target.append({
        "project": str(project or "-"),
        "item": str(item or ""),
        "message": " ".join(str(message).split())[:1000],  # jedna linia, bez gigantycznych treści
    })


def record_error(project, item, message) -> None:
    """Zapamiętuje szczegół BŁĘDU do zbiorczego podsumowania kroku."""
    _record(_ERRORS, "errors", project, item, message)


def record_warning(project, item, message) -> None:
    """Zapamiętuje szczegół OSTRZEŻENIA do zbiorczego podsumowania kroku."""
    _record(_WARNINGS, "warnings", project, item, message)


def write_shard_summary(script_stem: str, shard_arg: "str | None", counts: dict) -> None:
    """Zapisuje liczby i zebrane szczegóły TEGO procesu do pipeline_summaries/.
    Zapis atomowy (plik tymczasowy + os.replace). Błąd zapisu nie przerywa
    samego skryptu - to tylko raportowanie."""
    try:
        SUMMARY_DIR.mkdir(exist_ok=True)
        # Orkiestrator ustawia PIPELINE_SHARD każdemu procesowi - także wtedy, gdy przekazuje mu
        # --project-keys zamiast --shard. Bez tego wszystkie procesy pisały do "_single.json"
        # i nadpisywały sobie podsumowania (zostawało tylko ostatnie).
        shard_arg = os.getenv("PIPELINE_SHARD") or shard_arg
        suffix = shard_arg.replace("/", "-") if shard_arg else "single"
        path = SUMMARY_DIR / f"{script_stem}_{suffix}.json"
        payload = dict(counts)
        payload["_errors"] = _ERRORS
        payload["_warnings"] = _WARNINGS
        payload["_dropped_errors"] = _dropped["errors"]
        payload["_dropped_warnings"] = _dropped["warnings"]
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001
        print(f"[OSTRZEŻENIE] Nie udało się zapisać podsumowania dla orkiestratora: {exc}")
