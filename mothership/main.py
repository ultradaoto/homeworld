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
[/bold purple][dim]  MOTHERSHIP COMMAND INTERFACE  ·  Fleet Management System  ·  Phase 4[/dim]
"""

HELP_TEXT = """[dim]
Commands:
  status                               — show fleet status panel
  ru <N>                               — deposit N resource units to agent fleet
  sync                                 — sync agent RU balance from live game
  obj <text>                           — set current objective
  scout [path]                            — map codebase sectors, earn RU (costs 200)
  tactic <aggressive|neutral|evasive>     — set fleet generation tactic
  harvest <url>                           — scrape a URL, deposit RUs
  research                                — synthesize recent harvests into a report
  destroy                                 — produce polished briefing
  carrier <sector> [objective]            — deploy sub-orchestrator to sector
  sphere                                  — 3-agent swarm verification
  mine <code>                             — lay pytest mines for given code
  salvage <url>                           — capture + structure hostile data
  engage <error text>                     — spawn enemies from errors
  intercept <error text>                  — debugging agent attacks the error
  run tests                               — run mine fields, resolve enemies on pass
  bentusi [list|<package>]                — trade RUs for packages
  skirmish <objective> | <url> | <url>    — Blue vs Red fleet competition
  hyperspace [commit message]             — snapshot + git commit + reset
  dashboard                               — live fleet status (15 sec)
  outputs                                 — list all files in outputs/
  read <file>                             — read an output file (rendered markdown)
  cmd <json>                              — send raw command to C engine
  exit / quit                             — shut down
  <anything else>                         — routed to Command Layer (planning LLM)
