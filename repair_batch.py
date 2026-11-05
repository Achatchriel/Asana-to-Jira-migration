#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
repair_batch.py

Uruchamia sprawdzone narzędzia naprawcze dla WIELU projektów po kolei (nigdy
równolegle - każde narzędzie zapisuje stan) i zbiera wynik w jedną tabelę.
Każdy projekt ma własny folder repair_reports/<KLUCZ>/ z raportami i pełnymi
logami, więc raporty się nie nadpisują, a krok "dup" korzysta z raportu "relink"
właściwego projektu.

Kroki (w tej kolejności):
  relink    relink_state.py           - odtworzenie zgubionych powiązań
  dup       resolve_relink_duplicates - duplikaty / nadmiarowe kopie (z raportu relink)
  migrated  handle_migrated_to_jira   - zadania "migriert zu Jira"
Pozostałe kroki przyjmują wiele kluczy same:
  refresh_state_flags.py / audit_hierarchy.py / fix_stale_resolution.py /
  detect_epic_candidates.py / asana_jira_sync.py / compare_project_totals.py
  --project-keys A,B,C

Użycie:
    python repair_batch.py relink   --project-keys A,B,C                 # raporty + tabela
    python repair_batch.py relink   --keys-file projekty.txt --apply      # zapis PEWNE
    python repair_batch.py relink   --keys-file projekty.txt --apply --include-similar --include-order
    python repair_batch.py dup      --keys-file projekty.txt              # plany + tabela
    python repair_batch.py dup      --keys-file projekty.txt --apply      # pyta raz o potwierdzenie
    python repair_batch.py migrated --keys-file projekty.txt [--apply]
projekty.txt: jeden klucz w linii, '#' = komentarz.

Równolegle (różne projekty w każdym procesie, max 3 - limity API Asany i Jiry):
    python repair_batch.py relink --keys-file projekty.txt --shard 1/3    # i 2/3, 3/3 w osobnych terminalach
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPORTS = HERE / "repair_reports"
RELINK_CATS = ["LINK", "PEWNE", "PODOBNE", "KOLEJNOSC", "TRESC", "DUPLIKAT", "NADMIAROWA_KOPIA",
               "NIEJEDNOZNACZNE", "BRAK_W_ASANIE", "NIEZMIGROWANE"]


def read_keys(args) -> list:
    keys = []
    if args.project_keys:
        keys += [k.strip().upper() for k in args.project_keys.split(",") if k.strip()]
    if args.keys_file:
        for line in Path(args.keys_file).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                keys.append(line.upper())
    seen, out = set(), []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    if args.shard:
        part, total = (int(x) for x in args.shard.split("/"))
        out = [k for i, k in enumerate(out) if i % total == part - 1]
    return out


def run(cmd: list, log: Path, stdin_text: "str | None" = None) -> "tuple[int, str]":
    log.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run([sys.executable, *cmd], cwd=HERE, capture_output=True, text=True, input=stdin_text)
    out = proc.stdout + ("\n--- stderr ---\n" + proc.stderr if proc.stderr else "")
    log.write_text(out, encoding="utf-8")
    return proc.returncode, out


def step_relink(keys, args):
    rows = []
    for n, key in enumerate(keys, 1):
        d = REPORTS / key
        cmd = ["relink_state.py", "--project-key", key, "--report-file", str(d / "relink.csv")]
        if args.apply:
            cmd.append("--apply")
            cmd += [f for f, on in (("--include-similar", args.include_similar), ("--include-order", args.include_order),
                                    ("--include-content", args.include_content)) if on]
        print(f"[{n}/{len(keys)}] {key}: relink{' --apply' if args.apply else ''}...", flush=True)
        code, out = run(cmd, d / ("relink_apply.log" if args.apply else "relink.log"))
        counts = {m.group(1): int(m.group(2)) for m in re.finditer(r"^\s{2}([A-Z_]+): (\d+)$", out, re.M)}
        restored = re.search(r"Odtworzono wpisów: (\d+)", out)
        rows.append((key, code, counts, int(restored.group(1)) if restored else None))
    print("\n" + "=" * 110)
    head = ["projekt"] + [c[:8] for c in RELINK_CATS] + (["odtw."] if args.apply else [])
    print(" ".join(f"{h:>9}" for h in head))
    for key, code, counts, restored in rows:
        vals = [key[:9]] + [str(counts.get(c, "-")) for c in RELINK_CATS] + ([str(restored or 0)] if args.apply else [])
        print(" ".join(f"{v:>9}" for v in vals) + ("   [BŁĄD - patrz log]" if code else ""))
    print(f"\nRaporty i logi: {REPORTS}/<KLUCZ>/")


