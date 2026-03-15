#!/usr/bin/env python3
"""
Homeworld Mothership — Unified Startup (Phase 11 Capstone)
Starts all subsystems in the correct order, then opens the commander TUI.
"""
import os
import subprocess
import sys
import time

# ── Make sure mothership package root is importable ───────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from rich.console import Console
from rich.panel   import Panel

console = Console(highlight=False, emoji=False, safe_box=True)


def print_banner():
    console.print("""[bold purple]
  ██╗  ██╗ ██████╗ ███╗   ███╗███████╗██╗    ██╗ ██████╗ ██████╗ ██╗     ██████╗
  ██║  ██║██╔═══██╗████╗ ████║██╔════╝██║    ██║██╔═══██╗██╔══██╗██║     ██╔══██╗
  ███████║██║   ██║██╔████╔██║█████╗  ██║ █╗ ██║██║   ██║██████╔╝██║     ██║  ██║
  ██╔══██║██║   ██║██║╚██╔╝██║██╔══╝  ██║███╗██║██║   ██║██╔══██╗██║     ██║  ██║
  ██║  ██║╚██████╔╝██║ ╚═╝ ██║███████╗╚███╔███╔╝╚██████╔╝██║  ██║███████╗██████╔╝
  ╚═╝  ╚═╝ ╚═════╝ ╚═╝     ╚═╝╚══════╝ ╚══╝╚══╝  ╚═════╝ ╚═╝  ╚═╝╚══════╝╚═════╝
[/bold purple][dim]  MOTHERSHIP COMMAND INTERFACE  ·  Phase 11 Capstone[/dim]
""")


def check_prerequisites():
    """Verify everything needed is present before starting."""
    errors = []

    # Load .env if present
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_HERE, ".env"))
    except ImportError:
        pass

    import config
    if not config.ANTHROPIC_API_KEY or not config.ANTHROPIC_API_KEY.startswith("sk-"):
        errors.append("ANTHROPIC_API_KEY missing — copy .env.example → .env and add your key")

    game_exe = r"D:\Projects\Homeworld\HomeworldSDL\build\homeworld.exe"
    if not os.path.exists(game_exe):
        errors.append(f"Game binary not found: {game_exe} — run: ninja -C HomeworldSDL/build")

    assets_big = r"D:\Projects\Homeworld\assets\homeworld.big"
    if not os.path.exists(assets_big):
        errors.append(f"homeworld.big not found — run stage-assets.ps1")

    docker_ok = subprocess.run(
        ["docker", "info"], capture_output=True
    ).returncode == 0
    if not docker_ok:
        console.print("[yellow]⚠ Docker not running — fleet containers disabled[/yellow]")

    if errors:
        console.print("[red]STARTUP BLOCKED:[/red]")
        for e in errors:
            console.print(f"  [red]✗[/red] {e}")
        sys.exit(1)

    console.print("[green]OK Prerequisites OK[/green]")


