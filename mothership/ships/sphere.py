import asyncio
import uuid
import json

from ships.researcher import ResearcherShip


async def sphere_verify(n: int = 3, task: dict = {}) -> dict:
    """
    Sphere formation: launch N researchers simultaneously on the same task.
    Mothership evaluates all results and returns the consensus finding.
    """
    import anthropic
    import config

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    coros = []
    for i in range(n):
        aid  = f"researcher_sphere_{uuid.uuid4().hex[:6]}"
        ship = ResearcherShip(aid)
        coros.append(ship.run({**task, "sphere_index": i}))

    results = await asyncio.gather(*coros, return_exceptions=True)
    valid   = [r for r in results if isinstance(r, dict) and r.get("status") == "ok"]

    if not valid:
        return {"error": "all sphere agents failed"}

    summaries = "\n\n---\n\n".join(
        f"Agent {i}: {r}" for i, r in enumerate(valid)
    )
    verdict = client.messages.create(
        model=config.MOTHERSHIP_MODEL,
        max_tokens=512,
        system=(
            "You are adjudicating N research agent outputs. "
            "Select the most accurate and complete result. "
            "Reply with JSON: {\"winner\": <index>, \"reason\": \"...\"}"
        ),
        messages=[{"role": "user", "content": summaries}]
    )

    text = verdict.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0].strip()

    try:
        v = json.loads(text)
        winner_idx = int(v.get("winner", 0))
        winner_idx = min(winner_idx, len(valid) - 1)
        return {"winner": valid[winner_idx], "reason": v.get("reason", "")}
    except Exception:
        return {"winner": valid[0], "reason": "parse fallback to first"}
