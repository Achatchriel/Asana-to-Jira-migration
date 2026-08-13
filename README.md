# Synchronizacja: Google Sheets (wybrane projekty z Asany) → Jira

Skrypt tworzy w Jira projekty **tylko dla tych pozycji, które są w arkuszu Google** —
arkusz jest źródłem prawdy dotyczącym tego, co ma powstać w Jira.

Dodatkowo skrypt pobiera listę projektów z Asany, żeby (jeśli znajdzie dopasowanie
po nazwie) wzbogacić opis nowego projektu w Jira o notatki z Asany. Jeśli dany wiersz
z arkusza nie ma odpowiednika w Asanie, projekt i tak zostanie utworzony — po prostu
bez opisu.

## 1. Instalacja

```bash
pip install -r requirements.txt
```

## 2. Konfiguracja

1. Skopiuj `.env.example` do `.env`:
   ```bash
   cp .env.example .env
   ```
2. Uzupełnij wartości w `.env`:
   - **Jira**: adres instancji, e-mail, [token API](https://id.atlassian.com/manage-profile/security/api-tokens)
   - **Asana**: [Personal Access Token](https://app.asana.com/0/my-apps), GID workspace'u
   - **Google Sheets**: konto serwisowe (JSON key) z [Google Cloud Console](https://console.cloud.google.com/)
     — pamiętaj, żeby **udostępnić arkusz** adresowi e-mail konta serwisowego.

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

## 5. Automatyczne dodawanie grupy deweloperów

Jeśli chcesz, żeby konkretna grupa Jira była automatycznie dodawana do roli
"Developers" (albo innej roli) w każdym nowo tworzonym projekcie, ustaw w `.env`:

```
DEVELOPER_GROUP_NAME=nazwa-grupy
DEVELOPER_ROLE_NAME=Developers
```

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

## 6. Automatyczne dodawanie administratora

Analogicznie możesz dodać **konkretnego użytkownika** (np. siebie) do roli
administratora w każdym nowo tworzonym projekcie:

```
ADMIN_ACCOUNT_ID=twój-accountId
ADMIN_ROLE_NAME=Administrators
```

`ADMIN_ACCOUNT_ID` to `accountId`, nie e-mail — znajdziesz go przez:
```bash
python get_my_account_id.py
```

Działa identycznie jak grupa deweloperów (sekcja 5): sprawdzenie roli raz na
starcie, dodanie po każdym utworzeniu projektu, ostrzeżenie zamiast twardego
błędu, jeśli się nie powiedzie.

## 7. Format arkusza Google

Pierwszy wiersz to nagłówki (wielkość liter nieistotna). **Wymagana jest tylko kolumna
`project_name`.** Reszta jest opcjonalna:

| project_name              | jira_key | project_type_key | template_key | lead_account_id       | description | board_template_id | asana_project_link |
|-----------------------------|----------|--------------------|-----------------|--------------------------|--------------|--------------------|----------------------|
| Kampania Q4                 |          | software           |                 | 5b10a2844c20165700ede21 |              |                    | https://app.asana.com/0/1234567890123456/list |
| Redesign strony głównej     |          | business           |                 |                          | Redesign UI  | 7276               |                      |

- **`board_template_id`** — używane TYLKO przez `bulk_configure_columns.py` (nie
  przez `jira_sync.py`). ID tablicy wzorcowej dla UKŁADU KOLUMN tego konkretnego
  projektu — przydatne, gdy projekt wzorcowy ma kilka tablic o różnych układach
  (np. `REFERENCE2` ma zarówno 11-kolumnowy wzorzec na tablicy 6110, jak i
  prostszy 4-kolumnowy na tablicach 7276/7273/7274/7275). Puste = użyty zostanie
  domyślny wzorzec (`JIRA_TEMPLATE_BOARD_ID` z `.env`).

- **`asana_project_link`** — opcjonalny, BEZPOŚREDNI link do projektu w
  Asanie (skopiowany z paska adresu przeglądarki). Jeśli podany, używany
  ZAMIAST dopasowania po nazwie — dużo pewniejsze, bo omija literówki,
  duplikaty nazw i zmiany nazwy projektu w Asanie. Obsługiwane formaty:
  `https://app.asana.com/0/<id>/list` oraz `https://app.asana.com/1/<workspace>/project/<id>/list`
  (i warianty `/board` zamiast `/list`). Puste = dopasowanie po nazwie,
  tak jak dotychczas. Używane przez `jira_sync.py` (wzbogacenie opisu) i
  `asana_jira_sync.py` (synchronizacja zadań).

- **`jira_key` jest teraz opcjonalna.** Jeśli ją zostawisz pustą (albo nie dodasz tej
  kolumny wcale), skrypt sam wygeneruje klucz na podstawie nazwy projektu
  (np. "Kampania Q4" → `KMPQ4`, "Redesign strony głównej" → `RSG`). Jeśli wolisz
  ręcznie kontrolować klucze — po prostu wpisz je w tej kolumnie.
- Wygenerowane klucze są zapisywane w lokalnym pliku `project_key_cache.json`
  (ścieżka konfigurowalna przez `PROJECT_KEY_CACHE_FILE` w `.env`), dzięki czemu
  **ta sama nazwa projektu zawsze dostanie ten sam klucz** przy kolejnych
  uruchomieniach skryptu — nie trzeba się martwić o powtarzalność.
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

## 9. Jak działa generowanie kluczy

Dla nazwy projektu bez podanego `jira_key`:
1. Kilka słów → inicjały każdego słowa (np. "Portal Klienta B2B" → `PKB`).
2. Jedno słowo → pierwsze do 10 znaków, wielkimi literami.
3. Jeśli wygenerowany klucz koliduje z już przydzielonym w tym samym przebiegu
   (lub w cache), doklejany jest numer (`PKB2`, `PKB3`, ...).
4. Wynik zapisywany jest do `project_key_cache.json`, żeby był trwały między
   uruchomieniami.

Jeśli chcesz mieć pełną kontrolę nad konkretnym kluczem — po prostu wpisz go
ręcznie w kolumnie `jira_key` dla danego wiersza, a skrypt go nie nadpisze.

## 10. Co skrypt wypisuje na końcu

- liczbę utworzonych i pominiętych projektów,
- błędy (np. brak uprawnień, brak leada, zajęty klucz),
- wiersze arkusza bez dopasowania w Asanie (projekt i tak powstaje, bez opisu),
- ścieżkę do pliku z mapowaniem nazwa → klucz.

## 11. Synchronizacja zadań Asana → Jira (`asana_jira_sync.py`)

Osobny skrypt (obok `jira_sync.py`, który tworzy tylko *projekty*) — synchronizuje
*zadania* wewnątrz już utworzonych projektów. Jednokierunkowo: Asana → Jira.

### Co synchronizuje

- Tytuł, opis, przypisany, termin — zawsze.
- Status — przez sekcję Asany, zmapowaną na status Jira (patrz niżej).
- Komentarze — tylko prawdziwe (bez zdarzeń systemowych typu "zmienił termin"),
  z prefiksem `[Asana — Autor, data]` w treści (Jira nie pozwala podszyć się
  pod oryginalnego autora bez dodatkowej konfiguracji).
- Załączniki — pobierane z Asany i wgrywane do Jiry.

### Jak nie duplikować przy wielokrotnym uruchomieniu

Lokalny plik `task_sync_state.json` (tworzony automatycznie, **nie kasować
między uruchomieniami**) zapamiętuje dla każdego zadania: jego klucz Jira,
ostatni znany `modified_at` z Asany, oraz ID już przeniesionych komentarzy
i załączników. Zapisywany po każdym projekcie, nie dopiero na końcu — bezpieczne
przy przerwaniu w połowie.

### Konfiguracja przed pierwszym uruchomieniem

1. **Wymagane pola** — Twój projekt wzorcowy może mieć pola obowiązkowe przy
   tworzeniu zadania (np. niestandardowe "Type", "Department"), których Asana
   nie ma. Sprawdź je:
   ```bash
   python check_required_fields.py KLUCZPROJEKTU
   ```
   Pola z jedną dozwoloną wartością uzupełniają się automatycznie. Pole "Type"
   ma ustawioną wartość domyślną `NONBILLABLE` (zmień w `.env`:
   `DEFAULT_TYPE_FIELD_VALUE=...`, albo `DEFAULT_TYPE_FIELD_ID=...`, jeśli to
   inne pole niż `customfield_10188`).

2. **Mapowanie sekcja Asany → status Jira** — sekcje Asany bywają etapami/
   kamieniami milowymi (np. "3. Kickoff"), nie nazwami pasującymi wprost do
   statusów Jiry ("In Progress"). Wypisz sekcje projektu:
   ```bash
   python list_asana_sections.py "Dokładna nazwa projektu w Asanie"
   ```
   Uzupełnij `section_status_map.json` (klucz = nazwa sekcji, wartość =
   nazwa statusu Jira). Ta tabela jest globalna — współdzielona między
   wszystkimi projektami, dokładaj wpisy w miarę odkrywania nowych sekcji.
   Sekcja bez wpisu = zadanie zostaje przy domyślnym statusie startowym
   (skrypt wypisze listę takich sekcji na końcu przebiegu).

3. **Mapowanie użytkowników** — bez tego zadania importują się bez
   przypisanego użytkownika:
   ```bash
   python generate_user_map.py
   ```
   Dopasowuje automatycznie po adresie e-mail; jeśli adresy w Jirze mają stały
   wzorzec (`Imię.Nazwisko@domena`, różny od adresu w Asanie), skrypt próbuje
   też zbudować taki adres z imienia/nazwiska (z wariantem transliterowanym
   niemieckich znaków ü/ö/ä/ß). Domena ustawiana przez `JIRA_EMAIL_DOMAIN`
   w `.env` (domyślnie `twoja-firma.pl`). Domyślnie **pomija gości** Asany —
   żeby ich uwzględnić: `python generate_user_map.py --include-guests`.
   Wynik: `user_map.json`. Osoby niedopasowane automatycznie możesz dopisać
   ręcznie do tego pliku (`"asana_user_gid": "jira_accountId"`).

### Uruchomienie

```bash
python asana_jira_sync.py --dry-run --limit 1 --project-keys KLUCZ   # podgląd bez zapisu
python asana_jira_sync.py --limit 1 --project-keys KLUCZ             # faktyczny zapis, 1 projekt
python asana_jira_sync.py                                             # wszystkie projekty z arkusza
```

`--dry-run` pokazuje, co **by** zrobił skrypt (odczyt jest bezpieczny, więc
nawet w dry-run zobaczysz realną liczbę komentarzy/załączników do przeniesienia),
bez faktycznego tworzenia/zmieniania niczego w Jirze.

### Czego świadomie jeszcze nie ma (Etap 3)

- Kierunku zwrotnego Jira → Asana (na razie tylko Asana → Jira).
- Konwersji bogatego formatowania z Asany (linki, listy, pogrubienia) —
  opis i komentarze trafiają jako zwykły tekst.

### Pliki tego pakietu

| Plik | Rola |
|---|---|
| `asana_jira_sync.py` | główny skrypt synchronizujący |
| `list_asana_sections.py` | wypisuje sekcje projektu Asany (do `section_status_map.json`) |
| `section_status_map.json` | mapowanie: sekcja Asany → status Jira (uzupełniasz ręcznie) |
| `generate_user_map.py` | generuje `user_map.json` (dopasowanie po e-mailu) |
| `user_map.json` | mapowanie: Asana user gid → Jira accountId |
| `check_required_fields.py` | diagnostyka: jakie pola są wymagane przy tworzeniu zadania |
| `task_sync_state.json` | **automatycznie tworzony** stan synchronizacji — nie kasować |

## 11a. Właściwa kolejność kroków (WAŻNE)

Statusy do kolumn tablicy przypisuje się **ręcznie** (automatyzacja drag-and-drop
okazała się zbyt zawodna w tej wersji Jiry — patrz `browser_automation/README.md`).
To wymusza konkretną kolejność — synchronizacja zadań (`asana_jira_sync.py`)
NIE powinna być uruchamiana od razu po utworzeniu projektu, bo tablica nie
jest jeszcze gotowa:

1. `python jira_sync.py` — utwórz projekt(y).
2. `python bulk_configure_columns.py` (albo `..._simple.py`) — utwórz kolumny.
3. **Ty ręcznie** przeciągasz statusy do właściwych kolumn w Jira (ściągawka
   wypisywana na końcu kroku 2).
4. Dopiero teraz: `python asana_jira_sync.py` — synchronizacja zadań ma sens,
   bo trafiają na już gotową, poprawnie skonfigurowaną tablicę.

## 12. Dodatkowe funkcje (zrealizowane)

Wszystko, co było w poprzedniej wersji README jako "możliwe rozszerzenie",
jest już zrealizowane:

- **Bogate formatowanie z Asany** (linki, listy, pogrubienie/kursywa/podkreślenie/
  przekreślenie) — konwertowane na ADF (opis/komentarze w Jirze) i z powrotem
  na HTML (przy synchronizacji zwrotnej). Patrz `html_to_adf`/`adf_to_html`
  w `asana_jira_sync.py`.
- **Aktualizacja istniejących projektów** — `jira_sync.py` nie tylko pomija
  już istniejące projekty, ale aktualizuje ich **opis**, jeśli różni się od
  tego w arkuszu/Asanie (patrz podsumowanie: "Zaktualizowany opis").
- **Log do pliku + podsumowanie na Slack** — nowy moduł `notify.py`,
  używany przez `jira_sync.py` i `asana_jira_sync.py`. Log trafia do
  `logs/<skrypt>_<data-godzina>.log` automatycznie (nic nie trzeba
  włączać). Slack jest opcjonalny — ustaw w `.env`:
  ```
  SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
  ```
  Bez tego skrypty po prostu nie wysyłają nic na Slacka (bez błędu).
  E-mail nie został zaimplementowany (wymaga danych SMTP) — daj znać, jeśli
  potrzebny, dopiszę analogicznie.
- **Kierunek zwrotny synchronizacji (Jira → Asana)** — osobny skrypt
  `jira_asana_sync.py` (nie tryb w `asana_jira_sync.py`, bo iteruje inaczej:
  po już powiązanych parach z `task_sync_state.json`, nie po zadaniach
  Asany). Synchronizuje tytuł, opis, termin, przypisanego (przez odwróconą
  `user_map.json`) oraz nowe komentarze/załączniki z Jiry do Asany.
  Rozstrzyganie konfliktów: proste porównanie znacznika czasu ostatniej
  modyfikacji w Jirze — **nie ma pełnego mergowania pól**, jeśli oba systemy
  zmieniły to samo zadanie w tym samym czasie. Status NIE jest
  synchronizowany zwrotnie (sekcje Asany i statusy workflow Jiry to różne
  koncepcje, nie ma naturalnego mapowania 1:1 w tę stronę).
  ```bash
  python jira_asana_sync.py --dry-run --limit 1 --project-keys KLUCZ
  python jira_asana_sync.py
  ```
  **Kolejność przy używaniu obu kierunków:** uruchamiaj naprzemiennie
  (`asana_jira_sync.py`, potem `jira_asana_sync.py`), nie równolegle —
  unikniesz sytuacji, gdzie oba nadpisują się nawzajem w trakcie działania.

Daj znać, jeśli chcesz coś jeszcze dopisać albo zmienić w którymkolwiek
z tych mechanizmów.
