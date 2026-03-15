"""
Homeworld Mothership — FastAPI + SQLite nerve center (Phase 10C).
All fleet state lives here. Ships register, poll orders, lock files,
heartbeat, and eject escape pods through this API.
"""
import sys, os

# Allow "from init_db import init" when server.py is in mothership/api/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3, uuid, json
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

DB_PATH = os.environ.get(
    "SQLITE_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "state", "homeworld.db")
)


# ── Database helper ───────────────────────────────────────────────────────────

def db() -> sqlite3.Connection:
    """Return a WAL-mode connection with row factory set."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


# ── Pydantic models ───────────────────────────────────────────────────────────

class ShipRegistration(BaseModel):
    ship_id:        str
    ship_class:     str
    model:          str
    port:           int
    context_budget: int = 8000

class Heartbeat(BaseModel):
    ship_id:      str
    context_used: int
    status:       str

class TaskCreate(BaseModel):
    type:      str
    prompt:    str
    priority:  int = 5
    sector_id: Optional[str] = None

class TaskComplete(BaseModel):
    ship_id:  str
    task_id:  str
    result:   str
    cost_usd: float = 0.0

class LockRequest(BaseModel):
    ship_id:     str
    filepath:    str
    ttl_minutes: int = 10

class EscapePodPayload(BaseModel):
    from_ship_id:   str
    task_id:        Optional[str] = None
    partial_output: Optional[str] = None
    context_window: str           # JSON-serialized message list
    next_steps:     Optional[str] = None
    last_file:      Optional[str] = None
    last_line:      Optional[int] = None

class IntelligencePost(BaseModel):
    ship_id:  str
    topic:    str
    content:  str
    task_id:  Optional[str] = None

class OrderAssign(BaseModel):
    """Phase 10B compat: assign a specific task to a named container."""
    type:     str
    prompt:   str
    priority: int = 5


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    from init_db import init
    init(DB_PATH)
    with db() as conn:
        deleted = conn.execute(
            "DELETE FROM file_locks WHERE expires_at < datetime('now')"
        ).rowcount
        if deleted:
            print(f"[mothership] Expired {deleted} stale file lock(s) on startup")
    yield


app = FastAPI(title="Homeworld Mothership", version="14.1.0", lifespan=lifespan)

# ── Console chat (Phase 14B) ──────────────────────────────────────────────────

class ConsoleMsg(BaseModel):
    ship_id:   str
    ship_type: str
    message:   str


def _voice_out():
    """Lazy singleton — only imports if HOMEWORLD_VOICE=1."""
    import os
    if os.environ.get("HOMEWORLD_VOICE", "0") != "1":
        return None
    try:
        from console.voice_output import VoiceOutput
        return VoiceOutput()
    except Exception:
        return None


@app.post("/api/console")
def console_chat(msg: ConsoleMsg):
    """
    Called by the C-side GameChat via libcurl (and voice_input.py).
    Returns a tactical reply from the Mothership AI.
    """
    from console.chat_agent import generate_reply
    print(f"\n[MOTHERSHIP] {msg.ship_id}: {msg.message}", flush=True)
    reply = generate_reply(msg.ship_id, msg.ship_type, msg.message)
    print(f"[MOTHERSHIP] reply: {reply}", flush=True)
    vo = _voice_out()
    if vo:
        vo.speak(reply)   # non-blocking
    return {"reply": reply, "ship_id": msg.ship_id}


@app.get("/api/console/history")
def console_history(ship_id: str):
    """Called when the console overlay first opens to restore session."""
    from console.chat_agent import get_session_history
    messages = get_session_history(ship_id)
    return {"ship_id": ship_id, "messages": messages}


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    with db() as conn:
        ship_count = conn.execute(
            "SELECT count(*) FROM active_ships WHERE status != 'dead'"
        ).fetchone()[0]
        task_count = conn.execute(
            "SELECT count(*) FROM tasks WHERE status = 'queued'"
        ).fetchone()[0]
    return {
        "status":       "online",
        "timestamp":    datetime.utcnow().isoformat(),
        "active_ships": ship_count,
        "queued_tasks": task_count,
    }


# ── Fleet registration & heartbeat ───────────────────────────────────────────

@app.post("/api/fleet/register", status_code=201)
def register(reg: ShipRegistration):
    with db() as conn:
        conn.execute("""
            INSERT INTO active_ships
                (id, class, model, status, port, context_budget,
                 registered_at, last_heartbeat)
            VALUES (?, ?, ?, 'idle', ?, ?, datetime('now'), datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                status         = 'idle',
                model          = excluded.model,
                port           = excluded.port,
                context_budget = excluded.context_budget,
                last_heartbeat = datetime('now')
        """, (reg.ship_id, reg.ship_class, reg.model, reg.port, reg.context_budget))
    print(f"[mothership] Registered: {reg.ship_id} ({reg.ship_class} / {reg.model})")
    return {"status": "registered", "ship_id": reg.ship_id}


@app.post("/api/fleet/heartbeat")
def heartbeat(hb: Heartbeat):
    with db() as conn:
        rows = conn.execute("""
            UPDATE active_ships
            SET last_heartbeat = datetime('now'),
                status         = ?,
                context_used   = ?
            WHERE id = ?
        """, (hb.status, hb.context_used, hb.ship_id)).rowcount
    if rows == 0:
        raise HTTPException(status_code=404, detail=f"Ship {hb.ship_id} not found")
    return {"acknowledged": True}


@app.get("/api/fleet/status")
def fleet_status():
    with db() as conn:
        ships = conn.execute("""
            SELECT s.*, t.type as task_type, t.prompt as task_prompt
            FROM active_ships s
            LEFT JOIN tasks t ON s.task_id = t.id
            ORDER BY s.registered_at ASC
        """).fetchall()
    return {"ships": [dict(s) for s in ships]}


@app.get("/api/fleet/{ship_id}")
def get_ship(ship_id: str):
    with db() as conn:
        ship = conn.execute(
            "SELECT * FROM active_ships WHERE id = ?", (ship_id,)
        ).fetchone()
    if not ship:
        raise HTTPException(status_code=404, detail="Ship not found")
    return dict(ship)


# ── Task management ───────────────────────────────────────────────────────────

@app.post("/api/tasks", status_code=201)
def create_task(task: TaskCreate):
    task_id = str(uuid.uuid4())[:8]
    with db() as conn:
        conn.execute("""
            INSERT INTO tasks (id, type, prompt, priority, sector_id)
            VALUES (?, ?, ?, ?, ?)
        """, (task_id, task.type, task.prompt, task.priority, task.sector_id))
    return {"task_id": task_id, "status": "queued"}


@app.get("/api/fleet/{ship_id}/orders")
def get_orders(ship_id: str):
    """
    Atomically claim the highest-priority queued task for this ship.
    Returns {"orders": null} if the queue is empty.
    """
    with db() as conn:
        task = conn.execute("""
            SELECT t.*, a.filepath as sector_path
            FROM tasks t
            LEFT JOIN asteroids_map a ON t.sector_id = a.id
            WHERE t.status = 'queued'
            ORDER BY t.priority DESC, t.created_at ASC
            LIMIT 1
        """).fetchone()

        if not task:
            return {"orders": None}

        claimed = conn.execute("""
            UPDATE tasks
            SET status      = 'assigned',
                assigned_to = ?,
                assigned_at = datetime('now')
            WHERE id = ? AND status = 'queued'
        """, (ship_id, task["id"])).rowcount

        if claimed == 0:
            return {"orders": None}

        conn.execute("""
            UPDATE active_ships SET status = 'working', task_id = ?
            WHERE id = ?
        """, (task["id"], ship_id))

    return {"orders": dict(task)}


# Phase 10B compat: assign orders to a specific ship by container_id
@app.post("/api/fleet/{ship_id}/orders/assign", status_code=201)
def assign_order(ship_id: str, order: OrderAssign):
    """Create a high-priority task visible to a specific ship."""
    task_id = str(uuid.uuid4())[:8]
    with db() as conn:
        conn.execute("""
            INSERT INTO tasks (id, type, prompt, priority, sector_id)
            VALUES (?, ?, ?, 9, ?)
        """, (task_id, order.type, order.prompt, ship_id))
    return {"task_id": task_id, "status": "queued", "assigned_hint": ship_id}


@app.post("/api/fleet/complete")
def complete_task(body: TaskComplete):
    with db() as conn:
        conn.execute("""
            UPDATE tasks
            SET status       = 'done',
                result       = ?,
                cost_usd     = ?,
                completed_at = datetime('now')
            WHERE id = ?
        """, (body.result, body.cost_usd, body.task_id))
        conn.execute("""
            UPDATE active_ships
            SET status  = 'idle',
                task_id = NULL
            WHERE id = ?
        """, (body.ship_id,))
        conn.execute("DELETE FROM file_locks WHERE held_by = ?", (body.ship_id,))
    return {"status": "acknowledged", "task_id": body.task_id}


@app.get("/api/tasks")
def list_tasks(status: Optional[str] = None, limit: int = 50):
    with db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
    return {"tasks": [dict(r) for r in rows]}


# ── File locking ──────────────────────────────────────────────────────────────

@app.post("/api/locks/acquire")
def acquire_lock(req: LockRequest):
    expires = (datetime.utcnow() + timedelta(minutes=req.ttl_minutes)).isoformat()
    with db() as conn:
        conn.execute("DELETE FROM file_locks WHERE expires_at < datetime('now')")
        existing = conn.execute(
            "SELECT held_by, expires_at FROM file_locks WHERE filepath = ?",
            (req.filepath,)
        ).fetchone()

        if existing:
            if existing["held_by"] == req.ship_id:
                conn.execute(
                    "UPDATE file_locks SET expires_at = ? WHERE filepath = ?",
                    (expires, req.filepath)
                )
                return {"locked": True, "renewed": True, "filepath": req.filepath}
            raise HTTPException(
                status_code=409,
                detail={
                    "error":    "FILE_LOCKED",
                    "filepath": req.filepath,
                    "held_by":  existing["held_by"],
                    "expires":  existing["expires_at"],
                }
            )

        conn.execute("""
            INSERT INTO file_locks (filepath, held_by, expires_at)
            VALUES (?, ?, ?)
        """, (req.filepath, req.ship_id, expires))

    return {"locked": True, "filepath": req.filepath, "expires_at": expires}


@app.delete("/api/locks/{filepath:path}")
def release_lock(filepath: str, ship_id: str = Query(...)):
    with db() as conn:
        deleted = conn.execute(
            "DELETE FROM file_locks WHERE filepath = ? AND held_by = ?",
            (filepath, ship_id)
        ).rowcount
    return {"released": deleted > 0, "filepath": filepath}


@app.get("/api/locks")
def list_locks():
    with db() as conn:
        conn.execute("DELETE FROM file_locks WHERE expires_at < datetime('now')")
        locks = conn.execute("SELECT * FROM file_locks").fetchall()
    return {"locks": [dict(l) for l in locks]}


# Phase 10B compat: lock/unlock by sector_id
@app.post("/api/fleet/lock/{sector_id}")
def lock_sector(sector_id: str, ship_id: str = Query(...)):
    return acquire_lock(LockRequest(ship_id=ship_id, filepath=sector_id))


@app.delete("/api/fleet/lock/{sector_id}")
def unlock_sector(sector_id: str, ship_id: str = Query(...)):
    return release_lock(filepath=sector_id, ship_id=ship_id)


# ── Escape pods ───────────────────────────────────────────────────────────────

@app.post("/api/escape_pods/eject", status_code=201)
def eject_escape_pod(pod: EscapePodPayload):
    pod_id = str(uuid.uuid4())
    with db() as conn:
        conn.execute("""
            INSERT INTO escape_pods
                (id, from_ship_id, task_id, partial_output,
                 context_window, next_steps, last_file, last_line)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (pod_id, pod.from_ship_id, pod.task_id, pod.partial_output,
              pod.context_window, pod.next_steps, pod.last_file, pod.last_line))

        if pod.task_id:
            conn.execute(
                "UPDATE tasks SET status = 'interrupted' WHERE id = ?",
                (pod.task_id,)
            )
        conn.execute(
            "UPDATE active_ships SET status = 'dead' WHERE id = ?",
            (pod.from_ship_id,)
        )
        conn.execute(
            "DELETE FROM file_locks WHERE held_by = ?", (pod.from_ship_id,)
        )

    print(f"[mothership] Escape pod ejected: {pod.from_ship_id} -> pod {pod_id[:8]}")
    return {"pod_id": pod_id, "status": "ejected"}


