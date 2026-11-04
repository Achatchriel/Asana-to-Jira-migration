#!/usr/bin/env python3
"""
fix_stale_resolution.py

Czyści pole Resolution w zgłoszeniach, które są OTWARTE (status poza kategorią
Done), a mimo to mają ustawione rozwiązanie. Jira pokazuje je wtedy jako
zamknięte (przekreślony klucz, wypadają z filtrów "unresolved"), choć stoją
w kolumnie otwartej. Powstaje to, gdy zgłoszenie było zamknięte (np. Fertig),
a potem przestawione z powrotem, a przejście w workflow nie czyści rozwiązania.

Trwała naprawa jest w workflow (administrator Jiry): przejścia wychodzące ze
statusów końcowych powinny mieć funkcję końcową "wyczyść pole Resolution".
Ten skrypt naprawia zgłoszenia już dotknięte.

Czyszczenie idzie przez edycję zgłoszenia, więc wymaga, żeby pole Resolution
było na ekranie edycji danego typu zgłoszenia. Skrypt sprawdza to najpierw
(editmeta) i dla typów, gdzie się nie da, tylko raportuje.

Użycie:
    python fix_stale_resolution.py --project-keys PROJ3            # raport
    python fix_stale_resolution.py --project-keys PROJ3 --apply    # czyszczenie
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from asana_jira_sync import JIRA_AUTH, JIRA_BASE_URL, REQUEST_TIMEOUT, request_with_retry  # noqa: E402


def find_stale(project_key: str) -> list:
    issues, token = [], None
    jql = f'project = "{project_key}" AND resolution is not EMPTY AND statusCategory != Done ORDER BY key'
    while True:
        body = {"jql": jql, "fields": ["summary", "status", "resolution", "issuetype"], "maxResults": 100}
        if token:
            body["nextPageToken"] = token
        r = request_with_retry("POST", f"{JIRA_BASE_URL}/rest/api/3/search/jql", auth=JIRA_AUTH,
                               json=body, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        for i in data.get("issues", []):
            f = i["fields"]
            issues.append({"key": i["key"], "type": f["issuetype"]["name"], "status": f["status"]["name"],
                           "resolution": (f.get("resolution") or {}).get("name"), "summary": f.get("summary") or ""})
        token = data.get("nextPageToken")
        if not token:
            return issues


def resolution_editable(issue_key: str) -> bool:
    r = request_with_retry("GET", f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/editmeta",
                           auth=JIRA_AUTH, timeout=REQUEST_TIMEOUT)
    return r.status_code == 200 and "resolution" in (r.json().get("fields") or {})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project-keys", required=True)
    parser.add_argument("--apply", action="store_true", help="Wyczyść rozwiązanie (domyślnie tylko raport)")
    args = parser.parse_args()

    import jira_sync as _core
    for project in _core.refuse_if_protected([k.strip().upper() for k in args.project_keys.split(",") if k.strip()],
                                              "fix_stale_resolution"):
        stale = find_stale(project)
        print(f"\n{project}: otwartych zgłoszeń z ustawionym rozwiązaniem: {len(stale)}")
        if not stale:
            continue
        by_type = Counter(i["type"] for i in stale)
        print("  wg typu: " + ", ".join(f"{t}: {n}" for t, n in by_type.most_common()))
        for i in stale[:10]:
            print(f"    {i['key']} [{i['type']}, {i['status']}, rozwiązanie: {i['resolution']}] {i['summary'][:50]}")
        if len(stale) > 10:
            print(f"    ... i jeszcze {len(stale) - 10}")

        # Edytowalność sprawdzana raz na typ zgłoszenia (ekran edycji zależy od typu).
        editable = {t: resolution_editable(next(i["key"] for i in stale if i["type"] == t)) for t in by_type}
        blocked = [t for t, ok in editable.items() if not ok]
        if blocked:
            print(f"  [UWAGA] Dla typów: {', '.join(blocked)} pole Resolution nie jest na ekranie edycji - "
                  f"skrypt ich nie wyczyści. Administrator może tymczasowo dodać Resolution do ekranu edycji "
                  f"albo naprawić workflow (funkcja końcowa czyszcząca rozwiązanie).")
        if not args.apply:
            print("  [TRYB RAPORTU] Nic nie zmieniono. Uruchom z --apply, żeby wyczyścić.")
            continue

        done, failed = 0, []
        for i in [i for i in stale if editable[i["type"]]]:
            r = request_with_retry("PUT", f"{JIRA_BASE_URL}/rest/api/3/issue/{i['key']}", auth=JIRA_AUTH,
                                   json={"fields": {"resolution": None}}, params={"notifyUsers": "false"},
                                   timeout=REQUEST_TIMEOUT)
            if r.status_code < 300:
                done += 1
            else:
                failed.append((i["key"], f"{r.status_code} {r.text[:150]}"))
        print(f"  Wyczyszczono: {done}, błędy: {len(failed)}, pominięte (typ bez pola na ekranie): "
              f"{sum(1 for i in stale if not editable[i['type']])}")
        for k, e in failed[:10]:
            print(f"    [BŁĄD] {k}: {e}")


if __name__ == "__main__":
    main()
