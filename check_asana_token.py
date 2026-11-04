#!/usr/bin/env python3
"""
check_asana_token.py

Szybki test: sprawdza, czy ASANA_TOKEN w ogóle działa (przez /users/me,
które NIE wymaga dostępu do żadnego konkretnego projektu) — pomaga
odróżnić "token nieprawidłowy/wygasły" od "brak dostępu do KONKRETNEGO
projektu".

Użycie:
    python check_asana_token.py
"""
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)

ASANA_TOKEN = os.environ["ASANA_TOKEN"]
ASANA_WORKSPACE_GID = os.environ["ASANA_WORKSPACE_GID"]
ASANA_HEADERS = {"Authorization": f"Bearer {ASANA_TOKEN}"}
ASANA_API = "https://app.asana.com/api/1.0"


def main() -> None:
    print("1. Sprawdzam, czy token w ogóle jest ważny (/users/me)...")
    resp = requests.get(f"{ASANA_API}/users/me", headers=ASANA_HEADERS, timeout=30)
    print(f"   Status: {resp.status_code}")
    if resp.status_code != 200:
        print(f"   Odpowiedź: {resp.text}")
        print("\n   TOKEN NIEPRAWIDŁOWY/WYGASŁY — wygeneruj nowy w Asanie:")
        print("   My Settings -> Apps -> Developer apps -> Personal Access Token")
        return
    me = resp.json()["data"]
    print(f"   OK — token należy do: {me.get('name')} ({me.get('email', 'brak e-maila')})")

    print("\n2. Sprawdzam dostęp do workspace'u z .env...")
    resp2 = requests.get(f"{ASANA_API}/workspaces/{ASANA_WORKSPACE_GID}", headers=ASANA_HEADERS, timeout=30)
    print(f"   Status: {resp2.status_code}")
    if resp2.status_code != 200:
        print(f"   Odpowiedź: {resp2.text}")
        print("   Token jest ważny, ale NIE MA dostępu do workspace'u ustawionego w ASANA_WORKSPACE_GID.")
        return
    ws = resp2.json()["data"]
    print(f"   OK — workspace: {ws.get('name')}")

    print("\n3. Listuję kilka pierwszych projektów w tym workspace (do porównania z linkami w arkuszu)...")
    resp3 = requests.get(
        f"{ASANA_API}/workspaces/{ASANA_WORKSPACE_GID}/projects",
        headers=ASANA_HEADERS, params={"opt_fields": "name,gid", "limit": 10}, timeout=30,
    )
    if resp3.status_code == 200:
        projects = resp3.json().get("data", [])
        print(f"   Widoczne projekty ({len(projects)} z pierwszej strony):")
        for p in projects:
            print(f"     - {p['name']} (gid={p['gid']})")
    else:
        print(f"   Nie udało się pobrać listy projektów: {resp3.status_code} {resp3.text}")

    print("\nWNIOSEK: jeśli kroki 1-2 przeszły OK, ale konkretne projekty z arkusza")
    print("nadal dają 403, to te projekty należą do INNEGO workspace'u/organizacji")
    print("niż ten, do którego token ma dostęp — porównaj gid-y z listy powyżej")
    print("z tymi w linkach w arkuszu.")


if __name__ == "__main__":
    main()
