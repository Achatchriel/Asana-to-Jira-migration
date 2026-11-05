#!/usr/bin/env python3
"""
run_full_pipeline.py

Orkiestrator — uruchamia CAŁY pipeline migracji JEDNYM poleceniem, w
poprawnej kolejności, wywołując istniejące, osobno przetestowane skrypty
jako podprocesy (NIE przepisuje ich logiki — zero ryzyka wprowadzenia
nowych błędów do wewnętrznej logiki każdego kroku):

  1. jira_sync.py               - tworzy projekty
  2. board_columns.py           - kolumny tablic i przypisanie statusów do kolumn (API, bez przeglądarki)
  3. board_columns.py --check   - kontrola zgodności tablic ze wzorcem (tylko odczyt)
  4. detect_epic_candidates.py  - wykrywa zadania z podzadaniami podzadań (przed synchronizacją zadań)
  5. asana_jira_sync.py         - synchronizuje zadania Asana -> Jira (w tym Epic/Task/Sub-task dla wykrytych w kroku 4)

Każdy krok musi się powieść, zanim uruchomi się następny — w razie błędu
pipeline ZATRZYMUJE SIĘ i pokazuje dokładnie, na którym kroku, zamiast
kontynuować na niepewnym stanie.

RÓWNOLEGŁOŚĆ (--parallel N):
  Dla KAŻDEGO kroku z osobna (nie dla całego pipeline'u na raz) uruchamia
  N procesów NACZAS, każdy z automatycznie przydzielonym --shard K/N —
  BEZ ręcznego kopiowania kluczy projektów (patrz jira_sync.py --help).
  Czeka, aż WSZYSTKIE N procesów danego kroku się skończy, ZANIM przejdzie
  do następnego kroku — więc np. wszystkie projekty muszą mieć utworzone
  kolumny, zanim jakikolwiek zacznie dostawać przypisane statusy.

  UWAGA o limitach API: N równoległych procesów DZIELI SIĘ tym samym
  limitem zapytań Jiry/Asany (limit jest per instancja, nie per proces) -
  większe N nie daje proporcjonalnego przyspieszenia. Błędy 429 i chwilowe
  błędy sieci są ponawiane przez skrypty kroków (do 4 prób), ale przy zbyt
  dużym N i tak spowalniają całość. Zalecane maksimum: 3.

WYBRANE PROJEKTY (--project-keys A,B / --keys-file plik.txt):
  Kroki, które to wspierają (kolumny, przypisanie statusów, Epiki,
  synchronizacja, weryfikacja), dostają tę listę; krok bez wsparcia
  (tworzenie projektów) jest wtedy POMIJANY z komunikatem, żeby nie ruszał
  całego arkusza przy pracy na partii projektów.

WZORZEC TABLICY: kroki 2-3 biorą układ kolumn i przypisanie statusów z tablicy
wzorcowej wiersza arkusza (kolumna board_template_id), a gdy jest pusta - z
JIRA_TEMPLATE_BOARD_ID / tablicy projektu JIRA_TEMPLATE_PROJECT_KEY z .env.
Schematy (workflow, ekrany, pola) w kroku 1 zawsze z JIRA_TEMPLATE_PROJECT_KEY.

OCHRONA PROJEKTÓW (działa w skryptach kroków, pipeline tylko o niej informuje):
  - protected_projects.txt - projekty wyłączone ze wszystkiego (znikają z arkusza),
  - sync_exclude.txt       - projekty wyłączone z synchronizacji (krok 5), np. na
                             czas naprawy powiązań.
  Pipeline NIGDY nie zapisuje do Asany (kierunek Jira -> Asana,
  jira_asana_sync.py, nie jest jego częścią).

KONTROLA PRZED STARTEM:
  - poprawność map statusów (section_status_map*.json) i szablonów
    (section_status_profiles/*.json, w tym istnienie szablonu z "__szablon__"),
  - (sesja przeglądarki nie jest już potrzebna - kroki 2-3 działają przez API).

WAŻNE ograniczenia (wynikające z możliwości poszczególnych skryptów, nie z
tego orkiestratora):
  - Krok 1 (jira_sync.py) NIE wspiera --limit — zawsze przetwarza cały
    arkusz (albo swój shard, jeśli użyjesz --parallel).
  - Kroki 2-3 (board_columns.py) działają przez API - nie potrzebują przeglądarki
    ani sesji logowania. Krok 2 w trybie --dry-run pokazuje obecny i docelowy układ
    tablic; krok 3 tylko czyta. Tablica, której workflow nie pasuje do wzorca (zapis
    zdjąłby z niej własne statusy projektu), jest pomijana z ostrzeżeniem.

CZĘŚCIOWE niepowodzenia (WAŻNE):
  - Krok 1: jeśli NIEKTÓRE projekty się nie utworzą (np. duplikat klucza,
    literówka w danych wiersza), pipeline NIE zatrzymuje się — kontynuuje
    z pozostałymi krokami dla projektów, które SIĘ udały (kod wyjścia 2 =
    "częściowa porażka", odróżniany od kodu 1 = "całkowita porażka", np.
    brak pliku .env, gdzie NIC nie powstało i sensowniej jest zatrzymać się
    od razu). Sprawdź log kroku 1, żeby zobaczyć, które konkretnie wiersze
    zawiodły i dlaczego. Przy --parallel, to samo dotyczy KAŻDEGO z N
    procesów z osobna — jeśli WSZYSTKIE zwrócą 0 albo 2, pipeline
    kontynuuje; jeśli KTÓRYKOLWIEK zwróci coś innego, pipeline się zatrzymuje.
  - Krok 5 (asana_jira_sync.py) NIGDY nie zwraca błędu z powodu częściowych
    niepowodzeń synchronizacji (to jego własne zachowanie, nie tego
    orkiestratora) — zawsze sprawdź podsumowanie tego kroku (Błędy,
    Statusy błąd, sekcje bez mapowania) albo uruchom z --verify.

Użycie:
    python run_full_pipeline.py                       # cały pipeline, sekwencyjnie, naprawdę
    python run_full_pipeline.py --parallel 3           # każdy krok jako 3 równoległe procesy
    python run_full_pipeline.py --dry-run              # podgląd (krok 3 pominięty, patrz wyżej)
    python run_full_pipeline.py --limit 5              # tylko pierwsze 5 (kroki 2-4; krok 1 zawsze cały arkusz)
    python run_full_pipeline.py --skip-columns         # pomiń kroki 2-3 (np. już zrobione ręcznie)
    python run_full_pipeline.py --skip-sync            # pomiń kroki 4-5 (tylko utwórz projekty/kolumny)
    python run_full_pipeline.py --skip epics           # pomiń tylko wykrywanie Epików (krok 4)
    python run_full_pipeline.py --skip projects --skip columns  # pomiń kilka kroków naraz
    python run_full_pipeline.py --only columns         # uruchom TYLKO jeden konkretny krok
    python run_full_pipeline.py --only sync --parallel 3   # tylko sync, 3 równoległe procesy
    python run_full_pipeline.py --skip projects --skip statuses --project-keys APPLO,APPIC   # wybrane projekty
    python run_full_pipeline.py --only sync --keys-file partia_00.txt --verify            # partia + kontrola
    python run_full_pipeline.py --only epics --force-epics --project-keys PROJ5         # ponowne wykrycie Epików
    python run_full_pipeline.py --verify                                                  # na koniec porównanie z Asaną

  Krok 6 (verify, compare_project_totals.py) jest OPCJONALNY: tylko z --verify
  albo --only verify. Bez listy projektów porównuje cały arkusz w trybie
  szybkim (--top-level, bez podzadań); z listą - pełne porównanie.
"""
import argparse
import codecs
import json
import os
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BROWSER_DIR = SCRIPT_DIR / "browser_automation"
SUMMARY_DIR = SCRIPT_DIR / "pipeline_summaries"
REPORT_DIR = SCRIPT_DIR / "logs"

