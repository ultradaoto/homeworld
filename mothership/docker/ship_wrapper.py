"""
Ship Wrapper — runs inside every Docker container.

Handles: registration, order polling, task execution, SIGTERM escape pod.

When the C engine sets a ship's HP to 0, it runs:
    docker stop --time 8 <container_id>

SIGTERM arrives 8 seconds before SIGKILL.
This wrapper catches it, serializes context to the escape pod API, and exits
cleanly.  The in-game ship explodes.  A Salvage Corvette can recover the work.
"""
import json
import os
import signal
import sys
import threading
import time

import httpx

# ── Environment ────────────────────────────────────────────────────────────

SHIP_ID    = os.environ.get("HOSTNAME", "unknown")
SHIP_CLASS = os.environ.get("SHIP_CLASS", "scout")
CALLSIGN   = os.environ.get("CALLSIGN",  f"Ship-{SHIP_ID[:6]}")
MODEL      = os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001")
API_KEY    = os.environ.get("ANTHROPIC_API_KEY", "")
MOTHERSHIP = os.environ.get("MOTHERSHIP_URL", "http://mothership-api:3000")
POS_X      = float(os.environ.get("POS_X", "0"))
POS_Y      = float(os.environ.get("POS_Y", "0"))
POS_Z      = float(os.environ.get("POS_Z", "0"))

_current_context: list = []   # live LLM context window
_current_task:    str  = ""   # current order description
_next_steps:      str  = ""   # ship's own plan for what comes next
_shutdown_event = threading.Event()


# ── Mothership comms ───────────────────────────────────────────────────────

def _register() -> bool:
    for attempt in range(10):
        try:
            httpx.post(f"{MOTHERSHIP}/api/fleet/register", json={
                "ship_id":    SHIP_ID,
                "callsign":   CALLSIGN,
                "ship_class": SHIP_CLASS,
                "model":      MODEL,
            }, timeout=5)
            print(f"[{CALLSIGN}] Registered with Mothership")
            return True
        except Exception:
            time.sleep(min(2 ** attempt, 30))
    return False


def _update_status(task: str, last_action: str, armor: int = 100):
    try:
        httpx.post(f"{MOTHERSHIP}/api/fleet/status", json={
            "ship_id":     SHIP_ID,
            "task":        task,
            "last_action": last_action,
            "armor":       armor,
        }, timeout=3)
    except Exception:
        pass


def _complete_order(result: str, output: str = "",
                    ru_earned: int = 0, next_task: str = ""):
    try:
        httpx.post(f"{MOTHERSHIP}/api/fleet/complete", json={
            "ship_id":   SHIP_ID,
            "result":    result,
            "output":    output,
            "ru_earned": ru_earned,
            "next_task": next_task,
        }, timeout=5)
    except Exception:
        pass


# ── SIGTERM → Escape Pod ──────────────────────────────────────────────────

def _sigterm_handler(signum, frame):
    """
    SIGTERM means the C engine set our HP to 0 (or docker stop was called).
    Serialize our context (+ cockpit memory) so a Salvage Corvette can resume.
    """
    print(f"[{CALLSIGN}] SIGTERM — launching escape pod")

    # Include last 5 cockpit messages in the pod
    cockpit_snapshot: list = []
    try:
        from memory.cockpit import load_cockpit
        cockpit_snapshot = load_cockpit(SHIP_ID)[-5:]
    except Exception:
        pass

    context_snapshot = json.dumps({
        "context": _current_context[-10:],
        "cockpit": cockpit_snapshot,
        "task":    _current_task,
    })
    try:
        httpx.post(f"{MOTHERSHIP}/api/fleet/escape_pod", json={
            "ship_id":      SHIP_ID,
            "callsign":     CALLSIGN,
            "ship_type":    SHIP_CLASS,
            "context_json": context_snapshot,
            "next_steps":   _next_steps or "task was interrupted",
            "pos_x":        POS_X,
            "pos_y":        POS_Y,
            "pos_z":        POS_Z,
        }, timeout=4)
        print(f"[{CALLSIGN}] Escape pod saved with cockpit memory.")
    except Exception as exc:
        print(f"[{CALLSIGN}] Escape pod failed: {exc}")
    _shutdown_event.set()
    sys.exit(0)


