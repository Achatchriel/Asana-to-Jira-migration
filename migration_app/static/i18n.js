// Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
// Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"use strict";

// Język panelu. Interfejs jest pisany po polsku; w trybie EN teksty są tłumaczone
// w momencie wyświetlania (słownik dokładnych tekstów + wzorce dla tekstów ze wstawkami).
// Nie są tłumaczone: logi i polecenia skryptów (.log, .cmd), wartości techniczne (.mono),
// pola edycji ani dane z Jiry/Asany, które nie pasują do słownika.

const LANG = (() => { try { return localStorage.getItem("lang") === "en" ? "en" : "pl"; } catch (e) { return "pl"; } })();
const LOCALE = LANG === "en" ? "en-GB" : "pl-PL";

const EN = {
  // --- nawigacja i elementy stałe
  "Panel migracji": "Migration panel", "Migracja": "Migration", "Start": "Start", "Przegląd": "Overview",
  "Projekty": "Projects", "Narzędzia": "Tools", "Statusy i osoby": "Statuses and people",
  "Mapowanie osób": "People mapping", "Mapowanie statusów": "Status mapping", "Kopia projektu": "Copy project",
  "Uruchomienia": "Runs", "Konfiguracja": "Configuration", "Raporty": "Reports", "Wybrane projekty": "Selected projects",
  "wyczyść": "clear", "brak": "none", "Anuluj": "Cancel", "Uruchom": "Run", "To uruchomienie zmieni dane": "This run will change data",
  "Rozumiem, uruchom bez podglądu": "I understand, run without preview", "Ładowanie…": "Loading…",
  "Niepoprawna odpowiedź serwera": "Invalid server response", "Błąd: ": "Error: ",

  // --- statusy uruchomień i znaczniki
  "trwa": "running", "OK": "OK", "częściowo": "partial", "błąd": "error", "przerwane": "stopped", "utracone": "lost",
  "zapis": "writes", "odczyt": "read", "podgląd": "preview", "odczyt / podgląd": "read / preview",
  "zgodne": "consistent", "chroniony": "protected", "bez sync": "no sync", "gość": "guest", "nowy": "new",
  "własna": "own", "główna": "main", "projekt": "project", "domyślny": "default", "ukończone": "completed",

  // --- Start
  "Start migracji": "Migration start",
  "Dane dostępowe do Jiry, Asany i arkusza Google (zapisywane w .env, poza repo).": "Access to Jira, Asana and the Google Sheet (saved in .env, outside the repo).",
  "Test połączeń": "Connection test",
  "Sprawdza konto Jiry i uprawnienia administratora, projekt i tablicę wzorcową, token Asany, dostęp do arkusza i jego kolumny.": "Checks the Jira account and admin permission, the template project and board, the Asana token, sheet access and its columns.",
  "Arkusz projektów": "Project sheet",
  "Wczytuje listę projektów do migracji. Każdy wiersz potrzebuje asana_project_link.": "Loads the list of projects to migrate. Every row needs asana_project_link.",
  "Kto z Asany odpowiada komu w Jirze; osoby bez konta oznaczasz „nie w Jirze”.": "Who in Asana is who in Jira; people without an account are marked \"not in Jira\".",
  "Która sekcja Asany trafia na który status Jiry i jaki status dostają zadania ukończone.": "Which Asana section goes to which Jira status, and which status completed tasks get.",
  "Sesja przeglądarki": "Browser session",
  "Kroki 2-3 (kolumny i statusy tablic) działają przez przeglądarkę - zaloguj się raz do Jiry (SSO/2FA).": "Steps 2-3 (board columns and statuses) use a browser - log in to Jira once (SSO/2FA).",
  "Podgląd całej migracji": "Preview of the whole migration",
  "Pipeline w trybie dry-run: pokazuje, co zostanie utworzone i zsynchronizowane, nic nie zapisując.": "The pipeline in dry-run mode: shows what would be created and synced, without writing anything.",
  "Pełny pipeline. Zalecane: najpierw zakładka arkusza z 1-2 projektami testowymi (GOOGLE_SHEET_RANGE).": "The full pipeline. Recommended: first a sheet tab with 1-2 test projects (GOOGLE_SHEET_RANGE).",
  "Weryfikacja": "Verification", "Zgodność statusów, przypisań i liczby zadań z Asaną.": "Status, assignee and task-count consistency with Asana.",
  "Wczytaj arkusz": "Load sheet", "Wczytaj ponownie": "Reload", "Lista projektów": "Project list",
  "Otwórz mapowanie osób": "Open people mapping", "Otwórz mapowanie statusów": "Open status mapping",
  "Otwórz przeglądarkę do logowania": "Open the browser to log in",
  "Po zalogowaniu wróć do panelu i wyślij Enter w polu odpowiedzi uruchomienia.": "After logging in, come back to the panel and send Enter in the run's input field.",
  "Uruchom podgląd (dry-run)": "Run preview (dry-run)", "Pełny pipeline…": "Full pipeline…",
  "Panel pokaże polecenie i poprosi o potwierdzenie przed zapisem.": "The panel shows the command and asks for confirmation before writing.",
  "Porównanie liczby zadań…": "Task count comparison…",
  "Adres Jiry": "Jira URL", "E-mail konta Jiry (administrator)": "Jira account e-mail (administrator)", "Token API Jiry": "Jira API token",
  "Token Asany": "Asana token", "Workspace Asany": "Asana workspace", "Arkusz Google (adres albo ID)": "Google Sheet (URL or ID)",
  "Zakładka i kolumny": "Tab and columns", "Projekt wzorcowy (klucz)": "Template project (key)", "Tablica wzorcowa (ID)": "Template board (ID)",
  "Domyślny lead projektów (accountId)": "Default project lead (accountId)",
  "id.atlassian.com → Security → API tokens": "id.atlassian.com → Security → API tokens",
  "app.asana.com/0/my-apps → Personal access token": "app.asana.com/0/my-apps → Personal access token",
  "Lista pojawi się po teście połączenia z tokenem.": "The list appears after a connection test with the token.",
  "Z niego nowe projekty dostają workflow, ekrany, pola i uprawnienia.": "New projects get their workflow, screens, fields and permissions from it.",
  "użyj mojego konta": "use my account", "Klucz konta serwisowego Google *": "Google service account key *",
  "Wklej tutaj zawartość pliku JSON z kluczem konta serwisowego Google…": "Paste the contents of the Google service account JSON key here…",
  "Google Cloud Console → IAM → Service Accounts → Keys → Add key (JSON). Plik zostaje lokalnie (jest w .gitignore).": "Google Cloud Console → IAM → Service Accounts → Keys → Add key (JSON). The file stays local (it is in .gitignore).",
  "Klucz zapisany. Udostępnij arkusz Google adresowi: ": "Key saved. Share the Google Sheet with: ", "Kopiuj": "Copy", "Skopiowano adres.": "Address copied.",
  "Wszystkie zmienne…": "All variables…", "Zapisz i sprawdź połączenia": "Save and check connections", "Zapisuję i sprawdzam…": "Saving and checking…",
  "Sprawdź połączenia": "Check connections", "Sprawdzam…": "Checking…", "Jira": "Jira", "Asana": "Asana", "Google Sheets": "Google Sheets",
  " - działa, ale są uwagi": " - works, with remarks", " - błąd": " - error", " - przed ostatnią zmianą .env": " - before the last .env change",
  "ustawiony - wpisz, aby zmienić": "set - type to change", "nieustawione": "not set",
  // kroki - szczegóły z serwera
  "Wymagane pola uzupełnione.": "Required fields filled in.", "Wszystkie połączenia działają.": "All connections work.",
  "Wynik sprzed ostatniej zmiany .env - sprawdź ponownie.": "Result from before the last .env change - check again.",
  "Nie sprawdzono.": "Not checked.", "Są błędy - zobacz szczegóły.": "There are errors - see details.", "Nie wczytano.": "Not loaded.",
  "Brak mapy osób.": "No people map.", "Brak mapy statusów.": "No status map.", "Brak zapisanej sesji.": "No saved session.",
  "Jeszcze nie uruchomiono.": "Not run yet.", "Jeszcze nic nie zmigrowano.": "Nothing migrated yet.",
  // wyniki testu połączeń
  "Konto ma uprawnienie administratora Jiry ✓": "The account has Jira administrator permission ✓",
  "Konto NIE jest administratorem Jiry - tworzenie projektów i kopiowanie schematów się nie uda.": "The account is NOT a Jira administrator - creating projects and copying schemes will fail.",
  "Projekt wzorcowy jest team-managed - schematy kopiują się tylko z projektów company-managed.": "The template project is team-managed - schemes can only be copied from company-managed projects.",
  "Projekt wzorcowy nie ma tablicy - kroki 2-3 (kolumny) nie będą miały wzorca.": "The template project has no board - steps 2-3 (columns) will have no template.",
  "Jira odrzuciła dane logowania (401) - sprawdź e-mail i token API.": "Jira rejected the credentials (401) - check the e-mail and API token.",
  "Uzupełnij JIRA_BASE_URL, JIRA_EMAIL i JIRA_API_TOKEN.": "Fill in JIRA_BASE_URL, JIRA_EMAIL and JIRA_API_TOKEN.",
  "Uzupełnij ASANA_TOKEN.": "Fill in ASANA_TOKEN.", "Asana odrzuciła token (401).": "Asana rejected the token (401).",
  "Wklej klucz konta serwisowego Google albo ustaw GOOGLE_APPLICATION_CREDENTIALS.": "Paste the Google service account key or set GOOGLE_APPLICATION_CREDENTIALS.",
  "Uzupełnij GOOGLE_SHEET_ID (ID z adresu arkusza).": "Fill in GOOGLE_SHEET_ID (the ID from the sheet URL).", "Arkusz o tym ID nie istnieje.": "No sheet with this ID exists.",
  "Brak kolumny asana_project_link - bez niej żaden projekt nie zostanie zmigrowany.": "No asana_project_link column - without it no project will be migrated.",

  // --- Przegląd
  "Sprawdź statusy i osoby": "Check statuses and people", "Weryfikacja liczby zadań": "Task count check",
  "Brak pliku .env. ": "No .env file. ", "Uzupełnij konfigurację →": "Complete the configuration →", "Brakujące zmienne w .env: ": "Missing variables in .env: ",
  "Uzupełnij →": "Complete →", "Brak zapisanej sesji przeglądarki - kroki 2-3 (kolumny, statusy kolumn) jej wymagają. ": "No saved browser session - steps 2-3 (columns, column statuses) need it. ",
  "Zapisz sesję →": "Save session →", "projektów w stanie synchronizacji": "projects in the sync state", "zmapowanych zadań (task_sync_state.json)": "mapped tasks (task_sync_state.json)",
  "osób w user_map.json": "people in user_map.json", "trwających uruchomień": "runs in progress", "Ochrona projektów": "Project protection",
  " - wyłączone ze wszystkiego": " - excluded from everything", " - wyłączone z synchronizacji": " - excluded from sync", "Edytuj listy": "Edit lists",
  "Środowisko": "Environment", "Python": "Python", "Plik stanu": "State file", "Ostatnie uruchomienia": "Recent runs", "Wszystkie": "All",
  "Jeszcze nic nie uruchomiono z panelu.": "Nothing has been run from the panel yet.", "Narzędzie": "Tool", "Polecenie": "Command", "Czas": "Duration",
  "Status": "Status",

  // --- Projekty
  "Wybrane": "Selected", "Bez stanu": "No state", "Z mapą projektu": "With project map", "Wyłączone z sync": "Excluded from sync",
  "Chronione": "Protected", "Problemy w sprawdzeniu": "Issues in check", "Zaznacz widoczne": "Select visible", "Odznacz widoczne": "Deselect visible",
  "Sprawdź statusy wybranych": "Check statuses of selected", "Synchronizacja…": "Sync…", "Pipeline dla wybranych…": "Pipeline for selected…",
  "Odśwież z arkusza": "Refresh from sheet", "Wczytywanie… (do kilku minut)": "Loading… (up to a few minutes)", "Wczytywanie…": "Loading…",
  "Nie udało się odczytać arkusza - pokazuję dane lokalne. ": "Could not read the sheet - showing local data. ", "Szczegóły": "Details",
  "Arkusz nie został jeszcze wczytany. ": "The sheet has not been loaded yet. ", "Szukaj po kluczu lub nazwie…": "Search by key or name…",
  "Klucz": "Key", "Projekt Asany": "Asana project", "Wpisy stanu": "State entries", "Mapa sekcji": "Section map", "Flagi": "Flags",
  "Ostatnie sprawdzenie": "Last check", "arkusz": "sheet", "lokalnie (stan + cache kluczy)": "local (state + key cache)",
  "Wczytywanie arkusza Google… Pierwszy odczyt może potrwać kilka minut (nazwy projektów są pobierane z Asany), potem panel korzysta z cache.": "Loading the Google Sheet… The first read can take a few minutes (project names are fetched from Asana), then the panel uses a cache.",
  "Najpierw zaznacz projekty na liście.": "Select projects in the list first.", "Brak wybranych projektów.": "No projects selected.", "wstaw wybrane projekty": "insert selected projects",

  // --- Narzędzia
  "Wybierz narzędzie z listy": "Pick a tool from the list",
  "Każde narzędzie to istniejący skrypt jiraSync. Panel składa polecenie z formularza, pokazuje je przed uruchomieniem i wymaga potwierdzenia, gdy uruchomienie zmienia dane. Zaczynaj od podglądu (dry-run) albo trybu raportu.": "Every tool is an existing jiraSync script. The panel builds the command from the form, shows it before running and asks for confirmation when the run changes data. Start with a preview (dry-run) or report mode.",
  "Narzędzie używa przeglądarki (Playwright) - potrzebna aktualna sesja logowania (narzędzie „Zapis sesji przeglądarki”).": "The tool uses a browser (Playwright) - a current login session is needed (the \"Save browser session\" tool).",
  "Bez parametrów.": "No parameters.", "To uruchomienie ZMIENI dane - panel poprosi o potwierdzenie.": "This run WILL CHANGE data - the panel will ask for confirmation.",
  "Tylko odczyt / podgląd - nic nie zostanie zmienione.": "Read-only / preview - nothing will be changed.", "(domyślnie)": "(default)",

  // --- Uruchomienia
  "Historia zapisywana w migration_app/runs/": "History saved in migration_app/runs/", "Brak uruchomień.": "No runs.", "← Uruchomienia": "← Runs",
  "Odpowiedź dla skryptu (np. t / tak / Enter) …": "Answer for the script (e.g. t / tak / Enter) …", "Wyślij": "Send", "Przerwij": "Stop",
  "Przerwać to uruchomienie?": "Stop this run?", "Usuń z historii": "Remove from history", "Usunąć wpis i log z historii?": "Remove the entry and log from history?",
  "Uruchom ponownie…": "Run again…", "Wyniki: ": "Results: ", "Nie ma takiego uruchomienia.": "No such run.",

  // --- Statusy i osoby
  "Sekcje Asany → statusy Jiry, przypisani → konta Jiry, zgodność zmigrowanych zadań. Tylko odczyt.": "Asana sections → Jira statuses, assignees → Jira accounts, consistency of migrated tasks. Read-only.",
  "Klucz(e) projektu, np. ABC albo ABC,DEF": "Project key(s), e.g. ABC or ABC,DEF", "Sprawdź teraz": "Check now", "Sprawdzanie…": "Checking…",
  "Podaj klucz projektu.": "Enter a project key.", "Projekt": "Project", "Sprawdzono": "Checked", "Zadań": "Tasks", "Różnice statusu": "Status differences",
  "Różnice przypisania": "Assignee differences", "Uwagi": "Notes",
  "Brak wyników. Podaj klucz projektu i kliknij „Sprawdź teraz” (albo zaznacz projekty na liście Projekty).": "No results. Enter a project key and click \"Check now\" (or select projects in the Projects list).",
  "Sprawdź ponownie": "Check again", "Błędy": "Errors", "zadań w projekcie Asany": "tasks in the Asana project", "Asana ↗": "Asana ↗",
  " · projekt ma własną mapę": " · the project has its own map", " · projekt używa tylko mapy głównej": " · the project uses only the main map",
  " - takiego statusu nie ma w projekcie!": " - this status does not exist in the project!",
  "pomijana (nie migruje)": "skipped (not migrated)", "brak mapowania": "no mapping", "(usuń wpis projektu)": "(remove project entry)", "Zapisz": "Save",
  "Sekcja Asany": "Asana section", "Otwarte": "Open", "Ukończone": "Completed", "Status w Jirze": "Status in Jira", "Źródło wpisu": "Entry source",
  "Mapa projektu": "Project map", "Edytuj mapę projektu": "Edit project map", "Synchronizacja tego projektu (podgląd)…": "Sync this project (preview)…",
  "„Zapisz” zmienia tylko mapę tego projektu (section_status_map.<KLUCZ>.json, z kopią w migration_app/backups/). Mapa główna i szablony - w Konfiguracji. Rozszerzone wpisy (etykiety, rozwiązanie, pomiń) edytuj w pliku.": "\"Save\" changes only this project's map (section_status_map.<KEY>.json, with a backup in migration_app/backups/). The main map and profiles are in Configuration. Edit extended entries (labels, resolution, skip) in the file.",
  "ważniejsza niż odhaczenie": "takes precedence over completion",
  "Osoba w Asanie": "Person in Asana", "Otwartych": "Open", "Konto w Jirze (user_map.json)": "Jira account (user_map.json)", "Stan": "State",
  "Nie w Jirze": "Not in Jira", "nie w Jirze": "not in Jira", "Cofnij": "Undo", "Zapisano ✓": "Saved ✓",
  "Lista „nie w Jirze”": "\"Not in Jira\" list", "Edytuj user_map.json": "Edit user_map.json", "Generuj user_map (po e-mailu)…": "Generate user_map (by e-mail)…",
  "Lista osób w Asanie…": "Asana people list…",
  "„Nie w Jirze” = osoba świadomie bez konta (lista w asana_users_not_in_jira.json, wspólna dla wszystkich projektów). Jej zadania trafiają do Jiry bez przypisanego (Jira wstawia leada) i nie są liczone jako różnice. Oznaczenie usuwa osobę z user_map.json.": "\"Not in Jira\" = a person deliberately without an account (list in asana_users_not_in_jira.json, shared by all projects). Their tasks go to Jira without an assignee and are not counted as differences. Marking removes the person from user_map.json.",
  "„Bez uprawnienia do przypisania” = mapowanie jest poprawne, ale Jira nie pozwala przypisać tego konta w projekcie: żadna rola tej osoby w projekcie (także przez grupę) nie ma uprawnienia „Assignable User” w schemacie uprawnień. Bycie leadem go nie daje. ": "\"Not assignable\" = the mapping is correct, but Jira does not allow assigning this account in the project: none of this person's roles in the project (including via a group) has the \"Assignable User\" permission in the permission scheme. Being the lead does not grant it. ",
  ". Rozwiązanie: dodać uprawnienie roli, w której są te osoby (zmienia wszystkie projekty z tym schematem), albo dodać osoby/grupę do jednej z ról powyżej w tym projekcie. Do tego czasu nowe zadania takich osób powstają bez przypisanego, a już utworzone zachowują obecnego przypisanego - jedno i drugie nie jest liczone jako różnica.": ". Fix: grant the permission to the role these people have (changes every project using this scheme), or add the people/group to one of the roles above in this project. Until then, new tasks of these people are created unassigned and existing ones keep their current assignee - neither counts as a difference.",
  "Nie udało się odczytać schematu uprawnień (wymaga uprawnień administratora Jiry).": "Could not read the permission scheme (requires Jira administrator permission).",
  "Wszystkie zmigrowane zadania mają w Jirze oczekiwany status i przypisanego.": "All migrated tasks have the expected status and assignee in Jira.",
  "Wszystkie rodzaje": "All kinds", "Szukaj…": "Search…", "Synchronizacja (podgląd)…": "Sync (preview)…", "Pełne porównanie…": "Full comparison…",
  "Zgłoszenie": "Issue", "Zadanie": "Task", "Rodzaj": "Kind", "Oczekiwane (wg Asany)": "Expected (per Asana)", "W Jirze": "In Jira",
  "status": "status", "przypisany": "assignee", "brak w Jirze": "missing in Jira", "brak uprawnienia w Jirze": "no permission in Jira",
  "problem z mapowaniem": "mapping problem", "brak w user_map.json": "not in user_map.json", "konto Jiry nie znalezione": "Jira account not found",
  "konto Jiry nieaktywne": "Jira account inactive", "zmapowany, ale bez uprawnienia do przypisania w projekcie": "mapped, but not assignable in the project",
  "zmapowany, ale bez uprawnienia do przypisania w projekcie (jest leadem projektu)": "mapped, but not assignable in the project (is the project lead)",
  "oznaczona „nie w Jirze”, ale jest w user_map.json": "marked \"not in Jira\" but is in user_map.json",
  "Nie udało się pobrać listy osób, które można przypisać w projekcie.": "Could not fetch the list of people assignable in the project.",
  "Projekt nie występuje w arkuszu (albo jest w protected_projects.txt).": "The project is not in the sheet (or is in protected_projects.txt).",
  "Brak (albo nierozpoznany) link do projektu Asany w arkuszu.": "Missing (or unrecognised) Asana project link in the sheet.",

  // --- Mapowanie osób
  "Osoby z Asany → konta Jiry z wybranych grup. Wynik: user_map.json i asana_users_not_in_jira.json.": "Asana people → Jira accounts from selected groups. Result: user_map.json and asana_users_not_in_jira.json.",
  "1. Grupy Jiry, z których wybierasz konta": "1. Jira groups to choose accounts from", "Filtruj grupy…": "Filter groups…", "Nie wybrano grup.": "No groups selected.",
  "Wczytaj osoby": "Load people", "Wybierz przynajmniej jedną grupę Jiry.": "Select at least one Jira group.", "Wczytano osoby z Asany i Jiry.": "People loaded from Asana and Jira.",
  "Wybierz grupy Jiry i kliknij „Wczytaj osoby”.": "Select Jira groups and click \"Load people\".", "2. Dopasuj osoby": "2. Match people",
  "Jira nie zwraca adresów e-mail (widoczność kont) - propozycje opierają się tylko na imieniu i nazwisku.": "Jira does not return e-mail addresses (account visibility) - suggestions rely on names only.",
  "Start:": "Start:", "od pustej mapy (świeża migracja)": "from an empty map (fresh migration)", "Do decyzji": "To decide", "Z propozycją": "With suggestion",
  "Zmapowane": "Mapped", "Zmienione (niezapisane)": "Changed (unsaved)", "Szukaj osoby…": "Search person…", "Przyjmij wszystkie propozycje": "Accept all suggestions",
  "Widoczne „do decyzji” → nie w Jirze": "Visible \"to decide\" → not in Jira", "Konto w Jirze": "Jira account", "Propozycja": "Suggestion",
  "zachowaj wpisy osób spoza wczytanej listy": "keep entries of people outside the loaded list", "Zapisz mapowanie": "Save mapping",
  "wpisz imię lub e-mail…": "type a name or e-mail…", "Wybierz osobę z listy podpowiedzi.": "Pick a person from the suggestions.", "brak e-maila": "no e-mail",
  "zmapowany": "mapped", "zmapowany (zmiana)": "mapped (changed)", "jest propozycja": "has suggestion", "do decyzji": "to decide",
  "Brak widocznych osób do decyzji.": "No visible people to decide.", "e-mail": "e-mail", "e-mail (inna domena)": "e-mail (other domain)",
  "imię i nazwisko": "first and last name", "wzorzec imie.nazwisko@": "pattern first.last@",
  "\nWpisy osób spoza wczytanej listy zostaną zachowane.": "\nEntries of people outside the loaded list will be kept.",
  "\nPliki zostaną zastąpione (świeża mapa) - kopie trafią do migration_app/backups/.": "\nThe files will be replaced (fresh map) - backups go to migration_app/backups/.",

  // --- Mapowanie statusów
  "Sekcje Asany ze wszystkich projektów arkusza → statusy Jiry. Wynik: section_status_map.json (mapa główna; mapy pojedynczych projektów - w „Statusy i osoby”).": "Asana sections from all sheet projects → Jira statuses. Result: section_status_map.json (the main map; single-project maps are in \"Statuses and people\").",
  "1. Źródło": "1. Source", "Wymaga wczytanego arkusza (Start → krok 3). Przy kilkuset projektach trwa 2-3 minuty.": "Requires a loaded sheet (Start → step 3). With several hundred projects it takes 2-3 minutes.",
  "Projekt ze statusami:": "Project with statuses:", "domyślnie projekt wzorcowy": "template project by default", "Wczytaj sekcje i statusy": "Load sections and statuses",
  "2. Przypisz statusy": "2. Assign statuses", "Zadania ukończone w Asanie →": "Tasks completed in Asana →",
  "Ma pierwszeństwo przed sekcją (wyjątki ustawisz w mapie projektu: „nad_ukonczonym”).": "Takes precedence over the section (exceptions go in the project map: \"nad_ukonczonym\").",
  "(wybierz status)": "(choose a status)", "Bez mapowania": "No mapping", "Status spoza wzorca": "Status outside template", "W mapach projektów": "In project maps", "zmapowana w mapach projektów: ": "mapped in project maps: ", "Pomijane": "Skipped",
  "Szukaj sekcji…": "Search section…", "min. projektów:": "min. projects:", "Projektów": "Projects",
  "(bez mapowania - status startowy)": "(no mapping - initial status)", "Do zrobienia": "To do", "W toku": "In progress", "Zakończone": "Done",
  "— pomiń: nie migruj zadań z tej sekcji —": "— skip: do not migrate tasks from this section —",
  "Sekcja bez mapowania = zadania zostają w statusie startowym workflow (to nie błąd). „Pomiń” = zadania z tej sekcji, z podzadaniami, nie są migrowane.": "A section without mapping = tasks stay in the workflow's initial status (not an error). \"Skip\" = tasks from this section, with subtasks, are not migrated.",
  "zachowaj wpisy sekcji spoza wczytanej listy": "keep entries of sections outside the loaded list",
  "Wybierz status dla zadań ukończonych w Asanie.": "Choose the status for tasks completed in Asana.",
  "\nWpisy sekcji spoza wczytanej listy zostaną usunięte.": "\nEntries of sections outside the loaded list will be removed.",
  "ta sama nazwa": "same name", "zakończone": "done", "w toku": "in progress", "do zrobienia": "to do",

  // --- Autorstwo i licencja
  "Wszelkie prawa zastrzeżone.": "All rights reserved.",
  "Tylko do użytku prywatnego. Użycie komercyjne jest całkowicie zabronione.": "Private use only. Any commercial use is strictly prohibited.",
  "Licencja": "License",

  // --- Migracja projektu
  "Migracja projektu": "Project migration",
  "Jeden projekt z Asany do Jiry na podstawie linku - bez arkusza. Statusy mapujesz indywidualnie na statusy projektu wzorcowego albo źródłowego.": "One project from Asana to Jira from a link - no sheet. You map statuses individually to the statuses of the template or source project.",
  "1. Projekt w Asanie": "1. Project in Asana", "Link do projektu w Asanie": "Asana project link", "Wczytaj projekt": "Load project", "Wczytuję…": "Loading…",
  "Wklej link do projektu Asany.": "Paste the Asana project link.", "Nazwa projektu w Jirze": "Project name in Jira", "Puste = nazwa z Asany.": "Empty = the name from Asana.",
  "2. Nowy projekt w Jirze": "2. New project in Jira",
  "Puste = DEFAULT_LEAD_ACCOUNT_ID z .env (wzorzec) albo lead projektu źródłowego.": "Empty = DEFAULT_LEAD_ACCOUNT_ID from .env (template) or the source project's lead.",
  "Puste = projekt bez opisu.": "Empty = no description.", "3. Konfiguracja projektu": "3. Project configuration",
  "projekt wzorcowy (brak JIRA_TEMPLATE_PROJECT_KEY)": "template project (no JIRA_TEMPLATE_PROJECT_KEY)", "projekt źródłowy (jak w „Kopii projektu”)": "source project (as in \"Copy project\")",
  "Wzorzec: nowy projekt dostaje te same schematy co projekt wzorcowy, tablica - kolumny i statusy tablicy wzorcowej, a role - grupę deweloperów i administratorów z .env (albo osoby z sekcji „Role i dostęp”).": "Template: the new project gets the same schemes as the template project, its board gets the template board's columns and statuses, and roles get the developer group and administrators from .env (or the people from \"Roles and access\").",
  "Jak „Kopia projektu”: każdy schemat źródła dostaje kopię z nazwą nowego projektu, tablice z kolumnami i statusami są kopiowane.": "As in \"Copy project\": every source scheme gets a copy named after the new project; boards with columns and statuses are copied.",
  "od klucza": "from the key", "od nazwy": "from the name", "Wczytaj statusy": "Load statuses",
  "Brak JIRA_TEMPLATE_PROJECT_KEY w .env.": "No JIRA_TEMPLATE_PROJECT_KEY in .env.", "Podaj projekt źródłowy.": "Enter the source project.",
  "4. Sekcje Asany → statusy Jiry": "4. Asana sections → Jira statuses",
  "Wczytaj projekt Asany (krok 1) i statusy konfiguracji (krok 2) - wtedy pojawią się sekcje do zmapowania.": "Load the Asana project (step 1) and the configuration's statuses (step 2) - then the sections to map appear.",
  "Mapa trafia do section_status_map.<KLUCZ>.json i dotyczy tylko tego projektu (pierwszeństwo przed mapą główną). Propozycje: wpis z mapy głównej, jeśli wskazuje istniejący status, inaczej nazwa i znaczenie sekcji.": "The map goes to section_status_map.<KEY>.json and applies to this project only (precedence over the main map). Suggestions: the main map entry if it points to an existing status, otherwise the section's name and meaning.",
  "5. Uruchomienie": "5. Run", "Utwórz i migruj": "Create and migrate", "tylko utwórz i skonfiguruj (bez zadań)": "only create and configure (no tasks)",
  "projekt już istnieje (dokończ pozostałe kroki)": "project already exists (finish the remaining steps)",
  "Role: przy projekcie wzorcowym pusta lista oznacza grupę deweloperów i administratorów z .env; przy projekcie źródłowym - nikogo (poza leadem). Projekt spoza arkusza nie wchodzi do pełnego pipeline'u - kolejne synchronizacje uruchamiasz stąd (z „projekt już istnieje”).": "Roles: with the template project an empty list means the developer group and administrators from .env; with a source project - nobody (except the lead). A project outside the sheet is not part of the full pipeline - run further syncs from here (with \"project already exists\").",
  "Wczytaj projekt Asany (krok 1).": "Load the Asana project (step 1).", "Podaj klucz nowego projektu.": "Enter the new project key.",
  "Wczytaj statusy konfiguracji (krok 2).": "Load the configuration's statuses (step 2).",
  "Migracja jednego projektu (link do Asany)": "Single project migration (Asana link)",
  "Link do Asany w każdym zgłoszeniu": "Asana link in every issue",
  "Link do projektu Asany": "Asana project link", "Nazwa (domyślnie z Asany)": "Name (from Asana by default)", "Konfiguracja z": "Configuration from",
  "Projekt źródłowy (dla source)": "Source project (for source)", "Plik mapy sekcji (JSON)": "Section map file (JSON)", "Bez migracji zadań": "Without task migration",
  "Link do projektu w Asanie → nowy projekt w Jirze (konfiguracja ze wzorca albo z projektu źródłowego), indywidualna mapa sekcji, tablica, zadania. Bez arkusza. Karta „Migracja projektu”.": "Asana project link → new Jira project (configuration from the template or a source project), individual section map, board, tasks. No sheet. The \"Project migration\" tab.",

  // --- Kopia projektu
  "Nowy projekt z konfiguracją projektu źródłowego - każdy schemat dostaje własną kopię z nazwą nowego projektu. Nie korzysta z projektu wzorcowego ani ustawień z .env (poza dostępem do Jiry).": "A new project with the source project's configuration - every scheme gets its own copy named after the new project. Does not use the template project or .env settings (except Jira access).",
  "Projekt źródłowy (klucz)": "Source project (key)", "Klucz nowego projektu": "New project key",
  "2-10 znaków: wielkie litery i cyfry, na początku litera.": "2-10 characters: uppercase letters and digits, starting with a letter.",
  "Nazwa nowego projektu": "New project name", "Lead (accountId)": "Lead (accountId)", "Puste = lead projektu źródłowego.": "Empty = the source project's lead.",
  "Opis": "Description", "wartość": "value", "KLUCZ": "KEY", "Nazwa projektu": "Project name", "NOWA_ZMIENNA": "NEW_VARIABLE", "Puste = projekt bez opisu (opis źródła nie jest kopiowany).": "Empty = no description (the source description is not copied).", "Nazwy kopii schematów": "Scheme copy names",
  "od klucza nowego projektu": "from the new project's key", "od nazwy nowego projektu": "from the new project's name",
  "Kopiowane są tylko schematy, których źródło używa (np. bez schematu bezpieczeństwa nie powstaje jego kopia). W instancjach sprzed migracji na Field Schemes zamiast „Field Scheme” powstaje „Field Configuration Scheme”. Uprawnień, których Jira nie przyjmuje przy tworzeniu schematu (np. Product Discovery, COPY_PROJECT), nie da się skopiować - log je wymienia. Schemat o takiej nazwie, który już istnieje, jest używany ponownie. Same workflow, ekrany i konfiguracje pól pozostają wspólne ze źródłem - wskazują na nie nowe schematy.": "Only schemes the source uses are copied (e.g. no security scheme means no copy of it). On instances not yet migrated to Field Schemes a \"Field Configuration Scheme\" is created instead of \"Field Scheme\". Permissions Jira does not accept when creating a scheme (e.g. Product Discovery, COPY_PROJECT) cannot be copied - the log lists them. An existing scheme with the same name is reused. Workflows, screens and field configurations themselves stay shared with the source - the new schemes point to them.",
  "Role i dostęp": "Roles and access",
  "Wyszukaj w Jirze osoby i grupy, które mają mieć dostęp do nowego projektu, i nadaj każdej role. Uwaga: przypisywać zadania można tylko osobom z roli, która w schemacie uprawnień ma „Assignable User”.": "Search Jira for the people and groups that should have access to the new project and give each of them roles. Note: issues can only be assigned to people in a role that has \"Assignable User\" in the permission scheme.",
  "osoba": "person", "grupa": "group", "imię, e-mail albo fragment nazwy grupy…": "name, e-mail or part of a group name…", "Szukaj": "Search",
  "nowym wpisom nadaj rolę:": "give new entries the role:", "(wybiorę przy wpisie)": "(I'll choose per entry)", "Osoba / grupa": "Person / group",
  "Role w nowym projekcie": "Roles in the new project", "+ dodaj rolę…": "+ add role…", "usuń rolę": "remove role", "wybierz rolę": "choose a role", "Usuń": "Remove",
  "Lista jest pusta - nikt nie zostanie dodany do ról nowego projektu (poza leadem). Wyszukaj osoby albo grupy powyżej.": "The list is empty - nobody will be added to the new project's roles (except the lead). Search for people or groups above.",
  "Wpisz co najmniej 2 znaki.": "Type at least 2 characters.", "Szukam…": "Searching…", "Nic nie znaleziono.": "Nothing found.", " (nieaktywne)": " (inactive)",
  "Co jeszcze skopiować": "What else to copy",
  "Domyślnie wszystko: ustawienia projektu (kategoria, URL, domyślne przypisanie), komponenty, wersje, konteksty pól niestandardowych ograniczone do źródła oraz tablice - filtr z tymi samymi udostępnieniami, kolumny i przypisanie statusów do kolumn jak w źródle (przez API, bez przeglądarki; po zapisie skrypt sprawdza, czy każda kolumna ma te same statusy).": "Everything by default: project settings (category, URL, default assignee), components, versions, custom field contexts limited to the source and boards - a filter with the same sharing, columns and the status-to-column mapping as in the source (via the API, no browser; after saving the script checks that every column has the same statuses).",
  "bez wersji": "no versions", "bez komponentów": "no components", "bez kontekstów pól (szybciej - pomija przegląd wszystkich pól)": "no field contexts (faster - skips scanning all fields)",
  "bez tablic": "no boards", "projekt docelowy już istnieje (dokończenie przerwanej kopii)": "target project already exists (finishing an interrupted copy)",
  "ID tablic źródła": "Source board IDs", "Puste = tablice przypisane do projektu źródłowego.": "Empty = boards assigned to the source project.",
  "Uruchomienie": "Run", "Podgląd (dry-run)": "Preview (dry-run)", "Utwórz kopię": "Create copy",
  "Zacznij od podglądu - pokazuje schematy źródła, nazwy kopii, role, komponenty i tablice. „Utwórz kopię” poprosi o potwierdzenie.": "Start with the preview - it shows the source schemes, copy names, roles, components and boards. \"Create copy\" asks for confirmation.",
  "projekt źródłowy": "the source project", "klucz nowego projektu": "the new project key", "nazwę nowego projektu": "the new project name",
  "Lista „Role i dostęp” jest pusta - nikt nie zostanie dodany do ról nowego projektu (poza leadem). Kontynuować?": "The \"Roles and access\" list is empty - nobody will be added to the new project's roles (except the lead). Continue?",
  "Wybierz rolę dla: ": "Choose a role for: ",

  // --- Konfiguracja
  "Przed każdym zapisem kopia trafia do migration_app/backups/": "Before every save a backup goes to migration_app/backups/",
  "Zmienne .env": ".env variables", "Pliki: mapy, listy, schematy": "Files: maps, lists, schemas", "Pliku .env jeszcze nie ma - zostanie utworzony przy zapisie.": "There is no .env file yet - it will be created on save.",
  "Zapisz .env": "Save .env", "Nic nie zmieniono.": "Nothing changed.", "puste": "empty", "Pliki": "Files", "Nowa mapa projektu": "New project map",
  "Otwórz": "Open", "Wybierz plik z listy.": "Pick a file from the list.", "JSON poprawny": "JSON valid", "Błąd JSON: ": "JSON error: ",
  "nowy plik - powstanie przy zapisie": "new file - created on save", "Jeden klucz w linii, # = komentarz.": "One key per line, # = comment.",
  "Formatuj JSON": "Format JSON", "Popraw JSON przed zapisem.": "Fix the JSON before saving.",
  "Wpisy: \"Sekcja\": \"Status\" albo {\"status\", \"labels\", \"components\", \"resolution\", \"nad_ukonczonym\", \"pomin\"}. \"__ukonczone__\" = status dla ukończonych, \"__szablon__\" = nazwa szablonu z section_status_profiles/.": "Entries: \"Section\": \"Status\" or {\"status\", \"labels\", \"components\", \"resolution\", \"nad_ukonczonym\", \"pomin\"}. \"__ukonczone__\" = status for completed tasks, \"__szablon__\" = profile name from section_status_profiles/.",

  // --- Raporty
  "Raporty i logi": "Reports and logs", "logs/, pipeline_summaries/, repair_reports/, raporty CSV/JSON": "logs/, pipeline_summaries/, repair_reports/, CSV/JSON reports",
  "Filtruj…": "Filter…", "Plik": "File", "Rozmiar": "Size", "Zmieniony": "Modified", "Wybierz plik.": "Pick a file.", "Pusty plik.": "Empty file.",
  " · pokazano koniec pliku": " · showing the end of the file",

  // --- komunikaty serwera
  "Niedozwolone źródło żądania.": "Request origin not allowed.", "Ścieżka poza folderem projektu.": "Path outside the project folder.",
  "Ten plik nie jest dostępny z panelu.": "This file is not available from the panel.", "Nieznany adres API.": "Unknown API address.", "Nie znaleziono": "Not found",
  "Brak wyniku sprawdzenia dla tego projektu.": "No check result for this project.", "Niepoprawny klucz projektu.": "Invalid project key.",
  "Niepoprawny klucz nowego projektu.": "Invalid new project key.", "Niepoprawny gid osoby z Asany.": "Invalid Asana person gid.",
  "Podaj projekt albo tekst do wyszukania.": "Enter a project or a search text.",
  "To nie jest klucz konta serwisowego (brak type=service_account, client_email lub private_key).": "This is not a service account key (missing type=service_account, client_email or private_key).",
  "Uruchomienie zmienia dane - wymaga potwierdzenia.": "The run changes data - confirmation required.", "Proces już się zakończył.": "The process has already finished.",
  "Nie można usunąć trwającego uruchomienia.": "A running run cannot be removed.",

  // --- katalog narzędzi: grupy
  "Pipeline migracji": "Migration pipeline", "Asana: statusy i przypisania": "Asana: statuses and assignees", "Naprawy": "Repairs",
  "Konfiguracja Jiry": "Jira configuration", "Kopia projektów": "Project copies", "Inne": "Other",
};

