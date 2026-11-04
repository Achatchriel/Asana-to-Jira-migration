#!/usr/bin/env python3
"""
diagnose_deep_chains.py

Read-only. Dla wierszy ze statusem WYMAGA_RECZNEJ_DECYZJI_GLEBOKA (z
revert_false_epics_report.csv) zbiera unikalne correct_parent i pokazuje
ich AKTUALNY stan w Jirze: typ, obecnego rodzica, i WSZYSTKIE ich
obecne dzieci (nie tylko ten jeden kandydat z raportu - potrzebujemy
pełnego obrazu, bo awans correct_parent na Epika wpłynąłby na KAŻDE
jego dziecko, nie tylko na to, przez które go znaleźliśmy).

Użycie:
    python diagnose_deep_chains.py --report revert_false_epics_report.csv
"""
import argparse
import csv
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core


def get_live_issue_state(issue_key: str) -> "dict | None":
    resp = requests.get(
        f"{core.JIRA_BASE_URL}/rest/api/3/issue/{issue_key}",
        auth=core.jira_auth(), params={"fields": "issuetype,parent,summary"},
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    fields = resp.json()["fields"]
    parent = fields.get("parent")
    return {
        "type": fields["issuetype"]["name"],
        "parent": parent["key"] if parent else None,
        "summary": fields.get("summary", ""),
    }


def get_children(issue_key: str) -> list:
    children = []
    next_token = None
    while True:
        params = {"jql": f'parent = "{issue_key}"', "fields": "summary,issuetype", "maxResults": 100}
        if next_token:
            params["nextPageToken"] = next_token
        resp = requests.get(f"{core.JIRA_BASE_URL}/rest/api/3/search/jql", auth=core.jira_auth(), params=params)
        resp.raise_for_status()
        data = resp.json()
        for issue in data.get("issues", []):
            children.append((issue["key"], issue["fields"]["issuetype"]["name"], issue["fields"]["summary"]))
        next_token = data.get("nextPageToken")
        if not next_token:
            break
    return children


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, help="revert_false_epics_report.csv")
    args = parser.parse_args()

    with open(args.report, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["status"] == "WYMAGA_RECZNEJ_DECYZJI_GLEBOKA"]

    print(f"Wczytano {len(rows)} wierszy GLEBOKA z {args.report}.\n")

    # Grupuj kandydatów per correct_parent (kilka kandydatów może mieć wspólnego rodzica)
    by_parent = {}
    for r in rows:
        by_parent.setdefault(r["correct_parent"], []).append(r["kandydat"])

    print(f"{len(by_parent)} unikalnych correct_parent do sprawdzenia.\n")
    print("=" * 70)

    for parent_key, candidates in sorted(by_parent.items()):
        state = get_live_issue_state(parent_key)
        if state is None:
            print(f"\n[{parent_key}] NIE ISTNIEJE (404)! Kandydaci którzy na niego wskazują: {candidates}")
            continue
        children = get_children(parent_key)
        other_children = [c for c in children if c[0] not in candidates]
        print(f"\n[{parent_key}] '{state['summary'][:60]}'")
        print(f"    obecny typ: {state['type']}  |  obecny rodzic: {state['parent']}")
        print(f"    kandydaci z GLEBOKA wskazujący na niego: {', '.join(candidates)}")
        print(f"    WSZYSTKICH dzieci w Jirze: {len(children)}")
        if other_children:
            print(f"    dzieci INNE niż nasi kandydaci (te też ucierpią przy awansie na Epika!):")
            for k, t, s in other_children:
                print(f"        {k} ({t}): '{s[:60]}'")
        else:
            print(f"    -> brak innych dzieci poza naszymi kandydatami (bezpieczniejszy awans)")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
