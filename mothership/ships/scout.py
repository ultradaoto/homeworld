"""
ScoutShip — codebase exploration agent.

Walks a directory tree, assigns 3D spatial coordinates to each sector
(directory), uses Claude to summarize what each sector contains, and
writes a navigable map that can drive future in-game ship placement.

3D coordinate model:
  x  = horizontal index within siblings  (breadth)
  y  = depth from root                   (depth layer)
  z  = normalized file count             (density / height)

The resulting scout_map JSON is designed to eventually feed back to
the C engine so real Scout ships can be placed at these coordinates.
"""

import os
import math
import datetime
import json
import anthropic

from ships.base_ship import BaseShip
import config

# Scout cost and reward
RU_COST_PER_SECTOR = 10   # deducted as scout travels deeper
RU_REWARD_PER_FILE = 2    # deposited for each file discovered

MAX_DEPTH   = 4    # how deep to recurse
MAX_SECTORS = 64   # cap to avoid runaway scans
SKIP_DIRS   = {".git", "__pycache__", "node_modules", "venv", ".venv",
               "build", "dist", ".mypy_cache", ".pytest_cache"}

# File type → ship class metaphor (for future in-game rendering)
FILE_CLASS = {
    ".py":   "interceptor",
    ".c":    "destroyer",
    ".h":    "support",
    ".md":   "scout",
    ".json": "salvage",
    ".txt":  "scout",
    ".js":   "interceptor",
    ".ts":   "interceptor",
    ".rs":   "destroyer",
    ".go":   "destroyer",
}

SCOUT_SYSTEM = """
You are a Scout intelligence reporting on a codebase sector.
Given a directory listing, write a 1-2 sentence tactical briefing:
what this directory does, what it contains, and whether it is worth
deeper investigation. Be specific — name key files. Be concise.
"""


def _walk_tree(root: str, max_depth: int) -> list[dict]:
    """Return a flat list of sector dicts, depth-first."""
    sectors = []
    sector_id = [0]

    def _recurse(path: str, depth: int, parent_id: int, sibling_idx: int):
        if depth > max_depth or len(sectors) >= MAX_SECTORS:
            return

        try:
            entries = sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name))
        except PermissionError:
            return

        files    = [e.name for e in entries if e.is_file()]
        subdirs  = [e for e in entries if e.is_dir() and e.name not in SKIP_DIRS]
        rel_path = os.path.relpath(path, root).replace("\\", "/")
        if rel_path == ".":
            rel_path = "/"

        # Count file types
        type_counts: dict = {}
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            type_counts[ext] = type_counts.get(ext, 0) + 1

        my_id = sector_id[0]
        sector_id[0] += 1

        sectors.append({
            "id":          my_id,
            "parent_id":   parent_id,
            "path":        rel_path,
            "depth":       depth,
            "sibling_idx": sibling_idx,
            "file_count":  len(files),
            "subdir_count": len(subdirs),
            "file_types":  type_counts,
            "sample_files": files[:8],
            "ship_class":  _dominant_class(type_counts),
            # 3D position assigned after full scan
            "pos": {"x": 0.0, "y": float(depth * 20), "z": 0.0},
        })

        for i, subdir in enumerate(subdirs):
            _recurse(subdir.path, depth + 1, my_id, i)

    _recurse(root, 0, -1, 0)
    return sectors


def _dominant_class(type_counts: dict) -> str:
    if not type_counts:
        return "scout"
    dominant_ext = max(type_counts, key=lambda e: type_counts[e])
    return FILE_CLASS.get(dominant_ext, "scout")


def _assign_positions(sectors: list[dict]) -> None:
    """Assign x/z positions so sectors spread out on a 2D grid per depth layer."""
    by_depth: dict = {}
    for s in sectors:
        by_depth.setdefault(s["depth"], []).append(s)

    for depth, group in by_depth.items():
        spread = max(len(group) * 15, 30)
        for i, s in enumerate(group):
            angle = (2 * math.pi * i / max(len(group), 1))
            radius = spread
            s["pos"]["x"] = round(math.cos(angle) * radius, 1)
            s["pos"]["z"] = round(math.sin(angle) * radius, 1)
            s["pos"]["y"] = round(float(depth * 25), 1)


