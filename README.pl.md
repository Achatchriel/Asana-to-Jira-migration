# Migracja i synchronizacja: Asana → Jira (sterowana arkuszem Google)

[English](README.md) · **Polski**

Pakiet narzędzi do przeniesienia wybranych projektów z Asany do Jiry i utrzymania
ich w synchronizacji. **Pracuje się w nim przez panel webowy** (`migration_app/`):
lokalną aplikację w przeglądarce, z której uruchamiasz wszystkie kroki migracji,
sprawdzasz statusy i osoby, edytujesz konfigurację i przeglądasz raporty. Pod
spodem panel wywołuje zwykłe skrypty Pythona, opisane szczegółowo w części II.

**Arkusz Google jest źródłem prawdy** o tym, które projekty mają powstać w Jirze:
narzędzia tworzą i synchronizują tylko projekty z wierszy arkusza.

- **Część I — panel:** instalacja jednym poleceniem, zakładka Start (nowa migracja
  krok po kroku), mapowanie osób i statusów, widoki panelu i typowa praca.
- **Część II — skrypty:** dokumentacja każdego narzędzia, opcje, pliki stanu,
  procedury naprawcze i rozwiązywanie problemów.

---

# Część I. Panel

## Szybki start

**Potrzebujesz:** Linuksa albo macOS z Pythonem 3.10+, konta **administratora Jiry**
(Cloud) z [tokenem API](https://id.atlassian.com/manage-profile/security/api-tokens),
[tokenu Asany](https://app.asana.com/0/my-apps), **konta serwisowego Google** (klucz
JSON) i **arkusza Google** z listą projektów do migracji (sekcja 7 w części II -
wystarczy kolumna `asana_project_link`, zalecane także `project_name` i `jira_key`).

```bash
git clone <adres repozytorium> jiraSync
cd jiraSync
./start.sh                 # http://127.0.0.1:8765
```

`start.sh` przy pierwszym uruchomieniu tworzy `.venv`, instaluje zależności
(`requirements.txt`, ponownie tylko po jego zmianie) i przeglądarkę dla Playwright
(ok. 300 MB, raz na komputer), a potem uruchamia panel i otwiera go w przeglądarce.
Opcje: `./start.sh --port 9000` (inny port), `NO_BROWSER=1 ./start.sh` (bez otwierania
przeglądarki), `PYTHON=python3.12 ./start.sh` (inny interpreter). Serwer nasłuchuje
wyłącznie na `127.0.0.1`. Zatrzymanie: `Ctrl+C`.

Panel działa od pustego folderu: `.env` powstaje z `.env.example` przy pierwszym
zapisie konfiguracji, a mapy i listy - z szablonów. Dalej prowadzi zakładka **Start**.

## Zakładka Start: nowa migracja krok po kroku

Każdy krok ma stan wykrywany automatycznie (z plików i wyników testów), więc widać,
co jest gotowe. Kolejność ma znaczenie - każdy krok korzysta z poprzednich.

| # | Krok | Co robi | Wynik |
|---|---|---|---|
| 1 | **Konfiguracja** | formularz najważniejszych zmiennych; wklejony klucz konta serwisowego Google; panel pokazuje adres, któremu trzeba udostępnić arkusz | `.env`, `service_account.json` |
| 2 | **Test połączeń** | konto Jiry i uprawnienie administratora, projekt i tablica wzorcowa, pola wymagane przy tworzeniu zadań, których skrypt nie uzupełni sam, token i workspace Asany (lista do wyboru), dostęp do arkusza, zakładka, nagłówki, wiersze bez linku do Asany | `migration_app/cache/setup_check.json` |
| 3 | **Arkusz projektów** | wczytanie listy projektów (przy wierszach bez nazwy - kilka minut, bo nazwy pobierane są z Asany) | `migration_app/cache/sheet.json` |
| 4 | **Mapowanie osób** | osoby z Asany ↔ konta z wybranych grup Jiry, z propozycjami; osoby bez konta oznaczasz „nie w Jirze” | `user_map.json`, `asana_users_not_in_jira.json` |
| 5 | **Mapowanie statusów** | sekcje Asany ze wszystkich projektów ↔ **statusy projektu wzorcowego**; status dla zadań ukończonych w Asanie | `section_status_map.json` |
| 6 | **Sesja przeglądarki** | jednorazowe logowanie do Jiry (SSO/2FA) dla kroków kolumn i statusów tablic; po zalogowaniu wysyłasz Enter w polu odpowiedzi uruchomienia | `auth_state.json` |
| 7 | **Podgląd** | cały pipeline w trybie dry-run - nic nie zapisuje | log w Uruchomieniach |
| 8 | **Migracja** | pełny pipeline; panel pokazuje polecenie i prosi o potwierdzenie | projekty i zadania w Jirze |
| 9 | **Weryfikacja** | zgodność statusów i przypisań, porównanie liczby zadań z Asaną | `migration_app/checks/` |

Krok 2 da się uruchomić też z terminala: `.venv/bin/python migration_app/setup_check.py`.
Zacznij od zakładki arkusza z 1-2 projektami testowymi (`GOOGLE_SHEET_RANGE`), przejdź
całość, obejrzyj wynik w Jirze i dopiero potem przełącz na pełną listę.

## Mapowanie osób i statusów

**Mapowanie osób** wczytuje członków workspace'u Asany i członków **wybranych grup
Jiry**. Propozycje dopasowań: ten sam e-mail, ta sama część przed `@` w innej domenie,
to samo imię i nazwisko, wzorzec `imie.nazwisko@JIRA_EMAIL_DOMAIN`. Każdą osobę możesz
dopasować ręcznie (podpowiedzi po imieniu lub e-mailu) albo oznaczyć **„nie w Jirze”**
- ich zadania powstają bez przypisanego i nie są liczone jako różnice. Start od obecnego
`user_map.json` albo od pustej mapy; goście Asany są domyślnie ukryci; panel ostrzega,
gdy dwie osoby z Asany wskazują to samo konto. Pamiętaj: przypisać zadanie można tylko
osobie, której rola w projekcie ma uprawnienie **„Assignable User”** w schemacie
uprawnień - samo mapowanie (ani bycie leadem projektu) tego nie daje.

**Mapowanie statusów** zbiera nazwy sekcji ze wszystkich projektów arkusza (z liczbą
projektów i zadań) oraz statusy projektu wzorcowego (`JIRA_TEMPLATE_PROJECT_KEY`, albo
innego wskazanego projektu). Do wyboru są **wyłącznie statusy, które istnieją we
wzorcu**, pogrupowane na „do zrobienia”, „w toku” i „zakończone”. Propozycje: ta sama
nazwa, a potem znaczenie sekcji (np. Fertig/Done/Abgeschlossen → status „zakończony”
wzorca, In Arbeit → „w toku”, Backlog/To Do → „do zrobienia”). Sekcję można oznaczyć
„pomiń” (zadania z poddrzewem nie są migrowane); sekcja bez mapowania zostaje w statusie
startowym workflow. **Status dla zadań ukończonych w Asanie wybierasz jawnie** (wpis
`__ukonczone__`) - bez niego skrypt użyłby `Fertig`, którego w Twojej Jirze może nie być.
Wpisy wskazujące status spoza wzorca są oznaczane, a krok 5 jest gotowy dopiero bez nich.

Mapy pojedynczych projektów (`section_status_map.<KLUCZ>.json`) edytujesz w widoku
**Statusy i osoby** albo w **Konfiguracji**.

## Kopia projektu

Osobna karta do skopiowania istniejącego projektu (company-managed) jako nowego.
Działa niezależnie od migracji: skrypt `clone_project.py` nie importuje innych
skryptów, nie korzysta z projektu wzorcowego i z `.env` czyta tylko dostęp do Jiry -
**wszystko bierze z projektu źródłowego**.

- **Schematy:** każdy schemat, którego używa źródło, dostaje kopię z nazwą opartą na
  nowym projekcie - od klucza albo od nazwy (wybór w karcie, z podglądem nazw), np.
  `XYZ Permission Scheme`, `XYZ Notification Scheme`, `XYZ Issue Security Scheme`,
  `XYZ Workflow Scheme`, `XYZ Issue Type Scheme`, `XYZ Issue Type Screen Scheme`,
  `XYZ Field Scheme` (w instancjach sprzed migracji na Field Schemes: `XYZ Field
  Configuration Scheme`), `XYZ Priority Scheme`. Uprawnień, których Jira nie przyjmuje
  przy tworzeniu schematu (np. Product Discovery, `COPY_PROJECT`), nie da się skopiować -
  log je wymienia. Schemat o takiej nazwie, który
  już istnieje (np. po przerwanym uruchomieniu), jest używany ponownie. Same workflow,
  ekrany i konfiguracje pól pozostają wspólne ze źródłem (wskazują na nie nowe schematy).
- **Projekt:** typ, lead (albo podany), opis, URL, kategoria i domyślne przypisanie jak
  w źródle; projekt powstaje od razu z kopiami schematów.
- **Role i dostęp:** karta wczytuje role źródła z osobami i grupami (wstępnie
  zaznaczonymi). Możesz je odznaczyć, dodać osoby lub grupy z wyszukiwarki i dodać
  inne role; do nowego projektu trafia dokładnie ten wybór (`--roles-file`). Z terminala
  bez pliku role i członkowie są jak w źródle, `--no-roles` - nikogo.
- **Tablice:** filtr z podmienionym projektem i tymi samymi udostępnieniami, tablica,
  kolumny i **przypisanie statusów do kolumn** jak w źródle (przez API GreenHopper,
  tokenem, bez przeglądarki). Workflow są wspólne ze źródłem, więc statusy mają te same
  ID. Po zapisie skrypt odczytuje nową tablicę i porównuje każdą kolumnę z jej statusami
  ze źródłem (w logu lista kolumn i statusów albo ostrzeżenie o różnicy).
- **Dodatkowo (każde można wyłączyć):** komponenty, wersje, konteksty pól
  niestandardowych ograniczone do źródła.
- **Nie kopiuje:** zgłoszeń, torów (swimlanes), szybkich filtrów i wyglądu kart tablic.

Zacznij od **Podglądu (dry-run)** - pokazuje schematy źródła, nazwy kopii, role,
komponenty i tablice. „Utwórz kopię” prosi o potwierdzenie. Po przerwaniu uruchom
ponownie z „projekt docelowy już istnieje”. Z terminala:
```bash
.venv/bin/python clone_project.py --source ABC --target-key XYZ --target-name "Nowy projekt" --dry-run
.venv/bin/python clone_project.py --source ABC --target-key XYZ --target-name "Nowy projekt" --name-base name
```
Starszy `copy_project.py` (sekcja 4a) podpina do nowego projektu **te same** schematy co
źródło i konfiguruje kolumny przez przeglądarkę.

## Widoki panelu

| Widok | Do czego służy |
|---|---|
| **Start** | kroki nowej migracji (wyżej) |
| **Przegląd** | stan całości na jednym ekranie: braki w `.env`, wiek sesji przeglądarki (potrzebnej w krokach kolumn i statusów), stan synchronizacji, projekty chronione i wyłączone, ostatnie uruchomienia |
| **Projekty** | projekty z arkusza (bez wczytanego arkusza: z pliku stanu i `project_key_cache.json`), liczba wpisów w stanie, mapa sekcji, flagi, wynik ostatniego sprawdzenia. **Zaznaczone projekty trafiają automatycznie do pól „Projekty” w narzędziach** |
| **Narzędzia** | 69 skryptów w siedmiu grupach (tabela niżej), każdy z formularzem opcji. Panel pokazuje dokładne polecenie przed startem |
| **Statusy i osoby** | sprawdzenie bez zapisu, per projekt: sekcje Asany → statusy Jiry (te same reguły co synchronizacja: mapa projektu, szablon, mapa główna, `__ukonczone__`), osoby → konta Jiry (czy są w `user_map.json`, czy aktywne, czy można je przypisać - przy braku uprawnienia panel pokazuje schemat uprawnień i role, które je dają) oraz różnice statusu i przypisanego w już zmigrowanych zadaniach. Brakujące mapowanie sekcji zapiszesz od razu do `section_status_map.<KLUCZ>.json`, osobę oznaczysz „nie w Jirze” |
| **Mapowanie osób** / **Mapowanie statusów** | jak wyżej |
| **Kopia projektu** | nowy projekt z konfiguracją istniejącego, z własnymi kopiami schematów (niżej) |
| **Uruchomienia** | historia z pełnymi logami na żywo; pole odpowiedzi dla skryptów, które pytają o potwierdzenie; przerwanie całego uruchomienia (z procesami potomnymi) |
| **Konfiguracja** | edycja `.env` (sekrety zamaskowane, puste pole = bez zmian), map statusów, szablonów, `user_map.json`, `asana_users_not_in_jira.json`, `sync_exclude.txt`, `protected_projects.txt` i schematów YAML; pliki, których jeszcze nie ma, powstają przy pierwszym zapisie |
| **Raporty** | `logs/`, `pipeline_summaries/`, `repair_reports/` i raporty CSV/JSON (CSV wyświetlany jako tabela) |

## Grupy narzędzi

| Grupa w panelu | Co zawiera | Opis w części II |
|---|---|---|
| **Pipeline migracji** | wczytanie arkusza, cały pipeline, tworzenie projektów, kolumny i statusy tablic, kandydaci na Epiki, synchronizacja w obie strony, zapis sesji przeglądarki, pliki projektu z Asany | sekcje 8, 11-12 |
| **Asana: statusy i przypisania** | sprawdzenie statusów i osób, źródła mapowania statusów, sekcje projektów, członkowie Asany, mapa użytkowników, token | sekcja 11 („Konfiguracja przed pierwszym uruchomieniem”) |
| **Weryfikacja** | liczenie i porównanie z Asaną, duplikaty, audyty hierarchii, diagnostyka | sekcja 14 |
| **Naprawy** | odtwarzanie powiązań, usuwanie duplikatów, „migriert zu Jira”, fałszywe Epiki, flagi stanu, naprawy partiami | sekcja 15 |
| **Konfiguracja Jiry** | schemat z YAML, układ i aktualizacja workflow, ekrany, field schemes, grupy na przejściach | sekcja 17 |
| **Kopia projektów** | kopia projektu w tej samej instancji i z innej instancji Jiry | sekcja 4a |
| **Inne** | Epiki i Story z arkusza zakresu i eksport z powrotem, zadania „Analysis” dla Story, dezaktywacja zawieszonych kont Atlassian | sekcje 20 i 22 |

## Jak panel chroni dane

- **Podgląd domyślnie.** Narzędzia, które mają tryb podglądu, startują z
  zaznaczonym `--dry-run`; zapis wymaga jego odznaczenia albo zaznaczenia `--apply`.
- **Potwierdzenie przed zapisem.** Każde uruchomienie, które zmieni dane w Jirze,
  Asanie albo w plikach, panel oznacza i wymaga potwierdzenia.
- **Kopie plików.** Przed każdym zapisem z panelu (konfiguracja, mapowania, `.env`)
  panel kopiuje plik do `migration_app/backups/`.
- **Sekrety poza repo.** `.env`, `service_account.json` i `auth_state.json` są w
  `.gitignore`; panel nie pokazuje wartości tokenów.
- **Te same zabezpieczenia co skrypty:** `protected_projects.txt`,
  `sync_exclude.txt`, blokady projektów i atomowy zapis stanu działają tak samo
  jak przy uruchomieniu z terminala (sekcja 11).

## Typowa praca w panelu

**Nowa migracja:** zakładka **Start**, kroki 1-9. Przed masowym tworzeniem zgłoszeń
poproś administratora Jiry o wyłączenie powiadomień i automatyzacji dla nowych
projektów (Jira wysyła maile przy każdym zgłoszeniu tworzonym przez API).

**Kolejne projekty w trwającej migracji:** **Projekty** → zaznacz projekty →
**Statusy i osoby** (brakujące sekcje i osoby) → **Narzędzia → Pipeline** z podglądem,
przejrzyj log w **Uruchomieniach**, potem ten sam pipeline bez podglądu → weryfikacja.

**Rutynowa synchronizacja:** Narzędzia → *Pipeline* → kandydaci na Epiki, potem
synchronizacja Asana → Jira (opcjonalnie Jira → Asana, nigdy równolegle z
pierwszą). Podsumowanie każdego kroku jest w logu i, jeśli skonfigurowany, na
Slacku. Jak reagować na podsumowanie: sekcja 18.

**Naprawy i kontrola:** grupy *Weryfikacja* i *Naprawy* — zawsze najpierw raport,
potem zapis. Kolejność kroków przy naprawie projektów zsynchronizowanych starszą
wersją opisują sekcje 15i i 18.

---

# Część II. Skrypty — dokumentacja szczegółowa

Każde narzędzie panelu to zwykły skrypt w głównym folderze. Formularz panelu
odpowiada jego opcjom wiersza poleceń, więc wszystko poniżej da się uruchomić
także z terminala (`.venv/bin/python <skrypt> ...` albo po `source .venv/bin/activate`).

## Przegląd pakietu

Pakiet skryptów, który przenosi wybrane projekty z Asany do Jiry i utrzymuje je
w synchronizacji. **Arkusz Google jest źródłem prawdy** o tym, które projekty
mają powstać w Jirze: skrypty tworzą projekty tylko dla wierszy z arkusza.

Co robi pakiet, w kolejności kroków:

1. tworzy projekty w Jirze (`jira_sync.py`),
2. konfiguruje kolumny tablic i przypisuje do nich statusy (`browser_automation/`),
3. wykrywa zadania wymagające hierarchii Epic → Task → Sub-task (`detect_epic_candidates.py`),
4. synchronizuje zadania, podzadania, komentarze i załączniki (`asana_jira_sync.py`),
5. opcjonalnie przenosi zmiany z powrotem z Jiry do Asany (`jira_asana_sync.py`).

Kroki 1-4 uruchamia jednym poleceniem orkiestrator `run_full_pipeline.py`
(sekcja 11a), także równolegle i ze zbiorczym podsumowaniem na Slacku.

**Arkusz zawiera tylko projekty, które mają być teraz synchronizowane.** Projekty
już zmigrowane i zarchiwizowane w Jirze są z niego usuwane, więc żadne narzędzie
działające na arkuszu ich nie dotyka. Całą procedurę pracy opisuje sekcja 18.

Dodatkowo pakiet zawiera:

- **narzędzia kontroli i naprawy** projektów zsynchronizowanych starszymi wersjami:
  porównanie z Asaną (`compare_project_totals.py`), audyt hierarchii
  (`audit_hierarchy.py`), odtwarzanie zgubionych powiązań (`relink_state.py`),
  usuwanie duplikatów (`resolve_relink_duplicates.py`), zadania przeniesione
  ręcznie do Jiry (`handle_migrated_to_jira.py`) i sterownik dla wielu projektów
  (`repair_batch.py`), sekcje 14-15,
- **kopiowanie projektów z innej instancji Jiry**, także z podziałem na kilka
  projektów i dociąganiem nowości (`copy_project_cross_instance.py`, sekcja 4a),
- **mechanizmy ochronne**: `sync_exclude.txt` (czasowe wyłączenie synchronizacji)
  i `protected_projects.txt` (trwałe wyłączenie ze wszystkiego), sekcja 11,
- **konfigurację Jiry z pliku YAML** (`build_jira_schema.py`, sekcja 17) oraz
  **Epiki i Story z arkusza zakresu** (`create_epics_stories_from_sheet.py`,
  sekcja 20); oba narzędzia są niezależne od migracji z Asany,
- **panel webowy** `migration_app/` (część I; pliki panelu: sekcja 21).

Każdy wiersz arkusza musi mieć `asana_project_link`. Wiersz bez linku (albo z
linkiem, do którego token nie ma dostępu) jest **pomijany w całości**: projekt
nie powstaje w Jirze. Dopasowanie po nazwie zostało celowo wyłączone (sekcja 7).

**Spis treści części II:** 1-3 instalacja i konfiguracja · 4-4a projekt wzorcowy i kopiowanie
projektów · 5-6 role · 7 format arkusza · 8-10 tworzenie projektów · 11 synchronizacja
zadań (w tym hierarchia Epików) · 11a cały pipeline i równoległość · 11b statusy
w kolumnach · 12 kierunek zwrotny · 13 logi i Slack · 14 weryfikacja i sprzątanie ·
15 naprawa hierarchii, zgubionych powiązań i duplikatów (15f-15i) · 16 pliki stanu ·
17 konfiguracja Jiry z YAML (pola, statusy, workflow, ekrany, schematy) ·
18 procedura pracy (naprawa, rutynowo, okresowo) · 19 rozwiązywanie problemów
(19a synchronizacja i naprawy, 19b konfiguracja Jiry w praktyce) ·
20 Epiki i Story z arkusza "Zakres MVP" · 21 pliki panelu ·
22 pozostałe skrypty pomocnicze

## 1. Instalacja

Najprościej `./start.sh` (część I, „Szybki start”). Ręcznie - panel i skrypty
oczekują środowiska właśnie w `.venv`:
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium   # kroki przeglądarkowe (kolumny i statusy tablic)
```

## 2. Konfiguracja

**W panelu:** zakładka Start, krok 1 - formularz zapisuje `.env` (tworzony z
`.env.example`), wklejony klucz konta serwisowego Google trafia do
`service_account.json`, a panel pokazuje adres, któremu trzeba udostępnić arkusz.
Krok 2 sprawdza wszystko naraz (z terminala: `.venv/bin/python migration_app/setup_check.py`).

**Ręcznie:**

1. Skopiuj `.env.example` do `.env`:
   ```bash
   cp .env.example .env
   ```
2. Uzupełnij wartości w `.env`:
   - **Jira**: adres instancji, e-mail, [token API](https://id.atlassian.com/manage-profile/security/api-tokens)
   - **Asana**: [Personal Access Token](https://app.asana.com/0/my-apps), GID workspace'u
   - **Google Sheets**: konto serwisowe (JSON key) z [Google Cloud Console](https://console.cloud.google.com/),
     ID arkusza i zakres (`GOOGLE_SHEET_RANGE`) — pamiętaj, żeby **udostępnić arkusz**
     adresowi e-mail konta serwisowego (Edytor, jeśli skrypty mają wpisywać klucze Jiry).
   - zalecane: **projekt wzorcowy** `JIRA_TEMPLATE_PROJECT_KEY` (sekcja 4) - z niego
     nowe projekty dostają workflow, ekrany i pola, a panel bierze listę statusów do
     mapowania sekcji; przy kilku tablicach wzorca także `JIRA_TEMPLATE_BOARD_ID`.

   `.env.example` opisuje każdą zmienną; wymagane są tylko sekcje Jira, Asana i Google.
   Pusta wartość (`X=`) to nie to samo co brak zmiennej - niektóre skrypty mają wartości
   domyślne tylko dla zmiennej nieobecnej, więc nieużywane opcje zostaw zakomentowane.

   Żeby szybko sprawdzić, czy `ASANA_TOKEN` faktycznie działa (niezależnie
   od dostępu do konkretnego projektu — przydatne przy błędach `403`):
   ```bash
   python check_asana_token.py
   ```

### Czego nie ma w repozytorium

Repozytorium zawiera wyłącznie kod i dokumentację. Pliki z danymi powstają
lokalnie przy pracy i są wykluczone w `.gitignore`: `.env`, klucz Google
(`service_account.json`), sesja przeglądarki (`auth_state.json`), pliki stanu i cache (`*.json`, sekcja 16), raporty `*.csv`,
arkusze `*.xlsx`, listy projektów `*.txt` (np. `sync_exclude.txt`,
`protected_projects.txt`), konfiguracje YAML konkretnej instancji oraz katalogi
`logs/`, `locks/`, `pipeline_summaries/`, `diagnostics/`, `repair_reports/`.
Klucze projektów, nazwy instancji i grup w przykładach (`PROJ`, `KLUCZ`,
`twoja-instancja.atlassian.net`, `KlientX`) są przykładowe — podstaw własne.

## 3. Typ tablicy

Domyślnie **wszystkie tworzone projekty są tablicami Kanban typu company-managed**
(klasyczny, zarządzany centralnie przez admina Jira — wymaga uprawnień administracyjnych
do tworzenia projektów tego typu). Jeśli wolisz wariant team-managed (prostszy,
konfigurowany samodzielnie przez zespół), w `.env.example` jest gotowa, zakomentowana
linia z odpowiednim kluczem szablonu.

## 4. Kopiowanie konfiguracji z projektu wzorcowego

Jeśli chcesz, żeby nowe projekty miały **taki sam workflow, layout ekranów i pola**
jak istniejący, wybrany projekt w Jira — ustaw w `.env`:

```
JIRA_TEMPLATE_PROJECT_KEY=TWOJKLUCZ
```

Skrypt wtedy dla każdego nowo tworzonego projektu skopiuje z projektu wzorcowego:

- **workflow** (schemat workflow),
- **layout** — schemat ekranów przypisanych do typów zadań (issue type screen scheme),
- **pola** — konfigurację pól (field configuration scheme),
- **typy zadań** — schemat typów zadań (issue type scheme),
- uprawnienia, powiadomienia i schemat bezpieczeństwa (jeśli projekt wzorcowy
  ma niestandardowe — w przeciwnym razie użyty zostanie domyślny Jira).

**Ograniczenia, o których warto wiedzieć:**
- Działa **tylko dla projektów company-managed (klasycznych)** — co i tak jest
  naszym domyślnym typem (patrz sekcja 3).
- Wymaga **uprawnień administratora Jira** (Administer Jira global permission) —
  bez nich Jira zwróci błąd 403 przy próbie przypisania workflow/layoutu/pól.
- Workflow, layout i pola są przypisywane w osobnym kroku, **zaraz po** utworzeniu
  pustego projektu — działa to tylko dopóki projekt nie ma żadnych zadań, co
  zawsze jest prawdą dla świeżo utworzonego projektu.
- Jeśli przypisanie któregoś ze schematów się nie powiedzie (np. brak
  uprawnień), skrypt **nie usuwa** już utworzonego projektu — zgłasza to jako
  ostrzeżenie w podsumowaniu, żebyś mógł to poprawić ręcznie w Jira.
- Tryb `--dry-run` nie testuje samego kopiowania konfiguracji (bo projekt
  jeszcze nie istnieje) — pokazuje tylko, że konfiguracja zostałaby skopiowana.

**Ważne ograniczenie: kolumny tablicy Kanban.** Jira **nie udostępnia publicznego
API do ustawiania kolumn tablicy** (board) — to jedyny element wizualnej konfiguracji,
którego nie da się skopiować automatycznie. Workflow, statusy, ekrany i pola będą
identyczne jak we wzorcu, ale nowa tablica dostanie domyślny układ kolumn Jiry
(zwykle jeden status = jedna kolumna). Żeby ułatwić ręczne odtworzenie układu ze
wzorca, uruchom:
```bash
python show_template_board_columns.py
```
Wypisze dokładny układ kolumn (nazwy + które statusy są w której kolumnie) z tablicy
wzorcowej — wystarczy przeciągnąć statusy w te same miejsca na nowej tablicy
(Board → Configure → Columns), co zajmuje dosłownie chwilę. Przy dużej liczbie
projektów zobacz też `browser_automation/` — automatyzację Playwright do tego kroku.

## 4a. Pełna kopia JEDNEGO projektu, WŁĄCZNIE z tablicami i kolumnami (`copy_project.py`)

> Do kopii projektu z **własnymi** schematami (nazwanymi od nowego projektu), bez
> przeglądarki i bez innych skryptów, służy `clone_project.py` - karta „Kopia projektu”
> w panelu (część I). Ten rozdział opisuje starszy `copy_project.py`, który podpina te
> same schematy co źródło.

Osobny skrypt do innego scenariusza niż główny `jira_sync.py` (masowe tworzenie
z arkusza) — kopiuje **jeden konkretny, istniejący** projekt Jiry w całości,
łącznie z tym, czego sekcja 4 wyżej nie potrafi zrobić automatycznie: **wszystkimi
tablicami** źródłowego projektu, ich **układem kolumn** i **przypisaniem statusów
do kolumn** (przez ten sam wewnętrzny mechanizm GraphQL/GreenHopper co
`run_column_assignments.py` — patrz sekcja 11b, wbudowany tu bezpośrednio, żeby
nie zależeć od dokładnej wersji tamtego pliku).

```bash
python copy_project.py --source ABC --target-key XYZ --target-name "Nowy Projekt" \
    --lead-account-id 5b10a2844c20165700ede21 --dry-run   # podgląd planu

python copy_project.py --source ABC --target-key XYZ --target-name "Nowy Projekt" \
    --lead-account-id 5b10a2844c20165700ede21             # faktyczne utworzenie
```

**Udostępnienia filtrów tablic.** Tablica jest widoczna tylko dla osób, które
widzą jej filtr. Nowy filtr dostaje te same udostępnienia i prawa edycji co filtr
źródłowy, z projektem źródłowym podmienionym na nowy (udostępnienie roli w
projekcie źródłowym staje się udostępnieniem tej roli w nowym projekcie, grupy
i inne udostępnienia bez zmian). Dodatkowo filtr jest zawsze udostępniany nowemu
projektowi, nawet gdy źródłowy był prywatny. Projekty skopiowane starszą wersją
skryptu (zespół nie widzi tablic) poprawia `fix_board_filter_permissions.py` *(skrypt nie jest dołączony do tego repozytorium)*:
```bash
python fix_board_filter_permissions.py --project-keys XYZ                      # raport
python fix_board_filter_permissions.py --project-keys XYZ --apply --editable   # udostępnij + prawo edycji
```
Ostrzeżenie `tablica jest przypisana do projektu ...` oznacza, że tablica nie
pojawi się w menu projektu; zmiana w Ustawienia tablicy → Ogólne → Lokalizacja.

**Lead projektu** (`--lead-account-id`) musi być poprawnym identyfikatorem konta
(24 znaki albo format `712020:...`) z dostępem do Jiry, inaczej Jira zwraca
`projectLead: You must specify a valid project lead`.

**Celowo pomija** role/członków (deweloperzy, administratorzy) — dodaj ręcznie
po utworzeniu, albo skorzystaj z `DEVELOPER_GROUP_NAME`/`ADMIN_ACCOUNT_ID` w `.env`
tak jak przy zwykłym `jira_sync.py` (sekcje 5–6).

Przydatne flagi:
- **`--board-ids 123,456`** — pomija automatyczne (wolne przy dużej instancji)
  wykrywanie tablic źródłowych, używa podanych ID bezpośrednio. Pierwsze
  uruchomienie bez tej flagi wypisze znalezione ID na końcu — warto je zapisać.
- **`--skip-project-creation`** — zakłada, że projekt docelowy już istnieje
  (np. z poprzedniej, przerwanej próby) i przechodzi od razu do tablic/kolumn/
  statusów, pomijając ponowne tworzenie projektu i przypisywanie schematów.

Wymaga zapisanej sesji przeglądarki (`auth_state.json` —
`browser_automation/save_login_session.py`), bo przypisywanie statusów do
kolumn używa tego samego nieoficjalnego, wewnętrznego API co
`run_column_assignments.py` — te same zastrzeżenia o stabilności (sekcja 11b).
Wymaga też `CLOUD_ID` w `.env` (identyfikator instancji Jira Cloud, używany
przez zapytania GraphQL).

### Statusy projektu (`list_project_statuses.py`)

Wypisuje statusy dostępne w projekcie dla każdego typu zgłoszenia. Przyjmuje
klucz albo dowolny link Jiry (tablica, backlog, ustawienia, zgłoszenie,
`?selectedIssue=`):
```bash
python list_project_statuses.py PROJ3
python list_project_statuses.py "https://twoja-instancja.atlassian.net/jira/software/c/projects/PROJ3/boards/123"
```
Przydaje się przy mapach statusów (sekcja 11 i kopiowanie z innej instancji):
mapy używają **nazw statusów**, a nie nazw kolumn tablicy.

### Zdejmowanie statusu z kolumny tablic (`remove_status_from_column.py`)

Przenosi wskazany status z kolumny tablicy do niezmapowanych statusów, na
wybranych albo wszystkich tablicach projektów z arkusza. Korzysta z tego samego
mechanizmu przeglądarkowego co `copy_project.py`, a po zapisie sprawdza całą
konfigurację tablicy.
```bash
python remove_status_from_column.py                                                  # podgląd; domyślnie status "Backlog" z kolumny "Backlog"
python remove_status_from_column.py --column "Done" --status "Closed"                 # podgląd dla innej pary
python remove_status_from_column.py --column "Done" --status "Closed" --apply
python remove_status_from_column.py --board-ids 8351,8352 --apply                    # wybrane tablice
```
Tablice już zmienione są przy ponownym uruchomieniu pokazywane jako "bez zmian",
więc przerwany przebieg wystarczy uruchomić jeszcze raz. Projekty z
`protected_projects.txt` są pomijane, także przy `--board-ids`.

### Gdy zapis kolumn przestaje działać

Przypisywanie statusów do kolumn (`copy_project.py`, `remove_status_from_column.py`,
krok 3 pipeline'u) korzysta z wewnętrznego zapytania GraphQL interfejsu Jiry.
Błędy i ich przyczyny:

| Komunikat | Przyczyna | Co zrobić |
|---|---|---|
| `... -> sesja przeglądarki wygasła` (odpowiedź `UNAUTHENTICATED`) | sesja zapisana przez `save_login_session.py` wygasła w trakcie długiego przebiegu | `python browser_automation/save_login_session.py` i ponowne uruchomienie. `remove_status_from_column.py` zatrzymuje się przy pierwszym takim błędzie (`[PRZERWANO]`) |
| `Brak poprawnej odpowiedzi GraphQL po 4 próbach ... pustą treścią` | przeciążenie albo ograniczenie zapytań (np. kilka dużych procesów naraz) | odczekać kilka minut i ponowić; skrypt sam ponawia pusty odczyt do 4 razy |
| `... Jira nie zna już skrótu zapytania` (`PersistedQueryNotFound`) | Atlassian zmienił skrót zapisanego zapytania przy aktualizacji interfejsu | przechwycić nowy skrót (niżej) |

**Nowy skrót zapytania:** w przeglądarce otwórz dowolną tablicę → Ustawienia
tablicy → Kolumny, DevTools (F12) → Network, filtr `ColumnsSettings`, przeładuj
stronę. W adresie zapytania `.../gateway/api/graphql/pq/<SKRÓT>?operation=<NAZWA>`
jest skrót i nazwa operacji. Wpisz je do `.env`:
```
GRAPHQL_QUERY_HASH=<SKRÓT>
GRAPHQL_OPERATION=<NAZWA>      # tylko jeśli zmieniła się też nazwa
```
Ten sam skrót wykorzystuje `run_column_assignments.py` / `assign_statuses_to_columns.js`
(sekcja 11b).

### Kopia projektu z INNEJ instancji Jiry (`copy_project_cross_instance.py`)

Przenosi **zawartość** projektu z cudzej instancji Jiry do naszej: zgłoszenia
z typem i hierarchią (Epic → Task → Sub-task, rodzice tworzeni przed dziećmi),
opisy, komentarze (cały wątek), załączniki, zgłaszającego i przypisanego,
**etykiety**, **priorytet** i status według mapy albo reguły. **Nie przenosi
konfiguracji** (pola niestandardowe i ich wartości, workflowy, ekrany, schematy),
terminów, oryginalnych dat ani powiązań między zgłoszeniami.

#### Dostęp do źródła

W `.env` (docelowa instancja to zwykłe `JIRA_*`):
```
SOURCE_JIRA_BASE_URL=https://ich-firma.atlassian.net
SOURCE_JIRA_EMAIL=...          # to samo konto Atlassian, jeśli masz tam dostęp tym mailem
SOURCE_JIRA_API_TOKEN=...      # zwykły token, NIE "token z zakresami" (scoped)
```
Token należy do konta Atlassian, nie do instancji, więc przy tym samym mailu
działa w obu. Zalecany osobny token z czytelną nazwą, do usunięcia po migracji.
Tokeny wygasają: skrypt na starcie sprawdza logowanie (`/myself`) i widoczność
projektu źródłowego. Przy `HTTP 401` albo braku projektu zatrzymuje się od razu
z komunikatem `[BŁĄD] Instancja źródłowa ... odrzuca logowanie`. Bez tej kontroli
wyszukiwanie zwróciłoby pustą listę jak dla anonimowego użytkownika i wyglądało
na "0 zadań w źródle".

#### Projekt docelowy

**Najlepiej utworzyć go wcześniej** ręcznie, z opcją "Udostępnij ustawienia
istniejącego projektu" i naszym projektem wzorcowym. Wtedy dostaje nasz
standardowy workflow i schematy, a skrypt tylko go wypełnia. Dla **istniejącego**
projektu `--target-name` i `--target-lead-account-id` są zbędne (lead i nazwa
zostają bez zmian, lead nie jest dopisywany do administratorów). Gdy projekt
nie istnieje, skrypt tworzy go z domyślną konfiguracją instancji i wtedy oba
parametry są wymagane. Jeśli projekt ma już naszą tablicę, dodaj `--skip-boards`.

Przy pierwszym ustawianiu statusów skrypt jednorazowo buduje mapę przejść
workflow przez kilka tymczasowych zgłoszeń, które od razu usuwa. Stąd luki
w numeracji (np. `CJD-2` do `CJD-7`). W aktywnym projekcie mogą one uruchomić
reguły automatyzacji.

**Projekt docelowy z istniejącymi zgłoszeniami:** podgląd pokazuje, ile zadań ze
źródła ma tytuł identyczny z istniejącym zgłoszeniem w celu (np. przeniesione
wcześniej ręcznie). `--skip-existing-titles` pomija je razem z ich podzadaniami.
Bez tej opcji kopiowane jest wszystko. Przy kopiowaniu do aktywnego projektu warto
też poprosić administratora o czasowe wstrzymanie automatyzacji i pamiętać, że
Jira przy tworzeniu przez API zawsze wysyła maile do przypisanych osób.

#### Odtworzenie workflow źródła (opcjonalnie, przed kopiowaniem zadań)

Gdy projekt docelowy ma mieć **ten sam workflow co źródło** (zamiast naszego
standardowego), przed kopiowaniem zadań odtwarza się go w trzech krokach.
Konto w instancji źródłowej musi być tam administratorem, inaczej workflow nie
da się odczytać.

1. **Eksport workflow** (`export_source_workflows.py`, tylko odczyt źródła) *(skrypt nie jest dołączony do tego repozytorium)*:
   ```bash
   python export_source_workflows.py --source-key KLUCZ
   python export_source_workflows.py --source-key KLUCZ --name-prefix "KLUCZ – " --out jira_schema_klucz.yaml
   ```
   Powstają `jira_schema_<klucz>.yaml` (statusy z kategoriami, workflow z
   przejściami, id i układem diagramu, mapowanie typ → workflow) oraz
   `status_map_<klucz>.json` (mapowanie statusów 1:1, gotowe do `--status-map`).
   Działa dla projektów company- i team-managed. Reguł przejść (warunki,
   walidatory, post-functions) **nie kopiuje**, bo odwołują się do grup i pól
   tamtej instancji; skrypt wypisuje je na końcu do ręcznego odtworzenia.
2. **Workflow i schemat u nas** (sekcja 17):
   ```bash
   python build_jira_schema.py --schema jira_schema_klucz.yaml --only statuses_workflow,workflow_scheme --dry-run
   python build_jira_schema.py --schema jira_schema_klucz.yaml --only statuses_workflow,workflow_scheme
   ```
   Sprawdź wcześniej dokładną pisownię statusów: status różniący się jednym
   znakiem powstanie jako nowy.
3. **Pusty projekt firmowy** z podpiętym schematem "KLUCZ – Workflow Scheme"
   (bez zgłoszeń Jira nie pyta o migrację statusów). Potem kopiowanie zadań
   z `--status-map status_map_<klucz>.json`; skrypt wykryje istniejący projekt
   i go użyje.

Pominięcie kroku 3 oznacza, że `copy_project_cross_instance.py` założy projekt
z domyślnym workflow instancji.

#### Statusy

Plik z mapą **nazw statusów** (nie kolumn tablicy) albo reguła według **kategorii**
statusu w źródle; wpis z pliku ma pierwszeństwo:
```bash
--status-map status_map_projekt1.json      # {"Status w źródle": "Status u nas"}
--done-status Done --other-status Misc     # kategoria Done -> Done, reszta -> Misc
```
Nazwy muszą zgadzać się co do znaku, łącznie ze znakami typu `·` i `✓`.
Kolumny tablicy często nazywają się inaczej niż statusy w nich (np. kolumna
"Review Kunde" zawiera status "Customer Testing"), więc nazwy statusów
sprawdzaj przez `python list_project_statuses.py KLUCZ` albo w ustawieniach
kolumn tablicy. Podgląd wypisuje faktyczne statusy źródła, plan
"docelowy <- źródłowe" i sprawdza, czy każdy status docelowy istnieje. Pozycja
`(domyślny)` w planie oznacza status źródła bez mapowania. Uwaga: dodanie
statusu do workflow współdzielonego przez wiele projektów zmienia go we wszystkich.

#### Osoby

Zgłaszający i przypisany są ustawiani, jeśli ta sama osoba ma konto u nas.
Dopasowanie: najpierw `--user-map`, potem ten sam identyfikator konta Atlassian,
potem mail. Podgląd wypisuje niedopasowanych z identyfikatorem w źródle:
```json
{"accountId w źródle": "accountId u nas"}
```
**Nazwij tę mapę inaczej niż `user_map.json`** (np. `wt_user_map.json`):
`user_map.json` to mapa użytkowników **Asany** używana przez `asana_jira_sync.py`.
Ustawienie zgłaszającego wymaga uprawnienia *Modify Reporter*, przypisanego
uprawnienia *Assignable User*. Twórcy i autorów komentarzy nie da się ustawić
przez API.

#### Notka o pochodzeniu, etykiety, priorytety, opisy

- **Notka (po niemiecku)** na początku opisu i prefiks przy każdym komentarzu:
  > *Übernommen aus ich-firma.atlassian.net · Original: WT-30 · erstellt am: 2025-03-14 · Melder: Pascal Probst · zugewiesen an: nicht zugewiesen*

  Teksty w stałych `ORIGIN_NOTE_TEMPLATE` i `COMMENT_PREFIX_TEMPLATE`, wyłączenie
  `--no-origin-note`.
- **Etykiety** są przenoszone 1:1 (spacje zamieniane na `_`, bo Jira ich nie dopuszcza).
- **Priorytet** po nazwie, ale tylko spośród priorytetów **dozwolonych w projekcie
  docelowym** (schemat priorytetów projektu). Nieznany priorytet zostaje domyślny
  i trafia do podsumowania (`Priorytety ze źródła nieznane u nas`).
- **Opis odrzucony przez Jirę** (elementy wskazujące na tamtą instancję) jest
  najpierw **oczyszczany**: wzmianka → "@Imię Nazwisko", obrazek/plik →
  "[Anhang: nazwa]", karta z linkiem → sam link, emoji/status/data → tekst
  (`[INFO] Opis oczyszczony`). Dopiero gdy i to nie przejdzie, zostaje sama notka.
- Pole odrzucone przez ekran albo schemat (etykiety, priorytet, osoby) jest
  pomijane, a zgłoszenie i tak powstaje.

#### Podział jednego źródła na kilka projektów

`--only-statuses` / `--exclude-statuses` (nazwy statusów **źródła**, po przecinku)
wybierają część zgłoszeń. Skrypt uruchamia się wtedy raz na każdy projekt docelowy,
zwykle z tą samą listą raz w `--only-statuses`, raz w `--exclude-statuses`.
Zasady, żeby nie rozrywać hierarchii:

| Element | Gdzie trafia |
|---|---|
| Task, Bug, Story itd. | według **własnego** statusu |
| Sub-task | **razem z rodzicem**, niezależnie od własnego statusu |
| Epic z dziećmi | do **każdego** projektu, do którego trafia choć jedno jego dziecko (bez pustych duplikatów) |
| Epic bez dzieci | według własnego statusu |

Przy podziale stan kopiowania jest prowadzony osobno dla pary `źródło>cel`
(np. `WEB>CJD`, `WEB>PROJ4`), więc ten sam Epik może być w dwóch projektach.
Sub-taski idą z rodzicem, więc mapa statusów projektu, do którego trafia rodzic,
musi obejmować też statusy podzadań z drugiej grupy (inaczej w planie pojawi się
`(domyślny)`).

#### Kopiowanie przyrostowe (nowości ze źródła)

Ponowne uruchomienie **tej samej komendy**:
- tworzy tylko zgłoszenia, których nie ma jeszcze w stanie kopiowania,
- dosyła nowe komentarze i załączniki do już skopiowanych,
- z `--update-status` przestawia status już skopiowanych, gdy w źródle się zmienił
  (ostatnio nadany status zapisuje się w stanie, więc kolejne przebiegi są szybkie),
- z `--refresh-existing` uzupełnia już skopiowane: dopisuje etykiety, ustawia
  priorytet i podmienia opis **tylko** tam, gdzie jest dziś sama notka,
- przy podziale na projekty **nie dubluje** zadania, które w źródle przeszło do
  statusu z drugiej grupy: zostaje w projekcie, do którego trafiło pierwotnie,
  a skrypt wypisze je jako `Pomijam N zadań skopiowanych już do innego projektu`.
  Jego status nie jest wtedy aktualizowany; o przeniesieniu decydujesz ręcznie.

Podgląd (`--dry-run`) rozróżnia: `NOWE do utworzenia` (z listą), `Już skopiowane`,
`Zmiana statusu już skopiowanych` oraz `Skopiowane przed --update-status` (przy
pierwszym użyciu tej opcji status każdego z nich zostanie sprawdzony raz).

#### Uprawnienia

Przed kopiowaniem zgłoszeń skrypt dodaje te same grupy i role co przy migracji
(z `.env`, sekcje 5-6). Dodatkowe grupy: `--extra-group "Nazwa grupy=Nazwa roli"`
(można powtarzać). Dla skonfigurowanego projektu użyj `--skip-access`.

#### Gotowe komendy

Pierwsza kopia całego projektu:
```bash
python copy_project_cross_instance.py \
    --source-key WT --target-key WT --target-name "Webshop Testing" \
    --target-lead-account-id "$DEFAULT_LEAD_ACCOUNT_ID" \
    --done-status Done --other-status Misc \
    --user-map wt_user_map.json --skip-boards --dry-run
```
Podział WEB na dwa istniejące projekty (CJD i PROJ4), także do późniejszego
dociągania nowości:
```bash
python copy_project_cross_instance.py \
    --source-key WEB --target-key CJD \
    --only-statuses "CJD - Backlog,CJD - On Hold,CJD - Neu / Offen,CJD - In Arbeit" \
    --status-map status_map_projekt1.json \
    --user-map wt_user_map.json --skip-boards --skip-access --update-status --dry-run

python copy_project_cross_instance.py \
    --source-key WEB --target-key PROJ4 \
    --exclude-statuses "CJD - Backlog,CJD - On Hold,CJD - Neu / Offen,CJD - In Arbeit" \
    --status-map status_map_projekt2.json \
    --user-map wt_user_map.json --skip-boards --skip-access --update-status --dry-run
```
Te same komendy bez `--dry-run` wykonują kopię. Można je bezpiecznie przerwać
(`Ctrl+C`) i uruchomić ponownie; jedyny wyjątek to przerwanie w trakcie komentarzy
jednego zgłoszenia, wtedy jego część komentarzy pojawi się drugi raz. Bezpieczniej
przerywać na początku kolejnego zgłoszenia (linia `[n/N] ... -> Utworzono ...`).

#### Współistnienie z projektami synchronizowanymi z Asany

Zgłoszenia skopiowane z innej instancji nie mają odpowiednika w Asanie.
Wszystkie narzędzia porównujące Jirę z Asaną (`compare_project_totals.py`,
`relink_state.py`, `handle_migrated_to_jira.py`) **pomijają** je na podstawie
`cross_instance_copy_state.json` (komunikat `Pomijam N zgłoszeń skopiowanych
z innej instancji Jiry`), a `resolve_relink_duplicates.py` nigdy ich nie usunie.
Dlatego `cross_instance_copy_state.json` **nie wolno kasować**, dopóki projekt
docelowy jest synchronizowany z Asaną. Statusy w tych zgłoszeniach wynikają ze
źródła, więc nie zamykaj ich narzędziem `close_children_of_done.py --source jira`.

**Alternatywa bez API:** w źródle eksport "Excel CSV (wszystkie pola)", u nas
Ustawienia → System → Import z systemów zewnętrznych → CSV. Załączniki są wtedy
tylko linkami do źródłowej instancji.

## 5. Automatyczne dodawanie grupy/użytkowników deweloperów

Jeśli chcesz, żeby konkretna grupa Jira **i/lub** konkretni użytkownicy byli
automatycznie dodawani do roli "Developers" (albo innej roli) w każdym nowo
tworzonym projekcie, ustaw w `.env`:

```
DEVELOPER_GROUP_NAME=nazwa-grupy
DEVELOPER_ACCOUNT_IDS=5b10a2844c20165700ede21,5c20a2844c20165700ede99
DEVELOPER_ROLE_NAME=Developers
```

Oba (grupa i lista użytkowników) działają **niezależnie** — możesz użyć jednego,
drugiego, albo obu naraz (dwa osobne żądania do tej samej roli).

Skrypt raz na starcie sprawdzi, czy taka rola istnieje w Jirze, i jeśli tak —
po każdym udanym utworzeniu projektu doda tę grupę do tej roli (dokłada, nie
nadpisuje ewentualnych innych aktorów już przypisanych do roli). Jeśli rola o
podanej nazwie nie istnieje, skrypt wypisze ostrzeżenie na starcie i nie doda
grupy do żadnego projektu (żeby nie próbować bez sensu 550 razy).

Wymaga uprawnień *Administer Projects* dla tworzonych projektów albo
*Administer Jira* — token użyty w `.env` musi je mieć, inaczej dostaniesz
ostrzeżenie per-projekt w podsumowaniu (projekt i tak zostanie utworzony,
tylko bez dodanej grupy — trzeba ją będzie dodać ręcznie).

Nie masz pewności co do dokładnej nazwy grupy albo roli? Uruchom:
```bash
python list_groups.py          # wszystkie grupy (opcjonalnie z fragmentem nazwy)
python list_project_roles.py   # wszystkie role projektowe
```

## 6. Automatyczne dodawanie administratora(ów)

Analogicznie możesz dodać **jednego lub kilku konkretnych użytkowników**
(np. siebie i kilku członków zespołu) do roli administratora w każdym nowo
tworzonym projekcie:

```
ADMIN_ACCOUNT_ID=twój-accountId
ADMIN_ROLE_NAME=Administrators
```

Kilka osób naraz — rozdziel przecinkami (jedno żądanie do API na projekt,
niezależnie od liczby osób):
```
ADMIN_ACCOUNT_ID=5b10a2844c20165700ede21,5c20a2844c20165700ede99
```

`ADMIN_ACCOUNT_ID` to `accountId`, nie e-mail — znajdziesz go przez:
```bash
python get_my_account_id.py              # dla siebie
python get_group_members.py "Nazwa grupy" # dla wielu osób z grupy naraz
```

Działa identycznie jak grupa deweloperów (sekcja 5): sprawdzenie roli raz na
starcie, dodanie po każdym utworzeniu projektu, ostrzeżenie zamiast twardego
błędu, jeśli się nie powiedzie.

**Dodatkowo:** możesz sprawić, żeby lead **każdego** projektu (różny per
projekt — kolumna `lead_account_id` w arkuszu, albo `DEFAULT_LEAD_ACCOUNT_ID`)
automatycznie dostawał uprawnienia administratora **w swoim własnym**
projekcie, niezależnie od globalnej listy `ADMIN_ACCOUNT_ID` powyżej:

```
ADD_LEAD_AS_ADMIN=true
```

Oba mechanizmy łączą się w **jedno** żądanie do API na projekt (bez
duplikatów, jeśli lead akurat jest też na liście `ADMIN_ACCOUNT_ID`).

## 7. Format arkusza Google

Test połączeń w panelu (Start, krok 2) sprawdza nagłówki, liczbę wierszy i wiersze
bez poprawnego linku do Asany. Pierwszy wiersz to nagłówki (wielkość liter nieistotna). **`asana_project_link`
jest bezwzględnie wymagany.** Bez niego (albo gdy dostęp do podanego linku się
nie powiedzie, np. projekt prywatny bez dostępu dla tokena) — **cały wiersz
jest pomijany, projekt NIE zostaje utworzony w Jirze** (nie tylko bez opisu —
w ogóle). To celowe: dopasowanie po nazwie zostało usunięte jako zawodne
(patrz sekcja `asana_project_link` niżej), więc bez linku nie ma bezpiecznego
sposobu na jednoznaczne powiązanie z właściwym projektem w Asanie. Skrypt
wypisuje zbiorcze podsumowanie wszystkich pominiętych wierszy na końcu
przebiegu. `project_name` możesz zostawić puste, jeśli podałeś link — nazwa
zostanie pobrana automatycznie z Asany. Reszta kolumn jest opcjonalna:

| project_name              | jira_key | jira_link | project_type_key | template_key | lead_account_id       | description | board_template_id | asana_project_link |
|-----------------------------|----------|-----------|--------------------|-----------------|--------------------------|--------------|--------------------|----------------------|
| Kampania Q4                 |          |           | software           |                 | 5b10a2844c20165700ede21 |              |                    | https://app.asana.com/0/1234567890123456/list |
| Redesign strony głównej     |          |           | business           |                 |                          | Redesign UI  | 7276               | https://app.asana.com/0/1111111111111111/list |
| *(puste — nazwa dociągnięta z linku)* |  |  |    |                 |                          |              |                    | https://app.asana.com/0/9876543210987654/list |

- **`board_template_id`** — używane przez `bulk_configure_columns.py` (nie przez
  `jira_sync.py`). ID tablicy wzorcowej dla UKŁADU KOLUMN tego konkretnego
  projektu — przydatne, gdy projekt wzorcowy ma kilka tablic o różnych układach
  (np. `REFERENCE2` ma zarówno 11-kolumnowy wzorzec na tablicy 6110, jak i
  prostszy 4-kolumnowy na tablicach 7276/7273/7274/7275). Puste = użyty zostanie
  domyślny wzorzec (`JIRA_TEMPLATE_BOARD_ID` z `.env`); wypełnione = użyta
  zostanie dokładnie ta tablica — **per wiersz**, więc różne projekty mogą
  korzystać z różnych wzorców w jednym przebiegu skryptu.

- **`asana_project_link`** — BEZPOŚREDNI link do projektu w Asanie (skopiowany
  z paska adresu przeglądarki). To **jedyny** sposób identyfikacji projektu
  Asany w całym pakiecie — dopasowanie po nazwie zostało **celowo usunięte**
  (bywały duplikaty nazw projektów w Asanie, co prowadziło do synchronizacji
  z niewłaściwym projektem — kosztowna lekcja z boleśnie długiego debugowania).
  Obsługiwane formaty: `https://app.asana.com/0/<id>/list` oraz
  `https://app.asana.com/1/<workspace>/project/<id>/list` (i warianty `/board`
  zamiast `/list`). Używane przez `jira_sync.py` (wzbogacenie opisu),
  `asana_jira_sync.py` i `jira_asana_sync.py` (synchronizacja zadań w obie strony).

- **`jira_key` jest zapisywany z powrotem do arkusza automatycznie** po
  wygenerowaniu (patrz sekcja 9) — więc nawet zostawiając to pole puste,
  po pierwszym uruchomieniu będziesz mieć gotowe klucze do szybkiego
  kopiowania (np. do ręcznej konfiguracji kolumn/statusów w Jirze).

- **`jira_link` (opcjonalna)** — jeśli dodasz tę kolumnę w nagłówku, skrypt
  automatycznie zapisze tam też gotowy, klikalny link do projektu
  (`.../browse/KLUCZ`), razem z zapisem `jira_key`. Bez tej kolumny wszystko
  działa dokładnie tak samo, po prostu bez linków.

- `lead_account_id` to `accountId` użytkownika w Jira (nie e-mail!). Znajdziesz go np. przez
  `GET /rest/api/3/user/search?query=email@firma.com`, albo prościej — uruchom dołączony
  `python get_my_account_id.py email@firma.com`. Jeśli wiersz nie ma leada, użyty zostanie
  `DEFAULT_LEAD_ACCOUNT_ID` z `.env` (jeśli ustawiony). **Jira wymaga poprawnego leada przy
  tworzeniu projektu** — jeśli oba pola są puste, skrypt zgłosi to jako czytelny błąd
  zamiast wysyłać żądanie, które i tak zostałoby odrzucone.

## 8. Uruchomienie

Najpierw podgląd, bez tworzenia czegokolwiek:
```bash
python jira_sync.py --dry-run
```

Faktyczne utworzenie projektów:
```bash
python jira_sync.py
```

Skrypt jest **idempotentny** — jeśli projekt o danym kluczu już istnieje w Jira,
zostanie pominięty. Można go więc uruchamiać wielokrotnie (np. za każdym razem,
gdy dodasz nowy wiersz do arkusza) bez ryzyka duplikatów.

Równolegle, w kilku terminalach (każdy proces bierze swoją część arkusza):
```bash
python jira_sync.py --shard 1/3
python jira_sync.py --shard 2/3
python jira_sync.py --shard 3/3
```
Wygodniej zrobić to przez orkiestrator: `python run_full_pipeline.py --only projects --parallel 3`
(sekcja 11a).

Kod wyjścia `2` oznacza **częściowe** niepowodzenie (część projektów powstała,
część nie), `1` oznacza błąd krytyczny (np. brak `.env`). Orkiestrator przy
kodzie `2` kontynuuje kolejne kroki dla projektów, które się udały.

## 9. Jak działa generowanie kluczy

Dla nazwy projektu bez podanego `jira_key`:
1. Kilka słów → inicjały każdego słowa (np. "Portal Klienta B2B" → `PKB`).
2. Jedno słowo → pierwsze do 10 znaków, wielkimi literami.
3. **Minimum 6 znaków** — jeśli wynik jest krótszy, dopełniany kolejnymi
   literami z nazwy, a w ostateczności literą "X" (np. "A" → `AXXXXX`).
4. Jeśli wygenerowany klucz koliduje z już przydzielonym w tym samym przebiegu,
   w cache, ALBO z **faktycznie istniejącym** projektem w Jirze (sprawdzane
   na żywo przez API — nie tylko po naszym lokalnym cache), doklejany jest
   numer (`PKB2`, `PKB3`, ...) i próba powtarza się.
5. Wynik zapisywany jest do `project_key_cache.json` (trwały między uruchomieniami)
   **oraz z powrotem do arkusza** (kolumna `jira_key`) — jednym zbiorczym
   żądaniem na końcu całego przebiegu, nie w trybie `--dry-run`. Wymaga, żeby
   konto serwisowe Google miało w arkuszu uprawnienia **Edytora**, nie tylko
   Przeglądającego, i żeby kolumna `jira_key` już istniała w nagłówku.

Jeśli chcesz mieć pełną kontrolę nad konkretnym kluczem — po prostu wpisz go
ręcznie w kolumnie `jira_key` dla danego wiersza, a skrypt go nie nadpisze.

**Cache kluczowany jest po GID Asany, nie po nazwie projektu.** Dwa różne
projekty Asany mogą mieć identyczną nazwę (np. dwa etapy tej samej umowy,
"Miniaudit" i "Umsetzung", nazwane tak samo w arkuszu) — kluczowanie po
nazwie błędnie przypisywało im TEN SAM klucz Jiry, mieszając ich zadania w
jednym projekcie (kosztowna lekcja: przypadek `PROJ9`, poprawione). Każdy
unikalny link Asany zawsze dostaje własny, osobny klucz.

**Ważne ograniczenie Jiry, niezależne od klucza:** Jira wymaga też **unikalnych
NAZW** projektów, nie tylko unikalnych kluczy. Jeśli dwa wiersze w arkuszu mają
identyczną nazwę (np. przez pusty `project_name`, dociągnięty automatycznie z
identycznie nazwanego projektu w Asanie), drugi z nich **zawsze** dostanie błąd
`400 A project with that name already exists`, niezależnie od tego, jak wiele
różnych kluczy skrypt wypróbuje. Rozwiązanie: wpisz ręcznie różne nazwy w
kolumnie `project_name` dla takich wierszy (nadpisuje automatyczne dociąganie
z Asany — patrz sekcja 7).

**Zabezpieczenie przed pomyłką w arkuszu:** na starcie każdego przebiegu
skrypt sprawdza, czy jakiś `jira_key` **wpisany ręcznie** w arkuszu nie
występuje w dwóch wierszach z **różnymi** `asana_project_link` — jeśli tak,
cały skrypt zatrzymuje się z czytelnym błędem, zanim cokolwiek zdąży się
utworzyć/wymieszać (patrz `check_duplicate_sheet_rows.py` w sekcji 14, żeby
to sprawdzić ręcznie w dowolnym momencie).

## 10. Co skrypt wypisuje na końcu

- liczbę utworzonych, zaktualizowanych (opis) i pominiętych projektów,
- błędy (np. brak uprawnień, brak leada, zajęty klucz lub nazwa),
- ostrzeżenia (np. nie udało się skopiować któregoś schematu z projektu wzorcowego
  albo dodać roli),
- listę wierszy pominiętych z powodu braku `asana_project_link` lub dostępu do
  projektu w Asanie (te projekty **nie** zostały utworzone),
- ścieżkę do pliku z mapowaniem (`project_key_cache.json`).

Przy uruchomieniu przez `run_full_pipeline.py` te same liczby i szczegóły błędów
trafiają do zbiorczego podsumowania kroku na Slacku (sekcja 13).

## 11. Synchronizacja zadań Asana → Jira (`asana_jira_sync.py`)

Osobny skrypt (obok `jira_sync.py`, który tworzy tylko *projekty*) — synchronizuje
*zadania* wewnątrz już utworzonych projektów. Jednokierunkowo: Asana → Jira
(kierunek zwrotny to osobny skrypt, `jira_asana_sync.py` — patrz sekcja 12).

### Co synchronizuje

- Tytuł (skracany bezpiecznie do 255 znaków — limit Jiry — z pełną wersją
  zachowaną na początku opisu, jeśli oryginał był dłuższy), opis (z bogatym
  formatowaniem: linki, listy, pogrubienie/kursywa/podkreślenie/przekreślenie),
  przypisany, termin — zawsze, i tylko jeśli faktycznie się zmieniły.
- Status — przez sekcję Asany, zmapowaną na status Jira (patrz niżej).
  Sprawdzany PRZY KAŻDYM uruchomieniu, niezależnie od tego, czy inne pola
  się zmieniły (przeniesienie między sekcjami nie zawsze aktualizuje
  znacznik czasu zadania w Asanie).
- Komentarze — tylko prawdziwe (bez zdarzeń systemowych typu "zmienił termin"),
  z prefiksem `[Asana — Autor, data]` w treści (Jira nie pozwala podszyć się
  pod oryginalnego autora bez dodatkowej konfiguracji).
- Załączniki — pobierane z Asany i wgrywane do Jiry.
- **Podzadania i hierarchia**: podzadania z Asany jako Sub-taski, a dla zadań
  z trzema poziomami zagnieżdżenia pełna struktura Epic → Task → Sub-task
  (szczegóły w podsekcji niżej).
- **Fallback assignee** — jeśli przypisany z Asany nie może być przypisany w projekcie
  Jira (żadna jego rola nie ma „Assignable User” w schemacie uprawnień), NOWE zadanie
  powstaje bez przypisanego (lead projektu tylko wtedy, gdy projekt nie pozwala na
  nieprzypisane). W JUŻ utworzonych zadaniach przypisany zostaje bez zmian, a tytuł,
  opis i termin zapisują się normalnie.
- **Fallback formatowania** — jeśli konwersja HTML→ADF wygeneruje strukturę,
  którą Jira odrzuci jako niepoprawną (rzadkie, ale się zdarza), skrypt
  automatycznie ponawia z opisem jako zwykłym tekstem (bez formatowania)
  zamiast tracić całe zadanie.

### Hierarchia zadań: Epic → Task → Sub-task

Jira ma trzy poziomy (Epic, Task, Sub-task), a Sub-task nie może mieć własnych
podzadań. Asana pozwala zagnieżdżać dowolnie głęboko, więc skrypt stosuje reguły:

| W Asanie | W Jirze |
|---|---|
| zadanie bez podzadań podzadań | Task |
| jego podzadanie | Sub-task pod tym Taskiem |
| zadanie, którego podzadanie ma własne podzadania (kandydat na Epic) | Epic |
| podzadanie kandydata | Task pod Epikiem |
| podzadanie takiego Taska | Sub-task |
| głębsze poziomy (dowolnie głęboko) | Sub-task pod najbliższym przodkiem, który nie jest Sub-taskiem (spłaszczenie) |

Synchronizacja schodzi rekurencyjnie na wszystkie poziomy, a listę zadań
projektu porządkuje tak, żeby rodzic zawsze był przetworzony przed dzieckiem
(Asana zwraca na liście projektu także podzadania dodane do projektu, w
dowolnej kolejności). **Starsze wersje** miały tu dwa błędy: pomijały podzadania
od drugiego poziomu w dół (jeśli pośrednie podzadanie nie było na liście
projektu) i tworzyły podzadanie jako samodzielny Task, gdy stało na liście przed
rodzicem. Projekty zsynchronizowane wcześniej sprawdza i naprawia sekcja 15e, a
brakujące zadania dociąga zwykła ponowna synchronizacja.

Kandydatów na Epic wykrywa **przed** synchronizacją `detect_epic_candidates.py`
i zapisuje ich listę do `epic_candidates.json`, który `asana_jira_sync.py`
czyta automatycznie:
```bash
python detect_epic_candidates.py                           # wszystkie projekty z arkusza
python detect_epic_candidates.py --project-keys KLUCZ1,KLUCZ2
python detect_epic_candidates.py --shard 1/3               # równolegle
python detect_epic_candidates.py --project-keys KLUCZ --force   # skanuj ponownie, mimo że projekt już jest w pliku
```
Domyślnie skrypt **wznawia pracę**: pomija projekty już zapisane w pliku. Po
zmianie struktury w Asanie albo po naprawach hierarchii użyj `--force` dla
dotkniętych projektów.

Podzadanie, które ktoś **dodatkowo dodał do projektu** w Asanie, widać na liście
zadań projektu jak zwykłe zadanie. Skrypt je rozpoznaje (ma rodzica w tym samym
projekcie) i nie traktuje go jako zadania najwyższego poziomu. Bez tego filtra
takie podzadanie mogło zostać fałszywym Epikiem; jak naprawić Epiki utworzone
przed wprowadzeniem filtra, opisuje sekcja 15.

Typ i rodzic zgłoszenia są ustawiane **tylko przy tworzeniu**. Kolejne
uruchomienia aktualizują treść i status, ale nie przestawiają hierarchii.
Zmiany struktury już zmigrowanych zadań robią narzędzia z sekcji 15.

### Jak nie duplikować przy wielokrotnym uruchomieniu

Lokalny plik `task_sync_state.json` (tworzony automatycznie, **nie kasować
między uruchomieniami**) zapamiętuje dla każdego zadania: jego klucz Jira,
ostatni znany `modified_at` z Asany, ID już przeniesionych komentarzy
i załączników oraz flagi hierarchii (`is_epic`, `is_subtask`,
`top_level_ancestor_key`, czyli miejsce, pod które trafiają nowe podzadania
zadania będącego Sub-taskiem). Flagi po naprawach hierarchii odświeża
`refresh_state_flags.py` (sekcja 15). Zadania usunięte ręcznie w Jirze mogą
być oznaczone `deleted_in_jira` i są wtedy trwale pomijane (sekcja 14e). Zapisywany **po każdym pojedynczym zadaniu** (nie dopiero po
całym projekcie) — bezpieczne nawet jeśli internet padnie w połowie przebiegu
(sprawdzone boleśnie w praktyce). Bezpieczny też przy kilku równoległych
instancjach uruchomionych na RÓŻNYCH projektach naraz: każdy proces zapamiętuje
migawkę stanu z chwili wczytania i zapisuje **wyłącznie wpisy, które sam zmienił**
(z blokadą pliku). **Starsze wersje** scalały cały projekt z chwili startu, więc
równoległe procesy kasowały sobie nawzajem świeżo dodane wpisy. Skutek: zgubione
powiązania, a przy kolejnych synchronizacjach całe serie duplikatów. Takie
projekty naprawia procedura z sekcji 15f-15i.

**Zapis jest atomowy** (tymczasowy plik + `os.replace`, z blokadą `fcntl`) —
odporny na race condition przy dużej równoległości (`--parallel` w
`run_full_pipeline.py`): odczyt w trakcie zapisu przez inny proces nie
skończy się uszkodzonym JSON-em. Jeśli mimo to plik kiedyś się uszkodzi
(np. po `Ctrl+C` w złym momencie), użyj `repair_state_file.py` (sekcja 14).

**Ponawianie przy błędach przejściowych** — wszystkie zapytania do Asany i
Jiry (w tym rate limit `429`, z uwzględnieniem nagłówka `Retry-After`) są
automatycznie ponawiane z rosnącym opóźnieniem, zamiast od razu przerywać
całe zadanie.

### Link do Asany w każdym zgłoszeniu

Każde zgłoszenie dostaje jednorazowo **link zdalny** do swojego zadania w Asanie
(sekcja "Linki" w Jirze, relacja "Asana", identyfikator `asana:task:<gid>`).
Flaga `asana_link` w stanie zapobiega dodawaniu go przy każdym przebiegu. Link
pozwala zespołowi przejść do oryginału i jest najpewniejszą podstawą do
odtwarzania powiązań (`relink_state.py`, przebieg `LINK`, sekcja 15f).
W podsumowaniu: `Linki do Asany dodane`.

### Wyłączanie projektów: `sync_exclude.txt` i `protected_projects.txt`

| Plik | Działa na | Kiedy używać |
|---|---|---|
| `sync_exclude.txt` | tylko synchronizację (oba kierunki, także `--project-keys` i podgląd) | **tymczasowo**, na czas naprawy projektu; narzędzia naprawcze działają normalnie |
| `protected_projects.txt` | synchronizację, pipeline i **wszystkie** narzędzia zmieniające dane, także przy kluczu podanym wprost | **trwale**, gdy projekt nie może być ruszany (np. zespół pracuje już tylko w Jirze) |

Format obu: jeden klucz w linii, `#` = komentarz:
```
PROJ3   # zespół pracuje w Jirze, bez synchronizacji i napraw
```
Projekty z `protected_projects.txt` znikają z arkusza dla wszystkich skryptów
(`jira_sync.get_sheet_rows()`), a narzędzia działające na pliku stanu albo na
kluczach podanych wprost (`refresh_state_flags`, `fix_stale_resolution`,
`close_children_of_done`, `relink_state`, `resolve_relink_duplicates`,
`handle_migrated_to_jira`, `audit_hierarchy`, `remove_status_from_column`,
`repair_batch`) pomijają je z komunikatem `[CHRONIONE]`. Konsekwencja: także
porównanie nie pokaże takiego projektu; żeby go podejrzeć, zakomentuj chwilowo
jego linię.

Zdejmowanie partii projektów z `sync_exclude.txt` bez ruszania reszty pliku:
```bash
python3 - partia.txt << 'PY'
import sys, re
part = {re.sub(r"#.*", "", l).strip().upper() for l in open(sys.argv[1], encoding="utf-8")} - {""}
lines = open("sync_exclude.txt", encoding="utf-8").read().splitlines()
keep = [l for l in lines if re.sub(r"#.*", "", l).strip().upper() not in part]
open("sync_exclude.txt", "w", encoding="utf-8").write("\n".join(keep) + "\n")
print(f"Zdjęto linii: {len(lines) - len(keep)}")
PY
```
Komunikat liczy usunięte **linie**: jeśli projekt był wpisany dwa razy, liczba
będzie większa niż liczba projektów. Podwójne wpisy usuwa
`awk '{k=$1} !seen[k]++' sync_exclude.txt > tmp && mv tmp sync_exclude.txt`.

### Zadania przeniesione ręcznie do Jiry ("migriert zu Jira")

Zespoły bywa, że przenoszą zadanie do Jiry ręcznie i oznaczają je w Asanie
dopiskiem, np. "Agenda JF (migriert zu Jira)" (także z literówką "mirgiert").
Dla takich zadań źródłem prawdy jest Jira, więc synchronizacja pomija je **razem
z całym poddrzewem**: nie tworzy ich i nie aktualizuje. To samo dotyczy wpisów
oznaczonych w stanie flagą `jira_master` (ustawia ją `handle_migrated_to_jira.py`,
sekcja 15h). Wzorzec (wyrażenie regularne, bez wielkości liter) można zmienić
w `.env`:
```
MIGRATED_TO_JIRA_PATTERN=mi[rg]{1,2}r?iert\s+zu\s+jira
```
W podsumowaniu: `prowadzone w Jirze ('migriert zu Jira')`. Zespołom warto
przekazać: przy ręcznym przeniesieniu zadania do Jiry dopisać to w Asanie.

### Konfiguracja przed pierwszym uruchomieniem

1. **`asana_project_link` w arkuszu** — wymagany dla każdego wiersza (patrz
   sekcja 7). Bez niego projekt jest pomijany z czytelnym ostrzeżeniem.

2. **Wymagane pola** — Twój projekt wzorcowy może mieć pola obowiązkowe przy
   tworzeniu zadania (np. niestandardowe "Type", "Department"), których Asana
   nie ma. Sprawdź je:
   ```bash
   python check_required_fields.py KLUCZPROJEKTU
   ```
   Test połączeń w panelu wypisuje takie pola dla projektu wzorcowego. Pola z jedną
   dozwoloną wartością uzupełniają się automatycznie. Dla jednego pola z wieloma
   wartościami ustaw `DEFAULT_TYPE_FIELD_ID` i `DEFAULT_TYPE_FIELD_VALUE`; gdy obu
   zmiennych nie ma w `.env`, skrypt używa `customfield_10188` = `NONBILLABLE` (pole
   „Type” instancji, dla której pakiet powstał), a pusta wartość wyłącza uzupełnianie.

3. **Mapowanie sekcja Asany → status Jira** — sekcje Asany bywają etapami/
   kamieniami milowymi (np. "3. Kickoff"), nie nazwami pasującymi wprost do
   statusów Jiry ("In Progress").

   **W panelu: „Mapowanie statusów”** (część I) - sekcje ze wszystkich projektów
   arkusza ↔ statusy projektu wzorcowego, tylko statusy istniejące we wzorcu, z
   propozycjami i jawnym statusem dla zadań ukończonych (`__ukonczone__`).

   **Z terminala** - wypisz sekcje **jednego** projektu:
   ```bash
   python list_asana_sections.py "Dokładna nazwa projektu w Asanie"
   python list_asana_sections.py --link "https://app.asana.com/0/123456789/list"
   ```
   Albo, przy wielu projektach naraz — wypisz **unikalne** sekcje ze
   **wszystkich** wierszy arkuszu jednym przebiegiem (z gotowym formatem
   `"sekcja: przykładowy projekt": "",` do wklejenia, i automatycznym
   porównaniem z tym, co już masz zmapowane):
   ```bash
   python list_all_asana_sections.py
   python list_all_asana_sections.py --limit 10          # test na próbce
   python list_all_asana_sections.py --save wynik.json   # zapis do pliku
   ```
   **Ważne:** wklejony w ten sposób klucz zawiera dopisany przykładowy
   projekt (po dwukropku) — to tylko podpowiedź kontekstu, nie prawdziwa
   nazwa sekcji. Przed zapisaniem do `section_status_map.json` odetnij część
   po **ostatnim** `": "` (chyba że prawdziwa nazwa sekcji sama zawiera
   dwukropek — wtedy sprawdź ręcznie, który dwukropek jest "Twój").

   Uzupełnij `section_status_map.json` (klucz = **czysta** nazwa sekcji,
   wartość = nazwa statusu Jira **albo lista statusów**, np.
   `["In progress", "Feedback Required"]` — przydatne, gdy jedna sekcja w
   prostym projekcie ma sensownie odpowiadać kilku bardziej szczegółowym
   statusom przy kierunku zwrotnym). Ta tabela jest globalna — współdzielona
   między wszystkimi projektami i OBOMA kierunkami synchronizacji, dokładaj
   wpisy w miarę odkrywania nowych sekcji. Sekcja bez wpisu = zadanie
   zostaje przy domyślnym statusie startowym (skrypt wypisze listę takich
   sekcji na końcu przebiegu). Nazwa sekcji jest automatycznie **oczyszczana
   z białych znaków** na początku/końcu przed porównaniem z mapą — sekcja
   `"Prio "` (ze spacją, jak bywa w Asanie) poprawnie dopasuje się do wpisu
   `"Prio"` w mapie.

4. **Mapowanie użytkowników** — bez tego zadania importują się bez
   przypisanego użytkownika. **W panelu: „Mapowanie osób”** (część I) - osoby z Asany
   ↔ konta z wybranych grup Jiry, z propozycjami; osoby bez konta oznaczasz „nie w
   Jirze” (`asana_users_not_in_jira.json`). Przypisać zadanie można tylko osobie, której
   rola w projekcie ma „Assignable User” w schemacie uprawnień - samo mapowanie (ani bycie
   leadem projektu) tego nie daje; panel w „Statusy i osoby” pokazuje schemat i role
   z tym uprawnieniem. Z terminala:
   ```bash
   python generate_user_map.py
   ```
   Dopasowuje automatycznie po adresie e-mail; jeśli adresy w Jirze mają stały
   wzorzec (`Imię.Nazwisko@domena`, różny od adresu w Asanie), skrypt próbuje
   też zbudować taki adres z imienia/nazwiska (z wariantem transliterowanym
   niemieckich znaków ü/ö/ä/ß). Domena ustawiana przez `JIRA_EMAIL_DOMAIN`
   w `.env` (gdy brak zmiennej: domena instancji, dla której pakiet powstał). Domyślnie **pomija gości** Asany —
   żeby ich uwzględnić: `python generate_user_map.py --include-guests`.
   Wynik: `user_map.json` (**nadpisywany w całości** - dlatego lista „nie w Jirze” jest
   w osobnym pliku). Osoby niedopasowane automatycznie możesz dopisać
   ręcznie do tego pliku (`"asana_user_gid": "jira_accountId"`), albo znaleźć
   gid Asany przez `python list_asana_members.py`.

### Statusy: mapa sekcji, mapy projektów, zadania ukończone i podzadania

**Zadania z listy projektu** dostają status według sekcji Asany przy **każdym**
przebiegu (nie tylko przy tworzeniu), z `section_status_map.json`. Zadanie
**odhaczone** w Asanie dostaje status końcowy niezależnie od sekcji.

**Status końcowy** to wpis `"__ukonczone__"`: najpierw z mapy projektu, potem z mapy
głównej, a gdy nie ma go nigdzie - `Fertig` (status instancji, dla której pakiet powstał;
w innej Jirze ustaw `__ukonczone__` w mapie głównej - panel robi to w „Mapowaniu
statusów”). Gdy wskazanego statusu nie ma w projekcie, zadanie dostaje status według
sekcji, a skrypt wypisuje ostrzeżenie.

**Mapa dla jednego projektu:** gdy projekt ma inne statusy niż standardowe albo
te same nazwy sekcji mają w nim prowadzić gdzie indziej, obok głównej mapy
tworzysz `section_status_map.<KLUCZ>.json` (dwie kropki, klucz wielkimi literami,
dokładnie jak w Jirze). Jego wpisy mają pierwszeństwo, ale tylko w tym projekcie;
główna mapa zostaje nietknięta. Wpis `"__ukonczone__"` wskazuje status dla
zadań odhaczonych w Asanie w tym projekcie (pierwszeństwo przed mapą główną; gdy brak w obu - `Fertig`):
```json
{
  "__ukonczone__": "Live",
  "Backlog / Anfragen": "Backlog",
  "Setup": "Setup",
  "Inhalt finalisieren": "Finalize Configuration",
  "TWINT Aktivierung (Live)": "Refinement",
  "Live": "Live"
}
```
Sprawdzenie, czy plik jest wczytywany (pusty słownik = zła nazwa pliku):
```bash
python3 -c "import asana_jira_sync as a; print(a.project_section_overrides('PROJ'))"
```
Kierunek zwrotny (Jira → Asana) korzysta z tego samego pliku. Wyłączenie mapy
projektu: zmień nazwę pliku (np. dopisz `.wylaczona`). Przed powrotem do głównej
mapy upewnij się, że workflow projektu ma statusy, na które ona wskazuje.

**Podzadania** (wszystkie poziomy):

| W Asanie | Status w Jirze |
|---|---|
| podzadanie odhaczone | status końcowy (`__ukonczone__` projektu, mapy głównej albo `Fertig`) |
| otwarte podzadanie pod **odhaczonym** zadaniem (na dowolnym poziomie) | status końcowy |
| otwarte podzadanie z **własną** sekcją (dodane też do listy projektu) | według swojej sekcji (pierwszeństwo) |
| pozostałe otwarte podzadania | status zadania najwyższego poziomu według jego sekcji |

Ostatnio nadany status jest zapisywany w stanie (`status_applied`), więc
przestawiany jest tylko wtedy, gdy wynik się zmienia. Pierwszy przebieg po tej
zmianie ustawia statusy **wszystkim** podzadaniom projektu.

Sekcje bez mapowania wypisywane są na końcu podsumowania ("Sekcje BEZ wpisu
w section_status_map.json"). Warianty z literówką dopisuje się do mapy jako
osobne klucze z tym samym statusem, bezpiecznie np. tak:
```bash
python3 -c "import json; p='section_status_map.json'; m=json.load(open(p,encoding='utf-8')); m['Whishlist (PM & Lead)']='To Clarify'; json.dump(m,open(p,'w',encoding='utf-8'),ensure_ascii=False,indent=2)"
```
Status nieosiągalny w workflow (brak przejścia, np. z "Setup" do "Backlog")
daje ostrzeżenie i `Statusy błąd` w podsumowaniu. Rozwiązanie: przejście
"z dowolnego statusu" w workflow albo ręczna zmiana statusu.

### Podsumowanie przebiegu

| Linia | Znaczenie |
|---|---|
| `Utworzono` / `Zaktualizowano` | z podziałem na Epiki, Taski, Sub-taski |
| `Linki do Asany dodane` | nowe linki zdalne (sekcja wyżej) |
| `bez zmian od ostatniego syncu` | zadanie w Asanie nie zmieniło się (w tym podzadania obecne też na liście projektu, obsłużone razem z rodzicem) |
| `usunięte w Jirze (deleted_in_jira)` | celowo pomijane (sekcja 14h) |
| `kandydaci na Epic do retrofit_epics.py` | zadanie powinno być Epikiem, ale ma już zmigrowane podzadania; opcjonalnie sekcja 15a |
| `prowadzone w Jirze ('migriert zu Jira')` | pominięte z całym poddrzewem |
| `Zadań-podzadań z własnymi podzadaniami (spłaszczone pod przodka)` | Asana zagnieżdża głębiej niż Jira; ich podzadania trafiają pod najbliższego przodka, który nie jest Sub-taskiem (dawniej osobna linia `[INFO]` przy każdym takim zadaniu) |
| `Statusy OK` / `Statusy błąd` | ustawione i nieosiągalne statusy |

### Uruchomienie

```bash
python asana_jira_sync.py --dry-run --limit 1 --project-keys KLUCZ   # podgląd bez zapisu
python asana_jira_sync.py --limit 1 --project-keys KLUCZ             # faktyczny zapis, 1 projekt
python asana_jira_sync.py                                             # wszystkie projekty z arkusza
python asana_jira_sync.py --shard 1/3                                 # 1. z 3 równoległych procesów
```

Na końcu skrypt wypisuje podsumowanie z podziałem na typy, np.
`Utworzono: 186 (Epiki: 6, Taski: 60, Sub-taski: 120)`, tak samo zaktualizowane,
komentarze, załączniki, statusy i błędy, oraz listę sekcji Asany bez wpisu
w `section_status_map.json`. Pominięte są rozbite na przyczyny:

| Przyczyna | Znaczenie |
|---|---|
| bez zmian od ostatniego syncu | normalne; w tym podzadania widoczne też na liście projektu, obsłużone razem z rodzicem |
| usunięte w Jirze (`deleted_in_jira`) | nie będą odtwarzane; jeśli projekt w Jirze utworzono od nowa, wyczyść jego wpisy w stanie (`clean_stale_task_state.py`) |
| kandydaci na Epic do `retrofit_epics.py` | zadanie powinno być Epikiem, a jego podzadania już istnieją (sekcja 15a) |

`--dry-run` pokazuje, co **by** zrobił skrypt (odczyt jest bezpieczny, więc
nawet w dry-run zobaczysz realną liczbę komentarzy/załączników do przeniesienia),
bez faktycznego tworzenia/zmieniania niczego w Jirze.

**Diagnostyka:** ustaw `DEBUG_SYNC=1` przed komendą, żeby zobaczyć szczegółowe
informacje o każdym zadaniu (dokładne dane wysyłane do Jiry, sekcję/status,
porównania znaczników czasu) — przydatne, gdy coś nie działa zgodnie z oczekiwaniami:
```bash
DEBUG_SYNC=1 python asana_jira_sync.py --limit 1 --project-keys KLUCZ
```

### Pliki tego pakietu

| Plik | Rola |
|---|---|
| `asana_jira_sync.py` | główny skrypt synchronizujący Asana → Jira |
| `run_full_pipeline.py` | orkiestrator całego pipeline'u, równoległość, zbiorcze podsumowania (sekcja 11a) |
| `pipeline_summary.py` | wspólny moduł: zapis liczb i szczegółów błędów dla orkiestratora |
| `detect_epic_candidates.py` | wykrywa kandydatów na Epic przed synchronizacją |
| `epic_candidates.json` | **automatycznie tworzona** lista kandydatów na Epic per projekt |
| `copy_project_cross_instance.py` | kopia projektu między dwiema instancjami Jiry (sekcja 4a) |
| `copy_project.py` | pełna kopia JEDNEGO istniejącego projektu Jiry, łącznie z tablicami/kolumnami (sekcja 4a) |
| `fix_board_filter_permissions.py` | udostępnia filtry tablic projektowi, gdy zespół nie widzi tablic (sekcja 4a) *(skrypt nie jest dołączony do tego repozytorium)* |
| `audit_hierarchy.py` | audyt i naprawa miejsca zgłoszeń w hierarchii względem drzewa Asany (sekcja 15e) |
| `compare_project_totals.py` | pełne porównanie projektu Asana vs Jira, wszystkie poziomy, po identyfikatorach (sekcja 14a) |
| `jira_asana_sync.py` | kierunek zwrotny, Jira → Asana (sekcja 12) |
| `list_asana_sections.py` | wypisuje sekcje JEDNEGO projektu Asany (do `section_status_map.json`) |
| `list_all_asana_sections.py` | wypisuje unikalne sekcje ze WSZYSTKICH projektów w arkuszu naraz, z porównaniem do już zmapowanych |
| `check_asana_token.py` | diagnostyka: sprawdza czy ASANA_TOKEN jest ważny i ma dostęp do workspace'u (niezależnie od konkretnego projektu) |
| `section_status_map.json` | mapowanie: sekcja Asany → status(y) Jira (uzupełniasz ręcznie) |
| `generate_user_map.py` | generuje `user_map.json` (dopasowanie po e-mailu) |
| `list_asana_members.py` | wypisuje członków/gości workspace'u Asany z ich gid |
| `get_group_members.py` | wypisuje członków grupy Jira z ich accountId |
| `user_map.json` | mapowanie: Asana user gid → Jira accountId |
| `check_required_fields.py` | diagnostyka: jakie pola są wymagane przy tworzeniu zadania |
| `list_project_statuses.py` | diagnostyka: jakie statusy faktycznie istnieją w danym projekcie |
| `check_task_section.py` | diagnostyka: sprawdza sekcję zadania dwoma niezależnymi zapytaniami |
| `count_state_entries.py` | diagnostyka: liczy/pokazuje wpisy w `task_sync_state.json` dla projektu |
| `count_migrated_tasks.py` | liczy zadania w Jirze (i opcjonalnie załączniki), porównuje z prawdziwą liczbą w Asanie |
| `find_duplicate_tasks.py` | wykrywa zdublowane tytuły zadań w obrębie projektu (tylko wykrywanie) |
| `resolve_duplicates.py` | wykrywa I bezpiecznie usuwa osierocone duplikaty zadań (sekcja 14) |
| `find_duplicate_comments_attachments.py` | wykrywa zdublowane komentarze/załączniki WEWNĄTRZ pojedynczych zadań |
| `check_duplicate_sheet_rows.py` | diagnostyka: klucze Jiry z więcej niż jednym wierszem w arkuszu |
| `diagnose_jira_asana_gap.py` | diagnostyka: które konkretnie tytuły istnieją tylko w Jirze / tylko w Asanie |
| `trace_task_origins.py` | diagnostyka: przy dwóch źródłach Asany pod jednym kluczem Jiry, ustala pochodzenie każdego zadania *(skrypt nie jest dołączony do tego repozytorium)* |
| `clean_stale_task_state.py` | usuwa z `task_sync_state.json` wpisy wskazujące na nieistniejące już zadania Jiry |
| `repair_state_file.py` | naprawia uszkodzony (np. po `Ctrl+C`) `task_sync_state.json` |
| `repair_missing_state_entries.py` | odtwarza brakujące wpisy w stanie, dopasowując zadania po tytule (sekcja 14e) |
| `mark_deleted_as_skipped.py` | oznacza zadania usunięte ręcznie w Jirze jako trwale pomijane (sekcja 14e) |
| `refresh_state_flags.py` | odświeża flagi hierarchii w stanie na podstawie Jiry (sekcja 15) |
| `retrofit_epics.py`, `convert_to_epic.py` | przebudowa hierarchii już zmigrowanych zadań (sekcja 15) |
| `audit_epic_candidates.py`, `revert_false_epics.py`, `resolve_deep_chains.py` | wykrywanie i naprawa fałszywych Epików (sekcja 15) |
| `compare_project_totals.py` | pełne porównanie projektów Asana vs Jira, wszystkie poziomy (sekcja 14a) |
| `close_children_of_done.py` | zamyka w Jirze (opcjonalnie też w Asanie) zadania otwarte pod ukończonymi (sekcja 14f) |
| `fix_stale_resolution.py` | czyści pozostawione Rozwiązanie w otwartych zgłoszeniach (sekcja 14g) |
| `relink_state.py` | odtwarza zgubione powiązania, wykrywa serie duplikatów (sekcja 15f) |
| `resolve_relink_duplicates.py` | usuwa duplikaty z raportu relink, zostawia najstarszą kopię (sekcja 15g) |
| `handle_migrated_to_jira.py` | porządkuje zadania z dopiskiem "migriert zu Jira" (sekcja 15h) |
| `repair_batch.py` | uruchamia 15f-15h dla wielu projektów z tabelą podsumowania (sekcja 15i) |
| `list_project_statuses.py` | statusy projektu dla każdego typu, z klucza albo linku (sekcja 4a) |
| `remove_status_from_column.py` | zdejmuje status z kolumny tablic (sekcja 4a) |
| `copy_project_cross_instance.py` | kopia (także przyrostowa i z podziałem) projektu z innej instancji Jiry (sekcja 4a) |
| `sync_exclude.txt`, `protected_projects.txt` | czasowe i trwałe wyłączenia projektów (sekcja 11) |
| `export_source_workflows.py` | eksport workflow i statusów z innej instancji Jiry do YAML i mapy statusów (sekcja 4a) *(skrypt nie jest dołączony do tego repozytorium)* |
| `build_jira_schema.py` | konfiguracja Jiry z pliku YAML (sekcja 17) |
| `set_transition_groups.py` | kto może wykonać przejście w workflow (sekcja 17j) |
| `parse_jira_pdfs.py`, `parse_jira_workflow.py`, `parse_jira_pptx.py`, `merge_jira_sources.py` | budowa pliku YAML z dokumentacji klienta (sekcja 17) |
| `create_epics_stories_from_sheet.py`, `export_stories_to_sheet.py` | Epiki i Story z arkusza zakresu i eksport z powrotem (sekcja 20) |
| `section_status_map.<KLUCZ>.json` | mapa statusów dla jednego projektu (sekcja 11) |
| `notify.py` | log do pliku + opcjonalne powiadomienie na Slack |
| `clone_project.py` | samodzielna kopia projektu z kopiami schematów nazwanymi od nowego projektu (część I, „Kopia projektu”) |
| `task_sync_state.json` | **automatycznie tworzony** stan synchronizacji (obu kierunków) — nie kasować |
| `workflow_graph_cache.json` | **automatycznie tworzony** cache mapy przejść workflow — bezpieczny do skasowania |

## 11a. Kolejność kroków i cały pipeline jednym poleceniem (`run_full_pipeline.py`)

Kroki muszą iść w tej kolejności, bo każdy zakłada, że poprzedni się skończył:

| Krok | Klucz | Skrypt | Co robi |
|---|---|---|---|
| 1 | `projects` | `jira_sync.py` | tworzy projekty z arkusza |
| 2 | `columns` | `browser_automation/bulk_configure_columns.py` | tworzy kolumny tablic (wzorzec z `.env` albo z kolumny `board_template_id`) |
| 3 | `statuses` | `browser_automation/run_column_assignments.py` | przypisuje statusy do kolumn (sekcja 11b) |
| 4 | `epics` | `detect_epic_candidates.py` | wykrywa kandydatów na Epic (sekcja 11) |
| 5 | `sync` | `asana_jira_sync.py` | synchronizuje zadania na gotowe, skonfigurowane tablice |

Opcjonalnie potem, w miarę bieżącej pracy: `python jira_asana_sync.py`
(kierunek zwrotny, sekcja 12). Nie jest częścią orkiestratora.

### Orkiestrator

`run_full_pipeline.py` uruchamia powyższe skrypty jako osobne procesy, w tej
kolejności, i zatrzymuje się na pierwszym kroku zakończonym błędem krytycznym.
Nie zmienia logiki żadnego kroku.

```bash
python run_full_pipeline.py                            # cały pipeline, sekwencyjnie
python run_full_pipeline.py --parallel 3               # każdy krok jako 3 równoległe procesy
python run_full_pipeline.py --dry-run                  # podgląd (krok 3 pominięty: nie ma trybu podglądu)
python run_full_pipeline.py --limit 5                  # pierwsze 5 projektów w krokach, które to wspierają
python run_full_pipeline.py --only sync --parallel 3   # tylko jeden krok (--only przyjmuje dokładnie jeden)
python run_full_pipeline.py --skip columns --skip statuses
python run_full_pipeline.py --skip-columns             # pomiń kroki 2-3
python run_full_pipeline.py --skip-sync                # pomiń kroki 4-5
```

**Równoległość (`--parallel N`)** dotyczy każdego kroku osobno: orkiestrator
uruchamia N procesów z automatycznym `--shard K/N` i czeka na wszystkie, zanim
przejdzie do następnego kroku. Procesy dzielą ten sam limit zapytań Jiry i
Asany, więc większe N nie przyspiesza proporcjonalnie. Zalecane 3-5. Przy
`--parallel` każda linia wyjścia ma prefiks procesu (`[2/3] ...`).

**Kody wyjścia:** `0` sukces, `2` częściowe niepowodzenie (tylko krok 1: pipeline
idzie dalej dla projektów, które powstały), każdy inny kod zatrzymuje pipeline.
Kolejne uruchomienie może pominąć gotowe kroki przez `--only` / `--skip`.

**Uwaga o kroku 3:** jeśli wymaga ręcznego zalogowania w przeglądarce (wygasła
sesja), uruchamiaj go bez `--parallel`, bo prompty z kilku procesów wymieszają
się w jednej linii. Najpierw odśwież sesję: `python browser_automation/save_login_session.py`.

**Podsumowanie po każdym kroku** trafia na Slack jako jedna wiadomość za
wszystkie procesy: status, czas, zsumowane liczby i lista błędów (sekcja 13).

## 11b. Automatyczne przypisywanie statusów do kolumn (`run_column_assignments.py`)

Jira **nie ma publicznego, udokumentowanego API** do zapisu przypisania
statusów do kolumn tablicy — stąd wcześniejsze (nieudane) próby przez
symulację przeciągania myszą. Znaleźliśmy jednak działający **wewnętrzny**
endpoint, którego używa samo UI Jiry (podejrzany przez DevTools -> Network
podczas ręcznego przeciągania):

- **Odczyt** — GraphQL, persisted query `ColumnsSettingsPageNewContentQuery`.
- **Zapis** — `PUT /rest/greenhopper/1.0/rapidviewconfig/columns` (stare,
  nieudokumentowane API "GreenHopper", poprzednik dzisiejszej nazwy
  "Jira Software").

**Ważne zastrzeżenie:** to NIEOFICJALNE API. Atlassian nie gwarantuje jego
stabilności — może się zmienić bez ostrzeżenia przy aktualizacji Jiry
(zwłaszcza `atl-client-version` i `x-experimentalapi`, przywiązane do
konkretnego builda frontendu). Jeśli po jakiejś przyszłej aktualizacji Jiry
skrypt zacznie zwracać błąd 400/415 z GraphQL, trzeba będzie świeżo
przechwycić te dwie wartości z Network (patrz komentarze w
`assign_statuses_to_columns.js`) i podmienić w skrypcie.

Wymaga tego, że te endpointy używają **sesji przeglądarki** (ciasteczka,
token CSRF), nie zwykłego tokenu API — dlatego wykonanie odbywa się przez
Playwright z zapisaną sesją logowania (`auth_state.json`), a nie przez
`requests` z tokenem, jak reszta skryptów.

### Pliki tego mechanizmu

| Plik | Rola |
|---|---|
| `assign_statuses_to_columns.js` | biblioteka JS (funkcje `assignStatusesToColumns`, `assignStatusesToManyBoards`) — może być też wklejona ręcznie w konsoli przeglądarki, niezależnie od Pythona |
| `generate_column_assignments.py` | generuje listę {boardId, projectKey, mapping} dla projektów z arkusza (tylko odczyt, zwykłe API) |
| `run_column_assignments.py` | **główny skrypt end-to-end** — łączy oba powyższe, wykonuje przez Playwright, bez ręcznej interakcji |

### Uruchomienie

```bash
python save_login_session.py                                   # jeśli sesja wygasła
python run_column_assignments.py --limit 1 --project-keys KLUCZ  # test na 1 projekcie
python run_column_assignments.py                                 # wszystkie projekty
```

Wynik zapisywany do `column_assignment_report.json`.

**Alternatywa bez Playwrighta:** można też wkleić `assign_statuses_to_columns.js`
ręcznie w konsoli przeglądarki (F12) i wywołać funkcje bezpośrednio — przydatne
do szybkiego, jednorazowego testu. Żeby nie wklejać za każdym razem, zapisz
skrypt jako DevTools Snippet (Sources -> Snippets -> zapisuje się na stałe
w przeglądarce, uruchamiane przez Ctrl+Enter).

## 12. Kierunek zwrotny synchronizacji (`jira_asana_sync.py`, Jira → Asana)

Osobny skrypt (nie tryb w `asana_jira_sync.py`, bo iteruje inaczej: po już
powiązanych parach z `task_sync_state.json` — WSPÓLNYM plikiem z `asana_jira_sync.py`
— nie po zadaniach Asany).

### Co synchronizuje

- Tytuł, opis (konwersja ADF → HTML), termin, przypisanego (przez ODWRÓCONY
  `user_map.json`, z fallbackiem: jeśli oryginalny assignee nie może zostać
  przypisany w Asanie, aktualizacja przechodzi bez niego zamiast się wywalać).
- **Status → sekcja** — przez ODWRÓCONĄ `section_status_map.json`. Ponieważ
  jedna sekcja Asany może odpowiadać wielu statusom Jiry (i odwrotnie, wiele
  sekcji temu samemu statusowi), skrypt **próbuje po kolei wszystkich
  kandydatów**, aż znajdzie taki, który faktycznie istnieje w KONKRETNYM
  projekcie Asany — nie zakłada, że pierwszy z listy zawsze pasuje. Wpisy specjalne
  (`__ukonczone__`, `__szablon__`) są pomijane, a z rozszerzonych wpisów
  (`{"status": ..., "labels": ...}`) brany jest `status`.
- Nowe komentarze i załączniki z Jiry do Asany.

**Rozstrzyganie konfliktów:** proste porównanie znacznika czasu ostatniej
modyfikacji w Jirze (`updated`) — **nie ma pełnego mergowania pól**, jeśli
oba systemy zmieniły to samo zadanie w tym samym czasie; wygrywa stan z Jiry.

### Uruchomienie

```bash
python jira_asana_sync.py --dry-run --limit 1   # podgląd, bez --project-keys (patrz niżej)
python jira_asana_sync.py --limit 1
python jira_asana_sync.py
DEBUG_SYNC=1 python jira_asana_sync.py --limit 1   # ze szczegółową diagnostyką
```

**Ważne o `--project-keys`:** jeśli podasz konkretne klucze tą flagą, skrypt
NIE ma skąd wziąć `asana_project_link`/nazwy projektu z arkusza dla tych
kluczy — synchronizacja statusu → sekcja **nie zadziała** w tym trybie
(dostaniesz czytelne ostrzeżenie). Do testowania na 1 projekcie użyj
`--limit 1` **bez** `--project-keys` — wtedy skrypt bierze pierwszy wiersz
z arkusza razem z jego linkiem.

**Kolejność przy używaniu obu kierunków:** uruchamiaj naprzemiennie
(`asana_jira_sync.py`, potem `jira_asana_sync.py`), nie równolegle —
unikniesz sytuacji, gdzie oba nadpisują się nawzajem w trakcie działania.

## 13. Dodatkowe funkcje

- **Log do pliku + podsumowanie na Slack** — moduł `notify.py`, używany przez
  `jira_sync.py`, `asana_jira_sync.py` i `jira_asana_sync.py`. Log trafia do
  `logs/<skrypt>_<data-godzina>.log` automatycznie (nic nie trzeba włączać).
  Slack jest opcjonalny — ustaw w `.env`:
  ```
  SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
  ```
  Bez tego skrypty po prostu nie wysyłają nic na Slacka (bez błędu). E-mail
  nie został zaimplementowany (wymaga danych SMTP).
- **Zbiorcze podsumowania w pipeline**: przy uruchomieniu przez
  `run_full_pipeline.py` poszczególne skrypty nie wysyłają własnych wiadomości
  (rozpoznają to po zmiennej środowiskowej `PIPELINE_RUN=1`, którą ustawia
  orkiestrator). Zamiast tego po każdym kroku przychodzi jedna wiadomość za
  wszystkie procesy równoległe:
  ```
  ⚠️ 1/5 Tworzenie projektów zakończony z częściowymi błędami
  Procesy: 3 | Czas: 4min 12s | Kody wyjścia: [0, 2, 0]
  utworzone: 15 | zaktualizowany opis: 3 | pominięte: 460 | błędy: 1 | ...

  Błędy (1):
  • DUPKEY Projekt Duplikat: 400 A project with that key already exists

  Pełny raport: logs/pipeline_projects_20260927_182054_raport.txt
  ```
  Każda liczba jest sumowana osobno ze wszystkich procesów (dla synchronizacji
  zadań m.in. utworzone i zaktualizowane Epiki, Taski i Sub-taski, komentarze,
  załączniki). Identyczne błędy są scalane z licznikiem (`60x ... 429`). Na
  Slacku widać do 20 błędów i 10 ostrzeżeń (stałe `SLACK_MAX_ERRORS` /
  `SLACK_MAX_WARNINGS` w `run_full_pipeline.py`), pełna lista trafia do
  `logs/pipeline_<krok>_<data>_raport.txt`. Jeśli proces padł (np. traceback),
  wiadomość zawiera ostatnie linie jego wyjścia. Na koniec całego pipeline'u
  przychodzi krótka wiadomość 🏁 z łącznym czasem.

  Liczby i szczegóły błędów raportują `jira_sync.py`, `bulk_configure_columns.py`,
  `detect_epic_candidates.py` i `asana_jira_sync.py`. Krok 3 raportuje status,
  czas i ewentualny crash. Żeby inny skrypt dołączył do podsumowań, wystarczy
  `record_error(...)` / `record_warning(...)` w miejscach łapania błędów i
  `write_shard_summary(...)` na końcu `main()` (moduł `pipeline_summary.py`).
- **Konfiguracja kolumn (`bulk_configure_columns.py`)** rozpoznaje wygasłą sesję
  przeglądarki (przerywa przebieg z komunikatem, zamiast timeoutu na każdym
  projekcie), pomija projekty team-managed, wybiera tablicę należącą do danego
  projektu i podaje adres, gdy Jira przekierowała gdzie indziej. Przy timeoucie
  zapisuje zrzut `error_<KLUCZ>.png`. Czas oczekiwania: `BULK_COLUMNS_TIMEOUT_MS`
  w `.env` (domyślnie 30000). Jeśli timeout zostaje mimo świeżej sesji, Jira
  prawdopodobnie zmieniła wygląd strony; selektory są w słowniku `SELECTORS`.
- **Aktualizacja istniejących projektów** — `jira_sync.py` nie tylko pomija
  już istniejące projekty, ale aktualizuje ich **opis**, jeśli różni się od
  tego w arkuszu/Asanie (patrz podsumowanie: "Zaktualizowany opis").

## 14. Weryfikacja i sprzątanie po migracji

Po głównej synchronizacji (sekcja 11), zwłaszcza po wielokrotnych, przerywanych
przebiegach, warto zweryfikować rezultat i posprzątać ewentualne duplikaty
powstałe przez utratę/uszkodzenie `task_sync_state.json` w trakcie wcześniejszych
prób. Poniższe narzędzia są w pełni niezależne od głównej synchronizacji —
nigdy nie modyfikują `task_sync_state.json` (poza `clean_stale_task_state.py`
i `repair_state_file.py`, jawnie do tego przeznaczonymi).

### 14a. Ile zadań faktycznie jest w Jirze (`count_migrated_tasks.py`)

```bash
python count_migrated_tasks.py                     # łączna liczba zadań + per projekt
python count_migrated_tasks.py --summary-only       # tylko łączna liczba
python count_migrated_tasks.py --with-attachments   # dodatkowo liczy załączniki (wolniejsze)
python count_migrated_tasks.py --compare-asana       # porównuje z PRAWDZIWĄ liczbą w Asanie
python count_migrated_tasks.py --compare-full        # szersze porównanie z Asaną (szczegóły: --help)
```

Liczy przez oficjalny endpoint `/search/approximate-count` (jedno zapytanie na
projekt — Jira Cloud nie ma prostszego sposobu policzenia zadań). `--compare-asana`
niezależnie weryfikuje wynik przez Asanę i wskazuje projekty z rozbieżnością
(zwykle sygnał pozostałych duplikatów). **Jeśli projekt ma więcej niż jeden
wiersz w arkuszu** (patrz `check_duplicate_sheet_rows.py` niżej) — liczby ze
WSZYSTKICH pasujących wierszy są sumowane, a sam fakt wielu wierszy jest
głośno flagowany jako wymagający ręcznej weryfikacji, nie cicho ignorowany.

**Pełne porównanie jednego projektu (`compare_project_totals.py`).** Narzędzia
wyżej liczą zadania najwyższego poziomu i bezpośrednie podzadania, więc nie widzą
braków od drugiego poziomu w dół ani zadań w złym miejscu. Ten skrypt liczy
wszystkie poziomy rekurencyjnie, każde zadanie raz, i porównuje po identyfikatorach
ze stanu (zduplikowane tytuły nie mylą wyniku). Tylko odczyt:
```bash
python compare_project_totals.py PROJ5
python compare_project_totals.py PROJ5,PROJ11 --details --attachments   # z treścią i liczbą załączników
python compare_project_totals.py --all --top-level                        # szybki przegląd całego arkusza (bez podzadań)
python compare_project_totals.py --all --shard 1/3                        # pełne porównanie wszystkich, równolegle (osobny CSV na część)
```
Kategorie rozbieżności: niezmigrowane (z poziomem zagnieżdżenia), w stanie ale
brak w Jirze, oznaczone jako usunięte, w Jirze bez powiązania, brak w Asanie.
Szczegóły w `compare_project_totals_report.csv`. Jedno zapytanie do Asany na
zadanie, więc duży projekt zajmuje kilka minut.

Jak czytać wynik:

| Wzorzec | Znaczenie | Naprawa |
|---|---|---|
| niezmigrowane na poziomie 1 = bez powiązania (np. 742 i 742) | stan **zgubił powiązania**, zgłoszenia są w Jirze | 15f; **nie synchronizować** przed naprawą (utworzy duplikaty) |
| Jira ma więcej niż Asana, dużo bez powiązania | ponowne synchronizacje już utworzyły **duplikaty** | 15f i 15g |
| niezmigrowane tylko od poziomu 2, bez powiązania 0 | brak głębokich podzadań (stary błąd rekurencji) | 15e, potem synchronizacja |
| W stanie, ale brak w Jirze | zgłoszenia usunięte albo przeniesione w Jirze | 14h |
| kilka bez powiązania, bez odpowiednika w Asanie | zgłoszenia utworzone przez zespół wprost w Jirze | nic (synchronizacja ich nie dotyka) |

Zgłoszenia skopiowane z innej instancji Jiry są pomijane (linia `Pomijam N
zgłoszeń skopiowanych z innej instancji Jiry`), a projekty z
`protected_projects.txt` w ogóle nie są widoczne.

### 14b. Wykrywanie i usuwanie zdublowanych zadań (`resolve_duplicates.py`)

Duplikaty powstają, gdy `task_sync_state.json` straci wpis dla już
zsynchronizowanego zadania (np. po `Ctrl+C` w złym momencie, przed naprawą
atomowego zapisu — sekcja 11) — kolejny przebieg synchronizacji tworzy
WTEDY drugą kopię tego samego zadania w Jirze, bo nie znajduje śladu
pierwszej.

**Wykrywanie:** grupuje zadania w obrębie JEDNEGO projektu po identycznym
tytule, potem klasyfikuje każdą grupę do jednej z trzech kategorii,
krzyżując z `task_sync_state.json` oraz porównując treść (opis, załączniki,
komentarze):

- **Bezpieczne do usunięcia** — dokładnie jeden klucz z grupy jest aktualnie
  śledzony w stanie (reszta to jego osierocone kopie o identycznej treści),
  ALBO żaden nie jest śledzony, ale wszystkie kopie mają identyczną treść
  (wtedy zachowywana jest kopia z najwyższym numerem — najnowsza).
- **Do przeglądu** — treść się różni w sposób, którego nie da się jednoznacznie
  rozstrzygnąć automatycznie. To jedyna kategoria wymagająca ręcznej uwagi;
  w praktyce zwykle to niewielki ułamek wszystkich duplikatów.
- **Potwierdzone różne (bez akcji)** — każdy klucz w grupie ma WŁASNY,
  niezależny wpis w stanie, czyli z definicji odpowiada innemu zadaniu w
  Asanie (np. powtarzające się nazwy szablonowych pozycji w różnych fazach
  tego samego wdrożenia, albo cykliczne zadania jak "Weekly Statusbericht").
  Czysto informacyjne.

```bash
python resolve_duplicates.py                    # wszystkie projekty, tylko raport
python resolve_duplicates.py --project KLUCZ     # tylko jeden projekt
python resolve_duplicates.py --delete            # wszystkie projekty, usuwa (z JEDNYM potwierdzeniem)
```

**Równolegle** (skanowanie, najwolniejsza część — przechodzi przez każde
zadanie w każdym projekcie):
```bash
python resolve_duplicates.py --shard 1/5 &
python resolve_duplicates.py --shard 2/5 &
python resolve_duplicates.py --shard 3/5 &
python resolve_duplicates.py --shard 4/5 &
python resolve_duplicates.py --shard 5/5 &
wait
python resolve_duplicates.py --collect            # zbiera wyniki wszystkich shardów, raport
python resolve_duplicates.py --collect --delete   # jak wyżej, plus usuwanie
```
`--delete` **nie łączy się** z `--shard` bezpośrednio (świadome ograniczenie
bezpieczeństwa — usuwanie to zawsze jeden, końcowy krok z jednym potwierdzeniem,
po `--collect`).

Usuwanie idzie przez oficjalny **Bulk Delete API** Jiry (do 1000 zadań na
zapytanie, asynchroniczne — skrypt odpytuje o postęp aż do zakończenia,
maks. godzinę; dla dużych partii Jira usuwa zadania jednowątkowo po swojej
stronie, więc to bywa wolne niezależnie od naszego podejścia).

Pełny raport (wszystkie trzy kategorie, bez obcinania) zawsze zapisywany do
`duplicate_resolution_report.json`.

### 14c. Zdublowane komentarze/załączniki WEWNĄTRZ zadania

Osobny, subtelniejszy przypadek: zadanie może mieć tylko JEDEN klucz Jiry
(żaden duplikat całego zadania), a mimo to mieć te same komentarze/załączniki
dodane wielokrotnie do TEGO SAMEGO zadania — bo stan śledzi OSOBNO, które
konkretne komentarze/załączniki już zsynchronizowano.

```bash
python find_duplicate_comments_attachments.py --project KLUCZ
python find_duplicate_comments_attachments.py               # wszystkie projekty
```

Tylko wykrywanie — nic nie usuwa (raport: `duplicate_comments_attachments_report.json`).

### 14d. Diagnostyka dla nietypowych przypadków

- **`check_duplicate_sheet_rows.py [KLUCZ]`** — czy jakiś klucz Jiry ma więcej
  niż jeden wiersz w arkuszu (z różnymi linkami Asany). Uruchamiane
  automatycznie na starcie `jira_sync.py` (sekcja 9) — ten skrypt to
  ręczna wersja do sprawdzenia w dowolnym momencie.
- **`diagnose_jira_asana_gap.py KLUCZ`** — dla jednego projektu, pokazuje
  DOKŁADNIE które tytuły zadań istnieją tylko w Jirze (nie tylko liczbę,
  jak `--compare-asana`) — rozróżnia "prawdziwe nadmiarowe duplikaty" od
  "unikalne zadanie, którego odpowiednik zniknął z Asany" (np. usunięty
  po zakończeniu etapu — to może być oczekiwane, nie błąd).
- **`trace_task_origins.py KLUCZ`** *(skrypt nie jest dołączony do tego repozytorium)* — gdy jeden klucz Jiry ma dwa różne
  źródła Asany w arkuszu (patrz `check_duplicate_sheet_rows.py`), ustala,
  ile zadań w Jirze pochodzi z którego źródła, i czy któreś nie pasuje do
  żadnego z obecnie istniejących zadań w obu.

### 14e. Niespójności między stanem a Jirą

- **`mark_deleted_as_skipped.py`**: zadania usunięte ręcznie w Jirze oznacza
  w stanie flagą `deleted_in_jira`, dzięki czemu synchronizacja nie próbuje ich
  aktualizować (błędy `404`) ani tworzyć ponownie. Traktuje usunięcie jako
  świadomą decyzję. `asana_jira_sync.py` ustawia tę flagę także sam, gdy przy
  aktualizacji dostanie `404`.
- **`repair_missing_state_entries.py`**: gdy zadanie istnieje w Jirze, ale
  stan nie ma dla niego wpisu (co przy następnej synchronizacji skończyłoby się
  duplikatem), odtwarza wpis, dopasowując zadania po tytule. Dla większych
  przypadków (zgubione powiązania, serie duplikatów) używaj `relink_state.py`
  i `resolve_relink_duplicates.py` (sekcje 15f-15g).
- **`clean_stale_task_state.py`**: odwrotny przypadek: usuwa ze stanu wpisy
  wskazujące na zadania, których w Jirze już nie ma.
- **`diagnose_cross_project_contamination.py`**: wykrywa zadania, które
  w stanie danego projektu mają klucz Jiry z **innego** projektu.
- **`diagnose_missing_projects_in_state.py`**: projekty z arkusza, dla których
  stan nie ma żadnych wpisów (np. synchronizacja nigdy się dla nich nie odbyła).
- **`diagnose_subtask_gap.py`**, **`diagnose_attachment_gap.py`**: jak
  `diagnose_jira_asana_gap.py`, ale dla podzadań i dla załączników.
- **`diff_two_issues.py`**: porównuje dokładną treść dwóch zgłoszeń Jiry
  (przydatne przy podejrzanych duplikatach z kategorii "Do przeglądu").

Opcje każdego z nich: `--help`.

### 14f. Podzadania pod zakończonymi zadaniami (`close_children_of_done.py`)

Zamyka w Jirze wszystko, co jest otwarte pod **ukończonym** zadaniem. Domyślnie
tylko raport.

| Opcja | Działanie |
|---|---|
| (bez opcji) | raport: otwarte zgłoszenia pod ukończonymi zadaniami |
| `--apply` | zamyka je **w Jirze** (status końcowy: `__ukonczone__` z mapy projektu albo głównej, inaczej `Fertig`; albo `--done-status`) |
| `--apply --also-asana` | dodatkowo odhacza odpowiadające im zadania **w Asanie** (tylko po udanej zmianie w Jirze) |
| `--source asana` (domyślnie) | drzewo i "ukończone" według Asany; spłaszczenie w Jirze nie ma znaczenia; wymaga projektu w arkuszu |
| `--source jira` | hierarchia i status końcowy według Jiry |
| `--only-keys A-1,A-2` | test na wybranych zgłoszeniach |

```bash
python close_children_of_done.py --project-keys PROJ4                # raport
python close_children_of_done.py --project-keys PROJ4 --apply        # tylko Jira
```
Od wprowadzenia dziedziczenia statusów (sekcja 11) synchronizacja robi to sama;
narzędzie służy głównie do kontroli (oczekiwane: 0 do zamknięcia). Nie używaj
`--source jira` w projektach skopiowanych z innej instancji Jiry: ich statusy
wynikają ze źródła.

### 14g. Przekreślone klucze w otwartych zgłoszeniach (`fix_stale_resolution.py`)

Zgłoszenie ma status otwarty, ale ustawione pole **Rozwiązanie** (Resolution),
więc Jira pokazuje je jako zamknięte (przekreślony klucz, wypada z filtrów
"unresolved"). Powstaje, gdy zgłoszenie było zamknięte, a potem przestawione
z powrotem (np. przez synchronizację), a przejście w workflow nie czyści
rozwiązania.
```bash
python fix_stale_resolution.py --project-keys PROJ3,PROJ4           # raport z podziałem na typy
python fix_stale_resolution.py --project-keys PROJ3,PROJ4 --apply   # czyszczenie, bez powiadomień
```
Czyszczenie idzie przez edycję zgłoszenia, więc pole Resolution musi być na
ekranie edycji danego typu (często nie ma go dla Epików). Skrypt sprawdza to
dla każdego typu i typy bez pola tylko zgłasza. Wtedy administrator może
tymczasowo dodać Resolution do ekranu edycji. **Trwała naprawa** jest
w workflow: przejścia wychodzące ze statusów końcowych powinny mieć funkcję
końcową "Update Issue Field: Resolution = None".

### 14h. Zgłoszenia usunięte przez zespół w Jirze

Gdy zespół usuwa w Jirze zadania, które nadal istnieją w Asanie (np. sprzątanie
starych szablonów), porównanie pokazuje je jako "W stanie, ale zgłoszenia NIE MA
w Jirze". Najpierw sprawdź, czy są usunięte (404), czy przeniesione do innego
projektu (Jira zachowuje stary klucz jako przekierowanie):
```bash
for k in KLUCZ-2 KLUCZ-519; do
  curl -s -u "$JIRA_EMAIL:$JIRA_API_TOKEN" "$JIRA_BASE_URL/rest/api/3/issue/$k?fields=project" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print('$k ->', d.get('key', 'USUNIĘTE (404)'), d.get('fields', {}).get('project', {}).get('key', ''))"
done
```
**Usunięte** oznacz w stanie, żeby synchronizacja ich nie odtwarzała (lista
z raportu porównania, każde sprawdzane jeszcze raz w Jirze):
```bash
python3 - << 'PY'
import csv, shutil
from datetime import datetime
import asana_jira_sync as ajs
KEY = "PROJ3"
rows = [r for r in csv.DictReader(open("compare_project_totals_report.csv", encoding="utf-8"))
        if r["projekt"] == KEY and r["kategoria"] == "brak_w_jirze"]
shutil.copy2(ajs.STATE_FILE, f"task_sync_state.backup_{datetime.now():%Y%m%d_%H%M%S}_usuniete_{KEY}.json")
state = ajs.load_state(); ps = state[KEY]; marked = []
for r in rows:
    gid, key = r["asana_gid"], r["jira_key"]
    resp = ajs.request_with_retry("GET", f"{ajs.JIRA_BASE_URL}/rest/api/3/issue/{key}", auth=ajs.JIRA_AUTH, timeout=ajs.REQUEST_TIMEOUT)
    if resp.status_code == 404 and gid in ps and ps[gid].get("jira_key") == key:
        ps[gid]["deleted_in_jira"] = True
        marked.append(key)
ajs.save_state({KEY: ps})
print(f"Oznaczono jako usunięte w Jirze: {len(marked)} z {len(rows)}")
PY
```
Porównanie pokaże je potem w "Oznaczone jako usunięte w Jirze (celowo pomijane)".
**Przeniesione** do innego projektu synchronizacja nadal aktualizowałaby pod
starym kluczem (czyli w nowym projekcie); jeśli to niepożądane, odłącz je
(`deleted_in_jira` albo `jira_master`). Zespołom warto przekazać: zadanie
usunięte w Jirze najlepiej usunąć też w Asanie albo dopisać tam "migriert zu Jira".

## 15. Naprawa hierarchii w już zmigrowanych projektach

Hierarchia jest ustawiana przy tworzeniu zgłoszeń, więc błąd w wykryciu
kandydatów albo zmiana struktury w Asanie wymaga osobnej naprawy. Wszystkie
narzędzia z tej sekcji mają tryb raportu (domyślny) i zmieniają Jirę dopiero
z `--apply`.

### 15a. Projekty zmigrowane przed obsługą Epików (`retrofit_epics.py`)

Konwertuje zadania, które zostały zmigrowane jako zwykłe Taski, na pełną
strukturę: Task → Epic, jego Sub-taski → Taski pod Epikiem, wnuki → Sub-taski.
`asana_jira_sync.py` sam kieruje do tego skryptu, gdy trafi na kandydata na
Epic z już zmigrowanymi podzadaniami:
```bash
python retrofit_epics.py --project-keys KLUCZ
```
Wspiera `--shard`. Niskopoziomowe operacje (zmiana typu, przepinanie, Bulk
Move dla typów, których nie da się zmienić zwykłą edycją) są w
`convert_to_epic.py` i są współdzielone przez narzędzia z tej sekcji.

Retrofit jest **opcjonalny**: nic nie ginie bez niego, zmienia się tylko struktura
(spłaszczone Sub-taski stają się Taskami pod Epikiem). Ponieważ zmienia typy
zgłoszeń, na których zespół może już pracować (inny widok na tablicy i w
filtrach, klucze i treść bez zmian), decyzję warto uzgodnić z PM-em projektu.
Kandydatów najprościej zbierać z podsumowań synchronizacji (linie `[POMINIĘTO]
... jest kandydatem na Epic`) i zdecydować na koniec dla wszystkich projektów.
Po retroficie: `refresh_state_flags.py --project-keys KLUCZ --apply` i
`audit_hierarchy.py --project-keys KLUCZ --full`. Zmiana Sub-taska na Taska
korzysta z tego samego mechanizmu co audyt, łącznie z wymaganym polem "Type".

### 15b. Fałszywe Epiki

Przed wprowadzeniem filtra z sekcji 11 podzadanie dodane dodatkowo do projektu
w Asanie mogło zostać Epikiem (np. `PROJ11-127`, w Asanie podzadanie
`PROJ11-107`). Procedura naprawy, w tej kolejności:

1. **Audyt** (tylko odczyt, zapisuje `epic_candidates_audit.csv`):
   ```bash
   python audit_epic_candidates.py
   ```
2. **Ponowne wykrycie kandydatów** dla dotkniętych projektów, żeby synchronizacja
   nie odtworzyła fałszywych Epików:
   ```bash
   python detect_epic_candidates.py --project-keys KLUCZ1,KLUCZ2 --force
   ```
3. **Raport, co trzeba zrobić** (stan sprawdzany na żywo w Jirze, nie z CSV):
   ```bash
   python revert_false_epics.py --csv epic_candidates_audit.csv
   ```
   | Status | Znaczenie | Naprawa |
   |---|---|---|
   | `OK_JUZ_POPRAWNY` | typ i rodzic się zgadzają | nic |
   | `OK_SPLASZCZONE` | zadanie wisi pod przodkiem, bo docelowy rodzic jest Sub-taskiem (po 15c) | nic |
   | `NAPRAWIALNE_SAM_RODZIC` | typ poprawny, brak rodzica (Epik) | `--apply` |
   | `NAPRAWIALNE_TYP_I_RODZIC` | Epic bez dzieci, rodzic jest Epikiem: zmiana na Task | `--apply` |
   | `NAPRAWIALNE_SUBTASK_I_RODZIC` | rodzic jest zwykłym zadaniem: zmiana na Sub-task | `--apply` |
   | `WYMAGA_RECZNEJ_DECYZJI` | Epic z dziećmi, rodzic jest Epikiem | `--apply-cascade` |
   | `WYMAGA_RECZNEJ_DECYZJI_GLEBOKA` | powinien być Sub-taskiem, ale ma własne dzieci (4+ poziomy) | krok 5 |
   | `CEL_JEST_SUBTASKIEM`, `CEL_NIE_ISTNIEJE`, `KANDYDAT_NIE_ISTNIEJE` | nietypowe dane | ręcznie |

   Pełny wynik: `revert_false_epics_report.csv`.
4. **Naprawa:**
   ```bash
   python revert_false_epics.py --csv epic_candidates_audit.csv --apply
   python revert_false_epics.py --csv epic_candidates_audit.csv --apply-cascade --only-keys KLUCZ-1   # test na jednym
   python revert_false_epics.py --csv epic_candidates_audit.csv --apply-cascade
   ```
   Kaskada najpierw zmienia typ i rodzica samego zadania, dopiero potem
   przepina jego dzieci jako Sub-taski (odwrotna kolejność jest niemożliwa:
   Sub-task nie może wisieć pod Epikiem). Błąd jednego dziecka nie blokuje
   pozostałych. `--dry-run` pokazuje operacje bez zapisu.
5. **Łańcuchy 4+ poziomów** (`WYMAGA_RECZNEJ_DECYZJI_GLEBOKA`) są spłaszczane:
   zadanie i wszystkie jego dzieci stają się Sub-taskami najbliższego stabilnego
   przodka. Skrypt sam śledzi łańcuchy (np. `92 → 97 → 106`) i dociąga z Jiry
   także dzieci spoza CSV:
   ```bash
   python diagnose_deep_chains.py --report revert_false_epics_report.csv   # co ucierpi (tylko odczyt)
   python resolve_deep_chains.py --csv epic_candidates_audit.csv                              # plan
   python resolve_deep_chains.py --csv epic_candidates_audit.csv --apply --only-roots KLUCZ-1 # test
   python resolve_deep_chains.py --csv epic_candidates_audit.csv --apply
   ```
   Świadomy kompromis: w Jirze znika jeden poziom grupowania z Asany. Treść,
   komentarze i załączniki zostają.
6. **Kontrola:** ponownie `revert_false_epics.py --csv ...` bez `--apply`.
   Wszystkie wiersze powinny mieć `OK_JUZ_POPRAWNY` albo `OK_SPLASZCZONE`.
7. **Odświeżenie flag w stanie** (sekcja 15c).

### 15c. Odświeżenie flag w stanie (`refresh_state_flags.py`)

Po każdej zmianie hierarchii bezpośrednio w Jirze (15a, 15b, ręcznie) flagi
`is_epic` / `is_subtask` / `top_level_ancestor_key` w `task_sync_state.json`
są nieaktualne. Skutki: nowe podzadania z Asany trafiają pod złego rodzica
albo kończą się błędem, a podział "zaktualizowane" w podsumowaniu jest
niedokładny. Skrypt ustawia flagi zgodnie z Jirą (typy pobierane hurtowo przez
JQL, 100 zgłoszeń na zapytanie):
```bash
python refresh_state_flags.py --project-keys KLUCZ1,KLUCZ2            # raport
python refresh_state_flags.py --project-keys KLUCZ1,KLUCZ2 --apply    # zapis
python refresh_state_flags.py --apply                                 # wszystkie projekty ze stanu
```
Przed zapisem tworzy kopię `task_sync_state.backup_<data>.json`. Zmienia
wyłącznie te trzy flagi, pełna lista zmian w `refresh_state_flags_report.csv`.
**Nie uruchamiaj równolegle z synchronizacją** w żadnym kierunku.

Po naprawach pierwsze uruchomienie `jira_asana_sync.py` potraktuje wszystkie
przestawione zgłoszenia jako zmienione (zmieniło się ich pole `updated` w
Jirze) i jednorazowo wypchnie ich treść do Asany. Treść się nie zmieniła,
więc to nieszkodliwe.

### 15d. Diagnostyka Bulk Move

Zmiana typu na Sub-task (albo z typu, którego nie da się zmienić zwykłą
edycją) idzie przez Bulk Move API. Przy przenoszeniu **na typ Sub-task**
rodzic musi być trzecim członem klucza mapowania,
`"PROJEKT,id_typu,KLUCZ-RODZICA"`, a nie polem `targetParentId` (inaczej Jira
zwraca ogólnikowe `Invalid request payload`). Ten format jest wbudowany
w `convert_to_epic.py`.

- `diagnose_subtask_types.py --project-keys KLUCZ`: jakie typy podzadań
  istnieją w projekcie (tylko odczyt).
- `diagnose_bulk_move_variants.py --key KLUCZ-1 --parent KLUCZ-2`: próbuje
  kilku wariantów payloadu na jednym zgłoszeniu. **Uwaga:** pierwszy wariant,
  który zadziała, naprawdę przenosi zgłoszenie. Nieudane warianty niczego nie
  zmieniają.

### 15e. Audyt i naprawa hierarchii (`audit_hierarchy.py`)

Sprawdza, czy każde zmigrowane zgłoszenie stoi w Jirze tam, gdzie wynika to
z drzewa Asany (te same reguły co w sekcji 11). Rozpoznaje też jako poprawne
struktury po naprawach z sekcji 15b. Projekty bez podzadań dodanych do listy
projektu są pomijane jednym tanim zapytaniem (`--full` wymusza pełne sprawdzenie).

| Status | Znaczenie | Naprawa |
|---|---|---|
| `OK` | zgodne | nic |
| `SAMODZIELNY_TASK` | powinien być Sub-taskiem, jest samodzielnym Taskiem | `--apply` (jego Sub-taski przechodzą razem z nim) |
| `ZLY_RODZIC_SUBTASK` | Sub-task pod złym rodzicem | `--apply` |
| `TASK_BEZ_EPIKA` | powinien być Taskiem pod Epikiem, nie ma rodzica | `--apply` |
| `SUBTASK_POD_EPIKIEM` | powinien być Taskiem pod Epikiem, jest Sub-taskiem pod nim | `--apply` |
| `WYMAGA_PRZEGLADU` | inna niezgodność, np. podzadanie wyciągnięte w Asanie na najwyższy poziom | ręcznie (np. Jira: `•••` → Konwertuj na zgłoszenie) |
| `RODZIC_NIEZMIGROWANY` | rodzica nie ma w Jirze | dociągnie go synchronizacja |

Naprawy idą w ustalonej kolejności (najpierw Sub-taski pod Epikiem stają się
Taskami, potem przepinanie Sub-tasków, potem samodzielne Taski), a przed każdą
zmianą stan jest sprawdzany na żywo.
```bash
python audit_hierarchy.py                                          # raport, projekty z arkusza
python audit_hierarchy.py --project-keys A,B --apply --only-keys A-12   # test na jednym zgłoszeniu
python audit_hierarchy.py --project-keys A,B --apply
python refresh_state_flags.py --project-keys A,B --apply
python audit_hierarchy.py --project-keys A,B                       # kontrola: same OK
```
Zmiany typu idą przez zwykłą edycję, a dla starych, zmigrowanych typów (np.
`Subtask` o id 10059, którego edycja jest odrzucana) przez **Bulk Move**. Przy
Bulk Move wymagane pola docelowego typu (u nas pole **"Type"**, wymagane dla
Tasków i Sub-tasków) są uzupełniane tak jak przy tworzeniu: `DEFAULT_TYPE_FIELD_VALUE`
z `.env` (domyślnie `NONBILLABLE`) oraz pola z jedną dozwoloną wartością
(Jira wymaga wtedy `inferFieldDefaults = false`, co narzędzie ustawia samo).
Komunikat `Bulk Move nie zakończyło się w ciągu 60s` oznacza zwykle tylko
wolną kolejkę po stronie Jiry: operacja najczęściej wykonuje się chwilę później,
a jeśli nie, zgłoszenie pojawi się w kolejnym raporcie i ponowne `--apply` je
dokończy. Długi przebieg można wstrzymać `kill -STOP <PID>` i wznowić
`kill -CONT <PID>` (operacja w toku może wtedy trafić do błędów).

Raport w `audit_hierarchy_report.csv` (przy `--shard` każdy proces nadpisuje
ten sam plik). Naprawa zmienia widok tablic: przepięte zgłoszenia pokazują się
pod rodzicem zamiast jako osobne karty, więc warto uprzedzić zespół. Projektów
zarchiwizowanych nie naprawiamy (są tylko do odczytu i zostały przyjęte w
obecnej postaci).

### 15f. Odtwarzanie zgubionych powiązań (`relink_state.py`)

Dla zgłoszeń, które są w Jirze, ale nie mają wpisu w stanie (zgubione przez
dawny błąd zapisu stanu, sekcja 11), szuka odpowiadającego zadania w Asanie.
**Przed naprawą projekt musi być w `sync_exclude.txt`**: synchronizacja
z niepełnym stanem utworzyłaby wszystko drugi raz.

Przebiegi dopasowania (kolejno, każdy działa na tym, czego nie dopasowały poprzednie):

| Kategoria | Reguła | Zapis |
|---|---|---|
| `LINK` | zgłoszenie ma link zdalny `asana:task:<gid>` | `--apply` |
| `PEWNE` | identyczny tytuł, jednoznaczna para, rodzic zgodny | `--apply` |
| `KOLEJNOSC` | ten sam tytuł powtórzony (np. szablony "TODOs:"); rozstrzygane po kolejności tworzenia, osobno dla każdej grupy o identycznym zbiorze kandydatów (w praktyce: pod tym samym rodzicem). Gdy kopii jest **więcej** niż zadań (ponowne synchronizacje utworzyły serie duplikatów, także niepełne po przerwanym przebiegu), najstarsza seria jest wiązana, a dalsze serie trafiają do `DUPLIKAT` | `--include-order` |
| `PODOBNE` | tytuł podobny, rodzic musi się zgadzać | `--include-similar` |
| `TRESC` | punktacja: tytuł, opis, termin, przypisany, ukończenie, rodzic; próg 5 pkt z przewagą 1,5 | `--include-content` |
| `DUPLIKAT` | druga (kolejna) kopia zadania, które ma już powiązanie | usuwa 15g |
| `NADMIAROWA_KOPIA` | kolejna kopia zadania z powtarzanym tytułem | usuwa 15g |
| `NIEJEDNOZNACZNE` | kilku kandydatów; w uwagach przyczyna: "rodzic też nierozstrzygnięty" albo "mniej kopii niż zadań" (wtedy nic nie jest zgadywane) | ręcznie |
| `BRAK_W_ASANIE` | zgłoszenie bez odpowiednika (utworzone w Jirze albo stara kopia przemianowanego zadania) | `--explain`, ewentualnie 15g `--include-orphans` |
| `NIEZMIGROWANE` | zadanie z Asany bez zgłoszenia | dociągnie synchronizacja |

**Reguła "podobne"** (zaostrzona po błędnych dopasowaniach krótkich niemieckich
tytułów): podobne są tytuły, w których wszystkie słowa krótszego występują
w dłuższym, a dłuższy ma najwyżej tyle **dodatkowych** słów, ile słów ma krótszy
(min. 1), albo tytuły prawie identyczne (co najmniej 85%).

| Para | Wynik |
|---|---|
| "NB" ~ "GoLive NB" | podobne (1 słowo dodane) |
| "DEV Breadcrumbs auf Collection-Pages" ~ "Breadcrumbs auf Collection-Pages" | podobne |
| "Show breadcrumbs aktivieren" ~ "Show breadcrumb links aktivieren" | podobne (92%) |
| "Artikel ... archiviert (mirgiert zu Jira)" ~ "Artikel ... archiviert" | podobne |
| "[Produkte]" ~ "[Produkte] Produkte anlegen" | **nie** (jednowyrazowy nagłówek + 2 słowa) |
| "Sale Bilder sind nicht lokalisiert" ~ "Newsletter Widget lokalisieren" | **nie** |
| "Projekt-Beschreibung anpassen" ~ "Float Projekt anpassen" | **nie** |

**Zawsze przejrzyj `PODOBNE` i `TRESC` przed zapisem:**
```bash
grep -E "^(PODOBNE|TRESC)" relink_state_report.csv | cut -d, -f3,5,7     # tytuł Asany, zgłoszenie, tytuł w Jirze / punktacja
python relink_state.py --project-key KLUCZ --explain KLUCZ-509            # kandydaci i punktacja dla jednego zgłoszenia
```

Komendy:
```bash
python relink_state.py --project-key KLUCZ                                  # raport
python relink_state.py --project-key KLUCZ --apply                          # LINK + PEWNE
python relink_state.py --project-key KLUCZ --apply --include-order --include-similar --include-content
python relink_state.py --project-key KLUCZ --report-file raporty/KLUCZ.csv  # własny plik raportu
```
Po zapisie każde odtworzone powiązanie dostaje link do Asany, a wpis ma
wyczyszczoną datę zmiany, więc najbliższa synchronizacja odświeży treść
zgłoszenia z Asany. Flagi `already_synced` sprawdzają komentarze i załączniki
już obecne w zgłoszeniu, więc nie powstaną drugi raz. Narzędzie na starcie
sprawdza wersję `asana_jira_sync.py` (stara wersja zapisu stanu = odmowa),
pomija zgłoszenia skopiowane z innej instancji i projekty chronione.

### 15g. Usuwanie duplikatów z raportu (`resolve_relink_duplicates.py`)

Usuwa zgłoszenia z kategorii `DUPLIKAT` i `NADMIAROWA_KOPIA` ostatniego raportu
`relink_state.py`. Zasady:

- wszystkie kopie jednego zadania są grupowane (bywa ich więcej niż dwie),
- **zostaje najstarsza** kopia (data utworzenia); stan i link do Asany są na nią
  przepinane, a data zmiany czyszczona (synchronizacja odświeży jej treść),
- przed usunięciem sprawdzana jest **praca zespołu** (komentarze i zmiany
  w historii od osoby innej niż konto synchronizacji). Kopia z pracą trafia do
  `DO_DECYZJI` i nie jest usuwana,
- podzadania usuwanej kopii, których nie ma zachowywane zgłoszenie, są
  **przenoszone** pod nie przed usunięciem (Bulk Move); gdy przeniesienie nie
  zostanie potwierdzone, kopia nie jest usuwana,
- usuwanie od najgłębszego poziomu w górę,
- **nigdy** nie usuwa zgłoszeń skopiowanych z innej instancji Jiry (`POMINIĘTE`).

```bash
python resolve_relink_duplicates.py --project-key KLUCZ                          # plan
python resolve_relink_duplicates.py --project-key KLUCZ --apply --limit 3        # test
python resolve_relink_duplicates.py --project-key KLUCZ --apply                  # pyta o TAK
grep "^DO_DECYZJI" resolve_relink_duplicates_plan.csv | cut -d, -f3,6            # powody wstrzymania
```
`--include-orphans` usuwa dodatkowo `BRAK_W_ASANIE`, ale **tylko** gdy raport
potwierdza, że każde zadanie z Asany ma już zgłoszenie (zero `NIEZMIGROWANE`,
`NIEJEDNOZNACZNE` i niezapisanych dopasowań); w przeciwnym razie odmawia.
Kopia, która ma podzadania powiązane z Asaną, trafia do `DO_DECYZJI`.
`--relink-report` i `--plan-file` wskazują pliki projektu (używa ich 15i).

Typowe powody w `DO_DECYZJI` i co z nimi zrobić:

| Powód | Co zrobić |
|---|---|
| komentarze/zmiany osób z zespołu | zespół pracował na tym zgłoszeniu: nie usuwać; często to przypadek z 15h |
| zmiany automatyzacji albo Twoje z testów | można usunąć ręcznie |
| podzadania powiązane z Asaną | sprawdzić strukturę, zwykle naprawi to 15e |

### 15h. Zadania przeniesione ręcznie do Jiry (`handle_migrated_to_jira.py`)

Porządkuje zadania z dopiskiem "migriert zu Jira" (sekcja 11) i ich poddrzewa.
Dla każdego zadania szuka **zgłoszenia zespołu** (bez powiązania, tytuł jak
w Asanie bez dopisku; dla podzadań pod zgłoszeniem zespołu rodzica) i **kopii**
z synchronizacji (tej, na którą wskazuje stan):

| Akcja | Kiedy | Skutek |
|---|---|---|
| `USUŃ_KOPIĘ` | jest zgłoszenie zespołu, na kopii nikt nie pracował | kopia usunięta, stan na zgłoszenie zespołu |
| `PRZEPNIJ` | jest zgłoszenie zespołu, kopii brak | stan na zgłoszenie zespołu |
| `OZNACZ` | brak osobnego zgłoszenia zespołu | zostaje istniejące zgłoszenie |
| `DO_DECYZJI` | na kopii też ktoś pracował | nic nie jest zmieniane |

We wszystkich przypadkach poza `DO_DECYZJI` wpis dostaje flagę `jira_master`:
synchronizacja już nigdy go nie tworzy ani nie aktualizuje.
```bash
python handle_migrated_to_jira.py --project-key KLUCZ            # plan
python handle_migrated_to_jira.py --project-key KLUCZ --apply
```
Plan wypisuje też pozostałe zgłoszenia bez powiązania, niezwiązane z dopiskiem.
Narzędzie ich nie rusza (najczęściej to zgłoszenia utworzone przez zespół wprost
w Jirze).

### 15i. Naprawa wielu projektów naraz (`repair_batch.py`)

Uruchamia narzędzia 15f-15h dla listy projektów **po kolei** (nigdy równolegle
na tym samym projekcie), z osobnym folderem raportów na projekt
(`repair_reports/<KLUCZ>/`: `relink.csv`, `dup_plan.csv`, `migrated_plan.csv`
i pełne logi każdego kroku), pokazuje wyjście narzędzi na bieżąco i kończy
jedną tabelą.
```bash
python repair_batch.py relink   --keys-file partia.txt                       # raport + tabela kategorii
python repair_batch.py relink   --keys-file partia.txt --apply --include-order --include-similar --include-content
python repair_batch.py dup      --keys-file partia.txt                       # plany + tabela
python repair_batch.py dup      --keys-file partia.txt --apply               # pyta raz o TAK
python repair_batch.py migrated --keys-file partia.txt [--apply]
python repair_batch.py relink   --keys-file partia.txt --shard 1/3           # i 2/3, 3/3 w osobnych terminalach
```
Plik partii: jeden klucz w linii, `#` = komentarz. `--project-keys A,B` zamiast
pliku. `--quiet` pokazuje samą tabelę. Projekty z `protected_projects.txt` są
pomijane. Przy podziale na części każdy projekt trafia do dokładnie jednej
części, a narzędzia zapisujące zakładają blokadę projektu; więcej niż 3 procesy
naraz nie przyspieszą pracy (limity zapytań Asany i Jiry).

**Czas pracy:** raport `relink` to jedno zapytanie do Asany na zadanie plus
jedno do Jiry na zgłoszenie bez powiązania (duży projekt: kilka tysięcy zapytań,
kilkanaście minut). Zapis jest wolniejszy, bo dla każdego odtwarzanego powiązania
sprawdza komentarze i załączniki.

**Procedura dla partii** (punkty **⏸**: wynik do przejrzenia przed zapisem):
```bash
PARTIA=partia_00.txt
KEYS=$(sed 's/#.*//' "$PARTIA" | tr -d ' \t' | grep -v '^$' | paste -sd, -)
cp task_sync_state.json "task_sync_state.backup_$(date +%Y%m%d_%H%M%S)_${PARTIA%.txt}.json"

# 1. powiązania
python repair_batch.py relink --keys-file "$PARTIA"                         # ⏸ tabela, przegląd PODOBNE/TRESC
python repair_batch.py relink --keys-file "$PARTIA" --apply --include-order --include-similar --include-content
# 2. duplikaty (świeży raport po zapisie)
python repair_batch.py relink --keys-file "$PARTIA"
python repair_batch.py dup --keys-file "$PARTIA"                            # ⏸ "do decyzji" = 0?
python repair_batch.py dup --keys-file "$PARTIA" --apply
# 3. "migriert zu Jira"
python repair_batch.py migrated --keys-file "$PARTIA"                       # jeśli coś pokaże: --apply
# 4. struktura
python refresh_state_flags.py --project-keys "$KEYS" --apply
python audit_hierarchy.py --project-keys "$KEYS" --full                     # ⏸ WYMAGA_PRZEGLADU = 0?
python audit_hierarchy.py --project-keys "$KEYS" --full --apply
python refresh_state_flags.py --project-keys "$KEYS" --apply
python fix_stale_resolution.py --project-keys "$KEYS"                       # jeśli coś pokaże: --apply
# 5. synchronizacja (zdjęcie partii z sync_exclude.txt - skrypt w sekcji 11)
python detect_epic_candidates.py --project-keys "$KEYS" --force
python asana_jira_sync.py --project-keys "$KEYS"                            # ⏸ podsumowanie
# 6. kontrola
python compare_project_totals.py "$KEYS"
python audit_hierarchy.py --project-keys "$KEYS" --full
```
Zmienne `PARTIA` i `KEYS` trzeba ustawić w każdym nowym terminalu. Przy wielu
partiach krok 5 można uruchomić przez `run_full_pipeline.py --only sync
--parallel 3`: projekty jeszcze nienaprawione są w `sync_exclude.txt`, więc
zostaną pominięte. Projekty, które mają tylko braki od poziomu 2 (bez zgubionych
powiązań), przechodzą kroki 1-3 bez zmian, a naprawia je dopiero krok 5.

### 15j. Ręczna konwersja na Epik (`convert_to_epic.py`)

```bash
python convert_to_epic.py --keys KLUCZ-1,KLUCZ-2 --dry-run
python convert_to_epic.py --keys KLUCZ-1 --convert-subtasks
```

Zamienia wskazane zadania na Epic i jawnie ustawia *Epic Name*. Jira przy
zmianie typu zostawia to pole puste, choć jest wymagane. Z
`--convert-subtasks` dodatkowo zamienia bezpośrednie podzadania na Task
pod nowym Epikiem. To narzędzie do pojedynczych przypadków, niezależne od
automatycznej synchronizacji.

Przy zmianie typu przez Bulk Move (typy, których nie da się zmienić zwykłą
edycją) wymagane pola docelowego typu, w tym "Type", są uzupełniane
automatycznie (sekcja 15e).

## 16. Pliki stanu i cache (nie kasować bez potrzeby)

| Plik | Tworzy | Rola | Czy można skasować |
|---|---|---|---|
| `task_sync_state.json` | `asana_jira_sync.py` i narzędzia naprawcze | powiązania Asana ↔ Jira, flagi hierarchii (`is_epic`, `is_subtask`, `top_level_ancestor_key`), `status_applied`, `asana_link`, `deleted_in_jira`, `jira_master`, zsynchronizowane komentarze i załączniki (oba kierunki) | **nie**: następna synchronizacja utworzy duplikaty |
| `task_sync_state.backup_*.json` | `refresh_state_flags.py`, `relink_state.py`, `resolve_relink_duplicates.py`, `handle_migrated_to_jira.py`, ręcznie | kopie zapasowe stanu (w nazwie data i powód, np. `_relink_KLUCZ`, `_dupl_KLUCZ`, `_migriert_KLUCZ`) | tak, gdy nie są już potrzebne |
| `sync_exclude.txt` | ręcznie | projekty **tymczasowo** wyłączone z synchronizacji (sekcja 11) | tak, gdy nic nie jest naprawiane |
| `protected_projects.txt` | ręcznie | projekty **trwale** wyłączone z synchronizacji i wszystkich narzędzi zmieniających dane | nie, dopóki ma obowiązywać |
| `section_status_map.json` | panel („Mapowanie statusów”) albo ręcznie | główna mapa sekcja Asany → status Jiry, z `__ukonczone__` | **nie** |
| `section_status_map.<KLUCZ>.json` | ręcznie | mapa sekcji dla jednego projektu, z `__ukonczone__` | tak, wtedy projekt wraca do głównej mapy |
| `project_key_cache.json` | `jira_sync.py` | przydzielone klucze projektów (po GID Asany) | nie zalecane |
| `asana_project_gids.json` | narzędzia kontrolne | cache GID projektów Asany spoza arkusza | tak |
| `epic_candidates.json` | `detect_epic_candidates.py` | kandydaci na Epic per projekt | tak, ale przed synchronizacją trzeba go odtworzyć |
| `workflow_graph_cache.json` | `asana_jira_sync.py` | mapa przejść workflow do zmiany statusów | tak |
| `auth_state.json` | `save_login_session.py` | sesja przeglądarki (kolumny tablic) | tak, wtedy trzeba zalogować się ponownie |
| `user_map.json` | panel („Mapowanie osób”), `generate_user_map.py` albo ręcznie | użytkownicy **Asany** → konta Jiry | **nie**; nie nadpisywać mapą z innej instancji |
| `asana_users_not_in_jira.json` | panel | osoby z Asany świadomie bez konta w Jirze; ich zadania są bez przypisanego i nie są liczone jako różnice | tak, wtedy panel znów pokaże je jako niezmapowane |
| `service_account.json` | panel (Start, wklejony klucz) | klucz konta serwisowego Google - **sekret** | tak, ale trzeba go wkleić ponownie |
| `migration_app/cache/` | panel | wyniki odczytów: arkusz, osoby, sekcje i statusy, test połączeń, wybór ról kopii projektu | tak (odczyt arkusza trwa wtedy kilka minut) |
| `migration_app/runs/`, `checks/`, `backups/` | panel | historia uruchomień, wyniki „Statusy i osoby”, kopie plików sprzed zapisu | tak |
| `wt_user_map.json` (lub inna nazwa) | ręcznie | osoby z cudzej instancji Jiry → nasze konta | tak, po zakończeniu kopiowania przyrostowego |
| `status_map_projekt1.json`, `status_map_projekt2.json` | ręcznie | mapy statusów dla kopiowania z innej instancji | nie, dopóki dociągane są nowości |
| `cross_instance_copy_state.json` | `copy_project_cross_instance.py` | powiązania źródło → cel (klucze `ŹRÓDŁO` albo `ŹRÓDŁO>CEL`), `status_applied` | **nie**, dopóki kopia ma być wznawialna, a zwłaszcza dopóki projekt docelowy jest synchronizowany z Asaną (narzędzia z sekcji 14-15 na jego podstawie pomijają te zgłoszenia) |
| `repair_reports/<KLUCZ>/` | `repair_batch.py` | raporty, plany i logi kroków naprawy projektu | tak, po zakończonej naprawie |
| `relink_state_report.csv`, `resolve_relink_duplicates_plan.csv`, `handle_migrated_to_jira_plan.csv` | narzędzia 15f-15h (uruchamiane bez `repair_batch.py`) | ostatni raport/plan; `resolve_relink_duplicates.py` czyta raport relink | tak, ale przed `dup` trzeba odświeżyć raport relink |
| `audit_hierarchy_report.csv`, `compare_project_totals_report.csv`, `refresh_state_flags_report.csv` | narzędzia kontrolne | raporty | tak |
| `projekty_all.txt`, `pilot.txt`, `partia_*.txt` | ręcznie | listy projektów do naprawy partiami | tak |
| `jira_schema_state.json` | `build_jira_schema.py` | mapowanie nazwa → ID obiektów konfiguracji (tylko podgląd; skrypt i tak sprawdza Jirę) | tak |
| `jira_schema_<klucz>.yaml`, `status_map_<klucz>.json` | `export_source_workflows.py` | workflow źródła i mapa statusów 1:1 | tak, po odtworzeniu workflow i skopiowaniu zadań |
| `epics_stories_state.json` | `create_epics_stories_from_sheet.py` | utworzone Epiki i Story | tak; **trzeba** go usunąć po skasowaniu zgłoszeń testowych w Jirze |
| `pipeline_summaries/`, `logs/` | skrypty | podsumowania i logi | tak |
| `__pycache__/` | Python | skompilowane moduły | tak, zawsze (Python odbuduje) |

## 17. Konfiguracja Jiry z pliku YAML (`build_jira_schema.py`)

Buduje konfigurację projektu company-managed na podstawie deklaratywnego
pliku YAML: pola niestandardowe, typy zgłoszeń, statusy, workflow, ekrany i
wszystkie schematy. Przykład pełnej konfiguracji: `jira_schema_klient.yaml` (konfiguracja klienta,
nie jest publikowana; punkt wyjścia do własnej: szablon `jira_schema.yaml`)
(odtworzenie konfiguracji KlientX).

```bash
python build_jira_schema.py --schema jira_schema_klient.yaml --dry-run
python build_jira_schema.py --schema jira_schema_klient.yaml
python build_jira_schema.py --schema jira_schema_klient.yaml --only statuses_workflow
python build_jira_schema.py --schema jira_schema_klient.yaml --include-incomplete
```

Wymaga uprawnienia **Administer Jira**. Skrypt jest **idempotentny**: obiekt
o danej nazwie, który już istnieje, jest używany ponownie zamiast tworzenia
duplikatu. Mapowanie nazwa → ID zapisuje w `jira_schema_state.json`, ale ten
plik jest tylko podglądem: skrypt i tak sprawdza stan w Jirze przy każdym
uruchomieniu. Log trafia do `logs/`, błędy i ostrzeżenia na Slacka (jeśli
skonfigurowany).

**Skąd bierze się plik YAML:** z dokumentacji klienta przez parsery
`parse_jira_pdfs.py`, `parse_jira_workflow.py`, `parse_jira_pptx.py`
i `merge_jira_sources.py` (np. `jira_schema_klient.yaml`: 47 pól, 6 ekranów,
46 statusów, 7 workflow), albo z innej instancji Jiry przez
`export_source_workflows.py` (sekcja 4a, kopiowanie z innej instancji).

Workflow oznaczone w YAML jako `incomplete: true` (np. brakujące przejścia)
są pomijane, chyba że podasz `--include-incomplete`.

**Znane pułapki:** statusy w starszych instancjach mają numeryczne ID, a
`statusReference` w definicji workflow musi być UUID, więc skrypt generuje go
lokalnie i wskazuje istniejący status przez `id`. Kategoria statusu jest brana
z Jiry, nie z YAML, a statusy o zakresie projektowym są pomijane przy budowaniu
workflow globalnych.

### 17a. Kroki (`--only`)

Wykonywane w tej kolejności. `--only` przyjmuje jeden krok albo kilka po przecinku, np. `--only screens,screen_scheme,issue_type_screen_scheme` — kolejność wykonania i tak jest zawsze taka jak w tabeli:

| Krok | Co robi | Co robi z istniejącym obiektem |
|---|---|---|
| `custom_fields` | pola niestandardowe, dla list także kontekst i opcje | dokłada brakujące opcje |
| `issue_types` | brakujące **globalne** typy zgłoszeń | nie zmienia |
| `issue_type_scheme` | schemat typów (w UI: *Work type scheme*) | dokłada brakujące typy |
| `statuses_workflow` | statusy i workflow | **nie zmienia** workflow |
| `screens` | ekrany, zakładki, pola (sekcja 17h) | dokłada brakujące zakładki i pola, ustawia kolejność zakładek; niczego nie usuwa |
| `screen_scheme` | screen schemes (ekran per operacja) | nie zmienia |
| `issue_type_screen_scheme` | typ zgłoszenia → screen scheme (*Work type screen scheme*) | dokłada brakujące mapowania |
| `workflow_scheme` | typ zgłoszenia → workflow | dokłada brakujące mapowania |
| `apply` | podpina ITSS i workflow scheme do projektów z `apply_to_projects` | — |
| `workflow_layout` | przelicza układ diagramu istniejących workflow (sekcja 17f); **tylko na żądanie**, nie w pełnym przebiegu | zmienia wyłącznie pozycje statusów |
| `workflow_update` | doprowadza istniejące workflow do stanu z YAML: przejścia, układ, START (sekcja 17g); **tylko na żądanie** | dodaje/zmienia/usuwa przejścia, dokłada statusy, zachowuje reguły |

Dzięki dokładaniu mapowań można budować konfigurację etapami: np. gdy
później powstanie workflow albo screen scheme dla kolejnego typu, ponowne
uruchomienie kroku dopisze go do istniejącego schematu. Działa to, dopóki
workflow scheme nie jest podpięty do projektu. Później Jira wymaga szkicu i
migracji statusów, a to trzeba zrobić w UI.

### 17b. Format pliku YAML

```yaml
prefix: ''                        # opcjonalny prefiks nazw wszystkich obiektów

custom_fields:
- name: Wycena [MD]
  type: float
- name: System
  type: multiselect
  options: [eCommerce]

issue_types:                      # tylko typy spoza standardu Jiry
- name: Deployment
  type: standard                  # albo: subtask

issue_type_scheme:
  name: eCommerce Sync – Work Type Scheme
  default: Task
  issue_types: [Epic, Story, Task, Bug, Sub-Task, Dev-Task, Deployment]

statuses:
- name: Nowy
  category: TODO                  # TODO / IN_PROGRESS / DONE

workflows:                        # obsługiwane też: 'workflow:' i '_pozostale_workflow:'
- name: Workflow-Story
  initial_status: Nowy            # status po utworzeniu zgłoszenia
  incomplete: false               # true = pomijany bez --include-incomplete
  statuses: [Nowy, W trakcie analizy (PL), Done]
  start_layout: [80, 40]          # opcjonalnie: pozycja punktu START
  layout:                         # opcjonalnie: lewy górny róg każdego statusu
    Nowy: [200, 100]              # (brak = układ wyliczany automatycznie)
    W trakcie analizy (PL): [200, 210]
    Done: [200, 320]
  transitions:
  - name: Do analizy
    id: '511'                     # opcjonalnie: id przejścia (np. z KlientX)
    from: [Nowy]                  # lista statusów albo ANY (przejście globalne)
    to: W trakcie analizy (PL)

screens:
- name: Story Create Screen
  tabs:                           # kolejność = kolejność zakładek w Jirze (17h)
  - name: Zakładka główna
    fields: [Podsumowanie, Opis, Wycena [MD], customfield_11977]   # nazwa albo ID pola

screen_schemes:
- name: Story Screen Scheme
  default: Story Edit Screen      # wymagany
  create: Story Create Screen     # opcjonalnie: create / edit / view

issue_type_screen_scheme:
  name: eCommerce Sync – Work Type Screen Scheme
  default_screen_scheme: Task Screen Scheme   # dla typów bez własnego wiersza
  mappings:
  - issue_types: [Story]
    screen_scheme: Story Screen Scheme

workflow_scheme:
  name: eCommerce Sync – Workflow Scheme
  default_workflow: Workflow-Task  # opcjonalnie; bez tego systemowy 'jira'
  mappings:
  - issue_types: [Task, Sub-Task, Dev-Task]
    workflow: Workflow-Task

apply_to_projects: []             # klucze projektów dla kroku 'apply'
```

Typy pól: `textfield`, `textarea`, `select`, `multiselect`, `datepicker`
(alias `date`), `datetime`, `float`, `userpicker` (alias `user`),
`multiuserpicker`, `labels`, `url`. Typy `issuelink` i `issuelist` są w
mapie, ale Jira Cloud odrzuca tworzenie takich pól przez API.

### 17c. Dopasowywanie nazw

- **Pola na ekranach** są szukane po nazwie (bez względu na wielkość liter),
  a pola systemowe także po polskich nazwach z UI („Podsumowanie”, „Opis”,
  „Osoba przypisana”, „Termin”, „Wersje do poprawienia”…), więc schemat po
  polsku działa także na koncie z językiem angielskim. Pola Jira Software
  (Sprint, Epic Name, Łącze epiku, Target start/end…) są rozpoznawane po typie.
- **Pole można też podać po ID** (`customfield_12083`) — trafia wtedy dokładnie
  w to pole, nawet jeśli w Jirze jest kilka o tej samej nazwie. Obecność pola na
  zakładce jest sprawdzana także po ID, więc pola, których nazwę API zwraca po
  angielsku, nie są dodawane drugi raz.
- **„Wersja bazowa” to w KlientX własne pole tekstowe**, nie systemowe Fix
  Version/s — skrypt nie tłumaczy już tej nazwy na `fixVersions`.
- **Typy zgłoszeń** są porównywane bez myślników i spacji: `Sub-Task`
  obejmuje zarówno `Sub-task`, jak i legacy `Subtask` (10059). To celowe:
  zmigrowane zadania używają legacy typu, a Jira nie podepnie schematu bez
  typu, który mają istniejące zgłoszenia.
- **Statusy i typy z projektów team-managed są pomijane.** Mogą mieć tę samą
  nazwę co globalne. Potwierdzone: istnieją dwa statusy „Done” (globalny
  10001 i projektowy 10233), a workflow globalny nie widzi projektowego
  (błąd „Status does not exist or is not visible to the workflow”). Nazwy
  wskazujące na kilka obiektów są oznaczane w logu jako `[UWAGA]`.
- **Kategoria istniejącego statusu** jest brana z Jiry, nie z YAML.
  Rozbieżność jest zgłaszana w logu.

### 17d. Zasady i ograniczenia workflow

- **`initial_status`** wskazuje status po utworzeniu zgłoszenia. Bez niego
  skrypt bierze pierwszy status z listy i ostrzega, bo kolejność w YAML
  rzadko odpowiada diagramowi (w Workflow-Story pierwszy był `Closed`).
- **Nazwy przejść mogą się powtarzać** (w KlientX są np. trzy „Need more
  info” w Deployment i dwa „Gotowe” w Story). Wcześniejsze założenie, że
  muszą być unikalne, było błędne — prawdziwą przyczyną ówczesnego błędu był
  status „Done” z projektu team-managed.
- **`from: ANY`** to przejście globalne z dowolnego statusu **do** `to`.
  Globalnej pętli „dowolny status → ten sam status” (np. „Przypisz
  Analityka” z post-function) nie da się tak wyrazić. Skonfiguruj ją
  ręcznie w edytorze workflow.
- Wirtualny punkt startowy z diagramów (`Początek`, `Start`) jest pomijany,
  bo przejście INITIAL generuje skrypt.
- **Krok `statuses_workflow` nie zmienia istniejącego workflow** (tylko
  tworzy brakujące). Do aktualizacji istniejących służy `workflow_update`
  (sekcja 17g), a do samego układu `workflow_layout` (sekcja 17f).
- **Układ diagramu** jest brany z `layout` w YAML (w `jira_schema_klient.yaml`
  to pozycje z diagramów KlientX, więc diagramy wyglądają 1:1). Bez `layout`
  jest liczony automatycznie, pionowo jak w KlientX:
  START i status początkowy na górze, kolejne wiersze według odległości od
  niego w grafie przejść, statusy w wierszu ułożone obok siebie tak, żeby
  strzałki mniej się krzyżowały. Łańcuchy dłuższe niż 16 wierszy zawijają
  się do pasa po prawej. Przejścia globalne (`ANY`) nie wpływają na układ.

### 17e. Podpinanie do projektów

Krok `apply` przypisuje ITSS i workflow scheme, ale endpoint przypisania
workflow scheme działa **tylko dla projektów bez zgłoszeń**. Projekty po
migracji podepnij ręcznie w ustawieniach projektu, w tej kolejności:
1. **Work type scheme** (typy zgłoszeń),
2. **Work type screen scheme** (ekrany),
3. **Workflow scheme**. Jira poprosi tu o wskazanie, na jaki nowy status
   trafia każdy stary status każdego typu.

Pełna kolejność dla nowego projektu na konfiguracji eCommerce Sync:

```bash
python build_jira_schema.py --schema jira_schema_klient.yaml --dry-run       # podgląd całości
python build_jira_schema.py --schema jira_schema_klient.yaml                 # pola, typy, workflow, ekrany, schematy
# podpięcie schematów do projektu (krok apply albo ręcznie, jak wyżej)
python associate_fields_to_field_scheme.py --schema jira_schema_klient.yaml --project KLUCZ   # sekcja 17i
python set_transition_groups.py --dry-run && python set_transition_groups.py                  # sekcja 17j
```

Po każdej późniejszej zmianie ekranów: `--only screens` (ew. z
`custom_fields`), a potem ponownie `associate_fields_to_field_scheme.py`.

### 17f. Układ diagramów istniejących workflow (`--only workflow_layout`)

Workflow utworzone starszą wersją skryptu mają wszystkie statusy w jednym
rzędzie, a punkt START bywa na drugim końcu diagramu. Ten krok przelicza
układ tym samym algorytmem i stawia START nad statusem początkowym. Graf przejść
bierze z Jiry, nie z YAML, więc uwzględnia też przejścia dodane ręcznie w
edytorze. Zmienia **wyłącznie pozycje statusów**: statusy, przejścia i
reguły są odsyłane bez zmian.

```bash
# podgląd: kolumny + walidacja Jiry, nic nie jest zapisywane
python build_jira_schema.py --schema jira_schema_klient.yaml --only workflow_layout \
    --workflow Workflow-Epic --dry-run
# zapis dla jednego workflow
python build_jira_schema.py --schema jira_schema_klient.yaml --only workflow_layout \
    --workflow Workflow-Epic
# wszystkie workflow z YAML
python build_jira_schema.py --schema jira_schema_klient.yaml --only workflow_layout
```

Krok nie wchodzi w pełny przebieg (bez `--only`), żeby nie nadpisywać
układu poprawionego ręcznie w edytorze. Jeśli workflow ma w YAML sekcję
`layout` (tak jest we wszystkich workflow `jira_schema_klient.yaml` — pozycje
odczytane z diagramów KlientX), krok używa jej i diagram wygląda jak w KlientX;
bez `layout` układ jest wyliczany automatycznie.

**Porty strzałek.** Jira nie dokumentuje, który numer `fromPort`/`toPort`
oznacza którą krawędź prostokąta statusu, więc strzałki tworzone przez API
wychodzą z przypadkowych boków. `diagnose_workflow_ports.py` (tylko odczyt)
ustala tę numerację na podstawie workflow narysowanych ręcznie w edytorze:
```bash
python diagnose_workflow_ports.py
python diagnose_workflow_ports.py --limit 300
```
Wynik wpisz do `LAYOUT_PORTS` w `build_jira_schema.py` (domyślnie pusty —
porty nie są wtedy ustawiane) i uruchom ponownie `workflow_layout` albo
`workflow_update`.

### 17g. Aktualizacja istniejących workflow (`--only workflow_update`)

Doprowadza workflow, które już są w Jirze, do stanu z YAML: przejścia (nazwy,
źródła, cele, id), układ diagramu i punkt START. Wypisuje, które strzałki
dochodzą (`+`), a które znikają (`-`).

```bash
# podgląd zmian + walidacja Jiry, nic nie jest zapisywane
python build_jira_schema.py --schema jira_schema_klient.yaml --only workflow_update \
    --workflow Workflow-Epic --dry-run
# zapis
python build_jira_schema.py --schema jira_schema_klient.yaml --only workflow_update
```

- Statusy są dopasowywane po nazwie (bez względu na wielkość liter).
  Brakujące są **dodawane** do workflow.
- **Statusu nie usuwa**: wymagałoby to przeniesienia zgłoszeń. Jeśli YAML
  nie zawiera statusu obecnego w workflow, workflow jest pomijany z
  komunikatem.
- **Reguły** (akcje, warunki, walidatory) istniejącego przejścia o tej samej
  nazwie i celu są przenoszone, więc konfiguracja dodana ręcznie w edytorze
  nie znika.
- Nie wchodzi w pełny przebieg — uruchamiaj świadomie, najpierw z `--dry-run`.

Workflow w `jira_schema_klient.yaml` zostały odtworzone z PDF-ów edytora KlientX
(widok Tekst i Diagram). Nazwy przejść globalnych Bug („To Do”, „Anulowane”),
Story („Anulowane”) i Task („On hold”, „Rejected”) są przyjęte, bo Jira nie
pokazuje ich na diagramie — do potwierdzenia przez KlientX. Widok Tekst był
kompletny tylko dla Epic, Acceptance, Question i Deployment; dla Bug i Story
częściowy, dla Task go nie było — resztę odczytano z diagramu. Globalne pętle
z post-function (np. „Przypisz Analityka”) są pominięte — skonfiguruj je ręcznie.

### 17h. Ekrany: pola po ID, kolejność zakładek, usuwanie pól

**Pole po ID.** Na liście pól zakładki można zamiast nazwy podać ID pola
(`customfield_11977`). Przydaje się, gdy w Jirze jest kilka pól o tej samej
nazwie (np. dwa „Environment.”) i trzeba wskazać konkretne. Komentarz za
`#` jest ignorowany:

```yaml
- name: System Project Deployment Edit/View Issue Screen-KlientX(v1)
  tabs:
  - name: Zakładka główna
    fields:
    - Podsumowanie
    - customfield_11977   # Data wdrożenia
```

**Kolejność zakładek.** Krok `screens` ustawia zakładki w kolejności z YAML.
Pierwsza zakładka to ta, którą Jira pokazuje od razu po otwarciu zgłoszenia,
więc pola takie jak Podsumowanie i Opis powinny być w pierwszej. Jira
dopisuje nowe zakładki na końcu, dlatego skrypt po dodaniu brakujących
przestawia je na właściwe miejsca. Zakładki spoza YAML zostają na końcu.
W `jira_schema_klient.yaml` pierwsza jest „Zakładka główna” (Acceptance:
„General”, Question: „Field Tab”). Eksport PDF z KlientX podawał zakładki
alfabetycznie, przez co opis lądował w ostatniej.

**Zakładka domyślna nowego ekranu.** Jira tworzy każdy nowy ekran z pustą
zakładką „Field Tab”. Skrypt zmienia jej nazwę na pierwszą zakładkę z YAML,
chyba że YAML sam przewiduje „Field Tab” (tak jest na ekranie Question).

**Jedno pole może być na ekranie tylko raz.** Jeśli pole przenosisz w YAML do
innej zakładki tego samego ekranu, skrypt go nie przeniesie (dodaje tylko
brakujące). Przeciągnij je ręcznie w edytorze ekranu.

**`--dry-run` przy istniejących ekranach niczego nie zapisuje.** Starsza
wersja skryptu w trybie podglądu dokładała do istniejących ekranów zakładki
i pola. Jeśli uruchamiałeś `screens` z `--dry-run` przed tą poprawką, zmiany
mogły już wejść.

**Usuwanie pól z ekranów (`remove_field_from_screens.py`).** `build_jira_schema.py`
tylko dodaje. Do zdejmowania pola służy osobny skrypt. Pole i jego wartości w
zgłoszeniach zostają, znika tylko z ekranów:

```bash
python remove_field_from_screens.py --field "Nr zgłoszenia u Dostawcy" --schema jira_schema_klient.yaml --dry-run
python remove_field_from_screens.py --field "Nr zgłoszenia u Dostawcy" --schema jira_schema_klient.yaml
python remove_field_from_screens.py --field customfield_11972 --all-screens --dry-run   # wszystkie ekrany w Jirze
```

`--schema` ogranicza się do ekranów z YAML (zalecane), `--all-screens`
przechodzi po wszystkich ekranach instancji, także innych projektów. Przy
kilku polach o tej samej nazwie skrypt odmawia i prosi o ID. Pamiętaj, żeby
usunąć pole także z YAML, inaczej następne `--only screens` doda je z powrotem.

### 17i. Field schemes — pole jest na ekranie, a go nie widać

Jira Cloud zastępuje „field configurations” nowym mechanizmem **field
schemes**. Na instancji FIRMA jest już włączony. Pole, które jest na ekranie,
ale **nie jest powiązane z field scheme projektu**, jest niewidoczne i nie da
się go edytować — także przez API i automatyzację. Tworzenie pól i ekranów w
`build_jira_schema.py` tego powiązania **nie robi**. Objaw: w „Find your field”
(⋯ w zgłoszeniu → Find your field) pozycja „Field scheme association: The
field … is not included in the 'Default Field Scheme'”.

`associate_fields_to_field_scheme.py` zbiera ID wszystkich pól niestandardowych
z ekranów w YAML i dokłada brakujące do field scheme projektu. Zdejmuje też
ograniczenie pola do wybranych typów pracy, jeśli takie znajdzie:

```bash
python associate_fields_to_field_scheme.py --schema jira_schema_klient.yaml --project PROJ --dry-run
python associate_fields_to_field_scheme.py --schema jira_schema_klient.yaml --project PROJ
python associate_fields_to_field_scheme.py --project PROJ --fields customfield_11977,customfield_11973
python associate_fields_to_field_scheme.py --schema jira_schema_klient.yaml --project PROJ \
    --dry-run --show-raw customfield_11977          # surowy wpis pola w field scheme
```

- **Uruchamiaj po każdym dodaniu pola do ekranów** (`--only screens`), dla
  każdego projektu, który korzysta ze schematów eCommerce Sync.
- Projekty FIRMA zwykle mają wspólny **„Default Field Scheme”**. Dodane pola
  stają się w nich dostępne, ale pokazują się tylko tam, gdzie są na ekranach.
- API field schemes jest nowe (format zapytań wg [RFC-105](https://community.developer.atlassian.com/t/rfc-105-new-field-scheme-model-apis-for-field-configuration-backup-restoration-and-management/94206)).
  Przy błędzie zapisu skrypt wypisuje pełną odpowiedź Jiry.

**Diagnostyka: `diagnose_field_on_screen.py`** (tylko odczyt). Dla jednego
pola sprawdza po kolei: czy istnieje (i czy nie ma duplikatów nazwy), kontekst
pola, na jakich ekranach jest, jakich ekranów projekt faktycznie używa dla danego
typu (create/edit/view), czy pole jest edytowalne w konkretnym zgłoszeniu,
field configuration oraz tabelę wszystkich pól ekranu edycji z kolumnami
„edytowalne / w field configuration / kontekst / typ”:

```bash
python diagnose_field_on_screen.py --field customfield_11977 --issue PROJ-11
python diagnose_field_on_screen.py --field "Wersja wdrożenia" --project PROJ --issue-type Deployment
```

Kolejność sprawdzania, gdy pola nie widać w zgłoszeniu:
1. czy pole istnieje i ma kontekst obejmujący projekt i typ (sekcja 2 wyniku),
2. czy jest na ekranie, którego projekt używa dla tego typu (sekcja 4),
3. czy jest w field scheme projektu („Find your field” albo `--show-raw`),
4. czy nie jest po prostu puste — Jira chowa puste pola pod „Więcej pól”.

### 17j. Kto może wykonać przejście — grupy (`set_transition_groups.py`)

Zakłada grupy i ustawia na przejściach workflow warunek „Restrict transition”
(`system:restrict-issue-transition`) z listą grup. Konfiguracja jest w
`transition_groups.yaml` (plik nie jest w repozytorium — utwórz go według wzoru):

```yaml
groups:                               # alias ze slajdu -> grupa w Jirze
  SX-Dev:    {name: "SX-Dev"}         # tworzona, jeśli nie istnieje
  PL-Admins: {name: "PL-Admins"}
  Admins:    {name: "Backbone-superuser", existing: true}   # tylko sprawdzana

always_allowed: [Admins]              # dopisywana do KAŻDEGO warunku

propagate:                            # te same reguły w innych workflow
- from: Workflow-Deployment
  to_workflows: [Workflow-Story, Workflow-Task, Workflow-Bug, Workflow-Epic]

workflows:
  Workflow-Deployment:
  - {transition: "Deploy to TEST", to: "Deploying (Test)", groups: [Admins]}
```

```bash
python set_transition_groups.py --dry-run                 # podgląd + walidacja Jiry
python set_transition_groups.py
python set_transition_groups.py --workflow Workflow-Deployment
```

- Przejście jest identyfikowane po **nazwie i statusie docelowym** (bez
  względu na wielkość liter), bo nazwy się powtarzają (3× „Need more info”).
- **`propagate`** nakłada reguły na przejścia o tej samej nazwie i celu w
  innych workflow; brak takiego przejścia nie jest błędem. Uwaga: status
  źródłowy nie jest porównywany, np. „Deployed To Test” w Bug/Task (z Done)
  dostaje grupy z „Deployed to TEST” w Deployment (z Deploying (Test)).
- Poprzedni warunek grupowy jest **zastępowany**, inne warunki przejścia
  zostają — skrypt można uruchamiać wielokrotnie.
  `build_jira_schema.py --only workflow_update` tych warunków nie kasuje
  (przenosi reguły przejść o tej samej nazwie i celu).
- **Nowe grupy są puste.** Zanim zapiszesz warunki, dodaj do grup ludzi —
  inaczej przejścia wykona tylko `always_allowed` (Backbone-superuser).
- Walidacja Jiry w `--dry-run` działa dopiero, gdy grupy już istnieją.
- Źródło grup: slajd 13 prezentacji KlientX v1.5 (tylko Deployment). Pominięte
  celowo: Create (warunków nie przyjmuje — tworzenie reguluje schemat
  uprawnień), przejścia „Auto.” (wykonuje je automatyzacja) i przejścia bez
  grup na slajdzie. Skróty rozwinięto: „An” → PL-An, „PM” bez prefiksu →
  SX-PM + PL-PM, „SX, PL” → SX-Dev, SX-PM, PL-PM, PL-An. Popraw w YAML,
  jeśli rozumiesz je inaczej.

## 18. Procedura pracy

### Nowa migracja

Zakładka **Start** w panelu (część I). Zacznij od zakładki arkusza z 1-2 projektami
testowymi (`GOOGLE_SHEET_RANGE`), przed masowym tworzeniem zgłoszeń poproś administratora
Jiry o wyłączenie powiadomień i automatyzacji dla nowych projektów, a po migracji sprawdź
projekty w „Statusy i osoby” i porównaniem liczby zadań (14a).

### Zasady

- **Najpierw raport, potem zapis.** Każde narzędzie zmieniające dane ma tryb
  raportu; `--apply` dopiero po przejrzeniu wyniku (zwłaszcza `PODOBNE`, `TRESC`,
  `DO_DECYZJI`, `WYMAGA_PRZEGLADU`).
- **Kopia stanu** przed każdą większą operacją:
  `cp task_sync_state.json "task_sync_state.backup_$(date +%Y%m%d_%H%M%S)_<powód>.json"`.
- **Nigdy dwa procesy na tym samym projekcie.** Różne projekty równolegle są
  bezpieczne (blokady projektów, zapis stanu tylko zmienionych wpisów), ale
  najwyżej 3 procesy naraz.
- **Synchronizacja dopiero po naprawie** projektu z zgubionymi powiązaniami;
  do tego czasu projekt jest w `sync_exclude.txt`.
- Projekty, w których zespół pracuje już tylko w Jirze, są w `protected_projects.txt`.
- Arkusz zawiera projekty do synchronizacji; wszystkie kroki domyślnie działają
  na arkuszu.

### Naprawa projektów zsynchronizowanych starszą wersją

Rozpoznanie: `python compare_project_totals.py --all --top-level` (szybko) albo
pełne `--all --shard K/N`. Wzorce i ich znaczenie: sekcja 14a.

**Krok 0, jednorazowo:**
```bash
cp task_sync_state.json "task_sync_state.backup_$(date +%Y%m%d_%H%M%S)_przed_naprawa_wszystkich.json"
python3 -c "import jira_sync as c; print('\n'.join(sorted({r.jira_key for r in c.get_sheet_rows() if r.jira_key})))" > projekty_all.txt
cat projekty_all.txt >> sync_exclude.txt          # żaden projekt nie jest synchronizowany do czasu naprawy
```
Do tego u administratora Jiry: **wyłączenie powiadomień** (np. pusty schemat
powiadomień dla projektów) i wstrzymanie automatyzacji na czas dociągania
brakujących zgłoszeń, bo Jira wysyła maile przy każdym utworzeniu przez API.

**Partia pilotażowa** z różnymi przypadkami (duży projekt ze zgubionymi
powiązaniami, mały, tylko brak głębokich poziomów, Jira > Asana), potem reszta
partiami po ok. 50 projektów:
```bash
grep -v -x -f pilot.txt projekty_all.txt | split -l 50 -d - partia_
```
Każda partia przechodzi kroki 1-6 z sekcji 15i. Projekty pilne można wydzielić
do osobnego pliku i przeprowadzić tymi samymi krokami poza kolejnością.

### Rutynowo, przy każdej synchronizacji

```bash
python run_full_pipeline.py --only epics --parallel 3
python run_full_pipeline.py --only sync --parallel 3
```
Jeden projekt: `python asana_jira_sync.py --project-keys KLUCZ` (przed nowym
projektem albo po zmianie struktury w Asanie także
`python detect_epic_candidates.py --project-keys KLUCZ --force`). Nowy projekt
z innym workflow: najpierw `section_status_map.<KLUCZ>.json` (sekcja 11),
podgląd `--dry-run` i sprawdzenie listy sekcji bez mapowania.

Reakcja na podsumowanie (konsola albo Slack):

| Sygnał | Co zrobić |
|---|---|
| `Sekcje BEZ wpisu w section_status_map.json` | dopisać sekcję do głównej mapy albo mapy projektu (także warianty z literówkami) |
| `Statusy błąd` > 0 | ostrzeżenie w logu wskazuje zgłoszenie i brakujące przejście; workflow albo ręczna zmiana |
| `kandydaci na Epic do retrofit_epics.py` > 0 | opcjonalnie retrofit (15a), po uzgodnieniu z PM-em |
| `pominięte usunięte w jirze` rośnie bez powodu | sekcja 14h |
| `[CHRONIONE] Pomijam ...` | oczekiwane dla projektów z `protected_projects.txt` |
| `Utworzono` dużo większe niż nowych zadań w Asanie | **zatrzymać**; możliwe zgubione powiązania (sekcja 14a) |
| błędy połączenia (`Temporary failure in name resolution`) | sekcja 19 |

### Okresowo (np. raz w miesiącu)

```bash
python compare_project_totals.py --all --top-level      # szybki przegląd kompletności
python audit_hierarchy.py                                # zmiany struktury robione w Asanie
python fix_stale_resolution.py --project-keys ...        # przekreślone klucze
```
Synchronizacja nie przestawia hierarchii już zmigrowanych zgłoszeń (typ i rodzic
ustawiane są przy tworzeniu), więc zmiany struktury w Asanie wyłapuje audyt.

### Dociąganie nowości z innej instancji Jiry

Ta sama komenda co przy pierwszym kopiowaniu, z `--update-status` (sekcja 4a,
"Kopiowanie przyrostowe"); najpierw `--dry-run` dla każdego projektu docelowego.

### Zawsze

Po ręcznych zmianach hierarchii w Jirze albo po dowolnym narzędziu naprawczym:
`python refresh_state_flags.py --project-keys ... --apply`.

### Procesy

Podgląd działających skryptów: `pgrep -af python`. Wstrzymanie i wznowienie:
`kill -STOP <PID>` / `kill -CONT <PID>` (albo `Ctrl+Z` i `fg`); operacja w toku
w chwili wstrzymania może po wznowieniu przekroczyć czas oczekiwania i trafić
do błędów. Zatrzymanie: `kill <PID>` albo `Ctrl+C` (zapis stanu jest atomowy;
ponowne uruchomienie tej samej komendy pomija zrobione). Po zatrzymaniu
`run_full_pipeline.py` sprawdź, czy nie zostały procesy shardów.

## 19. Rozwiązywanie problemów

### 19a. Synchronizacja, naprawy, kopiowanie

| Objaw | Przyczyna | Co zrobić |
|---|---|---|
| `Temporary failure in name resolution`, serie `[PONAWIAM n/4]` | przerwa w sieci albo DNS po Twojej stronie | sprawdzić `ping -c 3 app.asana.com`; przy dłuższej przerwie przerwać, po powrocie sieci uruchomić tę samą komendę ponownie (już zrobione są pomijane), na końcu `compare_project_totals.py` |
| `copy_project_cross_instance.py`: `0 zadań` albo `odrzuca logowanie: HTTP 401` | token do instancji źródłowej wygasł albo został cofnięty, konto straciło dostęp, instancja zmieniła adres | nowy token (zwykły, nie "z zakresami") w `SOURCE_JIRA_API_TOKEN`; sprawdzenie: `curl -u "$SOURCE_JIRA_EMAIL:$SOURCE_JIRA_API_TOKEN" "$SOURCE_JIRA_BASE_URL/rest/api/3/myself"` |
| `AttributeError: module 'jira_sync' has no attribute ...` albo raport z identycznymi liczbami mimo nowej wersji | w folderze jest stara wersja pliku (pobrana nowa zapisała się jako `plik (1).py`) albo nieaktualny `__pycache__` | `ls -la nazwa*.py`, przenieść nową wersję na właściwą nazwę, `rm -rf __pycache__`; pliki z jednej paczki podmieniać razem |
| `repair_batch.py: error: podaj --project-keys albo --keys-file` | pusta zmienna `$PARTIA` (nowy terminal) | ustawić `PARTIA` i `KEYS` ponownie |
| `GRUPA:: nie znaleziono polecenia` przy `source .env` | wartość ze spacją bez cudzysłowu | `DEVELOPER_GROUP_NAME="MOJA GRUPA: Staff"` |
| `sesja przeglądarki wygasła` / `UNAUTHENTICATED` / pusta odpowiedź GraphQL | sesja, przeciążenie, zmiana skrótu zapytania | sekcja 4a, "Gdy zapis kolumn przestaje działać" |
| `Bulk Move nie zakończyło się w ciągu 60s` | wolna kolejka po stronie Jiry | ponowne `--apply` dokończy brakujące (sekcja 15e) |
| `For issue X Type is required` | wymagane pole "Type" przy zmianie typu | obsługiwane automatycznie przez `convert_to_epic.py`; jeśli wraca, sprawdzić `DEFAULT_TYPE_FIELD_ID`/`DEFAULT_TYPE_FIELD_VALUE` w `.env` |
| `The priority selected is invalid` przy kopiowaniu | priorytet spoza schematu priorytetów projektu | obsługiwane automatycznie (pole pomijane, reszta zapisywana) |
| przekreślony klucz w otwartym zgłoszeniu | pozostawione pole Resolution | sekcja 14g |
| setki maili/powiadomień o usunięciach i zmianach | zgłoszenia tworzone są z konta synchronizacji, które jest zgłaszającym | osobiste ustawienia powiadomień w profilu Jiry; przy masowym tworzeniu wyłączenie powiadomień przez administratora |
| liczba w `Zdjęto z wykluczeń` większa niż liczba projektów | podwójne wpisy w `sync_exclude.txt` | nieszkodliwe; deduplikacja: sekcja 11 |
| podgląd synchronizacji nic nie pokazuje dla projektu | projekt w `sync_exclude.txt` albo `protected_projects.txt` | `grep KLUCZ sync_exclude.txt protected_projects.txt` |
| `./start.sh`: `Address already in use` | port 8765 zajęty (np. działający panel) | otwórz działający panel albo `./start.sh --port 9000` |
| kroki 2-3 nie uruchamiają przeglądarki | brak bibliotek systemowych dla Chromium (Linux) | `sudo .venv/bin/python -m playwright install-deps chromium` |
| panel: pusta lista projektów | arkusz wczytuje się tylko na żądanie | Start → krok 3 albo Projekty → „Odśwież z arkusza” |
| test połączeń: brak dostępu do arkusza (403) | arkusz nieudostępniony kontu serwisowemu | udostępnij arkusz adresowi pokazanemu w kroku 1 |
| zadania bez przypisanego, choć osoba jest w `user_map.json` | rola osoby nie ma „Assignable User” w schemacie uprawnień | „Statusy i osoby” → Osoby (schemat i role z uprawnieniem) |
| odhaczone zadania nie dostają statusu końcowego | brak `__ukonczone__`, a projekt nie ma statusu `Fertig` | ustaw status w „Mapowaniu statusów” |
| kopia projektu: „pominięto uprawnienia…” | Jira nie przyjmuje części uprawnień przy tworzeniu schematu (Product Discovery, `COPY_PROJECT`) | dodaj je ręcznie w schemacie kopii, jeśli są potrzebne |

### 19b. Konfiguracja Jiry w praktyce: znane problemy i rozwiązania

**Pole jest na ekranie, ale go nie widać / nie da się edytować.** Patrz
sekcja 17i (field scheme) — to najczęstsza przyczyna na FIRMA. Szybki test w
UI: ⋯ w zgłoszeniu → **Find your field**.

**Nie da się edytować nazwy Story.** Story ma dwa ekrany: Create (tworzenie)
i Edit (edycja i podgląd). Ekran Edit z eksportu KlientX nie miał Podsumowania
ani Opisu. W `jira_schema_klient.yaml` dodano mu „Zakładkę główną” (16.10.2026)
— wystarczy `--only screens`. Ta sama przyczyna sprawiała, że pole Components
Story było niewidoczne.

**Zmiana typu Task → Story z ikony typu nie działa** („You cannot edit this
work type in this space”). Task i Story mają różne workflow, a zmiana typu z
ikony działa tylko przy wspólnym workflow. Użyj **⋯ → Przenieś (Move)**,
zostaw projekt, wybierz typ i zmapuj status. Wiele zgłoszeń naraz: wyszukiwanie
→ **Bulk change → Move**. Klucz, komentarze i historia zostają.

**Automatyzacja „Work item created” nie działa na części zgłoszeń.**
Zgłoszenia **przeniesione** z innego projektu (Move, np. RED-28 → PROJ-450)
nie wywołują zdarzenia „created”. Dla pól ustawianych automatycznie (np.
Key_txt = `{{issue.key}}`) dodaj:
- regułę z wyzwalaczem **Work item moved** (projekt docelowy) i tą samą akcją —
  zawsze nadpisuje, bo przy przeniesieniu zmienia się klucz,
- albo regułę **Scheduled** z JQL `project = PROJ AND "Key_txt[Short text]" is EMPTY`
  (lub `cf[12083] is EMPTY`) i **bez** „Only include work items that have
  changed since the last time…”. Łapie każde zgłoszenie z pustym polem,
  niezależnie od tego, jak trafiło do projektu, i uzupełnia też stare.

Rozpoznanie: dziennik audytu reguły (⋯ → Audit log). Brak wpisu = reguła się
nie uruchomiła (zakres albo sposób powstania zgłoszenia), wpis z błędem = pole
(field scheme), „Success” a pole puste = coś nadpisało wartość (sprawdź
Historię zgłoszenia, np. synchronizację Backbone).

**Zmiana klucza projektu (PROJT → PROJ).** Reguły automatyzacji trzymają
się projektu, nie klucza — działają dalej. W skryptach podawaj nowy klucz
(`--project PROJ`); stare klucze zgłoszeń przekierowują w UI. Pliki stanu
zapisane pod starym kluczem (np. `epics_stories_state.json`) zaczną od nowa,
ale skrypty i tak szukają istniejących zgłoszeń w Jirze, więc nie dublują.
Pola z zapisanym kluczem (Key_txt) mogą zawierać stary klucz.

**Synchronizacja Backbone — „Analityk u Klienta” = `Bacbone-Issue-Sync (${user.emailAddress})`.**
To niepodstawiony szablon w mapowaniu Backbone; do poprawienia w
konfiguracji synchronizacji, nie w Jirze.

## 20. Epiki i Story z arkusza „Zakres MVP”

### 20a. Tworzenie (`create_epics_stories_from_sheet.py`)

Źródło: plik `.xlsx` (Google Sheets: Plik → Pobierz → Microsoft Excel).

| Zakładka | Kolumna | Trafia do |
|---|---|---|
| Lista Epiców | D | tytuł Epika (gdy pusta lub formuła bez wartości: B + „ - ” + C) |
| Lista Epiców | E | opis Epika |
| Biznesowy zakres MVP | D (Funkcja) | tytuł Story |
| Biznesowy zakres MVP | F (Opis kontekstowy) | opis Story |
| Biznesowy zakres MVP | G (Uwagi) | dopisywane do opisu jako „Uwagi: …” (przy pustym F opis = same Uwagi) |
| Biznesowy zakres MVP | E (Status: MVP / POST-MVP) | **komponent** Story (pole Components) |
| Biznesowy zakres MVP | B + „ - ” + C | Epik nadrzędny (dopasowany do kolumny D „Listy Epiców”) |

```bash
python create_epics_stories_from_sheet.py --file "Zakres MVP.xlsx" --project PROJ --dry-run
python create_epics_stories_from_sheet.py --file "Zakres MVP.xlsx" --project PROJ --limit 5   # test
python create_epics_stories_from_sheet.py --file "Zakres MVP.xlsx" --project PROJ
```

| Opcja | Działanie |
|---|---|
| `--limit N` | test: N pierwszych Story i tylko ich Epiki; pełne uruchomienie potem dokończy resztę bez duplikatów |
| `--status MVP` | tylko Story o tym statusie |
| `--only-epics` | tylko Epiki |
| `--no-status-component` | bez komponentów ze statusu |
| `--strip-epic-components` | zdejmuje MVP/POST-MVP z istniejących Epików (ustawione przez starszą wersję skryptu) |
| `--epic-sheet`, `--story-sheet`, `--story-type` | inne nazwy zakładek / typu Story |

**Zasady:**
- **Dopasowanie Story → Epik** nie zależy od wielkości liter, nadmiarowych
  spacji ani rodzaju myślnika. Gdy pełny tytuł się nie zgadza, skrypt łączy
  po numerze obszaru („2.”) i nazwie Epika (kolumna C) — tak obsługuje
  „2. Hub ofertowy / Backoffice (panel administracyjny)” vs „2. Hub ofertowy /
  Backoffice”. Pary dopasowane w ten sposób wypisuje w bloku `[INFO]`.
  Tytuł Epika zawsze pochodzi z kolumny D „Listy Epiców”.
- **Story bez Epika** (B + C nie pasuje do niczego) są pomijane i wypisywane
  na początku — zwykle to literówka w arkuszu.
- **Pusty opis w arkuszu = pusty opis w Jirze** (pole nie jest wysyłane).
- **Komponenty dostają tylko Story.** Brakujące komponenty (MVP, POST-MVP)
  skrypt zakłada w projekcie.
- **Weryfikacja po zapisie:** po utworzeniu skrypt odczytuje zgłoszenie i,
  jeśli Jira pominęła opis albo komponent, dopisuje go osobną aktualizacją.
  Przy istniejących zgłoszeniach uzupełnia pusty opis i brakujący komponent
  (wypełnionego opisu nie nadpisuje). Odrzucenie przez Jirę jest wypisywane
  z pełnym komunikatem.
- **Pole „Epic Name”**, jeśli Jira go wymaga przy tworzeniu, dostaje tytuł Epika.
- **Duplikaty:** przed utworzeniem skrypt szuka w projekcie Epika o tym samym
  tytule i Story o tym samym tytule pod tym Epikiem. Stan zapisuje w
  `epics_stories_state.json`. Po usunięciu zgłoszeń testowych w Jirze usuń też
  ten plik, inaczej skrypt uzna je za istniejące.

### 20b. Eksport z powrotem do arkusza (`export_stories_to_sheet.py`)

Pobiera Story z Jiry i zapisuje do zakładki „Jira” z kolumnami
**Klucz | Epik (klucz) | Epik | Tytuł | Opis** (klucze są linkami, opis jako
zwykły tekst, wiersze posortowane po kluczu):

```bash
# nowa zakładka w KOPII arkusza: "<plik> - eksport Jira.xlsx" (oryginał nietknięty)
python export_stories_to_sheet.py --project PROJ --file "Zakres MVP.xlsx"
# osobny plik
python export_stories_to_sheet.py --project PROJ --out stories_PROJ.xlsx
# własny filtr
python export_stories_to_sheet.py --project PROJ --jql 'project = PROJ AND component = MVP'
```

## 21. Pliki panelu (`migration_app/`)

Obsługę panelu opisuje część I. Pliki:

| Ścieżka | Co to |
|---|---|
| `../start.sh` | instalacja (`.venv`, zależności, przeglądarka) i start panelu; `panel.sh` tylko go wywołuje |
| `server.py` | serwer HTTP i API |
| `catalog.py` | katalog narzędzi: grupy, parametry formularzy, kiedy narzędzie zapisuje |
| `runner.py` | podprocesy, logi, wejście dla pytań skryptów, przerywanie |
| `asana_check.py` | sprawdzenie statusów i przypisań (można też uruchomić ręcznie) |
| `sheet_projects.py` | odczyt projektów z arkusza do `cache/sheet.json` |
| `setup_check.py` | testy połączeń i konfiguracji (Start, krok 2) |
| `people_source.py` | osoby z Asany i grup Jiry z propozycjami dopasowań |
| `status_source.py` | sekcje Asany i statusy projektu wzorcowego do mapowania |
| `jira_lookup.py` | role, osoby i grupy dla karty „Kopia projektu” |
| `cache/` | wyniki odczytów (arkusz, osoby, sekcje, testy) — nie jest w repozytorium |
| `static/` | interfejs (HTML, JS, CSS) |
| `runs/` | historia uruchomień (metadane i log) — nie jest w repozytorium |
| `checks/` | wyniki sprawdzeń statusów i osób — nie jest w repozytorium |
| `backups/` | kopie plików sprzed każdego zapisu z panelu — nie jest w repozytorium |

**Nowy skrypt w panelu** dodaje się jednym wpisem w liście `TOOLS` w
`catalog.py`: grupa, ścieżka skryptu, parametry formularza (rodzaje opisane na
początku pliku: `bool`, `str`/`int`, `keys`, `choice`, `pos`, `repeat`) i pole
`writes` (`never`, `always`, `unless:--dry-run`, `with:--apply`), od którego
zależy, czy panel poprosi o potwierdzenie.

## 22. Pozostałe skrypty pomocnicze

| Skrypt | Do czego służy |
|---|---|
| `add_admin_to_projects.py` | dodaje jedną osobę (accountId) do roli Administrator w istniejących projektach |
| `add_group_to_projects.py` | dodaje grupę do jednej lub wielu ról w istniejących projektach |
| `assign_category_to_projects.py` | przypisuje istniejącą kategorię projektu do podanych projektów |
| `copy_permission_scheme.py` | kopiuje schemat uprawnień pod nową nazwą i opcjonalnie podpina go do projektu |
| `empty_notification.py` | podpina wskazany schemat powiadomień do listy projektów (klucze w `RAW_PROJECT_KEYS`, dane logowania ze zmiennych środowiskowych `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`) |
| `atlassian_suspend_to_deactivate.py` | dezaktywuje konta o statusie „suspended” w organizacji Atlassian (Lifecycle API) |
| `backfill_jira_links.py` | jednorazowo uzupełnia kolumnę `jira_link` w arkuszu dla wierszy z `jira_key` |
| `list_sheet_tabs.py` | wypisuje dokładne nazwy zakładek arkusza Google |
| `check_jira_project_access.py` | sprawdza, czy e-mail i token API mają dostęp do odczytu w Jirze |
| `check_project_level_attachments.py` | wykrywa pliki przypięte do projektu w Asanie (nie do zadań), których synchronizacja nie przenosi |
| `migrate_project_level_attachments.py` | przenosi te pliki do Jiry jako załączniki zadania-kontenera „Zasoby projektu” |
| `bulk_configure_columns_simple.py` | wariant konfiguracji kolumn dla prostego układu 4 kolumn |
| `configure_columns_via_cli.py` | konfiguracja kolumn przez Jira Command Line Interface (Appfire) zamiast przeglądarki |
| `move_all_statuses_to_new.py` | przenosi wszystkie statusy tablicy do jednej kolumny „New In” |
| `create_analysis_for_stories.py` | dla każdego Story tworzy powiązane zadanie „Analysis” |
| `diagnose_field_config.py`, `diagnose_transition.py` | diagnostyka: surowa odpowiedź field configuration scheme i dostępne przejścia zadania |
| `project_lock.py` | moduł blokady pojedynczego projektu przy równoległych uruchomieniach (używany przez inne skrypty) |