# Ile szczegółów pokazać bezpośrednio w wiadomości Slack (reszta trafia do
# pełnego raportu w logs/ - Slack ucina bardzo długie wiadomości).
SLACK_MAX_ERRORS = 20
SLACK_MAX_WARNINGS = 10
SLACK_MAX_LINE = 220
CRASH_TAIL_LINES = 12

sys.path.insert(0, str(SCRIPT_DIR))
from notify import send_slack_summary

STEPS = [
    {
        "key": "projects",
        "name": "1/5 Tworzenie projektów",
        "script": SCRIPT_DIR / "jira_sync.py",
        "supports_dry_run": True,
        "supports_limit": False,
        "supports_shard": True,
        "supports_project_keys": False,
        # Kod 2 = CZĘŚCIOWE niepowodzenie (niektóre projekty zawiodły, ale
        # inne powstały poprawnie) - kontynuuj pipeline dla tych, które się
        # udały, zamiast blokować wszystko. Kod 1 (np. brak .env) nadal
        # zatrzymuje - patrz tolerable_exit_codes.
        "tolerable_exit_codes": {2},
    },
    {
        # Kolumny i przypisanie statusów do kolumn zapisywane razem, przez API (board_columns.py) -
        # bez przeglądarki i bez sesji logowania (wcześniej bulk_configure_columns.py + run_column_assignments.py).
        "key": "columns",
        "name": "2/5 Kolumny i statusy w kolumnach (API)",
        "script": SCRIPT_DIR / "board_columns.py",
        "supports_dry_run": True,
        "supports_limit": True,
        "supports_shard": True,
        "supports_project_keys": True,
        "tolerable_exit_codes": {2},  # część projektów z błędem - pozostałe są gotowe
    },
    {
        "key": "statuses",
        "name": "3/5 Kontrola kolumn i statusów (tylko odczyt)",
        "script": SCRIPT_DIR / "board_columns.py",
        "extra_args": ["--check"],
        "read_only": True,  # tylko odczyt - wykonywany także przy --dry-run
        "supports_dry_run": False,
        "supports_limit": True,
        "supports_shard": True,
        "supports_project_keys": True,
        "tolerable_exit_codes": {2},
    },
    {
        "key": "epics",
        "name": "4/5 Wykrywanie kandydatów na Epic (podzadania podzadań)",
        "script": SCRIPT_DIR / "detect_epic_candidates.py",
        "supports_dry_run": False,  # skrypt tylko czyta z Asany i zapisuje lokalny plik - nie dotyka Jiry/Asany
        "supports_limit": False,
        "supports_shard": True,
        "supports_project_keys": True,
        "tolerable_exit_codes": set(),
    },
    {
        "key": "sync",
        "name": "5/5 Synchronizacja zadań Asana -> Jira",
        "script": SCRIPT_DIR / "asana_jira_sync.py",
        "supports_dry_run": True,
        "supports_limit": True,
        "supports_shard": True,
        "supports_project_keys": True,
        "tolerable_exit_codes": set(),
    },
    {
        "key": "verify",
        "name": "6/6 Weryfikacja: porównanie z Asaną (opcjonalnie)",
        "script": SCRIPT_DIR / "compare_project_totals.py",
        "supports_dry_run": False,
        "read_only": True,          # tylko odczyt - wykonywany także przy --dry-run
        "optional": True,           # tylko z --verify albo --only verify
        "supports_limit": False,
        "supports_shard": True,
        "supports_project_keys": True,
        "tolerable_exit_codes": set(),
    },
]


