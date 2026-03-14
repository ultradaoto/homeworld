"""
Persistent spatial index — maps the codebase to 3D space.

  Directory  →  sector (dust cloud) at depth-based orbital radius
  Code file  →  asteroid inside that sector

Coordinates are stable per path (based on walk order + depth layer).
"""
import datetime
import hashlib
import json
import math
import os

from memory import store


# Directories to skip entirely
_SKIP_DIRS = frozenset({
    "__pycache__", "build", "node_modules", "venv", ".venv",
    ".git", "state", "outputs", ".mypy_cache", ".pytest_cache",
    "dist", "target", ".tox", "eggs", ".eggs",
})

_CODE_EXTS = frozenset({
    ".py", ".c", ".h", ".cpp", ".cs", ".rs", ".go", ".java",
    ".js", ".ts", ".tsx", ".jsx",
    ".md", ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini",
    ".rb", ".php", ".sh", ".bat", ".ps1",
})


def _path_id(path: str) -> str:
    return hashlib.md5(path.encode()).hexdigest()[:8].upper()


def index_project(root_dir: str) -> dict:
    """
    Walk root_dir and assign 3D coordinates to every directory (sector)
    and code file (asteroid).  Returns the sectors dict and persists it.
    """
    root_dir = os.path.abspath(root_dir)
    sectors: dict = {}
    depth_counts: dict[int, int] = {}   # depth → how many sectors placed so far

    for dirpath, dirnames, filenames in os.walk(root_dir, topdown=True):
        dirnames[:] = sorted(
            d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")
        )

        rel   = os.path.relpath(dirpath, root_dir)
        depth = 0 if rel == "." else rel.replace("\\", "/").count("/") + 1

        layer = depth_counts.get(depth, 0)
        depth_counts[depth] = layer + 1

        # Polar orbit — deeper dirs are further from the Mothership origin
        radius = (depth + 1) * 8000
        # Spread siblings evenly around the ring; use a temporary large
        # denominator so sectors aren't all piled on the same angle before
        # we know the total sibling count.  Good enough for a map display.
        max_at_depth = max(depth_counts.get(depth, 1), 1)
        angle = (layer * 2.0 * math.pi) / max(max_at_depth, 1)
        x = int(radius * math.cos(angle))
        z = int(radius * math.sin(angle))
        y = depth * -1500   # deeper = further "into" the map on Y axis

        code_files = [f for f in filenames
                      if os.path.splitext(f)[1].lower() in _CODE_EXTS]

        asteroids: dict = {}
        total_lines = 0
        for cf in sorted(code_files):
            fpath = os.path.join(dirpath, cf)
            try:
                with open(fpath, errors="ignore") as fp:
                    lines = sum(1 for _ in fp)
            except OSError:
                lines = 0
            total_lines += lines
            aid = _path_id(fpath)
            asteroids[aid] = {
                "id":       aid,
                "filename": cf,
                "path":     fpath,
                "lines":    lines,
                "ru_value": min(lines // 5, 200),
                "scouted":  False,
                "intel":    None,
                "threat":   0,
            }

        sid = _path_id(dirpath)
        sectors[sid] = {
            "id":          sid,
            "path":        dirpath,
            "rel_path":    rel.replace("\\", "/"),
            "depth":       depth,
            "pos":         {"x": x, "y": y, "z": z},
            "asteroids":   asteroids,
            "file_count":  len(code_files),
            "total_lines": total_lines,
            "ru_value":    min(total_lines // 10, 500),
            "scouted":     False,
            "intel":       None,
            "threat_level": 0,
            "beacons":     [],
        }

    store.set("sector_map", sectors)
    store.set("sector_map_root", root_dir)
    store.log_action("map", "indexed", f"{len(sectors)} sectors from {root_dir}")
    return sectors


def get_sector_by_path(path: str) -> dict | None:
    sectors = store.get("sector_map", {})
    return sectors.get(_path_id(os.path.abspath(path)))


def get_sector_by_id(sector_id: str) -> dict | None:
    """Look up a sector by its 8-char hex ID (as used in sector_entry.json)."""
    return store.get("sector_map", {}).get(sector_id)


def get_nearest_unscouted(from_pos: dict, n: int = 12) -> list:
    sectors = store.get("sector_map", {})
    unscouted = [s for s in sectors.values() if not s["scouted"]]

    def dist(s: dict) -> float:
        p = s["pos"]
        return math.sqrt(
            (p["x"] - from_pos["x"]) ** 2 +
            (p["y"] - from_pos["y"]) ** 2 +
            (p["z"] - from_pos["z"]) ** 2
        )

    return sorted(unscouted, key=dist)[:n]


def mark_scouted(sector_id: str, intel: str, threat: int = 0,
                 asteroid_intels: dict | None = None):
    sectors = store.get("sector_map", {})
    if sector_id not in sectors:
        return
    sectors[sector_id].update({
        "scouted":      True,
        "intel":        intel,
        "threat_level": threat,
        "scouted_at":   datetime.datetime.utcnow().isoformat(),
    })
    if asteroid_intels:
        for aid, ast_intel in asteroid_intels.items():
            if aid in sectors[sector_id]["asteroids"]:
                sectors[sector_id]["asteroids"][aid].update({
                    "scouted": True,
                    "intel":   ast_intel,
                })
    store.set("sector_map", sectors)


def place_beacon(sector_id: str, label: str, btype: str = "intel"):
    sectors = store.get("sector_map", {})
    if sector_id in sectors:
        sectors[sector_id]["beacons"].append({
            "label": label,
            "type":  btype,
            "ts":    datetime.datetime.utcnow().isoformat(),
        })
    store.set("sector_map", sectors)


_THREAT_COLORS = [
    # (r, g, b, a) by threat band: 0, 1-2, 3-5, 6-8, 9-10
    (20,  180, 20,  0),    # 0: no cloud
    (60,  180, 60,  80),   # 1-2: dim green
    (220, 160, 20,  100),  # 3-5: amber
    (220, 30,  30,  120),  # 6-8: red
    (200, 0,   50,  140),  # 9+:  deep crimson
]

def _threat_color(tl: int) -> tuple:
    if tl <= 0: return _THREAT_COLORS[0]
    if tl <= 2: return _THREAT_COLORS[1]
    if tl <= 5: return _THREAT_COLORS[2]
    if tl <= 8: return _THREAT_COLORS[3]
    return _THREAT_COLORS[4]


def write_map_for_engine(out_path: str):
    """Write a compact engine-readable JSON the C bridge can count and render."""
    sectors = store.get("sector_map", {})
    data = []
    for s in sectors.values():
        tl = s["threat_level"]
        cr, cg, cb, ca = _threat_color(tl)
        cloud_radius   = 3000 + tl * 600          # matches smThreatCloudsDraw
        cloud_density  = min(tl / 10.0, 1.0)      # 0.0–1.0 for future use
        data.append({
            "id":           s["id"],
            "label":        s["rel_path"][:28],
            "x":            s["pos"]["x"],
            "y":            s["pos"]["y"],
            "z":            s["pos"]["z"],
            "scouted":      1 if s["scouted"] else 0,
            "threat":       tl,
            "ru_value":     s["ru_value"],
            "beacons":      len(s["beacons"]),
            "cloud_r":      cr,
            "cloud_g":      cg,
            "cloud_b":      cb,
            "cloud_a":      ca,
            "cloud_radius": cloud_radius,
            "cloud_density":cloud_density,
            "asteroids": [
                {
                    "id":       a["id"],
                    "filename": a["filename"],
                    "lines":    a["lines"],
                    "ru_value": a["ru_value"],
                    "scouted":  1 if a["scouted"] else 0,
                    "threat":   a["threat"],
                }
                for a in s["asteroids"].values()
            ],
        })
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))


def map_stats() -> dict:
    sectors = store.get("sector_map", {})
    if not sectors:
        return {"indexed": False}
    total_files   = sum(s["file_count"]  for s in sectors.values())
    total_lines   = sum(s["total_lines"] for s in sectors.values())
    scouted_count = sum(1 for s in sectors.values() if s["scouted"])
    threats       = sum(1 for s in sectors.values()
                        if s["scouted"] and s["threat_level"] >= 3)
    return {
        "indexed":        True,
        "sectors":        len(sectors),
        "scouted":        scouted_count,
        "dark":           len(sectors) - scouted_count,
        "total_files":    total_files,
        "total_lines":    total_lines,
        "threat_sectors": threats,
    }