signal.signal(signal.SIGTERM, _sigterm_handler)
signal.signal(signal.SIGINT,  _sigterm_handler)


# ── Task dispatch ─────────────────────────────────────────────────────────

def _execute_task(order: dict) -> dict:
    global _current_task, _current_context, _next_steps

    task_type = order.get("type", "noop")
    _current_task = f"{task_type}: {json.dumps(order)[:80]}"
    _update_status(_current_task, "starting")

    if task_type == "harvest":
        return _task_harvest(order)
    elif task_type == "scout":
        return _task_scout(order)
    elif task_type == "research":
        return _task_research(order)
    elif task_type == "salvage_pod":
        return _task_recover_pod(order)
    elif task_type == "llm":
        return _task_llm(order)
    else:
        return {"result": "error", "output": f"unknown task type: {task_type}"}


def _task_harvest(order: dict) -> dict:
    """Fetch a URL and summarise for fleet intel."""
    global _current_context, _next_steps
    import anthropic

    url = order.get("url", "")
    _update_status(f"harvesting {url}", "fetching")
    try:
        resp = httpx.get(url, timeout=15, follow_redirects=True)
        text = resp.text[:6000]
    except Exception as exc:
        return {"result": "error", "output": str(exc), "ru_earned": 0}

    _current_context = [{"role": "user", "content":
        f"Summarise this page for fleet intel:\n{text[:4000]}"}]
    _next_steps = "Deliver intel to Mothership, await next harvest order"

    client  = anthropic.Anthropic(api_key=API_KEY)
    resp2   = client.messages.create(
        model=MODEL, max_tokens=400, messages=_current_context
    )
    summary = resp2.content[0].text
    _current_context.append({"role": "assistant", "content": summary})
    _update_status(f"depositing harvest from {url}", summary[:80])
    return {"result": "ok", "output": summary, "ru_earned": 100}


def _task_scout(order: dict) -> dict:
    """Walk a codebase path and return file-level intel."""
    global _next_steps
    import ast

    path = order.get("path", ".")
    _update_status(f"scouting {path}", "reading files")
    summaries = []

    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in sorted(dirs)
                   if not d.startswith(".") and d not in
                   ("__pycache__", "node_modules", "venv", ".venv")][:4]
        for fname in sorted(files)[:6]:
            fpath = os.path.join(root, fname)
            ext   = os.path.splitext(fname)[1].lower()
            if ext not in (".py", ".c", ".h", ".js", ".ts", ".md",
                           ".json", ".yaml", ".toml"):
                continue
            try:
                with open(fpath, errors="ignore") as fp:
                    content = fp.read(2000)
                if ext == ".py":
                    try:
                        tree  = ast.parse(content)
                        funcs = len([n for n in ast.walk(tree)
                                     if isinstance(n, ast.FunctionDef)])
                        classes = len([n for n in ast.walk(tree)
                                       if isinstance(n, ast.ClassDef)])
                        summaries.append(
                            f"{fname}: {funcs} funcs, {classes} classes"
                        )
                    except SyntaxError:
                        summaries.append(f"{fname}: [parse error]")
                else:
                    lines = content.count("\n")
                    summaries.append(f"{fname}: {lines} lines")
            except OSError:
                summaries.append(f"{fname}: [unreadable]")

    intel = "\n".join(summaries) or "empty sector"
    _next_steps = "Return to Mothership. Await next patrol order."
    return {"result": "ok", "output": intel, "ru_earned": 30}