def slack_enabled() -> bool:
    return bool(os.getenv("SLACK_WEBHOOK_URL", "").strip())


def keys_for_shard(keys: list, shard: "str | None") -> list:
    """Przy --parallel z listą projektów każdy proces dostaje INNE projekty (co N-ty) -
    nigdy dwa procesy na tym samym projekcie."""
    if not shard:
        return keys
    k, n = (int(x) for x in shard.split("/"))
    return keys[k - 1::n]


def build_cmd(step: dict, args: argparse.Namespace, shard: "str | None") -> list:
    cmd = [sys.executable, str(step["script"])]
    keys = keys_for_shard(args.project_keys, shard) if args.project_keys else []
    if step["key"] == "verify":
        # compare_project_totals.py: klucze jako argument pozycyjny, bez nich cały arkusz w trybie szybkim
        if keys:
            cmd.append(",".join(keys))
        else:
            cmd += ["--all", "--top-level"]
            if shard:
                cmd += ["--shard", shard]
        return cmd
    if args.dry_run and step["supports_dry_run"]:
        cmd.append("--dry-run")
    if args.limit and step["supports_limit"]:
        cmd += ["--limit", str(args.limit)]
    if keys and step.get("supports_project_keys"):
        cmd += ["--project-keys", ",".join(keys)]
    elif shard and step["supports_shard"]:
        cmd += ["--shard", shard]
    if step["key"] == "epics" and args.force_epics:
        cmd.append("--force")
    cmd += step.get("extra_args", [])
    return cmd


def read_key_list(path: Path) -> set:
    if not path.exists():
        return set()
    return {line.split("#", 1)[0].strip().upper() for line in path.read_text(encoding="utf-8").splitlines()} - {""}


