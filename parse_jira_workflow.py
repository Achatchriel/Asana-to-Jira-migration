#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
parse_jira_workflow.py

Wyciąga definicję workflow (statusy + przejścia) z eksportu HTML widoku
tekstowego przepływu pracy Jiry ("Zarządzanie przepływem pracy" ->
widok tekstowy -> zapis strony jako HTML).

Struktura, którą parsuje (na komórkę przejścia):
    <nazwa przejścia> (<id>)  [status źródłowy...]  --strzałka SVG-->  <status docelowy>

Strzałka (<svg> w div.css-j8fuf8) rozdziela statusy źródłowe od docelowego -
bez niej nie da się odróżnić kierunku, bo oba są zwykłymi <span>.

Użycie:
    python parse_jira_workflow.py --input Workflow-Story-TXT.html --name "Workflow Story"
    python parse_jira_workflow.py --input-dir . --output workflows.yaml
"""
import argparse
import re
import sys
from pathlib import Path

import yaml

ARROW_MARKER = "@@ARROW@@"


def _clean(text: str) -> str:
    """Usuwa znaki z Prywatnego Obszaru Unicode (ikony) i normalizuje spacje."""
    text = re.sub(r"[\ue000-\uf8ff]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_workflow_html(path: Path, workflow_name: str = None) -> dict:
    html = path.read_text(encoding="utf-8", errors="replace")

    m = re.search(r"<table.*?</table>", html, re.S)
    if not m:
        raise RuntimeError(f"Nie znaleziono tabeli workflow w {path.name}")
    table = m.group(0)

    rows = re.findall(r"<tr.*?</tr>", table, re.S)
    statuses, transitions, current_status = [], {}, None

    for row in rows:
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)
        if not cells:
            continue

        # Wiersz ze statusem ma osobną komórkę "Nazwa (id)"; wiersze
        # kontynuacji mają tylko komórkę z przejściem.
        for cell in cells[:-1]:
            txt = _clean(re.sub(r"<[^>]*>", " ", cell))
            sm = re.fullmatch(r"(.+?)\s+\((\d+)\)", txt)
            if sm:
                current_status = sm.group(1).strip()
                if current_status not in [s["name"] for s in statuses]:
                    statuses.append({"name": current_status, "id": sm.group(2)})

        # Komórka z przejściem: zamieniamy strzałkę (SVG) na marker tekstowy.
        # Bez tego kierunek przejścia jest nie do odczytania - statusy
        # źródłowy i docelowy to identyczne <span>-y, rozdzielone tylko ikoną.
        cell = cells[-1]
        marked = re.sub(r"<svg.*?</svg>", ARROW_MARKER, cell, flags=re.S)

        # Rozbij na bloki po nazwie przejścia "Nazwa (id)"
        plain = _clean(re.sub(r"<[^>]*>", " ", marked))
        if not plain or ARROW_MARKER not in plain:
            continue

        for tm in re.finditer(r"(.+?)\s+\((\d+)\)\s+(.*?)(?=\S+\s+\(\d+\)\s|$)",
                              plain, re.S):
            t_name, t_id, body = tm.group(1).strip(), tm.group(2), tm.group(3)
            # Marker strzałki z POPRZEDNIEGO przejścia wpada w nazwę następnego
            # (bloki nie mają separatora poza samą strzałką) - obcinamy.
            t_name = _clean(t_name.replace(ARROW_MARKER, ""))
            if ARROW_MARKER not in body:
                continue
            src_part, _, dst_part = body.partition(ARROW_MARKER)
            src = _clean(src_part.replace(ARROW_MARKER, " "))
            dst = _clean(dst_part.split(ARROW_MARKER)[0])
            if not dst:
                continue
            transitions[t_id] = {"name": t_name, "id": t_id,
                                 "from_raw": src, "to": dst}

    return {
        "name": workflow_name or path.stem,
        "statuses": statuses,
        "transitions": list(transitions.values()),
    }


def split_statuses(raw: str, known: list) -> list:
    """Rozdziela sklejony ciąg nazw statusów na listę, dopasowując do znanych
    nazw (najdłuższe najpierw, żeby 'Done' nie zjadło 'Back To Done')."""
    if not raw:
        return []
    result, rest = [], raw
    for name in sorted(known, key=len, reverse=True):
        while name in rest:
            result.append(name)
            rest = rest.replace(name, " ", 1)
    leftover = _clean(rest)
    if leftover and leftover.lower() not in {"dowolny status", "siebie"}:
        result.append(leftover)
    if "dowolny status" in raw.lower():
        return ["ANY"]
    return result


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", help="Pojedynczy plik HTML.")
    p.add_argument("--input-dir", help="Katalog z plikami *-TXT.html.")
    p.add_argument("--output", default="workflows.yaml")
    args = p.parse_args()

    files = []
    if args.input:
        files = [Path(args.input)]
    elif args.input_dir:
        files = sorted(Path(args.input_dir).glob("*TXT*.html"))
    if not files:
        print("[BŁĄD] Podaj --input lub --input-dir z plikami *TXT*.html")
        sys.exit(1)

    workflows = []
    for f in files:
        try:
            wf = parse_workflow_html(f)
            known = [s["name"] for s in wf["statuses"]]
            for t in wf["transitions"]:
                t["from"] = split_statuses(t.pop("from_raw"), known)
            workflows.append(wf)
            print(f"  [OK] {f.name}: {len(wf['statuses'])} statusów, "
                  f"{len(wf['transitions'])} przejść")
        except Exception as exc:  # noqa: BLE001
            print(f"  [BŁĄD] {f.name}: {exc}")

    out = Path(args.output)
    out.write_text(yaml.dump({"workflows": workflows}, allow_unicode=True,
                             sort_keys=False, width=120), encoding="utf-8")
    print(f"\nZapisano: {out}")


if __name__ == "__main__":
    main()