# Phase 10B compat route
@app.post("/api/pods/eject", status_code=201)
def eject_pod_compat(pod: EscapePodPayload):
    return eject_escape_pod(pod)


@app.get("/api/escape_pods/unclaimed")
def list_unclaimed_pods():
    with db() as conn:
        pods = conn.execute("""
            SELECT ep.*, t.type as task_type, t.prompt as task_prompt
            FROM escape_pods ep
            LEFT JOIN tasks t ON ep.task_id = t.id
            WHERE ep.recovered_by IS NULL
            ORDER BY ep.ejected_at DESC
        """).fetchall()
    return {"pods": [dict(p) for p in pods], "count": len(pods)}


@app.post("/api/escape_pods/{pod_id}/recover")
def recover_pod(pod_id: str, ship_id: str = Query(...)):
    with db() as conn:
        pod = conn.execute(
            "SELECT * FROM escape_pods WHERE id = ?", (pod_id,)
        ).fetchone()

        if not pod:
            raise HTTPException(status_code=404, detail="Escape pod not found")
        if pod["recovered_by"] is not None:
            raise HTTPException(status_code=409, detail="Pod already recovered")

        conn.execute("""
            UPDATE escape_pods
            SET recovered_by  = ?,
                recovered_at  = datetime('now')
            WHERE id = ?
        """, (ship_id, pod_id))

        if pod["task_id"]:
            conn.execute("""
                UPDATE tasks
                SET status      = 'assigned',
                    assigned_to = ?,
                    assigned_at = datetime('now')
                WHERE id = ?
            """, (ship_id, pod["task_id"]))

    # Phase 12: restore dead ship's cockpit into salvage ship's cockpit
    cockpit_restored = 0
    dead_callsign    = pod["from_ship_id"] if "from_ship_id" in pod.keys() else "unknown"
    try:
        payload  = json.loads(pod["context_window"] or "{}")
        cockpit_history = payload.get("cockpit", [])
        if cockpit_history:
            from memory.cockpit import append_cockpit
            for msg in cockpit_history:
                append_cockpit(
                    ship_id,
                    msg.get("role", "assistant"),
                    f"[RECOVERED FROM {dead_callsign}] {msg.get('content', '')}",
                )
            cockpit_restored = len(cockpit_history)
    except Exception as exc:
        print(f"[mothership] Cockpit restore error: {exc}")

    print(f"[mothership] Pod {pod_id[:8]} recovered by {ship_id} "
          f"(+{cockpit_restored} cockpit messages)")
    return {
        "pod":              dict(pod),
        "status":           "context_restored",
        "cockpit_restored": cockpit_restored,
    }