def start_subsystems() -> subprocess.Popen:
    """Start all background threads and services. Returns the API process."""
    steps = []

    # 1. Database (fleet.db — idempotent + Phase 12 migrations)
    from memory.store import init_db, migrate_db
    init_db()
    migrate_db()
    steps.append("SQLite schema (fleet.db + Phase 12 migrations)")

    # 2. Homeworld.db (Phase 11 schema — entropy, tech tree, etc.)
    try:
        import init_db as _idb
        _idb.init()
        steps.append("SQLite schema (homeworld.db)")
    except Exception as exc:
        console.print(f"[yellow]⚠ homeworld.db init skipped: {exc}[/yellow]")

    # 3. Bridge (writes JSON for C engine every second)
    from bridge.state_file import start as bridge_start
    bridge_start()
    steps.append("Bridge (file writer, 1 Hz)")

    # 4. Build queue
    from bridge.build_queue import start as bq_start
    bq_start()
    steps.append("Build queue")

    # 5. Taiidan enemy AI
    from combat.taiidan_tick import start as taiidan_start
    taiidan_start()
    steps.append("Taiidan AI (45 s tick)")

    # 6. Combat event watcher
    from combat.event_watcher import start as watcher_start
    watcher_start()
    steps.append("Combat event watcher")

    # 7. Sector entry auto-scout
    try:
        from map.entry_watcher import start as entry_start
        entry_start()
        steps.append("Sector entry watcher (auto-scout)")
    except Exception as exc:
        console.print(f"[yellow]⚠ Entry watcher skipped: {exc}[/yellow]")

    # 7b. Phase 12: Symmetrical enemy commander (fleet.db doctrine)
    try:
        from combat.enemy_commander import start as enemy_cmd_start
        enemy_cmd_start()
        steps.append("Enemy Commander — symmetrical doctrine (120 s)")
    except Exception as exc:
        console.print(f"[yellow]⚠ Enemy Commander skipped: {exc}[/yellow]")

    # 8. Entropy System background services
    try:
        import threading
        from entropy_engine import entropy_scan_loop
        from enemy_commander import enemy_commander_loop
        from research_substation import research_loop, ensure_tech_tree
        ensure_tech_tree()
        threading.Thread(target=entropy_scan_loop, daemon=True,
                         name="entropy_engine").start()
        threading.Thread(target=enemy_commander_loop, daemon=True,
                         name="enemy_commander").start()
        threading.Thread(target=research_loop, daemon=True,
                         name="research_substation").start()
        steps.append("Entropy System (scanner + commander + research)")
    except Exception as exc:
        console.print(f"[yellow]⚠ Entropy services skipped: {exc}[/yellow]")

    # 9. FastAPI Mothership server (daemon thread — no subprocess needed)
    _ensure_api_running(steps)

    for s in steps:
        console.print(f"  [green]OK[/green] {s}")

    return None


def _ensure_api_running(steps: list = None):
    """Start the API in a background daemon thread if not already up."""
    import urllib.request
    import threading

    def _alive():
        try:
            urllib.request.urlopen("http://127.0.0.1:3000/health", timeout=1)
            return True
        except Exception:
            return False

    if _alive():
        if steps is not None:
            steps.append("FastAPI Mothership :3000 (already running)")
        return

    def _run():
        import uvicorn
        uvicorn.run("api.server:app", host="127.0.0.1", port=3000,
                    log_level="error", access_log=False)

    t = threading.Thread(target=_run, daemon=True, name="api-server")
    t.start()

    # Wait up to 5 s for it to bind
    for _ in range(10):
        time.sleep(0.5)
        if _alive():
            break

    if steps is not None:
        steps.append("FastAPI Mothership :3000")


def offer_game_launch():
    try:
        from rich.prompt import Confirm
        if not Confirm.ask("\nLaunch Homeworld now?", default=True):
            return
    except (KeyboardInterrupt, EOFError):
        return

    game_exe = r"D:\Projects\Homeworld\HomeworldSDL\build\homeworld.exe"
    assets   = r"D:\Projects\Homeworld\assets"
    subprocess.Popen([game_exe, "-d", assets, "/window"])
    console.print("[dim]Game launched in windowed mode.[/dim]")


def run():
    print_banner()
    console.print(Panel(
        "Starting all subsystems...",
        title="[bold purple]MOTHERSHIP BOOT SEQUENCE[/bold purple]",
        border_style="purple",
    ))

    check_prerequisites()
    api_proc = start_subsystems()

    console.print("\n[bold green]All systems online. Fleet Command ready.[/bold green]")
    offer_game_launch()
    console.print("\n[dim]Opening commander interface...[/dim]\n")
    time.sleep(0.5)

    from main import run as tui_run
    try:
        tui_run()
    finally:
        console.print("\n[dim]Shutting down API server...[/dim]")
        api_proc.terminate()


if __name__ == "__main__":
    run()
