"""
swarm_command.py — Swarm delegation protocol (Phase 11 / Target D, Phase 13 rewrite).

Engages an entropy unit using a swarm of interceptors working in parallel,
then synthesizes their results via a frigate-class coordinator that runs the
full patch pipeline.

Phase 13 change: interceptors now dispatch typed tasks (map_paths, git_blame,
syntax_check, draft_fix); the frigate synthesis drives run_patch_pipeline();
combat outcome is determined by test results.

Public API:
    engage_enemy_swarm(entropy_unit_id, player_ship_ids, db_path) -> dict
"""
import ast
import json
import os
import sqlite3
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path


# ── Helpers ────────────────────────────────────────────────────────────────────

def _db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


def _load_entropy_unit(unit_id: str, conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT * FROM entropy_fleet WHERE id = ?", (unit_id,)
    ).fetchone()
    return dict(row) if row else None


def _load_ship(ship_id: str, conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT * FROM active_ships WHERE id = ?", (ship_id,)
    ).fetchone()
    return dict(row) if row else None


# ── Typed interceptor tasks ────────────────────────────────────────────────────

def _task_map_paths(sector: str) -> dict:
    """Walk sector and return file inventory with AST counts."""
    EXTS = {".py", ".c", ".h", ".js", ".ts"}
    results: list[str] = []
    base = Path(sector)

    if base.is_file():
        results.append(str(base))
        return {"type": "map_paths", "output": "\n".join(results), "issues": []}

    for root, dirs, files in os.walk(sector):
        dirs[:] = sorted(
            d for d in dirs
            if not d.startswith(".")
            and d not in ("__pycache__", "node_modules", "venv", ".venv")
        )[:4]
        for fname in sorted(files)[:8]:
            fpath = Path(root) / fname
            ext   = fpath.suffix.lower()
            if ext not in EXTS:
                continue
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
                if ext == ".py":
                    tree    = ast.parse(content)
                    funcs   = sum(1 for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
                    classes = sum(1 for n in ast.walk(tree) if isinstance(n, ast.ClassDef))
                    results.append(f"{fpath}: {funcs} funcs, {classes} classes")
                else:
                    results.append(f"{fpath}: {content.count(chr(10))} lines")
            except (OSError, SyntaxError):
                results.append(f"{fpath}: [unreadable]")

    return {"type": "map_paths", "output": "\n".join(results) or "empty sector", "issues": []}


def _task_git_blame(sector: str) -> dict:
    """Run git log --oneline on the sector path for recent history."""
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "-10", "--", sector],
            capture_output=True, text=True, timeout=10,
        )
        output = result.stdout.strip() or "no git history"
        issues = []
        if "TODO" in output or "FIXME" in output or "HACK" in output:
            issues.append(f"Commit messages suggest known tech debt in {sector}")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        output = "git not available"
        issues = []

    return {"type": "git_blame", "output": output, "issues": issues}


def _task_syntax_check(sector: str) -> dict:
    """AST-parse all .py files in sector, collect syntax issues."""
    sp = Path(sector)
    py_files = [sp] if (sp.is_file() and sp.suffix == ".py") else list(sp.rglob("*.py"))

    issues:  list[str] = []
    output_lines: list[str] = []

    for pyf in py_files[:15]:
        try:
            src = pyf.read_text(encoding="utf-8", errors="replace")
            ast.parse(src, filename=str(pyf))
            output_lines.append(f"OK: {pyf.name}")
        except SyntaxError as exc:
            issues.append(f"SyntaxError in {pyf.name} line {exc.lineno}: {exc.msg}")
            output_lines.append(f"ERR: {pyf.name}:{exc.lineno}: {exc.msg}")
        except OSError as exc:
            output_lines.append(f"UNREADABLE: {pyf.name}: {exc}")

    return {
        "type":   "syntax_check",
        "output": "\n".join(output_lines) or "no Python files",
        "issues": issues,
    }


