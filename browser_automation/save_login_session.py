#!/usr/bin/env python3
"""
save_login_session.py

Otwiera przeglądarkę, żebyś zalogował się ręcznie do Jiry (w tym SSO/2FA,
jeśli macie), a potem zapisuje sesję (ciasteczka + localStorage) do pliku
auth_state.json. Ta sesja jest później używana przez
bulk_configure_columns.py, żeby nie logować się ręcznie 550 razy.

Sesja jest ważna tyle, ile normalna sesja przeglądarki w Jira (zwykle kilka
godzin do kilku dni, zależnie od polityki organizacji) — jeśli
bulk_configure_columns.py zacznie dostawać błędy związane z logowaniem,
uruchom ten skrypt ponownie.

Użycie:
    python save_login_session.py
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright


def _find_env_file() -> "Path | None":
    here = Path(__file__).resolve().parent
    for candidate in (here / ".env", here.parent / ".env"):
        if candidate.exists():
            return candidate
    return None


_env_path = _find_env_file()
if _env_path:
    load_dotenv(dotenv_path=_env_path, override=True)
else:
    print("Ostrzeżenie: nie znaleziono pliku .env ani obok tego skryptu, ani katalog wyżej.")

JIRA_BASE_URL = os.environ["JIRA_BASE_URL"].rstrip("/")
STATE_FILE = "auth_state.json"


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(JIRA_BASE_URL)

        print("=" * 70)
        print("Zaloguj się ręcznie w otwartym oknie przeglądarki (login, SSO, 2FA...).")
        print("Gdy zobaczysz swój pulpit / listę projektów Jiry, wróć tutaj")
        print("i naciśnij ENTER w tym terminalu.")
        print("=" * 70)
        input()

        context.storage_state(path=STATE_FILE)
        print(f"Zapisano sesję do {STATE_FILE}.")
        browser.close()


if __name__ == "__main__":
    main()
