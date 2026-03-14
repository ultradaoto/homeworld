"""
research_substation.py — Entropy System research synthesis (Phase 11).

Processes scout intel into architecture documents and advances the tech tree.
Run as a background service or via: python research_substation.py

Tech tree tiers (hardcoded defaults, seeded into tech_tree table on first run):
  Tier 1  — Exploration (no prereqs): harvesters, light interceptors
  Tier 2  — Understanding: ast_mapping
  Tier 3  — Tactical: database_schema_understanding, auth_flow_mapping
  Tier 4  — Strategic: full_system_context  → unlocks destroyers
"""
import glob
import json
import os
import sqlite3
import time
from datetime import datetime

DB_PATH = os.environ.get(
    "SQLITE_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "homeworld.db"),
)
CODEBASE_PATH    = os.environ.get("CODEBASE_PATH", "D:/Projects/Homeworld")
INTEL_DIR        = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "intel")
DOCS_DIR         = os.path.join(CODEBASE_PATH, "docs", "architecture")
SYNTHESIS_THRESHOLD = 3   # intel files needed before auto-synthesis triggers
LOOP_INTERVAL       = 60  # seconds

# Canonical tech tree definition -----------------------------------------------
TECH_TREE_NODES = [
    {
        "node_id":      "ast_mapping",
        "requires":     ["3_sectors_scouted"],
        "produces":     "dependency_graph.md",
        "unlocks":      "interceptors",
        "description":  "Map codebase AST and dependency graph from scout reports",
    },
    {
        "node_id":      "database_schema_understanding",
        "requires":     ["ast_mapping"],
        "produces":     "schema.md",
        "unlocks":      "assault_frigates",
        "description":  "Understand database schema and data-flow from intel",
    },
    {
        "node_id":      "auth_flow_mapping",
        "requires":     ["ast_mapping"],
        "produces":     "auth_flow.md",
        "unlocks":      "carriers",
        "description":  "Map authentication and authorization flows",
    },
    {
        "node_id":      "full_system_context",
        "requires":     ["database_schema_understanding", "auth_flow_mapping"],
        "produces":     "system_architecture.md",
        "unlocks":      "destroyers",
        "description":  "Complete system context enabling capital-ship deployment",
    },
]


# ── Database ───────────────────────────────────────────────────────────────────

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


def ensure_tech_tree() -> None:
    """Seed tech_tree with canonical nodes if table is empty."""
    with _db() as conn:
        for node in TECH_TREE_NODES:
            conn.execute(
                """INSERT OR IGNORE INTO tech_tree
                   (node_id, status, requires_json, produces, unlocks, description)
                   VALUES (?, 'locked', ?, ?, ?, ?)""",
                (
                    node["node_id"],
                    json.dumps(node["requires"]),
                    node["produces"],
                    node["unlocks"],
                    node["description"],
                ),
            )
        conn.commit()
    print("[research_substation] Tech tree seeded.")


# ── Prerequisite checking ─────────────────────────────────────────────────────

def _count_scouted_sectors() -> int:
    try:
        map_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "state", "sector_map.json"
        )
        if not os.path.exists(map_path):
            return 0
        data = json.loads(open(map_path, encoding="utf-8").read())
        return sum(1 for s in data.get("sectors", {}).values() if s.get("scouted"))
    except Exception:
        return 0


def _can_unlock(node_id: str, conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT requires_json FROM tech_tree WHERE node_id = ?", (node_id,)
    ).fetchone()
    if not row:
        return False
    requires = json.loads(row["requires_json"] or "[]")
    for req in requires:
        if req == "3_sectors_scouted":
            if _count_scouted_sectors() < 3:
                return False
        else:
            dep = conn.execute(
                "SELECT status FROM tech_tree WHERE node_id = ?", (req,)
            ).fetchone()
            if not dep or dep["status"] != "completed":
                return False
    return True


# ── Intel collection ──────────────────────────────────────────────────────────

def _list_intel_files() -> list[str]:
    if not os.path.exists(INTEL_DIR):
        return []
    return glob.glob(os.path.join(INTEL_DIR, "*.json"))


