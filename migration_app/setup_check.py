#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
setup_check.py — TYLKO ODCZYT. Testy połączeń i konfiguracji przed migracją
(zakładka Start w panelu). Wynik: migration_app/cache/setup_check.json.

Celowo NIE importuje skryptów jiraSync (asana_jira_sync.py kończy się błędem,
gdy brakuje zmiennych) - czyta .env sam i odpytuje API bezpośrednio, żeby
pokazać czytelny komunikat dla każdej brakującej rzeczy.
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "cache" / "setup_check.json"
ENV = {k: (v or "").strip() for k, v in dotenv_values(ROOT / ".env").items()} if (ROOT / ".env").exists() else {}
TIMEOUT = 20
SHEET_HEADERS = ["project_name", "jira_key", "asana_project_link", "lead_account_id", "board_template_id",
                 "project_type_key", "template_key", "description"]


def result(ok, messages, data=None, warn=False):
    return {"ok": ok, "warn": warn and ok, "messages": messages, "data": data or {}}


def check_jira() -> dict:
    base, email, token = ENV.get("JIRA_BASE_URL", "").rstrip("/"), ENV.get("JIRA_EMAIL"), ENV.get("JIRA_API_TOKEN")
    if not (base and email and token):
        return result(False, ["Uzupełnij JIRA_BASE_URL, JIRA_EMAIL i JIRA_API_TOKEN."])
    auth = (email, token)
    try:
        me = requests.get(f"{base}/rest/api/3/myself", auth=auth, timeout=TIMEOUT)
    except requests.RequestException as exc:
        return result(False, [f"Brak połączenia z {base}: {exc}"])
    if me.status_code == 401:
        return result(False, ["Jira odrzuciła dane logowania (401) - sprawdź e-mail i token API."])
    if not me.ok:
        return result(False, [f"Jira zwróciła {me.status_code}: {me.text[:200]}"])
    me = me.json()
    msgs, warn = [f"Zalogowano jako {me.get('displayName')} ({me.get('emailAddress') or email})."], False
    data = {"account_id": me.get("accountId"), "name": me.get("displayName")}
    perms = requests.get(f"{base}/rest/api/3/mypermissions", auth=auth, timeout=TIMEOUT,
                         params={"permissions": "ADMINISTER"})
    is_admin = perms.ok and perms.json().get("permissions", {}).get("ADMINISTER", {}).get("havePermission")
    data["admin"] = bool(is_admin)
    if is_admin:
        msgs.append("Konto ma uprawnienie administratora Jiry ✓")
    else:
        warn = True
        msgs.append("Konto NIE jest administratorem Jiry - tworzenie projektów i kopiowanie schematów się nie uda.")

    tpl = ENV.get("JIRA_TEMPLATE_PROJECT_KEY")
    if not tpl:
        warn = True
        msgs.append("Brak JIRA_TEMPLATE_PROJECT_KEY - nowe projekty dostaną domyślną konfigurację Jiry, "
                    "a panel nie ma skąd wziąć listy statusów do mapowania sekcji.")
    else:
        p = requests.get(f"{base}/rest/api/3/project/{tpl}", auth=auth, timeout=TIMEOUT)
        if not p.ok:
            return result(False, msgs + [f"Projekt wzorcowy {tpl} nie istnieje albo konto nie ma do niego dostępu ({p.status_code})."], data)
        p = p.json()
        data["template"] = {"key": tpl, "name": p.get("name"), "style": p.get("style"), "lead": (p.get("lead") or {}).get("displayName")}
        msgs.append(f"Projekt wzorcowy {tpl}: {p.get('name')} ✓")
        if p.get("style") == "next-gen":
            warn = True
            msgs.append("Projekt wzorcowy jest team-managed - schematy kopiują się tylko z projektów company-managed.")
        boards = requests.get(f"{base}/rest/agile/1.0/board", auth=auth, timeout=TIMEOUT, params={"projectKeyOrId": tpl})
        bl = boards.json().get("values", []) if boards.ok else []
        data["template"]["boards"] = [{"id": b["id"], "name": b["name"]} for b in bl]
        board_id = ENV.get("JIRA_TEMPLATE_BOARD_ID")
        if board_id and not any(str(b["id"]) == board_id for b in bl):
            warn = True
            msgs.append(f"JIRA_TEMPLATE_BOARD_ID={board_id} nie jest tablicą projektu {tpl} (tablice: "
                        f"{', '.join(f'{b['id']} {b['name']}' for b in bl) or 'brak'}).")
        elif not board_id and len(bl) > 1:
            warn = True
            msgs.append(f"Projekt wzorcowy ma {len(bl)} tablice - wskaż wzorzec kolumn w JIRA_TEMPLATE_BOARD_ID: "
                        + ", ".join(f"{b['id']} ({b['name']})" for b in bl))
        elif not bl:
            warn = True
            msgs.append("Projekt wzorcowy nie ma tablicy - kroki 2-3 (kolumny) nie będą miały wzorca.")
        else:
            msgs.append(f"Tablica wzorcowa: {(next((b for b in bl if str(b['id']) == board_id), bl[0]))['name']} ✓")

        # Pola wymagane przy tworzeniu Taska, których skrypt nie uzupełni sam
        meta = requests.get(f"{base}/rest/api/3/issue/createmeta/{tpl}/issuetypes", auth=auth, timeout=TIMEOUT)
        task = next((t for t in (meta.json().get("issueTypes") or meta.json().get("values") or []) if t.get("name") == "Task"), None) if meta.ok else None
        if task:
            fm = requests.get(f"{base}/rest/api/3/issue/createmeta/{tpl}/issuetypes/{task['id']}", auth=auth, timeout=TIMEOUT)
            fields = (fm.json().get("fields") or fm.json().get("values") or []) if fm.ok else []
            standard = {"summary", "issuetype", "project", "reporter", "parent"}
            hard = []
            for f in fields:
                fid = f.get("fieldId") or f.get("key")
                if not f.get("required") or fid in standard or f.get("hasDefaultValue"):
                    continue
                allowed = f.get("allowedValues") or []
                if len(allowed) == 1:
                    continue  # skrypt sam wybierze jedyną możliwość
                if fid == ENV.get("DEFAULT_TYPE_FIELD_ID"):
                    continue
                hard.append(f"{f.get('name')} ({fid}" + (f", możliwe: {', '.join(str(a.get('value') or a.get('name')) for a in allowed[:6])}" if allowed else "") + ")")
            if hard:
                warn = True
                msgs.append("Pola wymagane przy tworzeniu zadania, których synchronizacja nie uzupełni sama: "
                            + "; ".join(hard) + ". Ustaw DEFAULT_TYPE_FIELD_ID/VALUE albo zdejmij wymagalność.")
    return result(True, msgs, data, warn)


