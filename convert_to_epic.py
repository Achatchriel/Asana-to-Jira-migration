#!/usr/bin/env python3
"""
convert_to_epic.py

Konwertuje RĘCZNIE WSKAZANE zadania (po kluczu) na typ Epic. To OSOBNE,
ręczne narzędzie - NIE jest wbudowane w automatyczną synchronizację
(świadoma decyzja: przy 206 przypadkach głębszego zagnieżdżenia w Asanie,
automatyczne wykrywanie + retroaktywna konwersja byłyby zbyt ryzykowne -
patrz rozmowa). Użyj tego narzędzia selektywnie, dla konkretnych zadań,
które sam wybierzesz.

WAŻNE - znany błąd Jiry: zmiana typu zadania na Epic przez zwykłe API
zostawia pole "Epic Name" PUSTE, mimo że jest wymagane (Jira nie pilnuje
wymagalności przy zmianie typu, tylko przy tworzeniu). Ten skrypt USTAWIA
to pole jawnie, osobnym zapytaniem - bez tego Epic wyglądałby "zepsuty"
w interfejsie Jiry.

Opcjonalnie (--convert-subtasks): dla każdego przekonwertowanego Epika,
znajduje jego BEZPOŚREDNIE podzadania w Jirze i konwertuje JE na zwykły
Task, ustawiając pole "parent" na nowo utworzony Epic (nowoczesny sposób
łączenia z Epikiem, zastępujący przestarzałe pole "Epic Link").

Użycie:
    python convert_to_epic.py --keys OMRTLI-1,OMRTLI-2 --dry-run
    python convert_to_epic.py --keys OMRTLI-1,OMRTLI-2
    python convert_to_epic.py --keys OMRTLI-1 --convert-subtasks
"""
import argparse
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira_sync as core

_EPIC_NAME_FIELD_CACHE = None


def get_epic_name_field_id() -> str:
    """Znajduje ID pola 'Epic Name' (custom field, ID różni się per
    instancja Jiry) - wyszukiwane raz, buforowane."""
    global _EPIC_NAME_FIELD_CACHE
    if _EPIC_NAME_FIELD_CACHE:
        return _EPIC_NAME_FIELD_CACHE
    resp = requests.get(f"{core.JIRA_BASE_URL}/rest/api/3/field", auth=core.jira_auth())
    resp.raise_for_status()
    for field in resp.json():
        if field.get("name", "").strip().lower() == "epic name":
            _EPIC_NAME_FIELD_CACHE = field["id"]
            return field["id"]
    raise RuntimeError("Nie znaleziono pola 'Epic Name' w tej instancji Jiry — sprawdź ręcznie nazwę pola.")


def get_non_subtask_issue_type_name(project_key: str) -> str:
    """Zwraca nazwę standardowego (NIE-podzadaniowego) typu zadania w danym
    projekcie, do którego konwertujemy byłe podzadania Epika (zwykle
    'Task', ale nie zakładamy tego na sztywno)."""
    proj_resp = requests.get(f"{core.JIRA_BASE_URL}/rest/api/3/project/{project_key}", auth=core.jira_auth())
    proj_resp.raise_for_status()
    numeric_id = proj_resp.json()["id"]
    resp = requests.get(
        f"{core.JIRA_BASE_URL}/rest/api/3/issuetype/project", auth=core.jira_auth(),
        params={"projectId": numeric_id, "level": 0},
    )
    resp.raise_for_status()
    types = resp.json()
    for t in types:
        if t.get("name", "").strip().lower() == "task":
            return t["name"]
    return types[0]["name"] if types else "Task"


def get_direct_subtasks(issue_key: str) -> list:
    resp = requests.get(
        f"{core.JIRA_BASE_URL}/rest/api/3/issue/{issue_key}",
        auth=core.jira_auth(), params={"fields": "subtasks"},
    )
    resp.raise_for_status()
    return [(s["key"], s["fields"]["summary"]) for s in resp.json().get("fields", {}).get("subtasks", [])]


