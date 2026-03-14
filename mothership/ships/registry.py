"""
Ship registry — persistent fleet tracking with callsigns, UUIDs, and status.
Every ship built (in-game or by TUI) gets a record here.
"""
import uuid
import datetime
from memory import store

SHIP_SPECS = {
    # class: {cost_ru, build_secs, role, armor, description}
    "scout":       {"cost": 35,   "build": 12,  "role": "recon",   "armor": 40,
                    "desc": "Fast recon. Low armor. Cheapest way to see the map."},
    "interceptor": {"cost": 55,   "build": 18,  "role": "strike",  "armor": 60,
                    "desc": "Anti-fighter. Faster than corvettes. Deploy against scouts."},
    "corvette":    {"cost": 115,  "build": 30,  "role": "assault", "armor": 120,
                    "desc": "Heavy fighter support. Good against enemy corvettes."},
    "salvage":     {"cost": 110,  "build": 28,  "role": "capture", "armor": 100,
                    "desc": "Capture enemy ships or hostile data formats."},
    "repair":      {"cost": 95,   "build": 25,  "role": "support", "armor": 80,
                    "desc": "Repairs fleet ships. Essential for long engagements."},
    "resource":    {"cost": 95,   "build": 25,  "role": "harvest", "armor": 80,
                    "desc": "Harvests asteroids (data sources). Deposits RUs."},
    "probe":       {"cost": 15,   "build": 6,   "role": "sensor",  "armor": 10,
                    "desc": "Disposable sensor drone. No weapons. Pure intel."},
    "frigate":     {"cost": 525,  "build": 90,  "role": "capital", "armor": 600,
                    "desc": "Heavy capital. Takes on destroyers and carriers."},
    "destroyer":   {"cost": 900,  "build": 150, "role": "capital", "armor": 1200,
                    "desc": "Heavy assault capital. Use against fortified sectors."},
    "carrier":     {"cost": 1200, "build": 200, "role": "command", "armor": 2000,
                    "desc": "Mobile sub-command. Deploys its own local fleet."},
}

# Counter relationships:
#   scout       → countered by: interceptor
#   interceptor → countered by: corvette
#   corvette    → countered by: frigate
#   frigate     → countered by: destroyer
# If you don't have the right class built when the enemy escalates,
# your ships will be destroyed and sectors attacked directly.

CALLSIGNS = {
    "scout": [
        "Kharak Eye", "Sajuuk Watch", "Dust Runner", "Veil Piercer",
        "Dark Probe", "Grim Beacon", "Sand Hawk", "Void Slip",
        "Ghost Wing", "Far Sight",
    ],
    "interceptor": [
        "Khar Blade", "Ion Lance", "Taiidan Bane", "Red Shift",
        "Flux Dagger", "Plasma Sting", "Nether Strike", "Comet Fang",
        "Warp Hornet", "Null Rush",
    ],
    "corvette": [
        "Iron Nomad", "Steel Kiith", "Heavy Drift", "Grav Claw",
        "Dust Fist", "Siege Moth", "Kharak Fang", "Wraith Hull",
        "Crush Drive", "Deep Anchor",
    ],
    "resource": [
        "Ore Seeker", "Dust Drinker", "Rock Tender", "Mass Lifter",
        "Vein Crawler", "Slag Hauler", "Deep Miner", "Gravel Mouth",
        "Core Drain", "Rift Feeder",
    ],
    "probe": [
        "Whisper", "Flicker", "Glint", "Mote", "Spark",
        "Trace", "Wisp", "Shim", "Haze", "Vex",
    ],
    "frigate": [
        "Siege Lord", "Ion Wall", "Hammer Ark", "Khar Dreadnought",
        "Crusade Wing", "Iron Covenant", "Dust Hammer", "Deep Bastion",
    ],
    "destroyer": [
        "End of Days", "Void Hammer", "Last Kiith", "Dust Titan",
        "Iron Reckoning", "Deep Verdict", "Khar Judgment",
    ],
    "salvage": [
        "Bone Picker", "Wreck Diver", "Hull Stripper", "Iron Vulture",
        "Salvage Prime", "Deep Claw", "Ruin Walker",
    ],
    "carrier": [
        "Karan's Blade", "Mothership Veil", "Fleet Anchor", "Deep Command",
    ],
}


def _next_callsign(ship_type: str, fleet: list) -> str:
    pool = CALLSIGNS.get(ship_type, [ship_type.upper()])
    used = {s["callsign"] for s in fleet if s["type"] == ship_type}
    for name in pool:
        if name not in used:
            return name
    return f"{pool[0 % len(pool)]}-{len(used) + 1}"


def register_ship(ship_type: str, source: str = "tui",
                  container_id: str = "", model: str = "") -> dict:
    fleet = store.get("fleet_registry", [])
    spec  = SHIP_SPECS.get(
        ship_type,
        {"cost": 100, "build": 30, "role": "unknown", "armor": 100, "desc": ""},
    )
    ship = {
        "uuid":         str(uuid.uuid4()),
        "short_id":     str(uuid.uuid4())[:8].upper(),
        "callsign":     _next_callsign(ship_type, fleet),
        "type":         ship_type,
        "role":         spec["role"],
        "armor":        spec["armor"],
        "max_armor":    spec["armor"],
        "status":       "queued",   # queued → building → active → lost
        "source":       source,
        "container_id": container_id,
        "model":        model,
        "spawned":      datetime.datetime.utcnow().isoformat(),
        "task":         None,
        "sector":       None,
        "kills":        0,
        "log":          [],
    }
    fleet.append(ship)
    store.set("fleet_registry", fleet)

    # Dual-write to SQLite so C engine and Docker containers see live state
    import sqlite3
    from memory.store import DB_PATH
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        INSERT OR REPLACE INTO active_ships
            (ship_id, short_id, callsign, class, status,
             armor, max_armor, container_id, model, spawned_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (ship["uuid"], ship["short_id"], ship["callsign"],
          ship_type, "queued",
          spec["armor"], spec["armor"],
          container_id, model,
          ship["spawned"]))
    con.commit()
    con.close()
    return ship


def get_ship(short_id_or_callsign: str) -> dict | None:
    fleet = store.get("fleet_registry", [])
    term  = short_id_or_callsign.upper()
    for s in fleet:
        if s["short_id"] == term or s["callsign"].upper() == term:
            return s
    return None


def update_ship(short_id: str, **kwargs):
    fleet = store.get("fleet_registry", [])
    for s in fleet:
        if s["short_id"] == short_id:
            s.update(kwargs)
    store.set("fleet_registry", fleet)


def get_fleet(status: str = None) -> list:
    fleet = store.get("fleet_registry", [])
    if status:
        return [s for s in fleet if s["status"] == status]
    return fleet


def update_ship_db(short_id: str, **kwargs):
    """Sync ship state changes to SQLite alongside update_ship()."""
    import sqlite3
    from memory.store import DB_PATH
    allowed = {"status", "armor", "kills", "current_task", "last_action",
               "pos_x", "pos_y", "pos_z", "container_id", "model", "ru_burn_rate"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return
    cols = ", ".join(f"{k}=?" for k in updates)
    vals = list(updates.values()) + [short_id]
    con = sqlite3.connect(DB_PATH)
    con.execute(f"UPDATE active_ships SET {cols} WHERE short_id=?", vals)
    con.commit()
    con.close()


def mark_lost(short_id: str):
    update_ship(short_id, status="lost")
    update_ship_db(short_id, status="lost")
    store.log_action("registry", "ship_lost", short_id)
