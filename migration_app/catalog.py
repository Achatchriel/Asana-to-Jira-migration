"""
catalog.py

Katalog narzędzi, które panel potrafi uruchomić. Każde narzędzie to ISTNIEJĄCY
skrypt z głównego folderu jiraSync - panel tylko składa linię poleceń z
formularza i uruchamia go jako podproces (logika skryptów zostaje nietknięta).

Rodzaje parametrów (kind):
  bool    - flaga bez wartości (--dry-run)
  str/int - flaga z wartością (--limit 5)
  keys    - lista kluczy projektów po przecinku (w panelu: wybór z listy projektów)
  choice  - jedna z wartości z "options"
  pos     - argument pozycyjny (bez flagi), w kolejności w "params"
  repeat  - flaga powtarzana dla każdej linii wartości (--extra-group A=B)

Zapis (writes) - kiedy uruchomienie ZMIENIA dane (panel wymaga wtedy potwierdzenia):
  "never"            - tylko odczyt / raport
  "always"           - zawsze zapisuje
  "unless:--dry-run" - zapisuje, chyba że zaznaczono podaną flagę
  "with:--apply,..." - zapisuje tylko z którąś z podanych flag
"""

GROUPS = [
    ("pipeline", "Pipeline migracji"),
    ("asana", "Asana: statusy i przypisania"),
    ("verify", "Weryfikacja"),
    ("repair", "Naprawy"),
    ("jira_config", "Konfiguracja Jiry"),
    ("copy", "Kopia projektów"),
    ("other", "Inne"),
]

DRY = {"flag": "--dry-run", "kind": "bool", "label": "Tylko podgląd (dry-run)", "default": True}
APPLY = {"flag": "--apply", "kind": "bool", "label": "Wprowadź zmiany (--apply)"}
YES = {"flag": "--yes", "kind": "bool", "label": "Bez pytania o potwierdzenie (--yes)"}
LIMIT = {"flag": "--limit", "kind": "int", "label": "Limit projektów"}
KEYS = {"flag": "--project-keys", "kind": "keys", "label": "Projekty"}
SHARD = {"flag": "--shard", "kind": "str", "label": "Shard K/N", "placeholder": "1/3"}
KEY1 = {"flag": "--project-key", "kind": "keys", "label": "Projekt", "single": True, "required": True}


def pos(label, **kw):
    return {"kind": "pos", "label": label, **kw}


