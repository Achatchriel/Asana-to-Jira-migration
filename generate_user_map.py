#!/usr/bin/env python3
"""
generate_user_map.py

Generuje user_map.json (Asana user gid -> Jira accountId) przez automatyczne
dopasowanie użytkowników po adresie e-mail. Używane przez asana_jira_sync.py
do ustawiania właściwego "assignee" na zadaniach.

UWAGA: Jira Cloud czasem NIE zwraca adresu e-mail w /rest/api/3/users/search
(zależy od ustawień prywatności organizacji: Site administration -> User
management -> Manage account visibility). Jeśli dopasowanie wypadnie słabo,
sprawdź tę opcję — bez adresu e-mail po stronie Jiry nie da się dopasować
automatycznie i trzeba by uzupełnić user_map.json ręcznie.

Użycie:
    python generate_user_map.py
"""
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)

JIRA_BASE_URL = os.environ["JIRA_BASE_URL"].rstrip("/")
JIRA_EMAIL = os.environ["JIRA_EMAIL"]
JIRA_API_TOKEN = os.environ["JIRA_API_TOKEN"]
JIRA_AUTH = (JIRA_EMAIL, JIRA_API_TOKEN)

ASANA_TOKEN = os.environ["ASANA_TOKEN"]
ASANA_WORKSPACE_GID = os.environ["ASANA_WORKSPACE_GID"]
ASANA_HEADERS = {"Authorization": f"Bearer {ASANA_TOKEN}"}
ASANA_API = "https://app.asana.com/api/1.0"

USER_MAP_FILE = Path("user_map.json")
REQUEST_TIMEOUT = 30

# Adresy w Jirze mają stały wzorzec Imię.Nazwisko@domena — inny niż to, co
# bywa w Asanie (różne domeny per klient, inny format). Gdy dopasowanie po
# wprost podanym adresie e-mail zawiedzie, próbujemy zbudować kandydata wg
# tego wzorca z imienia i nazwiska użytkownika Asany.
JIRA_EMAIL_DOMAIN = os.getenv("JIRA_EMAIL_DOMAIN", "twoja-firma.pl")


def transliterate(s: str) -> str:
    """Zamiana niemieckich znaków specjalnych na ich standardowy zapis ASCII
    (ü->ue, ö->oe, ä->ae, ß->ss) — na wypadek, gdyby adresy w Jirze były
    zapisane w tej formie zamiast z oryginalnymi znakami."""
    return (
        s.replace("ü", "ue").replace("ö", "oe").replace("ä", "ae").replace("ß", "ss")
         .replace("Ü", "Ue").replace("Ö", "Oe").replace("Ä", "Ae")
    )


def candidate_emails_from_name(name: str) -> list[str]:
    """Buduje możliwe adresy e-mail wg wzorca Imię.Nazwisko@domena na
    podstawie pełnego imienia i nazwiska (bierze pierwszy i ostatni człon,
    żeby obsłużyć drugie imiona). Zwraca kilka wariantów (z transliteracją
    i bez), do sprawdzenia po kolei."""
    parts = name.strip().split()
    if len(parts) < 2:
        return []
    first, last = parts[0], parts[-1]
    variants = set()
    for f in {first, transliterate(first)}:
        for l in {last, transliterate(last)}:
            variants.add(f"{f}.{l}@{JIRA_EMAIL_DOMAIN}".lower())
    return list(variants)