def _task_draft_fix(sector: str, issue_description: str, issue_line: int) -> dict:
    """Use Haiku to draft a concise fix suggestion for the identified issue."""
    try:
        import anthropic
        client = anthropic.Anthropic()

        # Read a snippet of the sector for context
        snippet = ""
        sp = Path(sector)
        if sp.is_file():
            try:
                lines = sp.read_text(encoding="utf-8", errors="replace").splitlines()
                start = max(0, issue_line - 5)
                end   = min(len(lines), issue_line + 10)
                snippet = "\n".join(f"{i+1}: {l}" for i, l in enumerate(lines[start:end], start))
            except OSError:
                pass

        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=(
                "You are an interceptor probe. Describe ONE specific code fix in "
                "1-2 sentences. Be concrete: name the function, variable, or pattern "
                "to change. No explanation. Just the fix."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"File: {sector}\n"
                    f"Line: {issue_line}\n"
                    f"Issue: {issue_description}\n\n"
                    f"Code context:\n{snippet}\n\n"
                    "One-sentence fix:"
                ),
            }],
        )
        fix_text = msg.content[0].text
        return {
            "type":   "draft_fix",
            "output": fix_text,
            "issues": [issue_description] if issue_description else [],
        }
    except Exception as exc:
        return {
            "type":   "draft_fix",
            "output": f"[draft_fix offline: {exc}]",
            "issues": [issue_description] if issue_description else [],
        }


def _interceptor_task(ship: dict, entropy_unit: dict) -> dict:
    """
    Dispatch a typed task based on ship specialization.
    Ships with 'scout'/'map' tags → map_paths
    Ships with 'research'/'blame' tags → git_blame
    Ships with 'interceptor'/'syntax' tags → syntax_check
    Default → draft_fix
    """
    sector     = entropy_unit.get("assigned_sector", ".")
    issue      = entropy_unit.get("issue_description") or entropy_unit.get("description", "")
    issue_line = entropy_unit.get("issue_line", 0) or 0
    spec_vec   = json.loads(ship.get("specialization_vector") or "[]")

    if any(t in spec_vec for t in ("scout", "map", "map_paths")):
        result = _task_map_paths(sector)
    elif any(t in spec_vec for t in ("research", "blame", "git")):
        result = _task_git_blame(sector)
    elif any(t in spec_vec for t in ("interceptor", "syntax", "syntax_check")):
        result = _task_syntax_check(sector)
    else:
        result = _task_draft_fix(sector, issue, issue_line)

    result["ship_id"] = ship.get("id", "unknown")
    return result


# ── Frigate synthesis + pipeline ──────────────────────────────────────────────

def _frigate_synthesize_and_patch(
    entropy_unit: dict,
    probe_results: list[dict],
    frigate_ship_id: str,
    codebase_root: str,
    db_path: str,
) -> tuple[str, "PatchResult"]:
    """
    Frigate coordinator:
      1. Synthesizes interceptor probes into an issue list.
      2. Runs run_patch_pipeline() against the sector.
      3. Returns (synthesis_text, PatchResult).
    """
    from combat.patch_pipeline import run_patch_pipeline, PatchResult

    sector = entropy_unit.get("assigned_sector", ".")

    # Collect all issues identified by interceptors
    all_issues: list[str] = []
    for r in probe_results:
        all_issues.extend(r.get("issues", []))

    # Add the entropy unit's own issue
    unit_issue = entropy_unit.get("issue_description") or entropy_unit.get("description", "")
    if unit_issue and unit_issue not in all_issues:
        all_issues.insert(0, unit_issue)

    probe_summaries = "\n\n".join(
        f"[{r['type']}] Ship {r['ship_id']}:\n{r['output']}"
        for r in probe_results
    )

    # Frigate synthesis text
    try:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            system=(
                "You are a frigate coordinator synthesizing interceptor probe reports "
                "into a tactical summary. Include: threat confirmed, consolidated issue "
                "list, recommended patch strategy."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Target sector: {sector}\n"
                    f"Entropy unit class: {entropy_unit.get('ship_class','?')}\n\n"
                    f"Interceptor reports:\n{probe_summaries}"
                ),
            }],
        )
        synthesis = msg.content[0].text
    except Exception as exc:
        synthesis = (
            f"[synthesis unavailable: {exc}]\n\n{probe_summaries[:600]}"
        )

    # Run patch pipeline
    patch_result = run_patch_pipeline(
        ship_id=frigate_ship_id,
        sector_path=sector,
        issues=all_issues[:10],          # cap at 10 issues per run
        codebase_root=codebase_root,
        db_path=db_path,
    )

    return synthesis, patch_result


# ── Combat outcome ─────────────────────────────────────────────────────────────

