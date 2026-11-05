#!/usr/bin/env python3
# Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
# Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
"""
resolve_duplicates.py

Znajduje grupy zdublowanych tytułów (jak find_duplicate_tasks.py) we
WSZYSTKICH projektach z arkusza (albo w jednym, jeśli podano --project),
a potem krzyżuje każdą grupę z task_sync_state.json, żeby ustalić, KTÓRY
klucz w grupie jest tym AKTUALNIE ŚLEDZONYM (czyli "prawdziwym", do
zachowania), a które są OSIEROCONE (nie ma ich już w bieżącym stanie -
to zaśmiecone duplikaty po utracie/uszkodzeniu stanu w którymś z
wcześniejszych przebiegów).

Klasyfikacja per grupa:
- DOKŁADNIE 1 klucz z grupy jest w state -> BEZPIECZNE: reszta to osierocone
  duplikaty, kandydaci do usunięcia.
- 0 kluczy z grupy w state -> NIEJEDNOZNACZNE: nie wiadomo, który "prawdziwy".
  Pomijane, wymaga ręcznej decyzji.
- 2+ kluczy z grupy w state -> NIEJEDNOZNACZNE: może to być DWA różne,
  prawdziwe zadania o tym samym tytule (mają OSOBNE wpisy w state pod
  różnymi gid Asany). Pomijane, wymaga ręcznej decyzji - NIE dotykamy.

DOMYŚLNIE tryb dry-run (tylko raport, dla WSZYSTKICH projektów naraz).
Usuwanie wymaga --delete ORAZ JEDNEGO, zbiorczego potwierdzenia na
końcu (nie osobno dla każdego z ~510 projektów).

Użycie:
    python resolve_duplicates.py                          # wszystkie projekty, raport
    python resolve_duplicates.py --shard 1/5               # tylko 1/5 projektów, raport (do ręcznego --parallel)
    python resolve_duplicates.py --delete                  # wszystkie projekty, usuwa (z JEDNYM potwierdzeniem)
    python resolve_duplicates.py --project AWGUSL          # tylko jeden projekt, raport
    python resolve_duplicates.py --project AWGUSL --delete # tylko jeden projekt, usuwa

Równoległość (--shard, jak w reszcie skryptów tego projektu):
    Skanowanie (najwolniejsza część) można przyspieszyć przez kilka
    równoległych terminali, np.:
        python resolve_duplicates.py --shard 1/5 &
        python resolve_duplicates.py --shard 2/5 &
        ... (aż do 5/5)
    KAŻDY shard zapisuje SWOJE wyniki do osobnego pliku
    (duplicate_scan_shard_K-N.json). Po zakończeniu WSZYSTKICH shardów,
    zbierz je i usuń JEDNYM, końcowym krokiem:
        python resolve_duplicates.py --collect --delete
    Usuwanie NIE wspiera --shard - to świadomie JEDEN, końcowy krok
    z JEDNYM potwierdzeniem, żeby nie rozpraszać ryzykownej operacji
    na wiele niezależnych procesów czekających osobno na potwierdzenie.
"""
import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core
from jira_sync import apply_shard

STATE_FILE = Path(__file__).resolve().parent / "task_sync_state.json"
SHARD_RESULTS_GLOB = "duplicate_scan_shard_*.json"


COMMENT_PREFIX_RE = re.compile(r"^\[Asana.*?\]$")


def _extract_comment_signature(comment_body: dict) -> str:
    """Wyciąga tekst prefiksu '[Asana — autor, data]' z ADF komentarza -
    to pierwszy akapit, pierwszy fragment tekstu (patrz post_jira_comment
    w asana_jira_sync.py). Zwraca pusty string, jeśli nie rozpoznano
    formatu (np. komentarz dodany ręcznie przez kogoś w Jirze) - taki
    komentarz jest pomijany przy budowaniu sygnatury zadania."""
    try:
        first_paragraph = comment_body["content"][0]
        text = first_paragraph["content"][0]["text"]
        return text if COMMENT_PREFIX_RE.match(text) else ""
    except (KeyError, IndexError, TypeError):
        return ""