def _task_research(order: dict) -> dict:
    """Use LLM to research a topic given a prompt."""
    global _current_context, _next_steps
    import anthropic

    prompt = order.get("prompt", "")
    _update_status(f"researching: {prompt[:40]}", "thinking")
    _current_context = [{"role": "user", "content": prompt}]
    _next_steps = "File research results, await next assignment"

    client = anthropic.Anthropic(api_key=API_KEY)
    resp   = client.messages.create(
        model=MODEL, max_tokens=1024, messages=_current_context
    )
    result = resp.content[0].text
    _current_context.append({"role": "assistant", "content": result})
    return {"result": "ok", "output": result, "ru_earned": 50}


def _task_llm(order: dict) -> dict:
    """Generic LLM task: run a prompt, return the response."""
    global _current_context, _next_steps
    import anthropic

    prompt  = order.get("prompt", "")
    system  = order.get("system", "")
    _current_context = [{"role": "user", "content": prompt}]
    _next_steps = order.get("next_steps", "await next order")

    client = anthropic.Anthropic(api_key=API_KEY)
    kwargs: dict = {"model": MODEL, "max_tokens": 2048, "messages": _current_context}
    if system:
        kwargs["system"] = system
    resp = client.messages.create(**kwargs)
    result = resp.content[0].text
    _current_context.append({"role": "assistant", "content": result})
    return {"result": "ok", "output": result, "ru_earned": 25}


def _task_recover_pod(order: dict) -> dict:
    """Salvage Corvette: recover an escape pod and resume the work."""
    global _current_context, _next_steps
    import anthropic

    pod_id = order.get("pod_id", "")
    resp   = httpx.post(
        f"{MOTHERSHIP}/api/fleet/recover_pod",
        params={"pod_id": pod_id, "salvage_ship_id": SHIP_ID},
        timeout=5,
    )
    if resp.status_code != 200:
        return {"result": "error",
                "output": f"pod recovery failed: {resp.text}"}

    pod_data         = resp.json()
    _current_context = pod_data.get("context", [])
    _next_steps      = pod_data.get("next_steps", "continue interrupted task")
    from_ship        = pod_data.get("from_ship", "unknown")

    _update_status(
        f"resumed task from {from_ship}",
        f"pod recovered — {_next_steps[:60]}"
    )

    if _current_context and _next_steps:
        _current_context.append({"role": "user", "content":
            f"You are resuming an interrupted task from {from_ship}. "
            f"Next steps: {_next_steps}. Continue now."})
        client = anthropic.Anthropic(api_key=API_KEY)
        resp2  = client.messages.create(
            model=MODEL, max_tokens=1024, messages=_current_context
        )
        result = resp2.content[0].text
        return {"result": "ok",
                "output": f"[RESUMED from {from_ship}] {result}",
                "ru_earned": 50}

    return {"result": "ok",
            "output": f"Recovered {from_ship}'s context, "
                      f"resuming: {_next_steps[:80]}",
            "ru_earned": 50}


# ── Main loop ──────────────────────────────────────────────────────────────

def main():
    if not _register():
        print(f"[{CALLSIGN}] Cannot reach Mothership. Aborting.")
        sys.exit(1)

    print(f"[{CALLSIGN}] Online. Class={SHIP_CLASS} Model={MODEL}")
    _update_status("awaiting_orders", "just spawned")

    while not _shutdown_event.is_set():
        try:
            resp   = httpx.get(
                f"{MOTHERSHIP}/api/fleet/{SHIP_ID}/orders", timeout=5
            )
            orders = resp.json()
        except Exception:
            orders = []
            time.sleep(5)
            continue

        if orders:
            order  = orders[0]
            result = _execute_task(order)
            _complete_order(
                result.get("result", "ok"),
                result.get("output", ""),
                result.get("ru_earned", 0),
            )
        else:
            _update_status("awaiting_orders", "idle")
            time.sleep(3)


if __name__ == "__main__":
    main()