[/dim]"""


def _get_engine_state():
    """Returns engine state dict if game is connected, else None."""
    try:
        from bridge.state_file import read_engine_state
        return read_engine_state()
    except Exception:
        return None


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

    scout_str = ""
    engine = _get_engine_state()
    if engine:
        game_ru    = engine.get("ru_balance", 0)
        game_ships = engine.get("game_ships", 0)
        scout_ids  = engine.get("scout_ids", [])
        scout_str  = ""
        if scout_ids:
            names     = "  ".join(f"[cyan]KS-{sid}[/cyan]" for sid in scout_ids)
            scout_str = f"\n[bold]Scouts:[/bold]     {names}"
        conn_line  = (f"[bold green]GAME: CONNECTED[/bold green]  |  "
                      f"[bold]Game RU:[/bold] {game_ru}  "
                      f"[bold]Ships:[/bold] {game_ships}")
    else:
        conn_line = "[bold red]GAME: OFFLINE[/bold red]  [dim](launch Homeworld to connect)[/dim]"

    console.print(Panel(
        f"{conn_line}{scout_str}\n"
        f"[bold]Agent RU:[/bold]   {ru}  |  [bold]Findings:[/bold] {findings}\n"
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
            amount = int(msg.split()[1])
        except (IndexError, ValueError):
            console.print("[red]Usage: ru <number>[/red]")
            return True
        engine = _get_engine_state()
        if not engine:
            console.print("[yellow]Game not connected — RUs tracked locally only.[/yellow]")
        current = store.get("ru_balance", 0)
        store.set("ru_balance", current + amount)
        console.print(f"[green]+{amount} RUs deposited. Agent balance: {current + amount}[/green]")
        return True

    if lower == "sync":
        engine = _get_engine_state()
        if not engine:
            console.print("[red]Game not connected — cannot sync.[/red]")
        else:
            game_ru = engine.get("ru_balance", 0)
            store.set("ru_balance", game_ru)
            console.print(f"[green]Agent RU synced from game: {game_ru}[/green]")
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

    if lower.startswith("cmd "):
        raw = msg[4:].strip()
        try:
            from bridge.state_file import write_command
            write_command(json.loads(raw))
            console.print(f"[green]Command sent to engine: {raw}[/green]")
        except Exception as e:
            console.print(f"[red]cmd error: {e}[/red]")
        return True

    if lower == "scout" or lower.startswith("scout "):
        parts   = msg.split()
        # scout [count] [path]  — count is optional integer, path is rest
        count   = 1
        path    = ""
        if len(parts) > 1:
            try:
                count = int(parts[1])
                path  = " ".join(parts[2:])
            except ValueError:
                path  = " ".join(parts[1:])
        count   = max(1, min(count, 9))
        ru      = store.get("ru_balance", 0)
        cost    = 200 * count
        if ru < cost:
            console.print(f"[red]Insufficient RU — {count} scout(s) costs {cost}, you have {ru}.[/red]")
            return True
        store.set("ru_balance", ru - cost)
        from ships.scout import ScoutShip
        aid  = f"scout_{uuid.uuid4().hex[:6]}"
        ship = ScoutShip(aid)
        target = path or "project root"
        console.print(f"[green]Scout deploying → {target}  ({count}x -200 RU = -{cost} RU)[/green]")
        try:
            from bridge.state_file import write_command
            write_command({"type": "build_ship", "class": "LightInterceptor", "count": count})
            console.print(f"[dim]{count} build order(s) sent to game engine.[/dim]")
        except Exception:
            pass
        result = asyncio.run(ship.run({"path": path} if path else {}))
        if result.get("status") == "ok":
            console.print(
                f"[bold]Sectors mapped:[/bold] {result['sectors']}  "
                f"[bold]Files:[/bold] {result['files']}  "
                f"[bold green]+{result['ru_earned']} RU[/bold green]"
            )
            console.print(f"[dim]Map  → {result['map']}[/dim]")
            console.print(f"[dim]Report → {result['report']}[/dim]")
        else:
            console.print(f"[red]Scout error:[/red] {result}")
        return True

    if lower.startswith("tactic "):
        from ships.tactics import set_tactic, get_current
        name = msg[7:].strip().lower()
        set_tactic(name)
        console.print(f"[green]Fleet tactic set: {get_current()}[/green]")
        return True

    if lower.startswith("carrier "):
        parts     = msg[8:].strip().split(" ", 1)
        sector    = parts[0]
        task_obj  = parts[1] if len(parts) > 1 else store.get("current_objective", "")
        from ships.carrier import CarrierShip
        aid  = f"carrier_{uuid.uuid4().hex[:6]}"
        ship = CarrierShip(aid)
        console.print(f"[green]Deploying Carrier to sector: {sector}[/green]")
        result = asyncio.run(ship.run({"sector": sector, "objective": task_obj}))
        console.print(f"[bold]Result:[/bold] {result}")
        return True

    if lower == "sphere":
        from ships.sphere import sphere_verify
        console.print("[green]Launching sphere formation (3 researchers)...[/green]")
        result = asyncio.run(sphere_verify(n=3))
        console.print(f"[bold]Winner:[/bold] {result.get('reason', result)}")
        return True

    if lower.startswith("mine "):
        from ships.minelayer import MinelayerShip
        code = msg[5:].strip()
        aid  = f"minelayer_{uuid.uuid4().hex[:6]}"
        ship = MinelayerShip(aid)
        console.print("[green]Minelayer deploying...[/green]")
        result = asyncio.run(ship.run({"code": code, "module": "user_input"}))
        console.print(f"[bold]Mines laid:[/bold] {result}")
        return True

    if lower.startswith("salvage "):
        from ships.salvage import SalvageShip
        url  = msg[8:].strip()
        aid  = f"salvage_{uuid.uuid4().hex[:6]}"
        ship = SalvageShip(aid)
        console.print(f"[green]Salvage Corvette deploying → {url}[/green]")
        result = asyncio.run(ship.run({"url": url, "label": "capture"}))
        console.print(f"[bold]Salvage result:[/bold] {result}")
        return True

    if lower.startswith("engage "):
        error_text = msg[7:].strip()
        from combat.threat_engine import analyse_errors, spawn_enemies, enemy_count
        threats = analyse_errors(error_text)
        n = spawn_enemies(threats)
        console.print(f"[red]{n} enemy ship{'s' if n != 1 else ''} spawned[/red]  "
                      f"[dim]total threats: {enemy_count()}[/dim]")
        return True

    if lower in ("run tests", "testfire"):
        from combat.ci_runner import run_tests
        console.print("[yellow]Running mine fields...[/yellow]")
        result = run_tests()
        if result.get("status") == "no_mines":
            console.print("[dim]No mines laid yet — use 'mine <code>' first.[/dim]")
        else:
            clr = "green" if result.get("failed", 1) == 0 else "red"
            console.print(
                f"[{clr}]passed:{result.get('passed',0)}  "
                f"failed:{result.get('failed',0)}  "
                f"enemies spawned:{result.get('enemies_spawned',0)}  "
                f"resolved:{result.get('enemies_resolved',0)}[/{clr}]"
            )
        return True

    if lower.startswith("intercept "):
        error = msg[10:].strip()
        from combat.interceptor_agent import InterceptorAgent
        aid  = f"interceptor_{uuid.uuid4().hex[:6]}"
        ship = InterceptorAgent(aid)
        console.print("[yellow]Interceptor engaging...[/yellow]")
        result = asyncio.run(ship.run({"error": error}))
        console.print(f"[bold]Analysis:[/bold] {result.get('analysis', result)}")
        return True

    if lower.startswith("bentusi"):
        arg = msg[7:].strip()
        from bentusi.exchange import visit_bentusi, list_catalog
        if not arg or arg == "list":
            catalog = list_catalog()
            rows = "\n".join(
                f"  [bold]{pkg:12}[/bold]  {info['cost']:4} RU  →  {info['capability']}"
                for pkg, info in catalog.items()
            )
            console.print(Panel(rows, title="[bold]Bentusi Exchange[/bold]",
                                border_style="yellow"))
        else:
            console.print(f"[yellow]Opening Bentusi channel for {arg}...[/yellow]")
            result = visit_bentusi(arg)
            clr = "green" if result.get("status") == "traded" else "red"
            console.print(f"[{clr}]{result}[/{clr}]")
        return True

    if lower.startswith("skirmish "):
        parts = msg[9:].strip().split("|")
        objective = parts[0].strip()
        urls = [u.strip() for u in parts[1:] if u.strip()]
        if not urls:
            console.print("[red]Usage: skirmish <objective> | <url1> | <url2>[/red]")
            return True
        console.print("[yellow]Launching skirmish — Blue vs Red fleet...[/yellow]")
        from arena.skirmish import run_skirmish
        result = asyncio.run(run_skirmish(objective, urls))
        winner = result.get("winner", "?")
        clr    = "blue" if winner == "blue" else "red"
        console.print(f"[bold {clr}]WINNER: {winner.upper()}[/bold {clr}]  "
                      f"blue:{result.get('blue_score','?')}  "
                      f"red:{result.get('red_score','?')}")
        console.print(f"[dim]{result.get('verdict','')}[/dim]")
        return True

    if lower.startswith("hyperspace"):
        commit_msg = msg[10:].strip()
        from hyperspace import jump
        console.print("[bold purple]HYPERSPACE MODULE CHARGING...[/bold purple]")
        result = jump(commit_message=commit_msg)
        console.print(f"[bold purple]JUMP COMPLETE — {result['ts']}[/bold purple]")
        console.print(
            f"[dim]RUs carried: {result['ru_carried']}  |  "
            f"Archive: {result['archive']}  |  "
            f"Git: {result['git'].get('msg') or result['git']}[/dim]"
        )
        return True

    if lower.startswith("read "):
        fname = msg[5:].strip()
        fpath = os.path.join(config.OUTPUT_DIR, fname)
        try:
            with open(fpath, encoding="utf-8") as f:
                from rich.markdown import Markdown
                console.print(Markdown(f.read()))
        except (FileNotFoundError, OSError):
            # Show closest matches to help the commander
            files = sorted(os.listdir(config.OUTPUT_DIR)) if os.path.exists(config.OUTPUT_DIR) else []
            matches = [f for f in files if fname.split("_")[0] in f] if "_" in fname else files[-5:]
            hint = "  " + "\n  ".join(matches[:5]) if matches else "  (no outputs yet)"
            console.print(f"[red]File not found:[/red] {fname}\n[dim]Did you mean:\n{hint}[/dim]")
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

    from bridge import start as bridge_start
    bridge_mode = os.environ.get("BRIDGE_MODE", "file")
    bridge_start(mode=bridge_mode)
    console.print(f"[dim]Karan Bridge active ({bridge_mode} mode) → {os.environ.get('BRIDGE_MODE', 'file')}[/dim]")

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
