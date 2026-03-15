"""
AbsenceMonitor — idle detection, auto-pause, auto-save.

Uses pynput to track last input time. On idle > threshold:
  - Sets PAUSED flag (file + fleet_state KV)
  - Enemy commander + combat daemons check this flag and skip ticks
  - Console prints a warning

Auto-save runs every 300 seconds unconditionally.

The pause flag can also be set manually:
  homeworld pause
  homeworld resume
"""

import threading
import time
import datetime
import os

# ── Pause flag file location ──────────────────────────────────────────────────

_STATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "state",
)
PAUSE_FLAG_FILE = os.path.join(_STATE_DIR, "PAUSED")


# ── Manual pause flag (file-based, survives process restarts) ─────────────────

def set_pause_flag(paused: bool):
    os.makedirs(_STATE_DIR, exist_ok=True)
    if paused:
        with open(PAUSE_FLAG_FILE, "w") as f:
            f.write(datetime.datetime.utcnow().isoformat())
    else:
        if os.path.exists(PAUSE_FLAG_FILE):
            os.remove(PAUSE_FLAG_FILE)
    _sync_kv_pause(paused)


def is_paused() -> bool:
    """
    Called by enemy_commander, taiidan_tick, and combat daemons.
    Returns True if the PAUSED flag file exists.
    """
    return os.path.exists(PAUSE_FLAG_FILE)


def _sync_kv_pause(paused: bool):
    """Mirror pause state into fleet_state KV so other components can query it."""
    try:
        from memory import store
        store.set("paused", paused)
    except Exception:
        pass


# ── Absence Monitor ───────────────────────────────────────────────────────────

class AbsenceMonitor:
    """
    Monitors keyboard/mouse activity.
    Pauses the game after `idle_threshold_seconds` of inactivity.
    Auto-saves every `autosave_interval_seconds`.
    """

    def __init__(self, savefile, idle_threshold_seconds: int = 300,
                 autosave_interval_seconds: int = 300):
        self.save              = savefile
        self.idle_threshold    = idle_threshold_seconds
        self.autosave_interval = autosave_interval_seconds
        self._last_input       = time.time()
        self._last_save        = time.time()
        self._running          = False
        self._thread           = None
        self._paused_by_us     = False
        self._setup_input_listener()

    def _setup_input_listener(self):
        """
        Uses pynput to track last input time.
        Degrades gracefully if pynput is not installed — auto-save still works.
        """
        try:
            from pynput import keyboard, mouse

            def on_activity(*args, **kwargs):
                self._last_input = time.time()
                if self._paused_by_us:
                    set_pause_flag(False)
                    self._paused_by_us = False
                    print("\n[HOMEWORLD] Commander returned — resuming fleet operations.")

            self._kb_listener = keyboard.Listener(
                on_press=on_activity, on_release=on_activity)
            self._mouse_listener = mouse.Listener(
                on_move=on_activity, on_click=on_activity,
                on_scroll=on_activity)
            self._input_tracking = True
        except ImportError:
            print("[absence] pynput not installed — idle detection disabled. "
                  "Auto-save every 5 min still active.")
            self._input_tracking = False

    def start(self):
        self._running = True
        if self._input_tracking:
            self._kb_listener.start()
            self._mouse_listener.start()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="absence-monitor")
        self._thread.start()

    def stop(self):
        self._running = False
        if self._input_tracking:
            try:
                self._kb_listener.stop()
                self._mouse_listener.stop()
            except Exception:
                pass

    def _loop(self):
        while self._running:
            time.sleep(30)  # check every 30 seconds
            now      = time.time()
            idle_sec = now - self._last_input

            # Auto-save
            if now - self._last_save >= self.autosave_interval:
                try:
                    self.save.write()
                    self._last_save = now
                    print(f"\n[AUTO-SAVE] .homeworld written "
                          f"({datetime.datetime.utcnow().strftime('%H:%M:%S')})")
                except Exception as e:
                    print(f"[AUTO-SAVE] Failed: {e}")

            # Idle pause
            if (self._input_tracking and
                    idle_sec >= self.idle_threshold and
                    not self._paused_by_us and
                    not is_paused()):
                set_pause_flag(True)
                self._paused_by_us = True
                idle_min = int(idle_sec / 60)
                print(f"\n[HOMEWORLD] Commander absent for {idle_min}m — "
                      f"fleet operations paused. Move mouse to resume.")
