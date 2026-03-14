import os, datetime, json
from abc import ABC, abstractmethod
from config import OUTPUT_DIR
from memory import store


class BaseShip(ABC):
    ship_type: str = "base"

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.started  = datetime.datetime.now(datetime.timezone.utc).isoformat()

    @abstractmethod
    async def run(self, task: dict) -> dict:
        """Execute the ship's mission. Return a result dict."""
        ...

    def write_output(self, filename: str, content) -> str:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        path = os.path.join(OUTPUT_DIR, filename)
        if isinstance(content, dict):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(content, f, indent=2, ensure_ascii=False)
        else:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
        store.log_action(self.ship_type, "write_output", path)
        return path

    def deposit_ru(self, amount: int):
        current = store.get("ru_balance", 0)
        store.set("ru_balance", current + amount)
        store.log_action(self.ship_type, "ru_deposit", str(amount))

    def mark_complete(self):
        agents = store.get("active_agents", [])
        store.set("active_agents",
            [a for a in agents if a["id"] != self.agent_id])
        store.log_action(self.ship_type, "completed", self.agent_id)
