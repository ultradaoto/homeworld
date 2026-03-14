import anthropic
import datetime

from ships.base_ship import BaseShip
import config

MINELAYER_SYSTEM = """
You are a Minelayer agent. Given a code file or function description,
generate pytest unit tests that act as "mines" — tripwires that will
detonate (fail) if future code breaks the expected contract.

Output ONLY valid Python pytest code. No explanation. No markdown fences.
"""


class MinelayerShip(BaseShip):
    ship_type = "minelayer"

    async def run(self, task: dict) -> dict:
        client      = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        target_code = task.get("code", "")
        module_name = task.get("module", "module")

        if not target_code:
            self.mark_complete()
            return {"error": "no code provided"}

        response = client.messages.create(
            model=config.MOTHERSHIP_MODEL,
            max_tokens=1500,
            system=MINELAYER_SYSTEM,
            messages=[{"role": "user",
                       "content": f"Generate tests for:\n\n{target_code}"}]
        )

        test_code  = response.content[0].text
        ts         = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename   = f"test_mine_{module_name}_{ts}.py"
        path       = self.write_output(filename, test_code)
        self.mark_complete()
        return {"status": "ok", "file": path,
                "mine_count": test_code.count("def test_")}
