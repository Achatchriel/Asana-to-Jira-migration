#!/usr/bin/env python3
"""
check_jira_project_access.py

Sprawdza, czy PODANE dane logowania (e-mail + token API) mają dostęp do
ODCZYTU danych w Jirze - trzy niezależne poziomy:

  1. Czy token w ogóle jest ważny (uwierzytelnienie).
  2. Czy widzi METADANE konkretnego projektu (nazwa, typ) - to sama
     widoczność projektu (uprawnienie "Browse Projects").
  3. Czy faktycznie może ODCZYTAĆ ZADANIA w tym projekcie (JQL search) -
     to bywa inne niż samo "widzę, że projekt istnieje": zdarza się, że
     token widzi metadane projektu, ale nie ma dostępu do jego zawartości
     (np. ograniczenia na poziomie ról/uprawnień szczegółowych w projekcie).

Bez podanego --project, wypisuje WSZYSTKIE projekty widoczne dla tego
tokenu (do szybkiego sprawdzenia zakresu dostępu bez zgadywania klucza).

Dane logowania - trzy sposoby, sprawdzane w tej kolejności:
  1. Argumenty wiersza poleceń (--base-url --email --token).
  2. Zmienne w .env (JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN), jeśli
     powyższe nie podane.
  3. Interaktywne pytanie (wklejasz przy uruchomieniu) - jeśli ani jedno,
     ani drugie niedostępne. Token wpisywany przez getpass (NIE pojawia
     się na ekranie/w historii terminala).

Użycie:
    python check_jira_project_access.py                              # .env albo pytanie
    python check_jira_project_access.py --project ABC                 # + konkretny projekt
    python check_jira_project_access.py --email a@b.pl --token xyz --base-url https://firma.atlassian.net --project ABC
"""
import argparse
import getpass
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)


def resolve_credentials(args: argparse.Namespace) -> tuple[str, str, str]:
    base_url = args.base_url or os.getenv("JIRA_BASE_URL") or input("Adres instancji Jiry (np. https://firma.atlassian.net): ").strip()
    email = args.email or os.getenv("JIRA_EMAIL") or input("E-mail: ").strip()
    token = args.token or os.getenv("JIRA_API_TOKEN") or getpass.getpass("Token API (niewidoczny podczas wpisywania): ").strip()
    return base_url.rstrip("/"), email, token


def check_token_valid(base_url: str, auth: tuple) -> bool:
    print("1. Sprawdzam, czy token w ogóle jest ważny (/rest/api/3/myself)...")
    resp = requests.get(f"{base_url}/rest/api/3/myself", auth=auth, timeout=30)
    print(f"   Status: {resp.status_code}")
    if resp.status_code != 200:
        print(f"   Odpowiedź: {resp.text}")
        print("\n   TOKEN NIEPRAWIDŁOWY/WYGASŁY albo złe dane logowania.")
        return False
    me = resp.json()
    print(f"   OK — token należy do: {me.get('displayName')} ({me.get('emailAddress', 'brak e-maila')})")
    return True


def list_visible_projects(base_url: str, auth: tuple) -> None:
    print("\n2. Listuję WSZYSTKIE projekty widoczne dla tego tokenu...")
    projects = []
    start_at = 0
    while True:
        resp = requests.get(
            f"{base_url}/rest/api/3/project/search",
            auth=auth, params={"startAt": start_at, "maxResults": 50}, timeout=30,
        )
        if resp.status_code != 200:
            print(f"   [BŁĄD] Nie udało się pobrać listy projektów: {resp.status_code} {resp.text}")
            return
        payload = resp.json()
        projects.extend(payload.get("values", []))
        if payload.get("isLast", True):
            break
        start_at += 50

    if not projects:
        print("   Token nie widzi ŻADNEGO projektu — sprawdź uprawnienia konta w Jirze "
              "(Administracja -> Role globalne / uprawnienia projektów).")
        return

    print(f"   Widoczne projekty ({len(projects)}):")
    for p in projects:
        print(f"     {p['key']:12} {p['name']}")
    print("\n   Uruchom z --project KLUCZ dla dowolnego z powyższych, żeby sprawdzić dostęp do zadań.")


