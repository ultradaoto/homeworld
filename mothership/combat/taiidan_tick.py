"""
Taiidan auto-spawn tick — the enemy AI that never sleeps.

Every TICK_INTERVAL seconds, evaluate the sector map for vulnerable targets:
  - High-threat scouted sectors → send scouts or bombers
  - No known threats → send probes into the fog

Escalation:
  threat_level 1-3 → taiidan_scout
  threat_level 4+  → taiidan_bomber
  fog / unknown    → taiidan_probe
"""
import threading
import time

from memory import store

TICK_INTERVAL    = 45    # seconds between threat evaluations
MAX_INBOUND      = 6     # don't flood the map
_running         = False


def start():
    global _running
    _running = True
    threading.Thread(target=_loop, daemon=True).start()
    print(f"[taiidan] Threat AI active — evaluating every {TICK_INTERVAL}s.")


def _loop():
    # First tick after a delay so the player can orient themselves
    time.sleep(TICK_INTERVAL)
    while _running:
        _evaluate()
        time.sleep(TICK_INTERVAL)


def _evaluate():
    sectors = store.get("sector_map", {})
    if len(sectors) < 3:
        return   # map not indexed yet — Taiidan stand down

    enemies = store.get("enemy_fleet", [])
    inbound = sum(1 for e in enemies if e["status"] == "inbound")
    if inbound >= MAX_INBOUND:
        return

    from combat.enemy_ai import spawn_enemy_scout
    from map.sector_map  import get_nearest_unscouted

    # Target scouted sectors with threat >= 2 first (escalation priority)
    threat_sectors = sorted(
        [s for s in sectors.values()
         if s.get("scouted") and s.get("threat_level", 0) >= 2],
        key=lambda s: s["threat_level"],
        reverse=True,
    )

    spawned = 0
    for sector in threat_sectors[:2]:
        if inbound + spawned >= MAX_INBOUND:
            break
        tl        = sector["threat_level"]
        ship_type = "taiidan_bomber" if tl >= 4 else "taiidan_scout"
        e = spawn_enemy_scout(tl, sector["id"], enemy_type=ship_type)
        print(f"[taiidan] {e['id']} ({ship_type}) inbound → {sector['rel_path']}")
        spawned += 1

    if spawned == 0:
        # No known threats — probe into fog
        unscouted = get_nearest_unscouted({"x": 20000, "y": 0, "z": 20000}, n=1)
        for s in unscouted[:1]:
            if inbound < MAX_INBOUND:
                e = spawn_enemy_scout(1, s["id"], enemy_type="taiidan_probe")
                print(f"[taiidan] FOG PROBE {e['id']} → {s['rel_path']}")
