"""
Scout mission — runs when you fly a scout ship into a dark sector.

Reads every code file (asteroid) in the sector, produces per-file intel
via lightweight AST/regex scanning, then asks the LLM for a synthesized
sector briefing.  No LLM call per file — only one call per sector.
"""
import ast
import datetime
import os
import re

import anthropic

import config
from config import STATE_DIR
from map.sector_map import mark_scouted, place_beacon, write_map_for_engine
from memory import store
from ships.registry import get_ship, update_ship

client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

SCOUT_SYSTEM = """
You are a Scout ship reporting to Fleet Command.
You have just flown through a new sector of the codebase and your
sensors have illuminated the asteroids (files) within.

Give a direct tactical briefing covering:

1. WHAT'S HERE — file types, main purpose, scale
2. COMPLEXITY — simple / moderate / complex (one word + brief reason)
3. THREATS — security risks, missing tests, dangerous patterns, tech debt
4. RESOURCES — estimated RU yield from this sector
5. NEXT MOVE — which ship class to dispatch next and what task

Use fleet metaphors. Max 150 words. Tactical brevity.
End with exactly: THREAT LEVEL: <0-5>
"""

_THREAT_PATTERNS = [
    ("exec(",        "dynamic exec"),
    ("eval(",        "eval usage"),
    ("password",     "credential handling"),
    ("secret",       "secret in code"),
    ("SELECT *",     "raw SQL wildcard"),
    ("subprocess",   "shell execution"),
    ("pickle.loads", "unsafe deserialization"),
    ("os.system(",   "os.system call"),
    ("TODO",         "unresolved TODO"),
    ("FIXME",        "known bug (FIXME)"),
    ("HACK",         "hack in code"),
    ("unsafe",       "unsafe marker"),
]


def _scan_file(filepath: str) -> dict:
    """Static analysis of one file — no LLM."""
    result: dict = {
        "path": filepath, "lines": 0,
        "summary": "", "threat_hints": [],
    }
    try:
        with open(filepath, errors="ignore") as f:
            content = f.read(6000)
    except OSError as e:
        result["summary"] = f"[unreadable: {e}]"
        return result

    result["lines"] = content.count("\n") + 1
    ext = os.path.splitext(filepath)[1].lower()

    if ext == ".py":
        try:
            tree    = ast.parse(content)
            classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
            funcs   = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
            imports = []
            for n in ast.walk(tree):
                if isinstance(n, ast.Import):
                    imports += [a.name for a in n.names]
                elif isinstance(n, ast.ImportFrom) and n.module:
                    imports.append(n.module)
            result["summary"] = (
                f"{len(classes)} class{'es' if len(classes) != 1 else ''}, "
                f"{len(funcs)} func{'s' if len(funcs) != 1 else ''}. "
                f"Imports: {', '.join(imports[:6])}"
            )
        except SyntaxError:
            result["summary"] = f"{result['lines']}L [syntax error]"

    elif ext in (".c", ".h", ".cpp"):
        # Rough function count: lines with a '(' not starting with # or //
        func_sigs = [
            l.strip() for l in content.split("\n")
            if "(" in l and not l.strip().startswith(("#", "//", "*", "/*"))
            and ";" not in l and l.strip()
        ]
        result["summary"] = (
            f"{result['lines']}L, ~{len(func_sigs)} func signatures"
        )

    elif ext in (".js", ".ts", ".tsx", ".jsx"):
        exports = len(re.findall(r'\bexport\b', content))
        comps   = len(re.findall(r'(function|const|class)\s+[A-Z]\w*', content))
        result["summary"] = f"{result['lines']}L, {exports} exports, ~{comps} components"

    elif ext == ".md":
        headings = re.findall(r'^#{1,3}\s+(.+)', content, re.MULTILINE)
        result["summary"] = f"Docs: {', '.join(headings[:4])}" if headings else f"{result['lines']}L markdown"

    elif ext in (".json", ".yaml", ".yml", ".toml"):
        result["summary"] = f"Config: {result['lines']}L"

    else:
        result["summary"] = f"{result['lines']}L"

    # Threat scan (case-insensitive for most)
    for pattern, hint in _THREAT_PATTERNS:
        if pattern.lower() in content.lower():
            result["threat_hints"].append(hint)

    return result


def scout_sector(sector: dict, ship_short_id: str) -> dict:
    """
    Run a scout mission on one sector.
    Reads all files, generates asteroid intel, produces sector briefing.
    """
    if ship_short_id:
        ship = get_ship(ship_short_id)
        if ship:
            update_ship(ship_short_id,
                        task=f"scouting {sector['rel_path']}",
                        status="active")
    else:
        ship = None

    asteroid_intels: dict = {}
    file_summaries:  list = []
    total_threat_hits = 0

    for aid, asteroid in sector["asteroids"].items():
        scan = _scan_file(asteroid["path"])
        intel = f"{asteroid['filename']}: {scan['summary']}"
        if scan["threat_hints"]:
            intel += f"  ⚠ {', '.join(scan['threat_hints'])}"
            total_threat_hits += len(scan["threat_hints"])
        asteroid_intels[aid] = intel
        file_summaries.append(intel)

    raw_scan = "\n".join(file_summaries)

    if not raw_scan.strip():
        briefing     = "Empty sector — no code files detected. Clear."
        threat_level = 0
    else:
        resp = client.messages.create(
            model=config.MOTHERSHIP_MODEL,
            max_tokens=350,
            system=SCOUT_SYSTEM,
            messages=[{"role": "user", "content":
                f"SECTOR: {sector['rel_path']}\n"
                f"FILES ILLUMINATED:\n{raw_scan[:4000]}"}]
        )
        briefing = resp.content[0].text
        m = re.search(r"THREAT LEVEL:\s*([0-5])", briefing)
        threat_level = int(m.group(1)) if m else min(total_threat_hits, 5)

    mark_scouted(sector["id"], briefing, threat_level, asteroid_intels)

    if threat_level >= 3:
        place_beacon(sector["id"], f"THREAT-{threat_level}", "danger")
    if sector["ru_value"] >= 100:
        place_beacon(sector["id"], f"RU-{sector['ru_value']}", "resource")

    # Reward for illuminating this sector
    ru_earned = max(10, sector["file_count"] * 5)
    store.set("ru_balance", store.get("ru_balance", 0) + ru_earned)

    # Write updated map for C engine
    map_path = os.path.join(STATE_DIR, "sector_map.json")
    write_map_for_engine(map_path)

    if ship:
        update_ship(ship_short_id, task=None)
        log = ship.get("log", [])
        log.append({
            "sector":  sector["rel_path"],
            "files":   sector["file_count"],
            "threat":  threat_level,
            "ts":      datetime.datetime.utcnow().isoformat(),
        })
        update_ship(ship_short_id, log=log)

    store.log_action("scout", "sector_cleared", sector["rel_path"])
    return {
        "sector":           sector["rel_path"],
        "briefing":         briefing,
        "threat":           threat_level,
        "ru_earned":        ru_earned,
        "files":            sector["file_count"],
        "asteroid_intels":  asteroid_intels,
    }