def convert_issue_to_epic(issue_key: str, epic_name: str, dry_run: bool) -> None:
    epic_field_id = get_epic_name_field_id()
    if dry_run:
        print(f"  [DRY-RUN] Zmieniłbym {issue_key} na Epic, z Epic Name = '{epic_name}'.")
        return
    resp = requests.put(
        f"{core.JIRA_BASE_URL}/rest/api/3/issue/{issue_key}",
        auth=core.jira_auth(), headers={"Content-Type": "application/json"},
        json={"fields": {"issuetype": {"name": "Epic"}, epic_field_id: epic_name}},
    )
    if resp.status_code >= 300 and epic_field_id and epic_field_id in resp.text:
        # Pole "Epic Name" istnieje GLOBALNIE w tej instancji Jiry, ale nie
        # jest skonfigurowane na ekranie edycji TEGO KONKRETNEGO projektu
        # (luka w konfiguracji per-projekt, niezależna od typu projektu -
        # potwierdzone ręcznie: dotyczy projektu company-managed, z Epikiem
        # obecnym w jego schemacie typów zadań). Potwierdzony błąd: "Field
        # 'customfield_XXXXX' cannot be set. It is not on the appropriate
        # screen, or unknown." Zamiast blokować całą konwersję, ponawiamy
        # BEZ tego pola.
        print(f"    [INFO] Pole 'Epic Name' nie jest skonfigurowane na ekranie edycji tego projektu — "
              f"pomijam je, kontynuuję konwersję bez niego.")
        resp = requests.put(
            f"{core.JIRA_BASE_URL}/rest/api/3/issue/{issue_key}",
            auth=core.jira_auth(), headers={"Content-Type": "application/json"},
            json={"fields": {"issuetype": {"name": "Epic"}}},
        )
    if resp.status_code >= 300:
        raise RuntimeError(f"Błąd zmiany typu na Epic: {resp.status_code} {resp.text[:300]}")
    print(f"  {issue_key}: zmieniono na Epic.")


def get_issue_type_id_by_name(project_key: str, type_name: str) -> str:
    """Zwraca ID (nie nazwę) typu zadania w danym projekcie - wymagane
    przez Bulk Move API, które przyjmuje '{project_key},{type_id}'
    jako klucz mapowania, nie nazwę."""
    proj_resp = requests.get(f"{core.JIRA_BASE_URL}/rest/api/3/project/{project_key}", auth=core.jira_auth())
    proj_resp.raise_for_status()
    numeric_id = proj_resp.json()["id"]
    resp = requests.get(
        f"{core.JIRA_BASE_URL}/rest/api/3/issuetype/project", auth=core.jira_auth(),
        params={"projectId": numeric_id, "level": 0},
    )
    resp.raise_for_status()
    for t in resp.json():
        if t.get("name", "").strip().lower() == type_name.strip().lower():
            return t["id"]
    raise RuntimeError(f"Nie znaleziono ID typu '{type_name}' w projekcie {project_key}")


def get_regular_subtask_type_id(project_key: str) -> str:
    """Zwraca ID REGULARNEGO (nie-legacy) typu podzadania w danym projekcie
    (level=-1). Filtruje typ legacy (opis zawiera 'Migrated') - dokładnie
    ten wzorzec, który powoduje błąd 'issue type selected is invalid' przy
    zwykłym PUT i 'appropriate hierarchy' przy set_parent.
    Używane przez bulk_convert_subtask_and_reparent do konwersji legacy
    Subtask -> regularny Sub-task przed przepięciem rodzica."""
    proj_resp = requests.get(f"{core.JIRA_BASE_URL}/rest/api/3/project/{project_key}", auth=core.jira_auth())
    proj_resp.raise_for_status()
    numeric_id = proj_resp.json()["id"]
    resp = requests.get(
        f"{core.JIRA_BASE_URL}/rest/api/3/issuetype/project", auth=core.jira_auth(),
        params={"projectId": numeric_id, "level": -1},
    )
    resp.raise_for_status()
    types = resp.json()
    # Preferuj typ BEZ "Migrated" w opisie (to jest ten legacy)
    for t in types:
        if "migrated" not in t.get("description", "").lower():
            return t["id"]
    if types:
        return types[0]["id"]
    raise RuntimeError(f"Nie znaleziono żadnego typu Sub-task w projekcie {project_key}")