TOOLS = [
    # ------------------------------------------------------------------ pipeline
    {"id": "sheet_load", "group": "pipeline", "script": "migration_app/sheet_projects.py",
     "title": "Wczytaj arkusz projektów",
     "desc": "Odczyt arkusza Google do cache panelu (lista projektów). Może potrwać kilka minut, gdy wiersze nie mają nazw.",
     "writes": "never", "params": []},
    {"id": "full_pipeline", "group": "pipeline", "script": "run_full_pipeline.py",
     "title": "Pełny pipeline",
     "desc": "Projekty → kolumny i statusy tablic (API) → kontrola tablic → kandydaci na Epiki → synchronizacja zadań (opcjonalnie weryfikacja). Bez przeglądarki.",
     "writes": "unless:--dry-run",
     "params": [DRY, KEYS,
                {"flag": "--keys-file", "kind": "str", "label": "Plik z kluczami", "placeholder": "partia_00.txt"},
                {"flag": "--limit", "kind": "int", "label": "Limit projektów"},
                {"flag": "--parallel", "kind": "int", "label": "Równolegle (N procesów, maks. 3)"},
                {"flag": "--verify", "kind": "bool", "label": "Na koniec porównanie z Asaną (--verify)"},
                {"flag": "--force-epics", "kind": "bool", "label": "Wykryj Epiki od nowa (--force-epics)"},
                {"flag": "--skip-columns", "kind": "bool", "label": "Pomiń kolumny i statusy kolumn"},
                {"flag": "--skip-sync", "kind": "bool", "label": "Pomiń Epiki i synchronizację"},
                {"flag": "--only", "kind": "choice", "label": "Tylko jeden krok",
                 "options": ["", "projects", "columns", "statuses", "epics", "sync", "verify"]},
                {"flag": "--skip", "kind": "repeat", "label": "Pomiń kroki (po jednym w linii)",
                 "placeholder": "columns"}]},
    {"id": "jira_sync", "group": "pipeline", "script": "jira_sync.py",
     "title": "1. Tworzenie projektów",
     "desc": "Tworzy w Jirze projekty z arkusza Google (cały arkusz albo shard).",
     "writes": "unless:--dry-run", "params": [DRY, SHARD]},
    {"id": "board_columns", "group": "pipeline", "script": "board_columns.py",
     "title": "2. Kolumny i statusy w kolumnach",
     "desc": "Układ kolumn i przypisanie statusów według tablicy wzorcowej - przez API, bez przeglądarki. Tablica, której workflow nie pasuje do wzorca, jest pomijana (chyba że --force).",
     "writes": "unless:--dry-run",
     "params": [DRY, KEYS, LIMIT,
                {"flag": "--template-board-id", "kind": "str", "label": "ID tablicy wzorcowej (domyślnie z arkusza / .env)"},
                {"flag": "--force", "kind": "bool", "label": "Zapisz mimo niepasującego workflow (--force)"},
                SHARD]},
    {"id": "board_columns_check", "group": "pipeline", "script": "board_columns.py",
     "title": "3. Kontrola kolumn i statusów",
     "desc": "Czy tablice mają kolumny i statusy jak wzorzec - tylko odczyt.",
     "writes": "never", "fixed_args": ["--check"],
     "params": [KEYS, LIMIT,
                {"flag": "--template-board-id", "kind": "str", "label": "ID tablicy wzorcowej (domyślnie z arkusza / .env)"}, SHARD]},
    {"id": "columns", "group": "other", "script": "bulk_configure_columns.py",
     "title": "Kolumny tablic (stara wersja, przeglądarka)",
     "desc": "Poprzednia wersja kroku 2 - przez przeglądarkę (Playwright). Zastąpiona przez „2. Kolumny i statusy w kolumnach”.",
     "writes": "unless:--dry-run", "browser": True,
     "params": [DRY, KEYS, LIMIT,
                {"flag": "--template-board-id", "kind": "str", "label": "ID tablicy wzorcowej"},
                {"flag": "--keep-old-columns", "kind": "bool", "label": "Zostaw stare kolumny"},
                {"flag": "--interactive", "kind": "bool", "label": "Widoczna przeglądarka + pauza"},
                SHARD]},
    {"id": "column_assignments", "group": "other", "script": "browser_automation/run_column_assignments.py",
     "title": "Statusy w kolumnach (stara wersja, przeglądarka)",
     "desc": "Poprzednia wersja kroku 3 - przez przeglądarkę (Playwright), bez trybu podglądu. Zastąpiona przez „2. Kolumny i statusy w kolumnach”.",
     "writes": "always", "browser": True,
     "params": [KEYS, LIMIT,
                {"flag": "--template-board-id", "kind": "str", "label": "ID tablicy wzorcowej"},
                {"flag": "--delay-ms", "kind": "int", "label": "Przerwa między projektami (ms)", "placeholder": "800"},
                SHARD]},
    {"id": "detect_epics", "group": "pipeline", "script": "detect_epic_candidates.py",
     "title": "4. Kandydaci na Epiki",
     "desc": "Wykrywa w Asanie zadania z podzadaniami podzadań (zapisuje epic_candidates.json).",
     "writes": "never",
     "params": [KEYS, SHARD, {"flag": "--force", "kind": "bool", "label": "Skanuj od nowa (--force)"}]},
    {"id": "asana_jira_sync", "group": "pipeline", "script": "asana_jira_sync.py",
     "title": "5. Synchronizacja Asana → Jira",
     "desc": "Zadania, podzadania, komentarze, załączniki, statusy i przypisania.",
     "writes": "unless:--dry-run", "params": [DRY, KEYS, LIMIT, SHARD]},
    {"id": "jira_asana_sync", "group": "pipeline", "script": "jira_asana_sync.py",
     "title": "Kierunek zwrotny Jira → Asana",
     "desc": "Przenosi zmiany z Jiry z powrotem do Asany. Zapisuje do Asany!",
     "writes": "unless:--dry-run", "params": [DRY, KEYS, LIMIT]},
    {"id": "save_login", "group": "pipeline", "script": "browser_automation/save_login_session.py",
     "title": "Zapis sesji przeglądarki",
     "desc": "Otwiera przeglądarkę do ręcznego logowania (SSO/2FA). Potrzebne tylko starszym narzędziom przez przeglądarkę (stare kroki kolumn, copy_project.py, kopia z innej instancji) - pipeline i „Kopia projektu” działają bez niej. Naciśnij Enter w polu wejścia po zalogowaniu.",
     "writes": "never", "browser": True, "params": []},
    {"id": "project_attachments", "group": "pipeline", "script": "migrate_project_level_attachments.py",
     "title": "Załączniki poziomu projektu",
     "desc": "Przenosi pliki przypięte bezpośrednio do projektu w Asanie.",
     "writes": "unless:--dry-run", "params": [DRY, LIMIT]},
    {"id": "backfill_links", "group": "pipeline", "script": "backfill_jira_links.py",
     "title": "Uzupełnij jira_link w arkuszu",
     "desc": "Wpisuje linki do projektów Jiry w kolumnę jira_link arkusza.",
     "writes": "unless:--dry-run", "params": [DRY]},

    # ------------------------------------------------------------------ asana
    {"id": "status_check", "group": "asana", "script": "migration_app/asana_check.py",
     "title": "Statusy i przypisania (sprawdzenie)",
     "desc": "Sekcje → statusy, osoby → konta Jiry i zgodność zmigrowanych zadań. Wyniki w zakładce „Statusy i osoby”.",
     "writes": "never", "params": [pos("Projekty", required=True, kind_hint="keys"),
                                   {"flag": "--fresh-sheet", "kind": "bool", "label": "Czytaj arkusz od nowa (wolne)"}]},
    {"id": "status_source", "group": "asana", "script": "migration_app/status_source.py",
     "title": "Sekcje i statusy do mapowania",
     "desc": "Sekcje ze wszystkich projektów arkusza i statusy projektu Jiry - źródło strony „Mapowanie statusów”.",
     "writes": "never",
     "params": [{"flag": "--project", "kind": "keys", "single": True, "label": "Projekt ze statusami (domyślnie wzorcowy)"}]},
    {"id": "all_sections", "group": "asana", "script": "list_all_asana_sections.py",
     "title": "Wszystkie sekcje Asany",
     "desc": "Sekcje ze wszystkich projektów arkusza - do uzupełniania map statusów.",
     "writes": "never",
     "params": [LIMIT, {"flag": "--save", "kind": "str", "label": "Zapisz do JSON", "placeholder": "sekcje.json"}]},
    {"id": "project_sections", "group": "asana", "script": "list_asana_sections.py",
     "title": "Sekcje jednego projektu",
     "desc": "Unikalne sekcje projektu Asany (podaj nazwę projektu albo link).",
     "writes": "never",
     "params": [{"flag": "--link", "kind": "str", "label": "Link do projektu Asany"},
                pos("albo nazwa projektu")]},
    {"id": "asana_members", "group": "asana", "script": "list_asana_members.py",
     "title": "Członkowie workspace'u",
     "desc": "gid, imię i e-mail osób w Asanie.",
     "writes": "never",
     "params": [{"flag": "--only-members", "kind": "bool", "label": "Tylko członkowie"},
                {"flag": "--only-guests", "kind": "bool", "label": "Tylko goście"},
                {"flag": "--include-inactive", "kind": "bool", "label": "Także nieaktywni"},
                {"flag": "--json", "kind": "str", "label": "Zapisz do JSON"}]},
    {"id": "user_map", "group": "asana", "script": "generate_user_map.py",
     "title": "Generuj user_map.json",
     "desc": "Dopasowuje osoby Asana → Jira po e-mailu i zapisuje user_map.json.",
     "writes": "always",
     "params": [{"flag": "--include-guests", "kind": "bool", "label": "Uwzględnij gości"}]},
    {"id": "task_section", "group": "asana", "script": "check_task_section.py",
     "title": "Sekcja pojedynczego zadania",
     "desc": "Diagnostyka sekcji zadania Asany dwiema metodami.",
     "writes": "never",
     "params": [pos("GID zadania", required=True), pos("GID projektu", required=True)]},
    {"id": "project_statuses", "group": "asana", "script": "list_project_statuses.py",
     "title": "Statusy projektu Jiry",
     "desc": "Statusy faktycznie dostępne w projekcie - cel map sekcji.",
     "writes": "never", "params": [pos("Klucz projektu", required=True, kind_hint="key")]},
    {"id": "asana_token", "group": "asana", "script": "check_asana_token.py",
     "title": "Test tokenu Asany", "desc": "Sprawdza ASANA_TOKEN przez /users/me.",
     "writes": "never", "params": []},

    # ------------------------------------------------------------------ verify
    {"id": "count_migrated", "group": "verify", "script": "count_migrated_tasks.py",
     "title": "Liczba zadań w Jirze", "desc": "Łączna liczba zadań w projektach migracji, opcjonalnie vs Asana.",
     "writes": "never",
     "params": [{"flag": "--summary-only", "kind": "bool", "label": "Tylko suma"},
                {"flag": "--compare-asana", "kind": "bool", "label": "Porównaj z Asaną"},
                {"flag": "--compare-full", "kind": "bool", "label": "Pełne porównanie (wolne)"},
                {"flag": "--with-attachments", "kind": "bool", "label": "Licz załączniki (wolne)"}]},
    {"id": "compare_totals", "group": "verify", "script": "compare_project_totals.py",
     "title": "Porównanie Asana ↔ Jira", "desc": "Wszystkie poziomy zagnieżdżenia, opcjonalnie załączniki.",
     "writes": "never",
     "params": [pos("Projekty", kind_hint="keys"),
                {"flag": "--all", "kind": "bool", "label": "Wszystkie z arkusza"},
                SHARD,
                {"flag": "--attachments", "kind": "bool", "label": "Także załączniki"},
                {"flag": "--top-level", "kind": "bool", "label": "Tylko najwyższy poziom"},
                {"flag": "--details", "kind": "bool", "label": "Wypisz konkretne zadania"}]},
    {"id": "audit_hierarchy", "group": "verify", "script": "audit_hierarchy.py",
     "title": "Audyt hierarchii", "desc": "Epic → Task → Sub-task; z --apply naprawia.",
     "writes": "with:--apply",
     "params": [KEYS, SHARD, {"flag": "--full", "kind": "bool", "label": "Pełny (dłużej)"},
                APPLY, {"flag": "--only-keys", "kind": "str", "label": "Napraw tylko zgłoszenia"},
                {**DRY, "default": False, "label": "Z --apply: tylko pokaż operacje"}]},
    {"id": "audit_epics", "group": "verify", "script": "audit_epic_candidates.py",
     "title": "Audyt kandydatów na Epik", "desc": "Tylko odczyt.", "writes": "never",
     "params": [{"flag": "--project-keys", "kind": "keys", "label": "Projekty"}]},
    {"id": "find_dup_tasks", "group": "verify", "script": "find_duplicate_tasks.py",
     "title": "Zdublowane zadania", "desc": "Wykrywa duplikaty tytułów w projektach migracji.",
     "writes": "never", "params": [{"flag": "--project", "kind": "keys", "label": "Projekt", "single": True}]},
    {"id": "find_dup_comments", "group": "verify", "script": "find_duplicate_comments_attachments.py",
     "title": "Zdublowane komentarze/załączniki", "desc": "Wewnątrz pojedynczych zadań.",
     "writes": "never",
     "params": [{"flag": "--project", "kind": "keys", "label": "Projekt", "single": True}, SHARD]},
    {"id": "gap_tasks", "group": "verify", "script": "diagnose_jira_asana_gap.py",
     "title": "Różnice tytułów zadań", "desc": "Jeden projekt: tytuły Jira vs Asana.",
     "writes": "never", "params": [pos("Klucz projektu", required=True, kind_hint="key")]},
    {"id": "gap_subtasks", "group": "verify", "script": "diagnose_subtask_gap.py",
     "title": "Różnice podzadań", "desc": "Jeden projekt: podzadania Jira vs Asana.",
     "writes": "never", "params": [pos("Klucz projektu", required=True, kind_hint="key")]},
    {"id": "gap_attachments", "group": "verify", "script": "diagnose_attachment_gap.py",
     "title": "Różnice załączników", "desc": "Jeden projekt: załączniki per zadanie.",
     "writes": "never", "params": [pos("Klucz projektu", required=True, kind_hint="key")]},
    {"id": "missing_in_state", "group": "verify", "script": "diagnose_missing_projects_in_state.py",
     "title": "Projekty bez stanu", "desc": "Klucze z arkusza nieobecne w task_sync_state.json.",
     "writes": "never", "params": []},
    {"id": "contamination", "group": "verify", "script": "diagnose_cross_project_contamination.py",
     "title": "Wpisy z obcych projektów", "desc": "Szuka w stanie zadań przypiętych do złego projektu.",
     "writes": "never", "params": []},
    {"id": "dup_sheet_rows", "group": "verify", "script": "check_duplicate_sheet_rows.py",
     "title": "Zdublowane wiersze arkusza", "desc": "Ten sam projekt w kilku wierszach.",
     "writes": "never", "params": []},
    {"id": "jira_access", "group": "verify", "script": "check_jira_project_access.py",
     "title": "Dostęp do projektu Jiry", "desc": "Czy dane logowania z .env widzą projekt.",
     "writes": "never",
     "params": [{"flag": "--project", "kind": "keys", "label": "Projekt", "single": True},
                {"flag": "--base-url", "kind": "str", "label": "Inny adres Jiry"}]},
    {"id": "count_state", "group": "verify", "script": "count_state_entries.py",
     "title": "Wpisy w stanie", "desc": "Ile zadań jest zmapowanych w task_sync_state.json.",
     "writes": "never", "params": []},

    # ------------------------------------------------------------------ repair
    {"id": "repair_batch", "group": "repair", "script": "repair_batch.py",
     "title": "Naprawa wielu projektów", "desc": "relink → dup → migrated dla listy projektów.",
     "writes": "with:--apply",
     "params": [{"kind": "pos", "label": "Krok", "options": ["relink", "dup", "migrated"], "required": True},
                KEYS, {"flag": "--keys-file", "kind": "str", "label": "Plik z kluczami"}, SHARD,
                APPLY, YES,
                {"flag": "--include-similar", "kind": "bool", "label": "relink: także PODOBNE"},
                {"flag": "--include-order", "kind": "bool", "label": "relink: także KOLEJNOŚĆ"},
                {"flag": "--include-content", "kind": "bool", "label": "relink: także TREŚĆ"},
                {"flag": "--include-orphans", "kind": "bool", "label": "dup: usuń też BRAK_W_ASANIE"}]},
    {"id": "relink", "group": "repair", "script": "relink_state.py",
     "title": "Odtwórz powiązania (relink)", "desc": "Odbudowuje powiązania Asana ↔ Jira w stanie.",
     "writes": "with:--apply",
     "params": [KEY1, APPLY,
                {"flag": "--include-content", "kind": "bool", "label": "Także TREŚĆ"},
                {"flag": "--include-order", "kind": "bool", "label": "Także KOLEJNOŚĆ"},
                {"flag": "--include-similar", "kind": "bool", "label": "Także PODOBNE"},
                {"flag": "--explain", "kind": "str", "label": "Wyjaśnij zgłoszenie", "placeholder": "ABC-123"},
                {"flag": "--report-file", "kind": "str", "label": "Plik raportu"}]},
    {"id": "relink_dups", "group": "repair", "script": "resolve_relink_duplicates.py",
     "title": "Duplikaty z raportu relink", "desc": "Przepina stan i usuwa duplikaty.",
     "writes": "with:--apply",
     "params": [KEY1, APPLY, YES, {"flag": "--limit", "kind": "int", "label": "Tylko N par"},
                {"flag": "--include-orphans", "kind": "bool", "label": "Usuń też BRAK_W_ASANIE"},
                {"flag": "--relink-report", "kind": "str", "label": "Raport relink"},
                {"flag": "--plan-file", "kind": "str", "label": "Plik planu"}]},
    {"id": "resolve_dups", "group": "repair", "script": "resolve_duplicates.py",
     "title": "Usuń zdublowane zadania", "desc": "Skan → --collect → --delete.",
     "writes": "with:--delete",
     "params": [{"flag": "--project", "kind": "keys", "label": "Projekt", "single": True}, SHARD,
                {"flag": "--collect", "kind": "bool", "label": "Zbierz wyniki shardów"},
                {"flag": "--delete", "kind": "bool", "label": "Usuń osierocone duplikaty"}]},
    {"id": "migrated", "group": "repair", "script": "handle_migrated_to_jira.py",
     "title": "Zadania „migriert zu Jira”", "desc": "Porządkuje zadania przeniesione ręcznie do Jiry.",
     "writes": "with:--apply",
     "params": [KEY1, APPLY, YES, {"flag": "--plan-file", "kind": "str", "label": "Plik planu"}]},
    {"id": "close_children", "group": "repair", "script": "close_children_of_done.py",
     "title": "Zamknij dzieci zakończonych", "desc": "Zamyka w Jirze wszystko pod zakończonym zadaniem.",
     "writes": "with:--apply",
     "params": [{**KEYS, "required": True},
                {"flag": "--source", "kind": "choice", "label": "Co decyduje o zakończeniu", "options": ["asana", "jira"]},
                {"flag": "--done-status", "kind": "str", "label": "Status docelowy"},
                APPLY, {"flag": "--also-asana", "kind": "bool", "label": "Także w Asanie"},
                {"flag": "--only-keys", "kind": "str", "label": "Tylko zgłoszenia"}]},
    {"id": "stale_resolution", "group": "repair", "script": "fix_stale_resolution.py",
     "title": "Przekreślone klucze", "desc": "Czyści Resolution w otwartych zgłoszeniach.",
     "writes": "with:--apply", "params": [{**KEYS, "required": True}, APPLY]},
    {"id": "refresh_flags", "group": "repair", "script": "refresh_state_flags.py",
     "title": "Odśwież flagi w stanie", "desc": "Flagi hierarchii w task_sync_state.json.",
     "writes": "with:--apply", "params": [KEYS, APPLY]},
    {"id": "retrofit_epics", "group": "repair", "script": "retrofit_epics.py",
     "title": "Dodaj Epiki do zmigrowanych", "desc": "Projekty zmigrowane przed obsługą Epików.",
     "writes": "unless:--dry-run", "params": [DRY, KEYS, SHARD]},
    {"id": "revert_epics", "group": "repair", "script": "revert_false_epics.py",
     "title": "Cofnij fałszywe Epiki", "desc": "Na podstawie CSV z audytu kandydatów.",
     "writes": "with:--apply,--apply-cascade",
     "params": [{"flag": "--csv", "kind": "str", "label": "Plik CSV", "required": True, "placeholder": "epic_candidates_audit.csv"},
                APPLY, {"flag": "--apply-cascade", "kind": "bool", "label": "Także Epiki z dziećmi"},
                {"flag": "--only-keys", "kind": "str", "label": "Tylko klucze"},
                {**DRY, "default": False, "label": "Z --apply: tylko pokaż operacje"}]},
    {"id": "deep_chains", "group": "repair", "script": "resolve_deep_chains.py",
     "title": "Głębokie łańcuchy", "desc": "Naprawia WYMAGA_RECZNEJ_DECYZJI_GLEBOKA.",
     "writes": "with:--apply",
     "params": [{"flag": "--csv", "kind": "str", "label": "Plik CSV audytu", "required": True},
                APPLY, {**DRY, "default": False, "label": "Z --apply: tylko pokaż"},
                {"flag": "--only-roots", "kind": "str", "label": "Tylko te rooty"}]},
    {"id": "convert_epic", "group": "repair", "script": "convert_to_epic.py",
     "title": "Konwersja na Epik", "desc": "Ręcznie wskazane zgłoszenia → Epic.",
     "writes": "unless:--dry-run",
     "params": [DRY, {"flag": "--keys", "kind": "str", "label": "Zgłoszenia", "required": True, "placeholder": "ABC-1,ABC-7"},
                {"flag": "--convert-subtasks", "kind": "bool", "label": "Podzadania → Task"}]},
    {"id": "missing_state", "group": "repair", "script": "repair_missing_state_entries.py",
     "title": "Brakujące wpisy stanu", "desc": "Przyczyna powracających duplikatów.",
     "writes": "with:--apply",
     "params": [{"flag": "--project", "kind": "keys", "label": "Projekt", "single": True, "required": True}, APPLY]},
    {"id": "clean_stale", "group": "repair", "script": "clean_stale_task_state.py",
     "title": "Usuń nieaktualne wpisy stanu", "desc": "Wpisy wskazujące na nieistniejące zgłoszenia.",
     "writes": "unless:--dry-run", "params": [DRY]},
    {"id": "mark_deleted", "group": "repair", "script": "mark_deleted_as_skipped.py",
     "title": "Usunięte w Jirze → pomijane", "desc": "Oznacza w stanie zgłoszenia usunięte przez zespół.",
     "writes": "unless:--dry-run", "params": [DRY]},
    {"id": "repair_state_file", "group": "repair", "script": "repair_state_file.py",
     "title": "Napraw uszkodzony plik stanu", "desc": "Po przerwanym zapisie task_sync_state.json.",
     "writes": "always", "params": [pos("Plik stanu", placeholder="task_sync_state.json")]},

    # ------------------------------------------------------------------ jira config
    {"id": "build_schema", "group": "jira_config", "script": "build_jira_schema.py",
     "title": "Konfiguracja z YAML", "desc": "Statusy, workflow, ekrany, pola ze schematu YAML.",
     "writes": "unless:--dry-run",
     "params": [DRY, {"flag": "--schema", "kind": "str", "label": "Plik YAML", "placeholder": "jira_schema.yaml"},
                {"flag": "--only", "kind": "str", "label": "Tylko kroki (po przecinku)"},
                {"flag": "--workflow", "kind": "str", "label": "Tylko workflow"},
                {"flag": "--include-incomplete", "kind": "bool", "label": "Także niekompletne workflow"}]},
    {"id": "transition_groups", "group": "jira_config", "script": "set_transition_groups.py",
     "title": "Grupy przejść workflow", "desc": "Kto może wykonać przejście (transition_groups.yaml).",
     "writes": "unless:--dry-run",
     "params": [DRY, {"flag": "--config", "kind": "str", "label": "Plik YAML", "placeholder": "transition_groups.yaml"},
                {"flag": "--workflow", "kind": "str", "label": "Tylko workflow"}]},
    {"id": "field_scheme", "group": "jira_config", "script": "associate_fields_to_field_scheme.py",
     "title": "Pola do field scheme", "desc": "Dodaje pola z ekranów do field scheme projektu.",
     "writes": "unless:--dry-run",
     "params": [DRY, {"flag": "--project", "kind": "keys", "label": "Projekt", "single": True, "required": True},
                {"flag": "--schema", "kind": "str", "label": "YAML"},
                {"flag": "--fields", "kind": "str", "label": "ID pól"}]},
    {"id": "remove_field", "group": "jira_config", "script": "remove_field_from_screens.py",
     "title": "Zdejmij pole z ekranów", "desc": "Pole i dane zostają.",
     "writes": "unless:--dry-run",
     "params": [DRY, {"flag": "--field", "kind": "str", "label": "Pole", "required": True},
                {"flag": "--schema", "kind": "str", "label": "YAML"},
                {"flag": "--all-screens", "kind": "bool", "label": "Wszystkie ekrany"}]},
    {"id": "remove_status_column", "group": "jira_config", "script": "remove_status_from_column.py",
     "title": "Zdejmij status z kolumny", "desc": "Status wraca do niezmapowanych.",
     "writes": "with:--apply",
     "params": [KEYS, {"flag": "--board-ids", "kind": "str", "label": "ID tablic"},
                {"flag": "--column", "kind": "str", "label": "Kolumna", "placeholder": "Backlog"},
                {"flag": "--status", "kind": "str", "label": "Status", "placeholder": "Backlog"}, APPLY]},
    {"id": "add_group", "group": "jira_config", "script": "add_group_to_projects.py",
     "title": "Dodaj grupę do ról", "desc": "Grupa → role w istniejących projektach.",
     "writes": "always",
     "params": [{"flag": "--group", "kind": "str", "label": "Grupa", "required": True},
                {"flag": "--role-names", "kind": "str", "label": "Role", "required": True, "placeholder": "Developer,Users"},
                KEYS, {"flag": "--all", "kind": "bool", "label": "Wszystkie z arkusza"}]},
    {"id": "add_admin", "group": "jira_config", "script": "add_admin_to_projects.py",
     "title": "Dodaj administratora", "desc": "Osoba (accountId) → rola Administrator.",
     "writes": "always",
     "params": [{"flag": "--account-id", "kind": "str", "label": "accountId", "required": True},
                KEYS, {"flag": "--all", "kind": "bool", "label": "Wszystkie z arkusza"},
                {"flag": "--role-name", "kind": "str", "label": "Nazwa roli"}]},
    {"id": "assign_category", "group": "jira_config", "script": "assign_category_to_projects.py",
     "title": "Kategoria projektów", "desc": "Przypisuje Project Category.",
     "writes": "unless:--list-categories",
     "params": [{"flag": "--list-categories", "kind": "bool", "label": "Tylko wypisz kategorie", "default": True},
                {"flag": "--category", "kind": "str", "label": "Kategoria"},
                KEYS, {"flag": "--all", "kind": "bool", "label": "Wszystkie z arkusza"}]},
    {"id": "copy_permissions", "group": "jira_config", "script": "copy_permission_scheme.py",
     "title": "Kopia schematu uprawnień", "desc": "Nowy schemat z istniejącego, opcjonalnie podpięty.",
     "writes": "unless:--dry-run",
     "params": [DRY, {"flag": "--source-id", "kind": "str", "label": "ID źródła", "required": True},
                {"flag": "--name", "kind": "str", "label": "Nazwa", "required": True},
                {"flag": "--description", "kind": "str", "label": "Opis"},
                {"flag": "--assign", "kind": "keys", "label": "Podepnij do projektu", "single": True}]},
    {"id": "template_columns", "group": "jira_config", "script": "show_template_board_columns.py",
     "title": "Kolumny tablicy wzorcowej", "desc": "Odczyt układu kolumn.",
     "writes": "never", "params": [pos("Klucz projektu", kind_hint="key"), pos("ID tablicy")]},
    {"id": "list_groups", "group": "jira_config", "script": "list_groups.py",
     "title": "Grupy Atlassiana", "desc": "Wszystkie grupy, opcjonalnie z filtrem.",
     "writes": "never", "params": [pos("Fragment nazwy")]},
    {"id": "group_members", "group": "jira_config", "script": "get_group_members.py",
     "title": "Członkowie grupy", "desc": "accountId, nazwisko, e-mail.",
     "writes": "never", "params": [pos("Nazwa grupy", required=True)]},
    {"id": "required_fields", "group": "jira_config", "script": "check_required_fields.py",
     "title": "Pola wymagane", "desc": "Przy tworzeniu zadania danego typu.",
     "writes": "never", "params": [pos("Klucz projektu", required=True, kind_hint="key"), pos("Typ zadania", placeholder="Task")]},

    # ------------------------------------------------------------------ copy
    {"id": "clone_project", "group": "copy", "script": "clone_project.py",
     "title": "Kopia projektu z kopiami schematów",
     "desc": "Samodzielna kopia projektu: każdy schemat źródła kopiowany pod nazwą nowego projektu, ustawienia, role, komponenty, wersje, tablice. Bez projektu wzorcowego. Karta „Kopia projektu”.",
     "writes": "unless:--dry-run",
     "params": [DRY, {"flag": "--source", "kind": "keys", "label": "Projekt źródłowy", "single": True, "required": True},
                {"flag": "--target-key", "kind": "str", "label": "Klucz nowego projektu", "required": True},
                {"flag": "--target-name", "kind": "str", "label": "Nazwa nowego projektu", "required": True},
                {"flag": "--name-base", "kind": "choice", "label": "Nazwy schematów od", "options": ["key", "name"]},
                {"flag": "--lead-account-id", "kind": "str", "label": "Lead (accountId, domyślnie jak w źródle)"},
                {"flag": "--description", "kind": "str", "label": "Opis (domyślnie jak w źródle)"},
                {"flag": "--board-ids", "kind": "str", "label": "ID tablic źródła"},
                {"flag": "--roles-file", "kind": "str", "label": "Plik: kto z jaką rolą (karta „Kopia projektu”)"},
                {"flag": "--roles-from-source", "kind": "bool", "label": "Role i członkowie jak w źródle"},
                {"flag": "--no-roles", "kind": "bool", "label": "Nikogo nie dodawaj do ról"},
                {"flag": "--no-versions", "kind": "bool", "label": "Bez wersji"},
                {"flag": "--no-components", "kind": "bool", "label": "Bez komponentów"},
                {"flag": "--no-field-contexts", "kind": "bool", "label": "Bez kontekstów pól (szybciej)"},
                {"flag": "--no-boards", "kind": "bool", "label": "Bez tablic"},
                {"flag": "--existing-target", "kind": "bool", "label": "Projekt docelowy już istnieje"}]},
    {"id": "copy_project", "group": "copy", "script": "copy_project.py",
     "title": "Kopia projektu (ta sama Jira)", "desc": "Nowy projekt z konfiguracją, tablicami i kolumnami.",
     "writes": "unless:--dry-run", "browser": True,
     "params": [DRY, {"flag": "--source", "kind": "keys", "label": "Źródło", "single": True, "required": True},
                {"flag": "--target-key", "kind": "str", "label": "Klucz nowego", "required": True},
                {"flag": "--target-name", "kind": "str", "label": "Nazwa nowego"},
                {"flag": "--lead-account-id", "kind": "str", "label": "Lead (accountId)"},
                {"flag": "--description", "kind": "str", "label": "Opis"},
                {"flag": "--board-ids", "kind": "str", "label": "ID tablic"},
                {"flag": "--copy-role-members", "kind": "bool", "label": "Kopiuj członków ról"},
                {"flag": "--skip-project-creation", "kind": "bool", "label": "Projekt już istnieje"}]},
    {"id": "copy_cross", "group": "copy", "script": "copy_project_cross_instance.py",
     "title": "Kopia z innej instancji Jiry", "desc": "Projekt z zadaniami między instancjami (SOURCE_JIRA_* w .env).",
     "writes": "unless:--dry-run", "browser": True,
     "params": [DRY, {"flag": "--source-key", "kind": "str", "label": "Klucz źródła", "required": True},
                {"flag": "--target-key", "kind": "keys", "label": "Klucz docelowy", "single": True, "required": True},
                {"flag": "--target-name", "kind": "str", "label": "Nazwa (gdy nowy)"},
                {"flag": "--target-lead-account-id", "kind": "str", "label": "Lead (gdy nowy)"},
                {"flag": "--update-status", "kind": "bool", "label": "Aktualizuj statusy"},
                {"flag": "--skip-existing-titles", "kind": "bool", "label": "Pomiń istniejące tytuły"},
                {"flag": "--refresh-existing", "kind": "bool", "label": "Uzupełnij skopiowane"},
                {"flag": "--only-statuses", "kind": "str", "label": "Tylko statusy źródła"},
                {"flag": "--exclude-statuses", "kind": "str", "label": "Bez statusów źródła"},
                {"flag": "--status-map", "kind": "str", "label": "Plik mapy statusów"},
                {"flag": "--done-status", "kind": "str", "label": "Status dla Done"},
                {"flag": "--other-status", "kind": "str", "label": "Status dla reszty"},
                {"flag": "--user-map", "kind": "str", "label": "Plik mapy osób", "placeholder": "wt_user_map.json"},
                {"flag": "--extra-group", "kind": "repeat", "label": "Dodatkowe grupy (Grupa=Rola, po jednej w linii)"},
                {"flag": "--no-origin-note", "kind": "bool", "label": "Bez notki o pochodzeniu"},
                {"flag": "--skip-access", "kind": "bool", "label": "Bez dodawania dostępu"},
                {"flag": "--skip-boards", "kind": "bool", "label": "Bez tablic"},
                {"flag": "--source-board-ids", "kind": "str", "label": "ID tablic źródła"}]},

    # ------------------------------------------------------------------ other
    {"id": "mvp_epics", "group": "other", "script": "create_epics_stories_from_sheet.py",
     "title": "Epiki i Story z arkusza MVP", "desc": "Z pliku .xlsx „Zakres MVP”.",
     "writes": "unless:--dry-run",
     "params": [DRY, {"flag": "--file", "kind": "str", "label": "Plik .xlsx", "required": True},
                {"flag": "--project", "kind": "keys", "label": "Projekt", "single": True, "required": True},
                {"flag": "--status", "kind": "str", "label": "Tylko status", "placeholder": "MVP"},
                {"flag": "--only-epics", "kind": "bool", "label": "Tylko Epiki"},
                {"flag": "--limit", "kind": "int", "label": "Tylko N Story"}]},
    {"id": "export_stories", "group": "other", "script": "export_stories_to_sheet.py",
     "title": "Eksport Story do .xlsx", "desc": "Do kopii arkusza albo osobnego pliku.",
     "writes": "never",
     "params": [{"flag": "--project", "kind": "keys", "label": "Projekt", "single": True, "required": True},
                {"flag": "--file", "kind": "str", "label": "Arkusz źródłowy"},
                {"flag": "--out", "kind": "str", "label": "Plik wynikowy"},
                {"flag": "--jql", "kind": "str", "label": "Własny JQL"}]},
    {"id": "analysis_stories", "group": "other", "script": "create_analysis_for_stories.py",
     "title": "Analysis dla Story", "desc": "Zadanie Analysis powiązane z każdym Story.",
     "writes": "unless:--dry-run",
     "params": [DRY, {"flag": "--project", "kind": "keys", "label": "Projekt", "single": True, "required": True},
                {"flag": "--jql", "kind": "str", "label": "JQL Story"},
                {"flag": "--file", "kind": "str", "label": "Eksport TSV/CSV"},
                {"flag": "--limit", "kind": "int", "label": "Tylko N"}]},
    {"id": "suspended_users", "group": "other", "script": "atlassian_suspend_to_deactivate.py",
     "title": "Dezaktywuj zawieszone konta", "desc": "Organizacja Atlassian: suspended → deactivated.",
     "writes": "with:--execute",
     "params": [{**DRY, "default": True}, {"flag": "--execute", "kind": "bool", "label": "Wykonaj dezaktywację"},
                {"flag": "--org-id", "kind": "str", "label": "ID organizacji"}]},
]

