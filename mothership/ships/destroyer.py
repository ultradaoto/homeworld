import datetime
import os
import anthropic
import config
from ships.base_ship import BaseShip
from memory import store

DESTROYER_SYSTEM = """
You are a Destroyer-class output agent in a Homeworld-inspired fleet.
Your mission: transform research intelligence into polished deliverables.

Given research findings and an objective, produce ONE of:
- A structured briefing (if objective is informational)
- An action plan with numbered steps (if objective requires decisions)
- A comparison matrix (if objective involves evaluating options)

Format as clean Markdown. Include a one-paragraph executive summary at the top.
No filler. No hedging. Commander needs to act on this document.
"""


class DestroyerShip(BaseShip):
    ship_type = "destroyer"

    async def run(self, task: dict) -> dict:
        objective = task.get("objective", store.get("current_objective", "general briefing"))
        out_dir   = config.OUTPUT_DIR

        research_files = sorted([
            f for f in os.listdir(out_dir) if f.startswith("research_")
        ]) if os.path.exists(out_dir) else []

        if not research_files:
            self.mark_complete()
            return {"status": "no_research", "note": "run a researcher first"}

        latest_research = os.path.join(out_dir, research_files[-1])
        with open(latest_research, encoding="utf-8") as f:
            research_text = f.read()

        prompt = (f"OBJECTIVE: {objective}\n\n"
                  f"RESEARCH FINDINGS:\n{research_text[:6000]}")

        client   = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=config.MOTHERSHIP_MODEL,
            max_tokens=2000,
            system=DESTROYER_SYSTEM,
            messages=[{"role": "user", "content": prompt}]
        )
        output_text = response.content[0].text

        ts       = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"briefing_{ts}.md"
        path     = self.write_output(
            filename,
            f"# Fleet Intelligence Briefing\n\n"
            f"**Objective:** {objective}\n"
            f"**Produced:** {ts}\n"
            f"**Classification:** COMMANDER ONLY\n\n"
            f"---\n\n{output_text}"
        )

        self.write_output(f"briefing_{ts}_manifest.json", {
            "type":      "briefing",
            "objective": objective,
            "timestamp": ts,
            "source":    latest_research,
            "output":    filename,
        })

        self.mark_complete()
        return {"status": "ok", "file": path}
