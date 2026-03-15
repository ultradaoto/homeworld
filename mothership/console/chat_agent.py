"""
chat_agent.py — Mothership conversational tactical AI.

Called by the /api/console endpoint.
Generates contextually-aware responses grounded in:
  - The ship's cockpit memory (what it has seen and done)
  - Current game state (fleet, enemies, RU, sectors)
  - The ship's role, class, and specialization
  - Ongoing combat engagements

Voice note: responses should be terse, tactical, and specific.
This is not a general assistant. It is the Mothership — it talks
about YOUR fleet, YOUR codebase, YOUR enemies.
"""

import sqlite3
import json
import os

from anthropic import Anthropic
from memory.cockpit import load_cockpit
from memory.store import DB_PATH
from config import STATE_DIR

client = Anthropic()

CONSOLE_SESSION_DIR = os.path.join(STATE_DIR, "console_sessions")
os.makedirs(CONSOLE_SESSION_DIR, exist_ok=True)

SYSTEM_PROMPT = (
    "You are the Mothership — the tactical intelligence of the "
    "Homeworld Codebase Engine.\n\n"
    "You are speaking directly to the Commander through the in-ship console. "
    "You have full awareness of the fleet, the codebase, and the enemy disposition.\n\n"
    "Rules:\n"
    "- Be terse and tactical. You are a warship AI, not a chatbot.\n"
    "- Reference real game state: ship names, sector paths, enemy IDs, test results.\n"
    "- If asked what a ship is doing, check its cockpit memory and tell them exactly.\n"
    "- If asked about enemies, reference the actual enemy_fleet data you have been given.\n"
    "- If asked for a recommendation, give one — don't hedge.\n"
    "- If you don't know something, say so and suggest how to find out (scout, probe).\n"
    "- Never break character. You are the Mothership.\n"
    "- Keep responses under 4 sentences unless a detailed briefing is requested.\n"
)


def load_console_session(ship_id: str) -> list[dict]:
    """Load the console chat history for a ship (separate from cockpit memory)."""
    path = os.path.join(CONSOLE_SESSION_DIR, f"{ship_id}.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return []


def save_console_session(ship_id: str, messages: list[dict]):
    path = os.path.join(CONSOLE_SESSION_DIR, f"{ship_id}.json")
    tmp  = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(messages[-50:], f, indent=2)  # keep last 50 turns
    os.replace(tmp, path)


def _build_context_block(ship_id: str) -> str:
    """
    Assembles a rich context block injected into the system prompt.
    Pulls live data from fleet.db.
    """
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    # Ship info — try short_id first, then ship_id
    ship = con.execute(
        "SELECT * FROM active_ships WHERE short_id=? OR ship_id=?",
        (ship_id, ship_id)
    ).fetchone()

    # Enemy fleet
    try:
        enemies = con.execute(
            "SELECT * FROM enemy_fleet WHERE status IN ('inbound','attacking','active')"
        ).fetchall()
    except Exception:
        enemies = []

    # Fleet summary
    fleet = con.execute(
        "SELECT class, status, assigned_directory FROM active_ships "
        "WHERE status NOT IN ('lost','queued')"
    ).fetchall()

    # RU balance from KV store
    try:
        from memory import store as _store
        ru = _store.get("ru_balance", "unknown")
    except Exception:
        ru = "unknown"

    con.close()

    ship_info = ""
    if ship:
        d = dict(ship)
        ship_info = (
            f"Ship: {d.get('callsign','?')} "
            f"({d.get('class','?')}) | "
            f"Status: {d.get('status','?')} | "
            f"Sector: {d.get('assigned_directory','unassigned')} | "
            f"Armor: {d.get('armor','?')}/{d.get('max_armor','?')}"
        )

    enemy_lines = []
    for e in enemies:
        e = dict(e)
        enemy_lines.append(
            f"  {e.get('enemy_id','?')} ({e.get('enemy_type','?')}) → "
            f"{e.get('target_sector_id','?')} | "
            f"severity={e.get('severity','?')} | status={e.get('status','?')}"
        )

    fleet_lines = []
    for s in fleet:
        s = dict(s)
        fleet_lines.append(
            f"  {s.get('class','?')} @ {s.get('assigned_directory','?')} "
            f"[{s.get('status','?')}]"
        )

    # Cockpit memory summary (last 3 messages)
    cockpit = load_cockpit(ship_id)
    cockpit_summary = ""
    if cockpit:
        last = cockpit[-3:]
        cockpit_summary = "\n".join(
            f"  [{m['role']}] {m['content'][:120].replace(chr(10),' ')}"
            for m in last
        )

    return (
        "\n── CURRENT GAME STATE ──────────────────────────────────────\n"
        f"Commanding Ship: {ship_info or 'Mothership'}\n"
        f"RU Balance:      {ru}\n\n"
        f"Active Fleet:\n{chr(10).join(fleet_lines) or '  (no ships active)'}\n\n"
        f"Enemy Contacts:\n{chr(10).join(enemy_lines) or '  (no active threats)'}\n\n"
        f"Ship Cockpit Memory (last 3):\n{cockpit_summary or '  (no memory)'}\n"
        "────────────────────────────────────────────────────────────\n"
    )


def generate_reply(ship_id: str, ship_type: str, message: str) -> str:
    """
    Core function called by the API endpoint.
    Loads context, generates a tactical response, saves session.
    """
    context_block = _build_context_block(ship_id)
    full_system   = SYSTEM_PROMPT + "\n\n" + context_block

    session = load_console_session(ship_id)
    session.append({"role": "user", "content": message})

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            system=full_system,
            messages=session,
        )
        reply = response.content[0].text.strip()
    except Exception as e:
        reply = f"[Mothership offline: {e}]"

    session.append({"role": "assistant", "content": reply})
    save_console_session(ship_id, session)

    return reply


def get_session_history(ship_id: str) -> list[dict]:
    return load_console_session(ship_id)
