"""
Cockpit Memory — per-ship persistent context window (Phase 12).

Each ship maintains a JSON file in state/cockpit/<ship_id>.json
containing its full message history as a flat messages array.
When context exceeds ~32k chars, a Haiku compression pass summarizes
the older history while preserving the last 2 messages verbatim.

This memory:
  - Survives Python restarts (written to disk on every append)
  - Survives Docker container death (saved before SIGTERM in ship_wrapper)
  - Feeds directly into Phase 13's generate_patch()

Interface (matches LLM messages format):
    load_cockpit(ship_id)               -> list[dict]  [{role, content}, ...]
    append_cockpit(ship_id, role, text) -> None
    clear_cockpit(ship_id)              -> None
    cockpit_summary(ship_id)            -> str
"""
import json
import os

from config import STATE_DIR

COCKPIT_DIR           = os.path.join(STATE_DIR, "cockpit")
COMPRESSION_THRESHOLD = 32000   # chars before auto-compress
COMPRESSION_MODEL     = "claude-haiku-4-5-20251001"


def _path(ship_id: str) -> str:
    safe = ship_id.replace("/", "_").replace("\\", "_").replace(":", "_")
    return os.path.join(COCKPIT_DIR, f"{safe}.json")


def load_cockpit(ship_id: str) -> list[dict]:
    """
    Return the ship's accumulated context as a messages array.
    Safe to call before any cockpit file exists — returns [].
    Migrates old {entries:[{ts,role,content}]} format on first read.
    """
    filepath = _path(ship_id)
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []

    # Migrate old envelope format → flat list
    if isinstance(data, dict) and "entries" in data:
        data = [{"role": e.get("role", "assistant"),
                 "content": e.get("content", "")}
                for e in data["entries"]]
        _write_cockpit(ship_id, data)
    elif not isinstance(data, list):
        return []

    return data


def _write_cockpit(ship_id: str, messages: list[dict]) -> None:
    os.makedirs(COCKPIT_DIR, exist_ok=True)
    filepath = _path(ship_id)
    tmp      = filepath + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(messages, f, indent=2)
    os.replace(tmp, filepath)


def append_cockpit(ship_id: str, role: str, content: str) -> None:
    """
    Append a new message to the ship's memory.
    Triggers compression if the total context exceeds COMPRESSION_THRESHOLD.
    role must be "user" or "assistant".
    """
    messages = load_cockpit(ship_id)
    messages.append({"role": role, "content": content})

    total = sum(len(m.get("content", "")) for m in messages)
    if total > COMPRESSION_THRESHOLD:
        messages = _summarize_cockpit(ship_id, messages)

    _write_cockpit(ship_id, messages)


def _summarize_cockpit(ship_id: str, messages: list[dict]) -> list[dict]:
    """
    Cheap compression pass to prevent context window bloat.
    Preserves the last 2 messages verbatim. Everything older is summarized.
    """
    print(f"[cockpit] {ship_id}: compressing {len(messages)} messages…")

    history    = messages[:-2] if len(messages) > 2 else messages
    tail       = messages[-2:] if len(messages) >= 2 else messages
    convo_text = "\n".join(
        f"{m['role'].upper()}: {m['content'][:500]}" for m in history
    )

    prompt = (
        "Summarize this ship's engineering work into a highly technical "
        "bulleted list. Include: files read, functions analyzed, "
        "vulnerabilities found, patches attempted, test results, "
        "architecture insights. Be specific and terse.\n\n"
        f"{convo_text[:8000]}"
    )

    try:
        import anthropic
        client   = anthropic.Anthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY", "")
        )
        response = client.messages.create(
            model=os.environ.get("MOTHERSHIP_MODEL", COMPRESSION_MODEL),
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        summary = response.content[0].text
    except Exception as exc:
        summary = f"[compression failed: {exc}] — {len(history)} messages truncated"

    compressed = [{
        "role":    "assistant",
        "content": (
            f"[MEMORY COMPRESSION LOG — {len(history)} messages compressed]\n"
            f"{summary}"
        ),
    }]
    compressed.extend(tail)
    print(f"[cockpit] {ship_id}: compressed to {len(compressed)} messages")
    return compressed


def clear_cockpit(ship_id: str) -> None:
    """Wipe a ship's memory. Use when reassigning to a new sector."""
    filepath = _path(ship_id)
    if os.path.exists(filepath):
        os.remove(filepath)


def cockpit_summary(ship_id: str) -> str:
    """One-line summary of what a ship remembers. For TUI display."""
    messages = load_cockpit(ship_id)
    if not messages:
        return "no memory"
    total = sum(len(m.get("content", "")) for m in messages)
    last  = messages[-1]["content"][:80].replace("\n", " ")
    return f"{len(messages)} messages ({total} chars) · last: {last}"