def preflight(steps: list, args: argparse.Namespace) -> None:
    """Kontrole przed startem: mapy statusów, ochrona projektów, sesja przeglądarki."""
    problems = []
    # 1. mapy statusów i szablony - błąd w JSON-ie wywróciłby synchronizację w połowie
    if any(s["key"] == "sync" for s in steps):
        profiles = SCRIPT_DIR / "section_status_profiles"
        maps = sorted(SCRIPT_DIR.glob("section_status_map*.json")) + sorted(profiles.glob("*.json"))
        for path in maps:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{path.name}: niepoprawny JSON ({exc})")
                continue
            tpl = data.get("__szablon__") if isinstance(data, dict) else None
            if tpl and not (profiles / f"{tpl}.json").exists():
                problems.append(f"{path.name}: szablon '{tpl}' nie istnieje (section_status_profiles/{tpl}.json)")
        print(f"Mapy statusów: sprawdzono {len(maps)} plik(ów)" + (", BŁĘDY poniżej." if problems else ", OK."))
    # 2. ochrona projektów (egzekwują ją same skrypty - tu tylko informacja)
    protected = read_key_list(SCRIPT_DIR / "protected_projects.txt")
    excluded = read_key_list(SCRIPT_DIR / "sync_exclude.txt")
    if protected:
        ps_ = sorted(protected)
        print(f"Projekty chronione (pomijane wszędzie): "
              + (", ".join(ps_) if len(ps_) <= 15 else f"{', '.join(ps_[:10])} ... (razem {len(ps_)})"))
    if excluded:
        print(f"Projekty wyłączone z synchronizacji (sync_exclude.txt): {len(excluded)}")
    if args.project_keys:
        hit_p = [k for k in args.project_keys if k in protected]
        hit_e = [k for k in args.project_keys if k in excluded]
        if hit_p:
            print(f"  [UWAGA] Z podanych projektów CHRONIONE (zostaną pominięte): {', '.join(hit_p)}")
        if hit_e and any(s["key"] == "sync" for s in steps):
            print(f"  [UWAGA] Z podanych projektów WYKLUCZONE z synchronizacji (krok 5 je pominie): {', '.join(hit_e)}")
    # 3. sesja przeglądarki przed krokami 2-3
    if any(s.get("uses_browser") for s in steps) and not args.dry_run:
        candidates = [p for p in (SCRIPT_DIR / "auth_state.json", BROWSER_DIR / "auth_state.json") if p.exists()]
        if not candidates:
            problems.append("brak zapisanej sesji przeglądarki (auth_state.json) - uruchom "
                            "python browser_automation/save_login_session.py")
        else:
            newest = max(candidates, key=lambda p: p.stat().st_mtime)
            age_h = (time.time() - newest.stat().st_mtime) / 3600
            if age_h > 12:
                print(f"  [UWAGA] Sesja przeglądarki ma {age_h:.0f} h ({newest.relative_to(SCRIPT_DIR)}). "
                      f"Jeśli kroki 2-3 przekierują na logowanie: python browser_automation/save_login_session.py")
            if len(candidates) == 2 and abs(candidates[0].stat().st_mtime - candidates[1].stat().st_mtime) > 3600:
                print("  [UWAGA] Są dwa pliki sesji o różnym wieku (auth_state.json i browser_automation/auth_state.json); "
                      "skrypty kolumn mogą czytać starszy. Najprościej skopiować świeższy w oba miejsca.")
    if problems:
        print("\n[BŁĄD] Kontrola przed startem nie przeszła:")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)


def evaluate_results(step: dict, returncodes: list) -> "tuple[str, list, list]":
    """Zwraca (werdykt, kody_krytyczne, kody_tolerowane). Werdykt: 'ok',
    'partial' albo 'failed'. Nic nie wysyła i nie kończy procesu - to robi
    finish_step(), żeby na Slack poszła JEDNA wiadomość na krok."""
    bad = [rc for rc in returncodes if rc != 0 and rc not in step["tolerable_exit_codes"]]
    tolerable = [rc for rc in returncodes if rc in step["tolerable_exit_codes"]]
    if bad:
        return "failed", bad, tolerable
    if tolerable:
        return "partial", bad, tolerable
    return "ok", bad, tolerable