def confirm(what: str, args) -> bool:
    if not args.apply or args.yes:
        return True
    return input(f"\n{what} Wpisz TAK: ").strip() == "TAK"


def step_dup(keys, args):
    if args.apply and not confirm(f"Usunąć NIEODWRACALNIE duplikaty według planów w {len(keys)} projektach?", args):
        print("Przerwano, nic nie zmieniono.")
        return
    rows = []
    pat = re.compile(r"Zadań: (\d+) \| do usunięcia kopii: (\d+) \(zgłoszeń: (\d+).*?\| do decyzji: (\d+) \| pominięte: (\d+)")
    for n, key in enumerate(keys, 1):
        d = REPORTS / key
        if not (d / "relink.csv").exists():
            rows.append((key, None, "brak raportu relink - najpierw: repair_batch.py relink"))
            continue
        cmd = ["resolve_relink_duplicates.py", "--project-key", key, "--relink-report", str(d / "relink.csv"),
               "--plan-file", str(d / "dup_plan.csv")]
        if args.include_orphans:
            cmd.append("--include-orphans")
        if args.apply:
            cmd += ["--apply", "--yes"]
        print(f"[{n}/{len(keys)}] {key}: duplikaty{' --apply' if args.apply else ''}...", flush=True)
        code, out = run(cmd, d / ("dup_apply.log" if args.apply else "dup.log"))
        m = pat.search(out)
        deleted = re.search(r"Usunięto: (\d+), błędy: (\d+)", out)
        info = (f"zadań {m.group(1)}, do usunięcia {m.group(3)} zgł., do decyzji {m.group(4)}, pominięte {m.group(5)}"
                if m else ("brak duplikatów w raporcie" if "Brak par DUPLIKAT" in out else "patrz log"))
        if deleted:
            info += f" | USUNIĘTO {deleted.group(1)}, błędy {deleted.group(2)}"
        rows.append((key, code, info))
    print("\n" + "=" * 100)
    for key, code, info in rows:
        print(f"  {key:<12} {info}" + ("   [BŁĄD - patrz log]" if code else ""))
    print(f"\nPlany i logi: {REPORTS}/<KLUCZ>/dup_plan.csv, dup*.log")


def step_migrated(keys, args):
    if args.apply and not confirm(f"Usunąć kopie zadań 'migriert zu Jira' według planów w {len(keys)} projektach?", args):
        print("Przerwano, nic nie zmieniono.")
        return
    rows = []
    for n, key in enumerate(keys, 1):
        d = REPORTS / key
        cmd = ["handle_migrated_to_jira.py", "--project-key", key, "--plan-file", str(d / "migrated_plan.csv")]
        if args.apply:
            cmd += ["--apply", "--yes"]
        print(f"[{n}/{len(keys)}] {key}: migriert zu Jira{' --apply' if args.apply else ''}...", flush=True)
        code, out = run(cmd, d / ("migrated_apply.log" if args.apply else "migrated.log"))
        roots = re.search(r"dopiskiem 'migriert zu Jira': (\d+), razem z poddrzewami: (\d+)", out)
        counts = re.search(r"^\s{2}((?:[A-ZĄĘŁŃÓŚŹŻ_]+: \d+(?:, )?)+)$", out, re.M)
        info = (f"zadań z dopiskiem {roots.group(1)} (z poddrzewami {roots.group(2)})" if roots else "patrz log")
        if counts:
            info += f" | {counts.group(1)}"
        rows.append((key, code, info))
    print("\n" + "=" * 100)
    for key, code, info in rows:
        print(f"  {key:<12} {info}" + ("   [BŁĄD - patrz log]" if code else ""))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=["relink", "dup", "migrated"])
    parser.add_argument("--project-keys", default=None)
    parser.add_argument("--keys-file", default=None)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--yes", action="store_true", help="Bez pytania o potwierdzenie (dup/migrated)")
    parser.add_argument("--shard", default=None, help="K/N - ten proces bierze co N-ty projekt, zaczynając od K-tego")
    parser.add_argument("--include-similar", action="store_true")
    parser.add_argument("--include-order", action="store_true")
    parser.add_argument("--include-content", action="store_true")
    parser.add_argument("--include-orphans", action="store_true")
    args = parser.parse_args()
    keys = read_keys(args)
    if not keys:
        parser.error("podaj --project-keys albo --keys-file")
    print(f"Projektów: {len(keys)} | krok: {args.step}{' (ZAPIS)' if args.apply else ' (raport)'}\n")
    {"relink": step_relink, "dup": step_dup, "migrated": step_migrated}[args.step](keys, args)


if __name__ == "__main__":
    main()
