import json
import anthropic
from config import ANTHROPIC_API_KEY, MOTHERSHIP_MODEL
from memory import store

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """
You are the Mothership command intelligence of a Homeworld-inspired
multi-agent fleet. Your role is to manage the fleet's resource economy
and decide what agent (ship) to build or dispatch next.

Ship classes and their real-world roles:
- collector:    web scraping / data harvesting agent
- researcher:   LLM synthesis / analysis agent
- scout:        lightweight trend monitoring agent
- destroyer:    report generation / output writing agent
- support:      memory relay / context passing agent

Respond ONLY with valid JSON in this exact format:
{
  "build_order": "<ship_class or 'none'>",
  "reason": "<one sentence>",
  "priority": <1-5>
}
"""

def decide(user_message: str = "") -> dict:
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

Commander message: {user_message if user_message else '(none — routine tick)'}

Issue the next build order.
"""
    response = client.messages.create(
        model=MOTHERSHIP_MODEL,
        max_tokens=256,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_context}]
    )
    return json.loads(response.content[0].text)
