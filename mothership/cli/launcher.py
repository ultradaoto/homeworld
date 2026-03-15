#!/usr/bin/env python3
"""
homeworld — Fleet Command CLI launcher.

Invoked directly by the /usr/local/bin/homeworld shim (or scripts/homeworld.bat on Windows).
Handles: project detection, .homeworld save load/create,
subsystem boot, and the main TUI loop.

Usage:
  homeworld              → launch game + TUI (default)
  homeworld status       → print save state, don't launch
  homeworld save         → write .homeworld now
  homeworld reset        → wipe save, confirm prompt
  homeworld pause        → freeze enemy ticks
  homeworld resume       → unfreeze
  homeworld --no-game    → TUI only (no game window)
  homeworld --help       → usage
"""

import os
import sys
import argparse

# Force UTF-8 output on Windows consoles that default to cp1252
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# ── locate the mothership package root ────────────────────────────────────────
LAUNCHER_DIR   = os.path.dirname(os.path.abspath(__file__))
MOTHERSHIP_DIR = os.path.dirname(LAUNCHER_DIR)   # mothership/
REPO_ROOT      = os.path.dirname(MOTHERSHIP_DIR) # repo root

if MOTHERSHIP_DIR not in sys.path:
    sys.path.insert(0, MOTHERSHIP_DIR)

# ── project directory = where the user ran `homeworld` from ──────────────────
PROJECT_DIR = os.getcwd()


def main():
    parser = argparse.ArgumentParser(
        prog="homeworld",
        description="Homeworld Codebase Engine — Fleet Command",
    )
    parser.add_argument(
        "command", nargs="?", default="launch",
        choices=["launch", "status", "save", "reset", "pause", "resume"],
        help="Subcommand (default: launch)",
    )
    parser.add_argument(
        "--no-game", action="store_true",
        help="Skip launching the HomeworldSDL game window (TUI only)",
    )
    args = parser.parse_args()

    from cli.savefile import SaveFile
    save = SaveFile(PROJECT_DIR)

    if args.command == "status":
        save.print_status()
        return

    if args.command == "save":
        if not save.exists():
            print(f"No active campaign in {PROJECT_DIR}. Run 'homeworld' to start one.")
            return
        save.load()
        save.write()
        print(f"Saved to {save.path}")
        return

    if args.command == "reset":
        confirm = input(
            f"Wipe .homeworld in {PROJECT_DIR}? [y/N] "
        ).strip().lower()
        if confirm == "y":
            save.delete()
            print("Campaign wiped. Run 'homeworld' to start fresh.")
        return

    if args.command == "pause":
        from cli.absence import set_pause_flag
        set_pause_flag(True)
        print("Fleet Command paused. Enemy ticks suspended.")
        return

    if args.command == "resume":
        from cli.absence import set_pause_flag
        set_pause_flag(False)
        print("Fleet Command resumed.")
        return

    # Default: launch
    _launch(save, no_game=args.no_game)


_GAME_EXE   = os.path.join(REPO_ROOT, "HomeworldSDL", "build", "homeworld.exe")
_GAME_DATA  = os.path.join(REPO_ROOT, "assets")
_MSYS2_BIN  = r"C:\msys64\mingw64\bin"


def _launch_game_window() -> None:
    """Start HomeworldSDL in windowed mode as a background process."""
    import subprocess

    if not os.path.exists(_GAME_EXE):
        print(f"[HOMEWORLD] Game binary not found at {_GAME_EXE} — skipping window launch.")
        print("[HOMEWORLD] Build with: MSYSTEM=MINGW64 C:/msys64/usr/bin/bash.exe --login -c \"meson compile -C /d/Projects/Homeworld/HomeworldSDL/build\"")
        return

    if not os.path.exists(_GAME_DATA):
        print(f"[HOMEWORLD] Assets not found at {_GAME_DATA} — skipping window launch.")
        return

    # Extend PATH so SDL2.dll and MSYS2 runtimes are found
    env = os.environ.copy()
    env["PATH"] = _MSYS2_BIN + os.pathsep + env.get("PATH", "")
    env["HW_Data"]         = _GAME_DATA
    env["HW_Bridge_State"] = os.path.join(MOTHERSHIP_DIR, "state")

    try:
        subprocess.Popen(
            [_GAME_EXE, "/bigoverride", _GAME_DATA],
            cwd=os.path.dirname(_GAME_EXE),
            env=env,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        print("[HOMEWORLD] Game window launching...")
    except OSError as exc:
        print(f"[HOMEWORLD] Could not start game window: {exc}")


def _launch(save, no_game: bool = False):
    """Full boot sequence — restore save or init fresh, then enter TUI."""
    from cli.savefile import SaveFile
    from cli.absence  import AbsenceMonitor

    print(f"\n[HOMEWORLD] Project: {PROJECT_DIR}")

    if save.exists():
        print(f"[HOMEWORLD] Save found — resuming campaign ({save.age_str})")
        save.restore()
    else:
        print("[HOMEWORLD] No save found — initialising new campaign")
        save.init_fresh(PROJECT_DIR)

    # ── Launch game window ────────────────────────────────────────────────────
    if not no_game:
        _launch_game_window()

    # ── Standard subsystem boot ───────────────────────────────────────────────
    from start import start_subsystems
    start_subsystems()

    # ── Absence monitor (auto-pause, auto-save) ───────────────────────────────
    monitor = AbsenceMonitor(save, idle_threshold_seconds=300)
    monitor.start()

    # ── Register clean-exit handler ───────────────────────────────────────────
    import atexit
    import signal

    def on_exit():
        print("\n[HOMEWORLD] Auto-saving before exit...")
        try:
            save.write()
        except Exception as e:
            print(f"[HOMEWORLD] Save failed: {e}")
        monitor.stop()
        print(f"[HOMEWORLD] Saved. Hyperspace jump complete.")

    atexit.register(on_exit)
    signal.signal(signal.SIGINT,  lambda s, f: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))

    # ── Hand off to the TUI loop ──────────────────────────────────────────────
    from main import run_tui_loop
    run_tui_loop()


if __name__ == "__main__":
    main()
