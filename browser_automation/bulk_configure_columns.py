#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
browser_automation/bulk_configure_columns.py

CIENKA NAKŁADKA - cała rzeczywista logika żyje w JEDNYM miejscu:
../bulk_configure_columns.py (główny folder projektu). Ten plik istnieje
tylko dlatego, że run_full_pipeline.py historycznie uruchamia go pod TĄ
ścieżką jako osobny proces (Krok 2).

WAŻNE, DLACZEGO TO ISTNIEJE: wcześniej to były DWIE PEŁNE, NIEZALEŻNE
kopie tego samego kodu - łatwo się rozjeżdżały (potwierdzony ręcznie
przypadek: poprawka assign_jira_key() trafiła tylko do jednej z nich,
powodując TypeError w drugiej). Ta nakładka eliminuje możliwość
rozjechania się na przyszłość - jest dosłownie NIEMOŻLIWE, żeby te dwa
pliki miały różną logikę, bo ten w ogóle jej nie ma, tylko przekazuje
wykonanie dalej.

Jeśli coś tu wymaga poprawki - edytuj ../bulk_configure_columns.py,
NIE ten plik.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bulk_configure_columns import *  # noqa: F401,F403 - re-eksport wszystkiego (funkcje, stałe)
from bulk_configure_columns import main

if __name__ == "__main__":
    main()
