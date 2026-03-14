import anthropic
import asyncio
import datetime
import json
import uuid

from ships.base_ship  import BaseShip
from ships.collector  import CollectorShip
from ships.researcher import ResearcherShip
from memory import store
import config

CARRIER_SYSTEM = """
You are a Carrier-class sub-orchestrator in a Homeworld-inspired fleet.
You manage a localized sector with your own agent budget.
Your Mothership has assigned you a specific domain objective.
Decide which micro-agents to spawn to complete it.

Respond ONLY with valid JSON:
{
  "spawn": [
    {"type": "collector", "target": "<url>"},
    {"type": "researcher", "notes": "<focus>"}
  ],
  "status": "<one sentence on plan>"
}
"""


class CarrierShip(BaseShip):
    ship_type = "carrier"

    async def run(self, task: dict) -> dict:
        client    = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        sector    = task.get("sector", "general")
        objective = task.get("objective", store.get("current_objective", ""))
        ru_budget = task.get("ru_budget", 1000)

        prompt = (f"SECTOR: {sector}\n"
                  f"OBJECTIVE: {objective}\n"
                  f"RU BUDGET: {ru_budget}\n"
                  f"Plan and spawn a micro-fleet for this sector.")

        response = client.messages.create(
            model=config.MOTHERSHIP_MODEL,
            max_tokens=512,
            system=CARRIER_SYSTEM,
            messages=[{"role": "user", "content": prompt}]
        )

        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.rsplit("```", 1)[0].strip()

        try:
            plan = json.loads(text)
        except Exception:
            self.mark_complete()
            return {"error": "carrier plan parse failed", "raw": text[:200]}

        spawned = []
        for order in plan.get("spawn", []):
            ship_type = order.get("type")
            aid       = f"{ship_type}_{uuid.uuid4().hex[:6]}"

            if ship_type == "collector":
                ship   = CollectorShip(aid)
                result = await ship.run({"url": order.get("target", "")})
            elif ship_type == "researcher":
                ship   = ResearcherShip(aid)
                result = await ship.run({"objective": objective})
            else:
                result = {"skipped": ship_type}

            spawned.append({"id": aid, "type": ship_type, "result": result})

            ru_budget -= 200
            if ru_budget <= 0:
                break

        ts   = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        path = self.write_output(f"carrier_{sector}_{ts}.json", {
            "sector":         sector,
            "objective":      objective,
            "spawned":        spawned,
            "carrier_status": plan.get("status", ""),
        })

        self.mark_complete()
        return {"status": "ok", "file": path, "agents_spawned": len(spawned)}
