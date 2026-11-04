#!/usr/bin/env python3
"""
revert_false_epics.py

Naprawia fałszywych kandydatów na Epic znalezionych przez
audit_epic_candidates.py (plik epic_candidates_audit.csv) — zadania, które
zostały błędnie potraktowane jako "szczyt hierarchii" w Asanie (bo podzadanie
z własnymi podzadaniami zostało DODATKOWO dodane do projektu), mimo że w
rzeczywistości mają w Asanie rodzica.

DLA KAŻDEGO WIERSZA Z CSV, ZAWSZE NAJPIERW SPRAWDZA STAN NA ŻYWO W JIRZE
(nie ufa CSV — to migawka sprzed jakiegoś czasu, stan mógł się zmienić):
  - jaki jest OBECNY typ zadania (może już nie być Epikiem, jeśli ktoś
    poprawił ręcznie),
  - czy zadanie ma już poprawnie ustawionego rodzica,
  - jeśli typ to Epic — czy ZDĄŻYŁO dostać własne dzieci w Jirze (wnuki z
    Asany przepięte pod nie przez wcześniejszy retrofit/sync). To jest
    kluczowe ryzyko: jeśli ma dzieci, samo zmienienie typu Epic->Task
    zostawiłoby validation w niepewnym stanie (Task nie może mieć
    Task-typu dzieci, tylko Sub-task) — takie przypadki NIE są naprawiane
    automatycznie, tylko zgłaszane do ręcznej decyzji.

Klasyfikacja każdego wiersza:
  OK_JUZ_POPRAWNY          - typ i rodzic już się zgadzają, nic do zrobienia
  NAPRAWIALNE_SAM_RODZIC   - typ już poprawny (nie Epic), trzeba tylko
                             ustawić/poprawić rodzica
  NAPRAWIALNE_TYP_I_RODZIC - typ to Epic, BRAK własnych dzieci w Jirze —
                             bezpieczne: zmień typ + ustaw rodzica jednym
                             zapytaniem (reużywa sprawdzonej
                             convert_to_epic.convert_subtask_to_task)
  WYMAGA_RECZNEJ_DECYZJI   - typ to Epic i MA już dzieci w Jirze - naprawiane
                             TYLKO z osobną flagą --apply-cascade (patrz niżej)
  CEL_NIE_ISTNIEJE         - rodzic_powinien_byc nie istnieje w Jirze (404)
                             — błąd w danych źródłowych, wymaga sprawdzenia

KASKADA (--apply-cascade) - jak działa i dlaczego w TEJ kolejności:
  Krok 1: zmień typ kandydata Epic -> właściwy typ (np. Task) + ustaw mu
          rodzica = correct_parent. DOKŁADNIE ta sama, już przetestowana
          operacja co dla NAPRAWIALNE_TYP_I_RODZIC (convert_subtask_to_task).
  Krok 2: dla KAŻDEGO dziecka: zmień jego typ na regularny Sub-task +
          ustaw rodzica = kandydat (już będący po kroku 1 zwykłym Task).
          DOKŁADNIE ta sama operacja co w retrofit_epics.py
          (bulk_convert_subtask_and_reparent).
  Kolejność MA znaczenie: gdyby zrobić to odwrotnie (dzieci przed
  rodzicem), dzieci dostałyby jako rodzica coś, co w tym momencie WCIĄŻ
  jest Epikiem — a Sub-task nie może mieć Epika jako rodzica. Robiąc
  rodzica najpierw, krok 2 staje się identyczny strukturalnie z czymś, co
  już wielokrotnie zadziałało w tym projekcie (Sub-task pod zwykłym Task).

  JEDYNA PRAWDZIWA NIEWIADOMA: czy Jira pozwoli na krok 1 (zmianę typu
  kandydata), gdy jego dzieci JESZCZE wskazują na niego jako rodzica ze
  STAREGO typu (Task, nie Sub-task) — przez chwilę między krokiem 1 i 2
  będzie to niespójny stan. Nie da się tego sprawdzić bez dostępu do
  żywej Jiry, dlatego: PRZETESTUJ NAJPIERW NA JEDNYM KANDYDACIE
  (--only-keys z jednym kluczem, najlepiej takim z 1 dzieckiem), sprawdź
  ręcznie w Jirze czy wynik jest poprawny, DOPIERO POTEM odpal na reszcie.

Użycie:
    python revert_false_epics.py --csv epic_candidates_audit.csv
        (TYLKO raport — nic nie zmienia, bezpieczne do wielokrotnego
        uruchamiania)

    python revert_false_epics.py --csv epic_candidates_audit.csv --apply
        (naprawia NAPRAWIALNE_SAM_RODZIC i NAPRAWIALNE_TYP_I_RODZIC —
        bezpieczne, bez dzieci)

    python revert_false_epics.py --csv epic_candidates_audit.csv --apply-cascade --only-keys PROJ11-127
        (TEST na jednym: naprawia WYMAGA_RECZNEJ_DECYZJI dla wskazanego
        klucza. Sprawdź ręcznie wynik w Jirze przed odpaleniem na reszcie.)

    python revert_false_epics.py --csv epic_candidates_audit.csv --apply --apply-cascade
        (wszystko naraz, na wszystkich wierszach — dopiero po udanym teście)

    Dodaj --dry-run do dowolnej kombinacji powyższych, żeby zobaczyć
    dokładną sekwencję operacji BEZ zapisu do Jiry.
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
import convert_to_epic as cte

REPORT_FILE = Path(__file__).resolve().parent / "revert_false_epics_report.csv"


def get_live_issue_state(issue_key: str) -> "dict | None":
    """Zwraca {'type': str, 'parent': str|None} albo None jeśli zadanie
    nie istnieje (404) — np. zostało usunięte."""
    resp = requests.get(
        f"{core.JIRA_BASE_URL}/rest/api/3/issue/{issue_key}",
        auth=core.jira_auth(), params={"fields": "issuetype,parent"},
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    fields = resp.json()["fields"]
    parent = fields.get("parent")
    return {
        "type": fields["issuetype"]["name"],
        "is_subtask": bool(fields["issuetype"].get("subtask", False)),
        "parent": parent["key"] if parent else None,
    }


def get_children(issue_key: str) -> list:
    """Zwraca listę (key, type, summary) dzieci danego zadania — przez
    JQL parent = "KEY", z paginacją przez nextPageToken (nowy endpoint
    /search/jql, /search zostało usunięte przez Atlassiana)."""
    children = []
    next_token = None
    while True:
        params = {
            "jql": f'parent = "{issue_key}"',
            "fields": "summary,issuetype",
            "maxResults": 100,
        }
        if next_token:
            params["nextPageToken"] = next_token
        resp = requests.get(
            f"{core.JIRA_BASE_URL}/rest/api/3/search/jql",
            auth=core.jira_auth(), params=params,
        )
        resp.raise_for_status()
        data = resp.json()
        for issue in data.get("issues", []):
            children.append((
                issue["key"],
                issue["fields"]["issuetype"]["name"],
                issue["fields"]["summary"],
            ))
        next_token = data.get("nextPageToken")
        if not next_token:
            break
    return children


def set_parent_only(issue_key: str, parent_key: str, dry_run: bool) -> None:
    """Ustawia TYLKO pole 'parent', bez zmiany typu zadania — dla wierszy,
    które już mają poprawny typ (nie Epic), trzeba im tylko poprawić
    powiązanie z rodzicem. Celowo BEZ automatycznego fallbacku na Bulk
    Move przy błędzie hierarchii — jeśli sam PUT nie przejdzie, to
    znaczy że jest tu coś nietypowego (np. issue_key ma legacy typ), i
    lepiej to zgłosić do ręcznego sprawdzenia niż zmieniać typ zadania
    bez pytania."""
    if dry_run:
        print(f"  [DRY-RUN] {issue_key}: ustawiłbym rodzica -> {parent_key}")
        return
    resp = requests.put(
        f"{core.JIRA_BASE_URL}/rest/api/3/issue/{issue_key}",
        auth=core.jira_auth(), headers={"Content-Type": "application/json"},
        json={"fields": {"parent": {"key": parent_key}}},
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"Błąd ustawienia rodzica: {resp.status_code} {resp.text[:300]}")
    print(f"  {issue_key}: rodzic ustawiony -> {parent_key}")


def fix_epic_with_children(candidate: str, correct_parent: str, children: list, dry_run: bool) -> None:
    """KASKADA dla WYMAGA_RECZNEJ_DECYZJI - patrz opis na górze pliku.
    Krok 1 (rodzic) MUSI się udać, zanim tkniemy jakiekolwiek dziecko -
    jeśli krok 1 rzuci wyjątek, funkcja przerywa się przed krokiem 2
    (nie zostawiamy dzieci przepiętych pod rodzica, który wciąż jest
    Epikiem). Błąd pojedynczego DZIECKA w kroku 2 NIE przerywa reszty
    dzieci tego samego kandydata - każde jest niezależne."""
    project_key = candidate.split("-")[0]
    target_type = cte.get_non_subtask_issue_type_name(project_key)

    print(f"  [1/2] {candidate}: Epic -> '{target_type}', rodzic -> {correct_parent}")
    cte.convert_subtask_to_task(candidate, target_type, correct_parent, dry_run)

    print(f"  [2/2] Przepinam {len(children)} dzieci pod {candidate} (jako Sub-task)...")
    child_failed = []
    for j, (child_key, child_type, child_summary) in enumerate(children, 1):
        try:
            print(f"        ({j}/{len(children)}) {child_key} ('{child_summary[:60]}', obecny typ: {child_type})")
            cte.bulk_convert_subtask_and_reparent(child_key, candidate, dry_run)
        except Exception as exc:  # noqa: BLE001
            print(f"        [BŁĄD] {child_key}: {exc}")
            child_failed.append((child_key, str(exc)))
    if child_failed:
        raise RuntimeError(f"{candidate}: rodzic naprawiony, ale {len(child_failed)}/{len(children)} "
                           f"dzieci się nie udało: {child_failed}")


def bulk_reparent_as_subtask(issue_key: str, parent_key: str, dry_run: bool) -> None:
    """Cienki wrapper na convert_to_epic.bulk_convert_subtask_and_reparent -
    dla przypadków, gdzie correct_parent NIE jest Epikiem: candidate musi
    stać się REGULARNYM Sub-taskiem (nie Task!) tego zwykłego zadania -
    to jest jedyna relacja, jaką Jira dopuszcza między dwoma zadaniami
    poziomu 0 (Task->Task via 'parent' NIE istnieje w Jirze)."""
    cte.bulk_convert_subtask_and_reparent(issue_key, parent_key, dry_run)


def classify_row(row: dict) -> dict:
    """Sprawdza stan NA ŻYWO w Jirze dla jednego wiersza CSV i zwraca
    klasyfikację + wszystkie dane potrzebne do ewentualnej naprawy.

    KLUCZOWE: relacja, jaką candidate MUSI mieć z correct_parent, zależy
    od TYPU correct_parent, nie tylko od obecnego typu candidate:
      - correct_parent to Epic  -> candidate musi być zwykłym Task,
        powiązanym przez pole 'parent' (Task pod Epikiem - standardowa,
        "spłaszczona" relacja Jira Software).
      - correct_parent to NIE Epic (zwykły Task/Story/...) -> candidate
        musi być REGULARNYM Sub-taskiem tego zadania - Task->Task przez
        'parent' NIE ISTNIEJE w Jirze jako relacja (stąd błąd "appropriate
        hierarchy" widziany w praktyce dla 30/31 przypadków, gdzie
        correct_parent okazał się zwykłym Task, nie Epikiem)."""
    candidate = row["kandydat_jira"]
    correct_parent = row["rodzic_powinien_byc"]

    parent_state = get_live_issue_state(correct_parent)
    if parent_state is None:
        return {"status": "CEL_NIE_ISTNIEJE", "candidate": candidate,
                "correct_parent": correct_parent, "live_type": None,
                "live_parent": None, "children": []}

    cand_state = get_live_issue_state(candidate)
    if cand_state is None:
        return {"status": "KANDYDAT_NIE_ISTNIEJE", "candidate": candidate,
                "correct_parent": correct_parent, "live_type": None,
                "live_parent": None, "children": []}

    live_type = cand_state["type"]
    live_parent = cand_state["parent"]

    if live_parent == correct_parent:
        # Jira nie pozwoliłaby zapisać tego powiązania, gdyby było
        # strukturalnie nieprawidłowe - skoro jest zapisane, jest OK.
        return {"status": "OK_JUZ_POPRAWNY", "candidate": candidate,
                "correct_parent": correct_parent, "live_type": live_type,
                "live_parent": live_parent, "children": []}

    if parent_state.get("is_subtask"):
        # correct_parent SAM jest Sub-taskiem (np. po spłaszczeniu przez
        # resolve_deep_chains.py) - nie może mieć dzieci. Poprawnym miejscem
        # dla kandydata jest wtedy RODZIC correct_parent (ten sam root, pod
        # który spłaszczono łańcuch). Nigdy nie próbujemy wstawiać pod Sub-task.
        flattened_root = parent_state.get("parent")
        if flattened_root and live_parent == flattened_root:
            return {"status": "OK_SPLASZCZONE", "candidate": candidate,
                    "correct_parent": flattened_root, "live_type": live_type,
                    "live_parent": live_parent, "children": []}
        return {"status": "CEL_JEST_SUBTASKIEM", "candidate": candidate,
                "correct_parent": correct_parent, "live_type": live_type,
                "live_parent": live_parent, "children": []}

    parent_is_epic = parent_state["type"] == "Epic"
    children = get_children(candidate)  # zawsze sprawdzamy - potrzebne w obu gałęziach

    if parent_is_epic:
        # Target: candidate = zwykły Task, dziecko Epika.
        if live_type == "Epic":
            if children:
                return {"status": "WYMAGA_RECZNEJ_DECYZJI", "candidate": candidate,
                        "correct_parent": correct_parent, "live_type": live_type,
                        "live_parent": live_parent, "children": children}
            return {"status": "NAPRAWIALNE_TYP_I_RODZIC", "candidate": candidate,
                    "correct_parent": correct_parent, "live_type": live_type,
                    "live_parent": live_parent, "children": []}
        # live_type już NIE jest Epikiem (Task/Story/...) - candidate MOŻE
        # mieć własne dzieci (Task normalnie miewa Sub-taski) - to nie
        # przeszkadza samemu ustawieniu jego rodzica.
        return {"status": "NAPRAWIALNE_SAM_RODZIC", "candidate": candidate,
                "correct_parent": correct_parent, "live_type": live_type,
                "live_parent": live_parent, "children": children}

    # correct_parent NIE jest Epikiem -> candidate musi zostać Sub-taskiem.
    if children:
        # Sub-task nie może mieć WŁASNYCH dzieci - a candidate je ma.
        # To wymaga głębszej decyzji (np. correct_parent też powinien
        # zostać Epikiem) - nie zgadujemy, zgłaszamy do ręcznego przeglądu.
        return {"status": "WYMAGA_RECZNEJ_DECYZJI_GLEBOKA", "candidate": candidate,
                "correct_parent": correct_parent, "live_type": live_type,
                "live_parent": live_parent, "children": children}
    return {"status": "NAPRAWIALNE_SUBTASK_I_RODZIC", "candidate": candidate,
            "correct_parent": correct_parent, "live_type": live_type,
            "live_parent": live_parent, "children": []}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", required=True, help="Plik CSV z audit_epic_candidates.py")
    parser.add_argument("--apply", action="store_true",
                        help="Napraw NAPRAWIALNE_SAM_RODZIC i NAPRAWIALNE_TYP_I_RODZIC (bezpieczne, bez dzieci).")
    parser.add_argument("--apply-cascade", action="store_true",
                        help="Napraw WYMAGA_RECZNEJ_DECYZJI (Epic z dziećmi) - PRZETESTUJ NAJPIERW na "
                             "jednym kluczu przez --only-keys, patrz opis na górze pliku.")
    parser.add_argument("--only-keys", default=None,
                        help="Ogranicz --apply/--apply-cascade TYLKO do tych kluczy kandydatów (po przecinku). "
                             "Raport i tak obejmuje wszystkie wiersze z CSV.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Pokaż dokładną sekwencję operacji (razem z --apply/--apply-cascade) BEZ zapisu do Jiry.")
    args = parser.parse_args()

    only_keys = set(k.strip() for k in args.only_keys.split(",")) if args.only_keys else None

    with open(args.csv, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("wymaga_naprawy") == "TAK"]

    print(f"Wczytano {len(rows)} wierszy do sprawdzenia z {args.csv}.")
    if not args.apply and not args.apply_cascade:
        print("[TRYB RAPORTU - nic nie zostanie zmienione. Użyj --apply i/lub --apply-cascade, żeby naprawić.]\n")
    elif args.dry_run:
        print("[TRYB DRY-RUN - pokażę dokładne operacje, ale nic nie zapiszę do Jiry.]\n")
    if only_keys:
        print(f"[--only-keys] Naprawa ograniczona do: {', '.join(sorted(only_keys))}\n")

    results = []
    for i, row in enumerate(rows, 1):
        try:
            result = classify_row(row)
        except Exception as exc:  # noqa: BLE001
            result = {"status": "BLAD_SPRAWDZANIA", "candidate": row["kandydat_jira"],
                      "correct_parent": row["rodzic_powinien_byc"], "live_type": None,
                      "live_parent": None, "children": [], "error": str(exc)}
        result["projekt"] = row["projekt"]
        results.append(result)
        print(f"[{i}/{len(rows)}] {result['candidate']}: {result['status']}"
              + (f" (typ={result['live_type']}, rodzic={result['live_parent']})" if result.get("live_type") else "")
              + (f" — {len(result['children'])} dzieci w Jirze!" if result["status"] == "WYMAGA_RECZNEJ_DECYZJI" else ""))

    # --- Podsumowanie ---
    from collections import Counter
    counts = Counter(r["status"] for r in results)
    print("\n" + "=" * 60)
    print("PODSUMOWANIE")
    for status in ["OK_JUZ_POPRAWNY", "OK_SPLASZCZONE", "CEL_JEST_SUBTASKIEM", "NAPRAWIALNE_SAM_RODZIC", "NAPRAWIALNE_TYP_I_RODZIC",
                   "NAPRAWIALNE_SUBTASK_I_RODZIC", "WYMAGA_RECZNEJ_DECYZJI",
                   "WYMAGA_RECZNEJ_DECYZJI_GLEBOKA", "CEL_NIE_ISTNIEJE",
                   "KANDYDAT_NIE_ISTNIEJE", "BLAD_SPRAWDZANIA"]:
        if counts.get(status):
            print(f"  {status}: {counts[status]}")
    print("=" * 60)

    if counts.get("WYMAGA_RECZNEJ_DECYZJI"):
        print(f"\n{counts['WYMAGA_RECZNEJ_DECYZJI']} Epików ma już własne dzieci w Jirze - "
              f"NIE naprawiane automatycznie (chyba że --apply-cascade). Szczegóły (klucz -> dzieci):")
        for r in results:
            if r["status"] == "WYMAGA_RECZNEJ_DECYZJI":
                dzieci_str = ", ".join(f"{k} ({t})" for k, t, _ in r["children"])
                print(f"  {r['candidate']} -> {dzieci_str}")

    if counts.get("WYMAGA_RECZNEJ_DECYZJI_GLEBOKA"):
        print(f"\n{counts['WYMAGA_RECZNEJ_DECYZJI_GLEBOKA']} zadań powinno zostać Sub-taskiem zwykłego "
              f"zadania, ale SAME mają własne dzieci (Sub-task nie może mieć dzieci) - wymaga to "
              f"głębszej decyzji (np. correct_parent też powinien zostać Epikiem). NIGDY nie jest "
              f"naprawiane automatycznie. Szczegóły (klucz -> dzieci):")
        for r in results:
            if r["status"] == "WYMAGA_RECZNEJ_DECYZJI_GLEBOKA":
                dzieci_str = ", ".join(f"{k} ({t})" for k, t, _ in r["children"])
                print(f"  {r['candidate']} (rodzic docelowy: {r['correct_parent']}) -> {dzieci_str}")

    if counts.get("CEL_NIE_ISTNIEJE") or counts.get("KANDYDAT_NIE_ISTNIEJE"):
        print("\nUWAGA - brakujące zadania (sprawdź ręcznie, dane w CSV mogą być nieaktualne):")
        for r in results:
            if r["status"] in ("CEL_NIE_ISTNIEJE", "KANDYDAT_NIE_ISTNIEJE"):
                print(f"  [{r['status']}] projekt={r['projekt']} kandydat={r['candidate']} "
                      f"cel={r['correct_parent']}")

    # --- Zapis pełnego raportu do CSV ---
    with open(REPORT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["projekt", "kandydat", "status", "live_type", "live_parent",
                          "correct_parent", "liczba_dzieci", "dzieci"])
        for r in results:
            writer.writerow([
                r["projekt"], r["candidate"], r["status"], r.get("live_type"),
                r.get("live_parent"), r["correct_parent"], len(r["children"]),
                "; ".join(f"{k}({t})" for k, t, _ in r["children"]),
            ])
    print(f"\nPełny raport zapisany do: {REPORT_FILE}")

    if not args.apply and not args.apply_cascade:
        print("\n[TRYB RAPORTU] Nic nie zostało zmienione. Uruchom z --apply (bezpieczne przypadki) "
              "i/lub --apply-cascade (Epiki z dziećmi - przetestuj najpierw na jednym przez --only-keys).")
        return

    def wanted(candidate: str) -> bool:
        return only_keys is None or candidate in only_keys

    # --- Naprawa: bezpieczne przypadki (--apply) ---
    fixed, failed = 0, []
    if args.apply:
        print("\n" + "=" * 60)
        print("NAPRAWA (--apply): NAPRAWIALNE_SAM_RODZIC + NAPRAWIALNE_TYP_I_RODZIC + NAPRAWIALNE_SUBTASK_I_RODZIC")
        print("=" * 60)
        SAFE_STATUSES = ("NAPRAWIALNE_SAM_RODZIC", "NAPRAWIALNE_TYP_I_RODZIC", "NAPRAWIALNE_SUBTASK_I_RODZIC")
        for r in results:
            candidate = r["candidate"]
            if r["status"] not in SAFE_STATUSES or not wanted(candidate):
                continue
            try:
                if r["status"] == "NAPRAWIALNE_SAM_RODZIC":
                    set_parent_only(candidate, r["correct_parent"], args.dry_run)
                elif r["status"] == "NAPRAWIALNE_TYP_I_RODZIC":
                    project_key = candidate.split("-")[0]
                    target_type = cte.get_non_subtask_issue_type_name(project_key)
                    cte.convert_subtask_to_task(candidate, target_type, r["correct_parent"], args.dry_run)
                else:  # NAPRAWIALNE_SUBTASK_I_RODZIC
                    bulk_reparent_as_subtask(candidate, r["correct_parent"], args.dry_run)
                fixed += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  [BŁĄD] {candidate}: {exc}")
                failed.append((candidate, str(exc)))

    # --- Naprawa: kaskada (--apply-cascade) ---
    cascade_fixed, cascade_failed = 0, []
    if args.apply_cascade:
        cascade_rows = [r for r in results if r["status"] == "WYMAGA_RECZNEJ_DECYZJI" and wanted(r["candidate"])]
        print("\n" + "=" * 60)
        print(f"NAPRAWA (--apply-cascade): {len(cascade_rows)} Epików z dziećmi do przepięcia")
        print("=" * 60)
        for i, r in enumerate(cascade_rows, 1):
            candidate = r["candidate"]
            print(f"\n[{i}/{len(cascade_rows)}] {candidate} ({len(r['children'])} dzieci)")
            try:
                fix_epic_with_children(candidate, r["correct_parent"], r["children"], args.dry_run)
                cascade_fixed += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  [BŁĄD] {candidate}: {exc}")
                cascade_failed.append((candidate, str(exc)))

    print("\n" + "=" * 60)
    print("PODSUMOWANIE NAPRAWY")
    if args.apply:
        print(f"  --apply:         naprawiono {fixed}, błędy {len(failed)}")
    if args.apply_cascade:
        print(f"  --apply-cascade: naprawiono {cascade_fixed}, błędy {len(cascade_failed)}")
    print("=" * 60)
    for key, err in failed + cascade_failed:
        print(f"  [BŁĄD] {key}: {err}")


if __name__ == "__main__":
    main()
