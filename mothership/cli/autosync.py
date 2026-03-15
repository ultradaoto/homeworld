"""
autosync.py — Automatic game state synchronization.

Polls the C bridge engine_state.json every 5 seconds.
When the game is found: silently syncs RU balance to fleet store.
Prints one line on connect, one line on disconnect. Never spams.
The Commander never types 'sync' again.
"""
import threading
import time


SYNC_INTERVAL = 5   # seconds


class AutoSync:
    def __init__(self):
        self._connected = False
        self._thread = None

    def start(self):
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="auto-sync")
        self._thread.start()

    def _loop(self):
        import json, os
        from bridge.state_file import STATE_FILE, STALE_SECONDS
        from memory import store

        printed_waiting = False
        while True:
            try:
                state = None
                if os.path.exists(STATE_FILE):
                    with open(STATE_FILE) as f:
                        data = json.load(f)
                    age = time.time() - data.get("timestamp", 0)
                    if age <= STALE_SECONDS:
                        state = data

                if state:
                    ru = state.get("ru_balance", 0)
                    store.set("ru_balance", ru)
                    if not self._connected:
                        self._connected = True
                        printed_waiting = False
                        ships = state.get("ship_count", 0)
                        print(f"\n[SYNC] Fleet online — {ships} ships | {ru} RU", flush=True)
                else:
                    if self._connected:
                        self._connected = False
                        print("\n[SYNC] Fleet offline — waiting...", flush=True)
                    elif not printed_waiting:
                        printed_waiting = True
                        print("[SYNC] Watching for fleet...", flush=True)
            except Exception:
                pass
            time.sleep(SYNC_INTERVAL)
