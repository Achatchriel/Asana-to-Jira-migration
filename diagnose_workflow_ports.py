#!/usr/bin/env python3
"""
diagnose_workflow_ports.py — TYLKO ODCZYT, niczego nie zmienia.

Ustala, co oznaczają numery portów (fromPort / toPort) w przejściach
workflow Jiry — czyli z której krawędzi prostokąta statusu wychodzi i do
której wchodzi strzałka. API ich nie dokumentuje, a build_jira_schema.py
potrzebuje tego, żeby strzałki szły jak w KlientX (wychodzą dołem, wchodzą
górą), zamiast wszystkie przez górną krawędź.

Metoda: bierze workflow narysowane RĘCZNIE w edytorze (pomija te z samymi
zerami — typowe dla workflow tworzonych przez API) i dla każdego numeru
portu liczy, w którą stronę średnio leży drugi koniec strzałki.

Użycie:
    python diagnose_workflow_ports.py
    python diagnose_workflow_ports.py --limit 200   # ile workflow przejrzeć
"""
import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core


def workflow_names(limit: int) -> list:
    names, start = [], 0
    while len(names) < limit:
        r = requests.get(f"{core.JIRA_BASE_URL}/rest/api/3/workflow/search",
                         auth=core.jira_auth(), params={"startAt": start, "maxResults": 50})
        r.raise_for_status()
        data = r.json()
        vals = data.get("values", [])
        names += [(w.get("id") or {}).get("name") or w.get("name") for w in vals]
        if data.get("isLast", True) or not vals:
            break
        start += len(vals)
    return [n for n in names if n][:limit]


def direction(dx: float, dy: float) -> str:
    """Kierunek wektora na ekranie (oś y rośnie w dół)."""
    ang = math.degrees(math.atan2(dy, dx)) % 360
    labels = ["prawo", "prawo-dół", "dół", "lewo-dół", "lewo", "lewo-góra", "góra", "prawo-góra"]
    return labels[int(((ang + 22.5) % 360) // 45)]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--limit", type=int, default=150)
    args = p.parse_args()

    names = workflow_names(args.limit)
    print(f"Przeglądam {len(names)} workflow...")

    # port -> lista wektorów (znormalizowanych) do drugiego końca strzałki
    from_vec, to_vec = defaultdict(list), defaultdict(list)
    used = 0
    for i in range(0, len(names), 20):
        batch = names[i:i + 20]
        r = requests.post(f"{core.JIRA_BASE_URL}/rest/api/3/workflows",
                          auth=core.jira_auth(), json={"workflowNames": batch})
        if r.status_code >= 300:
            print(f"  [OSTRZEŻENIE] Pobieranie partii: HTTP {r.status_code} {r.text[:150]}")
            continue
        for wf in r.json().get("workflows", []):
            pos = {s["statusReference"]: ((s.get("layout") or {}).get("x"), (s.get("layout") or {}).get("y"))
                   for s in wf.get("statuses", [])}
            links = [(l, t) for t in wf.get("transitions", []) for l in (t.get("links") or [])]
            if not links or all(l.get("fromPort", 0) == 0 and l.get("toPort", 0) == 0 for l, _ in links):
                continue  # utworzony przez API (same zera) — nic nie mówi o portach
            used += 1
            for l, t in links:
                a, b = pos.get(l.get("fromStatusReference")), pos.get(t.get("toStatusReference"))
                if not a or not b or None in a or None in b or a == b:
                    continue
                dx, dy = b[0] - a[0], b[1] - a[1]
                n = math.hypot(dx, dy)
                from_vec[l.get("fromPort")].append((dx / n, dy / n))
                to_vec[l.get("toPort")].append((-dx / n, -dy / n))

    print(f"Workflow z ręcznie ustawionymi portami: {used}\n")
    if not used:
        print("Brak danych — w tej Jirze nie ma workflow z ręcznie przeciąganymi strzałkami.")
        return

    def report(title, vecs):
        print(title)
        for port in sorted(vecs, key=lambda x: (x is None, x)):
            v = vecs[port]
            mx, my = sum(x for x, _ in v) / len(v), sum(y for _, y in v) / len(v)
            print(f"  port {port!s:>4}: {len(v):4d} strzałek, drugi koniec średnio w kierunku: "
                  f"{direction(mx, my):11s} (dx={mx:+.2f}, dy={my:+.2f})")
    report("fromPort — gdzie leży CEL strzałki wychodzącej z tego portu:", from_vec)
    print()
    report("toPort — gdzie leży ŹRÓDŁO strzałki wchodzącej do tego portu:", to_vec)
    print("\nPort na DOLE ma drugi koniec zwykle w kierunku 'dół', na GÓRZE — 'góra', itd.")


if __name__ == "__main__":
    main()
