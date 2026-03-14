import uuid
import asyncio
import datetime
from memory import store
from config import MAX_COLLECTORS, MAX_RESEARCHERS, MAX_DESTROYERS
from ships.collector  import CollectorShip
from ships.researcher import ResearcherShip

LIMITS = {
    "collector":  MAX_COLLECTORS,
    "researcher": MAX_RESEARCHERS,
    "destroyer":  MAX_DESTROYERS,
    "scout":      8,
    "support":    4,
}

SHIP_CLASSES = {
    "collector":  CollectorShip,
    "researcher": ResearcherShip,
}


def dispatch(build_order: dict, task_extras: dict = {}) -> str:
    ship_type = build_order.get("build_order", "none")
    reason    = build_order.get("reason", "")

    if ship_type == "none":
        return "no_action"
    if ship_type not in SHIP_CLASSES:
        # Unknown types still get registered but not executed
        return _register_only(ship_type, reason)

    active = store.get("active_agents", [])
    count  = sum(1 for a in active if a["type"] == ship_type)
    limit  = LIMITS.get(ship_type, 1)
    if count >= limit:
        return f"at_capacity:{ship_type}"

    agent_id = f"{ship_type}_{uuid.uuid4().hex[:6]}"
    active.append({
        "id":      agent_id,
        "type":    ship_type,
        "spawned": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "status":  "running",
    })
    store.set("active_agents", active)
    store.log_action(ship_type, "spawned", reason)

    ship = SHIP_CLASSES[ship_type](agent_id)
    task = {"objective": store.get("current_objective", ""), **task_extras}

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(ship.run(task))
    except RuntimeError:
        # No running loop (sync context) — run to completion
        asyncio.run(ship.run(task))

    return f"spawned:{agent_id}"


def _register_only(ship_type: str, reason: str) -> str:
    """Register an unimplemented ship type in the fleet list without running it."""
    active = store.get("active_agents", [])
    count  = sum(1 for a in active if a["type"] == ship_type)
    limit  = LIMITS.get(ship_type, 1)
    if count >= limit:
        return f"at_capacity:{ship_type}"

    agent_id = f"{ship_type}_{uuid.uuid4().hex[:6]}"
    active.append({
        "id":      agent_id,
        "type":    ship_type,
        "spawned": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "status":  "idle",
    })
    store.set("active_agents", active)
    store.log_action(ship_type, "spawned", reason)
    return f"spawned:{agent_id}"
