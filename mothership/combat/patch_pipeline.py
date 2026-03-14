"""
patch_pipeline.py — Phase 13 "Combat That Bleeds Into Code"

5-step pipeline: read → generate → apply → test → commit/revert

A single run_patch_pipeline() call:
  1. read_context()     — reads sector files into a context dict
  2. generate_patch()   — Sonnet produces a unified diff
  3. apply_patch()      — applies the diff (patch CLI or pure-Python fallback)
  4. run_tests()        — auto-detects pytest / jest / eslint / syntax
  5. commit or revert   — keeps changes if tests pass, reverts if they fail

Public API:
    run_patch_pipeline(ship_id, sector_path, issues, codebase_root, model) -> PatchResult
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path


# ── Result type ────────────────────────────────────────────────────────────────

@dataclass
class PatchResult:
    pipeline_id:  str
    sector_path:  str
    patch_text:   str  = ""
    applied:      bool = False
    tests_passed: int  = 0
    tests_failed: int  = 0
    test_output:  str  = ""
    committed:    bool = False
    reverted:     bool = False
    error:        str  = ""
    degraded:     bool = False   # True when patch caused a syntax error
    runner_used:  str  = ""


# ── Step 1: Read context ───────────────────────────────────────────────────────

def read_context(sector_path: str, max_chars: int = 12000) -> dict:
    """
    Read files in sector_path and return:
        {"files": {relpath: content}, "total_chars": int, "truncated": bool}
    """
    EXTS = {".py", ".c", ".h", ".js", ".ts", ".json", ".yaml", ".toml"}
    files: dict[str, str] = {}
    total = 0
    truncated = False

    base = Path(sector_path)
    if base.is_file():
        try:
            content = base.read_text(encoding="utf-8", errors="replace")
            if len(content) > max_chars:
                content = content[:max_chars]
                truncated = True
            files[base.name] = content
            total = len(content)
        except OSError:
            pass
        return {"files": files, "total_chars": total, "truncated": truncated}

    for root, dirs, fnames in os.walk(sector_path):
        dirs[:] = sorted(
            d for d in dirs
            if not d.startswith(".")
            and d not in ("__pycache__", "node_modules", "venv", ".venv")
        )
        for fname in sorted(fnames):
            ext = Path(fname).suffix.lower()
            if ext not in EXTS:
                continue
            fpath = Path(root) / fname
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = str(fpath.relative_to(sector_path))
            remaining = max_chars - total
            if remaining <= 0:
                truncated = True
                break
            if len(content) > remaining:
                content = content[:remaining]
                truncated = True
            files[rel] = content
            total += len(content)
        if total >= max_chars:
            truncated = True
            break

    return {"files": files, "total_chars": total, "truncated": truncated}


# ── Step 2: Generate patch ─────────────────────────────────────────────────────

def generate_patch(
    ship_id: str,
    context: dict,
    issues: list[str],
    model: str,
) -> str:
    """
    Call Claude with cockpit memory + sector context + issues.
    Returns a unified diff string.
    """
    import anthropic
    from memory.cockpit import load_cockpit

    cockpit = load_cockpit(ship_id)  # flat list[dict] LLM messages format

    files_block = "\n\n".join(
        f"=== {path} ===\n{content}"
        for path, content in context.get("files", {}).items()
    )
    issues_block = (
        "\n".join(f"- {i}" for i in issues)
        if issues else "General quality improvement."
    )

    user_prompt = (
        "You are a code repair system. Produce a SINGLE unified diff "
        "(standard `diff -u` format) that fixes the issues listed below.\n\n"
        f"ISSUES:\n{issues_block}\n\n"
        f"SOURCE FILES:\n{files_block}\n\n"
        "Output ONLY the unified diff. No explanation. No markdown fences. "
        "Start with `--- ` on the first line."
    )

    messages = list(cockpit) + [{"role": "user", "content": user_prompt}]

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=model,
        max_tokens=4096,
        system=(
            "You produce minimal, correct unified diffs for code repair. "
            "Always use `--- a/path` and `+++ b/path` headers relative to "
            "the codebase root. Include 3 lines of context around each change. "
            "Never include binary files."
        ),
        messages=messages,
    )
    raw = resp.content[0].text.strip()

    # Strip accidental markdown fences
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)

    return raw


# ── Step 3a: Pure-Python patch applier ────────────────────────────────────────

def _parse_diff(patch_text: str) -> list[dict]:
    """
    Parse a unified diff into:
        [{"path": str, "hunks": [{"old_start", "old_count",
                                   "new_start", "new_count", "lines"}]}]
    """
    file_patches: list[dict] = []
    current_file: dict | None = None
    current_hunk: dict | None = None

    for raw_line in patch_text.splitlines(keepends=True):
        line = raw_line.rstrip("\n")
        if line.startswith("--- "):
            current_file = {"path": None, "hunks": []}
            current_hunk = None
        elif line.startswith("+++ ") and current_file is not None:
            raw_path = line[4:].split("\t")[0]
            raw_path = re.sub(r"^[ab]/", "", raw_path)
            current_file["path"] = raw_path
            file_patches.append(current_file)
        elif line.startswith("@@ ") and current_file is not None:
            m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if m:
                current_hunk = {
                    "old_start": int(m.group(1)),
                    "old_count": int(m.group(2)) if m.group(2) is not None else 1,
                    "new_start": int(m.group(3)),
                    "new_count": int(m.group(4)) if m.group(4) is not None else 1,
                    "lines":     [],
                }
                current_file["hunks"].append(current_hunk)
        elif current_hunk is not None:
            if line.startswith((" ", "+", "-")):
                current_hunk["lines"].append(line)

    return file_patches


def _apply_hunk_to_lines(
    lines: list[str],
    hunk: dict,
    offset: int,
) -> tuple[list[str], int]:
    """
    Apply one hunk to a lines list (no trailing newlines).
    Returns (new_lines, new_offset) where new_offset is the cumulative delta.
    """
    old_start  = hunk["old_start"] - 1 + offset  # 0-indexed
    hunk_lines = hunk["lines"]

    before   = lines[:old_start]
    orig_pos = old_start
    new_chunk: list[str] = []

    for hl in hunk_lines:
        if hl.startswith(" "):
            new_chunk.append(lines[orig_pos] if orig_pos < len(lines) else hl[1:])
            orig_pos += 1
        elif hl.startswith("-"):
            orig_pos += 1
        elif hl.startswith("+"):
            new_chunk.append(hl[1:])

    after     = lines[orig_pos:]
    new_lines = before + new_chunk + after
    delta = (
        sum(1 for hl in hunk_lines if hl.startswith("+"))
        - sum(1 for hl in hunk_lines if hl.startswith("-"))
    )
    return new_lines, offset + delta


def _python_apply_patch(
    patch_text: str,
    codebase_root: str,
) -> tuple[bool, list[str], dict]:
    """
    Pure-Python unified diff applier.
    Returns (success, errors, originals).
    """
    originals: dict[str, str] = {}
    errors:    list[str]      = []
    root = Path(codebase_root)

    for fp in _parse_diff(patch_text):
        rel_path = fp.get("path")
        if not rel_path:
            errors.append("Patch entry has no file path")
            continue

        fpath = root / rel_path
        if fpath.exists():
            try:
                originals[str(fpath)] = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                errors.append(f"Cannot read {rel_path}: {exc}")
                continue
            lines = originals[str(fpath)].splitlines()
        else:
            originals[str(fpath)] = ""
            lines = []

        offset = 0
        for hunk in fp["hunks"]:
            try:
                lines, offset = _apply_hunk_to_lines(lines, hunk, offset)
            except Exception as exc:
                errors.append(f"Hunk failed in {rel_path}: {exc}")

        try:
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text("\n".join(lines), encoding="utf-8")
        except OSError as exc:
            errors.append(f"Cannot write {rel_path}: {exc}")

    return len(errors) == 0, errors, originals


# ── Step 3b: apply_patch (CLI + fallback) ─────────────────────────────────────

def apply_patch(
    patch_text: str,
    codebase_root: str,
) -> tuple[bool, list[str], dict]:
    """
    Apply a unified diff.  Tries `patch -p1` CLI first; falls back to
    the pure-Python applier.

    Returns (success, errors, originals) where originals maps
    absolute paths → original content (feed to revert_changes()).
    """
    if not patch_text.strip():
        return False, ["Empty patch"], {}

    # Snapshot originals before touching disk
    originals: dict[str, str] = {}
    for fp in _parse_diff(patch_text):
        if fp.get("path"):
            fpath = Path(codebase_root) / fp["path"]
            if fpath.exists():
                try:
                    originals[str(fpath)] = fpath.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    originals[str(fpath)] = ""

    patch_exe = shutil.which("patch")
    if patch_exe:
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".patch", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(patch_text)
                tmp_path = tmp.name

            # Dry-run first
            dry = subprocess.run(
                [patch_exe, "-p1", "--dry-run", "--input", tmp_path],
                cwd=codebase_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if dry.returncode != 0:
                os.unlink(tmp_path)
                return False, [f"patch dry-run: {dry.stderr.strip()}"], originals

            # Real apply
            result = subprocess.run(
                [patch_exe, "-p1", "--input", tmp_path],
                cwd=codebase_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            os.unlink(tmp_path)
            if result.returncode == 0:
                return True, [], originals
            return False, [f"patch apply: {result.stderr.strip()}"], originals

        except (subprocess.TimeoutExpired, OSError):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            # fall through to Python fallback

    return _python_apply_patch(patch_text, codebase_root)


# ── Step 4: Run tests ──────────────────────────────────────────────────────────

def _load_runner_config(codebase_root: str) -> dict:
    """Load state/test_runner.json override if present."""
    state_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "state",
    )
    cfg_path = os.path.join(state_dir, "test_runner.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def _detect_runner(sector_path: str, codebase_root: str) -> str:
    """
    Auto-detect test runner.
    Returns: "pytest" | "jest" | "eslint" | "syntax" | "none"
    """
    root = Path(codebase_root)

    if any((root / f).exists() for f in
           ("pytest.ini", "setup.cfg", "pyproject.toml", "tox.ini")):
        return "pytest"

    pkg = root / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            if "jest" in data.get("devDependencies", {}):
                return "jest"
            if "jest" in data.get("scripts", {}).get("test", ""):
                return "jest"
        except (OSError, json.JSONDecodeError):
            pass

    if any((root / f).exists() for f in
           (".eslintrc", ".eslintrc.js", ".eslintrc.json", ".eslintrc.yaml")):
        return "eslint"

    sp = Path(sector_path)
    if sp.suffix.lower() == ".py" or (sp.is_dir() and list(sp.rglob("*.py"))):
        return "syntax"

    return "none"


def _run_syntax_check(sector_path: str) -> tuple[int, int, str]:
    """Python syntax check on .py files in sector."""
    sp = Path(sector_path)
    py_files = [sp] if (sp.is_file() and sp.suffix == ".py") else list(sp.rglob("*.py"))

    passed = 0
    failed = 0
    lines:  list[str] = []

    for pyf in py_files[:20]:
        try:
            src = pyf.read_text(encoding="utf-8", errors="replace")
            ast.parse(src, filename=str(pyf))
            passed += 1
            lines.append(f"OK: {pyf.name}")
        except SyntaxError as exc:
            failed += 1
            lines.append(f"SYNTAX ERROR: {pyf.name}:{exc.lineno}: {exc.msg}")
        except OSError as exc:
            failed += 1
            lines.append(f"READ ERROR: {pyf.name}: {exc}")

    return passed, failed, "\n".join(lines)


def run_tests(sector_path: str, codebase_root: str) -> tuple[int, int, str]:
    """
    Auto-detect and run tests.  Returns (passed, failed, output).
    """
    cfg     = _load_runner_config(codebase_root)
    runner  = cfg.get("runner") or _detect_runner(sector_path, codebase_root)
    paths   = cfg.get("test_paths", [])
    timeout = cfg.get("timeout_seconds", 60)
    env     = {**os.environ, **cfg.get("env", {})}

    if runner == "pytest":
        cmd = [sys.executable, "-m", "pytest", "--tb=short", "-q"] + (paths or ["."])
        try:
            r   = subprocess.run(cmd, cwd=codebase_root, capture_output=True,
                                 text=True, timeout=timeout, env=env)
            out = r.stdout + r.stderr
            m_p = re.search(r"(\d+) passed", out)
            m_f = re.search(r"(\d+) failed", out)
            return (
                int(m_p.group(1)) if m_p else 0,
                int(m_f.group(1)) if m_f else (0 if r.returncode == 0 else 1),
                out[-3000:],
            )
        except subprocess.TimeoutExpired:
            return 0, 1, f"pytest timed out after {timeout}s"
        except FileNotFoundError:
            return 0, 0, "pytest not available — runner_used=none"

    elif runner == "jest":
        cmd = ["npx", "jest", "--passWithNoTests", "--no-coverage"] + paths
        try:
            r   = subprocess.run(cmd, cwd=codebase_root, capture_output=True,
                                 text=True, timeout=timeout, env=env)
            out = r.stdout + r.stderr
            m   = re.search(r"Tests:\s+(?:(\d+) passed)?(?:,\s*)?(?:(\d+) failed)?", out)
            return (
                int(m.group(1)) if m and m.group(1) else 0,
                int(m.group(2)) if m and m.group(2) else (0 if r.returncode == 0 else 1),
                out[-3000:],
            )
        except subprocess.TimeoutExpired:
            return 0, 1, f"jest timed out after {timeout}s"
        except FileNotFoundError:
            return 0, 0, "jest not available — runner_used=none"

    elif runner == "eslint":
        target = paths or [str(Path(sector_path))]
        cmd    = ["npx", "eslint", "--format", "compact"] + target
        try:
            r   = subprocess.run(cmd, cwd=codebase_root, capture_output=True,
                                 text=True, timeout=timeout, env=env)
            out = r.stdout + r.stderr
            errors = out.count(": error ")
            return max(0, 1 - errors), errors, out[-3000:]
        except subprocess.TimeoutExpired:
            return 0, 1, f"eslint timed out after {timeout}s"
        except FileNotFoundError:
            return 0, 0, "eslint not available — runner_used=none"

    elif runner == "syntax":
        return _run_syntax_check(sector_path)

    return 0, 0, "no test runner detected"


# ── Step 5: Revert ─────────────────────────────────────────────────────────────

def revert_changes(originals: dict) -> None:
    """Restore files from the originals snapshot produced by apply_patch()."""
    for abs_path, content in originals.items():
        try:
            p = Path(abs_path)
            if content == "":
                if p.exists():
                    p.unlink()
            else:
                p.write_text(content, encoding="utf-8")
        except OSError:
            pass


# ── Audit helper ───────────────────────────────────────────────────────────────

def _insert_audit(
    db_path: str,
    result: PatchResult,
    ship_id: str,
    sector_path: str,
    issues: list[str],
) -> None:
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            INSERT INTO patch_audit
                (pipeline_id, ship_id, sector_path, issues_json,
                 patch_text, applied, tests_passed, tests_failed,
                 test_output, committed, reverted, degraded, runner_used)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            result.pipeline_id,
            ship_id,
            sector_path,
            json.dumps(issues),
            result.patch_text,
            int(result.applied),
            result.tests_passed,
            result.tests_failed,
            result.test_output[:4000],
            int(result.committed),
            int(result.reverted),
            int(result.degraded),
            result.runner_used,
        ))
        conn.commit()
        conn.close()
    except Exception:
        pass  # audit is best-effort


def _mark_degraded(ship_id: str, db_path: str) -> None:
    """Add 'degraded' tag to active_ships.specialization_vector."""
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        row = conn.execute(
            "SELECT specialization_vector FROM active_ships WHERE id = ?",
            (ship_id,)
        ).fetchone()
        if row:
            vec = json.loads(row[0] or "[]")
            if "degraded" not in vec:
                vec.append("degraded")
                conn.execute(
                    "UPDATE active_ships SET specialization_vector = ? WHERE id = ?",
                    (json.dumps(vec), ship_id)
                )
                conn.commit()
        conn.close()
    except Exception:
        pass


# ── Orchestrator ───────────────────────────────────────────────────────────────

def run_patch_pipeline(
    ship_id: str,
    sector_path: str,
    issues: list[str],
    codebase_root: str,
    model: str | None = None,
    db_path: str | None = None,
) -> PatchResult:
    """
    Run the full 5-step patch pipeline.

    Parameters
    ----------
    ship_id       : fleet ship ID (for cockpit memory)
    sector_path   : file or directory being patched
    issues        : list of issue strings to fix
    codebase_root : root for patch -p1 application
    model         : LLM model (default: MOTHERSHIP_MODEL env or claude-sonnet-4-6)
    db_path       : homeworld.db path for audit log (optional)

    Returns
    -------
    PatchResult with all fields set.
    """
    model = model or os.environ.get("MOTHERSHIP_MODEL", "claude-sonnet-4-6")

    _default_db = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "state", "homeworld.db",
    )
    db_path = db_path or _default_db

    result = PatchResult(
        pipeline_id=str(uuid.uuid4())[:8],
        sector_path=sector_path,
    )

    # Step 1 — read context
    try:
        context = read_context(sector_path)
    except Exception as exc:
        result.error = f"read_context: {exc}"
        _insert_audit(db_path, result, ship_id, sector_path, issues)
        return result

    if not context["files"]:
        result.error = "No readable source files in sector"
        _insert_audit(db_path, result, ship_id, sector_path, issues)
        return result

    # Step 2 — generate patch
    try:
        patch_text      = generate_patch(ship_id, context, issues, model)
        result.patch_text = patch_text
    except Exception as exc:
        result.error = f"generate_patch: {exc}"
        _insert_audit(db_path, result, ship_id, sector_path, issues)
        return result

    if not patch_text.strip().startswith("---"):
        result.error = "LLM did not produce a valid unified diff (no '--- ' header)"
        _insert_audit(db_path, result, ship_id, sector_path, issues)
        return result

    # Step 3 — apply patch
    originals: dict = {}
    try:
        applied, apply_errors, originals = apply_patch(patch_text, codebase_root)
        result.applied = applied
        if not applied:
            result.error = "; ".join(apply_errors)
            _insert_audit(db_path, result, ship_id, sector_path, issues)
            return result
    except Exception as exc:
        result.error = f"apply_patch: {exc}"
        _insert_audit(db_path, result, ship_id, sector_path, issues)
        return result

    # Step 4 — run tests
    runner = (
        _load_runner_config(codebase_root).get("runner")
        or _detect_runner(sector_path, codebase_root)
    )
    result.runner_used = runner

    try:
        passed, failed, test_out  = run_tests(sector_path, codebase_root)
        result.tests_passed       = passed
        result.tests_failed       = failed
        result.test_output        = test_out
    except Exception as exc:
        result.tests_failed = 1
        result.test_output  = f"test runner error: {exc}"

    if "SYNTAX ERROR" in result.test_output or "SyntaxError" in result.test_output:
        result.degraded = True

    # Step 5 — commit or revert
    if result.tests_failed == 0 and result.tests_passed > 0:
        result.committed = True
        try:
            from memory.cockpit import append_cockpit
            append_cockpit(
                ship_id, "assistant",
                f"[PATCH COMMITTED pipeline={result.pipeline_id}] "
                f"sector={sector_path} | "
                f"{result.tests_passed} tests passed | runner={runner}",
            )
        except Exception:
            pass
    else:
        try:
            revert_changes(originals)
            result.reverted = True
        except Exception as exc:
            result.error = f"revert: {exc}"

        if result.degraded:
            _mark_degraded(ship_id, db_path)

        try:
            from memory.cockpit import append_cockpit
            status = "DEGRADED" if result.degraded else "REVERTED"
            append_cockpit(
                ship_id, "assistant",
                f"[PATCH {status} pipeline={result.pipeline_id}] "
                f"sector={sector_path} | "
                f"{result.tests_passed} passed, {result.tests_failed} failed | "
                f"runner={runner}",
            )
        except Exception:
            pass

    _insert_audit(db_path, result, ship_id, sector_path, issues)
    return result
