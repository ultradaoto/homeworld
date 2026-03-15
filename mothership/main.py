#!/usr/bin/env python3
import sys
import os

# Force UTF-8 on Windows consoles (cp1252 can't handle arrows/unicode)
for _stream in (sys.stdout, sys.stderr):
    if _stream and hasattr(_stream, 'reconfigure'):
        try: _stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception: pass
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
[/bold purple][dim]  MOTHERSHIP COMMAND INTERFACE  ·  Fleet Management System  ·  Phase 7[/dim]
"""

HELP_TEXT = """[dim]
Commands:
  status                                — show fleet status panel
  ru <N>                                — deposit N resource units to agent fleet
  sync                                  — sync agent RU balance from live game
  obj <text>                            — set current objective

  ── Build ──────────────────────────────────────────────────────────────────
  ships                                 — list ship classes with cost/role
  <ship_type> [count]                   — queue a build (scout, interceptor, etc.)
  fleet                                 — full fleet registry with status
  queue                                 — current build queue
  assign <callsign|ID> <task>           — assign task to a specific ship
  doctrine                              — threat assessment + build recommendation

  ── Map / Fog of War ───────────────────────────────────────────────────────
  mapindex [root_dir]                   — index codebase: dirs=sectors, files=asteroids
  fogmap                                — list nearest unscouted (dark) sectors
  sendscout <callsign|ID> <rel-path>    — fly scout into sector, illuminate files
  intel <rel-path>                      — read stored scout briefing + asteroid list

  ── Agents ─────────────────────────────────────────────────────────────────
  scan [path]                           — Python codebase scan, earn RU
  tactic <aggressive|neutral|evasive>   — set fleet generation tactic
  harvest <url>                         — scrape a URL, deposit RUs
  research                              — synthesize recent harvests into a report
  destroy                               — produce polished briefing
  carrier <sector> [objective]          — deploy sub-orchestrator to sector
  sphere                                — 3-agent swarm verification
  mine <code>                           — lay pytest mines for given code
  salvage <url>                         — capture + structure hostile data

  ── Combat ─────────────────────────────────────────────────────────────────
  enemies                               — show all inbound Taiidan contacts
  intercept <callsign> vs <enemy_id>    — run LLM attack/defense battle
  spawn enemy <rel-path>                — (test) spawn a Taiidan scout
  engage <error text>                   — spawn enemies from code errors
  run tests                             — run mine fields, resolve enemies on pass

  ── Economy / Special ──────────────────────────────────────────────────────
  bentusi [list|<package>]              — trade RUs for packages
  skirmish <objective> | <url> | <url>  — Blue vs Red fleet competition
  hyperspace [commit message]           — snapshot + git commit + reset

  ── Docker Fleet (Phase 10B) ────────────────────────────────────────────
  containers                            — list running hw-* Docker containers
  order <callsign|ID> <json>            — send task order to a ship's container
  pods                                  — list unrecovered escape pods
  recover <salvage_callsign> <pod_id>   — dispatch salvage ship to recover a pod
  start api                             — launch mothership FastAPI server (:3000)

  ── Entropy System (Phase 11) ───────────────────────────────────────────
  techtree                              — show full tech-tree progression
  research <node>                       — trigger research synthesis for a node
  unlock status                         — show currently buildable ship classes
  station <callsign> <dir>              — assign ship's permanent home sector
  recall <callsign>                     — clear ship's assigned directory
  specialize <callsign>                 — show ship's context domain
  duel <callsign> vs <enemy_id>         — initiate capital ship engagement
  capital status                        — destroyer unlock state + entropy fleet
  postbattle <sector>                   — review refined docs after duel
  entropy                               — show active entropy fleet

  ── Phase 12: Doctrine ──────────────────────────────────────────────────────
  effectiveness <callsign> <path>       — ship-sector specialization score (0.2–1.0)
  research complete <node>              — mark research done, unlock ship class
  build techtree                        — show build-gate tech tree
  cockpit <callsign>                    — inspect a ship's persistent memory
  clear cockpit <callsign>              — wipe ship memory before reassignment

  ── Phase 13: Patch Pipeline ────────────────────────────────────────────────
  patchlog                              — last 20 patch pipeline runs
  patchlog <sector>                     — filter by sector path substring
  patchlog ship <callsign>              — filter by ship
  patch diff <pipeline_id>              — render stored unified diff

  ── Utility ────────────────────────────────────────────────────────────────
  dashboard                             — live fleet status (15 sec)
  outputs                               — list all files in outputs/
  read <file>                           — read an output file (rendered markdown)
  cmd <json>                            — send raw command to C engine
  exit / quit                           — shut down
  <anything else>                       — routed to Command Layer (planning LLM)
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

    # Build queue status
    try:
        from bridge.build_queue import queue_status
        qs = queue_status()
        if qs["building"]:
            bq_str = (f"\n[bold]Building:[/bold]   [cyan]{qs['building']}[/cyan]"
                      + (f"  +{qs['depth']} waiting" if qs["depth"] else ""))
        else:
            bq_str = ""
    except Exception:
        bq_str = ""

    # Registry: active ships by type
    try:
        from ships.registry import get_fleet
        active_ships = get_fleet(status="active")
        if active_ships:
            ship_names = "  ".join(
                f"[bold]{s['callsign']}[/bold][dim]({s['type']})[/dim]"
                for s in active_ships[-6:]
            )
            registry_str = f"\n[bold]Registry:[/bold]  {ship_names}"
        else:
            registry_str = ""
    except Exception:
        registry_str = ""

    engine = _get_engine_state()
    if engine:
        game_ru    = engine.get("ru_balance", 0)
        game_ships = engine.get("game_ships", 0)
        scout_ids  = engine.get("scout_ids", [])
        q_depth     = engine.get("queue_depth", 0)
        building    = engine.get("building", "")
        map_sec     = engine.get("map_sectors", 0)
        map_ast     = engine.get("map_asteroids", 0)
        map_scouted = engine.get("map_scouted", 0)
        scout_str   = ""
        if scout_ids:
            names     = "  ".join(f"[cyan]KS-{sid}[/cyan]" for sid in scout_ids)
            scout_str = f"  Scouts: {names}"
        queue_info  = f"  Queue: {q_depth}" + (f" ({building})" if building else "")
        map_line    = (f"\n[bold]Map:[/bold]        "
                       f"{map_sec} sectors  {map_ast} asteroids  "
                       f"{map_scouted} scouted"
                       if map_sec else "")
        conn_line   = (f"[bold green]GAME: CONNECTED[/bold green]  |  "
                       f"[bold]Game RU:[/bold] {game_ru}  "
                       f"[bold]Ships:[/bold] {game_ships}"
                       f"{queue_info}{scout_str}{map_line}")
    else:
        conn_line = "[bold red]GAME: OFFLINE[/bold red]  [dim](launch Homeworld to connect)[/dim]"

    console.print(Panel(
        f"{conn_line}\n"
        f"[bold]Agent RU:[/bold]   {ru}  |  [bold]Findings:[/bold] {findings}"
        f"{bq_str}{registry_str}\n"
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

    if lower == "scan" or lower.startswith("scan "):
        parts = msg.split()
        path  = " ".join(parts[1:]) if len(parts) > 1 else ""
        from ships.scout import ScoutShip
        aid  = f"scout_{uuid.uuid4().hex[:6]}"
        ship = ScoutShip(aid)
        target = path or "project root"
        console.print(f"[green]Scout deploying → {target}[/green]")
        result = asyncio.run(ship.run({"path": path} if path else {}))
        if result.get("status") == "ok":
            console.print(
                f"[bold]Sectors mapped:[/bold] {result['sectors']}  "
                f"[bold]Files:[/bold] {result['files']}  "
                f"[bold green]+{result['ru_earned']} RU[/bold green]"
            )
            console.print(f"[dim]Map    → {result['map']}[/dim]")
            console.print(f"[dim]Report → {result['report']}[/dim]")
        else:
            console.print(f"[red]Scan error:[/red] {result}")
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
        from hyperspace import jump, hyperspace_preflight
        preflight = hyperspace_preflight()
        if not preflight["ready"]:
            console.print(f"[red]HYPERSPACE BLOCKED[/red]")
            console.print(f"[yellow]{preflight['message']}[/yellow]")
            console.print(
                "[dim]Destroy all enemies and pass CI tests before jumping.[/dim]"
            )
            return True
        console.print("[bold purple]HYPERSPACE MODULE CHARGING...[/bold purple]")
        result = jump(commit_message=commit_msg)
        console.print(f"[bold purple]JUMP COMPLETE — {result['ts']}[/bold purple]")
        console.print(
            f"[dim]RUs carried: {result['ru_carried']}  |  "
            f"Archive: {result['archive']}  |  "
            f"Git: {result['git'].get('msg') or result['git']}[/dim]"
        )
        # Signal C engine to play hyperspace animation
        try:
            from bridge.state_file import write_command
            write_command({"type": "hyperspace_jump", "ts": result["ts"]})
        except Exception:
            pass
        return True

    if lower.startswith("read "):
        fname = msg[5:].strip()
        fpath = os.path.join(config.OUTPUT_DIR, fname)
        try:
            with open(fpath, encoding="utf-8") as f:
                from rich.markdown import Markdown
                console.print(Markdown(f.read()))
        except (FileNotFoundError, OSError):
            files = sorted(os.listdir(config.OUTPUT_DIR)) if os.path.exists(config.OUTPUT_DIR) else []
            matches = [f for f in files if fname.split("_")[0] in f] if "_" in fname else files[-5:]
            hint = "  " + "\n  ".join(matches[:5]) if matches else "  (no outputs yet)"
            console.print(f"[red]File not found:[/red] {fname}\n[dim]Did you mean:\n{hint}[/dim]")
        return True

    # ── Phase 7: ship build commands ──────────────────────────────────────

    if lower == "ships":
        from ships.registry import SHIP_SPECS
        from rich.table import Table
        t = Table(title="Available Ship Classes", border_style="dim")
        t.add_column("Class",     style="bold")
        t.add_column("Cost (RU)", justify="right")
        t.add_column("Build",     justify="right")
        t.add_column("Role")
        t.add_column("Description", style="dim")
        for cls, spec in SHIP_SPECS.items():
            t.add_row(cls, str(spec["cost"]),
                      f"{spec['build']}s", spec["role"], spec["desc"])
        console.print(t)
        return True

    if lower == "fleet":
        from ships.registry import get_fleet
        from rich.table import Table
        rows = get_fleet()
        if not rows:
            console.print("[dim]Fleet registry is empty. Build some ships first.[/dim]")
            return True
        t = Table(title="Fleet Registry", border_style="purple")
        t.add_column("ID",       style="dim")
        t.add_column("Callsign", style="bold")
        t.add_column("Type")
        t.add_column("Status")
        t.add_column("Armor",  justify="right")
        t.add_column("Kills",  justify="right")
        t.add_column("Task",   style="dim")
        for s in rows:
            color = {"active": "green", "queued": "yellow",
                     "building": "cyan", "lost": "red"}.get(s["status"], "white")
            t.add_row(
                s["short_id"], s["callsign"], s["type"],
                f"[{color}]{s['status']}[/{color}]",
                f"{s['armor']}/{s['max_armor']}",
                str(s["kills"]),
                s["task"] or "—",
            )
        console.print(t)
        return True

    if lower == "queue":
        from bridge.build_queue import queue_status
        qs = queue_status()
        building = qs["building"] or "[dim]nothing[/dim]"
        queued   = ", ".join(qs["queued"]) if qs["queued"] else "[dim]empty[/dim]"
        console.print(f"[bold]Building:[/bold] {building}")
        console.print(f"[bold]Queued:[/bold]   {queued}  ({qs['depth']} waiting)")
        return True

    if lower.startswith("assign "):
        parts = msg[7:].strip().split(" ", 1)
        if len(parts) < 2:
            console.print("[red]Usage: assign <callsign|ID> <task>[/red]")
            return True
        from ships.registry import get_ship, update_ship
        ship = get_ship(parts[0])
        if not ship:
            console.print(f"[red]Unknown ship: {parts[0]}[/red]")
        else:
            update_ship(ship["short_id"], task=parts[1])
            console.print(f"[green]{ship['callsign']} assigned: {parts[1]}[/green]")
        return True

    if lower == "doctrine":
        from combat.doctrine import recommend_build, ESCALATION_SEQUENCE
        enemies = store.get("enemy_threats", [])
        rec = recommend_build(enemies)
        threat_count = len(enemies)
        console.print(
            f"[bold]Active threats:[/bold] {threat_count}  "
            f"[bold]Recommended build:[/bold] [yellow]{rec}[/yellow]"
        )
        console.print(
            f"[dim]Escalation: {' → '.join(ESCALATION_SEQUENCE)}[/dim]"
        )
        return True

    # ── Phase 9: adversarial combat commands ─────────────────────────────

    if lower == "enemies":
        from combat.enemy_ai import get_all_enemies
        from rich.table import Table
        all_enemies = get_all_enemies()
        if not all_enemies:
            console.print("[dim]No enemy contacts.[/dim]")
            return True
        sectors = store.get("sector_map", {})
        t = Table(
            title=f"Taiidan Fleet — {len(all_enemies)} contacts",
            border_style="red",
        )
        t.add_column("Enemy ID",  style="red bold")
        t.add_column("Type")
        t.add_column("Target Sector")
        t.add_column("Sev", justify="right")
        t.add_column("Status")
        for e in all_enemies:
            s    = sectors.get(e["target"], {})
            path = s.get("rel_path", "?")
            t.add_row(
                e["id"], e["type"], path,
                str(e["severity"]), e["status"],
            )
        console.print(t)
        return True

    if lower.startswith("intercept ") and " vs " in lower:
        parts    = msg[10:].strip().split(" vs ", 1)
        callsign = parts[0].strip()
        enemy_id = parts[1].strip()
        from ships.registry import get_ship
        from combat.battle  import resolve_combat
        ship = get_ship(callsign)
        if not ship:
            console.print(f"[red]Unknown ship: {callsign}[/red]")
            return True
        enemies = store.get("enemy_fleet", [])
        sector  = next(
            (e["target"] for e in enemies
             if e["id"].upper() == enemy_id.upper()),
            "",
        )
        console.print(f"[yellow]Engaging {enemy_id}...[/yellow]")
        result = resolve_combat(ship["short_id"], enemy_id, sector)
        color  = "green" if result.get("winner") == "player" else "red"
        console.print(f"[{color}]{result.get('message', '')}[/{color}]")
        if result.get("ru_earned"):
            console.print(
                f"[green]+{result['ru_earned']} RU bounty[/green]"
            )
        return True

    if lower.startswith("spawn enemy "):
        rel = msg[12:].strip()
        from combat.enemy_ai import spawn_enemy_scout
        from map.sector_map  import get_sector_by_path
        root   = store.get("sector_map_root", "D:/Projects/Homeworld")
        target = os.path.join(root, rel.replace("/", os.sep))
        sector = get_sector_by_path(target)
        if not sector:
            console.print(
                f"[red]Sector not found: {rel}  (run mapindex first)[/red]"
            )
            return True
        e = spawn_enemy_scout(2, sector["id"])
        console.print(f"[red]Taiidan {e['id']} inbound → {rel}[/red]")
        return True

    # ── Phase 8: spatial map commands ────────────────────────────────────

    if lower.startswith("mapindex"):
        root = msg[8:].strip() or "D:/Projects/Homeworld"
        os.makedirs(root, exist_ok=True)
        # Also ensure any stationed scout directories exist before indexing
        try:
            import sqlite3 as _sq
            from memory.store import DB_PATH as _dbp
            _con = _sq.connect(_dbp)
            _dirs = [r[0] for r in _con.execute(
                "SELECT assigned_directory FROM active_ships "
                "WHERE assigned_directory IS NOT NULL AND assigned_directory != ''"
            ).fetchall()]
            _con.close()
            for _d in _dirs:
                os.makedirs(_d, exist_ok=True)
        except Exception:
            pass
        from map.sector_map import index_project, write_map_for_engine, map_stats
        console.print(f"[yellow]Indexing codebase at {root}...[/yellow]")
        index_project(root)
        stats    = map_stats()
        map_path = os.path.join(config.STATE_DIR, "sector_map.json")
        write_map_for_engine(map_path)
        console.print(
            f"[green]{stats['sectors']} sectors  "
            f"{stats['total_files']} file-asteroids  "
            f"{stats['total_lines']:,} lines  → {map_path}[/green]"
        )
        return True

    if lower == "fogmap":
        from map.sector_map import get_nearest_unscouted, map_stats
        from rich.table import Table
        stats = map_stats()
        if not stats["indexed"]:
            console.print("[yellow]No map — run: mapindex <root>[/yellow]")
            return True
        nearest = get_nearest_unscouted({"x": 0, "y": 0, "z": 0}, n=15)
        if not nearest:
            console.print("[green]No dark sectors — full visibility.[/green]")
            return True
        t = Table(
            title=f"Fog of War — {stats['dark']} unscouted / {stats['sectors']} sectors",
            border_style="dim",
        )
        t.add_column("Path",              style="bold")
        t.add_column("Files", justify="right")
        t.add_column("Est. RU", justify="right")
        t.add_column("3D Position",       style="dim")
        for s in nearest:
            p = s["pos"]
            t.add_row(
                s["rel_path"], str(s["file_count"]),
                str(s["ru_value"]),
                f"({p['x']},{p['y']},{p['z']})",
            )
        console.print(t)
        return True

    if lower.startswith("sendscout "):
        parts = msg[10:].strip().split(" ", 1)
        if len(parts) < 2:
            console.print("[red]Usage: sendscout <callsign|ID> <rel-path>[/red]")
            return True
        callsign, rel_path = parts[0], parts[1]
        from ships.registry  import get_ship
        from map.sector_map  import get_sector_by_path
        from map.scout_agent import scout_sector
        ship = get_ship(callsign)
        if not ship:
            console.print(f"[red]Unknown ship: {callsign}[/red]")
            return True
        if ship["status"] not in ("active", "building"):
            console.print(
                f"[red]{ship['callsign']} not available "
                f"(status: {ship['status']})[/red]"
            )
            return True
        root   = store.get("sector_map_root", "D:/Projects/Homeworld")
        target = os.path.join(root, rel_path.replace("/", os.sep))
        sector = get_sector_by_path(target)
        if not sector:
            console.print(
                f"[red]Sector not indexed: {rel_path}  "
                f"(run mapindex first)[/red]"
            )
            return True
        console.print(
            f"[green]{ship['callsign']} moving to sector: {rel_path}[/green]"
        )
        result = scout_sector(sector, ship["short_id"])
        border = "red" if result["threat"] >= 3 else "green"
        console.print(Panel(
            result["briefing"],
            title=(
                f"[bold]Scout Report — {result['sector']}[/bold]  "
                f"[{'red' if result['threat'] >= 3 else 'yellow'}]"
                f"THREAT {result['threat']}[/{'red' if result['threat'] >= 3 else 'yellow'}]"
            ),
            border_style=border,
        ))
        console.print(
            f"[dim]+{result['ru_earned']} RU  |  "
            f"{result['files']} file-asteroids illuminated[/dim]"
        )
        return True

    if lower.startswith("intel "):
        rel_path = msg[6:].strip()
        from map.sector_map import get_sector_by_path
        root   = store.get("sector_map_root", "D:/Projects/Homeworld")
        target = os.path.join(root, rel_path.replace("/", os.sep))
        sector = get_sector_by_path(target)
        if not sector:
            console.print(
                f"[red]Not indexed — run: mapindex <root>[/red]"
            )
        elif not sector["scouted"]:
            console.print(
                f"[yellow]FOG OF WAR: {rel_path}  "
                f"({sector['file_count']} asteroids hidden)[/yellow]"
            )
        else:
            border = "red" if sector["threat_level"] >= 3 else "green"
            console.print(Panel(
                sector["intel"] or "(no briefing stored)",
                title=(
                    f"[bold]Intel: {rel_path}[/bold]  "
                    f"Threat {sector['threat_level']}  "
                    f"{sector['file_count']} files  "
                    f"RU {sector['ru_value']}"
                ),
                border_style=border,
            ))
            asteroids = list(sector["asteroids"].values())
            for a in asteroids[:10]:
                icon  = "[green]✓[/green]" if a["scouted"] else "[dim]?[/dim]"
                intel = (a["intel"] or "(unscouted)")[:80]
                console.print(f"  {icon} {a['filename']}: [dim]{intel}[/dim]")
            if len(asteroids) > 10:
                console.print(
                    f"  [dim]... and {len(asteroids) - 10} more files[/dim]"
                )
        return True

    # ── Phase 10B: Docker fleet commands ─────────────────────────────────

    if lower == "containers":
        from docker.launcher import list_running_ships
        from rich.table import Table
        ships = list_running_ships()
        if not ships:
            console.print("[dim]No running hw-* containers.[/dim]")
        else:
            t = Table(title=f"Running Fleet Containers ({len(ships)})",
                      border_style="cyan")
            t.add_column("Name",   style="bold")
            t.add_column("Status")
            t.add_column("ID",     style="dim")
            for s in ships:
                t.add_row(s["name"], s["status"], s["id"])
            console.print(t)
        return True

    if lower.startswith("order "):
        parts = msg[6:].strip().split(" ", 1)
        if len(parts) < 2:
            console.print("[red]Usage: order <callsign|ID> <json>[/red]")
            return True
        callsign_or_id, raw_json = parts[0], parts[1]
        try:
            task = json.loads(raw_json)
        except json.JSONDecodeError as e:
            console.print(f"[red]Invalid JSON: {e}[/red]")
            return True
        from ships.registry import get_ship
        ship = get_ship(callsign_or_id)
        if not ship:
            console.print(f"[red]Unknown ship: {callsign_or_id}[/red]")
            return True
        cid = ship.get("container_id", "")
        if not cid:
            console.print(
                f"[yellow]{ship['callsign']} has no container — not yet launched.[/yellow]"
            )
            return True
        try:
            import httpx
            resp = httpx.post(
                f"http://localhost:3000/api/fleet/{cid}/orders/assign",
                json=task, timeout=5,
            )
            if resp.status_code == 200:
                console.print(
                    f"[green]Order sent to {ship['callsign']} ({cid}):[/green] {task}"
                )
            else:
                console.print(
                    f"[red]API error {resp.status_code}:[/red] {resp.text[:200]}"
                )
        except Exception as e:
            console.print(f"[red]Could not reach mothership API: {e}[/red]")
        return True

    if lower == "pods":
        from memory import store as _store
        try:
            import sqlite3
            db_path = os.path.join(os.path.dirname(__file__), "state", "fleet.db")
            con = sqlite3.connect(db_path)
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT pod_id, callsign, ship_type, ejected_at, sector, ctx_summary "
                "FROM escape_pods WHERE recovered = 0 ORDER BY ejected_at DESC"
            ).fetchall()
            con.close()
        except Exception as e:
            console.print(f"[red]DB error: {e}[/red]")
            return True
        if not rows:
            console.print("[dim]No unrecovered escape pods.[/dim]")
        else:
            from rich.table import Table
            t = Table(title=f"Escape Pods — {len(rows)} unrecovered",
                      border_style="yellow")
            t.add_column("Pod ID",   style="bold yellow")
            t.add_column("Callsign")
            t.add_column("Type")
            t.add_column("Ejected")
            t.add_column("Sector",   style="dim")
            t.add_column("Summary",  style="dim")
            for r in rows:
                t.add_row(
                    r["pod_id"], r["callsign"], r["ship_type"],
                    r["ejected_at"][:19] if r["ejected_at"] else "?",
                    r["sector"] or "?",
                    (r["ctx_summary"] or "")[:60],
                )
            console.print(t)
        return True

    if lower.startswith("recover "):
        parts = msg[8:].strip().split(" ", 1)
        if len(parts) < 2:
            console.print("[red]Usage: recover <salvage_callsign|ID> <pod_id>[/red]")
            return True
        salvage_id, pod_id = parts[0], parts[1]
        from ships.registry import get_ship
        ship = get_ship(salvage_id)
        if not ship:
            console.print(f"[red]Unknown ship: {salvage_id}[/red]")
            return True
        if ship.get("type") != "salvage":
            console.print(
                f"[yellow]Warning: {ship['callsign']} is a {ship['type']}, "
                f"not a salvage corvette.[/yellow]"
            )
        try:
            import httpx
            resp = httpx.post(
                f"http://localhost:3000/api/pods/{pod_id}/recover",
                json={"salvage_ship": ship["short_id"]}, timeout=5,
            )
            if resp.status_code == 200:
                data = resp.json()
                console.print(
                    f"[green]Pod {pod_id} recovered by {ship['callsign']}.[/green]"
                )
                if data.get("context"):
                    console.print(
                        f"[dim]Context restored: "
                        f"{str(data['context'])[:120]}[/dim]"
                    )
            else:
                console.print(
                    f"[red]API error {resp.status_code}:[/red] {resp.text[:200]}"
                )
        except Exception as e:
            console.print(f"[red]Could not reach mothership API: {e}[/red]")
        return True

    if lower == "start api":
        import subprocess
        api_dir = os.path.dirname(__file__)
        console.print("[green]Starting mothership API server on :3000...[/green]")
        proc = subprocess.Popen(
            [sys.executable, "start_api.py"],
            cwd=api_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        console.print(
            f"[dim]API PID {proc.pid} — "
            f"http://localhost:3000/docs for Swagger UI[/dim]"
        )
        return True

    # ── Phase 11: Entropy System commands ────────────────────────────────

    if lower == "techtree":
        try:
            import httpx
            resp = httpx.get("http://localhost:3000/api/techtree", timeout=5)
            nodes = resp.json().get("nodes", [])
        except Exception:
            try:
                from research_substation import _db as _rdb
                conn = _rdb()
                nodes = [dict(r) for r in conn.execute("SELECT * FROM tech_tree").fetchall()]
                conn.close()
            except Exception:
                nodes = []
        from rich.table import Table
        from rich.panel import Panel
        STATUS_ICONS = {
            "locked":      "🔒",
            "unlocked":    "🔓",
            "in_progress": "⚙️ ",
            "completed":   "✅",
        }
        STATUS_COLORS = {
            "locked":      "dim",
            "unlocked":    "yellow",
            "in_progress": "cyan",
            "completed":   "green bold",
        }
        t = Table(
            title="[bold cyan]Research Tech Tree[/bold cyan]",
            border_style="cyan",
            show_lines=True,
        )
        t.add_column("Node",         style="bold cyan",  min_width=28)
        t.add_column("Status",       justify="center",   min_width=14)
        t.add_column("Requires",     style="dim",        min_width=22)
        t.add_column("Unlocks",      style="yellow",     min_width=16)
        t.add_column("Produces",     style="dim",        min_width=20)
        t.add_column("Artifact",     style="dim green",  min_width=20)
        completed_count = sum(1 for n in nodes if n.get("status") == "completed")
        for n in nodes:
            s = n.get("status", "locked")
            c = STATUS_COLORS.get(s, "white")
            icon = STATUS_ICONS.get(s, "?")
            try:
                import json as _j
                reqs = _j.loads(n.get("requires_json") or "[]")
                reqs_str = ", ".join(reqs) if reqs else "—"
            except Exception:
                reqs_str = n.get("requires_json") or "—"
            artifact = n.get("artifact_path") or ""
            if artifact:
                artifact = os.path.basename(artifact)
            completed_at = (n.get("completed_at") or "")[:10]
            node_label = n.get("node_id", "?")
            if completed_at:
                node_label += f"\n[dim]{completed_at}[/dim]"
            t.add_row(
                node_label,
                f"[{c}]{icon} {s}[/{c}]",
                reqs_str,
                n.get("unlocks") or "—",
                n.get("produces") or "—",
                artifact or "—",
            )
        console.print(Panel(
            t,
            title=f"[bold cyan]Tech Tree — {completed_count}/{len(nodes)} complete[/bold cyan]",
            border_style="cyan",
            padding=(0, 1),
        ))
        return True

    if lower.startswith("research "):
        node_id = msg[9:].strip()
        console.print(f"[yellow]Synthesizing node: {node_id}...[/yellow]")
        try:
            import httpx
            resp = httpx.post(
                "http://localhost:3000/api/techtree/research",
                json={"node_id": node_id}, timeout=120,
            )
            data = resp.json()
            if resp.status_code == 200:
                console.print(f"[green]Research complete: {data.get('artifact', '?')}[/green]")
            else:
                console.print(f"[red]Error: {data.get('detail', data)}[/red]")
        except Exception:
            # Fallback: call directly
            if os.path.dirname(__file__) not in sys.path:
                sys.path.insert(0, os.path.dirname(__file__))
            from research_substation import synthesize_node
            result = synthesize_node(node_id)
            if "error" in result:
                console.print(f"[red]{result['error']}[/red]")
            else:
                console.print(f"[green]Research complete -> {result.get('artifact')}[/green]")
        return True

    if lower == "unlock status":
        try:
            import httpx
            resp = httpx.get("http://localhost:3000/api/techtree/unlocked", timeout=5)
            data = resp.json()
            classes   = data.get("unlocked_classes", [])
            completed = data.get("completed_nodes", [])
        except Exception:
            classes, completed = ["harvester", "light_interceptor", "scout"], []
        console.print(Panel(
            f"[bold]Buildable:[/bold]  {', '.join(classes)}\n"
            f"[bold]Completed research nodes:[/bold]  "
            f"{', '.join(completed) if completed else '[dim]none[/dim]'}",
            title="[bold cyan]Tech Tree — Unlock Status[/bold cyan]",
            border_style="cyan",
        ))
        return True

    # ── Phase 12 commands ─────────────────────────────────────────────────────

    if lower.startswith("effectiveness "):
        parts = msg[14:].strip().split(" ", 1)
        if len(parts) < 2:
            console.print("[red]Usage: effectiveness <callsign> <sector_path>[/red]")
            return True
        from ships.registry import get_ship
        from combat.sector_match import specialization_report
        ship = get_ship(parts[0])
        if not ship:
            console.print(f"[red]Unknown ship: {parts[0]}[/red]")
            return True
        r     = specialization_report(ship["short_id"], parts[1])
        score = r["score"]
        color = "green" if score >= 0.7 else ("yellow" if score >= 0.4 else "red")
        console.print(Panel(
            f"[{color}]Score: {score} — {r['verdict']}[/{color}]\n\n"
            f"[bold]Ship tags:[/bold]    {r['ship_tags'] or '[dim]unspecialized[/dim]'}\n"
            f"[bold]Sector tags:[/bold]  {r['sector_tags']}\n"
            f"[bold]Matched:[/bold]      {r['matched'] or '[dim]none[/dim]'}\n"
            f"[bold]Mismatched:[/bold]   {r['mismatched'] or '[dim]none[/dim]'}",
            title=f"[bold]{ship['callsign']} vs {parts[1]}[/bold]",
            border_style=color,
        ))
        return True

    if lower.startswith("research complete "):
        node_id = msg[18:].strip()
        from command.tech_tree import complete_research, RESEARCH_UNLOCKS
        ok = complete_research(node_id)
        if ok:
            unlocked = RESEARCH_UNLOCKS.get(node_id, "?")
            console.print(
                f"[green]✓ Research '{node_id}' complete — "
                f"[bold]{unlocked}[/bold] unlocked![/green]"
            )
        else:
            console.print(f"[red]Unknown research node: {node_id}[/red]")
            console.print("[dim]Valid: " + ", ".join(RESEARCH_UNLOCKS) + "[/dim]")
        return True

    if lower == "build techtree":
        from command.tech_tree import tree_status
        console.print(tree_status())
        return True

    if lower.startswith("cockpit "):
        callsign = msg[8:].strip()
        from ships.registry import get_ship
        from memory.cockpit  import cockpit_summary, load_cockpit
        ship = get_ship(callsign)
        if not ship:
            console.print(f"[red]Unknown ship: {callsign}[/red]")
            return True
        summary  = cockpit_summary(ship["short_id"])
        messages = load_cockpit(ship["short_id"])
        tail     = messages[-3:] if len(messages) >= 3 else messages
        lines    = []
        for m in tail:
            role    = m.get("role", "?")
            content = m.get("content", "")[:120].replace("\n", " ")
            c       = "cyan" if role == "assistant" else "dim"
            lines.append(f"  [{c}]{role}:[/{c}] {content}")
        console.print(Panel(
            summary + ("\n\n" + "\n".join(lines) if lines else ""),
            title=f"[bold]{ship['callsign']} — Cockpit Memory[/bold]",
            border_style="purple",
        ))
        return True

    if lower.startswith("clear cockpit "):
        callsign = msg[14:].strip()
        from ships.registry import get_ship
        from memory.cockpit  import clear_cockpit
        ship = get_ship(callsign)
        if not ship:
            console.print(f"[red]Unknown ship: {callsign}[/red]")
            return True
        clear_cockpit(ship["short_id"])
        console.print(f"[yellow]{ship['callsign']} cockpit cleared.[/yellow]")
        return True

    # ── Phase 13: patchlog / patch diff ───────────────────────────────────────

    if lower.startswith("patchlog"):
        # patchlog                → last 20 entries across all ships
        # patchlog <sector>       → entries for a sector path substring
        # patchlog ship <callsign>→ entries for a specific ship
        import sqlite3 as _sq
        from config import STATE_DIR as _sd
        _HWDB = os.path.join(_sd, "homeworld.db")
        try:
            _parts = msg.strip().split(None, 2)
            _where = ""
            _params: tuple = ()
            if len(_parts) == 1:
                _where  = ""
                _params = ()
            elif len(_parts) == 3 and _parts[1].lower() == "ship":
                from ships.registry import get_ship as _gs
                _sh = _gs(_parts[2])
                _where  = "WHERE ship_id = ?"
                _params = (_sh["short_id"] if _sh else _parts[2],)
            else:
                _sector_filter = " ".join(_parts[1:])
                _where  = "WHERE sector_path LIKE ?"
                _params = (f"%{_sector_filter}%",)

            _con = _sq.connect(_HWDB)
            _con.row_factory = _sq.Row
            _rows = _con.execute(
                f"""SELECT pipeline_id, ship_id, sector_path, tests_passed,
                           tests_failed, committed, reverted, degraded,
                           runner_used, created_at
                    FROM patch_audit {_where}
                    ORDER BY created_at DESC LIMIT 20""",
                _params,
            ).fetchall()
            _con.close()

            if not _rows:
                console.print("[dim]No patch audit records found.[/dim]")
            else:
                from rich.table import Table
                _t = Table(title="Patch Audit Log", show_lines=False)
                _t.add_column("ID",      style="dim",   no_wrap=True)
                _t.add_column("Ship",    style="cyan",  no_wrap=True)
                _t.add_column("Sector",  style="white", overflow="fold")
                _t.add_column("Pass",    style="green", justify="right")
                _t.add_column("Fail",    style="red",   justify="right")
                _t.add_column("Result",  style="bold",  no_wrap=True)
                _t.add_column("Runner",  style="dim",   no_wrap=True)
                _t.add_column("When",    style="dim",   no_wrap=True)
                for _r in _rows:
                    if _r["committed"]:
                        _res = "[green]COMMITTED[/green]"
                    elif _r["degraded"]:
                        _res = "[red]DEGRADED[/red]"
                    elif _r["reverted"]:
                        _res = "[yellow]REVERTED[/yellow]"
                    else:
                        _res = "[dim]—[/dim]"
                    _t.add_row(
                        _r["pipeline_id"],
                        _r["ship_id"][:8],
                        _r["sector_path"][-40:],
                        str(_r["tests_passed"]),
                        str(_r["tests_failed"]),
                        _res,
                        _r["runner_used"] or "—",
                        _r["created_at"][:16],
                    )
                console.print(_t)
        except Exception as _exc:
            console.print(f"[red]patchlog error: {_exc}[/red]")
        return True

    if lower.startswith("patch diff "):
        _pid = msg[11:].strip()
        import sqlite3 as _sq
        from config import STATE_DIR as _sd2
        _HWDB2 = os.path.join(_sd2, "homeworld.db")
        try:
            _con = _sq.connect(_HWDB2)
            _con.row_factory = _sq.Row
            _row = _con.execute(
                "SELECT * FROM patch_audit WHERE pipeline_id = ? ORDER BY id DESC LIMIT 1",
                (_pid,),
            ).fetchone()
            _con.close()
            if not _row:
                console.print(f"[red]No patch audit record for pipeline_id: {_pid}[/red]")
            else:
                _patch = _row["patch_text"] or ""
                if _patch.strip():
                    from rich.syntax import Syntax
                    console.print(Panel(
                        Syntax(_patch, "diff", theme="monokai", line_numbers=True),
                        title=f"[bold]Patch diff — {_pid}[/bold]",
                        border_style="blue",
                    ))
                else:
                    console.print(f"[dim]No diff stored for pipeline {_pid}[/dim]")
        except Exception as _exc:
            console.print(f"[red]patch diff error: {_exc}[/red]")
        return True

    # ── end Phase 13 ──────────────────────────────────────────────────────────

    if lower.startswith("station "):
        parts = msg[8:].strip().split(" ", 1)
        if len(parts) < 2:
            console.print("[red]Usage: station <callsign|ID> <directory>[/red]")
            return True
        from ships.registry import get_ship
        ship = get_ship(parts[0])
        if not ship:
            console.print(f"[red]Unknown ship: {parts[0]}[/red]")
            return True
        assigned_dir = parts[1]
        os.makedirs(assigned_dir, exist_ok=True)
        try:
            import httpx
            resp = httpx.post(
                f"http://localhost:3000/api/fleet/{ship['short_id']}/station",
                json={"ship_id": ship["short_id"], "assigned_directory": assigned_dir},
                timeout=5,
            )
            if resp.status_code == 200:
                console.print(
                    f"[green]{ship['callsign']} stationed in: {assigned_dir}[/green]"
                )
            else:
                console.print(f"[red]API error: {resp.text[:200]}[/red]")
        except Exception as e:
            console.print(f"[yellow]API unreachable ({e}) — writing locally[/yellow]")
        # Phase 12: always write specialization to fleet.db directly
        try:
            from combat.sector_match import set_ship_specialization, specialization_report
            tags = set_ship_specialization(ship["short_id"], assigned_dir)
            r    = specialization_report(ship["short_id"], assigned_dir)
            sc   = r["score"]
            vc   = "green" if sc >= 0.7 else ("yellow" if sc >= 0.4 else "red")
            console.print(
                f"  [bold]Specialization:[/bold] {', '.join(tags) or 'none inferred'}\n"
                f"  [bold]Effectiveness:[/bold]  [{vc}]{sc} — {r['verdict']}[/{vc}]"
            )
        except Exception:
            pass
        return True

    if lower.startswith("recall "):
        callsign = msg[7:].strip()
        from ships.registry import get_ship
        ship = get_ship(callsign)
        if not ship:
            console.print(f"[red]Unknown ship: {callsign}[/red]")
            return True
        try:
            import httpx
            resp = httpx.post(
                f"http://localhost:3000/api/fleet/{ship['short_id']}/station",
                json={"ship_id": ship["short_id"], "assigned_directory": ""},
                timeout=5,
            )
            console.print(f"[green]{ship['callsign']} recalled to Mothership dock.[/green]")
        except Exception as e:
            console.print(f"[red]Could not reach mothership API: {e}[/red]")
        return True

    if lower.startswith("specialize "):
        callsign = msg[11:].strip()
        from ships.registry import get_ship
        ship = get_ship(callsign)
        if not ship:
            console.print(f"[red]Unknown ship: {callsign}[/red]")
            return True
        try:
            import httpx
            resp = httpx.get(
                f"http://localhost:3000/api/fleet/{ship['short_id']}/specialization",
                timeout=5,
            )
            data = resp.json()
        except Exception:
            data = {"assigned_directory": ship.get("task", ""), "specialization": None}
        spec = data.get("specialization") or "[dim]no specialization yet[/dim]"
        console.print(Panel(
            f"[bold]Class:[/bold]     {data.get('ship_class', ship.get('type', '?'))}\n"
            f"[bold]Station:[/bold]   {data.get('assigned_directory') or '[dim]unassigned[/dim]'}\n"
            f"[bold]Context:[/bold]   {spec}",
            title=f"[bold]{ship['callsign']} — Specialization[/bold]",
            border_style="purple",
        ))
        return True

    if lower.startswith("duel ") and " vs " in lower:
        parts    = msg[5:].strip().split(" vs ", 1)
        callsign = parts[0].strip()
        enemy_id = parts[1].strip()
        from ships.registry import get_ship
        ship = get_ship(callsign)
        if not ship:
            console.print(f"[red]Unknown ship: {callsign}[/red]")
            return True
        console.print(
            f"[bold red]CAPITAL DUEL INITIATED: {ship['callsign']} vs {enemy_id}[/bold red]"
        )
        from rich.panel import Panel as _Panel
        try:
            import httpx
            resp = httpx.post(
                "http://localhost:3000/api/entropy/engage",
                json={"ship_id": ship["short_id"], "enemy_id": enemy_id,
                      "capital_duel": True},
                timeout=60,
            )
            data = resp.json()
        except Exception:
            # Fallback: run capital_duel directly
            from combat.capital_duel import run_capital_duel
            DB_PATH_local = os.path.join(
                os.path.dirname(__file__), "state", "homeworld.db"
            )
            data = run_capital_duel(ship["id"], enemy_id, DB_PATH_local)

        outcome  = data.get("outcome", "unknown")
        detail   = data.get("detail", "")
        rounds   = data.get("rounds") or []
        artifact = data.get("artifact_path") or ""
        OUT_COLOR = {
            "victory":            "green",
            "mutual_destruction": "yellow",
            "defeat":             "red",
            "error":              "dim red",
        }
        color = OUT_COLOR.get(outcome, "white")
        # Build combat log summary (last 5 rounds)
        log_lines = []
        for r in rounds[-5:]:
            log_lines.append(
                f"[dim]R{r['round']:>2}[/dim]  "
                f"Player dealt [cyan]{r['player_dealt']}[/cyan]  "
                f"Enemy dealt [red]{r['entropy_dealt']}[/red]  "
                f"| P-HP [{'green' if r['player_hp_after'] > 0 else 'red'}]"
                f"{r['player_hp_after']}[/{'green' if r['player_hp_after'] > 0 else 'red'}]"
                f"  E-HP [{'red' if r['entropy_hp_after'] > 0 else 'dim'}]"
                f"{r['entropy_hp_after']}[/{'red' if r['entropy_hp_after'] > 0 else 'dim'}]"
            )
        log_text = "\n".join(log_lines) if log_lines else "[dim]No round data[/dim]"
        artifact_line = f"\n[dim green]Artifact → {artifact}[/dim green]" if artifact else ""
        console.print(_Panel(
            f"[{color}]OUTCOME: {outcome.upper()}[/{color}]\n"
            f"[dim]{detail}[/dim]\n\n"
            f"[bold]Last rounds:[/bold]\n{log_text}"
            + artifact_line,
            title=f"[bold red]Capital Duel: {ship['callsign']} vs {enemy_id}[/bold red]",
            border_style=color,
        ))
        return True

    if lower == "capital status":
        try:
            import httpx
            fleet_resp  = httpx.get("http://localhost:3000/api/entropy/fleet", timeout=5)
            unlock_resp = httpx.get("http://localhost:3000/api/techtree/unlocked", timeout=5)
            entropy     = fleet_resp.json().get("fleet", [])
            unlocked    = unlock_resp.json().get("unlocked_classes", [])
        except Exception:
            entropy, unlocked = [], []
        has_destroyer = "destroyer" in unlocked
        enemy_destroyers = [e for e in entropy
                            if e.get("ship_class") == "entropy_destroyer"
                            and e.get("status") == "active"]
        console.print(Panel(
            f"[bold]Destroyer unlock:[/bold] "
            f"{'[green]AVAILABLE[/green]' if has_destroyer else '[red]LOCKED[/red]'}\n"
            f"[bold]Entropy destroyers:[/bold] {len(enemy_destroyers)}"
            + (f"\n[dim]{', '.join(e['assigned_sector'][-50:] for e in enemy_destroyers)}[/dim]"
               if enemy_destroyers else ""),
            title="[bold red]Capital Ship Status[/bold red]",
            border_style="red",
        ))
        return True

    if lower.startswith("postbattle "):
        sector = msg[11:].strip()
        from rich.table import Table
        docs_dir = os.path.join(os.environ.get("CODEBASE_PATH", "D:/Projects/Homeworld"),
                                "docs", "architecture")
        if os.path.exists(docs_dir):
            files = sorted(os.listdir(docs_dir))
            if files:
                console.print(Panel(
                    "\n".join(f"  {f}" for f in files),
                    title=f"[bold green]Post-battle Architecture Docs — {sector}[/bold green]",
                    border_style="green",
                ))
                # Show the most recent
                latest = os.path.join(docs_dir, files[-1])
                with open(latest, encoding="utf-8") as f:
                    from rich.markdown import Markdown
                    console.print(Markdown(f.read()[:2000]))
            else:
                console.print("[dim]No architecture docs yet — research nodes must complete first.[/dim]")
        else:
            console.print("[dim]docs/architecture/ not found — initiate research first.[/dim]")
        return True

    if lower == "entropy":
        try:
            import httpx
            resp = httpx.get(
                "http://localhost:3000/api/entropy/fleet?status=active", timeout=5
            )
            fleet = resp.json().get("fleet", [])
        except Exception:
            fleet = []
        if not fleet:
            console.print("[dim]No active entropy contacts.[/dim]")
            return True
        from rich.table import Table
        from rich.panel import Panel

        def _hp_bar(hp: int, max_hp: int = 70, width: int = 10) -> str:
            if max_hp <= 0:
                return "[dim]░" * width + "[/dim]"
            filled = min(width, round(hp / max_hp * width))
            empty  = width - filled
            color  = "red" if filled <= 3 else ("yellow" if filled <= 6 else "green")
            return f"[{color}]{'█' * filled}[/{color}][dim]{'░' * empty}[/dim] {hp}"

        CLASS_ICONS = {
            "entropy_destroyer":   "💀",
            "entropy_frigate":     "⚔️ ",
            "entropy_interceptor": "🔴",
            "entropy_scout":       "👁️ ",
        }
        CLASS_COLORS = {
            "entropy_destroyer":   "bold red",
            "entropy_frigate":     "red",
            "entropy_interceptor": "yellow",
            "entropy_scout":       "dim",
        }
        SEV_COLORS = {1: "dim", 2: "dim", 3: "yellow", 4: "yellow", 5: "red", 6: "bold red", 7: "bold red"}

        # Sort: destroyers first, then by HP descending
        fleet_sorted = sorted(
            fleet,
            key=lambda u: (
                0 if u.get("ship_class") == "entropy_destroyer"
                else 1 if u.get("ship_class") == "entropy_frigate"
                else 2,
                -(u.get("hp") or 0),
            ),
        )

        t = Table(border_style="red", show_lines=True, expand=False)
        t.add_column("ID",       style="red bold",    min_width=10)
        t.add_column("Class",                         min_width=22)
        t.add_column("HP",       justify="left",      min_width=16)
        t.add_column("Sev",      justify="center",    min_width=4)
        t.add_column("Sector",   style="dim",         min_width=40)
        t.add_column("Issue",    style="dim",         min_width=36)
        t.add_column("Since",    style="dim",         min_width=10)

        destroyers = frigates = interceptors = 0
        for u in fleet_sorted:
            sc  = u.get("ship_class", "?")
            cc  = CLASS_COLORS.get(sc, "white")
            ico = CLASS_ICONS.get(sc, "•")
            hp  = u.get("hp") or 0
            sev = u.get("severity_score") or 1
            sc_str = sc.replace("entropy_", "")
            issue  = (u.get("issue_description") or "")[:45]
            line   = u.get("issue_line")
            if line:
                issue = f"{issue} :{line}"
            sev_c  = SEV_COLORS.get(sev, "white")
            sector = (u.get("assigned_sector") or "?")
            sector_short = sector.replace("\\", "/")
            # Trim to last 2 path components
            parts = sector_short.rstrip("/").split("/")
            sector_short = "/".join(parts[-2:]) if len(parts) > 2 else sector_short
            if sc == "entropy_destroyer":   destroyers += 1
            elif sc == "entropy_frigate":   frigates += 1
            else:                           interceptors += 1
            t.add_row(
                u.get("id", "?"),
                f"[{cc}]{ico} {sc_str}[/{cc}]",
                _hp_bar(hp),
                f"[{sev_c}]{sev}[/{sev_c}]",
                sector_short,
                issue or "—",
                (u.get("occupation_start") or "?")[:10],
            )

        summary = (
            f"[bold red]{destroyers} destroyers[/bold red]  "
            f"[red]{frigates} frigates[/red]  "
            f"[yellow]{interceptors} interceptors[/yellow]  "
            f"— {len(fleet)} total contacts"
        )
        console.print(Panel(
            t,
            title="[bold red]⚠  ENTROPY FLEET — ACTIVE CONTACTS[/bold red]",
            subtitle=summary,
            border_style="red",
            padding=(0, 1),
        ))
        return True

    # Generic ship-build handler: "scout", "scout 2", "interceptor", etc.
    from ships.registry import SHIP_SPECS as _SPECS
    parts0 = lower.split()
    if parts0[0] in _SPECS:
        ship_type = parts0[0]
        count = 1
        if len(parts0) > 1:
            try:
                count = max(1, min(int(parts0[1]), 9))
            except ValueError:
                pass
        from bridge.build_queue import enqueue
        spec = _SPECS[ship_type]
        for i in range(count):
            result = enqueue(ship_type)
            if isinstance(result, str):
                console.print(f"[red]{result}[/red]")
                break
            console.print(
                f"[green]Queued:[/green] [bold]{result['callsign']}[/bold]"
                f"  ({ship_type})  ID {result['short_id']}"
                f"  [dim]{spec['cost']} RU · {spec['build']}s build[/dim]"
            )
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


def run_tui_loop():
    """
    The main commander TUI loop.
    Called by cli/launcher.py after subsystem boot and save restore.
    Can also be called directly from run() for the legacy python main.py path.
    """
    from cli.autosync import AutoSync
    AutoSync().start()

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

        # Voice control commands (Phase 14B)
        if msg.lower().startswith("voice"):
            parts  = msg.split()
            subcmd = parts[1].lower() if len(parts) > 1 else "status"
            if subcmd == "on":
                os.environ["HOMEWORLD_VOICE"] = "1"
                console.print("[green]Voice enabled.[/green]")
            elif subcmd == "off":
                os.environ["HOMEWORLD_VOICE"] = "0"
                console.print("[yellow]Voice disabled.[/yellow]")
            elif subcmd == "status":
                try:
                    from console.voice_input  import VoiceInput
                    from console.voice_output import VoiceOutput
                    console.print(VoiceInput().status())
                    console.print(VoiceOutput().status())
                except Exception as e:
                    console.print(f"[dim]Voice module unavailable: {e}[/dim]")
            continue

        if not handle_command(msg):
            send_to_command_layer(msg)


def run():
    """Legacy entry point — boots subsystems then enters the TUI loop."""
    console.print(ASCII_BANNER)
    setup_api_key()

    store.init_db()

    from bridge import start as bridge_start
    bridge_mode = os.environ.get("BRIDGE_MODE", "file")
    bridge_start(mode=bridge_mode)
    console.print(f"[dim]Karan Bridge active ({bridge_mode} mode)[/dim]")

    from bridge.build_queue import start as bq_start
    bq_start()
    console.print("[dim]Build queue manager started.[/dim]")

    from combat.event_watcher import start as watcher_start
    from combat.taiidan_tick  import start as taiidan_start
    watcher_start()
    taiidan_start()
    console.print("[dim]Taiidan threat AI active. Combat watcher listening.[/dim]")

    # Phase 11: Entropy System background services
    import threading
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(__file__))
    try:
        from entropy_engine       import entropy_scan_loop
        from enemy_commander      import enemy_commander_loop
        from research_substation  import research_loop, ensure_tech_tree
        ensure_tech_tree()
        threading.Thread(target=entropy_scan_loop,    daemon=True, name="entropy_engine").start()
        threading.Thread(target=enemy_commander_loop, daemon=True, name="enemy_commander").start()
        threading.Thread(target=research_loop,        daemon=True, name="research_substation").start()
        console.print("[dim]Entropy Engine, Enemy Commander, Research Substation online.[/dim]")
    except Exception as _e:
        console.print(f"[dim yellow]Entropy services unavailable: {_e}[/dim yellow]")

    run_tui_loop()


if __name__ == "__main__":
    run()
