#!/usr/bin/env python3
"""
diff_two_issues.py

Pobiera opis + listę sygnatur komentarzy dla DWÓCH konkretnych zadań
Jiry i pokazuje różnicę - do zrozumienia, DLACZEGO porównanie treści
w resolve_duplicates.py nie uznało ich za identyczne.

Użycie:
    python diff_two_issues.py BRESBBRG-267 BRESBBRG-327
"""
import re
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core

COMMENT_PREFIX_RE = re.compile(r"^\[Asana.*?\]$")


def extract_comment_signature(comment_body):
    try:
        return comment_body["content"][0]["content"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return None


def fetch(key):
    resp = requests.get(
        f"{core.JIRA_BASE_URL}/rest/api/3/issue/{key}",
        auth=core.jira_auth(),
        params={"fields": "summary,description,attachment,comment"},
    )
    resp.raise_for_status()
    fields = resp.json()["fields"]
    comments = fields.get("comment", {}).get("comments", []) or []
    sigs = sorted(s for s in (extract_comment_signature(c.get("body", {})) for c in comments) if s)
    attachments = sorted(a.get("filename", "") for a in (fields.get("attachment") or []))
    return {
        "summary": fields.get("summary"),
        "description": fields.get("description"),
        "attachments": attachments,
        "comment_sigs": sigs,
    }


key1, key2 = sys.argv[1], sys.argv[2]
d1, d2 = fetch(key1), fetch(key2)

print(f"=== {key1} ===")
print(f"Tytuł: {d1['summary']}")
print(f"Załączniki: {d1['attachments']}")
print(f"Liczba komentarzy: {len(d1['comment_sigs'])}")
for s in d1['comment_sigs']:
    print(f"    {s}")

print(f"\n=== {key2} ===")
print(f"Tytuł: {d2['summary']}")
print(f"Załączniki: {d2['attachments']}")
print(f"Liczba komentarzy: {len(d2['comment_sigs'])}")
for s in d2['comment_sigs']:
    print(f"    {s}")

print("\n" + "=" * 60)
print("RÓŻNICE:")
print(f"  Opis identyczny: {d1['description'] == d2['description']}")
print(f"  Załączniki identyczne: {d1['attachments'] == d2['attachments']}")
print(f"  Komentarze identyczne: {d1['comment_sigs'] == d2['comment_sigs']}")

set1, set2 = set(d1['comment_sigs']), set(d2['comment_sigs'])
only_in_1 = set1 - set2
only_in_2 = set2 - set1
print(f"  Komentarze TYLKO w {key1}: {sorted(only_in_1)}")
print(f"  Komentarze TYLKO w {key2}: {sorted(only_in_2)}")
if set1 <= set2:
    print(f"  -> {key1} to PODZBIÓR {key2} (mniej komentarzy - starsza migawka tego samego zadania)")
elif set2 <= set1:
    print(f"  -> {key2} to PODZBIÓR {key1} (mniej komentarzy - starsza migawka tego samego zadania)")
