import anthropic
import datetime
import json
import subprocess
import sys

from ships.base_ship import BaseShip
from combat.threat_engine import resolve_enemy
import config

INTERCEPTOR_SYSTEM = """
You are an Interceptor-class debugging agent.
You receive a Python error traceback and surrounding code.
Produce a minimal, surgical fix. Output format:
{
  "analysis": "one sentence diagnosis",
  "fix":      "the corrected code block only",
  "test":     "one pytest assertion that proves the fix"
}
JSON only. No markdown fences.
"""


class InterceptorAgent(BaseShip):
    ship_type = "interceptor"

    async def run(self, task: dict) -> dict:
        client      = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        error_text  = task.get("error", "")
        code_ctx    = task.get("code", "")
        target_file = task.get("file", "")

        prompt = (f"ERROR:\n{error_text}\n\n"
                  f"CONTEXT CODE:\n{code_ctx[:3000]}")

        response = client.messages.create(
            model=config.MOTHERSHIP_MODEL,
            max_tokens=1024,
            system=INTERCEPTOR_SYSTEM,
            messages=[{"role": "user", "content": prompt}]
        )

        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.rsplit("```", 1)[0].strip()

        try:
            result = json.loads(text)
        except Exception:
            self.mark_complete()
            return {"error": "parse failed", "raw": text[:200]}

        ts   = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        path = self.write_output(f"fix_{ts}.json", result)

        # Attempt to verify fix with generated test
        if result.get("test"):
            try:
                proc = subprocess.run(
                    [sys.executable, "-c", result["test"]],
                    capture_output=True, text=True, timeout=10
                )
                if proc.returncode == 0:
                    resolve_enemy("RuntimeError")
                    result["test_passed"] = True
                else:
                    result["test_passed"] = False
                    result["test_output"] = proc.stderr[:200]
            except Exception as e:
                result["test_error"] = str(e)

        self.mark_complete()
        return {
            "status":   "ok",
            "file":     path,
            "analysis": result.get("analysis", ""),
        }
