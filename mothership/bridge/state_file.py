"""
Fallback bridge — writes fleet_state.json once per second.
The C engine polls this file; no ZeroMQ dependency needed.
"""
import json
import os
import time
import threading
from memory import store
from config import STATE_DIR

STATE_FILE = os.path.join(STATE_DIR, "fleet_state.json")
CMD_FILE   = os.path.join(STATE_DIR, "engine_cmd.json")
_running   = False


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


def write_command(cmd: dict):
    with open(CMD_FILE, "w") as f:
        json.dump(cmd, f)


def stop():
    global _running
    _running = False
