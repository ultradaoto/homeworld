"""
entropy_engine.py — Entropy System scanner (Phase 11 / Target C).

Scans the codebase for vulnerabilities / code-quality issues and spawns
Entropy fleet units in the entropy_fleet table.  Run as a background service
or via: python entropy_engine.py

Severity → ship class mapping (per spec v1.1):
  1–2  entropy_interceptor   HP: severity * 10
  3–4  entropy_frigate       HP: severity * 10
  5+   entropy_destroyer     HP: severity * 10
"""
import json
import os
import sqlite3
import subprocess
import time
import uuid as _uuid

DB_PATH = os.environ.get(
    "SQLITE_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "homeworld.db"),
)
CODEBASE_PATH = os.environ.get("CODEBASE_PATH", "D:/Projects/Homeworld")
SCAN_INTERVAL = 45  # seconds

# bandit severity strings → numeric score
_BANDIT_SEV = {"LOW": 1, "MEDIUM": 3, "HIGH": 5}
# confidence boosts score slightly
_BANDIT_CONF = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


def _severity_to_class(score: int) -> str:
    if score >= 5:
        return "entropy_destroyer"
    if score >= 3:
        return "entropy_frigate"
    return "entropy_interceptor"


# ── Database ───────────────────────────────────────────────────────────────────

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


def _already_occupied(filepath: str, conn: sqlite3.Connection) -> bool:
    return bool(conn.execute(
        "SELECT id FROM entropy_fleet WHERE assigned_sector = ? AND status = 'active'",
        (filepath,),
    ).fetchone())


def _spawn_unit(ship_class: str, sector: str, hp: int,
                score: int, description: str, line: int) -> str | None:
    with _db() as conn:
        if _already_occupied(sector, conn):
            return None
        unit_id = f"ENT-{_uuid.uuid4().hex[:6].upper()}"
        conn.execute(
            """INSERT INTO entropy_fleet
               (id, ship_class, assigned_sector, hp, occupation_start, status,
                issue_description, issue_line, severity_score)
               VALUES (?, ?, ?, ?, datetime('now'), 'active', ?, ?, ?)""",
            (unit_id, ship_class, sector, hp,
             description[:300], line, score),
        )
        conn.commit()
        return unit_id


# ── Scanners ──────────────────────────────────────────────────────────────────

def _run_bandit(path: str) -> list[dict]:
    """
    Run bandit -r <path> -f json.
    bandit exits 0 (no issues) or 1 (issues found) — both are valid.
    Any other return code means bandit is not installed / failed.
    """
    issues: list[dict] = []
    try:
        r = subprocess.run(
            ["bandit", "-r", path, "-f", "json", "-q", "--exit-zero"],
            capture_output=True, text=True, timeout=60,
        )
        data = json.loads(r.stdout or "{}")
        for item in data.get("results", []):
            base_sev  = _BANDIT_SEV.get(item.get("issue_severity", "LOW"), 1)
            conf_bump = _BANDIT_CONF.get(item.get("issue_confidence", "LOW"), 0)
            score     = min(base_sev + conf_bump, 7)
            issues.append({
                "filepath":    item.get("filename", ""),
                "score":       score,
                "description": item.get("issue_text", ""),
                "line":        item.get("line_number", 0),
            })
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    except FileNotFoundError:
        pass  # bandit not installed — caller falls back
    return issues


def _run_pylint(path: str) -> list[dict]:
    """pylint --output-format=json — exits non-zero when issues found (that's fine)."""
    issues: list[dict] = []
    try:
        r = subprocess.run(
            ["pylint", path, "--output-format=json",
             "--disable=all", "--enable=E,W", "--recursive=y"],
            capture_output=True, text=True, timeout=60,
        )
        data = json.loads(r.stdout or "[]")
        for item in data:
            score = 5 if item.get("type") == "error" else 2
            issues.append({
                "filepath":    item.get("path", ""),
                "score":       score,
                "description": item.get("message", ""),
                "line":        item.get("line", 0),
            })
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass
    return issues


def _heuristic_fallback(path: str) -> list[dict]:
    """
    Lightweight grep-based heuristic used when bandit/pylint are unavailable.
    Returns one issue per file — avoids false-positive floods.
    """
    PATTERNS = [
        ("eval(",            5, "use of eval()"),
        ("exec(",            5, "use of exec()"),
        ("os.system(",       3, "use of os.system()"),
        ("subprocess.call(", 2, "unchecked subprocess.call()"),
        ("password",         3, "possible hardcoded credential"),
        ("secret",           3, "possible hardcoded secret"),
        ("TODO",             1, "unresolved TODO"),
        ("FIXME",            2, "FIXME marker"),
    ]
    issues: list[dict] = []
    for dirpath, _, filenames in os.walk(path):
        for fname in filenames:
            if not fname.endswith((".py", ".js", ".ts")):
                continue
            fpath = os.path.join(dirpath, fname)
            try:
                text = open(fpath, encoding="utf-8", errors="replace").read()
                for pat, score, desc in PATTERNS:
                    if pat.lower() in text.lower():
                        issues.append({
                            "filepath": fpath,
                            "score":    score,
                            "description": desc,
                            "line":    next(
                                (i + 1 for i, line in enumerate(text.splitlines())
                                 if pat.lower() in line.lower()), 0),
                        })
                        break  # one issue per file
            except OSError:
                pass
    return issues


# ── Threat level update ───────────────────────────────────────────────────────

def _update_threat_level(filepath: str, score: int) -> None:
    """Increment asteroids_map.threat_level for the occupied file."""
    with _db() as conn:
        conn.execute(
            """INSERT INTO asteroids_map (id, filepath, threat_level)
               VALUES (lower(hex(randomblob(8))), ?, ?)
               ON CONFLICT(filepath) DO UPDATE SET
                   threat_level = MAX(excluded.threat_level, threat_level)""",
            (filepath, score),
        )
        conn.commit()


# ── Main loop ─────────────────────────────────────────────────────────────────

def entropy_scan_loop() -> None:
    print("[entropy_engine] Starting entropy scan loop...")
    while True:
        try:
            issues = _run_bandit(CODEBASE_PATH)
            if not issues:
                # Bandit unavailable or returned nothing — try pylint
                issues = _run_pylint(CODEBASE_PATH)
            if not issues:
                issues = _heuristic_fallback(CODEBASE_PATH)

            spawned = 0
            for issue in issues:
                fp = issue.get("filepath", "")
                if not fp:
                    continue
                score      = issue["score"]
                ship_class = _severity_to_class(score)
                hp         = score * 10
                unit = _spawn_unit(
                    ship_class=ship_class,
                    sector=fp,
                    hp=hp,
                    score=score,
                    description=issue.get("description", ""),
                    line=issue.get("line", 0),
                )
                if unit:
                    spawned += 1
                    _update_threat_level(fp, score)
                    print(f"[entropy_engine] {unit} ({ship_class} HP:{hp}) "
                          f"-> {fp[-55:]}")

            print(f"[entropy_engine] Scan: {len(issues)} issues, "
                  f"{spawned} new units spawned")
        except Exception as exc:
            print(f"[entropy_engine] Error: {exc}")

        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    entropy_scan_loop()
