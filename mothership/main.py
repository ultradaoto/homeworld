#!/usr/bin/env python3
import sys
import os
import json
import uuid
import asyncio
import datetime

from rich.console import Console
from rich.panel   import Panel
from rich.prompt  import Prompt

import config
from memory import store
from command import planner
from router  import dispatcher
from ships.collector  import CollectorShip
from ships.researcher import ResearcherShip
from ships.destroyer  import DestroyerShip

console = Console()

ASCII_BANNER = """[bold purple]
  ██╗  ██╗ ██████╗ ███╗   ███╗███████╗██╗    ██╗ ██████╗ ██████╗ ██╗     ██████╗
  ██║  ██║██╔═══██╗████╗ ████║██╔════╝██║    ██║██╔═══██╗██╔══██╗██║     ██╔══██╗
  ███████║██║   ██║██╔████╔██║█████╗  ██║ █╗ ██║██║   ██║██████╔╝██║     ██║  ██║
  ██╔══██║██║   ██║██║╚██╔╝██║██╔══╝  ██║███╗██║██║   ██║██╔══██╗██║     ██║  ██║
  ██║  ██║╚██████╔╝██║ ╚═╝ ██║███████╗╚███╔███╔╝╚██████╔╝██║  ██║███████╗██████╔╝
  ╚═╝  ╚═╝ ╚═════╝ ╚═╝     ╚═╝╚══════╝ ╚══╝╚══╝  ╚═════╝ ╚═╝  ╚═╝╚══════╝╚═════╝
[/bold purple][dim]  MOTHERSHIP COMMAND INTERFACE  ·  Fleet Management System  ·  Phase 3[/dim]
"""

HELP_TEXT = """[dim]
Commands:
  status          — show fleet status panel
  ru <N>          — deposit N resource units
  obj <text>      — set current objective
  harvest <url>   — scrape a URL, deposit RUs, save to outputs/
  research        — synthesize recent harvests into a report
  destroy         — produce polished briefing from research
  dashboard       — live fleet status display (15 sec)
  outputs         — list all files in outputs/
  read <file>     — read an output file (rendered markdown)
  exit / quit     — shut down
  <anything else> — routed to Command Layer (planning LLM)
[/dim]"""


def show_fleet_status():
    ru       = store.get("ru_balance", 0)
    agents   = store.get("active_agents", [])
    obj      = store.get("current_objective", "awaiting orders")
    findings = store.get("findings_count", 0)

    counts = {}
    for a in agents:
        counts[a["type"]] = counts.get(a["type"], 0) + 1

    fleet_str = ("  ".join(f"{t}×{n}" for t, n in counts.items())
                 if counts else "[dim]no active agents[/dim]")

    console.print(Panel(
        f"[bold]RU Balance:[/bold] {ru}  |  [bold]Findings:[/bold] {findings}\n"
        f"[bold]Objective:[/bold]  {obj}\n"
        f"[bold]Fleet:[/bold]      {fleet_str}",
        title="[bold purple]Fleet Status[/bold purple]",
        border_style="purple",
    ))


def setup_api_key():
    if config.ANTHROPIC_API_KEY:
        return
    console.print(Panel(
        "[yellow]No ANTHROPIC_API_KEY found.[/yellow]\n"
        "Either create [bold]mothership/.env[/bold] from [bold].env.example[/bold],\n"
        "or enter your key now (stored only in .env, never committed).",
        title="[red]API Key Required[/red]",
    ))
    key = Prompt.ask("[bold]Paste your Anthropic API key[/bold]")
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    with open(env_path, "w") as f:
        f.write(f"ANTHROPIC_API_KEY={key}\n")
        f.write(f"MOTHERSHIP_MODEL=claude-sonnet-4-6\n")
    os.environ["ANTHROPIC_API_KEY"] = key
    config.ANTHROPIC_API_KEY = key
    console.print("[green]Key saved to .env[/green]")


