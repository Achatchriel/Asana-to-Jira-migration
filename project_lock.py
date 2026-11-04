"""
project_lock.py

Blokada na poziomie POJEDYNCZEGO PROJEKTU (nie pliku stanu jako całości) -
zapobiega sytuacji, w której dwa równolegle uruchomione procesy (np. dwa
uruchomienia run_full_pipeline.py naraz, albo dwa ręczne wywołania
asana_jira_sync.py z pokrywającymi się --project-keys) przetwarzają TEN SAM
projekt jednocześnie. To właśnie ten scenariusz mógłby prowadzić do
duplikatów zadań w Jirze (dwa procesy sprawdzają "czy zadanie już istnieje"
w tej samej chwili, oba widzą "nie", oba tworzą osobne zadanie dla tej
samej rzeczy z Asany) - mechanizm fcntl.flock przy zapisie task_sync_state.json
chroni tylko sam ZAPIS pliku, nie całą sekwencję sprawdź->utwórz.

Nieblokujące (LOCK_NB): jeśli inny proces już przetwarza dany projekt,
funkcja natychmiast zwraca acquired=False (zamiast czekać w nieskończoność
na zwolnienie blokady) - wywołujący kod ma wtedy pominąć ten projekt i
przejść do następnego, żeby nie blokować całego przebiegu.

Działa na Linuksie/macOS (moduł fcntl) - nie na Windows.

Użycie:
    from project_lock import project_lock

    for jira_key in project_keys:
        with project_lock(jira_key) as acquired:
            if not acquired:
                print(f"[POMINIĘTO] {jira_key}: przetwarzany właśnie przez inny proces.")
                continue
            # ... właściwa logika dla tego projektu ...
"""
import fcntl
from contextlib import contextmanager
from pathlib import Path

LOCKS_DIR = Path(__file__).resolve().parent / "locks"


@contextmanager
def project_lock(project_key: str):
    LOCKS_DIR.mkdir(exist_ok=True)
    lock_path = LOCKS_DIR / f"{project_key}.lock"
    lock_file = open(lock_path, "w")
    try:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            acquired = False
        yield acquired
    finally:
        if acquired:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
