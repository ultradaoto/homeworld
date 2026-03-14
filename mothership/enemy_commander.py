"""
enemy_commander.py — Entropy System build doctrine (Phase 11).

Mirrors player fleet composition to maintain threat parity.
Run as a background service or via: python enemy_commander.py

Doctrine rules (from spec):
  - Always maintain a base swarm of entropy_interceptors.
  - If player has a Destroyer, spawn an entropy_destroyer in the largest debt sector.
  - If player has Frigates, mirror their count with entropy_frigates.
"""
import os
import sqlite3
import time
import uuid as _uuid

DB_PATH = os.environ.get(
    "SQLITE_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "homeworld.db"),
)
DOCTRINE_INTERVAL = 120  # seconds

THREAT_HP = {
    "entropy_destroyer":    100,
    "entropy_frigate":       60,
    "entropy_interceptor":   20,
    "entropy_scout":         10,
}
BASE_SWARM_SIZE = 5  # minimum interceptor presence


# ── Database ───────────────────────────────────────────────────────────────────

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


# ── State queries ──────────────────────────────────────────────────────────────

def _load_game_state(conn: sqlite3.Connection) -> dict:
    player = {
        r["class"]: r["count"]
        for r in conn.execute(
            "SELECT class, COUNT(*) AS count FROM active_ships "
            "WHERE status != 'dead' GROUP BY class"
        )
    }
    enemy = {
        r["ship_class"]: r["count"]
        for r in conn.execute(
            "SELECT ship_class, COUNT(*) AS count FROM entropy_fleet "
            "WHERE status = 'active' GROUP BY ship_class"
        )
    }
    return {"player": player, "enemy": enemy}


def _find_largest_debt_sector(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT filepath FROM asteroids_map ORDER BY threat_level DESC LIMIT 1"
    ).fetchone()
    return row["filepath"] if row else "unknown_sector"


def _find_weakest_sector(conn: sqlite3.Connection) -> str:
    """Sector with highest threat level that has no entropy coverage."""
    row = conn.execute(
        """SELECT a.filepath FROM asteroids_map a
           LEFT JOIN entropy_fleet e
             ON e.assigned_sector = a.filepath AND e.status = 'active'
           WHERE e.id IS NULL
           ORDER BY a.threat_level DESC LIMIT 1"""
    ).fetchone()
    return row["filepath"] if row else "unknown_sector"


def _enemy_has(conn: sqlite3.Connection, ship_class: str) -> bool:
    return bool(
        conn.execute(
            "SELECT id FROM entropy_fleet WHERE ship_class = ? AND status = 'active' LIMIT 1",
            (ship_class,),
        ).fetchone()
    )


def _player_count(conn: sqlite3.Connection, *classes: str) -> int:
    placeholders = ",".join("?" * len(classes))
    row = conn.execute(
        f"SELECT COUNT(*) AS c FROM active_ships "
        f"WHERE class IN ({placeholders}) AND status != 'dead'",
        classes,
    ).fetchone()
    return row["c"] if row else 0


# ── Build actions ─────────────────────────────────────────────────────────────

def _queue_build(conn: sqlite3.Connection, ship_class: str,
                 sector: str, count: int = 1) -> int:
    spawned = 0
    existing = conn.execute(
        "SELECT assigned_sector FROM entropy_fleet "
        "WHERE status = 'active' AND assigned_sector = ? LIMIT 1",
        (sector,),
    ).fetchone()
    for _ in range(count):
        unit_id = f"ENT-{_uuid.uuid4().hex[:6].upper()}"
        hp = THREAT_HP.get(ship_class, 50)
        conn.execute(
            """INSERT OR IGNORE INTO entropy_fleet
               (id, ship_class, assigned_sector, hp, occupation_start, status)
               VALUES (?, ?, ?, ?, datetime('now'), 'active')""",
            (unit_id, ship_class, sector, hp),
        )
        spawned += 1
        print(f"[enemy_commander] Spawned {unit_id} ({ship_class}) -> {sector[-60:]}")
    return spawned


# ── Doctrine ───────────────────────────────────────────────────────────────────

def enemy_build_doctrine(conn: sqlite3.Connection) -> None:
    state  = _load_game_state(conn)
    player = state["player"]
    enemy  = state["enemy"]

    # 1. Base interceptor swarm — always maintain presence
    current_interceptors = enemy.get("entropy_interceptor", 0)
    if current_interceptors < BASE_SWARM_SIZE:
        sector = _find_weakest_sector(conn)
        _queue_build(conn, "entropy_interceptor", sector,
                     BASE_SWARM_SIZE - current_interceptors)

    # 2. Parity escalation: Destroyer
    if _player_count(conn, "destroyer") > 0 and not _enemy_has(conn, "entropy_destroyer"):
        sector = _find_largest_debt_sector(conn)
        _queue_build(conn, "entropy_destroyer", sector)

    # 3. Parity escalation: Frigates
    player_frigates = _player_count(conn, "assault_frigate", "frigate")
    enemy_frigates  = enemy.get("entropy_frigate", 0)
    if player_frigates > enemy_frigates:
        sector = _find_weakest_sector(conn)
        _queue_build(conn, "entropy_frigate", sector, player_frigates - enemy_frigates)


# ── Main loop ─────────────────────────────────────────────────────────────────

def enemy_commander_loop() -> None:
    print("[enemy_commander] Starting enemy build doctrine loop...")
    while True:
        try:
            with _db() as conn:
                enemy_build_doctrine(conn)
                conn.commit()
        except Exception as exc:
            print(f"[enemy_commander] Error: {exc}")
        time.sleep(DOCTRINE_INTERVAL)


if __name__ == "__main__":
    enemy_commander_loop()
