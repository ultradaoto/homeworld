import uuid
import datetime
from memory import store
from config import MAX_COLLECTORS, MAX_RESEARCHERS, MAX_DESTROYERS

LIMITS = {
    "collector":  MAX_COLLECTORS,
    "researcher": MAX_RESEARCHERS,
    "destroyer":  MAX_DESTROYERS,
    "scout":      8,
    "support":    4,
}

def dispatch(build_order: dict) -> str:
    ship   = build_order.get("build_order", "none")
    reason = build_order.get("reason", "")

    if ship == "none":
        return "no_action"

    active = store.get("active_agents", [])
    current_count = sum(1 for a in active if a["type"] == ship)
    limit = LIMITS.get(ship, 1)

    if current_count >= limit:
        return f"at_capacity:{ship}"

    agent_id = f"{ship}_{uuid.uuid4().hex[:6]}"
    active.append({
        "id":      agent_id,
        "type":    ship,
        "spawned": datetime.datetime.utcnow().isoformat(),
        "status":  "idle",
    })
    store.set("active_agents", active)
    store.log_action(ship, "spawned", reason)
    return f"spawned:{agent_id}"
