import asyncio
import datetime
import glob
import json
import os
import uuid

import anthropic

from ships.collector  import CollectorShip
from ships.researcher import ResearcherShip
from ships.destroyer  import DestroyerShip
from memory import store
import config

JUDGE_SYSTEM = """
You are Fleet Command adjudicating a skirmish between two fleets.
Both were given the same objective. Evaluate their outputs on:
1. Completeness  (0-10)
2. Accuracy      (0-10)
3. Efficiency    (lower token count for same quality is better)
4. Actionability (0-10)

Respond in JSON only:
{
  "winner":     "blue|red",
  "blue_score": <0-30>,
  "red_score":  <0-30>,
  "verdict":    "one paragraph"
}
"""


async def _run_fleet(colour: str, objective: str, urls: list,
                     system_bias: str) -> dict:
    outputs = []

    for url in urls[:3]:
        aid  = f"{colour}_collector_{uuid.uuid4().hex[:4]}"
        ship = CollectorShip(aid)
        r    = await ship.run({"url": url})
        outputs.append(r)

    aid  = f"{colour}_researcher_{uuid.uuid4().hex[:4]}"
    ship = ResearcherShip(aid)
    r    = await ship.run({"objective": f"{objective} [{system_bias}]"})
    outputs.append(r)

    aid  = f"{colour}_destroyer_{uuid.uuid4().hex[:4]}"
    ship = DestroyerShip(aid)
    r    = await ship.run({"objective": objective})
    outputs.append(r)

    briefings = sorted(glob.glob(
        os.path.join(config.OUTPUT_DIR, "briefing_*.md")
    ))
    return {
        "colour":   colour,
        "briefing": briefings[-1] if briefings else None,
        "outputs":  outputs,
    }


async def run_skirmish(objective: str, urls: list) -> dict:
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    store.set("current_objective", objective)

    blue, red = await asyncio.gather(
        _run_fleet("blue", objective, urls, "conservative, thorough analysis"),
        _run_fleet("red",  objective, urls, "aggressive, creative analysis"),
    )

    def _read(path):
        if not path:
            return "[no output]"
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()[:3000]
        except Exception:
            return "[read error]"

    text = (
        f"OBJECTIVE: {objective}\n\n"
        f"=== BLUE FLEET ===\n{_read(blue['briefing'])}\n\n"
        f"=== RED FLEET ===\n{_read(red['briefing'])}"
    )

    verdict_resp = client.messages.create(
        model=config.MOTHERSHIP_MODEL,
        max_tokens=512,
        system=JUDGE_SYSTEM,
        messages=[{"role": "user", "content": text}]
    )

    vtext = verdict_resp.content[0].text.strip()
    if vtext.startswith("```"):
        vtext = vtext.split("```", 2)[1]
        if vtext.startswith("json"):
            vtext = vtext[4:]
        vtext = vtext.rsplit("```", 1)[0].strip()

    try:
        result = json.loads(vtext)
    except Exception:
        result = {"winner": "blue", "verdict": vtext}

    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    arena_path = os.path.join(config.OUTPUT_DIR, f"arena_{ts}.json")
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    with open(arena_path, "w", encoding="utf-8") as f:
        json.dump({"objective": objective, "ts": ts, **result}, f, indent=2)

    return result