def check_project_access(base_url: str, auth: tuple, project_key: str) -> None:
    print(f"\n2. Sprawdzam widoczność metadanych projektu '{project_key}'...")
    resp = requests.get(f"{base_url}/rest/api/3/project/{project_key}", auth=auth, timeout=30)
    print(f"   Status: {resp.status_code}")
    if resp.status_code == 404:
        print(f"   [BRAK DOSTĘPU] Token NIE widzi projektu '{project_key}' — albo projekt nie istnieje, "
              f"albo token nie ma nawet podstawowego uprawnienia 'Browse Projects' dla niego.")
        return
    if resp.status_code != 200:
        print(f"   [BŁĄD] Nieoczekiwana odpowiedź: {resp.text}")
        return
    project = resp.json()
    print(f"   OK — widzi projekt: '{project.get('name')}' (typ: {project.get('projectTypeKey')})")

    print(f"\n3. Sprawdzam faktyczny odczyt ZADAŃ w '{project_key}' (JQL search)...")
    # UWAGA: stary endpoint GET /rest/api/3/search został przez Atlassiana
    # CAŁKOWICIE USUNIĘTY (nie tylko oznaczony jako przestarzały) - trzeba
    # używać /rest/api/3/search/jql. Nowy endpoint NIE zwraca już pola
    # "total" (które wcześniej pokazywało łączną liczbę zadań spełniających
    # zapytanie) - pokazujemy więc liczbę zwróconych zadań na tej stronie
    # wyników, nie łączną liczbę w całym projekcie.
    resp2 = requests.get(
        f"{base_url}/rest/api/3/search/jql",
        auth=auth,
        params={"jql": f"project = {project_key}", "maxResults": 5, "fields": "summary,status"},
        timeout=30,
    )
    print(f"   Status: {resp2.status_code}")
    if resp2.status_code != 200:
        print(f"   [BRAK DOSTĘPU DO ZADAŃ] {resp2.text}")
        print("   Token widzi PROJEKT, ale NIE MA dostępu do jego zadań — sprawdź uprawnienia "
              f"'Browse Projects' konkretnie w roli/uprawnieniach projektu '{project_key}' "
              "(mogą być inne niż globalne uprawnienia konta).")
        return

    payload = resp2.json()
    issues = payload.get("issues", [])
    more_available = bool(payload.get("nextPageToken"))
    print(f"   OK — token ma pełny dostęp do odczytu zadań. "
          f"Pobrano {len(issues)} zadań (nowe API Jiry nie zwraca już łącznej liczby — "
          f"{'jest ich więcej niż pokazano' if more_available else 'to wszystkie, jeśli mniej niż 5'}).")
    if issues:
        print("   Przykładowe zadania:")
        for issue in issues:
            status_name = issue["fields"].get("status", {}).get("name", "?")
            print(f"     {issue['key']:12} [{status_name}] {issue['fields'].get('summary', '')}")

    if not issues:
        print("\n4-5. Brak zadań w tym projekcie — nie da się sprawdzić dostępu do "
              "komentarzy/załączników bez chociaż jednego istniejącego zadania.")
        return

    sample_key = issues[0]["key"]
    check_comments_access(base_url, auth, sample_key)
    check_attachments_access(base_url, auth, sample_key)


def check_comments_access(base_url: str, auth: tuple, issue_key: str) -> None:
    print(f"\n4. Sprawdzam odczyt KOMENTARZY (na przykładzie {issue_key})...")
    resp = requests.get(f"{base_url}/rest/api/3/issue/{issue_key}/comment", auth=auth, timeout=30)
    print(f"   Status: {resp.status_code}")
    if resp.status_code != 200:
        print(f"   [BRAK DOSTĘPU DO KOMENTARZY] {resp.text}")
        return
    comments = resp.json().get("comments", [])
    if not comments:
        print(f"   OK — endpoint dostępny, ale {issue_key} nie ma jeszcze żadnych komentarzy do pokazania.")
        return
    print(f"   OK — token ma dostęp do odczytu komentarzy. Znaleziono {len(comments)}.")
    last = comments[-1]
    author = (last.get("author") or {}).get("displayName", "?")
    print(f"   Ostatni komentarz: autor '{author}', utworzony {last.get('created', '?')}.")


def check_attachments_access(base_url: str, auth: tuple, issue_key: str) -> None:
    print(f"\n5. Sprawdzam odczyt ZAŁĄCZNIKÓW (na przykładzie {issue_key})...")
    resp = requests.get(
        f"{base_url}/rest/api/3/issue/{issue_key}", auth=auth,
        params={"fields": "attachment"}, timeout=30,
    )
    print(f"   Status (metadane): {resp.status_code}")
    if resp.status_code != 200:
        print(f"   [BRAK DOSTĘPU DO METADANYCH ZAŁĄCZNIKÓW] {resp.text}")
        return

    attachments = resp.json().get("fields", {}).get("attachment", [])
    if not attachments:
        print(f"   OK — endpoint dostępny, ale {issue_key} nie ma jeszcze żadnych załączników do pokazania.")
        return
    print(f"   OK — metadane załączników dostępne. Znaleziono {len(attachments)}.")

    first = attachments[0]
    print(f"   Sprawdzam FAKTYCZNY odczyt treści pierwszego załącznika ('{first.get('filename')}', "
          f"{first.get('size', 0)} B)...")
    content_resp = requests.get(first["content"], auth=auth, timeout=30)
    print(f"   Status (pobranie treści): {content_resp.status_code}")
    if content_resp.status_code != 200:
        print(f"   [BRAK DOSTĘPU DO TREŚCI] Metadane widoczne, ale pobranie samego pliku się nie powiodło "
              f"— sprawdź uprawnienia do treści załączników osobno od metadanych.")
        return
    print(f"   OK — token ma PEŁNY dostęp do odczytu treści załączników "
          f"(pobrano {len(content_resp.content)} B).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=str, default=None, help="Klucz projektu do sprawdzenia (np. ABC).")
    parser.add_argument("--base-url", type=str, default=None, help="Adres instancji Jiry (nadpisuje .env).")
    parser.add_argument("--email", type=str, default=None, help="E-mail (nadpisuje .env).")
    parser.add_argument("--token", type=str, default=None, help="Token API (nadpisuje .env). Uwaga: widoczny w historii powłoki - wolisz podać interaktywnie, zostaw puste.")
    args = parser.parse_args()

    base_url, email, token = resolve_credentials(args)
    auth = (email, token)

    if not check_token_valid(base_url, auth):
        return

    if args.project:
        check_project_access(base_url, auth, args.project.strip().upper())
    else:
        list_visible_projects(base_url, auth)


if __name__ == "__main__":
    main()