def _summarize_sectors(sectors: list[dict]) -> dict[int, str]:
    """Ask Claude for a one-line briefing on each top-level sector."""
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    summaries: dict = {}

    # Only summarize top 2 depth levels to keep API calls reasonable
    targets = [s for s in sectors if s["depth"] <= 1]

    for s in targets:
        listing = (
            f"Path: {s['path']}\n"
            f"Files ({s['file_count']}): {', '.join(s['sample_files'][:6])}\n"
            f"Subdirs: {s['subdir_count']}\n"
            f"File types: {s['file_types']}"
        )
        try:
            resp = client.messages.create(
                model=config.MOTHERSHIP_MODEL,
                max_tokens=128,
                system=SCOUT_SYSTEM,
                messages=[{"role": "user", "content": listing}]
            )
            summaries[s["id"]] = resp.content[0].text.strip()
        except Exception as e:
            summaries[s["id"]] = f"(scan error: {e})"

    return summaries


class ScoutShip(BaseShip):
    ship_type = "scout"

    async def run(self, task: dict) -> dict:
        root = task.get("path", task.get("objective", ""))
        if not root or not os.path.isdir(root):
            # Default to the project root
            root = os.path.normpath(
                os.path.join(os.path.dirname(__file__), "..", "..")
            )

        root = os.path.abspath(root)

        # ── Walk the tree ──────────────────────────────────────────────
        sectors = _walk_tree(root, MAX_DEPTH)
        _assign_positions(sectors)

        total_files   = sum(s["file_count"]   for s in sectors)
        total_sectors = len(sectors)

        # ── RU economy ────────────────────────────────────────────────
        ru_earned = total_files * RU_REWARD_PER_FILE
        self.deposit_ru(ru_earned)

        # ── LLM sector briefings ──────────────────────────────────────
        summaries = _summarize_sectors(sectors)
        for s in sectors:
            s["briefing"] = summaries.get(s["id"], "")

        # ── Build sector type distribution ────────────────────────────
        class_counts: dict = {}
        for s in sectors:
            cls = s["ship_class"]
            class_counts[cls] = class_counts.get(cls, 0) + 1

        # ── Write map JSON (machine-readable, feeds C engine later) ───
        ts       = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        map_data = {
            "scout_id":     self.agent_id,
            "root":         root,
            "timestamp":    ts,
            "total_sectors": total_sectors,
            "total_files":  total_files,
            "ru_earned":    ru_earned,
            "class_distribution": class_counts,
            "sectors":      sectors,
        }
        map_path = self.write_output(f"scout_map_{ts}.json", map_data)

        # ── Write human-readable markdown report ──────────────────────
        lines = [
            f"# Scout Report — {os.path.basename(root)}",
            f"*{ts} UTC | {total_sectors} sectors | {total_files} files | +{ru_earned} RU*\n",
            "## Sector Map\n",
        ]
        for s in sectors:
            indent  = "  " * s["depth"]
            briefing = f" — {s['briefing']}" if s["briefing"] else ""
            lines.append(
                f"{indent}- **{s['path']}** "
                f"[{s['file_count']} files, class: {s['ship_class']}]"
                f"{briefing}"
            )

        lines += [
            "\n## Fleet Class Distribution\n",
            *[f"- {cls}: {n}" for cls, n in sorted(class_counts.items())],
            "\n## Navigation Coordinates (depth layers)\n",
            "| Sector | x | y | z | class |",
            "| --- | --- | --- | --- | --- |",
            *[
                f"| {s['path']} | {s['pos']['x']} | {s['pos']['y']} | {s['pos']['z']} | {s['ship_class']} |"
                for s in sectors if s["depth"] <= 2
            ],
        ]

        report_path = self.write_output(
            f"scout_report_{ts}.md", "\n".join(lines)
        )

        self.mark_complete()
        return {
            "status":    "ok",
            "sectors":   total_sectors,
            "files":     total_files,
            "ru_earned": ru_earned,
            "map":       map_path,
            "report":    report_path,
        }
