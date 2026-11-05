#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
resolve_deep_chains.py

Naprawia WYMAGA_RECZNEJ_DECYZJI_GLEBOKA (patrz revert_false_epics.py) -
zadania, które w Asanie są 4+ poziomy zagnieżdżone, więc nie mieszczą się
w 3-poziomowej hierarchii Jiry (Epic -> Task -> Sub-task) nawet po epic
treatmencie.

STRATEGIA: SPŁASZCZENIE. Diagnostyka (diagnose_deep_chains.py) pokazała,
że 12 z 13 correct_parent ma już WIELE innych, poprawnie ustrukturyzowanych
dzieci (prawdziwe checklisty) - awans na Epika zepsułby je wszystkie bez
korzyści. Zamiast tego: kandydat GLEBOKA i WSZYSTKIE jego dzieci stają się
bezpośrednimi Sub-taskami stabilnego correct_parent (tracąc jeden poziom
grupowania z Asany, ale zachowując poprawną strukturę w Jirze).

ŁAŃCUCHY: czasem correct_parent JEST SAM kandydatem GLEBOKA (np.
MNRPAI-97 - cel dla MNRPAI-92 - jest jednocześnie kandydatem ze swoim
własnym correct_parent=MNRPAI-106). Ten skrypt ZAWSZE na żywo:
  1) sprawdza, czy correct_parent jest sam kandydatem GLEBOKA - jeśli tak,
     podąża dalej jego łańcuchem, aż trafi na stabilny węzeł (root),
  2) dociąga ŻYWE dzieci KAŻDEGO węzła w łańcuchu (nie tylko
     oryginalnego kandydata) - to automatycznie łapie przypadki takie jak
     MNRPAI-98 (już wcześniej naprawione jako dziecko MNRPAI-97 - też
     musi trafić do MNRPAI-106, inaczej zostanie osierocone).
Efekt: WSZYSTKIE węzły łańcucha (kandydaci pośredni + ich dzieci) stają
się bezpośrednimi Sub-taskami jednego, stabilnego roota.

Użycie:
    python resolve_deep_chains.py --csv epic_candidates_audit.csv
        (raport - pokazuje rooty i co pod nie trafi, nic nie zmienia)

    python resolve_deep_chains.py --csv epic_candidates_audit.csv --apply --only-roots CMANBE-112
        (test na jednym, prostym rootcie bez łańcucha)

    python resolve_deep_chains.py --csv epic_candidates_audit.csv --apply
        (wszystko, po udanym teście)

    Dodaj --dry-run do --apply, żeby zobaczyć operacje bez zapisu.
"""
import argparse
import csv
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import revert_false_epics as rfe
import convert_to_epic as cte


def resolve_final_target(candidate: str, correct_parent: str, candidate_to_parent: dict, max_hops: int = 10) -> str:
    """Podąża łańcuchem correct_parent, dopóki trafiony węzeł SAM jest
    kandydatem GLEBOKA (czyli sam potrzebuje przekierowania) - zwraca
    pierwszy napotkany węzeł, który NIE jest kandydatem (stabilny root)."""
    target = correct_parent
    seen = {candidate}
    hops = 0
    while target in candidate_to_parent and target not in seen:
        seen.add(target)
        target = candidate_to_parent[target]
        hops += 1
        if hops > max_hops:
            raise RuntimeError(f"Zbyt długi łańcuch (>{max_hops} przeskoków) zaczynając od {candidate} - "
                               f"możliwa pętla w danych, sprawdź ręcznie.")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", required=True, help="epic_candidates_audit.csv (oryginalny plik audytu)")
    parser.add_argument("--apply", action="store_true", help="Wykonaj naprawę. Bez tej flagi - tylko raport.")
    parser.add_argument("--dry-run", action="store_true", help="Z --apply: pokaż operacje bez zapisu.")
    parser.add_argument("--only-roots", default=None,
                        help="Ogranicz --apply TYLKO do tych finalnych rootów (po przecinku).")
    args = parser.parse_args()

    with open(args.csv, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("wymaga_naprawy") == "TAK"]

    print(f"Sprawdzam na żywo {len(rows)} wierszy z {args.csv}, szukam WYMAGA_RECZNEJ_DECYZJI_GLEBOKA...")
    gleboka = {}  # candidate -> correct_parent (obie strony ŻYWE, nie z CSV)
    for row in rows:
        r = rfe.classify_row(row)
        if r["status"] == "WYMAGA_RECZNEJ_DECYZJI_GLEBOKA":
            gleboka[r["candidate"]] = r["correct_parent"]

    print(f"Znaleziono {len(gleboka)} kandydatów GLEBOKA (stan na TERAZ, nie z CSV).")
    if not gleboka:
        print("Nic do zrobienia.")
        return

    final_target = {cand: resolve_final_target(cand, parent, gleboka) for cand, parent in gleboka.items()}

    roots = {}
    for cand, root in final_target.items():
        roots.setdefault(root, set()).add(cand)

    print("\nDociągam ŻYWE dzieci każdego węzła łańcucha (może chwilę potrwać)...")
    for root, nodes in roots.items():
        extra = set()
        for node in list(nodes):
            for child_key, child_type, child_summary in rfe.get_children(node):
                extra.add(child_key)
        nodes.update(extra)

    if args.only_roots:
        wanted_roots = set(k.strip() for k in args.only_roots.split(","))
        roots = {r: n for r, n in roots.items() if r in wanted_roots}
        print(f"\n[--only-roots] Ograniczono do: {', '.join(sorted(roots.keys()))}")

    print(f"\n{len(roots)} finalnych, stabilnych rootów:")
    total_nodes = 0
    for root, nodes in sorted(roots.items()):
        chain_note = " (ŁAŃCUCH - kilka poziomów scalonych)" if len(nodes & set(gleboka.keys())) > 1 else ""
        print(f"\n  root: {root}{chain_note}")
        print(f"    {len(nodes)} zadań trafi pod niego bezpośrednio jako Sub-task:")
        for n in sorted(nodes):
            tag = " [kandydat GLEBOKA]" if n in gleboka else ""
            print(f"      {n}{tag}")
        total_nodes += len(nodes)
    print(f"\nŁĄCZNIE: {total_nodes} zadań zostanie przepiętych.")

    if not args.apply:
        print("\n[TRYB RAPORTU] Nic nie zmieniono. Uruchom z --apply (opcjonalnie --only-roots), żeby wykonać.")
        return

    print("\n" + "=" * 60)
    print("WYKONANIE" + (" [DRY-RUN]" if args.dry_run else ""))
    print("=" * 60)
    fixed, failed = 0, []
    for root, nodes in sorted(roots.items()):
        print(f"\n--- root: {root} ---")
        for node in sorted(nodes):
            try:
                cte.bulk_convert_subtask_and_reparent(node, root, args.dry_run)
                fixed += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  [BŁĄD] {node}: {exc}")
                failed.append((node, str(exc)))

    print(f"\nNaprawiono: {fixed}. Błędy: {len(failed)}.")
    for key, err in failed:
        print(f"  {key}: {err}")


if __name__ == "__main__":
    main()
