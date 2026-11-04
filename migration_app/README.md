# Panel migracji Asana → Jira

Lokalna aplikacja webowa na skryptach jiraSync: każde narzędzie uruchamia skrypt jako
podproces (z głównego folderu, interpreterem z `.venv`), pokazuje polecenie przed startem
i log na żywo. Sam serwer używa tylko biblioteki standardowej Pythona i nasłuchuje
wyłącznie na 127.0.0.1.

## Uruchomienie

```bash
./start.sh                 # z głównego folderu; przy pierwszym razie instaluje .venv i zależności
./start.sh --port 9000     # inny port; NO_BROWSER=1 ./start.sh - bez otwierania przeglądarki
```

Panel działa od pustego folderu (świeży klon repo): pliki konfiguracyjne i mapy powstają
przy pierwszym zapisie, `.env` - z `.env.example`.

## Widoki

- **Start** – dziewięć kroków nowej migracji ze stanem wykrywanym z plików: konfiguracja
  (formularz `.env`, wklejenie klucza Google → `service_account.json`), test połączeń
  (`setup_check.py`), arkusz, mapowanie osób, mapowanie statusów, sesja przeglądarki,
  podgląd (dry-run), migracja, weryfikacja.
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
  Osoby, które świadomie nie dostaną konta w Jirze, oznacza się przyciskiem „Nie w Jirze”
  (lista `asana_users_not_in_jira.json` w głównym folderze, wspólna dla wszystkich projektów;
  osobno od `user_map.json`, bo `generate_user_map.py` nadpisuje tamten plik). Ich zadania
  nie są liczone jako różnice, a oznaczenie usuwa osobę z `user_map.json`.
- **Mapowanie osób** – `people_source.py` wczytuje członków workspace'u Asany i członków
  wybranych grup Jiry, proponuje dopasowania (e-mail → ta sama część przed @ → imię i nazwisko
  → wzorzec imie.nazwisko@). Każdą osobę można dopasować ręcznie albo oznaczyć „nie w Jirze”.
  Start od obecnego `user_map.json` albo od pustej mapy (świeża migracja). Zapis tworzy
  `user_map.json` i `asana_users_not_in_jira.json` (z kopiami w `backups/`). Goście Asany są
  domyślnie ukryci.
- **Mapowanie statusów** – `status_source.py` zbiera nazwy sekcji ze wszystkich projektów
  arkusza (z liczbą projektów i zadań) i statusy projektu wzorcowego (albo wskazanego).
  Do wyboru są **tylko statusy wzorca**; propozycje: ta sama nazwa, potem znaczenie sekcji
  (Fertig/Done/Abgeschlossen → status „zakończony” wzorca, In Arbeit → „w toku”,
  Backlog/To Do → „do zrobienia”). Sekcję można pominąć (zadania nie są migrowane).
  Status dla zadań ukończonych w Asanie wybiera się jawnie (`__ukonczone__`), bo bez
  niego skrypt użyłby `Fertig`. Wpisy ze statusem spoza wzorca są oznaczane, a krok 5
  na Start jest gotowy dopiero bez nich.
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
| `sheet_projects.py` | odczyt projektów z arkusza do `cache/sheet.json` |
| `setup_check.py` | testy połączeń i konfiguracji (zakładka Start) |
| `people_source.py` | osoby z Asany i grup Jiry + propozycje dopasowań |
| `status_source.py` | sekcje Asany i statusy Jiry do mapowania |
| `cache/` | wyniki odczytów (arkusz, osoby, sekcje, testy) |
| `runs/` | historia uruchomień (metadane + log) |
| `checks/` | wyniki sprawdzeń statusów i osób |
| `backups/` | kopie plików sprzed każdego zapisu z panelu |

Nowy skrypt dodaje się jednym wpisem w `TOOLS` w `catalog.py`.
