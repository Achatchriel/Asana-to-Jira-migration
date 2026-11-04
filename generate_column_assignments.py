#!/usr/bin/env python3
"""
generate_column_assignments.py

Czyta arkusz (klucze projektów + board_template_id) i dla KAŻDEGO projektu
generuje gotową konfigurację (board ID + mapowanie kolumna -> statusy) w
formacie JavaScript, gotową do wklejenia razem z assign_statuses_to_columns.js
w konsoli przeglądarki (assignStatusesToManyBoards).

Reużywa funkcji z bulk_configure_columns.py (find_board_id, get_board_columns,
get_status_names, resolve_template_board_id) - te same, sprawdzone REST API,
tylko GENEROWANIE listy, żadnej przeglądarki na tym etapie.

Użycie:
    python generate_column_assignments.py                    # wszystkie z arkusza
    python generate_column_assignments.py --limit 5           # test na próbce
    python generate_column_assignments.py --project-keys A,B  # konkretne klucze
    python generate_column_assignments.py --output configs.js # inna nazwa pliku
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jira_sync as core  # reużywamy odczytu arkusza i cache'u kluczy
from bulk_configure_columns import (  # reużywamy sprawdzonych funkcji, bez duplikacji
    find_board_id,
    get_board_columns,
    get_status_names,
    resolve_template_board_id,
)

DEFAULT_OUTPUT = "column_assignments.js"

# Cache wzorców per board_template_id, żeby nie pytać Jiry wielokrotnie o to
# samo (analogicznie do bulk_configure_columns.py).
_template_cache: dict[str, dict] = {}


def get_mapping_for_template(board_template_id: str) -> dict:
    """Zwraca {nazwa_kolumny: [nazwy_statusów, ...]} dla danego wzorca."""
    resolved_id = int(board_template_id) if board_template_id else resolve_template_board_id()
    cache_key = str(resolved_id)
    if cache_key not in _template_cache:
        cols = get_board_columns(resolved_id)
        ids = [sid for c in cols for sid in c.statuses]
        names = get_status_names(ids)
        mapping = {c.name: [names.get(sid, sid) for sid in c.statuses] for c in cols if c.statuses}
        _template_cache[cache_key] = mapping
        print(f"  [Wzorzec: tablica {resolved_id}] {len(mapping)} kolumn ze statusami.")
    return _template_cache[cache_key]


def get_project_pairs(args) -> list[tuple[str, str]]:
    if args.project_keys:
        keys = [k.strip().upper() for k in args.project_keys.split(",") if k.strip()]
        override = args.template_board_id or ""
        return [(k, override) for k in keys]
    sheet_rows = core.get_sheet_rows()
    cache = core.load_key_cache()
    reserved = set(cache.values())
    pairs = []
    for row in sheet_rows:
        gid = core.extract_asana_project_gid(row.asana_project_link)
        key = core.assign_jira_key(row, gid, cache, reserved)
        pairs.append((key, (row.board_template_id or "").strip()))
    return pairs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--project-keys", type=str, default=None)
    parser.add_argument("--template-board-id", type=str, default=None)
    parser.add_argument("--output", type=str, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    project_pairs = get_project_pairs(args)
    if args.limit:
        project_pairs = project_pairs[: args.limit]
    print(f"Projektów do przetworzenia: {len(project_pairs)}")

    configs = []
    skipped = []
    for i, (key, board_template_id) in enumerate(project_pairs):
        print(f"[{i + 1}/{len(project_pairs)}] {key}")
        board_id = find_board_id(key)
        if not board_id:
            print(f"  [OSTRZEŻENIE] Brak tablicy dla {key} — pomijam.")
            skipped.append(key)
            continue

        mapping = get_mapping_for_template(board_template_id)
        configs.append({"boardId": board_id, "projectKey": key, "mapping": mapping})

    js_content = (
        "// Wygenerowane przez generate_column_assignments.py\n"
        "// Wklej to PO wklejeniu assign_statuses_to_columns.js, w tej samej konsoli.\n"
        "const columnAssignmentConfigs = "
        + json.dumps(configs, indent=2, ensure_ascii=False)
        + ";\n\n"
        "// Uruchom:\n"
        "// await assignStatusesToManyBoards(columnAssignmentConfigs);\n"
    )
    Path(args.output).write_text(js_content, encoding="utf-8")

    print("\n" + "=" * 60)
    print("PODSUMOWANIE")
    print(f"  Wygenerowano konfiguracji: {len(configs)}")
    print(f"  Pominięto (brak tablicy): {len(skipped)}")
    print(f"\nZapisano do: {args.output}")
    print(f"\nOtwórz ten plik, skopiuj CAŁĄ zawartość, wklej w konsoli przeglądarki")
    print(f"(po wcześniejszym wklejeniu assign_statuses_to_columns.js), a potem uruchom:")
    print(f"  await assignStatusesToManyBoards(columnAssignmentConfigs);")


if __name__ == "__main__":
    main()
