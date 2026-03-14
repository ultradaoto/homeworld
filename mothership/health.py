"""
Fleet Health Check — run standalone to diagnose issues.
    python health.py
"""
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))


def _check(label: str, ok: bool, detail: str = "") -> bool:
    icon  = "✓" if ok else "✗"
    color = "\033[32m" if ok else "\033[31m"
    reset = "\033[0m"
    line  = f"  {color}[{icon}]{reset} {label}"
    if detail:
        line += f"  {detail}"
    print(line)
    return ok


def run_checks() -> bool:
    print("\n=== HOMEWORLD MOTHERSHIP HEALTH CHECK ===\n")
    all_ok = True

    # ── Environment ───────────────────────────────────────────────────────────
    print("ENVIRONMENT")
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_HERE, ".env"))
    except ImportError:
        pass

    key = os.environ.get("ANTHROPIC_API_KEY", "")
    all_ok &= _check(
        ".env loaded",
        key.startswith("sk-"),
        "missing!" if not key else f"sk-...{key[-4:]}",
    )

    game_exe = r"D:\Projects\Homeworld\HomeworldSDL\build\homeworld.exe"
    all_ok &= _check("Game binary", os.path.exists(game_exe))

    assets_big = r"D:\Projects\Homeworld\assets\homeworld.big"
    all_ok &= _check("homeworld.big", os.path.exists(assets_big))

    docker = subprocess.run(
        ["docker", "info"], capture_output=True
    ).returncode == 0
    _check("Docker daemon", docker, "(optional)")   # don't affect all_ok

    # ── SQLite: fleet.db ─────────────────────────────────────────────────────
    print("\nSQLITE — fleet.db")
    fleet_db = os.path.join(_HERE, "state", "fleet.db")
    if os.path.exists(fleet_db):
        try:
            con = sqlite3.connect(fleet_db)
            tables = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            required = [
                "fleet_state", "active_ships", "asteroids_map",
                "escape_pods", "enemy_fleet", "ci_results",
            ]
            for t in required:
                all_ok &= _check(f"  table: {t}", t in tables)

            ships   = con.execute("SELECT COUNT(*) FROM active_ships WHERE status!='lost'").fetchone()[0]
            enemies = con.execute("SELECT COUNT(*) FROM enemy_fleet WHERE status='inbound'").fetchone()[0]
            pods    = con.execute("SELECT COUNT(*) FROM escape_pods WHERE recovered=0").fetchone()[0]
            sectors = con.execute("SELECT COUNT(*) FROM asteroids_map").fetchone()[0]
            con.close()
            print(f"  → {ships} active ships, {enemies} enemies, "
                  f"{pods} escape pods, {sectors} sectors")
        except sqlite3.Error as exc:
            all_ok &= _check("  fleet.db readable", False, str(exc))
    else:
        all_ok &= _check("fleet.db exists", False,
                         "run: python init_db.py  (or python start.py)")

    # ── SQLite: homeworld.db ─────────────────────────────────────────────────
    print("\nSQLITE — homeworld.db  (Phase 11)")
    hw_db = os.path.join(_HERE, "state", "homeworld.db")
    if os.path.exists(hw_db):
        try:
            con = sqlite3.connect(hw_db)
            tables = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            phase11_tables = [
                "active_ships", "entropy_fleet", "tech_tree",
                "tasks", "postbattle_registry",
            ]
            for t in phase11_tables:
                all_ok &= _check(f"  table: {t}", t in tables)

            entropy = con.execute(
                "SELECT COUNT(*) FROM entropy_fleet WHERE status='active'"
            ).fetchone()[0]
            tech_done = con.execute(
                "SELECT COUNT(*) FROM tech_tree WHERE status='completed'"
            ).fetchone()[0]
            con.close()
            print(f"  → {entropy} active entropy units, {tech_done} tech nodes completed")
        except sqlite3.Error as exc:
            all_ok &= _check("  homeworld.db readable", False, str(exc))
    else:
        _check("homeworld.db exists", False,
               "run: python init_db.py  (creates on first boot)")

    # ── Bridge state files ────────────────────────────────────────────────────
    print("\nBRIDGE STATE FILES")
    state_dir = os.path.join(_HERE, "state")
    bridge_files = {
        "fleet_state.json":  "Python → C engine (RU, fleet)",
        "ship_overlays.json": "Python → C engine (per-ship overlay)",
        "sector_map.json":   "Python → C engine (dust clouds)",
    }
    for fname, desc in bridge_files.items():
        fpath = os.path.join(state_dir, fname)
        if os.path.exists(fpath):
            age = time.time() - os.path.getmtime(fpath)
            ok  = age < 5
            all_ok &= _check(
                f"  {fname}", ok,
                f"{age:.0f}s old — bridge may have stopped" if not ok else desc,
            )
        else:
            all_ok &= _check(f"  {fname}", False,
                             "not found — start bridge: python start.py")

    # ── FastAPI ───────────────────────────────────────────────────────────────
    print("\nMOTHERSHIP API")
    try:
        import httpx
        resp = httpx.get("http://localhost:3000/health", timeout=2)
        all_ok &= _check("  /health endpoint", resp.status_code == 200,
                         resp.json().get("status", ""))
        # Count routes
        routes = httpx.get("http://localhost:3000/openapi.json", timeout=2)
        if routes.status_code == 200:
            n = len(routes.json().get("paths", {}))
            _check(f"  API routes", True, f"{n} paths registered")
    except Exception as exc:
        all_ok &= _check("  /health endpoint", False,
                         f"not running ({exc}) — run: python start_api.py")

    # ── Docker containers ─────────────────────────────────────────────────────
    print("\nDOCKER FLEET")
    if docker:
        proc = subprocess.run(
            ["docker", "ps", "--filter", "name=hw-",
             "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True, text=True,
        )
        lines = [l for l in proc.stdout.strip().split("\n") if l]
        if lines:
            for line in lines[:8]:
                parts = line.split("\t", 1)
                name, status = parts[0], parts[1] if len(parts) > 1 else "?"
                _check(f"  {name}", "Up" in status, status)
        else:
            _check("  hw-* containers", True,
                   "none running (ok — fleet not yet launched)")
    else:
        _check("  Docker fleet", False, "Docker unavailable (fleet containers disabled)")

    # ── Summary ───────────────────────────────────────────────────────────────
    if all_ok:
        print(f"\n\033[32m✓ ALL SYSTEMS GO\033[0m\n")
    else:
        print(f"\n\033[31m✗ ISSUES FOUND — check above\033[0m\n")

    return all_ok


if __name__ == "__main__":
    ok = run_checks()
    sys.exit(0 if ok else 1)
