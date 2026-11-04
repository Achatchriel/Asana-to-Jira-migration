#!/usr/bin/env python3
"""
merge_jira_sources.py

Scala dane z trzech parserów w jeden plik schematu dla build_jira_schema.py:
  - schema_z_pdf.yaml     (parse_jira_pdfs.py)  -> ekrany + pola z listy pól Jiry
  - pola_prez.yaml        (parse_jira_pptx.py)  -> typy pól + dozwolone wartości
  - workflows.yaml        (parse_jira_workflow.py) -> statusy i przejścia

Pola bez ustalonego typu NIE trafiają do custom_fields (nie da się ich
utworzyć), ale ZOSTAJĄ w definicjach ekranów - build_jira_schema.py zgłosi
je wtedy jako brakujące, co daje gotową listę do ręcznego uzupełnienia.

Użycie:
    python merge_jira_sources.py --out jira_schema_klient.yaml
"""
import argparse
import re
from pathlib import Path

import yaml


def norm(s: str) -> str:
    return re.sub(r"[\s.\-–—]+", " ", s.lower()).strip()


def load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pdf", default="schema_z_pdf.yaml")
    p.add_argument("--pptx", default="pola_prez.yaml")
    p.add_argument("--workflows", default="workflows.yaml")
    p.add_argument("--out", default="jira_schema_klient.yaml")
    args = p.parse_args()

    pdf = load(Path(args.pdf)) or {}
    prez = load(Path(args.pptx)) or {}
    wfs = load(Path(args.workflows)) or {}

    # --- pola: typ z prezentacji ma pierwszeństwo (podaje też dozwolone wartości)
    fields = {}
    for f in pdf.get("custom_fields", []):
        fields[norm(f["name"])] = {"name": f["name"], "type": f["type"],
                                   "_jira_id": f.get("_jira_id")}
    for f in prez.get("custom_fields", []):
        if f["type"] is None:
            continue
        key = norm(f["name"])
        entry = fields.get(key, {"name": f["name"]})
        entry["type"] = f["type"]
        allowed = f.get("allowed", "")
        # Dozwolone wartości tylko dla list wyboru i tylko gdy wyglądają jak
        # lista (krótkie, rozdzielone przecinkami) - dłuższe teksty to opisy,
        # które parser prezentacji wciągnął razem z tytułami slajdów.
        if f["type"] in ("select", "multiselect") and allowed and len(allowed) < 60:
            entry["options"] = [x.strip() for x in allowed.split(",") if x.strip()]
        fields[key] = entry

    system_fields = {norm(n) for n in prez.get("pola_systemowe", [])}

    # --- ekrany: zostawiamy wszystkie pola; brakujące zgłosi build_jira_schema
    screens = pdf.get("screens", [])
    used = {}  # znormalizowana -> oryginalna pisownia
    for s in screens:
        for t in s["tabs"]:
            for f in t["fields"]:
                used.setdefault(norm(f), f)
    missing = sorted(orig for n, orig in used.items()
                     if n not in fields and n not in system_fields)

    # --- workflow: konwersja do formatu schematu (pierwszy status = początkowy)
    workflows = []
    for w in wfs.get("workflows", []):
        name = re.sub(r"^\d+_", "", w["name"]).replace("-TXT", "").replace("_", " ")
        st_names = [s["name"] for s in w["statuses"]]
        transitions = []
        for t in w["transitions"]:
            frm = t.get("from") or []
            # "Siebie" to post-funkcja przypisania, nie status docelowy
            if t["to"] in st_names:
                transitions.append({"name": t["name"],
                                    "from": "ANY" if frm == ["ANY"] else frm,
                                    "to": t["to"]})
        workflows.append({"name": name,
                          "statuses": st_names,
                          "transitions": transitions})

    schema = {
        "prefix": "",
        "custom_fields": [
            {k: v for k, v in f.items() if not k.startswith("_")}
            for f in sorted(fields.values(), key=lambda x: x["name"])
        ],
        "screens": screens,
    }
    # Workflow: schemat build_jira_schema.py przyjmuje JEDEN workflow, więc
    # pozostałe wypisujemy obok jako warianty do wyboru/ręcznego rozdzielenia.
    if workflows:
        schema["workflow"] = workflows[0]
        if len(workflows) > 1:
            schema["_pozostale_workflow"] = workflows[1:]
        # Statusy z wszystkich workflow, zdeduplikowane.
        # Kategoria zgadywana z nazwy - Jira wymaga jej przy tworzeniu, a
        # eksporty jej nie podają. Domyślnie TODO, więc każdy status
        # i tak wart sprawdzenia przed uruchomieniem.
        done_hints = ("closed", "done", "deployed", "zamkni", "gotow", "zakoncz",
                      "anulowan", "odrzuc", "rejected", "accepted", "akcept")
        progress_hints = ("progress", "w trakcie", "review", "testing", "analiz",
                          "realizac", "development", "testów", "refinement")
        seen, statuses = set(), []
        for w in workflows:
            for s in w["statuses"]:
                if s in seen:
                    continue
                seen.add(s)
                low = s.lower()
                if any(h in low for h in done_hints):
                    cat = "DONE"
                elif any(h in low for h in progress_hints):
                    cat = "IN_PROGRESS"
                else:
                    cat = "TODO"
                statuses.append({"name": s, "category": cat})
        schema["statuses"] = statuses

    out = Path(args.out)
    out.write_text(
        "# Scalone z: eksportów PDF (ekrany, pola), prezentacji (typy pól),\n"
        "# HTML workflow (statusy, przejścia). Wygenerowane przez merge_jira_sources.py\n"
        "#\n"
        "# DO UZUPEŁNIENIA RĘCZNIE:\n"
        "#  1. 'category' przy statusach — ZGADNIĘTE z nazwy statusu\n"
        "#     (eksporty jej nie podają). Sprawdź każdy: TODO / IN_PROGRESS / DONE.\n"
        "#  2. Pola wymienione na ekranach, ale bez definicji typu (lista niżej).\n"
        "#  3. '_pozostale_workflow' — schemat buduje jeden workflow na raz.\n"
        + ("#\n# POLA BEZ TYPU (ekrany je wymieniają, ale nie zostaną utworzone):\n"
           + "".join(f"#   - {m}\n" for m in missing) if missing else "")
        + "\n"
        + yaml.dump(schema, allow_unicode=True, sort_keys=False, width=110),
        encoding="utf-8")

    print(f"Pól z typem: {len(schema['custom_fields'])}")
    print(f"Ekranów: {len(screens)} | Workflow: {len(workflows)} "
          f"| Statusów: {len(schema.get('statuses', []))}")
    print(f"Pól bez typu (zostają na ekranach, do uzupełnienia): {len(missing)}")
    print(f"\nZapisano: {out}")


if __name__ == "__main__":
    main()