def check_asana() -> dict:
    token = ENV.get("ASANA_TOKEN")
    if not token:
        return result(False, ["Uzupełnij ASANA_TOKEN."])
    try:
        r = requests.get("https://app.asana.com/api/1.0/users/me", headers={"Authorization": f"Bearer {token}"},
                         params={"opt_fields": "name,email,workspaces.name"}, timeout=TIMEOUT)
    except requests.RequestException as exc:
        return result(False, [f"Brak połączenia z Asaną: {exc}"])
    if r.status_code == 401:
        return result(False, ["Asana odrzuciła token (401)."])
    if not r.ok:
        return result(False, [f"Asana zwróciła {r.status_code}: {r.text[:200]}"])
    me = r.json()["data"]
    ws = [{"gid": w["gid"], "name": w["name"]} for w in me.get("workspaces", [])]
    msgs = [f"Token należy do {me.get('name')} ({me.get('email')})."]
    gid = ENV.get("ASANA_WORKSPACE_GID")
    data = {"workspaces": ws}
    if not gid:
        return result(False, msgs + ["Wybierz workspace (ASANA_WORKSPACE_GID): " + ", ".join(f"{w['name']} ({w['gid']})" for w in ws)], data)
    hit = next((w for w in ws if w["gid"] == gid), None)
    if not hit:
        return result(False, msgs + [f"ASANA_WORKSPACE_GID={gid} nie jest workspace'em tego tokenu."], data)
    return result(True, msgs + [f"Workspace: {hit['name']} ✓"], data)