def get_asana_users(include_guests: bool = False) -> list[dict]:
    """Zwraca użytkowników przez workspace_memberships (nie /users), bo tylko
    ten endpoint ujawnia pole 'is_guest' — zwykłe /workspaces/{gid}/users
    zwraca WSZYSTKICH członków (w tym gości) bez rozróżnienia."""
    url = f"{ASANA_API}/workspaces/{ASANA_WORKSPACE_GID}/workspace_memberships"
    params = {"opt_fields": "user.name,user.email,is_guest,is_active", "limit": 100}
    memberships = []
    offset = None
    while True:
        if offset:
            params["offset"] = offset
        resp = requests.get(url, headers=ASANA_HEADERS, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
        memberships.extend(payload.get("data", []))
        offset = payload.get("next_page", {}).get("offset") if payload.get("next_page") else None
        if not offset:
            break

    users = []
    for m in memberships:
        if not m.get("is_active", True):
            continue
        if m.get("is_guest") and not include_guests:
            continue
        user = m.get("user") or {}
        if user.get("gid"):
            users.append(user)
    return users


def get_jira_users() -> list[dict]:
    """Zwraca wszystkich aktywnych użytkowników Jiry (z paginacją) —
    accountId + emailAddress (jeśli widoczny)."""
    url = f"{JIRA_BASE_URL}/rest/api/3/users/search"
    users = []
    start_at = 0
    max_results = 50
    while True:
        resp = requests.get(
            url, auth=JIRA_AUTH,
            params={"startAt": start_at, "maxResults": max_results},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        users.extend(batch)
        if len(batch) < max_results:
            break
        start_at += max_results
    # Tylko prawdziwi użytkownicy (pomijamy konta typu "app"/boty)
    return [u for u in users if u.get("accountType") == "atlassian"]


def main() -> None:
    include_guests = "--include-guests" in sys.argv

    print("Pobieram użytkowników z Asany...")
    asana_users = get_asana_users(include_guests=include_guests)
    label = "(łącznie z gośćmi)" if include_guests else "(bez gości)"
    print(f"  Znaleziono {len(asana_users)} użytkowników {label}.")

    print("Pobieram użytkowników z Jiry...")
    jira_users = get_jira_users()
    print(f"  Znaleziono {len(jira_users)} użytkowników.")

    jira_by_email = {
        u["emailAddress"].strip().lower(): u["accountId"]
        for u in jira_users
        if u.get("emailAddress")
    }

    if not jira_by_email:
        print(
            "\nUWAGA: żaden użytkownik Jiry nie ma widocznego adresu e-mail w API. "
            "Sprawdź: Site administration -> User management -> Manage account visibility "
            "(musi być ustawione tak, żeby e-mail był widoczny przez API dla adminów/aplikacji)."
        )

    user_map = {}
    matched_direct = []
    matched_by_pattern = []
    unmatched = []

    for au in asana_users:
        email = (au.get("email") or "").strip().lower()
        name = au.get("name", "")

        if email and email in jira_by_email:
            user_map[au["gid"]] = jira_by_email[email]
            matched_direct.append(name)
            continue

        matched_via_pattern = False
        for candidate in candidate_emails_from_name(name):
            if candidate in jira_by_email:
                user_map[au["gid"]] = jira_by_email[candidate]
                matched_by_pattern.append(f"{name} -> {candidate}")
                matched_via_pattern = True
                break

        if not matched_via_pattern:
            unmatched.append(f"{name} <{au.get('email', 'brak e-maila')}>")

    USER_MAP_FILE.write_text(json.dumps(user_map, indent=2, ensure_ascii=False), encoding="utf-8")

    total_matched = len(matched_direct) + len(matched_by_pattern)
    print(f"\nDopasowano: {total_matched}/{len(asana_users)}")
    print(f"  - po adresie e-mail wprost: {len(matched_direct)}")
    print(f"  - po wzorcu Imię.Nazwisko@{JIRA_EMAIL_DOMAIN}: {len(matched_by_pattern)}")
    if matched_by_pattern:
        print("\n  Dopasowani przez wzorzec (zweryfikuj wyrywkowo, czy to na pewno te same osoby):")
        for m in matched_by_pattern:
            print(f"    - {m}")

    if unmatched:
        print(f"\nNIE dopasowano ({len(unmatched)}) — te osoby będą importowane bez przypisania:")
        for u in unmatched:
            print(f"  - {u}")
        print(
            "\nJeśli to konto istnieje w Jirze pod innym adresem e-mail, dopisz wpis ręcznie "
            f"do {USER_MAP_FILE}: \"asana_user_gid\": \"jira_accountId\""
        )

    print(f"\nZapisano: {USER_MAP_FILE.resolve()}")


if __name__ == "__main__":
    main()