# Phase 10B compat route
@app.post("/api/pods/{pod_id}/recover")
def recover_pod_compat(pod_id: str, ship_id: str = Query(None)):
    sid = ship_id or "unknown"
    return recover_pod(pod_id=pod_id, ship_id=sid)


# ── Intelligence ──────────────────────────────────────────────────────────────

@app.post("/api/intelligence", status_code=201)
def store_intelligence(body: IntelligencePost):
    intel_id = str(uuid.uuid4())
    with db() as conn:
        conn.execute("""
            INSERT INTO intelligence (id, topic, source_ship, content, related_task_id)
            VALUES (?, ?, ?, ?, ?)
        """, (intel_id, body.topic, body.ship_id, body.content, body.task_id))
    return {"id": intel_id, "stored": True}


@app.get("/api/intelligence")
def get_intelligence(topic: Optional[str] = None, limit: int = 20):
    with db() as conn:
        if topic:
            rows = conn.execute("""
                SELECT * FROM intelligence
                WHERE topic LIKE ?
                ORDER BY created_at DESC LIMIT ?
            """, (f"%{topic}%", limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM intelligence
                ORDER BY created_at DESC LIMIT ?
            """, (limit,)).fetchall()
    return {"results": [dict(r) for r in rows], "count": len(rows)}


# ── Codebase map (asteroids) ──────────────────────────────────────────────────

@app.post("/api/map/scan")
def scan_sector(root_path: str = Query(...), ship_id: str = Query(...)):
    """Walk a directory and populate asteroids_map."""
    import os as _os
    scanned = 0
    with db() as conn:
        for dirpath, _, filenames in _os.walk(root_path):
            for fname in filenames:
                fpath = _os.path.join(dirpath, fname)
                ext   = _os.path.splitext(fname)[1].lstrip(".")
                try:
                    size  = _os.path.getsize(fpath)
                    mtime = datetime.utcfromtimestamp(
                        _os.path.getmtime(fpath)
                    ).isoformat()
                except OSError:
                    continue
                conn.execute("""
                    INSERT INTO asteroids_map
                        (id, filepath, filetype, size_bytes, last_scanned, last_modified)
                    VALUES (lower(hex(randomblob(8))), ?, ?, ?, datetime('now'), ?)
                    ON CONFLICT(filepath) DO UPDATE SET
                        size_bytes    = excluded.size_bytes,
                        last_scanned  = datetime('now'),
                        last_modified = excluded.last_modified
                """, (fpath, ext, size, mtime))
                scanned += 1
    return {"scanned": scanned, "root": root_path}


@app.get("/api/map")
def get_map(threat_min: int = 0, locked_only: bool = False):
    with db() as conn:
        if locked_only:
            rows = conn.execute("""
                SELECT a.*, f.held_by, f.expires_at as lock_expires_at
                FROM asteroids_map a
                JOIN file_locks f ON a.filepath = f.filepath
                WHERE a.threat_level >= ?
            """, (threat_min,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM asteroids_map
                WHERE threat_level >= ?
                ORDER BY threat_level DESC, filepath ASC
            """, (threat_min,)).fetchall()
    return {"files": [dict(r) for r in rows], "count": len(rows)}


# ── Entropy System (Phase 11) ─────────────────────────────────────────────────

class EntropyEngageBody(BaseModel):
    ship_id:   str          # player ship initiating combat
    enemy_id:  str          # entropy fleet unit ID
    sector:    Optional[str] = None

class StationBody(BaseModel):
    ship_id:            str
    assigned_directory: str

class ResearchTrigger(BaseModel):
    node_id: str


@app.get("/api/entropy/fleet")
def get_entropy_fleet(status: Optional[str] = None):
    """List entropy (enemy) fleet units."""
    with db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM entropy_fleet WHERE status = ? ORDER BY occupation_start DESC",
                (status,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM entropy_fleet ORDER BY occupation_start DESC"
            ).fetchall()
    return {"fleet": [dict(r) for r in rows], "count": len(rows)}


@app.get("/api/entropy/occupied")
def get_occupied_sectors():
    """List sectors currently under entropy occupation with throttle info."""
    with db() as conn:
        rows = conn.execute("""
            SELECT e.assigned_sector, e.ship_class, e.hp, e.id AS enemy_id,
                   a.threat_level, a.size_bytes
            FROM entropy_fleet e
            LEFT JOIN asteroids_map a ON a.filepath = e.assigned_sector
            WHERE e.status = 'active'
            ORDER BY e.ship_class DESC, a.threat_level DESC
        """).fetchall()
    return {"occupied": [dict(r) for r in rows], "count": len(rows)}


@app.post("/api/entropy/engage")
def engage_entropy_unit(body: EntropyEngageBody):
    """
    Initiate combat between a player ship and an entropy unit.
    Outcome is calculated from ship class vs enemy class.
    Mutual destruction (capital duels) is supported.
    """
    with db() as conn:
        player = conn.execute(
            "SELECT * FROM active_ships WHERE id = ?", (body.ship_id,)
        ).fetchone()
        enemy = conn.execute(
            "SELECT * FROM entropy_fleet WHERE id = ? AND status = 'active'",
            (body.enemy_id,)
        ).fetchone()

        if not player:
            raise HTTPException(status_code=404, detail="Player ship not found")
        if not enemy:
            raise HTTPException(status_code=404, detail="Entropy unit not found or already destroyed")

        p_class = (player["class"] or "interceptor").lower()
        e_class = (enemy["ship_class"] or "entropy_scout").lower()

        # Capital duel: mutual destruction
        if "destroyer" in p_class and "destroyer" in e_class:
            conn.execute(
                "UPDATE entropy_fleet SET status = 'destroyed' WHERE id = ?",
                (body.enemy_id,)
            )
            conn.execute(
                "UPDATE active_ships SET status = 'dead' WHERE id = ?",
                (body.ship_id,)
            )
            outcome = "mutual_destruction"
            message = ("Capital ship duel resolved: mutual destruction. "
                       "Sector refined. Player Destroyer context wiped.")
        # Player wins: heavier class or same tier
        elif (("destroyer" in p_class) or
              ("frigate" in p_class and "interceptor" in e_class) or
              ("interceptor" in p_class and "scout" in e_class)):
            conn.execute(
                "UPDATE entropy_fleet SET status = 'destroyed' WHERE id = ?",
                (body.enemy_id,)
            )
            # Reduce threat level in asteroids_map for this sector
            conn.execute(
                "UPDATE asteroids_map SET threat_level = MAX(0, threat_level - 1) "
                "WHERE filepath = ?",
                (enemy["assigned_sector"],)
            )
            outcome = "player_victory"
            message = f"Player {p_class} destroyed {e_class}. Sector {enemy['assigned_sector'][-40:]} cleared."
        else:
            # Enemy wins: player ship takes damage (reduce armor in registry)
            outcome = "enemy_victory"
            message = (f"{e_class} repelled {p_class}. "
                       "Reinforce with escort before re-engaging.")

    print(f"[mothership] Combat: {body.ship_id} vs {body.enemy_id} -> {outcome}")
    return {
        "outcome":  outcome,
        "message":  message,
        "enemy_id": body.enemy_id,
        "sector":   body.sector or (dict(enemy)["assigned_sector"] if enemy else None),
    }


@app.post("/api/fleet/{ship_id}/station")
def station_ship(ship_id: str, body: StationBody):
    """Assign a ship's permanent home sector (directory)."""
    with db() as conn:
        ship = conn.execute(
            "SELECT id FROM active_ships WHERE id = ?", (ship_id,)
        ).fetchone()
        if not ship:
            raise HTTPException(status_code=404, detail="Ship not found")
        conn.execute(
            "UPDATE active_ships SET assigned_directory = ? WHERE id = ?",
            (body.assigned_directory, ship_id)
        )
    return {"ship_id": ship_id, "assigned_directory": body.assigned_directory}


@app.get("/api/fleet/{ship_id}/specialization")
def get_specialization(ship_id: str):
    """Return a ship's current context domain tags."""
    with db() as conn:
        row = conn.execute(
            "SELECT id, class, assigned_directory, specialization_vector FROM active_ships WHERE id = ?",
            (ship_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Ship not found")
    spec = None
    if row["specialization_vector"]:
        try:
            spec = json.loads(row["specialization_vector"])
        except Exception:
            spec = row["specialization_vector"]
    return {
        "ship_id":            ship_id,
        "ship_class":         row["class"],
        "assigned_directory": row["assigned_directory"],
        "specialization":     spec,
    }


# ── Tech Tree ─────────────────────────────────────────────────────────────────

@app.get("/api/techtree")
def get_tech_tree():
    """Return full tech tree state."""
    with db() as conn:
        rows = conn.execute("SELECT * FROM tech_tree ORDER BY node_id").fetchall()
    nodes = []
    for r in rows:
        d = dict(r)
        try:
            d["requires"] = json.loads(d.get("requires_json") or "[]")
        except Exception:
            d["requires"] = []
        nodes.append(d)
    return {"nodes": nodes}


@app.post("/api/techtree/research")
def trigger_research(body: ResearchTrigger):
    """Manually trigger synthesis for a tech-tree node."""
    import sys, os as _os
    sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from research_substation import synthesize_node
    result = synthesize_node(body.node_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.get("/api/techtree/unlocked")
def get_unlocked_ships():
    """Return list of ship classes currently buildable given tech tree state."""
    # Base tier is always available
    unlocked = ["harvester", "light_interceptor", "scout"]
    with db() as conn:
        completed = {r["node_id"] for r in conn.execute(
            "SELECT node_id FROM tech_tree WHERE status = 'completed'"
        )}
    if "ast_mapping" in completed:
        unlocked += ["interceptor"]
    if "database_schema_understanding" in completed:
        unlocked += ["assault_frigate"]
    if "auth_flow_mapping" in completed:
        unlocked += ["carrier"]
    if "full_system_context" in completed:
        unlocked += ["destroyer"]
    return {"unlocked_classes": unlocked, "completed_nodes": list(completed)}