def handle_command(msg: str) -> bool:
    """Handle built-in commands. Returns True if handled, False if should go to LLM."""
    lower = msg.lower()

    if lower in ("exit", "quit"):
        console.print("[dim]Mothership standing by. Farewell, Commander.[/dim]")
        sys.exit(0)

    if lower == "status":
        return True  # status shown each loop already

    if lower.startswith("ru "):
        try:
            amount  = int(msg.split()[1])
            current = store.get("ru_balance", 0)
            store.set("ru_balance", current + amount)
            console.print(f"[green]+{amount} RUs deposited. Balance: {current + amount}[/green]")
        except (IndexError, ValueError):
            console.print("[red]Usage: ru <number>[/red]")
        return True

    if lower.startswith("obj "):
        obj = msg[4:].strip()
        store.set("current_objective", obj)
        console.print(f"[green]Objective set: {obj}[/green]")
        return True

    if lower.startswith("harvest "):
        url      = msg[8:].strip()
        agent_id = f"collector_{uuid.uuid4().hex[:6]}"
        ship     = CollectorShip(agent_id)
        console.print(f"[green]Dispatching collector → {url}[/green]")
        result = asyncio.run(ship.run({"url": url}))
        console.print(f"[bold]Result:[/bold] {result}")
        return True

    if lower == "research":
        agent_id = f"researcher_{uuid.uuid4().hex[:6]}"
        ship     = ResearcherShip(agent_id)
        console.print("[green]Dispatching researcher...[/green]")
        result = asyncio.run(ship.run({}))
        console.print(f"[bold]Result:[/bold] {result}")
        return True

    if lower == "destroy":
        agent_id = f"destroyer_{uuid.uuid4().hex[:6]}"
        ship     = DestroyerShip(agent_id)
        console.print("[green]Dispatching destroyer — producing briefing...[/green]")
        result = asyncio.run(ship.run({}))
        console.print(f"[bold]Result:[/bold] {result}")
        return True

    if lower == "dashboard":
        from dashboard import show_dashboard
        show_dashboard(seconds=15)
        return True

    if lower == "outputs":
        files = sorted(os.listdir(config.OUTPUT_DIR)) if os.path.exists(config.OUTPUT_DIR) else []
        console.print(Panel(
            "\n".join(files) or "no outputs yet",
            title="[bold]Outputs Directory[/bold]"
        ))
        return True

    if lower.startswith("read "):
        fname = msg[5:].strip()
        fpath = os.path.join(config.OUTPUT_DIR, fname)
        try:
            with open(fpath, encoding="utf-8") as f:
                from rich.markdown import Markdown
                console.print(Markdown(f.read()))
        except FileNotFoundError:
            console.print(f"[red]File not found: {fpath}[/red]")
        return True

    return False


def send_to_command_layer(msg: str):
    console.print("[dim]Routing to Command Layer...[/dim]")
    try:
        order = planner.decide(user_message=msg)
    except json.JSONDecodeError as e:
        console.print(f"[red]Command Layer returned invalid JSON: {e}[/red]")
        return
    except Exception as e:
        console.print(f"[red]Command Layer error: {e}[/red]")
        return

    console.print(Panel(
        f"[bold]Build order:[/bold] {order.get('build_order', '?')}\n"
        f"[bold]Reason:[/bold]      {order.get('reason', '?')}\n"
        f"[bold]Priority:[/bold]    {order.get('priority', '?')}",
        title="[bold purple]Command Layer Response[/bold purple]",
        border_style="purple",
    ))

    result = dispatcher.dispatch(order)
    console.print(f"[bold]Router:[/bold] {result}")

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    ts       = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(config.OUTPUT_DIR, f"order_{ts}.json")
    with open(out_path, "w") as f:
        json.dump({
            "commander_input": msg,
            "build_order":     order,
            "router_result":   result,
            "timestamp":       ts,
        }, f, indent=2)
    console.print(f"[dim]Output written → {out_path}[/dim]")


def run():
    console.print(ASCII_BANNER)
    setup_api_key()

    store.init_db()

    console.print(HELP_TEXT)

    while True:
        try:
            show_fleet_status()
            msg = Prompt.ask("\n[bold purple]Commander[/bold purple]")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Mothership standing by. Farewell, Commander.[/dim]")
            break

        msg = msg.strip()
        if not msg:
            continue

        if not handle_command(msg):
            send_to_command_layer(msg)


if __name__ == "__main__":
    run()
