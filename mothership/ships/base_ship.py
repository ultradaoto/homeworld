"""
Abstract base class for all ship agents.
Phase 2 will add CollectorShip, ResearcherShip, DestroyerShip as subclasses.
"""
from abc import ABC, abstractmethod
import datetime
from memory import store


class BaseShip(ABC):
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.ship_type = self.__class__.__name__
        self.started_at = datetime.datetime.utcnow().isoformat()

    @abstractmethod
    def run(self, payload: dict) -> dict:
        """Execute this ship's mission. Returns a result dict."""
        ...

    def _set_status(self, status: str):
        active = store.get("active_agents", [])
        for a in active:
            if a["id"] == self.agent_id:
                a["status"] = status
                break
        store.set("active_agents", active)

    def _log(self, action: str, detail: str = ""):
        store.log_action(self.ship_type, action, detail)
