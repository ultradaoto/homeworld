"""
Hyperspace Jump — snapshot fleet state, commit outputs to git, reset ephemeral state.

Initiating a jump:
  1. Archive current outputs + state to state/jump_<ts>/
  2. git add + commit the outputs directory
  3. Clear active agents, objective, enemy threats
  4. RU balance carries over to the next deployment zone
"""
import datetime
import json
import os
import shutil
import subprocess

from memory import store
from config import OUTPUT_DIR, STATE_DIR


def jump(commit_message: str = "") -> dict:
    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    # 1. Archive
    archive_dir = os.path.join(STATE_DIR, f"jump_{ts}")
    os.makedirs(archive_dir, exist_ok=True)

    snapshot = {
        "ru_balance":            store.get("ru_balance", 0),
        "findings_count":        store.get("findings_count", 0),
        "unlocked_capabilities": store.get("unlocked_capabilities", []),
        "objective":             store.get("current_objective", ""),
        "harvest_refs":          store.get("harvest_refs", []),
        "jump_ts":               ts,
    }
    with open(os.path.join(archive_dir, "snapshot.json"), "w") as f:
        json.dump(snapshot, f, indent=2)

    if os.path.exists(OUTPUT_DIR):
        shutil.copytree(OUTPUT_DIR, os.path.join(archive_dir, "outputs"),
                        dirs_exist_ok=True)

    # 2. Git commit
    msg = commit_message or (
        f"hyperspace: jump at {ts} — "
        f"{snapshot['objective'][:60] or 'mission complete'}"
    )
    git_result: dict = {"committed": False}
    try:
        project_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..")
        )
        subprocess.run(
            ["git", "add", "-f", "mothership/outputs/"],
            cwd=project_root, check=True,
            capture_output=True
        )
        proc = subprocess.run(
            ["git", "commit", "-m", msg],
            cwd=project_root, capture_output=True, text=True
        )
        git_result = {
            "committed": proc.returncode == 0,
            "msg":       proc.stdout.strip()[:200],
        }
    except Exception as e:
        git_result["error"] = str(e)

    # 3. Reset ephemeral state (RUs carry over)
    store.set("active_agents", [])
    store.set("current_objective", "awaiting next orders")
    store.set("enemy_threats", [])
    store.log_action("hyperspace", "jump", ts)

    return {
        "status":     "jumped",
        "ts":         ts,
        "archive":    archive_dir,
        "git":        git_result,
        "ru_carried": snapshot["ru_balance"],
    }