// Katalog narzędzi: tytuły, opisy i etykiety pól.
Object.assign(EN, {
  "Wczytaj arkusz projektów": "Load project sheet",
  "2. Kolumny i statusy w kolumnach": "2. Columns and statuses in columns",
  "Układ kolumn i przypisanie statusów według tablicy wzorcowej - przez API, bez przeglądarki. Tablica, której workflow nie pasuje do wzorca, jest pomijana (chyba że --force).": "Column layout and status mapping from the template board - via the API, no browser. A board whose workflow does not match the template is skipped (unless --force).",
  "ID tablicy wzorcowej (domyślnie z arkusza / .env)": "Template board ID (default from the sheet / .env)",
  "Zapisz mimo niepasującego workflow (--force)": "Write despite a non-matching workflow (--force)",
  "3. Kontrola kolumn i statusów": "3. Column and status check", "Czy tablice mają kolumny i statusy jak wzorzec - tylko odczyt.": "Whether boards have the template's columns and statuses - read-only.",
  "Kolumny tablic (stara wersja, przeglądarka)": "Board columns (old version, browser)",
  "Poprzednia wersja kroku 2 - przez przeglądarkę (Playwright). Zastąpiona przez „2. Kolumny i statusy w kolumnach”.": "The previous step 2 - through the browser (Playwright). Replaced by \"2. Columns and statuses in columns\".",
  "Statusy w kolumnach (stara wersja, przeglądarka)": "Statuses in columns (old version, browser)",
  "Poprzednia wersja kroku 3 - przez przeglądarkę (Playwright), bez trybu podglądu. Zastąpiona przez „2. Kolumny i statusy w kolumnach”.": "The previous step 3 - through the browser (Playwright), no preview mode. Replaced by \"2. Columns and statuses in columns\".",
  "Otwiera przeglądarkę do ręcznego logowania (SSO/2FA). Potrzebne tylko starszym narzędziom przez przeglądarkę (stare kroki kolumn, copy_project.py, kopia z innej instancji) - pipeline i „Kopia projektu” działają bez niej. Naciśnij Enter w polu wejścia po zalogowaniu.": "Opens a browser for manual login (SSO/2FA). Only needed by the older browser tools (old column steps, copy_project.py, copy from another instance) - the pipeline and \"Copy project\" work without it. Press Enter in the input field after logging in.",
  "Projekty → kolumny i statusy tablic (API) → kontrola tablic → kandydaci na Epiki → synchronizacja zadań (opcjonalnie weryfikacja). Bez przeglądarki.": "Projects → board columns and statuses (API) → board check → Epic candidates → task sync (optional verification). No browser.",
  "Odczyt arkusza Google do cache panelu (lista projektów). Może potrwać kilka minut, gdy wiersze nie mają nazw.": "Reads the Google Sheet into the panel cache (project list). Can take a few minutes when rows have no names.",
  "Pełny pipeline": "Full pipeline", "Projekty → kolumny → statusy kolumn → kandydaci na Epiki → synchronizacja zadań (opcjonalnie weryfikacja).": "Projects → columns → column statuses → Epic candidates → task sync (optional verification).",
  "Tylko podgląd (dry-run)": "Preview only (dry-run)", "Plik z kluczami": "Keys file", "Limit projektów": "Project limit", "Równolegle (N procesów, maks. 3)": "In parallel (N processes, max 3)",
  "Na koniec porównanie z Asaną (--verify)": "Compare with Asana at the end (--verify)", "Wykryj Epiki od nowa (--force-epics)": "Detect Epics again (--force-epics)",
  "Pomiń kolumny i statusy kolumn": "Skip columns and column statuses", "Pomiń Epiki i synchronizację": "Skip Epics and sync", "Tylko jeden krok": "Only one step",
  "Pomiń kroki (po jednym w linii)": "Skip steps (one per line)",
  "1. Tworzenie projektów": "1. Create projects", "Tworzy w Jirze projekty z arkusza Google (cały arkusz albo shard).": "Creates Jira projects from the Google Sheet (whole sheet or a shard).",
  "Shard K/N": "Shard K/N", "2. Kolumny tablic": "2. Board columns", "Ustawia kolumny tablic Kanban według tablicy wzorcowej.": "Sets Kanban board columns from the template board.",
  "ID tablicy wzorcowej": "Template board ID", "Zostaw stare kolumny": "Keep old columns", "Widoczna przeglądarka + pauza": "Visible browser + pause",
  "3. Statusy w kolumnach": "3. Statuses in columns", "Przypisuje statusy do kolumn przez przeglądarkę (Playwright). Brak trybu podglądu.": "Assigns statuses to columns through the browser (Playwright). No preview mode.",
  "Przerwa między projektami (ms)": "Pause between projects (ms)", "4. Kandydaci na Epiki": "4. Epic candidates",
  "Wykrywa w Asanie zadania z podzadaniami podzadań (zapisuje epic_candidates.json).": "Detects Asana tasks with sub-subtasks (writes epic_candidates.json).",
  "Skanuj od nowa (--force)": "Scan again (--force)", "5. Synchronizacja Asana → Jira": "5. Sync Asana → Jira",
  "Zadania, podzadania, komentarze, załączniki, statusy i przypisania.": "Tasks, subtasks, comments, attachments, statuses and assignees.",
  "Kierunek zwrotny Jira → Asana": "Reverse direction Jira → Asana", "Przenosi zmiany z Jiry z powrotem do Asany. Pierwsze uruchomienie dla projektu zapisuje tylko punkt startowy (nic nie zmienia w Asanie); kolejne przenoszą zmiany zrobione w Jirze od tej chwili. Zapisuje do Asany!": "Moves changes from Jira back to Asana. The first run for a project only records a starting point (nothing changes in Asana); later runs move changes made in Jira since then. Writes to Asana!",
  "Link do projektu Asany (projekt spoza arkusza)": "Asana project link (project outside the sheet)",
  "Zadania, podzadania, komentarze, załączniki, statusy i przypisania. Opcjonalnie tagi, obserwujący i pola niestandardowe.": "Tasks, subtasks, comments, attachments, statuses and assignees. Optionally tags, followers and custom fields.",
  "Tagi jako etykiety": "Tags as labels",
  "Obserwujący (przez mapę osób)": "Followers (via the people map)",
  "Pola niestandardowe (custom_field_map.json albo po nazwie)": "Custom fields (custom_field_map.json or by name)",
  "Zapis sesji przeglądarki": "Save browser session",
  "Otwiera przeglądarkę do ręcznego logowania (SSO/2FA) - potrzebne przed krokami 2-3. Naciśnij Enter w polu wejścia po zalogowaniu.": "Opens a browser for manual login (SSO/2FA) - needed before steps 2-3. Press Enter in the input field after logging in.",
  "Załączniki poziomu projektu": "Project-level attachments", "Przenosi pliki przypięte bezpośrednio do projektu w Asanie.": "Moves files attached directly to the Asana project.",
  "Uzupełnij jira_link w arkuszu": "Fill in jira_link in the sheet", "Wpisuje linki do projektów Jiry w kolumnę jira_link arkusza.": "Writes Jira project links into the sheet's jira_link column.",
  "Statusy i przypisania (sprawdzenie)": "Statuses and assignees (check)",
  "Sekcje → statusy, osoby → konta Jiry i zgodność zmigrowanych zadań. Wyniki w zakładce „Statusy i osoby”.": "Sections → statuses, people → Jira accounts and consistency of migrated tasks. Results in the \"Statuses and people\" tab.",
  "Projekty": "Projects", "Czytaj arkusz od nowa (wolne)": "Read the sheet again (slow)",
  "Sekcje i statusy do mapowania": "Sections and statuses for mapping",
  "Sekcje ze wszystkich projektów arkusza i statusy projektu Jiry - źródło strony „Mapowanie statusów”.": "Sections from all sheet projects and a Jira project's statuses - the source of the \"Status mapping\" page.",
  "Projekt ze statusami (domyślnie wzorcowy)": "Project with statuses (template by default)",
  "Wszystkie sekcje Asany": "All Asana sections", "Sekcje ze wszystkich projektów arkusza - do uzupełniania map statusów.": "Sections from all sheet projects - for filling in status maps.",
  "Zapisz do JSON": "Save to JSON", "Sekcje jednego projektu": "Sections of one project",
  "Unikalne sekcje projektu Asany (podaj nazwę projektu albo link).": "Unique sections of an Asana project (give the project name or link).",
  "Link do projektu Asany": "Asana project link", "albo nazwa projektu": "or project name", "Członkowie workspace'u": "Workspace members",
  "gid, imię i e-mail osób w Asanie.": "gid, name and e-mail of Asana people.", "Tylko członkowie": "Members only", "Tylko goście": "Guests only", "Także nieaktywni": "Include inactive",
  "Generuj user_map.json": "Generate user_map.json", "Dopasowuje osoby Asana → Jira po e-mailu i zapisuje user_map.json.": "Matches Asana → Jira people by e-mail and writes user_map.json.",
  "Uwzględnij gości": "Include guests", "Sekcja pojedynczego zadania": "Section of a single task", "Diagnostyka sekcji zadania Asany dwiema metodami.": "Diagnoses an Asana task's section in two ways.",
  "GID zadania": "Task GID", "GID projektu": "Project GID", "Statusy projektu Jiry": "Jira project statuses",
  "Statusy faktycznie dostępne w projekcie - cel map sekcji.": "Statuses actually available in the project - the target of section maps.",
  "Klucz projektu": "Project key", "Test tokenu Asany": "Asana token test", "Sprawdza ASANA_TOKEN przez /users/me.": "Checks ASANA_TOKEN via /users/me.",
  "Liczba zadań w Jirze": "Task count in Jira", "Łączna liczba zadań w projektach migracji, opcjonalnie vs Asana.": "Total task count in migration projects, optionally vs Asana.",
  "Tylko suma": "Total only", "Porównaj z Asaną": "Compare with Asana", "Pełne porównanie (wolne)": "Full comparison (slow)", "Licz załączniki (wolne)": "Count attachments (slow)",
  "Porównanie Asana ↔ Jira": "Asana ↔ Jira comparison", "Wszystkie poziomy zagnieżdżenia, opcjonalnie załączniki.": "All nesting levels, optionally attachments.",
  "Wszystkie z arkusza": "All from the sheet", "Także załączniki": "Include attachments", "Tylko najwyższy poziom": "Top level only", "Wypisz konkretne zadania": "List specific tasks",
  "Audyt hierarchii": "Hierarchy audit", "Epic → Task → Sub-task; z --apply naprawia.": "Epic → Task → Sub-task; repairs with --apply.", "Pełny (dłużej)": "Full (longer)",
  "Wprowadź zmiany (--apply)": "Apply changes (--apply)", "Napraw tylko zgłoszenia": "Repair only issues", "Z --apply: tylko pokaż operacje": "With --apply: only show operations",
  "Audyt kandydatów na Epik": "Epic candidate audit", "Tylko odczyt.": "Read-only.", "Zdublowane zadania": "Duplicate tasks",
  "Wykrywa duplikaty tytułów w projektach migracji.": "Detects duplicate titles in migration projects.", "Zdublowane komentarze/załączniki": "Duplicate comments/attachments",
  "Wewnątrz pojedynczych zadań.": "Within individual tasks.", "Różnice tytułów zadań": "Task title differences", "Jeden projekt: tytuły Jira vs Asana.": "One project: Jira vs Asana titles.",
  "Różnice podzadań": "Subtask differences", "Jeden projekt: podzadania Jira vs Asana.": "One project: Jira vs Asana subtasks.",
  "Różnice załączników": "Attachment differences", "Jeden projekt: załączniki per zadanie.": "One project: attachments per task.",
  "Projekty bez stanu": "Projects without state", "Klucze z arkusza nieobecne w task_sync_state.json.": "Sheet keys missing from task_sync_state.json.",
  "Wpisy z obcych projektów": "Entries from other projects", "Szuka w stanie zadań przypiętych do złego projektu.": "Finds tasks attached to the wrong project in the state.",
  "Zdublowane wiersze arkusza": "Duplicate sheet rows", "Ten sam projekt w kilku wierszach.": "The same project in several rows.",
  "Dostęp do projektu Jiry": "Jira project access", "Czy dane logowania z .env widzą projekt.": "Whether the .env credentials can see the project.", "Inny adres Jiry": "Another Jira URL",
  "Wpisy w stanie": "State entries", "Ile zadań jest zmapowanych w task_sync_state.json.": "How many tasks are mapped in task_sync_state.json.",
  "Naprawa wielu projektów": "Repair many projects", "relink → dup → migrated dla listy projektów.": "relink → dup → migrated for a list of projects.", "Krok": "Step",
  "Bez pytania o potwierdzenie (--yes)": "Without confirmation prompt (--yes)", "relink: także PODOBNE": "relink: also SIMILAR", "relink: także KOLEJNOŚĆ": "relink: also ORDER",
  "relink: także TREŚĆ": "relink: also CONTENT", "dup: usuń też BRAK_W_ASANIE": "dup: also remove BRAK_W_ASANIE",
  "Odtwórz powiązania (relink)": "Restore links (relink)", "Odbudowuje powiązania Asana ↔ Jira w stanie.": "Rebuilds Asana ↔ Jira links in the state.",
  "Także TREŚĆ": "Also CONTENT", "Także KOLEJNOŚĆ": "Also ORDER", "Także PODOBNE": "Also SIMILAR", "Wyjaśnij zgłoszenie": "Explain issue", "Plik raportu": "Report file",
  "Duplikaty z raportu relink": "Duplicates from the relink report", "Przepina stan i usuwa duplikaty.": "Re-links the state and removes duplicates.", "Tylko N par": "Only N pairs",
  "Usuń też BRAK_W_ASANIE": "Also remove BRAK_W_ASANIE", "Raport relink": "Relink report", "Plik planu": "Plan file",
  "Usuń zdublowane zadania": "Remove duplicate tasks", "Skan → --collect → --delete.": "Scan → --collect → --delete.", "Zbierz wyniki shardów": "Collect shard results",
  "Usuń osierocone duplikaty": "Remove orphaned duplicates", "Zadania „migriert zu Jira”": "\"migriert zu Jira\" tasks", "Porządkuje zadania przeniesione ręcznie do Jiry.": "Tidies tasks moved to Jira by hand.",
  "Zamknij dzieci zakończonych": "Close children of completed", "Zamyka w Jirze wszystko pod zakończonym zadaniem.": "Closes everything under a completed task in Jira.",
  "Co decyduje o zakończeniu": "What decides completion", "Status docelowy": "Target status", "Także w Asanie": "Also in Asana", "Tylko zgłoszenia": "Only issues",
  "Przekreślone klucze": "Struck-through keys", "Czyści Resolution w otwartych zgłoszeniach.": "Clears Resolution in open issues.",
  "Odśwież flagi w stanie": "Refresh state flags", "Flagi hierarchii w task_sync_state.json.": "Hierarchy flags in task_sync_state.json.",
  "Dodaj Epiki do zmigrowanych": "Add Epics to migrated", "Projekty zmigrowane przed obsługą Epików.": "Projects migrated before Epic support.",
  "Cofnij fałszywe Epiki": "Revert false Epics", "Na podstawie CSV z audytu kandydatów.": "Based on the candidate audit CSV.", "Plik CSV": "CSV file",
  "Także Epiki z dziećmi": "Also Epics with children", "Tylko klucze": "Only keys", "Głębokie łańcuchy": "Deep chains", "Naprawia WYMAGA_RECZNEJ_DECYZJI_GLEBOKA.": "Repairs WYMAGA_RECZNEJ_DECYZJI_GLEBOKA.",
  "Plik CSV audytu": "Audit CSV file", "Z --apply: tylko pokaż": "With --apply: only show", "Tylko te rooty": "Only these roots",
  "Konwersja na Epik": "Convert to Epic", "Ręcznie wskazane zgłoszenia → Epic.": "Hand-picked issues → Epic.", "Zgłoszenia": "Issues", "Podzadania → Task": "Subtasks → Task",
  "Brakujące wpisy stanu": "Missing state entries", "Przyczyna powracających duplikatów.": "The cause of recurring duplicates.",
  "Usuń nieaktualne wpisy stanu": "Remove stale state entries", "Wpisy wskazujące na nieistniejące zgłoszenia.": "Entries pointing to issues that no longer exist.",
  "Usunięte w Jirze → pomijane": "Deleted in Jira → skipped", "Oznacza w stanie zgłoszenia usunięte przez zespół.": "Marks issues deleted by the team in the state.",
  "Napraw uszkodzony plik stanu": "Repair a damaged state file", "Po przerwanym zapisie task_sync_state.json.": "After an interrupted write of task_sync_state.json.",
  "Konfiguracja z YAML": "Configuration from YAML", "Statusy, workflow, ekrany, pola ze schematu YAML.": "Statuses, workflows, screens, fields from a YAML schema.",
  "Plik YAML": "YAML file", "Tylko kroki (po przecinku)": "Only steps (comma-separated)", "Tylko workflow": "Only workflows", "Także niekompletne workflow": "Include incomplete workflows",
  "Grupy przejść workflow": "Workflow transition groups", "Kto może wykonać przejście (transition_groups.yaml).": "Who can perform a transition (transition_groups.yaml).",
  "Pola do field scheme": "Fields to field scheme", "Dodaje pola z ekranów do field scheme projektu.": "Adds fields from screens to the project's field scheme.",
  "YAML": "YAML", "ID pól": "Field IDs", "Zdejmij pole z ekranów": "Remove field from screens", "Pole i dane zostają.": "The field and its data stay.",
  "Pole": "Field", "Wszystkie ekrany": "All screens", "Zdejmij status z kolumny": "Remove status from column", "Status wraca do niezmapowanych.": "The status returns to unmapped.",
  "ID tablic": "Board IDs", "Kolumna": "Column", "Dodaj grupę do ról": "Add group to roles", "Grupa → role w istniejących projektach.": "Group → roles in existing projects.",
  "Grupa": "Group", "Role": "Roles", "Dodaj administratora": "Add administrator", "Osoba (accountId) → rola Administrator.": "Person (accountId) → Administrator role.",
  "accountId": "accountId", "Nazwa roli": "Role name", "Kategoria projektów": "Project category", "Przypisuje Project Category.": "Assigns a Project Category.",
  "Tylko wypisz kategorie": "Only list categories", "Kategoria": "Category", "Kopia schematu uprawnień": "Permission scheme copy",
  "Nowy schemat z istniejącego, opcjonalnie podpięty.": "A new scheme from an existing one, optionally attached.", "ID źródła": "Source ID", "Nazwa": "Name",
  "Podepnij do projektu": "Attach to project", "Kolumny tablicy wzorcowej": "Template board columns", "Odczyt układu kolumn.": "Reads the column layout.",
  "ID tablicy": "Board ID", "Grupy Atlassiana": "Atlassian groups", "Wszystkie grupy, opcjonalnie z filtrem.": "All groups, optionally filtered.", "Fragment nazwy": "Name fragment",
  "Członkowie grupy": "Group members", "accountId, nazwisko, e-mail.": "accountId, name, e-mail.", "Nazwa grupy": "Group name",
  "Pola wymagane": "Required fields", "Przy tworzeniu zadania danego typu.": "When creating an issue of a given type.", "Typ zadania": "Issue type",
  "Kopia projektu z kopiami schematów": "Project copy with scheme copies",
  "Samodzielna kopia projektu: każdy schemat źródła kopiowany pod nazwą nowego projektu, ustawienia, role, komponenty, wersje, tablice. Bez projektu wzorcowego. Karta „Kopia projektu”.": "A standalone project copy: every source scheme copied under the new project's name, settings, roles, components, versions, boards. No template project. The \"Copy project\" tab.",
  "Projekt źródłowy": "Source project", "Nazwy schematów od": "Scheme names from", "Lead (accountId, domyślnie jak w źródle)": "Lead (accountId, source's by default)",
  "Opis (domyślnie pusty)": "Description (empty by default)", "Plik: kto z jaką rolą (karta „Kopia projektu”)": "File: who gets which role (\"Copy project\" tab)",
  "Role i członkowie jak w źródle": "Roles and members as in the source", "Nikogo nie dodawaj do ról": "Add nobody to roles", "Bez wersji": "No versions",
  "Bez komponentów": "No components", "Bez kontekstów pól (szybciej)": "No field contexts (faster)", "Bez tablic": "No boards", "Projekt docelowy już istnieje": "Target project already exists",
  "Kopia projektu (ta sama Jira)": "Project copy (same Jira)", "Nowy projekt z konfiguracją, tablicami i kolumnami.": "A new project with configuration, boards and columns.",
  "Źródło": "Source", "Klucz nowego": "New key", "Nazwa nowego": "New name", "Kopiuj członków ról": "Copy role members", "Projekt już istnieje": "Project already exists",
  "Kopia z innej instancji Jiry": "Copy from another Jira instance", "Projekt z zadaniami między instancjami (SOURCE_JIRA_* w .env).": "A project with its issues between instances (SOURCE_JIRA_* in .env).",
  "Klucz źródła": "Source key", "Klucz docelowy": "Target key", "Nazwa (gdy nowy)": "Name (if new)", "Lead (gdy nowy)": "Lead (if new)", "Aktualizuj statusy": "Update statuses",
  "Pomiń istniejące tytuły": "Skip existing titles", "Uzupełnij skopiowane": "Complete copied", "Tylko statusy źródła": "Only source statuses", "Bez statusów źródła": "Without source statuses",
  "Plik mapy statusów": "Status map file", "Status dla Done": "Status for Done", "Status dla reszty": "Status for the rest", "Plik mapy osób": "People map file",
  "Dodatkowe grupy (Grupa=Rola, po jednej w linii)": "Extra groups (Group=Role, one per line)", "Bez notki o pochodzeniu": "No origin note", "Bez dodawania dostępu": "No access granting",
  "Epiki i Story z arkusza MVP": "Epics and Stories from the MVP sheet", "Z pliku .xlsx „Zakres MVP”.": "From the \"Zakres MVP\" .xlsx file.", "Plik .xlsx": ".xlsx file",
  "Tylko status": "Only status", "Tylko Epiki": "Epics only", "Tylko N Story": "Only N Stories", "Eksport Story do .xlsx": "Export Stories to .xlsx",
  "Do kopii arkusza albo osobnego pliku.": "To a copy of the sheet or a separate file.", "Arkusz źródłowy": "Source sheet", "Plik wynikowy": "Output file", "Własny JQL": "Custom JQL",
  "Analysis dla Story": "Analysis for Stories", "Zadanie Analysis powiązane z każdym Story.": "An Analysis issue linked to every Story.", "JQL Story": "Story JQL",
  "Eksport TSV/CSV": "TSV/CSV export", "Tylko N": "Only N", "Dezaktywuj zawieszone konta": "Deactivate suspended accounts",
  "Organizacja Atlassian: suspended → deactivated.": "Atlassian organisation: suspended → deactivated.", "Wykonaj dezaktywację": "Run the deactivation", "ID organizacji": "Organisation ID",
});

