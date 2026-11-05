#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
export_stories_to_sheet.py — eksport Story z Jiry do arkusza .xlsx.

Kolumny: Klucz | Epik (klucz) | Epik | Tytuł | Opis

Użycie:
    # nowa zakładka "Jira" w KOPII arkusza źródłowego (oryginał nietknięty)
    python export_stories_to_sheet.py --project PROJT --file "Zakres MVP.xlsx"

    # osobny plik tylko z eksportem
    python export_stories_to_sheet.py --project PROJT --out stories_PROJT.xlsx

Opcje:
    --issue-type Story        typ zgłoszeń (domyślnie Story)
    --sheet-name Jira         nazwa zakładki
    --jql "..."               własny filtr zamiast project + typ
"""
import argparse
import sys
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core

API = "/rest/api/3"


def search(jql, fields):
    out, token = [], None
    while True:
        body = {"jql": jql, "maxResults": 100, "fields": fields}
        if token:
            body["nextPageToken"] = token
        r = requests.post(f"{core.JIRA_BASE_URL}{API}/search/jql", auth=core.jira_auth(), json=body,
                          headers={"Accept": "application/json"}, timeout=60)
        if r.status_code >= 300:
            raise SystemExit(f"[BŁĄD] Wyszukiwanie: HTTP {r.status_code} {r.text[:300]}")
        d = r.json()
        out += d.get("issues", [])
        token = d.get("nextPageToken")
        if not token:
            return out


def adf_to_text(node):
    """Atlassian Document Format -> zwykły tekst (akapity, listy, podziały wierszy)."""
    if not node:
        return ""
    if isinstance(node, str):
        return node
    t = node.get("type")
    kids = node.get("content") or []
    if t == "text":
        return node.get("text", "")
    if t == "hardBreak":
        return "\n"
    if t in ("bulletList", "orderedList"):
        items = []
        for i, li in enumerate(kids, 1):
            mark = f"{i}. " if t == "orderedList" else "- "
            items.append(mark + adf_to_text(li).strip())
        return "\n".join(items) + "\n\n"
    inner = "".join(adf_to_text(k) for k in kids)
    if t in ("paragraph", "heading", "blockquote", "codeBlock"):
        return inner + "\n\n"
    return inner


def key_num(key):
    p, _, n = key.rpartition("-")
    return (p, int(n) if n.isdigit() else 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--issue-type", default="Story")
    ap.add_argument("--jql")
    ap.add_argument("--file", help="Arkusz źródłowy — eksport trafi do nowej zakładki w jego KOPII")
    ap.add_argument("--out", help="Plik wynikowy (domyślnie: '<plik> - eksport Jira.xlsx' albo stories_<KLUCZ>.xlsx)")
    ap.add_argument("--sheet-name", default="Jira")
    a = ap.parse_args()

    jql = a.jql or f'project = "{a.project}" AND issuetype = "{a.issue_type}" ORDER BY key ASC'
    issues = search(jql, ["summary", "description", "parent"])
    issues.sort(key=lambda i: key_num(i["key"]))
    print(f"Pobrano {len(issues)} zgłoszeń ({jql})")

    rows, no_epic = [], 0
    for i in issues:
        f = i["fields"]
        parent = f.get("parent") or {}
        if not parent:
            no_epic += 1
        rows.append([
            i["key"],
            parent.get("key", ""),
            (parent.get("fields") or {}).get("summary", ""),
            f.get("summary", ""),
            adf_to_text(f.get("description")).strip(),
        ])

    if a.file:
        wb = openpyxl.load_workbook(a.file)
        out = Path(a.out or Path(a.file).with_name(Path(a.file).stem + " - eksport Jira.xlsx"))
    else:
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        out = Path(a.out or f"stories_{a.project}.xlsx")
    name = a.sheet_name
    if name in wb.sheetnames:
        del wb[name]
    ws = wb.create_sheet(name)

    header = ["Klucz", "Epik (klucz)", "Epik", "Tytuł", "Opis"]
    ws.append(header)
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="FFFF00")
    base = core.JIRA_BASE_URL.rstrip("/")
    for r in rows:
        ws.append(r)
        row = ws.max_row
        for col in (1, 2):
            cell = ws.cell(row=row, column=col)
            if cell.value:
                cell.hyperlink = f"{base}/browse/{cell.value}"
                cell.font = Font(color="0563C1", underline="single")
        ws.cell(row=row, column=5).alignment = Alignment(wrap_text=True, vertical="top")
        for col in (1, 2, 3, 4):
            ws.cell(row=row, column=col).alignment = Alignment(vertical="top", wrap_text=col in (3, 4))
    for col, width in zip("ABCDE", (14, 14, 45, 45, 90)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:E{ws.max_row}"

    wb.save(out)
    print(f"Zapisano: {out}  (zakładka '{name}', {len(rows)} wierszy"
          + (f", bez Epika: {no_epic}" if no_epic else "") + ")")


if __name__ == "__main__":
    main()
