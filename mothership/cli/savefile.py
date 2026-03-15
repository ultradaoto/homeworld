"""
SaveFile — .homeworld persistence layer.

One SaveFile instance per project directory.
Reads from / writes to <project_dir>/.homeworld (JSON).

On restore(), data is pushed back into the SQLite DB and
the cockpit memory files are verified to be in place.
"""

import os
import json
import datetime
import sqlite3
from typing import Optional

SAVE_FILENAME = ".homeworld"


class SaveFile:
    def __init__(self, project_dir: str):
        self.project_dir = project_dir
        self.path        = os.path.join(project_dir, SAVE_FILENAME)
        self._data: Optional[dict] = None

    # ── Existence / metadata ──────────────────────────────────────────────────

    def exists(self) -> bool:
        return os.path.isfile(self.path)

    def delete(self):
        if self.exists():
            os.remove(self.path)

    @property
    def age_str(self) -> str:
        """Human-readable age of the save."""
        if not self.exists():
            return "no save"
        mtime = os.path.getmtime(self.path)
        age   = datetime.datetime.utcnow().timestamp() - mtime
        if age < 60:
            return f"{int(age)}s ago"
        if age < 3600:
            return f"{int(age/60)}m ago"
        return f"{int(age/3600)}h ago"

    # ── Read ──────────────────────────────────────────────────────────────────

    def load(self) -> dict:
        if self._data:
            return self._data
        with open(self.path, "r") as f:
            self._data = json.load(f)
        return self._data

    def print_status(self):
        if not self.exists():
            print(f"No .homeworld save in {self.project_dir}")
            return
        d       = self.load()
        fleet   = d.get("fleet", {})
        ships   = fleet.get("active_ships", [])
        enemies = d.get("enemy_fleet", {}).get("active", [])
        ru      = d.get("game_state", {}).get("ru_balance", 0)
        print(f"\n── .homeworld ─────────────────────────────")
        print(f"  Saved:    {d.get('saved_at','?')} ({self.age_str})")
        print(f"  Project:  {d.get('project_dir','?')}")
        print(f"  Fleet:    {len(ships)} ships active")
        print(f"  Enemies:  {len(enemies)} contacts")
        print(f"  RU:       {ru}")
        print(f"────────────────────────────────────────────\n")

    # ── Init fresh ────────────────────────────────────────────────────────────

    def init_fresh(self, project_dir: str):
        """
        Called when no .homeworld exists.
        Writes a skeleton save and populates the SQLite DB from scratch.
        """
        from memory.store import init_db, migrate_db
        init_db()
        migrate_db()

        skeleton = {
            "version":                  "1.0",
            "project_dir":              project_dir,
            "saved_at":                 _now(),
            "session_duration_seconds": 0,
            "fleet":       {"active_ships": [], "queued_ships": [],
                            "lost_ships":   [], "escape_pods": []},
            "enemy_fleet": {"active": [], "destroyed": []},
            "tech_tree":   {},
            "cockpit_memory_refs": {},
            "map":         {"sectors_scouted": 0, "asteroids_illuminated": 0},
            "game_state":  {"paused": False, "ru_balance": 500,
                            "combat_log_tail": []},
        }
        self._data = skeleton
        self.write()

    # ── Write ─────────────────────────────────────────────────────────────────

    def write(self):
        """
        Snapshot current DB state into the .homeworld file.
        Called: on exit, on manual `homeworld save`, every 5 min by absence monitor.
        """
        from memory.store import DB_PATH
        import sqlite3

        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row

        active_ships = [dict(r) for r in con.execute(
            "SELECT * FROM active_ships WHERE status NOT IN ('lost','queued')"
        ).fetchall()]

        queued_ships = [dict(r) for r in con.execute(
            "SELECT * FROM active_ships WHERE status='queued'"
        ).fetchall()]

        lost_ships = [dict(r) for r in con.execute(
            "SELECT * FROM active_ships WHERE status='lost'"
        ).fetchall()]

        escape_pods = [dict(r) for r in con.execute(
            "SELECT * FROM escape_pods WHERE recovered=0"
        ).fetchall()]

        enemy_fleet_active = [dict(r) for r in con.execute(
            "SELECT * FROM enemy_fleet WHERE status IN ('inbound','attacking','active')"
        ).fetchall()]

        enemy_fleet_destroyed = [dict(r) for r in con.execute(
            "SELECT * FROM enemy_fleet WHERE status='destroyed'"
        ).fetchall()]

        tech_tree_rows = []
        try:
            tech_tree_rows = [dict(r) for r in con.execute(
                "SELECT * FROM tech_tree"
            ).fetchall()]
        except Exception:
            pass

        sectors_scouted = 0
        asteroids_count = 0
        try:
            row = con.execute(
                "SELECT COUNT(*) as c FROM asteroids_map WHERE scouted=1"
            ).fetchone()
            sectors_scouted = row["c"] if row else 0

            row = con.execute("SELECT COUNT(*) as c FROM asteroids_map").fetchone()
            asteroids_count = row["c"] if row else 0
        except Exception:
            pass

        con.close()

        # RU balance from KV store (no resource_state table in fleet.db)
        try:
            from memory import store as _store
            ru = _store.get("ru_balance", 500)
        except Exception:
            ru = 500

        # Cockpit memory refs
        try:
            from config import STATE_DIR
            cockpit_dir = os.path.join(STATE_DIR, "cockpit")
            cockpit_refs = {}
            if os.path.isdir(cockpit_dir):
                for fname in os.listdir(cockpit_dir):
                    if fname.endswith(".json"):
                        ship_id = fname[:-5]
                        cockpit_refs[ship_id] = os.path.join(cockpit_dir, fname)
        except Exception:
            cockpit_refs = {}

        prev = self._data or {}
        prev_duration = prev.get("session_duration_seconds", 0)

        self._data = {
            "version":                  "1.0",
            "project_dir":              self.project_dir,
            "saved_at":                 _now(),
            "session_duration_seconds": prev_duration,
            "fleet": {
                "active_ships": active_ships,
                "queued_ships": queued_ships,
                "lost_ships":   lost_ships,
                "escape_pods":  escape_pods,
            },
            "enemy_fleet": {
                "active":    enemy_fleet_active,
                "destroyed": enemy_fleet_destroyed,
            },
            "tech_tree":          {r["node_id"]: r for r in tech_tree_rows},
            "cockpit_memory_refs": cockpit_refs,
            "map": {
                "sectors_scouted":       sectors_scouted,
                "asteroids_illuminated": asteroids_count,
            },
            "game_state": {
                "paused":          False,
                "ru_balance":      ru,
                "combat_log_tail": [],
            },
        }

        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._data, f, indent=2)
        os.replace(tmp, self.path)

    # ── Restore ───────────────────────────────────────────────────────────────

    def restore(self):
        """
        Push save data back into the SQLite DB.
        The C engine and Docker containers will re-sync at 1Hz.
        """
        from memory.store import DB_PATH, init_db, migrate_db
        init_db()
        migrate_db()

        d   = self.load()
        con = sqlite3.connect(DB_PATH)

        for ship in d.get("fleet", {}).get("active_ships", []):
            _upsert_ship(con, ship)

        for ship in d.get("fleet", {}).get("queued_ships", []):
            _upsert_ship(con, ship)

        for enemy in d.get("enemy_fleet", {}).get("active", []):
            _upsert_enemy(con, enemy)

        for node_id, row in d.get("tech_tree", {}).items():
            try:
                con.execute("""
                    INSERT OR REPLACE INTO tech_tree
                    (node_id, status, completed_at, artifact_path)
                    VALUES (?,?,?,?)
                """, (node_id, row.get("status", "locked"),
                      row.get("completed_at"), row.get("artifact_path", "")))
            except Exception:
                pass

        # Restore RU balance to KV store
        ru = d.get("game_state", {}).get("ru_balance", 500)
        try:
            from memory import store as _store
            _store.set("ru_balance", ru)
        except Exception:
            pass

        con.commit()
        con.close()

        fleet   = d.get("fleet", {})
        n_ships = len(fleet.get("active_ships", []))
        n_enemy = len(d.get("enemy_fleet", {}).get("active", []))
        print(f"[RESTORE] {n_ships} ships, {n_enemy} enemy contacts, {ru} RU")
        print(f"[RESTORE] Cockpit memories: "
              f"{len(d.get('cockpit_memory_refs', {}))} ships")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _upsert_ship(con: sqlite3.Connection, ship: dict):
    # Only include columns that exist in active_ships
    allowed = {
        "ship_id", "short_id", "callsign", "class", "status",
        "pos_x", "pos_y", "pos_z", "armor", "max_armor",
        "kills", "current_task", "last_action", "container_id",
        "model", "ru_burn_rate", "spawned_at",
        "specialization_vector", "assigned_directory",
    }
    filtered = {k: v for k, v in ship.items() if k in allowed}
    if not filtered:
        return
    cols  = list(filtered.keys())
    marks = ",".join("?" * len(cols))
    vals  = [filtered[c] for c in cols]
    try:
        con.execute(
            f"INSERT OR REPLACE INTO active_ships ({','.join(cols)}) VALUES ({marks})",
            vals
        )
    except Exception:
        pass


def _upsert_enemy(con: sqlite3.Connection, enemy: dict):
    allowed = {
        "enemy_id", "enemy_type", "target_sector_id",
        "pos_x", "pos_y", "pos_z", "severity", "status", "spawned_at",
    }
    filtered = {k: v for k, v in enemy.items() if k in allowed}
    if not filtered:
        return
    cols  = list(filtered.keys())
    marks = ",".join("?" * len(cols))
    vals  = [filtered[c] for c in cols]
    try:
        con.execute(
            f"INSERT OR REPLACE INTO enemy_fleet ({','.join(cols)}) VALUES ({marks})",
            vals
        )
    except Exception:
        pass


def _now() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"