for (const [k, v] of Object.entries(EN)) if (k !== k.trim() && EN[k.trim()] === undefined) EN[k.trim()] = v.trim();

// Opisy zmiennych .env w widoku Konfiguracja (po polsku pochodzą z komentarzy w .env.example).
const ENV_HELP_EN = {
  JIRA_BASE_URL: "Your Jira Cloud URL, e.g. https://yourcompany.atlassian.net (required).",
  JIRA_EMAIL: "E-mail of the account the API token belongs to (an account with Jira administrator permission).",
  JIRA_API_TOKEN: "Create at https://id.atlassian.com/manage-profile/security/api-tokens",
  ASANA_TOKEN: "Personal Access Token: https://app.asana.com/0/my-apps",
  ASANA_WORKSPACE_GID: "GID of the Asana workspace to take projects from (the panel lists them after a connection test).",
  GOOGLE_APPLICATION_CREDENTIALS: "Path to the Google service account JSON key. The panel can save a pasted key to ./service_account.json and shows the address to share the sheet with (Viewer; Editor if the scripts should write Jira keys).",
  GOOGLE_SHEET_ID: "Sheet ID from its URL: https://docs.google.com/spreadsheets/d/THE_ID/edit",
  GOOGLE_SHEET_RANGE: "Range to read (tab + columns). The first row holds the headers, e.g. project_name, jira_key, asana_project_link, lead_account_id, board_template_id.",
  JIRA_TEMPLATE_PROJECT_KEY: "Key of an EXISTING company-managed Jira project whose workflow, screens, fields, permissions and notifications are copied to every new project. The panel also takes the statuses for section mapping from it. Empty = Jira defaults. Requires Jira administrator permission.",
  JIRA_TEMPLATE_BOARD_ID: "Template board ID (column layout) when the template project has more than one board. Empty = the template project's board. A sheet row can override it (board_template_id).",
  DEFAULT_PROJECT_TYPE_KEY: "Project type for new projects when the sheet row has no project_type_key.",
  DEFAULT_TEMPLATE_KEY: "Project template for new projects when the sheet row has no template_key (Kanban company-managed by default).",
  DEFAULT_LEAD_ACCOUNT_ID: "Default lead (accountId) for projects whose sheet row has no lead_account_id. Jira REQUIRES a valid lead when creating a project. Your accountId is shown in the panel (Start → Jira connection test).",
  DEVELOPER_GROUP_NAME: "Jira group added to EVERY newly created project. Empty = none. Note: issues can only be assigned to people in a role that has \"Assignable User\" in the permission scheme.",
  DEVELOPER_ACCOUNT_IDS: "Specific users (accountId, comma-separated) added to the same role.",
  DEVELOPER_ROLE_NAME: "Project role for the group/users above. Check with: python list_project_roles.py",
  ADMIN_ACCOUNT_ID: "accountId (one or several, comma-separated) added as administrator to EVERY project.",
  ADMIN_ROLE_NAME: "Project role for the administrators above.",
  ADD_LEAD_AS_ADMIN: "true = each project's lead is also added to that project's administrator role.",
  JIRA_EMAIL_DOMAIN: "Domain of Jira e-mail addresses, pattern First.Last@domain - fallback when matching people (when the Asana e-mail differs from Jira's).",
  DEFAULT_TYPE_FIELD_ID: "A custom field required on issue creation that has several possible values (cannot be filled automatically). Empty if you have no such field. Check: python check_required_fields.py PROJECTKEY",
  DEFAULT_TYPE_FIELD_VALUE: "The value to set in that field.",
  MIGRATED_TO_JIRA_PATTERN: "Regex for Asana task titles of tasks moved to Jira by hand (default: \"migriert zu Jira\"). Leave commented out to use the default.",
  SLACK_WEBHOOK_URL: "Slack incoming webhook - a summary of every pipeline run.",
  SOURCE_JIRA_BASE_URL: "Another Jira instance to copy from (copy_project_cross_instance.py only).",
  SOURCE_JIRA_EMAIL: "E-mail for the source Jira instance.", SOURCE_JIRA_API_TOKEN: "API token for the source Jira instance.",
  PROJECT_KEY_CACHE_FILE: "File with the project name -> generated Jira key mapping (stable keys between runs).",
  BULK_COLUMNS_TIMEOUT_MS: "Page load timeout for the column settings page (ms) in step 2.",
  CLOUD_ID: "For copy_project.py when Atlassian changes its frontend (see README 11b).",
  GRAPHQL_QUERY_HASH: "For copy_project.py when Atlassian changes its frontend (see README 11b).",
  GRAPHQL_OPERATION: "For copy_project.py when Atlassian changes its frontend (see README 11b).",
  ATLASSIAN_ORG_ID: "atlassian_suspend_to_deactivate.py only (Atlassian organisation).", ATLASSIAN_API_KEY: "atlassian_suspend_to_deactivate.py only (organisation API key).",
};