def get_all_summaries(project_key):
    """Zwraca listę (summary, issue_key, description, attachment_signature,
    comment_signature) dla WSZYSTKICH zadań w projekcie. Opis, załączniki
    I KOMENTARZE pobierane DODATKOWO obok tytułu - używane jako niezależne
    sygnały bezpieczeństwa przy klasyfikacji duplikatów:
    - opis: dwa różne, prawdziwe zadania o tym samym tytule niemal na
      pewno mają RÓŻNY opis (złapane ręcznie na przykładzie AWGUSL-2/3).
    - załączniki: te same nazwy plików w tej samej liczbie to mocny sygnał
      tej samej, wielokrotnie odtworzonej kopii.
    - komentarze: ten sam ZESTAW sygnatur '[Asana — autor, data]' to
      RÓWNIE mocny sygnał tej samej kopii - dwa różne, prawdziwe zadania
      praktycznie na pewno nie miałyby identycznego zestawu komentarzy
      od tych samych autorów o tych samych znacznikach czasu."""
    results = []
    next_page_token = None
    while True:
        body = {"jql": f"project = {project_key}", "maxResults": 100, "fields": ["summary", "description", "attachment", "comment"]}
        if next_page_token:
            body["nextPageToken"] = next_page_token
        resp = requests.post(
            f"{core.JIRA_BASE_URL}/rest/api/3/search/jql",
            auth=core.jira_auth(),
            json=body,
        )
        if resp.status_code != 200:
            print(f"   [OSTRZEŻENIE] Błąd stronicowania dla {project_key}: {resp.status_code} {resp.text[:150]}")
            break
        data = resp.json()
        for issue in data.get("issues", []):
            fields = issue.get("fields", {})
            attachments = fields.get("attachment") or []
            attachment_sig = tuple(sorted(a.get("filename", "") for a in attachments))
            comments = fields.get("comment", {}).get("comments", []) or []
            comment_sigs = tuple(sorted(
                sig for sig in (_extract_comment_signature(c.get("body", {})) for c in comments) if sig
            ))
            results.append((fields.get("summary", ""), issue["key"], fields.get("description"), attachment_sig, comment_sigs))
        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            break
    return results


def load_all_tracked_keys():
    """Zbiór WSZYSTKICH kluczy Jiry aktualnie śledzonych w task_sync_state.json,
    per projekt: {project_key: {jira_key1, jira_key2, ...}}."""
    if not STATE_FILE.exists():
        return {}
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {
        project_key: {entry["jira_key"] for entry in project_state.values() if entry.get("jira_key")}
        for project_key, project_state in state.items()
    }


