"""
Tactics map directly to LLM generation parameters:

AGGRESSIVE  → high temperature (0.9), creative, risky
NEUTRAL     → balanced temperature (0.6), default
EVASIVE     → low temperature (0.2), strict, verified
"""

TACTICS = {
    "aggressive": {"temperature": 0.9, "verify": False},
    "neutral":    {"temperature": 0.6, "verify": False},
    "evasive":    {"temperature": 0.2, "verify": True},
}

_current = "neutral"


def set_tactic(name: str):
    global _current
    if name in TACTICS:
        _current = name
        from memory import store
        store.set("current_tactic", name)


def get_params() -> dict:
    return TACTICS.get(_current, TACTICS["neutral"])


def get_current() -> str:
    return _current
