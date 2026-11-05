#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
repair_state_file.py

Naprawia uszkodzony task_sync_state.json (przerwany w trakcie zapisu -
niepoprawny JSON w środku pliku) przez znalezienie NAJWIĘKSZEGO poprawnego
prefiksu (przeszukiwanie binarne: obcina plik w różnych miejscach, domyka
brakujące nawiasy klamrowe, sprawdza czy wynik jest poprawnym JSON-em).

Znaleziony poprawny prefiks to niemal na pewno stan SPRZED przerwanego
zapisu - czyli wszystko, co było zapisane PRZED tym jednym, feralnym
zapisem. Tracimy TYLKO ten jeden, przerwany zapis (garść zadań z jednego
sharda) - nie całą historię synchronizacji.

Użycie:
    python repair_state_file.py                    # naprawia task_sync_state.json
    python repair_state_file.py inny_plik.json      # naprawia podany plik
"""
import json
import sys
from pathlib import Path


def count_unclosed_braces(text):
    """Liczy niedomknięte { poza literałami tekstowymi (uwzględnia \" i \\)."""
    depth = 0
    in_string = False
    escaped = False
    for ch in text:
        if escaped:
            escaped = False
            continue
        if ch == "\\" and in_string:
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
    return depth


def try_parse_with_closing(text):
    """Próbuje sparsować tekst, domykając brakujące klamry na końcu.
    Dodatkowo usuwa ewentualny wiszący przecinek przed domknięciem."""
    depth = count_unclosed_braces(text)
    if depth < 0:
        return None  # więcej zamknięć niż otwarć - ten prefiks jest zepsuty inaczej
    candidate = text.rstrip()
    candidate = candidate.rstrip(",")
    candidate += "}" * depth
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def find_largest_valid_prefix(content):
    """Przeszukiwanie binarne: znajduje największą długość N, dla której
    content[:N] (z domkniętymi klamrami) jest poprawnym JSON-em."""
    lo, hi = 0, len(content)
    best_result = None
    best_n = 0

    result = try_parse_with_closing(content)
    if result is not None:
        return result, len(content)

    while lo <= hi:
        mid = (lo + hi) // 2
        result = try_parse_with_closing(content[:mid])
        if result is not None:
            best_result = result
            best_n = mid
            lo = mid + 1
        else:
            hi = mid - 1

    if best_result is None:
        raise ValueError("Nie znaleziono ŻADNEGO poprawnego prefiksu - plik jest uszkodzony od samego początku.")
    return best_result, best_n


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "task_sync_state.json")
    if not path.exists():
        print(f"[BŁĄD] Nie znaleziono {path}.")
        sys.exit(1)

    content = path.read_text(encoding="utf-8")
    print(f"Wczytano {len(content)} znaków z {path}.")

    try:
        json.loads(content)
        print("Plik jest JUŻ poprawnym JSON-em - nic do naprawy.")
        return
    except json.JSONDecodeError as e:
        print(f"Potwierdzone uszkodzenie: {e}")

    print("Szukam największego poprawnego prefiksu (przeszukiwanie binarne)...")
    repaired, n = find_largest_valid_prefix(content)

    lost_chars = len(content) - n
    print(f"\nZnaleziono poprawny prefiks: {n} z {len(content)} znaków "
          f"(utracono {lost_chars} znaków, {lost_chars / len(content) * 100:.3f}% pliku).")
    print(f"Odzyskano {len(repaired)} projekt(ów), "
          f"{sum(len(v) for v in repaired.values())} wpisów zadań łącznie.")

    backup_path = path.with_suffix(".json.corrupted_backup")
    backup_path.write_text(content, encoding="utf-8")
    print(f"\nKopia USZKODZONEGO oryginału zachowana: {backup_path}")

    path.write_text(json.dumps(repaired, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Naprawiony plik zapisany: {path}")
    print("\nMożesz teraz bezpiecznie wznowić synchronizację - odzyskany stan")
    print("zostanie poprawnie rozpoznany, a jedynie garść zadań z PRZERWANEGO")
    print("zapisu zostanie ponownie przetworzona (nadal bezpiecznie, dzięki")
    print("idempotentności synchronizacji).")


if __name__ == "__main__":
    main()
