import datetime
import os
import anthropic
import config
from ships.base_ship import BaseShip
from memory import store

RESEARCH_SYSTEM = """
You are a Research Ship intelligence in a Homeworld-inspired agent fleet.
Your mission: synthesize harvested information into actionable intelligence.

Given a set of harvested documents, produce:
1. KEY FINDINGS   — 3-5 bullet points, most important insights
2. PATTERNS       — what themes recur across sources
3. GAPS           — what information is missing or contradictory
4. BUILD ORDERS   — recommend which ship class to dispatch next and why

Write in clear, direct prose. No filler. Every sentence earns its place.
"""


class ResearcherShip(BaseShip):
    ship_type = "researcher"

    async def run(self, task: dict) -> dict:
        objective = task.get("objective", store.get("current_objective", "general analysis"))
        refs      = store.get("harvest_refs", [])

        if not refs:
            self.mark_complete()
            return {"status": "no_harvests", "note": "no collector data to analyse yet"}

        docs = []
        for ref in refs[-6:]:
            fpath = os.path.join(config.OUTPUT_DIR, ref["file"])
            try:
                with open(fpath, encoding="utf-8") as f:
                    docs.append(f"### {ref['title']}\n{f.read()[:2000]}")
            except Exception:
                pass

        if not docs:
            self.mark_complete()
            return {"status": "no_harvests", "note": "harvest files not readable"}

        combined = "\n\n---\n\n".join(docs)
        prompt   = (f"OBJECTIVE: {objective}\n\n"
                    f"HARVESTED DOCUMENTS:\n{combined}")

        client   = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=config.MOTHERSHIP_MODEL,
            max_tokens=1500,
            system=RESEARCH_SYSTEM,
            messages=[{"role": "user", "content": prompt}]
        )
        findings_text = response.content[0].text

        ts       = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"research_{ts}.md"
        path     = self.write_output(
            filename,
            f"# Research Report\n\n"
            f"**Objective:** {objective}\n"
            f"**Timestamp:** {ts}\n"
            f"**Sources:** {len(docs)}\n\n"
            f"{findings_text}"
        )

        count = store.get("findings_count", 0)
        store.set("findings_count", count + 1)
        self.mark_complete()
        return {"status": "ok", "file": path}