TOOLS_BY_ID = {t["id"]: t for t in TOOLS}

# Nazwa pola formularza: flaga albo "posN" dla argumentów pozycyjnych.
for _tool in TOOLS:
    for _i, _p in enumerate(_tool["params"]):
        _p["name"] = _p.get("flag") or f"pos{_i}"


def build_argv(tool: dict, values: dict) -> list:
    """Składa argumenty linii poleceń z wartości formularza. Puste wartości są
    pomijane, więc skrypt używa wtedy swoich domyślnych."""
    argv = list(tool.get("fixed_args", []))  # stałe argumenty narzędzia (np. --check)
    positional = []
    for p in tool["params"]:
        v = values.get(p["name"])
        kind = p["kind"]
        if kind == "bool":
            if v:
                argv.append(p["flag"])
            continue
        if kind == "repeat":
            for line in str(v or "").splitlines():
                if line.strip():
                    argv += [p["flag"], line.strip()]
            continue
        if v is None or str(v).strip() == "":
            if p.get("required"):
                raise ValueError(f"Pole „{p['label']}” jest wymagane.")
            continue
        v = str(v).strip()
        if kind == "int" and not v.lstrip("-").isdigit():
            raise ValueError(f"Pole „{p['label']}” musi być liczbą.")
        if kind == "pos":
            positional.append(v)
        else:
            argv += [p["flag"], v]
    return argv + positional


def run_writes(tool: dict, argv: list) -> bool:
    """Czy uruchomienie z tymi argumentami zmieni dane."""
    rule = tool.get("writes", "never")
    if rule == "never":
        return False
    if rule == "always":
        return True
    kind, _, flags = rule.partition(":")
    flags = set(flags.split(","))
    if kind == "unless":
        return not (flags & set(argv))
    if kind == "with":
        return bool(flags & set(argv))
    return True


def public_catalog() -> dict:
    return {"groups": [{"id": g, "title": t} for g, t in GROUPS], "tools": TOOLS}