def _concat_intel(files: list[str], cap: int = 12) -> str:
    parts = []
    for fpath in files[:cap]:
        try:
            data = json.loads(open(fpath, encoding="utf-8").read())
            heading = os.path.basename(fpath)
            body = data.get("briefing") or data.get("content") or str(data)[:500]
            parts.append(f"## {heading}\n{body}")
        except Exception:
            pass
    return "\n\n".join(parts) if parts else "(no scout intel available)"


# ── Synthesis ─────────────────────────────────────────────────────────────────

def synthesize_node(node_id: str) -> dict:
    """
    Run LLM synthesis for one tech-tree node.
    Returns a result dict: {status, node_id, artifact} or {error}.
    """
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM tech_tree WHERE node_id = ?", (node_id,)
        ).fetchone()
        if not row:
            return {"error": f"Unknown node: {node_id}"}
        if row["status"] == "completed":
            return {"status": "already_complete", "artifact": row["artifact_path"]}
        if not _can_unlock(node_id, conn):
            return {"error": f"Prerequisites not met for {node_id}"}
        conn.execute(
            "UPDATE tech_tree SET status = 'in_progress' WHERE node_id = ?", (node_id,)
        )
        conn.commit()
        description = row["description"] or node_id
        produces    = row["produces"]    or f"{node_id}.md"

    # Gather intel for synthesis
    intel_content = _concat_intel(_list_intel_files())

    # LLM synthesis call
    try:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=os.environ.get("MOTHERSHIP_MODEL", "claude-haiku-4-5-20251001"),
            max_tokens=2000,
            system=(
                "You are an Architecture Research Agent for a software project. "
                "Synthesize the provided scout intel into a concise, structured "
                "technical document. Focus on actionable architectural insights."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Research task: {description}\n\n"
                    f"Scout Intelligence:\n\n{intel_content}\n\n"
                    "Produce a markdown document with sections: "
                    "Overview, Key Dependencies, Risk Areas, Recommended Approach."
                ),
            }],
        )
        result_text = msg.content[0].text
    except Exception as exc:
        result_text = (
            f"# {node_id}\n\nSynthesis unavailable: {exc}\n\n"
            f"## Raw Intel\n{intel_content[:800]}"
        )

    # Write artifact to docs/architecture/
    os.makedirs(DOCS_DIR, exist_ok=True)
    artifact_path = os.path.join(DOCS_DIR, produces)
    with open(artifact_path, "w", encoding="utf-8") as fh:
        fh.write(result_text)

    # Mark completed
    with _db() as conn:
        conn.execute(
            """UPDATE tech_tree SET status = 'completed',
               completed_at = datetime('now'), artifact_path = ?
               WHERE node_id = ?""",
            (artifact_path, node_id),
        )
        conn.commit()

    print(f"[research_substation] Node '{node_id}' completed -> {artifact_path}")
    return {"status": "completed", "node_id": node_id, "artifact": artifact_path}


# ── Auto-unlock pass ──────────────────────────────────────────────────────────

def check_auto_unlock() -> list[str]:
    """Promote 'locked' nodes to 'unlocked' when prerequisites are met."""
    unlocked: list[str] = []
    with _db() as conn:
        locked = [r["node_id"] for r in conn.execute(
            "SELECT node_id FROM tech_tree WHERE status = 'locked'"
        )]
        for node_id in locked:
            if _can_unlock(node_id, conn):
                conn.execute(
                    "UPDATE tech_tree SET status = 'unlocked' WHERE node_id = ?",
                    (node_id,),
                )
                unlocked.append(node_id)
                print(f"[research_substation] Node '{node_id}' unlocked.")
        if unlocked:
            conn.commit()
    return unlocked


def process_intel_queue() -> None:
    """If enough intel has accumulated, synthesize the next unlocked node."""
    if len(_list_intel_files()) < SYNTHESIS_THRESHOLD:
        return
    with _db() as conn:
        row = conn.execute(
            "SELECT node_id FROM tech_tree WHERE status = 'unlocked' LIMIT 1"
        ).fetchone()
    if row:
        synthesize_node(row["node_id"])


# ── Main loop ─────────────────────────────────────────────────────────────────

def research_loop() -> None:
    ensure_tech_tree()
    print("[research_substation] Starting research loop...")
    while True:
        try:
            check_auto_unlock()
            process_intel_queue()
        except Exception as exc:
            print(f"[research_substation] Error: {exc}")
        time.sleep(LOOP_INTERVAL)


if __name__ == "__main__":
    research_loop()
