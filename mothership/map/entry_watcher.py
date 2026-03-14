"""
entry_watcher.py — Automatic scout trigger on in-game sector entry (Phase 11 / Step 5b).

When a player ship enters a dark sector in-game, the C engine writes
state/sector_entry.json.  This watcher picks it up within 0.3 s and
fires the Python scout mission automatically — no TUI typing needed.

C → Python protocol (sector_entry.json):
    {
        "ship_id":   "<bridgeShortId>",
        "sector_id": "<8-char hex sector ID>",
        "ship_type": "<display name>"
    }

Start with:
    from map.entry_watcher import start
    start()
"""
import json
import os
import threading
import time

from config import STATE_DIR

ENTRY_FILE = os.path.join(STATE_DIR, "sector_entry.json")
_running   = False
_recent: set = set()   # (ship_id, sector_id) pairs — debounce duplicates


def start():
    global _running
    _running = True
    threading.Thread(target=_watch, daemon=True, name="entry_watcher").start()
    print("[entry_watcher] Sector entry auto-scout active.")


def _watch():
    global _recent
    while _running:
        time.sleep(0.3)
        if not os.path.exists(ENTRY_FILE):
            continue
        try:
            with open(ENTRY_FILE, encoding="utf-8") as f:
                event = json.load(f)
            os.remove(ENTRY_FILE)
        except (OSError, json.JSONDecodeError):
            continue

        ship_id   = event.get("ship_id", "")
        sector_id = event.get("sector_id", "")
        ship_type = event.get("ship_type", "unknown")

        if not ship_id or not sector_id:
            continue

        key = (ship_id, sector_id)
        if key in _recent:
            continue   # debounce — already triggered this entry
        _recent.add(key)
        if len(_recent) > 200:
            _recent.clear()

        # Resolve sector from in-memory map
        try:
            from map.sector_map  import get_sector_by_id
            from ships.registry  import get_ship
        except ImportError as exc:
            print(f"[entry_watcher] import error: {exc}")
            continue

        sector = get_sector_by_id(sector_id)
        ship   = get_ship(ship_id)

        if not sector:
            # Sector not in map yet — benign, map may not be indexed
            continue

        if sector.get("scouted"):
            continue   # already illuminated

        callsign = ship["callsign"] if ship else ship_id

        print(f"\n[AUTO-SCOUT] {callsign} ({ship_type}) entered "
              f"'{sector.get('rel_path', sector_id)}'")

        try:
            from map.scout_agent import scout_sector
            scout_sector(sector, ship_id if ship else None)
            print(f"[AUTO-SCOUT] Sector '{sector.get('rel_path', sector_id)}' illuminated.")
        except Exception as exc:
            print(f"[entry_watcher] scout error: {exc}")