def collect_step_results(step: dict) -> dict:
    """Sumuje liczby i scala listy błędów/ostrzeżeń ze WSZYSTKICH plików
    podsumowań tego kroku (po jednym na shard), potem sprząta pliki."""
    script_stem = step["script"].stem
    result = {"totals": {}, "errors": [], "warnings": [], "dropped_errors": 0,
              "dropped_warnings": 0, "reported_shards": set()}
    if not SUMMARY_DIR.exists():
        return result
    matching = sorted(SUMMARY_DIR.glob(f"{script_stem}_*.json"))
    for path in matching:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        result["reported_shards"].add(path.stem[len(script_stem) + 1:])
        for k, v in data.items():
            if k == "_errors":
                result["errors"].extend(v or [])
            elif k == "_warnings":
                result["warnings"].extend(v or [])
            elif k == "_dropped_errors":
                result["dropped_errors"] += int(v or 0)
            elif k == "_dropped_warnings":
                result["dropped_warnings"] += int(v or 0)
            elif k.startswith("_"):
                continue  # pola techniczne (np. z nowszej/starszej wersji modułu) - nie są liczbami do pokazania
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                result["totals"][k] = result["totals"].get(k, 0) + v
    for path in matching:
        path.unlink(missing_ok=True)
    return result


def clear_stale_summaries(step: dict) -> None:
    """Usuwa pliki podsumowań po poprzednim (np. przerwanym) uruchomieniu,
    żeby nie zostały doliczone do sum bieżącego kroku."""
    if not SUMMARY_DIR.exists():
        return
    for path in SUMMARY_DIR.glob(f"{step['script'].stem}_*.json*"):
        path.unlink(missing_ok=True)


def _fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}h {m}min {sec}s" if h else (f"{m}min {sec}s" if m else f"{sec}s")


def _fmt_detail(d: dict) -> str:
    project = d.get("project") or "-"
    item = d.get("item") or ""
    msg = d.get("message") or ""
    line = f"{project}" + (f" {item}" if item else "") + f": {msg}"
    return line if len(line) <= SLACK_MAX_LINE else line[: SLACK_MAX_LINE - 1] + "…"


def _group_details(details: list) -> list:
    """Scala identyczne wpisy (ten sam projekt, zadanie i komunikat) w jeden
    z licznikiem - np. 50x ten sam 429 nie zajmie 50 linii."""
    grouped: dict = {}
    for d in details:
        key = (d.get("project"), d.get("item"), d.get("message"))
        grouped.setdefault(key, [d, 0])[1] += 1
    return [(d, n) for d, n in grouped.values()]


def _write_full_report(step: dict, results: dict, crashes: list) -> Path:
    REPORT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORT_DIR / f"pipeline_{step['key']}_{stamp}_raport.txt"
    lines = [f"Raport kroku: {step['name']}", f"Wygenerowano: {stamp}", ""]
    for title, key in (("BŁĘDY", "errors"), ("OSTRZEŻENIA", "warnings")):
        lines.append(f"=== {title} ({len(results[key])}) ===")
        lines += [f"{n}x " + _fmt_detail(d) if n > 1 else _fmt_detail(d)
                  for d, n in _group_details(results[key])]
        lines.append("")
    for shard, rc, tail in crashes:
        lines.append(f"=== PROCES {shard} zakończony kodem {rc} - ostatnie linie wyjścia ===")
        lines += tail
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _details_block(title: str, details: list, limit: int, dropped: int) -> list:
    if not details and not dropped:
        return []
    grouped = _group_details(details)
    total = len(details) + dropped
    out = [f"*{title} ({total}):*"]
    for d, n in grouped[:limit]:
        out.append(("• " + (f"{n}x " if n > 1 else "")) + _fmt_detail(d))
    hidden = len(grouped) - limit
    if hidden > 0 or dropped:
        out.append(f"… i jeszcze {max(hidden, 0) + dropped} (pełna lista w raporcie)")
    return out


