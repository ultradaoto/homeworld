"""
capital_duel.py — Capital ship duel resolution (Phase 11 / Phase 13 rewrite).

A destroyer-class player ship engages an entropy_destroyer in a single-sector
duel.  In Phase 13 each round is a real patch pipeline run:
  - Player deals damage equal to tests_passed × 50
  - Entropy counter-fires tests_failed × 30 (reduced by escort point defense)
  - Syntax error → degraded round: half damage to both
  - No test runner → 50% effectiveness

Outcome table:
  all tests pass in final round  → victory
  player hp ≤ 0                  → defeat
  both ≤ 0 same round            → mutual_destruction

Public API:
    run_capital_duel(player_ship_id, entropy_unit_id, db_path) -> dict
"""
import json
import os
import re
import sqlite3
from datetime import datetime, timezone


# ── Helpers ────────────────────────────────────────────────────────────────────

def _db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


def _load_row(table: str, pk_col: str, pk_val: str, conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        f"SELECT * FROM {table} WHERE {pk_col} = ?", (pk_val,)
    ).fetchone()
    return dict(row) if row else None


def _sector_slug(filepath: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", filepath.replace("\\", "/"))[-60:]


def _count_escort_ships(player_ship_id: str, conn: sqlite3.Connection) -> int:
    """Count active corvettes/interceptors in the same sector as the destroyer."""
    row = conn.execute(
        "SELECT sector_id FROM active_ships WHERE id = ?", (player_ship_id,)
    ).fetchone()
    if not row or not row["sector_id"]:
        return 0
    result = conn.execute(
        """SELECT COUNT(*) FROM active_ships
           WHERE sector_id = ? AND class IN ('corvette','interceptor') AND status = 'active'""",
        (row["sector_id"],),
    ).fetchone()
    return result[0] if result else 0


# ── Phase 13: pipeline-driven round ───────────────────────────────────────────

def _run_pipeline_round(
    player_ship_id: str,
    entropy_unit: dict,
    round_num: int,
    escort_count: int,
    codebase_root: str,
    db_path: str,
) -> dict:
    """
    One round of capital duel via patch pipeline.
    Returns per-round dict with pipeline results and HP deltas.
    """
    from combat.patch_pipeline import run_patch_pipeline

    sector     = entropy_unit.get("assigned_sector", ".")
    issue      = entropy_unit.get("issue_description") or entropy_unit.get("description", "")
    issue_line = entropy_unit.get("issue_line", 0) or 0

    issues = []
    if issue:
        issues.append(f"Round {round_num} — {issue} (line {issue_line})")

    patch_result = run_patch_pipeline(
        ship_id=player_ship_id,
        sector_path=sector,
        issues=issues,
        codebase_root=codebase_root,
        db_path=db_path,
    )

    passed  = patch_result.tests_passed
    failed  = patch_result.tests_failed
    degraded = patch_result.degraded
    runner_missing = (
        "not available" in patch_result.test_output
        or "not found" in patch_result.test_output
        or patch_result.runner_used in ("none", "")
    )

    # Player damage to entropy
    if not patch_result.applied:
        player_dealt = 0
        label = "no_apply"
    elif degraded:
        player_dealt = 25
        label = "degraded"
    elif runner_missing:
        player_dealt = max(1, passed * 25)
        label = "runner_missing"
    elif passed > 0 and failed == 0:
        player_dealt = passed * 50
        label = "full_hit"
    elif passed > 0:
        player_dealt = passed * 50
        label = "partial"
    else:
        player_dealt = 0
        label = "reverted"

    # Entropy counter-fire: tests_failed × 30, reduced by escort point defense
    point_defense = escort_count * 10
    entropy_dealt = max(0, failed * 30 - point_defense)

    if degraded:
        entropy_dealt = max(0, entropy_dealt // 2)

    return {
        "round":             round_num,
        "pipeline_id":       patch_result.pipeline_id,
        "outcome_label":     label,
        "player_dealt":      player_dealt,
        "entropy_dealt":     entropy_dealt,
        "tests_passed":      passed,
        "tests_failed":      failed,
        "degraded":          degraded,
        "committed":         patch_result.committed,
        "runner_used":       patch_result.runner_used,
        "player_hp_after":   None,   # filled by caller
        "entropy_hp_after":  None,   # filled by caller
    }


# ── Post-battle artifact ───────────────────────────────────────────────────────

def _generate_refinement_artifact(
    player_ship: dict,
    entropy_unit: dict,
    outcome: str,
    rounds: list[dict],
) -> str:
    rounds_summary = "\n".join(
        f"Round {r['round']} [{r['outcome_label']}]: "
        f"player dealt {r['player_dealt']} / entropy dealt {r['entropy_dealt']} | "
        f"pipeline {r['pipeline_id']} | "
        f"{r['tests_passed']} passed, {r['tests_failed']} failed"
        for r in rounds
    )
    sector     = entropy_unit.get("assigned_sector", "unknown")
    issue      = entropy_unit.get("issue_description") or entropy_unit.get("description", "")
    issue_line = entropy_unit.get("issue_line", 0)
    sev        = entropy_unit.get("severity_score", 1)

    try:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=700,
            system=(
                "You are a post-battle analyst for a software fleet. "
                "Write a brief sector-refinement report. Include: threat confirmed, "
                "root cause, which pipeline rounds committed changes, and whether "
                "the sector is now safe."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Outcome: {outcome}\n"
                    f"Sector: {sector}\n"
                    f"Issue (line {issue_line}, severity {sev}): {issue}\n\n"
                    f"Combat log:\n{rounds_summary}\n\n"
                    "Write the sector refinement report."
                ),
            }],
        )
        return msg.content[0].text
    except Exception as exc:
        return (
            f"# Sector Refinement: {os.path.basename(sector)}\n\n"
            f"**Outcome:** {outcome}\n\n"
            f"**Issue:** {issue} (line {issue_line}, severity {sev})\n\n"
            f"**Combat log:**\n{rounds_summary}\n\n"
            f"*[LLM synthesis unavailable: {exc}]*"
        )


