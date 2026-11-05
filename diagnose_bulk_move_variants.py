#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
diagnose_bulk_move_variants.py

Bezpieczna diagnostyka - próbuje KILKU wariantów payloadu Bulk Move na
JEDNYM, wskazanym zadaniu, żeby ustalić który format Jira faktycznie
akceptuje. Bezpieczne: każdy nieudany wariant zwraca 400 i NIC nie
zmienia w danych - dopiero pierwszy wariant, który zwróci 200/201/202,
faktycznie coś zmieni (i wtedy skrypt się zatrzymuje, nie próbuje dalej).

Kontekst: dla zadań AKTUALNIE typu Task (poziom 0), próba jednoczesnej
zmiany na Sub-task (poziom -1) + ustawienia NOWEGO rodzica jednym Bulk
Move zwraca ogólnikowe "Invalid request payload" - mimo że payload
wygląda identycznie jak w przypadkach, które wcześniej zadziałały (tam
zadania były JUŻ typu Sub-task, tylko zmieniał się rodzic - inny
scenariusz niż zmiana poziomu + rodzica naraz).

Użycie:
    python diagnose_bulk_move_variants.py --key GSESLA-65 --parent GSESLA-XX
"""
import argparse
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core
import convert_to_epic as cte


def try_variant(name: str, payload: dict) -> bool:
    print(f"\n--- Wariant: {name} ---")
    print(f"Payload: {payload}")
    resp = requests.post(
        f"{core.JIRA_BASE_URL}/rest/api/3/bulk/issues/move",
        auth=core.jira_auth(), headers={"Content-Type": "application/json"},
        json=payload,
    )
    print(f"Status: {resp.status_code}")
    print(f"Odpowiedź: {resp.text[:500]}")
    if resp.status_code in (200, 201, 202):
        print(">>> TEN WARIANT ZADZIAŁAŁ! <<<")
        task_id = resp.json().get("taskId") or resp.json().get("id")
        if task_id:
            print(f"Czekam na zakończenie (taskId={task_id})...")
            deadline = time.time() + 60
            while time.time() < deadline:
                poll = requests.get(f"{core.JIRA_BASE_URL}/rest/api/3/task/{task_id}", auth=core.jira_auth())
                if poll.status_code == 200:
                    status = poll.json().get("status", "")
                    print(f"  status Bulk Move: {status}")
                    if status == "COMPLETE":
                        break
                    if status in ("FAILED", "CANCELLED"):
                        print(f"  [UWAGA] Bulk Move zakończyło się niepowodzeniem mimo 2xx na starcie: {poll.text[:300]}")
                        break
                time.sleep(2)
        return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", required=True, help="Zadanie do przeniesienia (np. GSESLA-65)")
    parser.add_argument("--parent", required=True, help="Docelowy rodzic (np. GSESLA-XX)")
    args = parser.parse_args()

    issue_key = args.key
    parent_key = args.parent
    project_key = issue_key.split("-")[0]

    subtask_type_id = cte.get_regular_subtask_type_id(project_key)
    project_numeric_id = cte._get_project_numeric_id(project_key)
    parent_numeric_id = cte._get_issue_numeric_id(parent_key)
    print(f"issue_key={issue_key} parent_key={parent_key} project_key={project_key}")
    print(f"subtask_type_id={subtask_type_id} project_numeric_id={project_numeric_id} parent_numeric_id={parent_numeric_id}")

    variants = [
        ("NOWY (wg oficjalnej dokumentacji): klucz = project,type_id,PARENT_KEY, rodzic W KLUCZU, bez targetParentId", {
            "targetToSourcesMapping": {
                f"{project_key},{subtask_type_id},{parent_key}": {
                    "issueIdsOrKeys": [issue_key],
                    "inferFieldDefaults": True,
                    "inferStatusDefaults": True,
                    "inferSubtaskTypeDefault": True,
                }
            }
        }),
        ("jak wyżej, ale z sendBulkNotification: False na górze", {
            "sendBulkNotification": False,
            "targetToSourcesMapping": {
                f"{project_key},{subtask_type_id},{parent_key}": {
                    "issueIdsOrKeys": [issue_key],
                    "inferFieldDefaults": True,
                    "inferStatusDefaults": True,
                    "inferSubtaskTypeDefault": True,
                }
            }
        }),
        ("obecny (project,project_numeric_id) + wszystkie infer + targetParentId", {
            "sendBulkNotification": False,
            "targetToSourcesMapping": {
                f"{project_key},{project_numeric_id}": {
                    "issueIdsOrKeys": [issue_key],
                    "targetIssueTypeId": subtask_type_id,
                    "targetParentId": parent_numeric_id,
                    "inferClassificationDefaults": True,
                    "inferFieldDefaults": True,
                    "inferStatusDefaults": True,
                }
            }
        }),
        ("bez pól infer* w ogóle", {
            "sendBulkNotification": False,
            "targetToSourcesMapping": {
                f"{project_key},{project_numeric_id}": {
                    "issueIdsOrKeys": [issue_key],
                    "targetIssueTypeId": subtask_type_id,
                    "targetParentId": parent_numeric_id,
                }
            }
        }),
        ("klucz mapowania = project,TYPE_ID (jak przy Task, nie project_numeric_id)", {
            "sendBulkNotification": False,
            "targetToSourcesMapping": {
                f"{project_key},{subtask_type_id}": {
                    "issueIdsOrKeys": [issue_key],
                    "targetParentId": parent_numeric_id,
                    "inferClassificationDefaults": True,
                    "inferFieldDefaults": True,
                    "inferStatusDefaults": True,
                }
            }
        }),
        ("bez sendBulkNotification w ogóle", {
            "targetToSourcesMapping": {
                f"{project_key},{project_numeric_id}": {
                    "issueIdsOrKeys": [issue_key],
                    "targetIssueTypeId": subtask_type_id,
                    "targetParentId": parent_numeric_id,
                    "inferClassificationDefaults": True,
                    "inferFieldDefaults": True,
                    "inferStatusDefaults": True,
                }
            }
        }),
        ("targetParentId jako klucz Jira (nie numeryczne ID)", {
            "sendBulkNotification": False,
            "targetToSourcesMapping": {
                f"{project_key},{project_numeric_id}": {
                    "issueIdsOrKeys": [issue_key],
                    "targetIssueTypeId": subtask_type_id,
                    "targetParentId": parent_key,
                    "inferClassificationDefaults": True,
                    "inferFieldDefaults": True,
                    "inferStatusDefaults": True,
                }
            }
        }),
    ]

    for name, payload in variants:
        if try_variant(name, payload):
            print(f"\n>>> ZNALEZIONY DZIAŁAJĄCY WARIANT: {name} <<<")
            return

    print("\n>>> ŻADEN WARIANT NIE ZADZIAŁAŁ. <<<")


if __name__ == "__main__":
    main()
