import sqlite3, json, os
from config import STATE_DIR

DB_PATH = os.path.join(STATE_DIR, "fleet.db")

def init_db():
    os.makedirs(STATE_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
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
    """)
    con.commit()
    con.close()

def get(key: str, default=None):
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT value FROM fleet_state WHERE key=?", (key,)).fetchone()
    con.close()
    return json.loads(row[0]) if row else default

def set(key: str, value):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO fleet_state(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value))
    )
    con.commit()
    con.close()

def log_action(ship_type: str, action: str, detail: str = ""):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO agent_log(ship_type,action,detail) VALUES(?,?,?)",
        (ship_type, action, detail)
    )
    con.commit()
    con.close()
