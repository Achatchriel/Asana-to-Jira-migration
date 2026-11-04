#!/usr/bin/env python3
"""
relink_state.py

Odtwarza w task_sync_state.json powiązania Asana <-> Jira dla zgłoszeń, które
istnieją w Jirze, ale zgubiły wpis w stanie (np. przez wyścig zapisów stanu
przy równoległych procesach w starszych wersjach). Bez tego następna
synchronizacja utworzyłaby te zadania drugi raz.

Przebieg 0: LINK. Zgłoszenia z linkiem do zadania Asany (sekcja "Linki",
globalId asana:task:<gid>, zakładany przez asana_jira_sync.py) są wiązane
dokładnie, bez porównywania tytułów. Kategoria LINK, zapisywana zawsze z --apply.

Dopasowanie (projekt po projekcie, od najwyższego poziomu w dół, żeby rodzice
byli już dopasowani, gdy rozstrzygamy dzieci):
  - kandydaci: zgłoszenia Jiry bez powiązania  x  zadania Asany bez wpisu
    w stanie (albo z wpisem wskazującym na nieistniejące zgłoszenie),
  - ten sam tytuł (bez różnic w wielkości liter i białych znakach),
  - przy kilku kandydatach o tym samym tytule rozstrzyga rodzic: zgłoszenie
    musi stać pod zgłoszeniem rodzica z Asany albo pod jego rodzicem w Jirze
    (spłaszczenie).

Drugi przebieg (dla tego, co zostało): TEN SAM RODZIC + PODOBNY TYTUŁ (jeden
zawiera drugi albo podobieństwo >= 0.85). Łapie tytuły zmienione w Asanie po
migracji (synchronizacja aktualizuje tytuły tylko powiązanych zgłoszeń, więc
w Jirze zostały stare, np. "NB" zamiast "GoLive NB"). Takie pary są osobną
kategorią i zapisywane wyłącznie z --include-similar.
Kandydatami są też zadania z wpisem "usunięte w Jirze", jeśli znajdzie się
dla nich istniejące zgłoszenie (np. stan wskazywał na usuniętą kopię).

Przebieg 3: TREŚĆ (dla zgłoszeń, które zostały bez pary). Punktacja z kilku
pól naraz, kandydaci z Asany o podobnym tytule albo identycznym opisie:
  tytuł identyczny +3 / podobny +1.5     opis identyczny +3 / bardzo podobny +1.5
  termin zgodny +1                         przypisany zgodny (user_map.json) +1
  ukończone = zakończone +0.5              rodzic zgodny +2 / nieoceniony 0 / niezgodny -1
Wygrywa kandydat z >= 5 pkt i przewagą >= 1.5 nad drugim. Niezgodny rodzic tylko
odejmuje punkty (łapie samodzielne Taski z błędu kolejności).

Kategorie w raporcie (relink_state_report.csv):
  LINK           - powiązanie odczytane z linku w zgłoszeniu -> --apply
  PEWNE          - jednoznaczne dopasowanie -> --apply odtwarza wpis
  PODOBNE        - ten sam rodzic, podobny tytuł -> --apply --include-similar
  KOLEJNOSC      - kilka zadań o tym samym tytule pod tym samym rodzicem w Jirze
                   (po spłaszczeniu nieodróżnialne, np. "TODOs:" pod każdym GoLive),
                   sparowane według kolejności tworzenia: synchronizacja tworzy
                   zgłoszenia po kolei, idąc po drzewie Asany, więc numery kluczy
                   rosną jak kolejność rodziców w Asanie. Tylko grupy, w których
                   liczba zadań = liczba zgłoszeń -> --apply --include-order
  TRESC          - nowe powiązanie z punktacji treści -> --apply --include-content
  DUPLIKAT       - zadanie z Asany ma JUŻ powiązane zgłoszenie, a to drugie ma
                   ten sam tytuł i rodzica (albo wygrało punktację) -> resolve_relink_duplicates.py
  NADMIAROWA_KOPIA - treść pasuje równie dobrze do kilku zadań, które WSZYSTKIE są
                   już powiązane (np. kolejna kopia "TODOs:" z szablonu) - na pewno
                   zbędna kopia, choć nie wiadomo której -> resolve_relink_duplicates.py
  NIEJEDNOZNACZNE- kilka możliwych par o tym samym tytule -> ręcznie
  BRAK_W_ASANIE  - zgłoszenie bez odpowiednika w Asanie (usunięte w Asanie
                   albo dodane ręcznie w Jirze)
  NIEZMIGROWANE  - zadanie z Asany bez zgłoszenia -> utworzy je synchronizacja

Odtwarzany wpis dostaje: klucz Jiry, flagi hierarchii z Jiry, oraz listę
komentarzy i załączników z Asany, które JUŻ są w tym zgłoszeniu (komentarz po
treści, załącznik po nazwie pliku) - pozostałe synchronizacja dośle, bez
duplikatów. asana_modified_at puste -> pierwsza synchronizacja odświeży treść.

Użycie (tylko odczyt, chyba że --apply):
    python relink_state.py --project-key PROJ3
    python relink_state.py --project-key PROJ3 --apply
"""
import argparse
import csv
import difflib
import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import asana_jira_sync as ajs  # noqa: E402
from compare_project_totals import _asana_list, collect_asana, collect_jira, sheet_projects  # noqa: E402