def _get_issue_numeric_id(issue_key: str) -> str:
    """Zwraca numeryczne ID zadania Jiry (potrzebne do targetParentId w Bulk Move)."""
    resp = requests.get(
        f"{core.JIRA_BASE_URL}/rest/api/3/issue/{issue_key}",
        auth=core.jira_auth(), params={"fields": "id"},
    )
    resp.raise_for_status()
    return resp.json()["id"]


def _get_project_numeric_id(project_key: str) -> str:
    """Zwraca numeryczne ID projektu Jiry.
    Klucz targetToSourcesMapping to '{projectKey},{projectNumericId}' —
    NIE '{projectKey},{issueTypeId}' jak można by błędnie zakładać."""
    resp = requests.get(
        f"{core.JIRA_BASE_URL}/rest/api/3/project/{project_key}",
        auth=core.jira_auth(), params={"fields": "id"},
    )
    resp.raise_for_status()
    return resp.json()["id"]


def bulk_convert_subtask_and_reparent(issue_key: str, parent_key: str, dry_run: bool) -> None:
    """Konwertuje zadanie (dowolnego obecnego typu - Task, legacy Subtask, Epic
    po wcześniejszym przekonwertowaniu na Task itd.) na regularny Sub-task via
    Bulk Move API, przepinając od razu do nowego rodzica (parent_key).

    POTWIERDZONY EMPIRYCZNIE format (2026-09-27, zadanie GSESLA-65) - zgodny
    z oficjalnym przykładem Atlassiana dla przenoszenia NA typ Sub-task:
    https://developer.atlassian.com/cloud/jira/platform/bulk-operation-additional-examples-and-faqs/

    KLUCZOWA RÓŻNICA względem przenoszenia NA typ level-0 (Task, patrz
    _bulk_move_to_task): gdy celem jest typ SUB-TASK, rodzic musi być
    TRZECIM członem samego KLUCZA mapowania - "{project},{type_id},{parent_key}"
    (jako klucz Jira, NIE numeryczne ID!) - a NIE osobnym polem
    "targetParentId" wewnątrz obiektu. Wcześniejsza wersja z osobnym polem
    targetParentId (i kluczem "{project},{project_numeric_id}") zwracała
    zawsze ogólnikowe "Invalid request payload" - przetestowano 5 innych
    wariantów payloadu, żaden nie zadziałał, dopiero ta struktura klucza
    zadziałała (status 201, Bulk Move zakończone COMPLETE)."""
    if dry_run:
        print(f"        [DRY-RUN] Skonwertowałbym {issue_key} na regularny Sub-task "
              f"i przepiął rodzica na {parent_key}.")
        return
    project_key = issue_key.split("-")[0]
    type_id = get_regular_subtask_type_id(project_key)
    target = {
        "issueIdsOrKeys": [issue_key],
        "inferFieldDefaults": True,
        "inferStatusDefaults": True,
        "inferSubtaskTypeDefault": True,
    }
    payload = {"targetToSourcesMapping": {f"{project_key},{type_id},{parent_key}": target}}
    resp = requests.post(
        f"{core.JIRA_BASE_URL}/rest/api/3/bulk/issues/move",
        auth=core.jira_auth(), headers={"Content-Type": "application/json"},
        json=payload,
    )
    if resp.status_code == 400 and "is required" in resp.text:
        # Wymagane pole docelowego typu (u nas "Type" także dla Subtaska, np. przy
        # zamianie Taska na Sub-task - PROJ3-23). Te same wartości co przy tworzeniu,
        # a przy podanych polach Jira wymaga inferFieldDefaults = False.
        mandatory = bulk_mandatory_fields(project_key, type_id=str(type_id))
        if mandatory:
            target = {**target, "inferFieldDefaults": False, "targetMandatoryFields": [{"fields": mandatory}]}
            payload = {"targetToSourcesMapping": {f"{project_key},{type_id},{parent_key}": target}}
            resp = requests.post(
                f"{core.JIRA_BASE_URL}/rest/api/3/bulk/issues/move",
                auth=core.jira_auth(), headers={"Content-Type": "application/json"},
                json=payload,
            )
    if resp.status_code not in (200, 201, 202):
        print(f"        [DEBUG] Payload wysłany do /bulk/issues/move: {payload}")
        print(f"        [DEBUG] Pełna odpowiedź Jiry ({resp.status_code}): {resp.text}")
        raise RuntimeError(f"Błąd Bulk Move (sub2): {resp.status_code} {resp.text[:300]}")
    task_id = resp.json().get("taskId") or resp.json().get("id")
    if task_id:
        _poll_bulk_move_task(str(task_id))
    print(f"        {issue_key}: skonwertowano na regularny Sub-task via Bulk Move, rodzic = {parent_key}.")


