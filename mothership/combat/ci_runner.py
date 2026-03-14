import glob
import os
import re
import subprocess
import sys

from combat.threat_engine import analyse_errors, spawn_enemies, resolve_enemy
from memory import store
from config import OUTPUT_DIR


def run_tests(test_path: str = None, verbose: bool = True) -> dict:
    """
    Run pytest on test_mine_*.py files in outputs/.
    Failed tests → spawn enemies.
    Passed tests → resolve enemies.
    """
    search_dir = test_path or OUTPUT_DIR
    mine_files = sorted(glob.glob(os.path.join(search_dir, "test_mine_*.py")))

    if not mine_files:
        return {"status": "no_mines", "note": "run 'mine <code>' first"}

    results = {"passed": 0, "failed": 0, "enemies_spawned": 0, "enemies_resolved": 0}

    for mf in mine_files:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", mf,
             "-v" if verbose else "-q", "--tb=short", "--no-header"],
            capture_output=True, text=True, timeout=60
        )
        output = proc.stdout + proc.stderr

        if proc.returncode == 0:
            results["passed"] += 1
            passed_count = len(re.findall(r"PASSED", output))
            for _ in range(passed_count):
                resolve_enemy("SyntaxError")
                results["enemies_resolved"] += 1
        else:
            results["failed"] += 1
            threats = analyse_errors(output)
            n = spawn_enemies(threats)
            results["enemies_spawned"] += n

    store.log_action("ci_runner", "test_run", str(results))
    return results