def finish_step(step: dict, outcomes: list, duration: float, dry_run: bool) -> None:
    """Po zakończeniu WSZYSTKICH procesów kroku: jedna zbiorcza wiadomość
    (konsola + Slack) ze statusem, czasem, zsumowanymi liczbami oraz
    SZCZEGÓŁAMI błędów i ostrzeżeń ze wszystkich shardów. Dla procesów,
    które padły (bez podsumowania), dołącza ostatnie linie ich wyjścia.
    outcomes: lista (shard, kod_wyjścia, ostatnie_linie_wyjścia)."""
    returncodes = [rc for _, rc, _ in outcomes]
    verdict, bad, tolerable = evaluate_results(step, returncodes)
    results = collect_step_results(step)
    n_proc = len(outcomes)

    # Procesy z krytycznym kodem wyjścia - pokaż, na czym się wyłożyły.
    crashes = [(shard, rc, tail) for shard, rc, tail in outcomes
               if rc != 0 and rc not in step["tolerable_exit_codes"]]
    missing = [shard for shard, _, _ in outcomes
               if shard.replace("/", "-") not in results["reported_shards"]]

    icon = {"ok": "✅", "partial": "⚠️", "failed": "❌"}[verdict]
    label = {"ok": "zakończony", "partial": "zakończony z częściowymi błędami",
             "failed": "ZAKOŃCZONY BŁĘDEM - pipeline zatrzymany"}[verdict]
    lines = [f"{icon} *{step['name']}* {label}" + (" [DRY-RUN]" if dry_run else "")]
    lines.append(f"Procesy: {n_proc} | Czas: {_fmt_duration(duration)}"
                 + (f" | Kody wyjścia: {returncodes}" if verdict != "ok" else ""))
    if results["totals"]:
        lines.append(" | ".join(f"{k.replace('_', ' ')}: {v}" for k, v in results["totals"].items()))
        if missing and results["reported_shards"]:
            lines.append(f"Uwaga: brak podsumowania z procesów {', '.join(missing)} - sumy są niepełne.")
    elif not crashes:
        lines.append("(ten skrypt nie raportuje liczb - sprawdź log kroku)")

    err_block = _details_block("Błędy", results["errors"], SLACK_MAX_ERRORS, results["dropped_errors"])
    warn_block = _details_block("Ostrzeżenia", results["warnings"], SLACK_MAX_WARNINGS, results["dropped_warnings"])
    if err_block:
        lines += [""] + err_block
    if warn_block:
        lines += [""] + warn_block
    for shard, rc, tail in crashes:
        shown = [ln[:SLACK_MAX_LINE] for ln in tail[-CRASH_TAIL_LINES:]] or ["(brak wyjścia)"]
        lines += ["", f"*Proces {shard} padł (kod {rc}), ostatnie linie:*", "```", *shown, "```"]

    if results["errors"] or results["warnings"] or crashes:
        report_path = _write_full_report(step, results, crashes)
        try:
            shown_path = report_path.relative_to(SCRIPT_DIR)
        except ValueError:
            shown_path = report_path
        lines += ["", f"Pełny raport: {shown_path}"]

    message = "\n".join(lines)
    print("\n[PODSUMOWANIE KROKU]\n" + message.replace("*", ""))
    send_slack_summary(message)
    print("[Slack] wysłano podsumowanie kroku." if slack_enabled() else
          "[Slack] NIE wysłano - brak SLACK_WEBHOOK_URL w .env.")

    if verdict == "failed":
        print("Pipeline ZATRZYMANY - popraw problem i uruchom ponownie. Możesz pominąć już")
        print("wykonane kroki przez --only/--skip, żeby nie powtarzać ich od zera.")
        sys.exit(max(bad))


_print_lock = threading.Lock()


def _pump_output(proc: subprocess.Popen, prefix: str, tail: deque) -> None:
    """Przepisuje wyjście podprocesu na konsolę NA BIEŻĄCO, kawałkami (nie
    linia po linii - inaczej prompty bez znaku nowej linii, np. input()
    w skryptach Playwright, nie pokazałyby się przed odpowiedzią) i
    zapamiętuje ostatnie linie na wypadek, gdyby proces padł bez zapisania
    podsumowania. Prefiks sharda dodawany na początku każdej linii."""
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    fd = proc.stdout.fileno()
    current = ""
    at_line_start = True
    while True:
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        text = decoder.decode(chunk)
        parts = text.split("\n")
        out = []
        for i, part in enumerate(parts):
            is_last = i == len(parts) - 1
            if (part or not is_last) and at_line_start and prefix:
                out.append(prefix)
            out.append(part)
            current += part
            if not is_last:
                out.append("\n")
                tail.append(current)
                current = ""
                at_line_start = True
            elif part:
                at_line_start = False
        with _print_lock:
            sys.stdout.write("".join(out))
            sys.stdout.flush()
    rest = decoder.decode(b"", final=True)
    current += rest
    if current:
        tail.append(current)


def _start_process(cmd: list, env: dict, prefix: str) -> tuple:
    proc = subprocess.Popen(cmd, cwd=SCRIPT_DIR, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, bufsize=0)
    tail: deque = deque(maxlen=40)
    thread = threading.Thread(target=_pump_output, args=(proc, prefix, tail), daemon=True)
    thread.start()
    return proc, thread, tail


