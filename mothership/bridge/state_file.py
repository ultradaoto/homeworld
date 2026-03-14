"""
Fallback bridge — writes fleet_state.json once per second (Python → C)
and reads engine_state.json (C → Python) for connection status.
"""
import json
import os
import time
import threading
from memory import store
from config import STATE_DIR

STATE_FILE  = os.path.join(STATE_DIR, "fleet_state.json")
ENGINE_FILE = os.path.join(STATE_DIR, "engine_state.json")
CMD_FILE    = os.path.join(STATE_DIR, "engine_cmd.json")

STALE_SECONDS = 5   # engine_state older than this = game offline

_running = False


def start():
    global _running
    _running = True
    os.makedirs(STATE_DIR, exist_ok=True)
    threading.Thread(target=_write_loop, daemon=True).start()


def _write_loop():
    while _running:
        state = {
            "ru_balance":    store.get("ru_balance", 0),
            "ships":         store.get("active_agents", []),
            "objective":     store.get("current_objective", ""),
            "findings":      store.get("findings_count", 0),
            "timestamp":     time.time(),
        }
        tmp = STATE_FILE + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(state, f)
            os.replace(tmp, STATE_FILE)
        except OSError:
            pass
        time.sleep(1.0)


def read_engine_state():
    """
    Returns dict with game state if engine is connected and fresh,
    or None if game isn't running / state file is stale / missing.
    """
    try:
        with open(ENGINE_FILE) as f:
            state = json.load(f)
        age = time.time() - state.get("timestamp", 0)
        if age > STALE_SECONDS:
            return None
        return state
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def write_command(cmd: dict):
    with open(CMD_FILE, "w") as f:
        json.dump(cmd, f)


def stop():
    global _running
    _running = False