def _issue_number(key):
    """Wyciąga numer zadania z klucza (np. 'FMPGEU-537' -> 537) - wyższy
    numer = utworzone później = najprawdopodobniej najbardziej aktualna
    kopia, gdy porównujemy WYŁĄCZNIE po treści (brak w stanie)."""
    try:
        return int(key.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return -1


def classify_project(project_key, tracked_keys):
    """Zwraca (safe_to_delete, needs_review, confirmed_distinct) dla jednego projektu.
    safe_to_delete: [(summary, keep_key, [orphan_keys], method), ...]
    needs_review: [(summary, keys, reason), ...] - NAPRAWDĘ wymaga ręcznej decyzji
    confirmed_distinct: [(summary, keys), ...] - POTWIERDZONE różne, prawdziwe
    zadania (KAŻDY klucz w tej liście ma WŁASNY, niezależny wpis w stanie =
    własny gid Asany = z definicji różne zadanie) - CZYSTO INFORMACYJNE.

    WAŻNE: grupa może być MIESZANA - część kluczy śledzona (różne, prawdziwe
    zadania), część NIE (mogą to być osierocone duplikaty JEDNEGO z nich).
    Traktowanie takiej grupy en bloc jako "potwierdzone różne" byłoby
    błędem - złapane ręcznie na przykładzie 'Weekly Statusbericht' w
    BRESBBRG: 38 kopii, tylko 3 śledzone, reszta (35) to prawdziwe
    duplikaty, całkowicie pomijane przez starą, uproszczoną logikę.

    Dla KAŻDEGO nieśledzonego klucza w grupie sprawdzamy osobno:
    - pasuje treściowo (opis+załączniki+komentarze) do DOKŁADNIE JEDNEGO
      śledzonego klucza w tej samej grupie -> to jego osierocony duplikat,
      bezpieczny do usunięcia.
    - nie pasuje do żadnego (albo pasuje niejednoznacznie do wielu) ->
      wymaga ręcznego przejrzenia.
    Jeśli w grupie w ogóle nie ma śledzonych kluczy, używamy starej
    ścieżki: identyczna treść między wszystkimi -> zachowaj najwyższy
    numer, usuń resztę."""
    by_key = get_all_summaries(project_key)
    by_summary = defaultdict(list)
    for summary, key, description, attachment_sig, comment_sig in by_key:
        by_summary[summary].append((key, description, attachment_sig, comment_sig))
    dupe_groups = {s: entries for s, entries in by_summary.items() if len(entries) > 1}

    safe_to_delete, needs_review, confirmed_distinct = [], [], []
    for summary, entries in dupe_groups.items():
        keys = [k for k, _, _, _ in entries]
        desc_by_key = {k: d for k, d, _, _ in entries}
        attach_by_key = {k: a for k, _, a, _ in entries}
        comment_by_key = {k: c for k, _, _, c in entries}

        def _content_matches(k1, k2):
            return (
                desc_by_key.get(k1) == desc_by_key.get(k2)
                and attach_by_key.get(k1) == attach_by_key.get(k2)
                and comment_by_key.get(k1) == comment_by_key.get(k2)
            )

        tracked_in_group = [k for k in keys if k in tracked_keys]
        untracked_in_group = [k for k in keys if k not in tracked_keys]

        if not untracked_in_group:
            # Wszystkie śledzone, każdy osobno -> potwierdzone różne, koniec sprawy.
            confirmed_distinct.append((summary, keys))
            continue

        if not tracked_in_group:
            # Zero śledzonych - stara ścieżka: identyczna treść między
            # WSZYSTKIMI -> zachowaj najwyższy numer, usuń resztę.
            all_same_content = all(_content_matches(k, keys[0]) for k in keys[1:])
            if all_same_content:
                keep_key = max(keys, key=_issue_number)
                orphans = [k for k in keys if k != keep_key]
                safe_to_delete.append((summary, keep_key, orphans, "treść"))
            else:
                needs_review.append((summary, keys, "ŻADEN klucz nie jest w stanie, treść/załączniki też się różnią - wymaga ręcznego przejrzenia"))
            continue

        # Grupa MIESZANA: są i śledzone, i nieśledzone. Każdy nieśledzony
        # sprawdzamy OSOBNO względem KAŻDEGO śledzonego w tej samej grupie.
        orphans_by_owner = defaultdict(list)
        unclear = []
        for uk in untracked_in_group:
            matching_tracked = [tk for tk in tracked_in_group if _content_matches(uk, tk)]
            if len(matching_tracked) == 1:
                orphans_by_owner[matching_tracked[0]].append(uk)
            elif len(matching_tracked) > 1:
                # Pasuje do KILKU śledzonych naraz - sprawdź, czy te
                # kilka też jest identycznych MIĘDZY SOBĄ (np. puste,
                # szablonowe zadania cykliczne bez unikalnej treści).
                # Jeśli tak, nie ma znaczenia, KTÓREMU przypiszemy
                # sierotę - to nie jest prawdziwa niejasność, tylko
                # efekt identycznej, pustej treści u wielu śledzonych.
                first = matching_tracked[0]
                if all(_content_matches(first, other) for other in matching_tracked[1:]):
                    owner = max(matching_tracked, key=_issue_number)
                    orphans_by_owner[owner].append(uk)
                else:
                    unclear.append(uk)
            else:
                unclear.append(uk)

        for owner_key, orphan_list in orphans_by_owner.items():
            safe_to_delete.append((summary, owner_key, orphan_list, "stan"))

        if unclear:
            needs_review.append((summary, unclear + tracked_in_group,
                                  f"nieśledzone {', '.join(unclear)} nie pasują treścią do żadnego z "
                                  f"{len(tracked_in_group)} śledzonych w tej grupie - wymaga ręcznego przejrzenia"))

        # Same śledzone (niezależnie od orphans_by_owner) to potwierdzone
        # różne zadania - informacyjnie.
        confirmed_distinct.append((summary, tracked_in_group))

    return safe_to_delete, needs_review, confirmed_distinct


def save_shard_results(shard_label, safe, needs_review, confirmed_distinct):
    """Zapisuje wyniki JEDNEGO sharda do osobnego pliku - żeby --collect
    mogło je później zebrać ze wszystkich shardów naraz."""
    path = Path(__file__).resolve().parent / f"duplicate_scan_shard_{shard_label}.json"
    data = {
        "safe": [{"project": p, "summary": s, "keep": k, "orphans": o, "method": m} for p, s, k, o, m in safe],
        "needs_review": [{"project": p, "summary": s, "keys": k, "reason": r} for p, s, k, r in needs_review],
        "confirmed_distinct": [{"project": p, "summary": s, "keys": k} for p, s, k in confirmed_distinct],
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def collect_shard_results():
    """Zbiera wyniki ze WSZYSTKICH plików duplicate_scan_shard_*.json
    w folderze skryptu w jedno, zbiorcze (all_safe, all_needs_review,
    all_confirmed_distinct)."""
    base_dir = Path(__file__).resolve().parent
    shard_files = sorted(base_dir.glob(SHARD_RESULTS_GLOB))
    if not shard_files:
        print("[BŁĄD] Nie znaleziono żadnych plików duplicate_scan_shard_*.json - uruchom najpierw skanowanie z --shard.")
        return None, None, None

    print(f"Zbieram wyniki z {len(shard_files)} plików shardów: {', '.join(f.name for f in shard_files)}")
    all_safe, all_needs_review, all_confirmed_distinct = [], [], []
    for f in shard_files:
        data = json.loads(f.read_text(encoding="utf-8"))
        for entry in data.get("safe", []):
            all_safe.append((entry["project"], entry["summary"], entry["keep"], entry["orphans"], entry.get("method", "stan")))
        for entry in data.get("needs_review", []):
            all_needs_review.append((entry["project"], entry["summary"], entry["keys"], entry["reason"]))
        for entry in data.get("confirmed_distinct", []):
            all_confirmed_distinct.append((entry["project"], entry["summary"], entry["keys"]))
    return all_safe, all_needs_review, all_confirmed_distinct


def clear_shard_result_files():
    base_dir = Path(__file__).resolve().parent
    for f in base_dir.glob(SHARD_RESULTS_GLOB):
        f.unlink()


def bulk_delete_issues(issue_keys, poll_interval=3, max_wait_seconds=3600):
    """Usuwa zadania WSADOWO przez oficjalny Bulk Delete API Jiry
    (POST /bulk/issues/delete, do 1000 na jedno zapytanie) - zamiast
    jednego DELETE na zadanie. Odpytuje GET /bulk/queue/{taskId} aż do
    ukończenia, jeśli operacja nie zakończy się natychmiast.

    max_wait_seconds=3600 (1h) - to zadanie po stronie Jiry usuwa zadania
    JEDNOWĄTKOWO (świadome ograniczenie Atlassiana), więc dla większych
    partii realnie może trwać dłużej niż kilka minut. Zbyt krótki limit
    powodował MYLĄCY raport "Usunięto: 0", mimo że zadanie wciąż
    poprawnie działało w tle na serwerze Jiry.

    Zwraca (usunięte, niedostępne_lub_błędne, wciąż_działa) - to ostatnie
    to lista (taskId, procent) dla partii, które NIE zdążyły się skończyć
    w limicie czasu - NIE oznacza to błędu, tylko że przestaliśmy patrzeć
    za wcześnie."""
    total_deleted = 0
    total_invalid = 0
    still_running = []  # [(task_id, batch_range, last_percent), ...] - przekroczono limit czasu, NIE błąd
    for i in range(0, len(issue_keys), 1000):
        batch = issue_keys[i:i + 1000]
        resp = requests.post(
            f"{core.JIRA_BASE_URL}/rest/api/3/bulk/issues/delete",
            auth=core.jira_auth(),
            json={"selectedIssueIdsOrKeys": batch, "sendBulkNotification": False},
        )
        if resp.status_code not in (200, 201, 202):
            print(f"  [BŁĄD] Nie udało się rozpocząć wsadowego usuwania partii {i}-{i+len(batch)}: {resp.status_code} {resp.text[:200]}")
            continue

        data = resp.json()
        task_id = data.get("taskId")
        status = data.get("status")
        waited = 0
        while status not in ("COMPLETE", "FAILED", "CANCELLED") and waited < max_wait_seconds:
            time.sleep(poll_interval)
            waited += poll_interval
            poll_resp = requests.get(f"{core.JIRA_BASE_URL}/rest/api/3/bulk/queue/{task_id}", auth=core.jira_auth())
            if poll_resp.status_code != 200:
                print(f"  [OSTRZEŻENIE] Błąd sprawdzania postępu (taskId={task_id}): {poll_resp.status_code}")
                break
            data = poll_resp.json()
            status = data.get("status")
            print(f"    ...partia {i}-{i+len(batch)}: {data.get('progressPercent', 0)}% ({status})")

        if status not in ("COMPLETE", "FAILED", "CANCELLED"):
            # Przekroczono limit czasu, ale zadanie WCIĄŻ działa na serwerze
            # Jiry - to NIE jest błąd, tylko "przestaliśmy patrzeć za wcześnie".
            still_running.append((task_id, f"{i}-{i+len(batch)}", data.get("progressPercent", 0)))
            print(f"  [WCIĄŻ DZIAŁA] Partia {i}-{i+len(batch)} (taskId={task_id}) nie zdążyła się skończyć w limicie {max_wait_seconds}s - "
                  f"kontynuuje w tle na serwerze Jiry, sprawdź później przez: GET /rest/api/3/bulk/queue/{task_id}")
            continue

        processed = len(data.get("processedAccessibleIssues", []))
        invalid = data.get("invalidOrInaccessibleIssueCount", 0)
        total_deleted += processed
        total_invalid += invalid
        print(f"  Partia {i}-{i+len(batch)}: usunięto {processed}, niedostępnych/błędnych {invalid} (status końcowy: {status})")

    return total_deleted, total_invalid, still_running


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", help="Sprawdź tylko ten jeden klucz projektu, zamiast wszystkich z arkusza.")
    parser.add_argument("--shard", type=str, default=None, help="Format 'K/N' - przetwórz tylko tę część projektów (do ręcznego uruchomienia w kilku terminalach naraz). Wyniki zapisywane do pliku, nie usuwają nic same z siebie.")
    parser.add_argument("--collect", action="store_true", help="Zbierz wyniki ze WSZYSTKICH zapisanych plików shardów (duplicate_scan_shard_*.json) w jedno, zbiorcze podsumowanie.")
    parser.add_argument("--delete", action="store_true", help="Faktycznie usuń osierocone duplikaty (domyślnie tylko raport). Nie łączy się z --shard - użyj po --collect.")
    args = parser.parse_args()

    if args.shard and args.delete:
        print("[BŁĄD] --delete nie łączy się z --shard - to świadome ograniczenie bezpieczeństwa.")
        print("Uruchom wszystkie shardy BEZ --delete, potem: python resolve_duplicates.py --collect --delete")
        sys.exit(1)

    if args.shard and args.collect:
        print("[BŁĄD] --collect nie łączy się z --shard w jednym poleceniu.")
        print("--collect IGNORUJE --shard i próbuje zebrać wyniki NATYCHMIAST, zanim ten shard")
        print("w ogóle zdąży się przeskanować - to da niepełne, przedwczesne zebranie.")
        print("Uruchom wszystkie shardy OSOBNO (bez --collect), poczekaj aż WSZYSTKIE się skończą,")
        print("dopiero potem osobno: python resolve_duplicates.py --collect")
        sys.exit(1)

    if args.collect:
        all_safe, all_needs_review, all_confirmed_distinct = collect_shard_results()
        if all_safe is None:
            return
    else:
        if args.project:
            project_keys = [args.project]
        else:
            print("Wczytuję listę projektów z arkusza...")
            sheet_rows = core.get_sheet_rows()
            project_keys = sorted(set(row.jira_key for row in sheet_rows if row.jira_key))
            print(f"Znaleziono {len(project_keys)} unikalnych kluczy projektów Jiry.")

        shard_label = None
        if args.shard:
            before = len(project_keys)
            project_keys = apply_shard(project_keys, args.shard)
            shard_label = args.shard.replace("/", "-")
            print(f"--shard {args.shard}: ten proces obsłuży {len(project_keys)} z {before} projektów.")

        all_tracked = load_all_tracked_keys()
        print(f"\nSprawdzam duplikaty w {len(project_keys)} projekt(ach) (to może potrwać kilka-kilkanaście minut)...\n")

        all_safe, all_needs_review, all_confirmed_distinct = [], [], []
        for i, project_key in enumerate(project_keys, 1):
            tracked = all_tracked.get(project_key, set())
            safe, needs_review, confirmed_distinct = classify_project(project_key, tracked)
            if safe or needs_review:
                n_orphans = sum(len(o) for _, _, o, _ in safe)
                print(f"[{i}/{len(project_keys)}] {project_key}: {len(safe)} bezpiecznych grup ({n_orphans} osieroconych), {len(needs_review)} DO PRZEGLĄDU, {len(confirmed_distinct)} potwierdzonych różnych (bez akcji)")
            for summary, keep_key, orphans, method in safe:
                all_safe.append((project_key, summary, keep_key, orphans, method))
            for summary, keys, reason in needs_review:
                all_needs_review.append((project_key, summary, keys, reason))
            for summary, keys in confirmed_distinct:
                all_confirmed_distinct.append((project_key, summary, keys))

        if shard_label:
            save_shard_results(shard_label, all_safe, all_needs_review, all_confirmed_distinct)
            print(f"\nWyniki tego sharda zapisane do duplicate_scan_shard_{shard_label}.json.")
            print("Gdy WSZYSTKIE shardy się skończą: python resolve_duplicates.py --collect")
            return  # shard sam z siebie nie pokazuje pełnego podsumowania ani nie usuwa

    total_orphans = sum(len(orphans) for _, _, _, orphans, _ in all_safe)
    n_by_state = sum(1 for *_, m in all_safe if m == "stan")
    n_by_content = sum(1 for *_, m in all_safe if m == "treść")

    print("\n" + "=" * 60)
    print("ZBIORCZE PODSUMOWANIE (wszystkie sprawdzone projekty)")
    print(f"  BEZPIECZNYCH grup (do usunięcia): {len(all_safe)}  ->  {total_orphans} osieroconych zadań łącznie")
    print(f"    z tego wg śledzenia w stanie: {n_by_state}, wg dopasowania treści/załączników: {n_by_content}")
    print(f"  DO PRZEGLĄDU (treść się różni, naprawdę niejasne): {len(all_needs_review)}")
    print(f"  POTWIERDZONYCH RÓŻNYCH (osobne, prawdziwe zadania - BEZ AKCJI): {len(all_confirmed_distinct)}")
    print("=" * 60)

    if all_safe:
        print(f"\nPrzykłady (pierwsze 15 z {len(all_safe)}) BEZPIECZNYCH do usunięcia - PEŁNA lista w pliku raportu:")
        for project_key, summary, keep_key, orphans, method in all_safe[:15]:
            print(f"  [{project_key}] ({method}) '{summary}': zachowaj {keep_key}, usuń {', '.join(orphans)}")

    if all_needs_review:
        print(f"\nDO PRZEGLĄDU - WSZYSTKIE {len(all_needs_review)} (to jedyne, które naprawdę wymagają Twojej uwagi):")
        for project_key, summary, keys, reason in all_needs_review:
            print(f"  [{project_key}] '{summary}' -> {', '.join(keys)} ({reason})")

    report_path = Path(__file__).resolve().parent / "duplicate_resolution_report.json"
    report_path.write_text(json.dumps({
        "safe_to_delete": [
            {"project": p, "summary": s, "keep": k, "orphans": o, "method": m} for p, s, k, o, m in all_safe
        ],
        "needs_review": [
            {"project": p, "summary": s, "keys": k, "reason": r} for p, s, k, r in all_needs_review
        ],
        "confirmed_distinct": [
            {"project": p, "summary": s, "keys": k} for p, s, k in all_confirmed_distinct
        ],
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nPełny raport (wszystkie trzy kategorie, bez obcinania) zapisany do: {report_path}")

    if not args.delete:
        print(f"\n[DRY-RUN] Nic nie usunięto. Uruchom z --delete, żeby faktycznie usunąć {total_orphans} osieroconych duplikatów ze WSZYSTKICH sprawdzonych projektów.")
        return

    if not all_safe:
        print("\nBrak bezpiecznych kandydatów do usunięcia.")
        return

    affected_projects = len(set(p for p, *_ in all_safe))
    print(f"\nUWAGA: zaraz TRWALE usuniesz {total_orphans} zadań z Jiry, w {affected_projects} projekt(ach).")
    print("Ta operacja jest NIEODWRACALNA.")
    confirm = input(f"Wpisz dokładnie 'USUŃ {total_orphans}' żeby potwierdzić: ")
    if confirm != f"USUŃ {total_orphans}":
        print("Anulowano - wpisany tekst się nie zgadza.")
        return

    orphan_keys = [orphan_key for _, _, _, orphans, _ in all_safe for orphan_key in orphans]
    print(f"\nUsuwam wsadowo (Bulk Delete API, do 1000 zadań na zapytanie)...")
    deleted, failed, still_running = bulk_delete_issues(orphan_keys)

    print(f"\nGotowe (z tego, co zdążyliśmy obserwować). Usunięto: {deleted}, niedostępne/błędne: {failed}.")
    if still_running:
        still_running_count = total_orphans - deleted - failed
        print(f"\n[WAŻNE] {len(still_running)} partii (~{still_running_count} zadań) WCIĄŻ DZIAŁA na serwerze Jiry -")
        print("przestaliśmy tylko PATRZEĆ, to NIE oznacza błędu ani że coś nie zostało usunięte.")
        print("Zadanie dokończy się samo w tle. Sprawdź stan za jakiś czas:")
        print(f"    python resolve_duplicates.py --project {list(set(p for p, *_ in all_safe))[0] if len(set(p for p, *_ in all_safe)) == 1 else '<klucz projektu>'}")
        print("(uruchom bez --delete - zobaczysz, ile duplikatów faktycznie zostało)")

    if args.collect:
        clear_shard_result_files()
        print("Pliki wyników shardów wyczyszczone.")


if __name__ == "__main__":
    main()
