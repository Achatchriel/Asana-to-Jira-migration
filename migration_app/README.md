# Panel migracji Asana → Jira

Lokalna aplikacja webowa na istniejących skryptach jiraSync. Niczego w nich nie zmienia: każde
narzędzie uruchamia skrypt jako podproces (z głównego folderu, interpreterem z `.venv`),
pokazuje polecenie przed startem i log na żywo.

## Uruchomienie

```bash
./migration_app/panel.sh
```

Potem otwórz http://127.0.0.1:8765. Nic nie trzeba doinstalowywać (tylko biblioteka
standardowa Pythona). Serwer nasłuchuje wyłącznie na 127.0.0.1.

## Widoki

- **Przegląd** – braki w `.env`, wiek sesji przeglądarki (kroki 2-3), stan synchronizacji,
  chronione/wyłączone projekty, ostatnie uruchomienia.
- **Projekty** – projekty z arkusza Google (bez dostępu do arkusza: z pliku stanu i
  `project_key_cache.json`), wpisy w stanie, mapa sekcji, flagi, wynik ostatniego sprawdzenia.
  Zaznaczone projekty trafiają automatycznie do pól „Projekty” w narzędziach.
- **Narzędzia** – 67 skryptów w grupach: pipeline, Asana, weryfikacja, naprawy, konfiguracja
  Jiry, kopia projektów, inne. Uruchomienie, które zmienia dane, wymaga potwierdzenia.
- **Statusy i osoby** – `asana_check.py` (tylko odczyt): sekcje Asany → statusy Jiry (te same
  reguły co synchronizacja: mapa projektu, szablon, mapa główna, `__ukonczone__`), osoby →
  konta Jiry (`user_map.json`, czy aktywne, czy można przypisać w projekcie) oraz różnice
  statusu/przypisanego dla już zmigrowanych zadań. Brakujące mapowanie sekcji można zapisać
  od razu do `section_status_map.<KLUCZ>.json`.
- **Uruchomienia** – historia z logami; odpowiedzi dla skryptów, które pytają o potwierdzenie
  (pole wejścia); przerwanie całej grupy procesów.
- **Konfiguracja** – `.env` (sekrety zamaskowane, puste pole = bez zmian), mapy statusów,
  szablony, `user_map.json`, `sync_exclude.txt`, `protected_projects.txt`, schematy YAML.
- **Raporty** – `logs/`, `pipeline_summaries/`, `repair_reports/`, raporty CSV/JSON (CSV jako tabela).

## Pliki panelu

| Ścieżka | Co to |
|---|---|
| `server.py` | serwer HTTP + API |
| `catalog.py` | katalog narzędzi: parametry formularzy, kiedy narzędzie zapisuje |
| `runner.py` | podprocesy, logi, stdin, przerywanie |
| `asana_check.py` | sprawdzenie statusów i przypisań (można też uruchomić ręcznie) |
| `sheet_projects.py` | odczyt projektów z arkusza (JSON) |
| `runs/` | historia uruchomień (metadane + log) |
| `checks/` | wyniki sprawdzeń statusów i osób |
| `backups/` | kopie plików sprzed każdego zapisu z panelu |

Nowy skrypt dodaje się jednym wpisem w `TOOLS` w `catalog.py`.
