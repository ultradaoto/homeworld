"""
Combat event watcher — monitors combat_event.json written by the C engine.

When two ships enter weapon range in-game, the C engine writes the file.
Python reads it within 0.5s, runs the LLM combat sequence, and writes
back the result (ship HP commands) to the bridge.
"""
import json
import os
import threading
import time

from config import STATE_DIR

COMBAT_FILE       = os.path.join(STATE_DIR, "combat_event.json")
_running          = False
_processed_events: set = set()   # hash-based dedup — C engine writes, Python owns delete


def start():
    global _running
    _running = True
    threading.Thread(target=_watch, daemon=True).start()
    print("[combat] Event watcher active — listening for engagements.")


def _watch():
    global _processed_events
    while _running:
        time.sleep(0.5)
        if not os.path.exists(COMBAT_FILE):
            continue
        try:
            with open(COMBAT_FILE) as f:
                raw = f.read()
            event_hash = hash(raw)
            if event_hash in _processed_events:
                # Already handled — C engine hasn't refreshed the file yet; skip
                continue
            event = json.loads(raw)
            # Python owns the delete — C engine must never remove this file
            os.remove(COMBAT_FILE)
            _processed_events.add(event_hash)
            if len(_processed_events) > 100:
                _processed_events.clear()
        except (OSError, json.JSONDecodeError):
            continue

        player_ref = event.get("player_ship", "")
        enemy_id   = event.get("enemy_id",    "")
        sector_id  = event.get("sector_id",   "")

        if not player_ref or not enemy_id:
            continue

        print(f"\n[COMBAT] Engagement detected: {player_ref} vs {enemy_id}")

        try:
            from combat.battle import resolve_combat
            result = resolve_combat(player_ref, enemy_id, sector_id)
            winner = result.get("winner", "?")
            label  = "PLAYER WINS" if winner == "player" else "ENEMY WINS"
            print(f"[RESULT] {label}: {result.get('message', '')}")
        except Exception as exc:
            print(f"[combat watcher] resolve error: {exc}")