def _compute_damage(patch_result: "PatchResult") -> tuple[int, str]:
    """
    Translate pipeline result into (damage_to_entropy, outcome_label).

    Outcome table (from Phase 13 spec):
      all pass          → full kill  (hp to 0)
      partial           → tests_passed × 50 damage
      all fail/reverted → 0 damage
      syntax error      → degraded, 25 damage (half effectiveness)
      runner not found  → 50% effectiveness (tests_passed × 25)
    """
    passed = patch_result.tests_passed
    failed = patch_result.tests_failed

    if not patch_result.applied:
        return 0, "no_apply"

    if patch_result.degraded:
        return 25, "degraded"

    runner_missing = "not available" in patch_result.test_output or \
                     "not found" in patch_result.test_output or \
                     patch_result.runner_used == "none"
    if runner_missing:
        return max(1, passed * 25), "runner_missing"

    if passed > 0 and failed == 0:
        return 999, "full_kill"        # will be capped to entropy HP

    if passed > 0:
        return passed * 50, "partial"

    return 0, "reverted"


# ── Main engagement ────────────────────────────────────────────────────────────

def engage_enemy_swarm(
    entropy_unit_id: str,
    player_ship_ids: list[str],
    db_path: str,
    codebase_root: str | None = None,
) -> dict:
    """
    Engage an entropy unit with a swarm of interceptors.

    Parameters
    ----------
    entropy_unit_id : ID of the entropy_fleet row to engage.
    player_ship_ids : IDs of player interceptors + one frigate coordinator.
    db_path         : Path to homeworld.db.
    codebase_root   : Project root for patch application (default: project root).

    Returns
    -------
    dict with keys:
        status, entropy_unit, total_damage, remaining_hp, destroyed,
        synthesis, probe_results, patch_result (PatchResult as dict)
    """
    if codebase_root is None:
        codebase_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    conn = _db(db_path)
    unit = _load_entropy_unit(entropy_unit_id, conn)
    if not unit:
        conn.close()
        return {"status": "error", "detail": f"No entropy unit: {entropy_unit_id}"}

    ships = [s for s in (_load_ship(sid, conn) for sid in player_ship_ids) if s]
    conn.close()

    if not ships:
        return {
            "status":        "no_ships",
            "entropy_unit":  unit,
            "total_damage":  0,
            "remaining_hp":  unit["hp"],
            "destroyed":     False,
            "synthesis":     "No interceptors available.",
            "probe_results": [],
            "patch_result":  None,
        }

    # Split: last ship in list is the frigate coordinator (if multiple ships)
    if len(ships) > 1:
        interceptors = ships[:-1]
        frigate      = ships[-1]
    else:
        interceptors = ships
        frigate      = ships[0]

    # Run typed interceptor probes concurrently
    probe_results: list[dict] = []
    max_workers = min(len(interceptors), 5)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_interceptor_task, ship, unit): ship
            for ship in interceptors
        }
        for future in as_completed(futures):
            try:
                probe_results.append(future.result())
            except Exception as exc:
                probe_results.append({
                    "type":    "error",
                    "ship_id": futures[future].get("id", "?"),
                    "output":  f"[probe failed: {exc}]",
                    "issues":  [],
                })

    # Frigate synthesizes + runs pipeline
    synthesis, patch_result = _frigate_synthesize_and_patch(
        entropy_unit=unit,
        probe_results=probe_results,
        frigate_ship_id=frigate.get("id", "unknown"),
        codebase_root=codebase_root,
        db_path=db_path,
    )

    total_damage, outcome_label = _compute_damage(patch_result)
    remaining_hp = max(0, unit["hp"] - total_damage)
    destroyed    = remaining_hp == 0

    # Persist HP change
    conn = _db(db_path)
    if destroyed:
        conn.execute(
            "UPDATE entropy_fleet SET status = 'destroyed', hp = 0 WHERE id = ?",
            (entropy_unit_id,),
        )
    else:
        conn.execute(
            "UPDATE entropy_fleet SET hp = ? WHERE id = ?",
            (remaining_hp, entropy_unit_id),
        )
    conn.commit()
    conn.close()

    return {
        "status":        "victory" if destroyed else "partial",
        "outcome_label": outcome_label,
        "entropy_unit":  unit,
        "total_damage":  total_damage,
        "remaining_hp":  remaining_hp,
        "destroyed":     destroyed,
        "synthesis":     synthesis,
        "probe_results": probe_results,
        "patch_result": {
            "pipeline_id":  patch_result.pipeline_id,
            "applied":      patch_result.applied,
            "committed":    patch_result.committed,
            "reverted":     patch_result.reverted,
            "degraded":     patch_result.degraded,
            "tests_passed": patch_result.tests_passed,
            "tests_failed": patch_result.tests_failed,
            "runner_used":  patch_result.runner_used,
            "error":        patch_result.error,
        },
    }