def _poll_bulk_move_task(task_id: str, timeout: int = 60) -> None:
    """Czeka na zakończenie asynchronicznego zadania Bulk Move - Jira
    przetwarza je w tle i zwraca status via /rest/api/3/task/{taskId}."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = requests.get(f"{core.JIRA_BASE_URL}/rest/api/3/task/{task_id}", auth=core.jira_auth())
        if resp.status_code == 200:
            status = resp.json().get("status", "")
            if status == "COMPLETE":
                return
            if status in ("FAILED", "CANCEL_REQUESTED", "CANCELLED"):
                raise RuntimeError(f"Bulk Move zakończone niepowodzeniem: status={status}")
        time.sleep(2)
    raise RuntimeError(f"Bulk Move nie zakończyło się w ciągu {timeout}s (task_id={task_id})")


_MANDATORY_CACHE: dict = {}


def bulk_mandatory_fields(project_key: str, type_name: "str | None" = None, type_id: "str | None" = None) -> dict:
    """Wymagane pola docelowego typu w formacie Bulk Move (targetMandatoryFields),
    z tymi samymi wartościami, które synchronizacja ustawia przy tworzeniu:
      - pole "Type" (DEFAULT_TYPE_FIELD_ID) -> opcja DEFAULT_TYPE_FIELD_VALUE (NONBILLABLE),
      - pola z dokładnie jedną dozwoloną wartością -> ta wartość.
    Wartości pól wyboru jako identyfikatory opcji."""
    cache_key = (project_key, type_name, type_id)
    if cache_key in _MANDATORY_CACHE:
        return _MANDATORY_CACHE[cache_key]
    type_field = os.getenv("DEFAULT_TYPE_FIELD_ID", "customfield_10188")
    type_value = os.getenv("DEFAULT_TYPE_FIELD_VALUE", "NONBILLABLE")
    resp = requests.get(f"{core.JIRA_BASE_URL}/rest/api/3/issue/createmeta", auth=core.jira_auth(),
                        params={"projectKeys": project_key, "expand": "projects.issuetypes.fields",
                                **({"issuetypeIds": type_id} if type_id else {"issuetypeNames": type_name})})
    resp.raise_for_status()
    projects = resp.json().get("projects") or []
    meta = (projects[0]["issuetypes"][0].get("fields", {}) if projects and projects[0].get("issuetypes") else {})
    fields = {}
    for fid, info in meta.items():
        if not info.get("required") or fid in ("summary", "issuetype", "project", "parent", "reporter"):
            continue
        allowed = info.get("allowedValues") or []
        option = None
        if fid == type_field:
            option = next((a for a in allowed if (a.get("value") or a.get("name")) == type_value), None)
        elif len(allowed) == 1:
            option = allowed[0]
        if option and option.get("id"):
            fields[fid] = {"retain": False, "type": "raw", "value": [str(option["id"])]}
    _MANDATORY_CACHE[cache_key] = fields
    return fields


def _bulk_move_to_task(issue_key: str, project_key: str, type_name: str, parent_key: "str | None" = None) -> None:
    """Używa Jira Bulk Move API do konwersji legacy-type podzadania (np.
    id=10059, 'Migrated on 14 Mar 2023') na zwykły Task. Zwykłe
    PUT /issue/{key} z polem issuetype jest przez Jirę odrzucane dla tego
    starszego, zmigrowanego z poprzedniej instancji Jiry typu - błąd:
    'The issue type selected is invalid.' Potwierdzono ręcznie, że UI
    używa Move flow (nie edycji pola) i to działa. Bulk Move API to
    REST-owy odpowiednik tego flow.
    WAŻNE: Bulk Move USUWA link rodzica - po konwersji trzeba ustawić
    rodzica osobno.

    Warianty klucza mapowania, po kolei:
      1. "PROJEKT,typ,RODZIC" (gdy podano parent_key) - przy wyjmowaniu PODZADANIA
         spod rodzica Jira odrzucała wariant 2 z "Type is required" (PROJ3-519);
         przy przenoszeniu NA Sub-task rodzic w kluczu okazał się wymagany,
      2. "PROJEKT,typ" - dotychczasowy format, sprawdzony przy zmianie Epika na Task."""
    type_id = get_issue_type_id_by_name(project_key, type_name)
    mandatory = bulk_mandatory_fields(project_key, type_name)
    keys = ([f"{project_key},{type_id},{parent_key}"] if parent_key else []) + [f"{project_key},{type_id}"]
    # Najpierw z wymaganymi polami docelowego typu (np. "Type" - bez niego Jira odpowiada
    # "Type is required"), potem bez nich (dotychczasowe, sprawdzone zachowanie).
    variants = [(k, mandatory) for k in keys if mandatory] + [(k, None) for k in keys]
    errors = []
    for mapping_key, mandatory_fields in variants:
        target = {
            "issueIdsOrKeys": [issue_key],
            "inferClassificationDefaults": True,
            "inferFieldDefaults": True,
            "inferStatusDefaults": True,
            "inferSubtaskTypeDefault": True,
        }
        if mandatory_fields:
            # Jira: "Target mandatory fields mapping cannot be present when inferFieldDefaults is true."
            target["inferFieldDefaults"] = False
            target["targetMandatoryFields"] = [{"fields": mandatory_fields}]
        resp = requests.post(
            f"{core.JIRA_BASE_URL}/rest/api/3/bulk/issues/move",
            auth=core.jira_auth(), headers={"Content-Type": "application/json"},
            json={"sendBulkNotification": False, "targetToSourcesMapping": {mapping_key: target}},
        )
        if resp.status_code in (200, 201, 202):  # Jira może zwrócić 201 lub 202 zależnie od wersji
            break
        errors.append(f"[{mapping_key}{' + pola wymagane' if mandatory_fields else ''}] {resp.status_code} {resp.text[:300]}")
    else:
        raise RuntimeError("Błąd Bulk Move: " + " | ".join(errors))
    task_id = resp.json().get("taskId") or resp.json().get("id")
    if task_id:
        _poll_bulk_move_task(str(task_id))


def convert_subtask_to_task(sub_key: str, task_type_name: str, epic_key: str, dry_run: bool) -> None:
    if dry_run:
        print(f"    [DRY-RUN] Zmieniłbym {sub_key} na '{task_type_name}', rodzic (parent) = {epic_key}.")
        return
    resp = requests.put(
        f"{core.JIRA_BASE_URL}/rest/api/3/issue/{sub_key}",
        auth=core.jira_auth(), headers={"Content-Type": "application/json"},
        json={"fields": {"issuetype": {"name": task_type_name}, "parent": {"key": epic_key}}},
    )
    if resp.status_code >= 300:
        err = resp.json().get("errors") or {}
        err_text = resp.text
        is_legacy_type = "issuetype" in err and "invalid" in err.get("issuetype", "").lower()
        # Jira zwraca "pid" błąd gdy: (a) typ Sub-task nie może mieć Epic jako rodzica
        # przez zwykły PUT, LUB (b) epic jest w innym projekcie. W obu przypadkach
        # Bulk Move API (który obsługuje zmianę hierarchii) radzi sobie z (a).
        # Przypadek (b) — cross-project — jest nierozwiązywalny; rzucimy czytelny błąd.
        is_hierarchy_error = ("pid" in err or
                              "appropriate hierarchy" in err_text or
                              "same project as the parent" in err_text)
        if is_legacy_type or is_hierarchy_error:
            # Sprawdź najpierw czy to nie cross-project (nierozwiązywalne)
            sub_proj = sub_key.split("-")[0]
            epic_proj = epic_key.split("-")[0]
            if sub_proj != epic_proj:
                raise RuntimeError(
                    f"Błąd konwersji podzadania: {sub_key} (projekt {sub_proj}) "
                    f"i epic {epic_key} (projekt {epic_proj}) są w różnych projektach — "
                    f"Jira nie pozwala na cross-project parent. Sprawdź task_sync_state.json."
                )
            # Ten sam projekt — legacy typ lub hierarchia. Użyj Bulk Move.
            print(f"      [INFO] {sub_key}: zwykła edycja odrzucona "
                  f"({'legacy typ' if is_legacy_type else 'hierarchia'}) "
                  f"— używam Bulk Move API.")
            project_key = sub_key.split("-")[0]
            _bulk_move_to_task(sub_key, project_key, task_type_name, parent_key=epic_key)
            # Bulk Move usuwa link rodzica - ustawiamy go osobno
            set_resp = requests.put(
                f"{core.JIRA_BASE_URL}/rest/api/3/issue/{sub_key}",
                auth=core.jira_auth(), headers={"Content-Type": "application/json"},
                json={"fields": {"parent": {"key": epic_key}}},
            )
            if set_resp.status_code >= 300:
                raise RuntimeError(f"Bulk Move OK, ale nie udało się ustawić rodzica: "
                                   f"{set_resp.status_code} {set_resp.text[:200]}")
            print(f"    {sub_key}: zmieniono na '{task_type_name}' via Bulk Move, rodzic = {epic_key}.")
            return
        raise RuntimeError(f"Błąd konwersji podzadania: {resp.status_code} {resp.text[:300]}")
    print(f"    {sub_key}: zmieniono na '{task_type_name}', rodzic = {epic_key}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keys", required=True, help="Klucze zadań do zmiany na Epic, po przecinku.")
    parser.add_argument("--convert-subtasks", action="store_true",
                         help="Dodatkowo: bezpośrednie podzadania każdego Epika zmień na zwykły Task, powiązany z Epikiem.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    keys = [k.strip() for k in args.keys.split(",") if k.strip()]
    print(f"Do konwersji na Epic: {len(keys)} zadań.")
    if args.dry_run:
        print("[TRYB DRY-RUN - nic nie zostanie zmienione]\n")

    ok, failed = 0, []
    for key in keys:
        try:
            resp = requests.get(f"{core.JIRA_BASE_URL}/rest/api/3/issue/{key}", auth=core.jira_auth(), params={"fields": "summary"})
            resp.raise_for_status()
            summary = resp.json()["fields"]["summary"]

            convert_issue_to_epic(key, summary, args.dry_run)

            if args.convert_subtasks:
                subtasks = get_direct_subtasks(key)
                if subtasks:
                    project_key = key.split("-")[0]
                    task_type = get_non_subtask_issue_type_name(project_key)
                    print(f"    Znaleziono {len(subtasks)} bezpośrednich podzadań — konwertuję na '{task_type}'...")
                    for sub_key, sub_summary in subtasks:
                        convert_subtask_to_task(sub_key, task_type, key, args.dry_run)
                else:
                    print(f"    Brak bezpośrednich podzadań dla {key}.")
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  [BŁĄD] {key}: {exc}")
            failed.append((key, str(exc)))

    print("\n" + "=" * 60)
    print(f"PODSUMOWANIE: OK={ok}, błędy={len(failed)}")
    print("=" * 60)
    if failed:
        for key, err in failed:
            print(f"  {key}: {err}")


if __name__ == "__main__":
    main()
