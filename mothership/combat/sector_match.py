"""
sector_match.py — Sector effectiveness scoring (Phase 11 / Target B).

Computes how well a ship's specialization vector matches a target sector,
so the fleet dispatcher can route the most capable ships to each file.

Public API:
    infer_sector_tags(filepath) -> list[str]
    effectiveness_score(ship_specialization, sector_tags) -> float
"""
import os
import re

# ── Tag inference rules ────────────────────────────────────────────────────────

# Maps filename / path patterns to domain tags
_PATH_TAG_RULES: list[tuple[str, str]] = [
    # Security / auth
    (r"auth",          "auth"),
    (r"login",         "auth"),
    (r"session",       "auth"),
    (r"token",         "auth"),
    (r"password",      "security"),
    (r"crypto",        "security"),
    (r"hash",          "security"),
    # Database
    (r"db",            "database"),
    (r"model",         "database"),
    (r"schema",        "database"),
    (r"migrat",        "database"),
    (r"sqlite",        "database"),
    # API / networking
    (r"api",           "api"),
    (r"server",        "api"),
    (r"router",        "api"),
    (r"endpoint",      "api"),
    (r"http",          "api"),
    (r"websocket",     "api"),
    # Frontend / UI
    (r"component",     "frontend"),
    (r"view",          "frontend"),
    (r"template",      "frontend"),
    (r"\.tsx?$",       "frontend"),
    (r"\.jsx?$",       "frontend"),
    # Testing
    (r"test",          "testing"),
    (r"spec",          "testing"),
    (r"mock",          "testing"),
    # Configuration / infrastructure
    (r"config",        "config"),
    (r"setting",       "config"),
    (r"env",           "config"),
    (r"docker",        "infra"),
    (r"ci",            "infra"),
    (r"deploy",        "infra"),
    (r"Makefile",      "infra"),
    # Combat / game logic (Homeworld-specific)
    (r"combat",        "combat"),
    (r"battle",        "combat"),
    (r"fleet",         "combat"),
    (r"entropy",       "combat"),
    (r"research",      "research"),
    (r"intel",         "research"),
    (r"memory",        "memory"),
    (r"cockpit",       "memory"),
]

# Content snippet patterns → tags (checked against first ~2 KB of file)
_CONTENT_TAG_RULES: list[tuple[str, str]] = [
    (r"import\s+anthropic",      "ai"),
    (r"client\.messages",        "ai"),
    (r"sqlite3",                 "database"),
    (r"sqlalchemy",              "database"),
    (r"fastapi|flask|django",    "api"),
    (r"jwt|oauth",               "auth"),
    (r"subprocess",              "infra"),
    (r"threading|asyncio",       "concurrency"),
    (r"numpy|pandas",            "data_science"),
]


def infer_sector_tags(filepath: str) -> list[str]:
    """
    Infer domain tags from a file path and its opening content.
    Returns a deduplicated list of lowercase tag strings.
    """
    tags: set[str] = set()
    lower_path = filepath.replace("\\", "/").lower()

    for pattern, tag in _PATH_TAG_RULES:
        if re.search(pattern, lower_path, re.IGNORECASE):
            tags.add(tag)

    # Peek at file content
    try:
        with open(filepath, encoding="utf-8", errors="replace") as fh:
            snippet = fh.read(2048).lower()
        for pattern, tag in _CONTENT_TAG_RULES:
            if re.search(pattern, snippet, re.IGNORECASE):
                tags.add(tag)
    except OSError:
        pass

    # Filetype fallback
    ext = os.path.splitext(filepath)[1].lower()
    if ext in (".py",):
        tags.add("python")
    elif ext in (".ts", ".tsx", ".js", ".jsx"):
        tags.add("frontend")
    elif ext in (".sql",):
        tags.add("database")
    elif ext in (".sh", ".bat", ".ps1"):
        tags.add("infra")

    return sorted(tags)


# ── SQLite-backed specialization (Phase 12) ────────────────────────────────────

