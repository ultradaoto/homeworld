import anthropic
import datetime
import ssl
import certifi
import httpx

from ships.base_ship import BaseShip
import config

SALVAGE_SYSTEM = """
You are a Salvage Corvette agent. Your mission: capture unstructured,
hostile data formats and convert them to clean structured JSON.
Input may be: legacy code, messy CSV, PDF text, raw HTML tables,
SQL dumps, or undocumented API responses.

Output ONLY valid JSON representing the structured data.
No explanation. No markdown fences. Pure JSON.
"""


class SalvageShip(BaseShip):
    ship_type = "salvage"

    async def run(self, task: dict) -> dict:
        client   = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        raw_data = task.get("data", "")
        url      = task.get("url", "")
        label    = task.get("label", "salvage")

        if url and not raw_data:
            try:
                ssl_ctx = ssl.create_default_context(cafile=certifi.where())
                async with httpx.AsyncClient(
                    timeout=15,
                    verify=ssl_ctx,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; HomeworldSalvage/1.0)"}
                ) as c:
                    r        = await c.get(url)
                    raw_data = r.text[:6000]
            except Exception as e:
                self.mark_complete()
                return {"error": str(e)}

        if not raw_data:
            self.mark_complete()
            return {"error": "no data provided"}

        response = client.messages.create(
            model=config.MOTHERSHIP_MODEL,
            max_tokens=2000,
            system=SALVAGE_SYSTEM,
            messages=[{"role": "user",
                       "content": f"Capture and structure this data:\n\n{raw_data[:5000]}"}]
        )

        structured = response.content[0].text
        ts         = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename   = f"salvage_{label}_{ts}.json"
        path       = self.write_output(filename, structured)
        self.deposit_ru(150)
        self.mark_complete()
        return {"status": "ok", "file": path}
