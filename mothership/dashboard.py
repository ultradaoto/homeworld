import os
import time
from rich.live   import Live
from rich.table  import Table
from rich.panel  import Panel
from rich.layout import Layout
from memory import store
from config import OUTPUT_DIR


def build_dashboard() -> Layout:
    ru       = store.get("ru_balance", 0)
    agents   = store.get("active_agents", [])
    obj      = store.get("current_objective", "awaiting orders")
    findings = store.get("findings_count", 0)
    refs     = store.get("harvest_refs", [])

    fleet_table = Table(title="Active Fleet", border_style="purple", show_lines=True)
    fleet_table.add_column("Agent ID",  style="dim")
    fleet_table.add_column("Type",      style="bold purple")
    fleet_table.add_column("Status")
    fleet_table.add_column("Spawned")
    for a in agents:
        fleet_table.add_row(
            a["id"], a["type"],
            a.get("status", "idle"),
            a.get("spawned", "")[:19]
        )
    if not agents:
        fleet_table.add_row("[dim]—[/dim]", "[dim]no active agents[/dim]", "", "")

    out_files = []
    if os.path.exists(OUTPUT_DIR):
        out_files = sorted(os.listdir(OUTPUT_DIR))[-5:]
    outputs_text = "\n".join(f"  {f}" for f in out_files) or "  none yet"

    layout = Layout()
    layout.split_column(
        Layout(Panel(
            f"[bold]RUs:[/bold] {ru}  |  "
            f"[bold]Objective:[/bold] {obj}  |  "
            f"[bold]Findings:[/bold] {findings}  |  "
            f"[bold]Harvests:[/bold] {len(refs)}",
            title="[bold purple]MOTHERSHIP STATUS[/bold purple]",
            border_style="purple",
        ), name="header", size=5),
        Layout(fleet_table, name="fleet", size=10),
        Layout(Panel(outputs_text, title="Recent Outputs"), name="outputs", size=8),
    )
    return layout


def show_dashboard(seconds: int = 15):
    """Display a live-updating dashboard for N seconds."""
    with Live(build_dashboard(), refresh_per_second=1) as live:
        for _ in range(seconds * 4):
            time.sleep(0.25)
            live.update(build_dashboard())