def load_ship_tags(ship_id: str) -> list[str]:
    """Load a ship's specialization vector from fleet.db active_ships."""
    import json as _json
    import sqlite3 as _sq
    from memory.store import DB_PATH
    try:
        con = _sq.connect(DB_PATH)
        con.row_factory = _sq.Row
        row = con.execute(
            "SELECT specialization_vector FROM active_ships WHERE short_id = ?",
            (ship_id,),
        ).fetchone()
        con.close()
        if row and row["specialization_vector"]:
            return _json.loads(row["specialization_vector"])
    except Exception:
        pass
    return []


def set_ship_specialization(ship_id: str, sector_path: str) -> list[str]:
    """
    Infer domain tags from sector_path and write them to active_ships.specialization_vector.
    Also writes assigned_directory. Returns the tag list.
    """
    import json as _json
    import sqlite3 as _sq
    from memory.store import DB_PATH
    tags = infer_sector_tags(sector_path)
    try:
        con = _sq.connect(DB_PATH)
        con.execute(
            "UPDATE active_ships "
            "SET specialization_vector = ?, assigned_directory = ? "
            "WHERE short_id = ?",
            (_json.dumps(tags), sector_path, ship_id),
        )
        con.commit()
        con.close()
    except Exception:
        pass
    return tags


def effectiveness_score_by_id(ship_id: str, target_sector: str) -> float:
    """
    Convenience wrapper: look up ship tags from SQLite, score against sector.
    Returns 0.2–1.0.  Unspecialized ships return 0.5.
    """
    ship_tags   = load_ship_tags(ship_id)
    sector_tags = infer_sector_tags(target_sector)
    if not ship_tags:
        return 0.5
    overlap = len(set(ship_tags) & set(sector_tags))
    return round(max(0.2, min(1.0, overlap / max(len(sector_tags), 1))), 2)


def specialization_report(ship_id: str, target_sector: str) -> dict:
    """Full breakdown for TUI display."""
    ship_tags   = load_ship_tags(ship_id)
    sector_tags = infer_sector_tags(target_sector)
    score       = effectiveness_score_by_id(ship_id, target_sector)
    matched     = sorted(set(ship_tags) & set(sector_tags))
    mismatched  = sorted(set(sector_tags) - set(ship_tags))
    verdict = (
        "OPTIMAL"    if score >= 0.8 else
        "ACCEPTABLE" if score >= 0.5 else
        "PENALIZED"  if score >= 0.3 else
        "MISMATCH — reassign this ship"
    )
    return {
        "score":       score,
        "ship_tags":   ship_tags,
        "sector_tags": sector_tags,
        "matched":     matched,
        "mismatched":  mismatched,
        "verdict":     verdict,
    }


def effectiveness_score(
    ship_specialization: list[str] | str,
    sector_tags: list[str],
) -> float:
    """
    Return a [0.0, 1.0] score representing how well the ship's specialization
    overlaps with the sector's domain tags.

    ship_specialization: list of tag strings, or a JSON-encoded list string.
    sector_tags:         list of tag strings from infer_sector_tags().

    Scoring:
      - Each matching tag contributes 1 point
      - Score = matches / max(len(ship_tags), len(sector_tags), 1)
      - Returns 0.5 (neutral) when the ship has no specialization
    """
    import json as _json

    if isinstance(ship_specialization, str):
        try:
            ship_tags = _json.loads(ship_specialization)
        except (_json.JSONDecodeError, TypeError):
            ship_tags = [t.strip() for t in ship_specialization.split(",") if t.strip()]
    else:
        ship_tags = list(ship_specialization or [])

    if not ship_tags:
        return 0.5  # unspecialized ship — neutral effectiveness

    ship_set   = set(t.lower() for t in ship_tags)
    sector_set = set(t.lower() for t in sector_tags)
    matches    = len(ship_set & sector_set)
    denom      = max(len(ship_set), len(sector_set), 1)
    return round(matches / denom, 4)