REPORT_FILE = Path(__file__).resolve().parent / "relink_state_report.csv"


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().casefold()


def adf_text(node) -> str:
    if isinstance(node, dict):
        return (node.get("text") or "") + " ".join(adf_text(c) for c in node.get("content") or [])
    if isinstance(node, list):
        return " ".join(adf_text(c) for c in node)
    return ""


def already_synced(gid: str, jira_key: str) -> "tuple[list, list]":
    """(gid-y komentarzy Asany, gid-y załączników Asany), które już są w zgłoszeniu."""
    jira_comments = []
    start = 0
    while True:
        r = ajs.request_with_retry("GET", f"{ajs.JIRA_BASE_URL}/rest/api/3/issue/{jira_key}/comment",
                                   auth=ajs.JIRA_AUTH, params={"startAt": start, "maxResults": 100},
                                   timeout=ajs.REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        jira_comments += [norm(adf_text(c.get("body"))) for c in data.get("comments", [])]
        start += len(data.get("comments", []))
        if not data.get("comments") or start >= data.get("total", 0):
            break
    blob = " ".join(jira_comments)
    synced_comments = []
    for story in _asana_list(f"{ajs.ASANA_API}/tasks/{gid}/stories", "resource_subtype,text"):
        if story.get("resource_subtype") != "comment_added":
            continue
        snippet = norm(story.get("text"))[:60]
        if snippet and snippet in blob:
            synced_comments.append(story["gid"])
    r = ajs.request_with_retry("GET", f"{ajs.JIRA_BASE_URL}/rest/api/3/issue/{jira_key}",
                               auth=ajs.JIRA_AUTH, params={"fields": "attachment"}, timeout=ajs.REQUEST_TIMEOUT)
    r.raise_for_status()
    jira_files = {norm(a.get("filename")) for a in r.json()["fields"].get("attachment") or []}
    synced_atts = [a["gid"] for a in _asana_list(f"{ajs.ASANA_API}/tasks/{gid}/attachments", "name")
                   if norm(a.get("name")) in jira_files]
    return synced_comments, synced_atts


def linked_gid(jira_key: str) -> "str | None":
    """gid zadania Asany z linku w zgłoszeniu (albo None)."""
    r = ajs.request_with_retry("GET", f"{ajs.JIRA_BASE_URL}/rest/api/3/issue/{jira_key}/remotelink",
                               auth=ajs.JIRA_AUTH, timeout=ajs.REQUEST_TIMEOUT)
    if r.status_code != 200:
        return None
    for link in r.json():
        gid = (link.get("globalId") or "")
        if gid.startswith("asana:task:"):
            return gid.split(":", 2)[2]
    return None


def match(asana: dict, jira: dict, pstate: dict, link_map: "dict | None" = None,
          user_map: "dict | None" = None, explain: "str | None" = None) -> tuple:
    """Zwraca (pewne, podobne, kolejność, z linków, z treści, wiersze raportu) - słowniki {gid: jira_key}."""
    user_map = user_map or {}
    live = {g: e for g, e in pstate.items() if not e.get("deleted_in_jira")}
    key_of = {g: e["jira_key"] for g, e in live.items() if e["jira_key"] in jira}
    linked_keys = set(key_of.values())
    unlinked = {k for k in jira if k not in linked_keys}
    unmapped = {g for g in asana if g not in key_of}  # także wpisy "usunięte", jeśli zgłoszenie się znajdzie
    by_link = {}
    for k, g in (link_map or {}).items():
        if k in unlinked and g in unmapped and g not in by_link:
            by_link[g] = k
            key_of[g] = k
    unlinked -= set(by_link.values())
    unmapped -= set(by_link)
    asana_title_count = defaultdict(int)
    for a in asana.values():
        asana_title_count[norm(a["name"])] += 1

    def parent_ok(gid: str, key: str) -> "bool | None":
        """True/False gdy da się ocenić rodzica, None gdy rodzic z Asany nie ma zgłoszenia."""
        pg = asana[gid]["parent"]
        if pg is None:
            return jira[key]["parent"] is None or jira[jira[key]["parent"]]["level"] >= 1
        pk = key_of.get(pg)
        if not pk:
            return None
        anchors = {pk, jira.get(pk, {}).get("parent")} - {None}
        return jira[key]["parent"] in anchors

    def similar(a: str, b: str) -> bool:
        """Wszystkie SŁOWA krótszego tytułu są w dłuższym ("NB" ~ "GoLive NB", ale nie
        ~ "Onboarding"), albo tytuły są bardzo podobne jako całość."""
        a, b = norm(a), norm(b)
        if not a or not b:
            return False
        la, lb = re.findall(r"\w+", a), re.findall(r"\w+", b)
        ta, tb = set(la), set(lb)
        if ta and tb and (ta <= tb or tb <= ta):
            # Dłuższy tytuł może mieć najwyżej tyle DODATKOWYCH słów, ile słów ma krótszy
            # (min. 1): "NB" ~ "GoLive NB" tak, ale "[Produkte]" ~ "[Produkte] Produkte anlegen"
            # nie - jednowyrazowy nagłówek pasowałby do wszystkiego, co od niego się zaczyna.
            short, long_ = (la, lb) if len(la) <= len(lb) else (lb, la)
            if len(long_) - len(short) <= max(1, len(short)):
                return True
        # 0.6 okazało się za luźne dla krótkich niemieckich tytułów ("Sale Bilder sind nicht
        # lokalisiert" ~ "Newsletter Widget lokalisieren") - tylko prawie identyczne całe tytuły
        return difflib.SequenceMatcher(None, a, b).ratio() >= 0.85

    certain, similar_pairs, ordered_pairs, report = {}, {}, {}, []
    series_dups = []  # (gid, nadmiarowe zgłoszenie, numer serii) - kolejne serie kopii po kolejności
    position = {g: i for i, g in enumerate(asana)}  # kolejność rodzeństwa jak w Asanie

    def key_num(k: str) -> int:
        return int(k.rsplit("-", 1)[-1])
    by_title_jira = defaultdict(set)
    for k in unlinked:
        by_title_jira[norm(jira[k]["summary"])].add(k)

    for depth in sorted({a["depth"] for a in asana.values()}):
        layer = [g for g in unmapped if asana[g]["depth"] == depth]
        by_title = defaultdict(list)
        for g in layer:
            by_title[norm(asana[g]["name"])].append(g)
        for title, gids in by_title.items():
            keys = by_title_jira.get(title, set())
            if not keys:
                continue
            pairs = [(g, k) for g in gids for k in keys if parent_ok(g, k) is not False]
            gid_opts, key_opts = defaultdict(list), defaultdict(list)
            for g, k in pairs:
                gid_opts[g].append(k)
                key_opts[k].append(g)
            for g, k in pairs:
                if len(gid_opts[g]) == 1 and len(key_opts[k]) == 1:
                    certain[g] = k
                    key_of[g] = k
                    keys.discard(k)
            ambiguous = [g for g in gids if g not in certain and gid_opts.get(g)]
            # Grupy o IDENTYCZNYM zbiorze kandydatów (w praktyce: pod tym samym rodzicem w Jirze).
            # Szablon powtórzony pod różnymi rodzicami ("TODOs:" pod A i pod B) daje osobne
            # zbiory kopii - każdą grupę rozstrzygamy niezależnie.
            groups = defaultdict(list)
            for g in ambiguous:
                groups[frozenset(k for k in gid_opts[g] if k in keys)].append(g)
            for cand_set, group in groups.items():
                cand = sorted(cand_set, key=key_num)
                # rodzic musi być znany (albo brak rodzica - najwyższy poziom), żeby ustalić kolejność
                parents_ok = all(asana[g]["parent"] is None or key_of.get(asana[g]["parent"]) for g in group)
                n, m = len(group), len(cand)
                if parents_ok and m >= n and not (cand_set & set(ordered_pairs.values())):
                    # Kolejność tworzenia: synchronizacja szła po drzewie Asany, więc numery kluczy
                    # rosną jak kolejność rodziców. Gdy kopii jest więcej (ponowne synchronizacje
                    # utworzyły serie duplikatów), pierwsza = najstarsza seria zostaje powiązana,
                    # kolejne serie (także NIEPEŁNA, z przerwanej synchronizacji) to duplikaty.
                    order = sorted(group, key=lambda g: (key_num(key_of[asana[g]["parent"]]) if asana[g]["parent"] else 0,
                                                         position[g]))
                    for series in range((m + n - 1) // n):
                        for g, k in zip(order, cand[series * n:(series + 1) * n]):
                            keys.discard(k)
                            if series == 0:
                                ordered_pairs[g] = k
                                key_of[g] = k  # dzieci rozstrzygane dalej względem tego rodzica
                            else:
                                series_dups.append((g, k, series + 1))
                else:
                    for g in group:
                        report.append(["NIEJEDNOZNACZNE", g, asana[g]["name"], asana[g]["depth"],
                                       ";".join(sorted(gid_opts[g], key=key_num)), "",
                                       "kilka zgłoszeń pasuje tytułem i rodzicem"
                                       + ("" if parents_ok else " (rodzic też nierozstrzygnięty)")
                                       + ("" if m >= n else " (mniej kopii niż zadań)")])

        # Przebieg 2: ten sam rodzic (musi dać się sprawdzić!) + podobny tytuł, tylko pary jednoznaczne.
        rest = [g for g in layer if g not in certain and g not in ordered_pairs
                and not any(r[0] == "NIEJEDNOZNACZNE" and r[1] == g for r in report)]
        taken = set(certain.values()) | set(similar_pairs.values()) | set(ordered_pairs.values())
        free_keys = [k for k in unlinked if k not in taken]
        pairs = [(g, k) for g in rest for k in free_keys
                 if parent_ok(g, k) is True and similar(asana[g]["name"], jira[k]["summary"])]
        gid_opts, key_opts = defaultdict(list), defaultdict(list)
        for g, k in pairs:
            gid_opts[g].append(k)
            key_opts[k].append(g)
        for g, k in pairs:
            if len(gid_opts[g]) == 1 and len(key_opts[k]) == 1:
                similar_pairs[g] = k
                key_of[g] = k  # dzieci rozstrzygane dalej względem tego rodzica

    def score(g: str, k: str) -> "tuple[float, dict]":
        a, j = asana[g], jira[k]
        parts = {}
        ta, tj = norm(a["name"]), norm(j["summary"])
        parts["tytuł"] = 3 if ta == tj else (1.5 if similar(a["name"], j["summary"]) else 0)
        na, nj = norm(a.get("notes")), norm(j.get("desc"))
        if na and len(na) >= 20:
            parts["opis"] = 3 if na == nj else (1.5 if nj and difflib.SequenceMatcher(None, na[:400], nj[:400]).ratio() >= 0.85 else 0)
        if a.get("due") and j.get("due"):
            parts["termin"] = 1 if a["due"] == j["due"] else 0
        mapped_assignee = user_map.get(a.get("assignee")) if a.get("assignee") else None
        if mapped_assignee and j.get("assignee"):
            parts["przypisany"] = 1 if mapped_assignee == j["assignee"] else 0
        if "completed" in a and "done" in j:
            parts["ukończone"] = 0.5 if a["completed"] == j["done"] else 0
        po = parent_ok(g, k)
        parts["rodzic"] = 2 if po is True else (0 if po is None else -1)
        return sum(parts.values()), parts

    token_index = defaultdict(set)
    notes_index = defaultdict(set)
    for g, a in asana.items():
        for t in set(re.findall(r"\w+", norm(a["name"]))):
            token_index[t].add(g)
        if norm(a.get("notes")) and len(norm(a.get("notes"))) >= 20:
            notes_index[norm(a["notes"])].add(g)

    def candidates(k: str) -> list:
        j = jira[k]
        gids = set()
        for t in set(re.findall(r"\w+", norm(j["summary"]))):
            gids |= token_index.get(t, set())
        gids = {g for g in gids if similar(asana[g]["name"], j["summary"])}
        gids |= notes_index.get(norm(j.get("desc")), set())
        return sorted(((score(g, k) + (g,)) for g in gids), key=lambda x: -x[0])

    content_pairs = {}
    if explain and explain in jira:
        print(f"\n--explain {explain}: '{jira[explain]['summary']}' (rodzic w Jirze: {jira[explain]['parent']}, "
              f"powiązane: {'tak' if explain not in unlinked else 'nie'})")
        for total, parts, g in candidates(explain)[:15]:
            mapped = f"powiązane z {key_of[g]}" if g in key_of else "bez zgłoszenia"
            print(f"  {total:>5.1f} pkt | gid {g} '{asana[g]['name'][:50]}' poziom {asana[g]['depth']} | {mapped} | "
                  + ", ".join(f"{n} {v:+g}" for n, v in parts.items()))

    # Duplikaty: zgłoszenie bez powiązania o tytule i rodzicu jak zadanie, które JUŻ ma zgłoszenie.
    used = set(certain.values()) | set(similar_pairs.values()) | set(ordered_pairs.values())
    for g, k, series in series_dups:
        report.append(["DUPLIKAT", g, asana[g]["name"], asana[g]["depth"], k, ordered_pairs[g],
                       f"kopia po kolejności (seria {series}); powiązane zostaje najstarsze {ordered_pairs[g]}"])
        used.add(k)
    for k in sorted(k for k in unlinked if k not in used):
        title = norm(jira[k]["summary"])
        twins = [g for g, kk in key_of.items() if g in asana and norm(asana[g]["name"]) == title
                 and asana_title_count[title] == 1 and parent_ok(g, k) is not False]
        if twins:
            g = twins[0]
            report.append(["DUPLIKAT", g, asana[g]["name"], asana[g]["depth"], k, key_of[g],
                           f"to samo zadanie co {key_of[g]} (powiązane); zostawić kopię z pracą zespołu"])
        elif not any(r[0] == "NIEJEDNOZNACZNE" and k in r[4].split(";") for r in report):
            ranked = candidates(k)
            best = ranked[0] if ranked else None
            second = ranked[1][0] if len(ranked) > 1 else -99
            if best and best[0] >= 5 and best[0] - second >= 1.5:
                g = best[2]
                if g in key_of and key_of[g] != k:
                    report.append(["DUPLIKAT", g, asana[g]["name"], asana[g]["depth"], k, key_of[g],
                                   f"po treści ({best[0]:g} pkt); to samo zadanie co {key_of[g]}"])
                elif g not in key_of and g not in content_pairs:
                    content_pairs[g] = k
                    report.append(["TRESC", g, asana[g]["name"], asana[g]["depth"], k, jira[k]["parent"] or "",
                                   f"{best[0]:g} pkt: " + ", ".join(f"{n} {v:+g}" for n, v in best[1].items())])
                else:
                    report.append(["BRAK_W_ASANIE", "", "", "", k, "", jira[k]["summary"]])
            elif best and best[0] >= 5 and all(c[2] in key_of for c in ranked if c[0] >= best[0] - 1.5):
                twins = [key_of[c[2]] for c in ranked if c[0] >= best[0] - 1.5]
                report.append(["NADMIAROWA_KOPIA", "", jira[k]["summary"], "", k, ";".join(twins[:5]),
                               f"pasuje równie dobrze do {len(twins)} już powiązanych ({best[0]:g} pkt)"])
            else:
                top = "; ".join(f"{asana[c[2]]['name'][:30]} ({c[0]:g})" for c in ranked[:3])
                report.append(["BRAK_W_ASANIE", "", "", "", k, "",
                               f"{jira[k]['summary']} | najbliżsi: {top or 'brak kandydatów'}"])

    for g in sorted(unmapped - set(certain) - set(similar_pairs) - set(ordered_pairs) - set(content_pairs)):
        if pstate.get(g, {}).get("deleted_in_jira"):
            continue  # oznaczone jako usunięte i bez odpowiednika - zostaje jak jest
        if not any(r[1] == g for r in report):
            report.append(["NIEZMIGROWANE", g, asana[g]["name"], asana[g]["depth"], "", "", "utworzy je synchronizacja"])
    for g, k in certain.items():
        note = "wpis był oznaczony jako usunięty" if pstate.get(g, {}).get("deleted_in_jira") else ""
        report.append(["PEWNE", g, asana[g]["name"], asana[g]["depth"], k, jira[k]["parent"] or "", note])
    for g, k in similar_pairs.items():
        report.append(["PODOBNE", g, asana[g]["name"], asana[g]["depth"], k, jira[k]["parent"] or "",
                       f"w Jirze: '{jira[k]['summary']}'"])
    for g, k in ordered_pairs.items():
        report.append(["KOLEJNOSC", g, asana[g]["name"], asana[g]["depth"], k, jira[k]["parent"] or "",
                       f"rodzic w Asanie: {key_of.get(asana[g]['parent'])}"])
    for g, k in by_link.items():
        report.append(["LINK", g, asana[g]["name"], asana[g]["depth"], k, jira[k]["parent"] or "", "z linku w zgłoszeniu"])
    return certain, similar_pairs, ordered_pairs, by_link, content_pairs, report


def require_current_sync_module() -> None:
    """Narzędzie korzysta z funkcji z najnowszego asana_jira_sync.py - starsza wersja
    w folderze wyłożyłaby się w trakcie pracy, więc sprawdzamy to na starcie."""
    missing = [n for n in ("ensure_asana_link", "load_sync_exclude", "_STATE_SNAPSHOT") if not hasattr(ajs, n)]
    if missing:
        print(f"[BŁĄD] W folderze jest starsza wersja asana_jira_sync.py (brak: {', '.join(missing)}). "
              f"Podmień ją na najnowszą i uruchom ponownie. Nic nie zostało zmienione.")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project-key", required=True)
    parser.add_argument("--apply", action="store_true", help="Odtwórz wpisy PEWNE (domyślnie tylko raport)")
    parser.add_argument("--report-file", default=None, help="Ścieżka raportu (domyślnie relink_state_report.csv)")
    parser.add_argument("--include-content", action="store_true",
                        help="Z --apply: odtwórz też TRESC (punktacja z opisu, terminu, przypisanego, rodzica)")
    parser.add_argument("--explain", default=None,
                        help="Pokaż kandydatów z Asany i punkty za każde pole dla wskazanego zgłoszenia (np. PROJ3-530)")
    parser.add_argument("--include-order", action="store_true",
                        help="Z --apply: odtwórz też KOLEJNOSC (powtarzające się tytuły pod tym samym rodzicem)")
    parser.add_argument("--include-similar", action="store_true",
                        help="Z --apply: odtwórz też PODOBNE (ten sam rodzic, podobny tytuł) - po przejrzeniu raportu")
    args = parser.parse_args()
    key = args.project_key.strip().upper()
    import jira_sync as _core
    if not _core.refuse_if_protected([key], "relink_state"):
        return
    require_current_sync_module()
    global REPORT_FILE
    if args.report_file:
        REPORT_FILE = Path(args.report_file)
        REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)

    single, multi = sheet_projects()
    if key not in single:
        print(f"[BŁĄD] {key}: " + ("kilka projektów Asany w arkuszu" if key in multi else "brak w arkuszu linku do Asany"))
        sys.exit(1)
    print(f"Pobieram Asanę (wszystkie poziomy) i Jirę dla {key}...")
    asana = collect_asana(single[key], with_details=True)
    jira = collect_jira(key, with_details=True)
    state = ajs.load_state()
    pstate = {g: e for g, e in state.get(key, {}).items() if isinstance(e, dict) and e.get("jira_key")}

    linked_keys = {e["jira_key"] for e in pstate.values() if not e.get("deleted_in_jira")}
    candidates = [k for k in jira if k not in linked_keys]
    print(f"Sprawdzam linki do Asany w {len(candidates)} zgłoszeniach bez powiązania...")
    link_map = {}
    for k in candidates:
        g = linked_gid(k)
        if g:
            link_map[k] = g
    certain, similar_pairs, ordered_pairs, by_link, content_pairs, report = match(
        asana, jira, pstate, link_map, user_map=ajs.load_user_map(), explain=args.explain)
    counts = defaultdict(int)
    for r in report:
        counts[r[0]] += 1
    with open(REPORT_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["kategoria", "asana_gid", "asana_tytul", "poziom", "jira_key", "drugie_zgloszenie_lub_rodzic", "uwagi"])
        w.writerows(sorted(report, key=lambda r: (r[0], str(r[4]))))
    print(f"\nAsana: {len(asana)} | Jira: {len(jira)} | wpisy w stanie: {len(pstate)}")
    for cat in ("LINK", "PEWNE", "PODOBNE", "KOLEJNOSC", "TRESC", "DUPLIKAT", "NADMIAROWA_KOPIA",
                "NIEJEDNOZNACZNE", "BRAK_W_ASANIE", "NIEZMIGROWANE"):
        print(f"  {cat}: {counts.get(cat, 0)}")
    for r in [r for r in report if r[0] == "PODOBNE"][:10]:
        print(f"    podobne: {r[4]} '{r[6][10:-1]}' <- Asana '{r[2][:50]}'")
    for r in [r for r in report if r[0] == "DUPLIKAT"][:10]:
        print(f"    duplikat: {r[4]} = {r[5]} | {r[2][:60]}")
    print(f"Pełny raport: {REPORT_FILE}")

    if not args.apply:
        print("\n[TRYB RAPORTU] Nic nie zapisano. Po przejrzeniu raportu: --apply (PEWNE), "
              "a po sprawdzeniu par PODOBNE / KOLEJNOSC także --include-similar / --include-order.")
        return
    certain = {**by_link, **certain}
    if args.include_similar:
        certain = {**certain, **similar_pairs}
    if args.include_order:
        certain = {**certain, **ordered_pairs}
    if args.include_content:
        certain = {**certain, **content_pairs}
    if not certain:
        print("Brak pewnych dopasowań do zapisania.")
        return
    with ajs.project_lock(key) as acquired:
        if not acquired:
            print(f"[BŁĄD] {key} jest właśnie przetwarzany przez inny proces - przerwij go i spróbuj ponownie.")
            sys.exit(1)
        backup = ajs.STATE_FILE.with_name(f"{ajs.STATE_FILE.stem}.backup_{datetime.now():%Y%m%d_%H%M%S}_relink_{key}.json")
        shutil.copy2(ajs.STATE_FILE, backup)
        print(f"\nKopia stanu: {backup}")
        state = ajs.load_state()
        project_state = state.setdefault(key, {})
        done = 0
        for n, (gid, jkey) in enumerate(sorted(certain.items(), key=lambda x: asana[x[0]]["depth"]), 1):
            try:
                comments, atts = already_synced(gid, jkey)
            except Exception as exc:  # noqa: BLE001
                print(f"  [{n}/{len(certain)}] {jkey}: [POMINIĘTO] nie udało się sprawdzić komentarzy ({exc})")
                continue
            level, parent = jira[jkey]["level"], jira[jkey]["parent"]
            entry = {"jira_key": jkey, "asana_modified_at": None, "synced_comment_gids": comments,
                     "synced_attachment_gids": atts, "is_epic": level >= 1, "is_subtask": level < 0}
            if level < 0 and parent:
                entry["top_level_ancestor_key"] = parent
            project_state[gid] = entry
            # link w zgłoszeniu - kolejna utrata wpisu da się odtworzyć bez zgadywania
            ajs.ensure_asana_link(gid, jkey, asana[gid]["name"], entry, False)
            done += 1
            if done % 25 == 0:
                ajs.save_state({key: project_state})
                print(f"  odtworzono {done}/{len(certain)}...")
        ajs.save_state({key: project_state})
        print(f"\nOdtworzono wpisów: {done}. Dalej: duplikaty z raportu, potem refresh_state_flags.py i audit_hierarchy.py.")

if __name__ == "__main__":
    main()
