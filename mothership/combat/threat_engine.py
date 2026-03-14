import re
from bridge.state_file import write_command
from memory import store

THREAT_MAP = {
    r"SyntaxError":               {"enemy": "interceptor",   "severity": 1},
    r"NameError|AttributeError":  {"enemy": "fighter",       "severity": 1},
    r"ImportError|ModuleNotFound":{"enemy": "corvette",      "severity": 2},
    r"TypeError|ValueError":      {"enemy": "assault_frig",  "severity": 2},
    r"RuntimeError|RecursionError":{"enemy": "destroyer",    "severity": 3},
    r"MemoryError|OverflowError": {"enemy": "heavy_cruiser", "severity": 4},
    r"CRITICAL|FATAL":            {"enemy": "mothership",    "severity": 5},
}


def analyse_errors(error_text: str) -> list:
    threats = []
    for pattern, threat in THREAT_MAP.items():
        matches = re.findall(pattern, error_text, re.IGNORECASE)
        for _ in matches:
            threats.append({**threat, "pattern": pattern})
    return threats


def spawn_enemies(threats: list) -> int:
    current = store.get("enemy_threats", [])
    for t in threats:
        current.append(t)
        write_command({
            "type":   "spawn_enemy",
            "ship":   t["enemy"],
            "sev":    t["severity"],
            "reason": t["pattern"],
        })
    store.set("enemy_threats", current[-20:])
    return len(threats)


def resolve_enemy(pattern: str):
    current = store.get("enemy_threats", [])
    store.set("enemy_threats",
              [t for t in current if t["pattern"] != pattern])
    write_command({"type": "destroy_enemy", "pattern": pattern})


def enemy_count() -> int:
    return len(store.get("enemy_threats", []))
