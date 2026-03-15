"""
Bridge state file writer — Phase 10A: SQLite as source of truth.

Reads active_ships, asteroids_map, escape_pods, enemy_fleet from fleet.db
and writes the JSON files the C engine polls every second.

fleet_state.json    ← ship roster, RU balance, objective
ship_overlays.json  ← per-ship display info for Sensors Manager panel
sector_map.json     ← sector positions, threat colors, escape pod beacons
"""
import json
import os
import time
import threading

from memory import store
from config import STATE_DIR

STATE_FILE   = os.path.join(STATE_DIR, "fleet_state.json")
ENGINE_FILE  = os.path.join(STATE_DIR, "engine_state.json")
CMD_FILE     = os.path.join(STATE_DIR, "engine_cmd.json")
OVERLAY_FILE = os.path.join(STATE_DIR, "ship_overlays.json")
SECTOR_FILE  = os.path.join(STATE_DIR, "sector_map.json")

STALE_SECONDS = 5

_running = False


def start():
    global _running
    _running = True
    os.makedirs(STATE_DIR, exist_ok=True)
    threading.Thread(target=_write_loop, daemon=True).start()


def _write_loop():
    while _running:
        try:
            fleet   = store.get_fleet_roster_for_engine()
            sectors = store.get_code_map_for_engine()
            pods    = store.get_escape_pods_for_engine()
            enemies = store.get_enemy_fleet_for_engine()
        except Exception:
            # DB not ready yet — fall back to in-memory store
            fleet   = store.get("active_agents", [])
            sectors = []
            pods    = []
            enemies = store.get("enemy_fleet", [])

        ru  = store.get("ru_balance", 0)
        obj = store.get("current_objective", "")

        state = {
            "ru_balance":     ru,
            "objective":      obj,
            "ship_count":     len(fleet),
            "ships":          fleet,
            "sector_count":   len(sectors),
            "enemy_count":    len(enemies),
            "escape_pods":    len(pods),
            "findings":       store.get("findings_count", 0),
            "timestamp":      time.time(),
        }
        _atomic_write(STATE_FILE, state)
        _write_overlays(fleet, enemies)
        _write_sector_map(sectors, pods)
        time.sleep(1.0)


def _atomic_write(path: str, data):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, separators=(",", ":"))
        os.replace(tmp, path)
    except OSError:
        pass


# ── Threat color helpers ───────────────────────────────────────────────────

_THREAT_COLORS = [
    (150, 150, 200, 30),    # 0 — clear
    (160, 160, 100, 40),    # 1 — low
    (180, 120,  60, 60),    # 2 — moderate
    (200,  80,  40, 80),    # 3 — elevated
    (220,  40,  40, 110),   # 4 — high
    (160,   0, 200, 140),   # 5 — critical
]


def _threat_color(t: int) -> tuple:
    return _THREAT_COLORS[min(max(t, 0), 5)]


def _threat_density(t: int) -> float:
    return [0.05, 0.15, 0.30, 0.50, 0.70, 0.90][min(t, 5)]


# ── Per-ship overlay writer ────────────────────────────────────────────────

def _write_overlays(fleet: list, enemies: list):
    overlays = []
    for ship in fleet:
        if not isinstance(ship, dict):
            continue
        overlays.append({
            "short_id":    ship.get("short_id", ship.get("ship_id", ""))[:16],
            "callsign":    ship.get("callsign", "")[:32],
            "ship_type":   ship.get("class",    ship.get("type", ""))[:24],
            "status":      ship.get("status",   "idle")[:16],
            "sector":      ship.get("current_task", "")[:48],
            "task":        ship.get("last_action", "idle")[:60],
            "ctx_used":    ship.get("ctx_used",  0),
            "ctx_budget":  ship.get("ctx_budget", 8000),
            "armor":       ship.get("armor",     100),
            "max_armor":   ship.get("max_armor", 100),
            "kills":       ship.get("kills",     0),
            "model":       ship.get("model",     "")[:32],
            "container":   ship.get("container_id", "")[:32],
            "ru_burn":     ship.get("ru_burn_rate", 0.0),
        })
    for e in enemies:
        if not isinstance(e, dict):
            continue
        eid = e.get("enemy_id", "")
        overlays.append({
            "short_id":  eid[:16],
            "callsign":  eid[:32],
            "ship_type": e.get("enemy_type", "taiidan")[:24],
            "status":    e.get("status", "inbound")[:16],
            "sector":    e.get("target_sector_id", "")[:48],
            "task":      "ATTACKING",
            "ctx_used":  0,
            "ctx_budget": 0,
            "armor":     50,
            "max_armor": 50,
            "kills":     0,
            "model":     "",
            "container": "",
            "ru_burn":   0.0,
        })
    _atomic_write(OVERLAY_FILE, overlays)


# ── Sector map writer ──────────────────────────────────────────────────────

def _write_sector_map(sectors: list, pods: list):
    data = []
    for s in sectors:
        if not isinstance(s, dict):
            continue
        t  = s.get("threat_level", 0)
        t5 = min(max(t, 0), 5)
        r, g, b, a = _threat_color(t5)
        radius = max(800, s.get("file_count", 1) * 120)
        data.append({
            "id":       s.get("sector_id", ""),
            "label":    (s.get("rel_path") or "?")[:28],
            "x":        s.get("pos_x", 0),
            "y":        s.get("pos_y", 0),
            "z":        s.get("pos_z", 0),
            "scouted":  s.get("scouted", 0),
            "threat":   t,
            "ru_value": s.get("ru_value", 0),
            "radius":   radius,
            "cloud_r":  r,
            "cloud_g":  g,
            "cloud_b":  b,
            "cloud_a":  a,
            "cloud_density": int(_threat_density(t5) * 100),
            "beacons":  s.get("beacon_count", 0),
            "locked":   1 if s.get("locked_by") else 0,
            "is_pod":   0,
        })
    # Append escape pod markers as golden beacons
    for p in pods:
        if not isinstance(p, dict):
            continue
        label = f"POD:{p.get('dead_ship_callsign','?')}"
        data.append({
            "id":       p.get("pod_id", "")[:8],
            "label":    label[:28],
            "x":        p.get("pos_x", 0),
            "y":        p.get("pos_y", 0),
            "z":        p.get("pos_z", 0),
            "scouted":  1,
            "threat":   0,
            "ru_value": 0,
            "radius":   400,
            "cloud_r":  255,
            "cloud_g":  200,
            "cloud_b":  0,
            "cloud_a":  200,
            "cloud_density": 80,
            "beacons":  1,
            "locked":   0,
            "is_pod":   1,
        })

    # Only write if we have data; fall back to sector_map.py output if empty
    if data:
        _atomic_write(SECTOR_FILE, data)


# ── Engine state reader ────────────────────────────────────────────────────

def read_engine_state():
    try:
        with open(ENGINE_FILE) as f:
            state = json.load(f)
        age = time.time() - state.get("timestamp", 0)
        if age > STALE_SECONDS:
            return None
        return state
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def write_command(cmd: dict):
    with open(CMD_FILE, "w") as f:
        json.dump(cmd, f)


def stop():
    global _running
    _running = False
