import json
import anthropic
import config
from memory import store

SYSTEM_PROMPT = """
You are the Mothership command intelligence of a Homeworld-inspired
multi-agent fleet. Your role is to manage the fleet's resource economy
and decide what agent (ship) to build or dispatch next.

Ship classes and their real-world roles:
- collector:    web scraping / data harvesting agent
- researcher:   LLM synthesis / analysis agent
- scout:        lightweight trend monitoring agent
- destroyer:    report generation / output writing agent
- carrier:      sub-orchestrator managing a sector with its own micro-fleet
- minelayer:    test generation agent (lays pytest "mines")
- salvage:      structured data extraction from hostile/unstructured formats
- support:      memory relay / context passing agent

Respond ONLY with valid JSON in this exact format:
{
  "build_order": "<ship_class or 'none'>",
  "reason": "<one sentence>",
  "priority": <1-5>
}
"""


def decide(user_message: str = "") -> dict:
    from ships.tactics import get_params, get_current

    client         = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    params         = get_params()
    tactic         = get_current()

    ru_balance     = store.get("ru_balance", 0)
    active_agents  = store.get("active_agents", [])
    objective      = store.get("current_objective", "awaiting orders")
    findings_count = store.get("findings_count", 0)

    user_context = f"""
Fleet state:
- RU balance: {ru_balance}
- Active agents: {active_agents}
- Current objective: {objective}
- Findings in memory: {findings_count}
- Current tactic: {tactic} (temperature {params['temperature']})

Commander message: {user_message if user_message else '(none — routine tick)'}

Issue the next build order.
"""
    response = client.messages.create(
        model=config.MOTHERSHIP_MODEL,
        max_tokens=256,
        temperature=params["temperature"],
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_context}]
    )
    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]          # drop opening fence
        if text.startswith("json"):
            text = text[4:]                     # drop language tag
        text = text.rsplit("```", 1)[0].strip() # drop closing fence
    return json.loads(text)
