#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
parse_jira_pdfs.py

Wyciąga konfigurację z PDF-ów będących eksportami ekranów administracyjnych
Jiry (zrzuty z przeglądarki) i składa je w jeden plik YAML zgodny ze
schematem build_jira_schema.py.

Rozpoznaje dwa rodzaje PDF-ów po nazwie pliku:
  - "Pola*"    -> lista custom fields (nazwa, typ, opis, ID)
  - "Screen-*" -> zawartość jednej zakładki ekranu (lista pól w kolejności)

Nazwa pliku "Screen-<TYP>-<ZAKŁADKA>.pdf" koduje typ zgłoszenia i nazwę
zakładki, np. "Screen-Story-Zakładka_główna.pdf".

Użycie:
    python parse_jira_pdfs.py --input /ścieżka/do/pdfów --output jira_schema_z_pdf.yaml
"""
import argparse
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree

import yaml

# Kolumna z treścią (pola / zakładki) zaczyna się na tej współrzędnej X.
# Wszystko na lewo to menu nawigacyjne Jiry, które trzeba odfiltrować.
CONTENT_X_MIN = 300.0

# Mapowanie polskich nazw typów pól z UI Jiry na klucze schematu
TYPE_MAP = {
    "krótki tekst": "textfield",
    "pole tekstowe": "textarea",
    "długi tekst": "textarea",
    "pole liczbowe": "float",
    "liczba": "float",
    "selektor daty": "datepicker",
    "data": "datepicker",
    "data i godzina": "datetime",
    "lista wyboru": "select",
    "pojedynczy wybór": "select",
    "wielokrotnego wyboru": "multiselect",
    "pola wyboru": "multiselect",
    "etykiety": "labels",
    "selektor użytkownika": "userpicker",
    "użytkownik": "userpicker",
    "adres url": "url",
    "url": "url",
}

# Pola systemowe Jiry - NIE tworzymy ich jako custom fields, tylko
# odwołujemy się po angielskiej nazwie w definicjach ekranów.
SYSTEM_FIELDS = {
    "podsumowanie": "Summary",
    "opis": "Description",
    "osoba przypisana": "Assignee",
    "osoba zgłaszająca": "Reporter",
    "priorytet": "Priority",
    "etykiety": "Labels",
    "termin": "Due Date",
    "załącznik": "Attachment",
    "komponenty": "Components",
    "wersje poprawek": "Fix Versions",
    "wersje, których dotyczy": "Affects Versions",
    "element nadrzędny": "Parent",
    "łącze epiku": "Epic Link",
    "rejestrowanie czasu": "Time Tracking",
    "środowisko": "Environment",
    "powiązane zgłoszenia": "Linked Issues",
}


def pdf_words(pdf_path: Path) -> list:
    """Zwraca listę (x, y, tekst) dla każdego słowa w PDF."""
    out = subprocess.run(["pdftotext", "-bbox-layout", str(pdf_path), "-"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"pdftotext nie powiodło się dla {pdf_path.name}")
    root = ElementTree.fromstring(out.stdout)
    ns = {"x": "http://www.w3.org/1999/xhtml"}
    words = []
    for w in root.iter():
        if not w.tag.endswith("word"):
            continue
        words.append((float(w.get("xMin")), float(w.get("yMin")), (w.text or "").strip()))
    return words


def lines_from_words(words: list, x_min: float = CONTENT_X_MIN, y_tol: float = 3.0) -> list:
    """Grupuje słowa w linie po współrzędnej Y, odfiltrowując lewą kolumnę."""
    filtered = [(x, y, t) for x, y, t in words if x >= x_min and t]
    filtered.sort(key=lambda w: (w[1], w[0]))
    lines, current, current_y = [], [], None
    for x, y, t in filtered:
        if current_y is None or abs(y - current_y) <= y_tol:
            current.append((x, t))
            current_y = y if current_y is None else current_y
        else:
            # Sortuj po X - grupowanie idzie po Y, więc bez tego słowa z tej
            # samej linii wychodzą w kolejności pionowej, nie czytania
            # (potwierdzone: nazwa pola sklejała się z kolumną "konteksty").
            lines.append((current_y, sorted(current, key=lambda i: i[0])))
            current, current_y = [(x, t)], y
    if current:
        lines.append((current_y, sorted(current, key=lambda i: i[0])))
    return lines


def parse_fields_pdf(pdf_path: Path) -> list:
    """Parsuje PDF z listą pól. Zwraca [{name, type, description, jira_id}]."""
    words = pdf_words(pdf_path)
    lines = lines_from_words(words, x_min=200.0)

    fields = []
    for _, items in lines:
        text = " ".join(t for _, t in items)
        m = re.search(r"customfield_(\d+)", text)
        if not m:
            continue
        jira_id = f"customfield_{m.group(1)}"

        # Nazwa to tekst przed opisem typu; typ rozpoznajemy po słowniku
        lowered = text.lower()
        ftype, type_pos = None, None
        for pl_name, key in TYPE_MAP.items():
            pos = lowered.find(pl_name)
            if pos >= 0 and (type_pos is None or pos < type_pos):
                ftype, type_pos = key, pos
        if type_pos is None:
            continue

        name = text[:type_pos].strip()
        # Usuń resztki menu nawigacyjnego doklejone z lewej strony
        name = re.sub(r"^(Przepływy pracy|Schematy workflow|Pola|Priorytety|"
                      r"Połączenia danych|Zrzuty ekranów|Formularze|Statusy|"
                      r"Rozwiązania|Schematy pól|Schematy priorytetów)\s+", "", name).strip()
        # Usuń kolumnę "N kontekst(ów)", jeśli wylądowała w nazwie
        name = re.sub(r"\s*\d+\s+konteks\w*\s*$", "", name).strip()
        if not name or name.endswith("…"):
            # Nazwa ucięta w UI - zapisujemy, ale oznaczamy do ręcznego uzupełnienia
            name = name.rstrip("… ") or f"NIEZNANE_{jira_id}"
            fields.append({"name": name, "type": ftype, "jira_id": jira_id,
                           "_truncated": True})
            continue
        fields.append({"name": name, "type": ftype, "jira_id": jira_id})
    return fields


def parse_screen_pdf(pdf_path: Path) -> dict:
    """Parsuje PDF z zawartością zakładki ekranu.
    Zwraca {screen_name, tab_name, fields: [...]}."""
    words = pdf_words(pdf_path)
    lines = lines_from_words(words)

    # Nazwa ekranu z tekstu wprowadzającego
    full_text = " ".join(t for _, items in lines for _, t in items)
    m = re.search(r"używających ekranu\s+(.+?)\s*\.\s*Wyświetlane", full_text)
    screen_name = m.group(1).strip() if m else pdf_path.stem

    # Nazwa zakładki z nazwy pliku: Screen-<TYP>-<ZAKŁADKA>
    stem = re.sub(r"^\d+_", "", pdf_path.stem)
    parts = stem.split("-", 2)
    issue_type = parts[1] if len(parts) > 1 else "?"
    tab_name = parts[2].replace("_", " ") if len(parts) > 2 else "Zakładka główna"

    # Pola: linie zawierające dokładnie jedną pozycję tekstową w kolumnie pól.
    # Odrzucamy zdania instruktażowe i elementy UI.
    skip_patterns = [
        "to są pola", "pola w pierwszej", "wielowierszowe", "możesz skonfigurować",
        "wybierz pole", "wskaż pole", "dodaj pole", "konfiguruj ekran",
        "udostępniono", "system.", "jira", "search", "utwórz", "zapytaj",
        "ustawienia", "przełącz", "wyszukaj",
    ]
    # Pozycje menu nawigacyjnego Jiry, które wchodzą w kolumnę treści
    # na niektórych zrzutach - odrzucamy po dokładnym dopasowaniu nazwy.
    nav_items = {
        "zgłoszenia", "typy zgłoszeń", "hierarchia typów zgłoszeń",
        "schematy typów zgłoszeń", "podzadania", "przepływy pracy",
        "schematy workflow", "zrzuty ekranów", "schematy zrzutów ekranów",
        "schematy ekranu typów zgłoszeń", "pola", "schematy pól", "priorytety",
        "schematy priorytetów", "formularze", "połączenia danych",
        "funkcje zgłoszeń", "rejestrowanie czasu", "tworzenie powiązań zgłoszeń",
        "atrybuty zgłoszeń", "statusy", "rozwiązania",
        "schematy zabezpieczeń zgłoszeń", "schematy powiadomień",
        "schematy uprawnień", "9+",
    }
    fields = []
    for _, items in lines:
        text = " ".join(t for _, t in items)
        # Normalizacja: spacje nierozdzielające (U+00A0), wielokrotne spacje,
        # oraz znaki z Prywatnego Obszaru Użytkownika (U+E000-U+F8FF) - to
        # glify ikon z fontu Atlassian, które pdftotext zwraca jako "tekst"
        # i które doklejały się do nazw pól ("Zgłoszenia \uf18c").
        text = re.sub(r"[\ue000-\uf8ff]", "", text.replace("\u00a0", " "))
        text = re.sub(r"\s+", " ", text).strip()
        # "Usuń" to przycisk renderowany OBOK nazwy pola - usuwamy go z tekstu
        # zamiast pomijać całą linię (wcześniej "usuń" było wzorcem pomijania,
        # przez co ginęły pola mające ten przycisk w tej samej linii, np. "Opis").
        text = re.sub(r"^\s*Usuń\s+|\s+Usuń\s*$", "", text).strip()
        if text.lower() == "usuń":
            continue
        low = text.lower()
        if not text or low in nav_items or any(p in low for p in skip_patterns):
            continue
        if len(text) > 60 or text.isdigit():
            continue
        if text and text not in fields:
            fields.append(text)

    return {"screen_name": screen_name, "issue_type": issue_type,
            "tab_name": tab_name, "fields": fields}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", required=True, help="Katalog z PDF-ami.")
    p.add_argument("--output", default="jira_schema_z_pdf.yaml", help="Wyjściowy YAML.")
    args = p.parse_args()

    in_dir = Path(args.input)
    pdfs = sorted(in_dir.glob("*.pdf"))
    if not pdfs:
        print(f"[BŁĄD] Brak PDF-ów w {in_dir}")
        sys.exit(1)

    all_fields, screens_raw, unparsed = [], [], []

    for pdf in pdfs:
        stem = re.sub(r"^\d+_", "", pdf.stem)
        try:
            if stem.lower().startswith("pola"):
                parsed = parse_fields_pdf(pdf)
                all_fields.extend(parsed)
                print(f"  [POLA] {stem}: {len(parsed)} pól")
            elif stem.lower().startswith("screen"):
                parsed = parse_screen_pdf(pdf)
                screens_raw.append(parsed)
                print(f"  [EKRAN] {stem}: {len(parsed['fields'])} pól "
                      f"(zakładka '{parsed['tab_name']}')")
            else:
                unparsed.append(stem)
        except Exception as exc:  # noqa: BLE001
            print(f"  [BŁĄD] {stem}: {exc}")
            unparsed.append(stem)

    # Grupuj zakładki wg nazwy ekranu
    by_screen = defaultdict(list)
    for s in screens_raw:
        by_screen[s["screen_name"]].append(s)

    screens = []
    for screen_name, tabs in sorted(by_screen.items()):
        screens.append({
            "name": screen_name,
            "tabs": [{"name": t["tab_name"], "fields": t["fields"]} for t in tabs],
        })

    # Deduplikuj pola po nazwie
    seen, custom_fields = set(), []
    truncated = []
    for f in all_fields:
        if f["name"] in seen:
            continue
        seen.add(f["name"])
        entry = {"name": f["name"], "type": f["type"]}
        if f.get("description"):
            entry["description"] = f["description"]
        entry["_jira_id"] = f["jira_id"]  # referencyjnie, nieużywane przy tworzeniu
        custom_fields.append(entry)
        if f.get("_truncated"):
            truncated.append(f["jira_id"])

    schema = {
        "prefix": "",
        "custom_fields": custom_fields,
        "screens": screens,
    }

    out = Path(args.output)
    out.write_text(
        "# Wygenerowane automatycznie przez parse_jira_pdfs.py\n"
        "# ZWERYFIKUJ przed użyciem — patrz uwagi w podsumowaniu.\n\n"
        + yaml.dump(schema, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8")

    print("\n" + "=" * 60)
    print(f"Pól: {len(custom_fields)} | Ekranów: {len(screens)} "
          f"| Zakładek: {len(screens_raw)}")
    if truncated:
        print(f"\n[UWAGA] {len(truncated)} pól ma nazwę uciętą w UI Jiry (kończy się '…') "
              f"— uzupełnij ręcznie: {', '.join(truncated)}")
    if unparsed:
        print(f"\n[UWAGA] Nierozpoznane pliki: {', '.join(unparsed)}")
    print(f"\nZapisano: {out}")
    print("=" * 60)


if __name__ == "__main__":
    main()
