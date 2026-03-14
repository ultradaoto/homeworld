"""
Serial build queue — one ship at a time.

Python validates RUs, registers the ship, and sends ONE build command to
the C engine. Only when the C engine writes engine_built.json (or the
fallback timer fires) does Python send the next command.
"""
import json
import os
import threading
import time
import datetime

from ships.registry import SHIP_SPECS, register_ship, update_ship, update_ship_db
from memory import store
from bridge.state_file import write_command

_queue:           list      = []
_lock             = threading.Lock()
_completion_lock  = threading.Lock()
_building: dict | None    = None
_running          = False


def enqueue(ship_type: str) -> "dict | str":
    """
    Validate RUs, register the ship, add to serial queue.
    Returns the ship dict on success, or an error string.
    """
    # Phase 12: tech tree build gate
    try:
        from command.tech_tree import is_buildable
        buildable, reason = is_buildable(ship_type)
        if not buildable:
            return f"LOCKED: {reason}"
    except Exception:
        pass   # gate unavailable — allow build (fail open)

    spec = SHIP_SPECS.get(ship_type)
    if not spec:
        return f"unknown ship type: {ship_type}"

    ru = store.get("ru_balance", 0)
    if ru < spec["cost"]:
        return (
            f"insufficient RUs: need {spec['cost']}, have {ru}. "
            f"Send harvesters or use 'ru <n>' to deposit."
        )

    store.set("ru_balance", ru - spec["cost"])
    store.log_action("build_queue", "ru_deducted",
                     f"{ship_type} cost {spec['cost']} RU")

    ship = register_ship(ship_type, source="tui")
    with _lock:
        _queue.append({"type": ship_type, "ship": ship, "spec": spec})

    _send_next_if_idle()
    return ship


def _send_next_if_idle():
    global _building
    with _lock:
        if _building is not None:
            return
        if not _queue:
            return
        item     = _queue.pop(0)
        _building = item

    ship = item["ship"]
    spec = item["spec"]
    update_ship(ship["short_id"], status="building")

    write_command({
        "type":       "build_ship",
        "ship_type":  item["type"],
        "short_id":   ship["short_id"],
        "callsign":   ship["callsign"],
        "build_secs": spec["build"],
    })
    store.log_action("build_queue", "sent_to_engine",
                     f"{ship['callsign']} ({item['type']})")


def on_ship_built(short_id: str):
    """Call when C engine (or fallback timer) confirms a ship launched."""
    global _building
    item = _building  # save before clearing

    update_ship(short_id, status="active",
                spawned=datetime.datetime.utcnow().isoformat())
    update_ship_db(short_id, status="active")
    store.log_action("build_queue", "ship_active", short_id)

    with _lock:
        _building = None

    # Launch the Docker container for this ship
    if item:
        try:
            from docker.launcher import launch_ship
            from ships.registry import get_ship
            ship = get_ship(short_id)
            if ship:
                result = launch_ship(
                    ship_type=ship["type"],
                    callsign=ship["callsign"],
                    short_id=short_id,
                )
                if result.get("container_id"):
                    cid   = result["container_id"]
                    model = result.get("model", "")
                    update_ship(short_id, container_id=cid, model=model)
                    update_ship_db(short_id, container_id=cid, model=model)
                    print(f"[BUILD] Docker container {cid} → "
                          f"{ship['callsign']} ({model})")
        except Exception as exc:
            store.log_action("build_queue", "docker_launch_failed", str(exc))

    _send_next_if_idle()


def queue_status() -> dict:
    with _lock:
        return {
            "building": _building["ship"]["callsign"] if _building else None,
            "queued":   [i["ship"]["callsign"] for i in _queue],
            "depth":    len(_queue),
        }


def start():
    global _running
    _running = True
    threading.Thread(target=_poll_completions, daemon=True).start()


def _poll_completions():
    """
    Background thread: watch for engine_built.json from C engine.
    Fallback: if game is offline, fire completion after build_secs * 1.5.
    """
    from config import STATE_DIR

    built_file = os.path.join(STATE_DIR, "engine_built.json")
    build_start: float = 0.0

    while _running:
        time.sleep(0.5)

        # Track when we started building for fallback timer
        with _lock:
            current = _building

        if current and build_start == 0.0:
            build_start = time.time()

        if not current:
            build_start = 0.0
            continue

        # Check for C engine completion signal
        if os.path.exists(built_file):
            with _completion_lock:
                if os.path.exists(built_file):   # double-check under lock
                    try:
                        with open(built_file) as f:
                            data = json.load(f)
                        os.remove(built_file)
                        sid = data.get("short_id", "")
                        if sid:
                            on_ship_built(sid)
                            build_start = 0.0
                            continue
                    except (OSError, json.JSONDecodeError):
                        pass

        # Fallback: mark complete if build time has elapsed
        # (game offline or engine_built.json never came)
        spec = current.get("spec", {})
        timeout = spec.get("build", 30) * 1.5
        if build_start and (time.time() - build_start) >= timeout:
            sid = current["ship"]["short_id"]
            on_ship_built(sid)
            build_start = 0.0
