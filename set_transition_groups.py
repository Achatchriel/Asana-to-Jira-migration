#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
set_transition_groups.py — tworzy grupy i ogranicza przejścia workflow do grup.

Na podstawie transition_groups.yaml:
  1. zakłada brakujące grupy (te z 'existing: true' tylko sprawdza),
  2. w każdym wskazanym workflow ustawia na przejściach warunek
     "Restrict transition" (system:restrict-issue-transition) z listą grup.
     Inne warunki przejścia zostają; poprzedni warunek grupowy jest zastępowany,
     więc skrypt można uruchamiać wielokrotnie.
  3. sekcja 'propagate': te same reguły nakłada na przejścia o tej samej nazwie
     i tym samym statusie docelowym w innych workflow (np. "Rejected" w Task).

Użycie:
    python set_transition_groups.py --dry-run          # podgląd + walidacja Jiry, nic nie zmienia
    python set_transition_groups.py
    python set_transition_groups.py --workflow Workflow-Deployment
    python set_transition_groups.py --config inny.yaml

Reguły zapisane tym skryptem przetrwają 'build_jira_schema.py --only workflow_update'
(przenosi reguły przejść o tej samej nazwie i celu).
"""
import argparse
import sys
import uuid
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core

API = "/rest/api/3"
RULE = "system:restrict-issue-transition"
KEEP_STATUS = ("id", "name", "statusCategory", "statusReference", "description")


def call(method, path, **kw):
    return requests.request(method, f"{core.JIRA_BASE_URL}{path}", auth=core.jira_auth(), timeout=60,
                            headers={"Content-Type": "application/json", "Accept": "application/json"}, **kw)


def find_group(name):
    r = call("GET", f"{API}/group/bulk", params={"groupName": name, "maxResults": 50})
    r.raise_for_status()
    for g in r.json().get("values", []):
        if g["name"].lower() == name.lower():
            return g["groupId"]
    return None


def resolve_groups(cfg, dry_run):
    ids, errors = {}, []
    print("=== GRUPY ===")
    for alias, spec in (cfg.get("groups") or {}).items():
        name = spec["name"]
        gid = find_group(name)
        if gid:
            print(f"  [ISTNIEJE] {alias:10} -> '{name}' ({gid})")
        elif spec.get("existing"):
            errors.append(f"Grupa '{name}' ({alias}) miała istnieć, a jej nie ma")
            print(f"  [BŁĄD] '{name}' ({alias}) — oznaczona jako istniejąca, nie znaleziono")
            continue
        elif dry_run:
            print(f"  [DRY-RUN] Utworzyłbym grupę '{name}' ({alias})")
            gid = f"<nowa:{name}>"
        else:
            r = call("POST", f"{API}/group", json={"name": name})
            if r.status_code >= 300:
                errors.append(f"Tworzenie grupy '{name}': HTTP {r.status_code} {r.text[:200]}")
                print(f"  [BŁĄD] Tworzenie '{name}': HTTP {r.status_code} {r.text[:200]}")
                continue
            gid = r.json()["groupId"]
            print(f"  [UTWORZONO] {alias:10} -> '{name}' ({gid})")
        ids[alias] = (gid, name)
    return ids, errors


def with_group_condition(conditions, group_ids):
    """Zwraca nowe 'conditions' przejścia: stary warunek grupowy usunięty,
    nowy dodany. Pozostałe warunki zachowane (ALL = wszystkie muszą być spełnione)."""
    rule = {"ruleKey": RULE, "id": str(uuid.uuid4()), "parameters": {
        "accountIds": "", "roleIds": "", "groupIds": ",".join(group_ids), "permissionKeys": "",
        "groupCustomFields": "", "allowUserCustomFields": "", "denyUserCustomFields": ""}}
    if not conditions or (not conditions.get("conditions") and not conditions.get("conditionGroups")):
        return {"operation": "ALL", "conditions": [rule], "conditionGroups": []}
    others = [c for c in conditions.get("conditions") or [] if c.get("ruleKey") != RULE]
    groups = conditions.get("conditionGroups") or []
    if conditions.get("operation", "ALL") == "ALL":
        return {"operation": "ALL", "conditions": others + [rule], "conditionGroups": groups}
    # istniejące warunki połączone przez ANY — zachowaj je jako podgrupę
    old = {"operation": "ANY", "conditions": others, "conditionGroups": groups}
    return {"operation": "ALL", "conditions": [rule], "conditionGroups": [old] if (others or groups) else []}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default="transition_groups.yaml")
    p.add_argument("--workflow", help="Tylko te workflow (po przecinku)")
    p.add_argument("--dry-run", action="store_true", help="Podgląd + walidacja Jiry, nic nie zmienia")
    a = p.parse_args()

    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    groups, errors = resolve_groups(cfg, a.dry_run)
    always = cfg.get("always_allowed") or []

    wanted = {w.strip().lower() for w in a.workflow.split(",")} if a.workflow else None
    plan = {k: [dict(r) for r in (v or [])] for k, v in (cfg.get("workflows") or {}).items()}
    # 'propagate': te same reguły na przejściach o tej samej nazwie i statusie
    # docelowym w innych workflow. Brak takiego przejścia nie jest tam błędem.
    for prop in cfg.get("propagate") or []:
        src_rules = plan.get(prop["from"]) or []
        for target in prop.get("to_workflows") or []:
            have = {(r["transition"].lower(), r["to"].lower()) for r in plan.get(target, [])}
            for r in src_rules:
                if (r["transition"].lower(), r["to"].lower()) not in have:
                    plan.setdefault(target, []).append({**r, "optional": True, "via": prop["from"]})
    for wf_name, rules in plan.items():
        if wanted and wf_name.lower() not in wanted:
            continue
        print(f"\n=== {wf_name} ===")
        if not rules:
            print("  (brak reguł w konfiguracji)")
            continue
        r = call("POST", f"{API}/workflows", json={"workflowNames": [wf_name]})
        if r.status_code >= 300:
            errors.append(f"{wf_name}: pobranie HTTP {r.status_code}")
            print(f"  [BŁĄD] HTTP {r.status_code}: {r.text[:300]}")
            continue
        data = r.json()
        wf = next((w for w in data.get("workflows", []) if w["name"] == wf_name), None)
        if not wf:
            errors.append(f"{wf_name}: nie istnieje w Jirze")
            print("  [BŁĄD] Workflow nie istnieje w Jirze")
            continue
        top = {s["statusReference"]: s for s in data.get("statuses", [])}
        sname = lambda ref: (top.get(ref) or {}).get("name", "")

        transitions = [dict(t) for t in wf.get("transitions", [])]
        changed = 0
        for rule in rules:
            aliases = list(dict.fromkeys(list(rule["groups"]) + list(always)))
            missing = [x for x in aliases if x not in groups]
            if missing:
                errors.append(f"{wf_name}/{rule['transition']}: nieznane grupy {missing}")
                print(f"  [BŁĄD] '{rule['transition']}': grupy {missing} nie są dostępne — pomijam")
                continue
            hits = [t for t in transitions if t.get("type") != "INITIAL"
                    and (t.get("name") or "").strip().lower() == rule["transition"].strip().lower()
                    and sname(t.get("toStatusReference")).strip().lower() == rule["to"].strip().lower()]
            if not hits:
                if rule.get("optional"):
                    continue
                errors.append(f"{wf_name}: brak przejścia '{rule['transition']}' -> '{rule['to']}'")
                print(f"  [BŁĄD] Nie znaleziono przejścia '{rule['transition']}' -> '{rule['to']}'")
                continue
            for t in hits:
                t["conditions"] = with_group_condition(t.get("conditions"), [groups[x][0] for x in aliases])
                changed += 1
                via = f"  (jak w {rule['via']})" if rule.get("via") else ""
                print(f"  {t['name']:28} -> {sname(t['toStatusReference']):30} : "
                      f"{', '.join(groups[x][1] for x in aliases)}{via}")

        if not changed:
            print("  (brak pasujących przejść)")
            continue
        if a.dry_run and any(str(groups[x][0]).startswith("<nowa:") for x in groups):
            print("  [DRY-RUN] Grupy jeszcze nie istnieją — walidacja Jiry możliwa dopiero po ich utworzeniu.")
            continue

        payload = {
            "statuses": [{k: top[ref][k] for k in KEEP_STATUS if k in top[ref]}
                         for ref in (s["statusReference"] for s in wf.get("statuses", [])) if ref in top],
            "workflows": [{
                "id": wf["id"], "version": wf["version"], "description": wf.get("description", ""),
                "statuses": [{"statusReference": s["statusReference"], "layout": s.get("layout"),
                              "properties": s.get("properties") or {}} for s in wf.get("statuses", [])],
                "transitions": transitions,
                **({"startPointLayout": wf["startPointLayout"]} if wf.get("startPointLayout") else {}),
                **({"loopedTransitionContainerLayout": wf["loopedTransitionContainerLayout"]}
                   if wf.get("loopedTransitionContainerLayout") else {}),
            }],
        }
        if a.dry_run:
            v = call("POST", f"{API}/workflows/update/validation",
                     json={"payload": payload, "validationOptions": {"levels": ["ERROR", "WARNING"]}})
            if v.status_code >= 300:
                errors.append(f"{wf_name}: walidacja HTTP {v.status_code}")
                print(f"  [BŁĄD] Walidacja HTTP {v.status_code}: {v.text[:800]}")
                continue
            probs = v.json().get("errors") or []
            for pr in probs:
                print(f"  [{pr.get('level')}] {pr.get('message')}")
            print("  [DRY-RUN] Walidacja Jiry OK" if not probs else f"  [DRY-RUN] Uwag: {len(probs)}")
            continue
        u = call("POST", f"{API}/workflows/update", json=payload)
        if u.status_code >= 300:
            errors.append(f"{wf_name}: zapis HTTP {u.status_code}")
            print(f"  [BŁĄD] Zapis HTTP {u.status_code}: {u.text[:800]}")
        else:
            print(f"  [ZAPISANO] {changed} przejść z warunkiem grupowym")

    print("\n" + "=" * 60)
    if errors:
        print(f"BŁĘDY ({len(errors)}):")
        for e in errors:
            print(f"  - {e}")
    else:
        print("ZAKOŃCZONO BEZ BŁĘDÓW")


if __name__ == "__main__":
    main()
