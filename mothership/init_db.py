"""
Run once to initialize homeworld.db (Phase 10C schema).
Also safe to run repeatedly — uses CREATE TABLE IF NOT EXISTS.
Usage: python init_db.py
"""
import sqlite3, os

DB_PATH = os.environ.get(
    "SQLITE_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "homeworld.db")
)

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS active_ships (
    id              TEXT PRIMARY KEY,
    class           TEXT NOT NULL,
    model           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'idle',
    sector_id       TEXT,
    task_id         TEXT,
    port            INTEGER,
    context_used    INTEGER DEFAULT 0,
    context_budget  INTEGER DEFAULT 8000,
    registered_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_heartbeat  TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS asteroids_map (
    id              TEXT PRIMARY KEY,
    filepath        TEXT UNIQUE NOT NULL,
    filetype        TEXT,
    size_bytes      INTEGER,
    locked_by       TEXT,
    lock_expires    TEXT,
    threat_level    INTEGER NOT NULL DEFAULT 0,
    last_scanned    TEXT,
    last_modified   TEXT,
    FOREIGN KEY (locked_by) REFERENCES active_ships(id)
);

CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    type            TEXT NOT NULL,
    priority        INTEGER NOT NULL DEFAULT 5,
    status          TEXT NOT NULL DEFAULT 'queued',
    sector_id       TEXT,
    prompt          TEXT NOT NULL,
    result          TEXT,
    artifacts       TEXT,
    assigned_to     TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    assigned_at     TEXT,
    completed_at    TEXT,
    cost_usd        REAL DEFAULT 0.0,
    FOREIGN KEY (assigned_to) REFERENCES active_ships(id)
);

CREATE TABLE IF NOT EXISTS escape_pods (
    id              TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    from_ship_id    TEXT NOT NULL,
    task_id         TEXT,
    partial_output  TEXT,
    context_window  TEXT NOT NULL,
    next_steps      TEXT,
    last_file       TEXT,
    last_line       INTEGER,
    ejected_at      TEXT NOT NULL DEFAULT (datetime('now')),
    recovered_by    TEXT,
    recovered_at    TEXT,
    FOREIGN KEY (task_id)       REFERENCES tasks(id),
    FOREIGN KEY (recovered_by)  REFERENCES active_ships(id)
);

CREATE TABLE IF NOT EXISTS file_locks (
    filepath        TEXT PRIMARY KEY,
    held_by         TEXT NOT NULL,
    acquired_at     TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at      TEXT NOT NULL,
    FOREIGN KEY (held_by) REFERENCES active_ships(id)
);

CREATE TABLE IF NOT EXISTS intelligence (
    id              TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    topic           TEXT NOT NULL,
    source_ship     TEXT,
    content         TEXT NOT NULL,
    related_task_id TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (source_ship)     REFERENCES active_ships(id),
    FOREIGN KEY (related_task_id) REFERENCES tasks(id)
);

-- Indexes for hot query paths
CREATE INDEX IF NOT EXISTS idx_tasks_status    ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_priority  ON tasks(priority DESC, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_ships_status    ON active_ships(status);
CREATE INDEX IF NOT EXISTS idx_locks_expiry    ON file_locks(expires_at);
CREATE INDEX IF NOT EXISTS idx_intel_topic     ON intelligence(topic);

-- ── Entropy System (Phase 11) ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS entropy_fleet (
    id                  TEXT PRIMARY KEY,
    ship_class          TEXT NOT NULL,
    assigned_sector     TEXT NOT NULL,
    hp                  INTEGER NOT NULL DEFAULT 100,
    occupation_start    TEXT NOT NULL DEFAULT (datetime('now')),
    status              TEXT NOT NULL DEFAULT 'active',
    description         TEXT
);

CREATE TABLE IF NOT EXISTS tech_tree (
    node_id         TEXT PRIMARY KEY,
    status          TEXT NOT NULL DEFAULT 'locked',
    requires_json   TEXT NOT NULL DEFAULT '[]',
    produces        TEXT,
    unlocks         TEXT,
    description     TEXT,
    completed_at    TEXT,
    artifact_path   TEXT
);

CREATE INDEX IF NOT EXISTS idx_entropy_status   ON entropy_fleet(status);
CREATE INDEX IF NOT EXISTS idx_entropy_sector   ON entropy_fleet(assigned_sector);
CREATE INDEX IF NOT EXISTS idx_techtree_status  ON tech_tree(status);

-- ── Phase 11.1 additions ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS postbattle_registry (
    sector              TEXT PRIMARY KEY,
    cleared_at          TEXT NOT NULL DEFAULT (datetime('now')),
    player_destroyer_id TEXT,
    refinement_artifact TEXT,
    notes               TEXT
);

-- ── Phase 13 — patch pipeline audit log ──────────────────────────────────────

CREATE TABLE IF NOT EXISTS patch_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_id     TEXT NOT NULL,
    ship_id         TEXT NOT NULL,
    sector_path     TEXT NOT NULL,
    issues_json     TEXT NOT NULL DEFAULT '[]',
    patch_text      TEXT NOT NULL DEFAULT '',
    applied         INTEGER NOT NULL DEFAULT 0,
    tests_passed    INTEGER NOT NULL DEFAULT 0,
    tests_failed    INTEGER NOT NULL DEFAULT 0,
    test_output     TEXT NOT NULL DEFAULT '',
    committed       INTEGER NOT NULL DEFAULT 0,
    reverted        INTEGER NOT NULL DEFAULT 0,
    degraded        INTEGER NOT NULL DEFAULT 0,
    runner_used     TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_patch_audit_ship    ON patch_audit(ship_id);
CREATE INDEX IF NOT EXISTS idx_patch_audit_sector  ON patch_audit(sector_path);
CREATE INDEX IF NOT EXISTS idx_patch_audit_ts      ON patch_audit(created_at DESC);
"""

# Idempotent column additions.  Each entry: (table, col_name, col_def).
_MIGRATIONS = [
    # Phase 11 — active_ships
    ("active_ships", "status_patrol",         "BOOLEAN NOT NULL DEFAULT 1"),
    ("active_ships", "assigned_directory",    "TEXT"),
    ("active_ships", "specialization_vector", "TEXT"),
    ("active_ships", "long_term_memory_id",   "TEXT"),
    # Target C — entropy_fleet issue metadata
    ("entropy_fleet", "issue_description", "TEXT"),
    ("entropy_fleet", "issue_line",        "INTEGER"),
    ("entropy_fleet", "severity_score",    "INTEGER DEFAULT 1"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent: add new columns to any table that is missing them."""
    # Build a per-table set of existing columns
    tables_seen: dict[str, set[str]] = {}
    for table, col_name, col_def in _MIGRATIONS:
        if table not in tables_seen:
            tables_seen[table] = {
                row[1] for row in conn.execute(f"PRAGMA table_info({table})")
            }
        if col_name not in tables_seen[table]:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")
            tables_seen[table].add(col_name)
            print(f"[init_db] Migrated {table}: added {col_name}")


def init(path: str = DB_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    conn.close()
    print(f"[init_db] Database ready at {path}")
    # Verify
    conn = sqlite3.connect(path)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    print(f"[init_db] Tables: {[t[0] for t in tables]}")
    conn.close()


if __name__ == "__main__":
    init()
