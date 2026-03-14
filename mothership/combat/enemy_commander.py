"""
enemy_commander.py — Symmetrical Taiidan strategic AI (Phase 12).

Runs every 120 seconds. Compares the player's fleet composition against
the enemy fleet in fleet.db. Applies Symmetrical Doctrine: when the
player escalates to a new ship class, the Taiidan deploy a matching
capital to a relevant target sector.

This makes Phase 13 duels meaningful — the enemy exists because YOU
built ships first. You are never safe at the top of the tech tree.

Separate from taiidan_tick.py (tactical scouts) and the root-level
enemy_commander.py (entropy fleet mirror). This module targets fleet.db.
"""
import datetime
import sqlite3
import threading
import time
import uuid

from bridge.state_file import write_command
from memory.store      import DB_PATH

_running = False

# (player_class, enemy_deploys, id_prefix, target_sector_path)
ESCALATION_DOCTRINE = [
    ("destroyer",  "taiidan_capital",  "TAI_CAP", "HomeworldSDL/src"),
    ("carrier",    "taiidan_sub",       "TAI_SUB", "mothership/combat"),
    ("frigate",    "taiidan_heavy",     "TAI_HVY", "mothership/ships"),
    ("corvette",   "taiidan_medium",    "TAI_MED", "mothership/api"),
    ("interceptor","taiidan_light",     "TAI_LGT", "mothership/map"),
]


def _get_player_classes() -> list[str]:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT DISTINCT class FROM active_ships WHERE status NOT IN ('lost','queued')"
    ).fetchall()
    con.close()
    return [r["class"] for r in rows if r["class"]]


def _get_enemy_types() -> list[str]:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT DISTINCT enemy_type FROM enemy_fleet "
        "WHERE status IN ('inbound','attacking')"
    ).fetchall()
    con.close()
    return [r["enemy_type"] for r in rows if r["enemy_type"]]


def _sector_pos(target_hint: str) -> dict:
    """Find a sector position matching target_hint, or use a far default."""
    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT pos_x, pos_y, pos_z FROM asteroids_map "
            "WHERE rel_path LIKE ? LIMIT 1",
            (f"%{target_hint}%",),
        ).fetchone()
        con.close()
        if row:
            return {"x": row["pos_x"], "y": row["pos_y"], "z": row["pos_z"]}
    except Exception:
        pass
    return {"x": 22000, "y": 0, "z": 22000}


def _register_enemy(enemy_type: str, id_prefix: str, target: str) -> str:
    enemy_id = f"{id_prefix}_{uuid.uuid4().hex[:4].upper()}"
    pos      = _sector_pos(target)
    spawn_x  = pos["x"] - 16000
    spawn_y  = pos["y"]
    spawn_z  = pos["z"] - 16000
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute(
            """INSERT OR REPLACE INTO enemy_fleet
               (enemy_id, enemy_type, target_sector_id,
                pos_x, pos_y, pos_z, severity, status, spawned_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (enemy_id, enemy_type, target,
             spawn_x, spawn_y, spawn_z,
             4, "inbound",
             datetime.datetime.utcnow().isoformat()),
        )
        con.commit()
        con.close()
    except Exception as exc:
        print(f"[enemy_commander] DB error: {exc}")
        return enemy_id

    # Tell C engine to render the ship
    write_command({
        "type":     "spawn_enemy",
        "id":       enemy_id,
        "ship":     enemy_type,
        "x":        spawn_x,
        "y":        spawn_y,
        "z":        spawn_z,
        "target_x": pos["x"],
        "target_y": pos["y"],
        "target_z": pos["z"],
        "threat":   4,
    })
    return enemy_id


def match_player_doctrine() -> None:
    """
    Core symmetry engine: for each escalation tier, if the player has
    the ship class but the Taiidan haven't matched it yet, deploy one.
    """
    player_classes = _get_player_classes()
    enemy_types    = _get_enemy_types()

    for player_class, enemy_type, id_prefix, target in ESCALATION_DOCTRINE:
        if player_class not in player_classes:
            continue
        if enemy_type in enemy_types:
            continue
        print(f"[enemy_commander] Player has {player_class} — "
              f"deploying {enemy_type} → {target}")
        eid = _register_enemy(enemy_type, id_prefix, target)
        print(f"[enemy_commander] {eid} inbound. Escalate or be destroyed.")


def _loop() -> None:
    time.sleep(60)   # give player a head start
    while _running:
        try:
            match_player_doctrine()
        except Exception as exc:
            print(f"[enemy_commander] error: {exc}")
        time.sleep(120)


def start() -> None:
    global _running
    _running = True
    threading.Thread(target=_loop, daemon=True, name="enemy_commander").start()
    print("[enemy_commander] Symmetrical doctrine active (120 s evaluation).")