def run_step(step: dict, args: argparse.Namespace) -> None:
    if args.dry_run and not step["supports_dry_run"] and not step.get("read_only"):
        print(f"\n[POMINIĘTO] {step['name']} — ten skrypt nie ma trybu --dry-run, "
              f"a wywołanie go naprawdę zaprzeczałoby intencji podglądu. "
              f"Uruchom bez --dry-run, kiedy będziesz gotów/a wykonać ten krok naprawdę.")
        return

    parallel_n = args.parallel or 1
    if args.project_keys and parallel_n > len(args.project_keys):
        parallel_n = len(args.project_keys)  # więcej procesów niż projektów nie ma sensu
    if parallel_n > 1 and not step["supports_shard"]:
        print(f"\n[UWAGA] {step['name']} nie wspiera --shard — uruchamiam jako 1 proces mimo --parallel {parallel_n}.")
        parallel_n = 1

    print("\n" + "=" * 70)
    print(f"KROK: {step['name']}" + (f" ({parallel_n} równoległych procesów)" if parallel_n > 1 else ""))
    print("=" * 70)

    clear_stale_summaries(step)
    # PIPELINE_RUN=1: skrypty kroków nie wysyłają własnych, cząstkowych
    # wiadomości na Slack (po jednej na shard) - zbiorczą wysyła finish_step().
    # PYTHONUNBUFFERED: wyjście podprocesu płynie na konsolę na bieżąco,
    # mimo że jest przechwytywane (bez tego pojawiałoby się porcjami).
    child_env = {**os.environ, "PIPELINE_RUN": "1", "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    started = time.monotonic()

    shards = [None] if parallel_n == 1 else [f"{k}/{parallel_n}" for k in range(1, parallel_n + 1)]
    running = []
    for shard in shards:
        # cwd=SCRIPT_DIR (główny folder, NIE folder samego skryptu!) - skrypty w
        # browser_automation/ oczekują uruchomienia z głównego folderu, bo
        # względne ścieżki w .env są liczone względem folderu roboczego procesu.
        cmd = build_cmd(step, args, shard=shard)
        print(f"Polecenie{f' [{shard}]' if shard else ''}: {' '.join(cmd)}")
        prefix = f"[{shard}] " if shard else ""
        env = {**child_env, "PIPELINE_SHARD": shard} if shard else child_env
        running.append((shard or "single", *_start_process(cmd, env, prefix)))

    if parallel_n > 1:
        print(f"\nCzekam na zakończenie wszystkich {parallel_n} proces(ów)...")
    outcomes = []
    for shard, proc, thread, tail in running:
        rc = proc.wait()
        thread.join(timeout=10)
        outcomes.append((shard, rc, list(tail)))
        if parallel_n > 1:
            print(f"  [{shard}] zakończony, kod wyjścia {rc}")

    finish_step(step, outcomes, time.monotonic() - started, args.dry_run)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Uruchamia cały pipeline migracji (projekty -> kolumny -> statusy -> sync) jednym poleceniem."
    )
    parser.add_argument("--dry-run", action="store_true",
                         help="Podgląd bez zapisu, tam gdzie to możliwe (patrz ograniczenia w docstring).")
    parser.add_argument("--limit", type=int, default=None,
                         help="Ogranicz liczbę przetwarzanych projektów w krokach, które to wspierają (2, 3 i 5; krok 1 zawsze cały arkusz/shard).")
    parser.add_argument("--project-keys", default=None,
                        help="Tylko te projekty (A,B,C). Krok 1 (tworzenie projektów) jest wtedy pomijany.")
    parser.add_argument("--keys-file", default=None,
                        help="Plik z kluczami projektów (jeden w linii, '#' = komentarz), np. partia_00.txt.")
    parser.add_argument("--force-epics", action="store_true",
                        help="Krok 4 z --force: wykryj kandydatów na Epic od nowa (np. po zmianach struktury w Asanie).")
    parser.add_argument("--verify", action="store_true",
                        help="Na koniec krok 6: porównanie z Asaną (compare_project_totals.py).")
    parser.add_argument("--parallel", type=int, default=None,
                         help="Uruchom KAŻDY krok jako N równoległych procesów (automatyczny --shard K/N, "
                              "bez ręcznego kopiowania kluczy). Zacznij od 2-3 — patrz uwaga o limitach API w docstring.")
    parser.add_argument("--skip-columns", action="store_true", help="Pomiń kroki 2-3 (kolumny + statusy).")
    parser.add_argument("--skip-sync", action="store_true", help="Pomiń kroki 4-5 (wykrywanie Epików + synchronizację zadań).")
    parser.add_argument("--skip", metavar="KROK", action="append", dest="skip_steps",
                        choices=[s["key"] for s in STEPS], default=[],
                        help="Pomiń konkretny krok po jego kluczu (można podać wielokrotnie). "
                             f"Dostępne klucze: {', '.join(s['key'] for s in STEPS)}.")
    parser.add_argument("--only", choices=[s["key"] for s in STEPS], default=None,
                         help="Uruchom TYLKO jeden konkretny krok, ignorując --skip-*.")
    args = parser.parse_args()

    if args.parallel is not None and args.parallel < 1:
        print("[BŁĄD] --parallel musi być liczbą całkowitą >= 1.")
        sys.exit(1)

    keys = []
    if args.project_keys:
        keys += [k.strip().upper() for k in args.project_keys.split(",") if k.strip()]
    if args.keys_file:
        kf = Path(args.keys_file)
        if not kf.exists():
            print(f"[BŁĄD] Nie ma pliku {kf}.")
            sys.exit(1)
        keys += sorted(read_key_list(kf))
    args.project_keys = list(dict.fromkeys(keys))  # bez powtórzeń, kolejność zachowana

    if args.only:
        steps_to_run = [s for s in STEPS if s["key"] == args.only]
        if args.verify and args.only != "verify":
            steps_to_run += [s for s in STEPS if s["key"] == "verify"]  # --only X --verify = X + weryfikacja
    else:
        steps_to_run = [s for s in STEPS if not s.get("optional") or (s["key"] == "verify" and args.verify)]
        if args.skip_columns:
            steps_to_run = [s for s in steps_to_run if s["key"] not in ("columns", "statuses")]
        if args.skip_sync:
            steps_to_run = [s for s in steps_to_run if s["key"] not in ("epics", "sync")]
        if args.skip_steps:
            steps_to_run = [s for s in steps_to_run if s["key"] not in args.skip_steps]

    if args.project_keys:
        unsupported = [s for s in steps_to_run if not s.get("supports_project_keys")]
        if args.only and unsupported:
            print(f"[BŁĄD] Krok '{args.only}' nie obsługuje wyboru projektów (--project-keys/--keys-file).")
            sys.exit(1)
        for s in unsupported:
            print(f"[POMINIĘTO] {s['name']} - nie obsługuje wyboru projektów, przy --project-keys/--keys-file "
                  f"nie jest uruchamiany (uruchom go osobno dla całego arkusza, jeśli trzeba).")
        steps_to_run = [s for s in steps_to_run if s.get("supports_project_keys")]
        print(f"Projekty: {len(args.project_keys)} ({', '.join(args.project_keys[:10])}"
              + (", ..." if len(args.project_keys) > 10 else "") + ")")

    missing = [s for s in steps_to_run if not s["script"].exists()]
    if missing:
        print("[BŁĄD] Nie znaleziono następujących skryptów:")
        for s in missing:
            print(f"  {s['script']}")
        sys.exit(1)

    if not steps_to_run:
        print("[BŁĄD] Brak kroków do wykonania po zastosowaniu opcji.")
        sys.exit(1)
    preflight(steps_to_run, args)
    print(f"Pipeline: {len(steps_to_run)} krok(ów) do wykonania: {', '.join(s['key'] for s in steps_to_run)}.")
    if slack_enabled():
        print("Slack: podsumowanie po każdym kroku i na koniec.")
        send_slack_summary(
            f"▶️ *run_full_pipeline.py* start: {', '.join(s['key'] for s in steps_to_run)}"
            + (f" | projekty: {', '.join(args.project_keys[:15])}{' …' if len(args.project_keys) > 15 else ''}"
               if args.project_keys else " | cały arkusz")
            + (f" | procesy: {args.parallel}" if args.parallel and args.parallel > 1 else "")
            + (" [DRY-RUN]" if args.dry_run else ""))
    else:
        print("[UWAGA] Brak SLACK_WEBHOOK_URL w .env - komunikaty na Slacka NIE będą wysyłane.")
    if args.parallel and args.parallel > 1:
        print(f"Każdy krok: {args.parallel} równoległych procesów (automatyczny podział --shard).")
    if args.dry_run:
        print("Tryb --dry-run: kroki bez wsparcia dla podglądu zostaną POMINIĘTE (nie wykonane na sucho).")

    pipeline_started = time.monotonic()
    for step in steps_to_run:
        run_step(step, args)

    print("\n" + "=" * 70)
    print("CAŁY PIPELINE ZAKOŃCZONY POMYŚLNIE.")
    print("=" * 70)
    send_slack_summary(f"🏁 *run_full_pipeline.py* zakończony: {len(steps_to_run)} krok(ów), "
                       f"łączny czas {_fmt_duration(time.monotonic() - pipeline_started)}"
                       + (" [DRY-RUN]" if args.dry_run else ""))


if __name__ == "__main__":
    main()
