"""
Tech Tree — build-class gating based on research (Phase 12).

Gates what ships you can build based on codebase research:

  (free)               → scout, probe, resource, repair, salvage
  ast_mapping          → interceptor   (3 sectors scouted)
  database_schema      → frigate       (5 sectors scouted)
  auth_flow            → carrier       (8 sectors scouted)
  full_system_context  → destroyer     (15 sectors scouted)

complete_research(node_id, artifact_path) is called by the Research Ship
(or manually via `research complete <node>` in the TUI) once an
architecture document has been produced for that domain.

is_buildable(ship_class) → (True, "") | (False, reason) is checked by
build_queue.enqueue() before accepting a build order.
"""
import datetime
import json
import os
import sqlite3

from config import STATE_DIR
from memory.store import DB_PATH

TECH_TREE_FILE = os.path.join(STATE_DIR, "tech_tree.json")

# ── Build-gate tiers ──────────────────────────────────────────────────────────

FREE_TIER = frozenset({
    "scout", "probe", "resource", "repair", "salvage",
})

# research node → ship class it unlocks
RESEARCH_UNLOCKS: dict[str, str] = {
    "ast_mapping":         "interceptor",
    "database_schema":     "frigate",
    "auth_flow":           "carrier",
    "full_system_context": "destroyer",
}

RESEARCH_REQUIREMENTS: dict[str, dict] = {
    "ast_mapping": {
        "description":             "Map the Python AST structure of the project",
        "requires_sectors_scouted": 3,
    },
    "database_schema": {
        "description":             "Produce a full database schema diagram and description",
        "requires_sectors_scouted": 5,
    },
    "auth_flow": {
        "description":             "Map the complete authentication and authorization flow",
        "requires_sectors_scouted": 8,
    },
    "full_system_context": {
        "description":             "Produce a complete system architecture overview",
        "requires_sectors_scouted": 15,
    },
}


# ── JSON persistence ──────────────────────────────────────────────────────────

def _default_tree() -> dict:
    return {
        "research_nodes": {
            node: {
                "status":   "locked",
                "unlocks":  RESEARCH_UNLOCKS[node],
                "requires": RESEARCH_REQUIREMENTS[node]["description"],
                "artifact": "",
            }
            for node in RESEARCH_UNLOCKS
        }
    }


def load_tech_tree() -> dict:
    if not os.path.exists(TECH_TREE_FILE):
        tree = _default_tree()
        _save_tree(tree)
        return tree
    try:
        with open(TECH_TREE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return _default_tree()


def _save_tree(tree: dict) -> None:
    os.makedirs(os.path.dirname(TECH_TREE_FILE), exist_ok=True)
    tmp = TECH_TREE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(tree, f, indent=2)
    os.replace(tmp, TECH_TREE_FILE)


# ── Core API ──────────────────────────────────────────────────────────────────

def is_buildable(ship_class: str) -> tuple[bool, str]:
    """
    Returns (True, "") if the ship class can be built now.
    Returns (False, reason) if research is required first.
    """
    if ship_class in FREE_TIER:
        return True, ""

    tree = load_tech_tree()
    for node_id, data in tree["research_nodes"].items():
        if data.get("unlocks") == ship_class:
            if data.get("status") == "completed":
                return True, ""
            req = RESEARCH_REQUIREMENTS.get(node_id, {})
            return False, (
                f"{ship_class} locked — research '{node_id}' first. "
                f"Requires: {req.get('description', '')}"
            )

    # Unknown class — allow by default (backwards compat)
    return True, ""


def complete_research(node_id: str, artifact_path: str = "") -> bool:
    """
    Mark a research node complete and unlock the corresponding ship class.
    Called by the Research Ship (or manually from the TUI).
    Returns True on success, False if node_id is unknown.
    """
    tree = load_tech_tree()
    if node_id not in tree["research_nodes"]:
        return False

    tree["research_nodes"][node_id].update({
        "status":       "completed",
        "artifact":     artifact_path,
        "completed_at": datetime.datetime.utcnow().isoformat(),
    })
    _save_tree(tree)

    # Sync to fleet.db tech_tree table
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute(
            """INSERT OR REPLACE INTO tech_tree
               (node_id, status, completed_at, artifact_path)
               VALUES (?, 'completed', datetime('now'), ?)""",
            (node_id, artifact_path),
        )
        con.commit()
        con.close()
    except Exception:
        pass   # table might not exist yet; JSON file is canonical

    unlocked = RESEARCH_UNLOCKS.get(node_id, "unknown")
    print(f"[tech_tree] ✓ Research '{node_id}' complete — {unlocked} unlocked")
    return True


def get_available_research(sectors_scouted: int) -> list[dict]:
    """Research nodes that are ready to start based on exploration progress."""
    tree      = load_tech_tree()
    available = []
    for node_id, data in tree["research_nodes"].items():
        if data.get("status") != "locked":
            continue
        needed = RESEARCH_REQUIREMENTS.get(node_id, {}).get("requires_sectors_scouted", 0)
        if sectors_scouted >= needed:
            available.append({
                "node_id":     node_id,
                "unlocks":     data["unlocks"],
                "description": RESEARCH_REQUIREMENTS[node_id]["description"],
                "needs":       needed,
            })
    return available


def tree_status() -> str:
    """Human-readable tech tree for TUI display."""
    tree  = load_tech_tree()
    lines = ["\n─── BUILD TECH TREE ──────────────────────────────────"]
    for node, data in tree["research_nodes"].items():
        status  = data.get("status", "locked")
        unlocks = data.get("unlocks", "?")
        icon    = "✓" if status == "completed" else "▶" if status == "in_progress" else "✗"
        color   = "completed_at" in data and status == "completed"
        when    = ("  [" + data["completed_at"][:10] + "]") if color else ""
        lines.append(
            f"  [{icon}]  {node:<28} → {unlocks:<16}  ({status}){when}"
        )
    lines.append("──────────────────────────────────────────────────────")
    return "\n".join(lines)
