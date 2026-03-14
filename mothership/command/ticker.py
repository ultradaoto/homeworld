import asyncio
import datetime
from rich.console import Console
from command import planner
from router  import dispatcher
from memory  import store
from config  import RU_THRESHOLD

console = Console()
TICK_INTERVAL = 30   # seconds between autonomous ticks


async def ticker():
    """Background loop — runs the fleet autonomously between commander inputs."""
    while True:
        await asyncio.sleep(TICK_INTERVAL)
        ru = store.get("ru_balance", 0)
        if ru < RU_THRESHOLD:
            continue   # not enough resources to act

        try:
            order  = planner.decide(user_message="")
            result = dispatcher.dispatch(order)
            if result not in ("no_action",) and not result.startswith("at_capacity"):
                ts = datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M:%S")
                console.print(
                    f"\n[dim][{ts}] AUTO-TICK → {order['build_order']}: "
                    f"{order['reason']} ({result})[/dim]"
                )
        except Exception as e:
            console.print(f"[dim][ticker error: {e}][/dim]")