# ── Main duel ─────────────────────────────────────────────────────────────────

def run_capital_duel(
    player_ship_id: str,
    entropy_unit_id: str,
    db_path: str,
    codebase_root: str | None = None,
) -> dict:
    """
    Run a full capital-ship duel to completion.

    Each round runs run_patch_pipeline() — real diffs applied and tested.

    Returns:
        outcome           : "victory" | "defeat" | "mutual_destruction"
        rounds            : list of per-round dicts (pipeline results + HP)
        player_hp_final   : int
        entropy_hp_final  : int
        artifact_path     : str | None
        sector            : str
        detail            : str
    """
    if codebase_root is None:
        codebase_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    conn = _db(db_path)
    player  = _load_row("active_ships", "id", player_ship_id, conn)
    entropy = _load_row("entropy_fleet", "id", entropy_unit_id, conn)
    escort_count = _count_escort_ships(player_ship_id, conn) if player else 0
    conn.close()

    if not player:
        return {"outcome": "error", "detail": f"Player ship not found: {player_ship_id}"}
    if not entropy:
        return {"outcome": "error", "detail": f"Entropy unit not found: {entropy_unit_id}"}

    if player.get("class") != "destroyer":
        return {
            "outcome": "error",
            "detail":  f"Only destroyers may enter capital duels (got: {player.get('class')})",
        }
    if entropy.get("ship_class") != "entropy_destroyer":
        return {
            "outcome": "error",
            "detail":  f"Target must be entropy_destroyer (got: {entropy.get('ship_class')})",
        }

    player_hp  = player.get("context_budget") or 100
    entropy_hp = entropy.get("hp") or 50

    MAX_ROUNDS = 10     # pipeline rounds are expensive — cap lower than numeric sim
    rounds: list[dict] = []

    for round_num in range(1, MAX_ROUNDS + 1):
        round_data = _run_pipeline_round(
            player_ship_id=player_ship_id,
            entropy_unit=entropy,
            round_num=round_num,
            escort_count=escort_count,
            codebase_root=codebase_root,
            db_path=db_path,
        )

        player_hp  = max(0, player_hp  - round_data["entropy_dealt"])
        entropy_hp = max(0, entropy_hp - round_data["player_dealt"])

        round_data["player_hp_after"]  = player_hp
        round_data["entropy_hp_after"] = entropy_hp
        rounds.append(round_data)

        if player_hp <= 0 or entropy_hp <= 0:
            break

    # Determine outcome
    if player_hp <= 0 and entropy_hp <= 0:
        outcome = "mutual_destruction"
    elif entropy_hp <= 0:
        outcome = "victory"
    else:
        outcome = "defeat"

    sector = entropy.get("assigned_sector", "")

    # Persist
    conn = _db(db_path)
    if outcome in ("victory", "mutual_destruction"):
        conn.execute(
            "UPDATE entropy_fleet SET status = 'destroyed', hp = 0 WHERE id = ?",
            (entropy_unit_id,),
        )
    if outcome in ("mutual_destruction", "defeat"):
        conn.execute(
            "UPDATE active_ships SET status = 'destroyed' WHERE id = ?",
            (player_ship_id,),
        )
    conn.commit()
    conn.close()

    # Generate refinement artifact for cleared sectors
    artifact_path: str | None = None
    if outcome in ("victory", "mutual_destruction"):
        artifact_text  = _generate_refinement_artifact(player, entropy, outcome, rounds)
        postbattle_dir = os.path.join(
            os.path.dirname(db_path), "..", "docs", "architecture", "postbattle"
        )
        os.makedirs(postbattle_dir, exist_ok=True)
        slug          = _sector_slug(sector)
        artifact_path = os.path.join(postbattle_dir, f"{slug}.md")
        try:
            with open(artifact_path, "w", encoding="utf-8") as fh:
                fh.write(artifact_text)
        except OSError:
            artifact_path = None

        # Register in postbattle_registry
        conn = _db(db_path)
        conn.execute(
            """INSERT INTO postbattle_registry
               (sector, player_destroyer_id, refinement_artifact, notes)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(sector) DO UPDATE SET
                   cleared_at          = datetime('now'),
                   player_destroyer_id = excluded.player_destroyer_id,
                   refinement_artifact = excluded.refinement_artifact,
                   notes               = excluded.notes""",
            (
                sector,
                player_ship_id if outcome == "victory" else None,
                artifact_path,
                outcome,
            ),
        )
        conn.commit()
        conn.close()

    detail_map = {
        "victory":            f"Sector cleared after {len(rounds)} rounds. Patches committed.",
        "defeat":             f"Player destroyer lost after {len(rounds)} rounds. Entropy holds.",
        "mutual_destruction": f"Both destroyed after {len(rounds)} rounds. Sector refined — mutual cost.",
    }

    return {
        "outcome":          outcome,
        "rounds":           rounds,
        "player_hp_final":  player_hp,
        "entropy_hp_final": entropy_hp,
        "artifact_path":    artifact_path,
        "sector":           sector,
        "detail":           detail_map.get(outcome, outcome),
    }
