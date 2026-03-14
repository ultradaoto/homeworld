"""
Docker fleet launcher.

Called by build_queue.on_ship_built() to spin up the actual container
once the C engine confirms a ship has been built in-game.
Also provides stop_ship() and list_running_ships() for TUI commands.
"""
import os
import subprocess

from memory import store

# Model and resource limits by ship class
_MODEL_MAP = {
    "probe":       "llama3.1:8b",
    "scout":       "claude-haiku-4-5-20251001",
    "interceptor": "claude-haiku-4-5-20251001",
    "corvette":    "claude-sonnet-4-6",
    "salvage":     "claude-sonnet-4-6",
    "repair":      "claude-haiku-4-5-20251001",
    "resource":    "claude-haiku-4-5-20251001",
    "frigate":     "claude-sonnet-4-6",
    "destroyer":   "claude-sonnet-4-6",
    "carrier":     "claude-opus-4-6",
}

_LIMIT_MAP = {
    "probe":       ("1", "2g"),
    "scout":       ("1", "2g"),
    "interceptor": ("2", "4g"),
    "corvette":    ("2", "4g"),
    "salvage":     ("2", "4g"),
    "repair":      ("1", "2g"),
    "resource":    ("1", "2g"),
    "frigate":     ("4", "8g"),
    "destroyer":   ("4", "8g"),
    "carrier":     ("8", "16g"),
}


def launch_ship(ship_type: str, callsign: str, short_id: str,
                task: dict | None = None) -> dict:
    """
    Spin up a Docker container for the given ship.

    Returns {"container_id": ..., "model": ...} on success
    or     {"error": ..., "container_id": None} on failure.

    The container_id is the first 12 chars of the Docker container ID.
    This is stored in active_ships.container_id so the C engine can
    call `docker stop --time 8 <container_id>` when HP hits 0.
    """
    container_name = f"hw-{ship_type}-{short_id.lower()}"
    model          = _MODEL_MAP.get(ship_type, "claude-haiku-4-5-20251001")
    cpus, mem      = _LIMIT_MAP.get(ship_type, ("1", "2g"))

    cmd = [
        "docker", "run", "-d",
        "--name",    container_name,
        "--cpus",    cpus,
        "--memory",  mem,
        "--network", "mothership_fleet",
        "-e", f"SHIP_CLASS={ship_type}",
        "-e", f"CALLSIGN={callsign}",
        "-e", f"LLM_MODEL={model}",
        "-e", f"MOTHERSHIP_URL=http://mothership-api:3000",
        "-e", f"ANTHROPIC_API_KEY={os.environ.get('ANTHROPIC_API_KEY', '')}",
        "-e", f"SHORT_ID={short_id}",
        "homeworld-ship:latest",
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        err = proc.stderr.strip()[:200]
        store.log_action("docker", "launch_failed",
                         f"{callsign} ({ship_type}): {err}")
        return {"error": err, "container_id": None}

    container_id = proc.stdout.strip()[:12]

    # Post initial task if supplied
    if task:
        try:
            import httpx
            httpx.post(
                f"http://localhost:3000/api/fleet/{container_id}/orders/assign",
                json=task, timeout=3
            )
        except Exception:
            pass

    store.log_action("docker", "container_started",
                     f"{callsign} → {container_id}")
    return {"container_id": container_id, "model": model}


def stop_ship(name_or_id: str, reason: str = "") -> bool:
    """
    Send SIGTERM to a container then wait up to 8 s before SIGKILL.
    SIGTERM triggers the escape pod handler in ship_wrapper.py.
    """
    proc = subprocess.run(
        ["docker", "stop", "--time", "8", name_or_id],
        capture_output=True, text=True
    )
    store.log_action("docker", "container_stopped",
                     f"{name_or_id}: {reason}")
    return proc.returncode == 0


def list_running_ships() -> list:
    """Return list of running hw-* containers."""
    proc = subprocess.run(
        ["docker", "ps", "--filter", "name=hw-",
         "--format", "{{.Names}}\t{{.Status}}\t{{.ID}}"],
        capture_output=True, text=True
    )
    ships = []
    for line in proc.stdout.strip().split("\n"):
        if line:
            parts = line.split("\t")
            if len(parts) >= 3:
                ships.append({
                    "name":   parts[0],
                    "status": parts[1],
                    "id":     parts[2],
                })
    return ships