// Teksty ze wstawkami: [wzorzec dla polskiego tekstu, angielski odpowiednik ($1, $2 … = wstawki)].
const EN_PATTERNS = [
  [/^Ukończone kroki: (\d+) z (\d+)\. Kolejność ma znaczenie - każdy krok korzysta z poprzednich\.$/, "Completed steps: $1 of $2. Order matters - every step builds on the previous ones."],
  [/^Brakuje: (.+)$/, "Missing: $1"],
  [/^(\d+) projektów, wczytano (.+)$/, "$1 projects, loaded $2"],
  [/^user_map\.json: (\d+) osób, nie w Jirze: (\d+)$/, "user_map.json: $1 people, not in Jira: $2"],
  [/^section_status_map\.json: (\d+) sekcji(.*)$/, (m, n, rest) => `section_status_map.json: ${n} sections` + rest
    .replace(", ukończone → ", ", completed → ").replace("brak statusu dla ukończonych zadań", "no status for completed tasks")
    .replace(/statusy spoza projektu (\S+): /, "statuses outside project $1: ")],
  [/^Sesja sprzed ([\d.]+) h$/, "Session from $1 h ago"],
  [/^Ostatni podgląd: (.+)$/, "Last preview: $1"],
  [/^(\d+) projektów, (\d+) zadań w stanie$/, "$1 projects, $2 tasks in the state"],
  [/^(\d+) sprawdzonych projektów$/, "$1 projects checked"],
  [/^Sprawdzono (.+?)( - przed ostatnią zmianą \.env)?$/, (m, d, old) => `Checked ${d}${old ? " - before the last .env change" : ""}`],
  [/^Sesja przeglądarki ma ([\d.]+) h - może wymagać ponownego logowania przed krokami 2-3\.$/, "The browser session is $1 h old - it may need a new login before steps 2-3."],
  [/^Źródło: (.+?) · wczytano (.+)$/, (m, src, d) => `Source: ${EN[src] || src} · loaded ${d}`],
  [/^(\d+) z (\d+)$/, "$1 of $2"],
  [/^(\d+) różnic$/, "$1 differences"],
  [/^(\d+) ostrzeżeń$/, "$1 warnings"],
  [/^(\d+) ostrz\.$/, "$1 warn."],
  [/^Projekt (\S+) nie był jeszcze sprawdzany\.$/, "Project $1 has not been checked yet."],
  [/^Sprawdź (\S+)$/, "Check $1"],
  [/^Sekcje \((\d+)\)$/, "Sections ($1)"], [/^Osoby \((\d+)\)$/, "People ($1)"], [/^Różnice \((\d+)\)$/, "Differences ($1)"],
  [/^zmigrowanych · (\d+) jeszcze nie$/, "migrated · $1 not yet"],
  [/^różnic statusu · (\d+) zgodnych$/, "status differences · $1 consistent"],
  [/^różnic przypisania · (\d+) zgodnych$/, "assignee differences · $1 consistent"],
  [/^Pominięte: (\d+) prowadzonych w Jirze \(„migriert zu Jira”\), (\d+) z sekcji „pomiń”, (\d+) bez mapowania statusu, (\d+) usuniętych w Jirze\. Nieprzypisanych w Asanie: (\d+)(.*)$/,
    (m, a, b, c, d, e, rest) => `Skipped: ${a} managed in Jira ("migriert zu Jira"), ${b} from "skip" sections, ${c} without a status mapping, ${d} deleted in Jira. Unassigned in Asana: ${e}` + rest
      .replace(/ \((\d+) z nich ma w Jirze domyślnie leada projektu( – [^,]+)?, liczone jako zgodne\)\./, " ($1 of them have the project lead in Jira by default$2, counted as consistent).")
      .replace(/ Zadania osób „nie w Jirze”: (\d+) \(nie liczone jako różnice\)\./, " Tasks of \"not in Jira\" people: $1 (not counted as differences).")
      .replace(/ Bez przypisanego, bo osoby nie da się przypisać: (\d+) \(zgodne\)\./, " Unassigned because the person cannot be assigned: $1 (consistent).")
      .replace(/ Osoby nie da się przypisać, w Jirze zostaje obecny przypisany: (\d+) \(zaakceptowane\)\./, " The person cannot be assigned, the current Jira assignee stays: $1 (accepted).")],
  [/^Ukończone w Asanie → „(.+)” \((.+)\)$/, (m, s, src) => `Completed in Asana → "${s}" (${({ projekt: "project", "domyślny": "default", "główna": "main" })[src] || src.replace("szablon", "profile")})`],
  [/^·? ?mapa projektu korzysta z szablonu „(.+)”$/, "· the project map uses profile \"$1\""],
  [/^szablon:? (.+)$/, (m, x) => (m.includes(":") ? "profile: " : "profile ") + x],
  [/^etykiety: (.+)$/, "labels: $1"], [/^komponenty: (.+)$/, "components: $1"], [/^rozwiązanie: (.+)$/, "resolution: $1"],
  [/^W schemacie „(.+)” \(id (\d+)\) przypisanie dają:\s*$/, "In scheme \"$1\" (id $2) assignment is granted by:"],
  [/^Zapisano w section_status_map\.(\S+)\.json\. Sprawdź ponownie, żeby zobaczyć efekt\.$/, "Saved in section_status_map.$1.json. Check again to see the effect."],
  [/^(.+) ma wpis w user_map\.json \((.+)\)\. Oznaczenie usunie ten wpis - synchronizacja przestanie przypisywać tej osobie zadania\. Kontynuować\?$/, "$1 has an entry in user_map.json ($2). Marking removes it - the sync will stop assigning tasks to this person. Continue?"],
  [/^(.+): nie w Jirze( \(usunięto z user_map\.json\))?\. Sprawdź ponownie, żeby przeliczyć różnice\.$/, (m, n, rm) => `${n}: not in Jira${rm ? " (removed from user_map.json)" : ""}. Check again to recount the differences.`],
  [/^(.+): zdjęto oznaczenie\.$/, "$1: mark removed."],
  [/^(\d+) osób z Asany nie zostanie poprawnie przypisanych\.$/, "$1 Asana people will not be assigned correctly."],
  [/^(\d+) sekcji mapowanych na status, którego nie ma w projekcie Jiry\.$/, "$1 sections are mapped to a status that does not exist in the Jira project."],
  [/^(\d+) sekcji z zadaniami bez wpisu w mapie \(zadania zostaną w statusie startowym\)\.$/, "$1 sections with tasks have no map entry (the tasks will stay in the initial status)."],
  [/^Status dla ukończonych „(.+)” nie istnieje w projekcie\.$/, "The status for completed tasks \"$1\" does not exist in the project."],
  [/^\(nieprzypisany\) — (.+?): (.+)$/, (m, n, p) => `(unassigned) — ${n}: ${EN[p] || p}`],
  [/^(.+) — (.+)$/, (m, a, b) => `${a} — ${EN[b] || b}`],
  [/^\(niezmapowany\) (.+)$/, "(unmapped) $1"],
  [/^\(nieprzypisany\)$/, "(unassigned)"],
  [/^Statusy z projektu (\S+) \((\d+)\), sekcje z (\d+) projektów \((\d+) różnych nazw\)\. Wczytano (.+)\.$/, "Statuses from project $1 ($2), sections from $3 projects ($4 distinct names). Loaded $5."],
  [/^ Błędy w (\d+) projektach\.$/, " Errors in $1 projects."],
  [/^od obecnej mapy \((\d+) wpisów\)$/, "from the current map ($1 entries)"],
  [/^od obecnego user_map\.json \((\d+)\)$/, "from the current user_map.json ($1)"],
  [/^(\d+) zmapowanych$/, "$1 mapped"], [/^(\d+) pomijanych$/, "$1 skipped"], [/^(\d+) bez mapowania$/, "$1 without mapping"],
  [/^(\d+) ze statusem spoza wzorca$/, "$1 with a status outside the template"], [/^(\d+) nie w Jirze$/, "$1 not in Jira"], [/^(\d+) do decyzji$/, "$1 to decide"],
  [/^z (\d+) sekcji \(min\. (\d+) projektów\)$/, "of $1 sections (min. $2 projects)"],
  [/^Pokazano (\d+) z (\d+) - zawęź filtr albo podnieś próg projektów\.$/, "Showing $1 of $2 - narrow the filter or raise the project threshold."],
  [/^Pokazano (\d+) z (\d+) - zawęź filtr\.$/, "Showing $1 of $2 - narrow the filter."],
  [/^Pokazano (\d+) z (\d+) wierszy\.$/, "Showing $1 of $2 rows."],
  [/^Przyjęto (\d+) propozycji\.$/, "Accepted $1 suggestions."],
  [/^Zapisano: (\d+) wpisów \(\+(\d+), −(\d+), zmienionych (\d+)\)\.$/, "Saved: $1 entries (+$2, −$3, $4 changed)."],
  [/^Zapisano: user_map\.json (\d+) wpisów \(\+(\d+), −(\d+), zmienionych (\d+)\), nie w Jirze: (\d+)\.$/, "Saved: user_map.json $1 entries (+$2, −$3, $4 changed), not in Jira: $5."],
  [/^Zapisać (\d+) wpisów do section_status_map\.json\?([\s\S]*)$/, (m, n, rest) => `Save ${n} entries to section_status_map.json?` + (rest ? (EN[rest] || rest) : "")],
  [/^Zapisać (\d+) mapowań do user_map\.json i (\d+) osób „nie w Jirze”\?([\s\S]*)$/, (m, a, b, rest) => `Save ${a} mappings to user_map.json and ${b} "not in Jira" people?` + (rest ? (EN[rest] || rest) : "")],
  [/^(\d+) wpisów wskazuje status, którego nie ma w projekcie (\S+) \(np\. „(.+)”\)\. Synchronizacja zostawi takie zadania w statusie startowym\. Zapisać mimo to\?$/, "$1 entries point to a status that does not exist in project $2 (e.g. \"$3\"). The sync will leave those tasks in the initial status. Save anyway?"],
  [/^Oznaczyć (\d+) widocznych, niezmapowanych osób jako „nie w Jirze”\?$/, "Mark $1 visible, unmapped people as \"not in Jira\"?"],
  [/^pokaż gości Asany \((\d+)\)$/, "show Asana guests ($1)"],
  [/^· (\d+) kont w grupach Jiry, nieużytych: (\d+)$/, "· $1 accounts in Jira groups, unused: $2"],
  [/^Ostatnio wczytano (.+)$/, "Last loaded $1"], [/^Wybrane: (.+)$/, "Selected: $1"],
  [/^to samo konto także: (.+)$/, "same account also: $1"],
  [/^✓ (.+)$/, (m, r) => `✓ ${EN[r] || r}`],
  [/^Dopasowanie: (.+)$/, (m, r) => `Match: ${EN[r] || r}`],
  [/^⚠ (.+) - nie ma tego statusu we wzorcu$/, "⚠ $1 - this status is not in the template"],
  [/^(.+) \(nie jest „zakończony”\)$/, "$1 (not \"done\")"],
  [/^„(.+)” zapisze zmiany( \(także przez przeglądarkę\))?\. Upewnij się, że wcześniej sprawdziłeś podgląd\.$/, (m, t, b) => `"${EN[t] || t}" will write changes${b ? " (also through the browser)" : ""}. Make sure you have checked the preview first.`],
  [/^ustawione \((.+)\) - wpisz, aby zmienić$/, "set ($1) - type to change"],
  [/^(.+): (\d+) zadań w (\d+) sekcjach\.$/, "$1: $2 tasks in $3 sections."],
  [/^Statusy projektu (\S+): (\d+)\.$/, "Statuses of project $1: $2."],
  [/^(\d+) w mapach projektów$/, "$1 in project maps"],
  [/^w mapach projektów: (\d+) z (\d+): $/, "in project maps: $1 of $2: "],
  [/^projekt wzorcowy \((\S+)\)$/, "template project ($1)"],
  [/^(\d+) sekcji z zadaniami bez mapowania - ich zadania zostaną w statusie startowym workflow\.$/, "$1 sections with tasks have no mapping - their tasks will stay in the workflow's initial status."],
  [/^Podaj (.+)\.$/, (m, w) => `Enter ${EN[w] || w}.`],
  [/^Wybierz rolę dla: (.+)$/, "Choose a role for: $1"],
  [/^Zapisano (.+)$/, "Saved $1"],
  [/^Zapisano: (.+)$/, "Saved: $1"],
  [/^Wybrane: $/, "Selected: "],
  [/^(\d+) wierszy bez poprawnego linku do Asany zostanie pominiętych\.$/, "$1 rows without a valid Asana link will be skipped."],
  [/^Zalogowano jako (.+)\.$/, "Logged in as $1."],
  [/^Projekt wzorcowy (\S+): (.+) ✓$/, "Template project $1: $2 ✓"],
  [/^Projekt wzorcowy (\S+) nie istnieje albo konto nie ma do niego dostępu \((\d+)\)\.$/, "Template project $1 does not exist or the account has no access to it ($2)."],
  [/^Tablica wzorcowa: (.+) ✓$/, "Template board: $1 ✓"],
  [/^Projekt wzorcowy ma (\d+) tablice - wskaż wzorzec kolumn w JIRA_TEMPLATE_BOARD_ID: (.+)$/, "The template project has $1 boards - set the column template in JIRA_TEMPLATE_BOARD_ID: $2"],
  [/^JIRA_TEMPLATE_BOARD_ID=(\S+) nie jest tablicą projektu (\S+) \(tablice: (.*)\)\.$/, "JIRA_TEMPLATE_BOARD_ID=$1 is not a board of project $2 (boards: $3)."],
  [/^Brak JIRA_TEMPLATE_PROJECT_KEY - nowe projekty dostaną domyślną konfigurację Jiry, a panel nie ma skąd wziąć listy statusów do mapowania sekcji\.$/, "No JIRA_TEMPLATE_PROJECT_KEY - new projects get Jira's default configuration, and the panel has nowhere to take the statuses for section mapping from."],
  [/^Pola wymagane przy tworzeniu zadania, których synchronizacja nie uzupełni sama: (.+)\. Ustaw DEFAULT_TYPE_FIELD_ID\/VALUE albo zdejmij wymagalność\.$/, "Fields required on issue creation that the sync cannot fill in: $1. Set DEFAULT_TYPE_FIELD_ID/VALUE or make them optional."],
  [/^Token należy do (.+)\.$/, "The token belongs to $1."],
  [/^Workspace: (.+) ✓$/, "Workspace: $1 ✓"],
  [/^Wybierz workspace \(ASANA_WORKSPACE_GID\): (.+)$/, "Choose a workspace (ASANA_WORKSPACE_GID): $1"],
  [/^ASANA_WORKSPACE_GID=(\S+) nie jest workspace'em tego tokenu\.$/, "ASANA_WORKSPACE_GID=$1 is not a workspace of this token."],
  [/^Konto serwisowe: (.+)$/, "Service account: $1"],
  [/^Arkusz: (.+) ✓$/, "Sheet: $1 ✓"],
  [/^Wierszy: (\d+), z linkiem do Asany: (\d+)\.$/, "Rows: $1, with an Asana link: $2."],
  [/^Brak dostępu do arkusza - udostępnij go adresowi (.+)\.$/, "No access to the sheet - share it with $1."],
  [/^Brak pliku klucza: (.+)\. Wklej klucz JSON w panelu\.$/, "Key file missing: $1. Paste the JSON key in the panel."],
  [/^Zakładka „(.+)” z GOOGLE_SHEET_RANGE nie istnieje\. Dostępne: (.+)\.$/, "Tab \"$1\" from GOOGLE_SHEET_RANGE does not exist. Available: $2."],
  [/^Kolumny ignorowane przez skrypty: (.+)\.$/, "Columns ignored by the scripts: $1."],
  [/^Brak kolumn (.+) - skrypt poradzi sobie bez nich, ale warto je mieć (.+)$/, "Missing columns $1 - the script copes without them, but they are worth having $2"],
  [/^Brak połączenia z (.+): (.+)$/, "No connection to $1: $2"],
  [/^Sesja sprzed (.+)$/, "Session from $1"],
  [/^(\d+) osób jest jednocześnie zmapowanych i oznaczonych „nie w Jirze”\.$/, "$1 people are both mapped and marked \"not in Jira\"."],
  [/^To nie jest poprawny JSON: (.+)$/, "This is not valid JSON: $1"],
  [/^Pole „(.+)” jest wymagane\.$/, (m, f) => `Field "${EN[f] || f}" is required.`],
  [/^Pole „(.+)” musi być liczbą\.$/, (m, f) => `Field "${EN[f] || f}" must be a number.`],
  [/^Nie udało się pobrać (grup|ról Jiry): (.+)$/, (m, w, e) => `Could not fetch ${w === "grup" ? "groups" : "Jira roles"}: ${e}`],
];

function tr(text) {
  if (LANG !== "en" || !text || typeof text !== "string") return text;
  const m = text.match(/^(\s*)([\s\S]*?)(\s*)$/);
  const core = m[2];
  if (!core) return text;
  if (EN[text] !== undefined) return EN[text];          // klucze z własnymi spacjami/nowymi liniami
  if (EN[core] !== undefined) return m[1] + EN[core] + m[3];
  for (const [re, rep] of EN_PATTERNS) {
    if (re.test(core)) return m[1] + core.replace(re, rep) + m[3];
  }
  if (core.includes(" · ")) {  // np. „etykiety: x · ważniejsza niż odhaczenie”
    const parts = core.split(" · ").map(tr);
    if (parts.join(" · ") !== core) return m[1] + parts.join(" · ") + m[3];
  }
  const par = core.match(/^\((.+)\)$/);
  if (par && tr(par[1]) !== par[1]) return m[1] + "(" + tr(par[1]) + ")" + m[3];
  return text;
}

// --- tłumaczenie drzewa DOM w trakcie wyświetlania
const NO_TR = ".log, .cmd, .mono, pre, code, textarea, [data-no-tr]";
function trNode(node) {
  if (LANG !== "en") return;
  if (node.nodeType === Node.TEXT_NODE) {
    const p = node.parentElement;
    if (!p || p.closest(NO_TR)) return;
    const t = tr(node.nodeValue);
    if (t !== node.nodeValue) node.nodeValue = t;
    return;
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return;
  if (node.parentElement && node.parentElement.closest(NO_TR)) return;
  for (const attr of ["placeholder", "title"]) {
    if (node.hasAttribute && node.hasAttribute(attr)) {
      const v = node.getAttribute(attr), t = tr(v);
      if (t !== v) node.setAttribute(attr, t);
    }
  }
  if (node.tagName === "OPTGROUP") { const t = tr(node.label); if (t !== node.label) node.label = t; }
  if (node.matches(NO_TR)) return;  // atrybuty tak, zawartość nie (pola edycji, logi, wartości techniczne)
  for (const child of [...node.childNodes]) trNode(child);
}

if (LANG === "en") {
  document.documentElement.lang = "en";
  const origConfirm = window.confirm.bind(window);
  window.confirm = msg => origConfirm(tr(msg));
  const obs = new MutationObserver(muts => {
    for (const m of muts) {
      if (m.type === "characterData") trNode(m.target);
      else m.addedNodes.forEach(trNode);
    }
  });
  const start = () => { trNode(document.body); document.title = tr(document.title); obs.observe(document.body, { childList: true, subtree: true, characterData: true }); };
  if (document.body) start(); else document.addEventListener("DOMContentLoaded", start);
}

// --- przełącznik języka w panelu bocznym
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-lang]").forEach(b => {
    b.classList.toggle("active", b.dataset.lang === LANG);
    b.addEventListener("click", () => {
      try { localStorage.setItem("lang", b.dataset.lang); } catch (e) { /* brak */ }
      location.reload();
    });
  });
});
