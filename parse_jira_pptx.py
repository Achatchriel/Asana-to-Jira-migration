#!/usr/bin/env python3
"""
parse_jira_pptx.py

Wyciąga definicje pól z prezentacji opisującej konfigurację Jiry
(tabele "Nazwa pola | Typ | Czy synchronizowane | Dopuszczane wartości").

Uzupełnia to, czego NIE dało się odczytać z eksportów PDF ekranów: typ pola
i dozwolone wartości dla list wyboru. PDF-y ekranów podają tylko nazwy pól
w kolejności, bez typów.

Parsowanie opiera się na kolumnie "Typ" jako kotwicy: extract-text spłaszcza
tabelę do strumienia komórek bez separatorów wierszy, więc jedyne, co
niezawodnie wyznacza granicę rekordu, to rozpoznana wartość typu.

Użycie:
    python parse_jira_pptx.py --input prezentacja.pptx --output pola_z_prezentacji.yaml
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

import yaml

# Wartości kolumny "Typ" -> klucz schematu build_jira_schema.py.
# None = pole systemowe Jiry (nie tworzymy go, tylko używamy na ekranach).
TYPE_MAP = {
    "systemowe": None,
    "systemowe /jira sprint field": None,
    "jira sprint field": None,
    "text field (single line)": "textfield",
    "text field (multi-line)": "textarea",
    "akapit (obsługuje tekst sformatowany)": "textarea",
    "akapit": "textarea",
    "lista wyboru (jeden wybór)": "select",
    "lista wyboru (wiele wyborów)": "multiselect",
    "pole wyboru": "select",
    "number field": "float",
    "pole liczbowe": "float",
    "date picker": "datepicker",
    "selektor daty": "datepicker",
    "user picker": "userpicker",
    "labels": "labels",
    "url field": "url",
}

HEADER_CELLS = {"nazwa pola", "typ", "czy synchronizowane", "dopuszczane wartości"}
SYNC_VALUES = {"tak", "nie"}


def extract_cells(pptx: Path) -> list:
    out = subprocess.run(["extract-text", str(pptx)], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"extract-text nie powiodło się: {out.stderr[:200]}")
    cells = []
    for line in out.stdout.splitlines():
        line = re.sub(r"\s+", " ", line.replace("\u00a0", " ")).strip()
        if line and not line.startswith("## Slide"):
            cells.append(line)
    return cells


def parse_fields(cells: list) -> list:
    """Idzie po strumieniu komórek. Rozpoznana wartość 'Typ' kończy nazwę pola;
    następna komórka to Tak/Nie; kolejne (aż do następnej nazwy) to opis."""
    fields, pending_name = [], []
    i = 0
    while i < len(cells):
        cell = cells[i]
        low = cell.lower().rstrip(".")

        if low in HEADER_CELLS:
            pending_name = []
            i += 1
            continue

        if low in TYPE_MAP:
            name = " ".join(pending_name).strip()
            pending_name = []
            ftype = TYPE_MAP[low]
            i += 1

            synced = None
            if i < len(cells) and cells[i].lower() in SYNC_VALUES:
                synced = cells[i].lower() == "tak"
                i += 1

            # Opis/dozwolone wartości: komórki do następnego typu, nagłówka
            # albo Tak/Nie (czyli początku kolejnego rekordu)
            desc_parts = []
            while i < len(cells):
                nxt = cells[i].lower().rstrip(".")
                if nxt in TYPE_MAP or nxt in HEADER_CELLS or nxt in SYNC_VALUES:
                    break
                desc_parts.append(cells[i])
                i += 1

            # Ostatni fragment opisu może być nazwą następnego pola - nie da się
            # tego rozstrzygnąć jednoznacznie, więc oddajemy go jako nazwę
            # kolejnego rekordu i oznaczamy pole jako wymagające weryfikacji.
            ambiguous = False
            if desc_parts and i < len(cells) and cells[i].lower().rstrip(".") in TYPE_MAP:
                pending_name = [desc_parts.pop()]
                ambiguous = bool(desc_parts)

            if not name:
                continue
            entry = {"name": name, "type": ftype, "synced": synced}
            if desc_parts:
                entry["allowed"] = " ".join(desc_parts)
            if ambiguous:
                entry["_verify"] = True
            fields.append(entry)
            continue

        pending_name.append(cell)
        i += 1
    return fields


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", required=True)
    p.add_argument("--output", default="pola_z_prezentacji.yaml")
    args = p.parse_args()

    pptx = Path(args.input)
    if not pptx.exists():
        print(f"[BŁĄD] Brak pliku: {pptx}")
        sys.exit(1)

    cells = extract_cells(pptx)
    fields = parse_fields(cells)

    # Deduplikacja po nazwie (pola powtarzają się na slajdach różnych typów zgłoszeń)
    seen, unique = {}, []
    for f in fields:
        key = f["name"].lower()
        if key in seen:
            # Uzupełnij brakujące dane z powtórzenia
            prev = seen[key]
            if not prev.get("allowed") and f.get("allowed"):
                prev["allowed"] = f["allowed"]
            continue
        seen[key] = f
        unique.append(f)

    custom = [f for f in unique if f["type"] is not None]
    system = [f for f in unique if f["type"] is None]

    out_data = {
        "custom_fields": [
            {k: v for k, v in f.items() if k != "type"} | {"type": f["type"]}
            for f in custom
        ],
        "pola_systemowe": [f["name"] for f in system],
    }
    out = Path(args.output)
    out.write_text(
        "# Wygenerowane przez parse_jira_pptx.py z prezentacji.\n"
        "# 'pola_systemowe' to pola wbudowane Jiry — NIE są tworzone, tylko używane na ekranach.\n"
        "# Wpisy z '_verify: true' wymagają sprawdzenia (granica rekordu niejednoznaczna).\n\n"
        + yaml.dump(out_data, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8")

    verify = [f["name"] for f in unique if f.get("_verify")]
    print(f"Pól niestandardowych: {len(custom)} | systemowych: {len(system)}")
    if verify:
        print(f"\n[UWAGA] Do weryfikacji ({len(verify)}): {', '.join(verify)}")
    print(f"\nZapisano: {out}")


if __name__ == "__main__":
    main()
