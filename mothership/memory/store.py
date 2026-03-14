import sqlite3, json, os
from config import STATE_DIR

DB_PATH = os.path.join(STATE_DIR, "fleet.db")


def _con() -> sqlite3.Connection:
    """Open fleet.db with WAL mode and a busy timeout for concurrent safety."""
    c = sqlite3.connect(DB_PATH, timeout=5)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


def init_db():
    os.makedirs(STATE_DIR, exist_ok=True)
    con = _con()
    con.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;

        -- original key-value store --
        CREATE TABLE IF NOT EXISTS fleet_state (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agent_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ts        DATETIME DEFAULT CURRENT_TIMESTAMP,
            ship_type TEXT,
            action    TEXT,
            detail    TEXT
        );

        -- Phase 10A: live fleet roster (C engine + Docker ships read this) --
        CREATE TABLE IF NOT EXISTS active_ships (
            ship_id      TEXT PRIMARY KEY,
            short_id     TEXT UNIQUE,
            callsign     TEXT,
            class        TEXT,
            status       TEXT DEFAULT 'queued',
            pos_x        REAL DEFAULT 0,
            pos_y        REAL DEFAULT 0,
            pos_z        REAL DEFAULT 0,
            armor        INTEGER DEFAULT 100,
            max_armor    INTEGER DEFAULT 100,
            kills        INTEGER DEFAULT 0,
            current_task TEXT DEFAULT 'idle',
            last_action  TEXT DEFAULT '',
            container_id TEXT DEFAULT '',
            model        TEXT DEFAULT '',
            ru_burn_rate REAL DEFAULT 0.0,
            spawned_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        -- Phase 10A: sector-level codebase map (dirs as asteroid fields) --
        CREATE TABLE IF NOT EXISTS asteroids_map (
            sector_id    TEXT PRIMARY KEY,
            rel_path     TEXT,
            pos_x        REAL DEFAULT 0,
            pos_y        REAL DEFAULT 0,
            pos_z        REAL DEFAULT 0,
            threat_level INTEGER DEFAULT 0,
            ru_value     INTEGER DEFAULT 0,
            file_count   INTEGER DEFAULT 0,
            scouted      INTEGER DEFAULT 0,
            beacon_count INTEGER DEFAULT 0,
            radius       REAL DEFAULT 2000,
            intel_brief  TEXT DEFAULT '',
            locked_by    TEXT DEFAULT '',
            last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        -- Phase 10A: escape pods from dead ships --
        CREATE TABLE IF NOT EXISTS escape_pods (
            pod_id             TEXT PRIMARY KEY,
            dead_ship_callsign TEXT,
            dead_ship_type     TEXT,
            dead_ship_id       TEXT,
            pos_x REAL, pos_y REAL, pos_z REAL,
            context_snapshot   TEXT,
            next_steps         TEXT,
            recovered          INTEGER DEFAULT 0,
            recovered_by       TEXT DEFAULT '',
            created_at         DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        -- Phase 10A: active Taiidan enemy fleet --
        CREATE TABLE IF NOT EXISTS enemy_fleet (
            enemy_id         TEXT PRIMARY KEY,
            enemy_type       TEXT,
            target_sector_id TEXT,
            pos_x REAL, pos_y REAL, pos_z REAL,
            severity         INTEGER DEFAULT 1,
            status           TEXT DEFAULT 'inbound',
            spawned_at       DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        -- Phase 10A: CI run results for hyperspace gate --
        CREATE TABLE IF NOT EXISTS ci_results (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ts        DATETIME DEFAULT CURRENT_TIMESTAMP,
            sector_id TEXT,
            test_file TEXT,
            result    TEXT,
            error_text TEXT
        );
    """)
    con.commit()
    con.close()


def migrate_db():
    """
    Idempotent Phase 12 column additions and table creation.
    Safe to call on every startup.
    """
    con = _con()
    # Phase 12: specialization columns on active_ships
    for col, defn in [
        ("specialization_vector", "TEXT DEFAULT '[]'"),
        ("assigned_directory",    "TEXT DEFAULT ''"),
    ]:
        try:
            con.execute(f"ALTER TABLE active_ships ADD COLUMN {col} {defn}")
            print(f"[store] Migrated active_ships: added {col}")
        except Exception:
            pass   # already exists

    # Phase 12: build-gate tech tree in fleet.db (separate from entropy tech tree)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS tech_tree (
            node_id       TEXT PRIMARY KEY,
            status        TEXT NOT NULL DEFAULT 'locked',
            completed_at  DATETIME,
            artifact_path TEXT DEFAULT ''
        );
    """)
    con.commit()
    con.close()


# ── Key-value helpers (unchanged) ─────────────────────────────────────────

def get(key: str, default=None):
    con = _con()
    row = con.execute("SELECT value FROM fleet_state WHERE key=?", (key,)).fetchone()
    con.close()
    return json.loads(row[0]) if row else default


def set(key: str, value):
    con = _con()
    con.execute(
        "INSERT INTO fleet_state(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value))
    )
    con.commit()
    con.close()


def log_action(ship_type: str, action: str, detail: str = ""):
    con = _con()
    con.execute(
        "INSERT INTO agent_log(ship_type,action,detail) VALUES(?,?,?)",
        (ship_type, action, detail)
    )
    con.commit()
    con.close()


# ── Phase 10A: SQLite queries for C engine bridge export ──────────────────

def get_fleet_roster_for_engine() -> list:
    """Active ships for fleet_state.json — bridge to C engine."""
    con = _con()
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT ship_id, short_id, callsign, class, status,
               pos_x, pos_y, pos_z, armor, max_armor,
               kills, current_task, last_action, container_id,
               model, ru_burn_rate
        FROM active_ships
        WHERE status != 'lost'
        ORDER BY spawned_at
    """).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_code_map_for_engine() -> list:
    """Sector threat levels from asteroids_map → dust cloud rendering."""
    con = _con()
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT sector_id, rel_path, pos_x, pos_y, pos_z,
               threat_level, ru_value, scouted, file_count,
               beacon_count, radius, locked_by, intel_brief
        FROM asteroids_map
        ORDER BY threat_level DESC
    """).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_escape_pods_for_engine() -> list:
    """Unrecovered escape pods — rendered as golden beacons."""
    con = _con()
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT pod_id, dead_ship_callsign, dead_ship_type,
               pos_x, pos_y, pos_z, created_at
        FROM escape_pods
        WHERE recovered = 0
    """).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_enemy_fleet_for_engine() -> list:
    """Active Taiidan enemies for bridge export."""
    con = _con()
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT enemy_id, enemy_type, target_sector_id,
               pos_x, pos_y, pos_z, severity, status
        FROM enemy_fleet
        WHERE status IN ('inbound', 'attacking')
    """).fetchall()
    con.close()
    return [dict(r) for r in rows]


def sync_sector_to_db(sector: dict):
    """Upsert one sector from the in-memory map into asteroids_map."""
    con = _con()
    con.execute("""
        INSERT INTO asteroids_map
            (sector_id, rel_path, pos_x, pos_y, pos_z,
             threat_level, ru_value, file_count, scouted,
             beacon_count, radius, intel_brief)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(sector_id) DO UPDATE SET
            scouted=excluded.scouted,
            threat_level=excluded.threat_level,
            ru_value=excluded.ru_value,
            beacon_count=excluded.beacon_count,
            intel_brief=excluded.intel_brief,
            last_updated=CURRENT_TIMESTAMP
    """, (
        sector["id"], sector["rel_path"],
        sector["pos"]["x"], sector["pos"]["y"], sector["pos"]["z"],
        sector["threat_level"], sector["ru_value"],
        sector["file_count"], 1 if sector["scouted"] else 0,
        len(sector.get("beacons", [])),
        max(800, sector["file_count"] * 120),
        sector.get("intel") or "",
    ))
    con.commit()
    con.close()


def sync_all_sectors_to_db():
    """Mirror full in-memory sector_map into asteroids_map table."""
    sectors = get("sector_map", {})
    for s in sectors.values():
        sync_sector_to_db(s)
    log_action("map", "db_sync", f"{len(sectors)} sectors")