def check_google() -> dict:
    path = ENV.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not path:
        return result(False, ["Wklej klucz konta serwisowego Google albo ustaw GOOGLE_APPLICATION_CREDENTIALS."])
    key_file = (ROOT / path).resolve() if not os.path.isabs(path) else Path(path)
    if not key_file.exists():
        return result(False, [f"Brak pliku klucza: {path}. Wklej klucz JSON w panelu."])
    try:
        info = json.loads(key_file.read_text(encoding="utf-8"))
        client_email = info["client_email"]
    except Exception as exc:  # noqa: BLE001
        return result(False, [f"Plik {path} nie jest kluczem konta serwisowego: {exc}"])
    msgs, data = [f"Konto serwisowe: {client_email}"], {"client_email": client_email}
    sheet_id = ENV.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        return result(False, msgs + ["Uzupełnij GOOGLE_SHEET_ID (ID z adresu arkusza)."], data)
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        creds = service_account.Credentials.from_service_account_file(
            str(key_file), scopes=["https://www.googleapis.com/auth/spreadsheets"])
        svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
        meta = svc.spreadsheets().get(spreadsheetId=sheet_id, fields="properties.title,sheets.properties.title").execute()
    except Exception as exc:  # noqa: BLE001
        text = str(exc)
        if "403" in text or "PERMISSION_DENIED" in text:
            return result(False, msgs + [f"Brak dostępu do arkusza - udostępnij go adresowi {client_email}."], data)
        if "404" in text:
            return result(False, msgs + ["Arkusz o tym ID nie istnieje."], data)
        return result(False, msgs + [f"Błąd odczytu arkusza: {text[:300]}"], data)
    tabs = [s["properties"]["title"] for s in meta.get("sheets", [])]
    data.update(title=meta["properties"]["title"], tabs=tabs)
    msgs.append(f"Arkusz: {meta['properties']['title']} ✓")
    rng = ENV.get("GOOGLE_SHEET_RANGE") or "Arkusz1!A:F"
    tab = rng.split("!")[0].strip("'")
    if tab not in tabs:
        return result(False, msgs + [f"Zakładka „{tab}” z GOOGLE_SHEET_RANGE nie istnieje. Dostępne: {', '.join(tabs)}."], data)
    values = svc.spreadsheets().values().get(spreadsheetId=sheet_id, range=rng).execute().get("values", [])
    header = [h.strip().lower() for h in (values[0] if values else [])]
    rows = values[1:]
    data.update(header=header, rows=len(rows))
    warn = False
    if not header:
        return result(False, msgs + [f"Zakres {rng} jest pusty - pierwszy wiersz musi zawierać nagłówki: "
                                     + ", ".join(SHEET_HEADERS[:3]) + " …"], data)
    if "asana_project_link" not in header:
        return result(False, msgs + ["Brak kolumny asana_project_link - bez niej żaden projekt nie zostanie zmigrowany."], data)
    unknown = [h for h in header if h and h not in SHEET_HEADERS and h != "jira_link"]
    missing = [h for h in ("project_name", "jira_key") if h not in header]
    if missing:
        warn = True
        msgs.append(f"Brak kolumn {', '.join(missing)} - skrypt poradzi sobie bez nich, ale warto je mieć "
                    "(jira_key: skrypt wpisze tam wygenerowane klucze, jeśli konto ma prawo edycji arkusza).")
    if unknown:
        msgs.append(f"Kolumny ignorowane przez skrypty: {', '.join(unknown)}.")
    idx = header.index("asana_project_link")
    with_link = sum(1 for r in rows if len(r) > idx and re.search(r"asana\.com/", r[idx] or ""))
    data["rows_with_link"] = with_link
    msgs.append(f"Wierszy: {len(rows)}, z linkiem do Asany: {with_link}.")
    if rows and with_link < len(rows):
        warn = True
        msgs.append(f"{len(rows) - with_link} wierszy bez poprawnego linku do Asany zostanie pominiętych.")
    return result(True, msgs, data, warn)


def main() -> None:
    out = {"checked_at": time.time()}
    for name, fn in (("jira", check_jira), ("asana", check_asana), ("google", check_google)):
        try:
            out[name] = fn()
        except Exception as exc:  # noqa: BLE001
            out[name] = result(False, [f"{type(exc).__name__}: {exc}"])
        print(f"{name}: {'OK' if out[name]['ok'] else 'BŁĄD'}", file=sys.stderr)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
