import datetime
import ssl
import certifi
import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify
from ships.base_ship import BaseShip
from memory import store

_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

RU_PER_PAGE = 100   # RUs awarded per successfully scraped page


class CollectorShip(BaseShip):
    ship_type = "collector"

    async def run(self, task: dict) -> dict:
        url = task.get("url")
        if not url:
            self.mark_complete()
            return {"error": "no url provided"}

        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True,
                                          verify=_SSL_CONTEXT) as client:
                response = await client.get(url, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; HomeworldCollector/2.0)"
                })
            response.raise_for_status()
        except Exception as e:
            self.mark_complete()
            return {"error": str(e), "url": url}

        soup = BeautifulSoup(response.text, "html.parser")

        for tag in soup(["script", "style", "nav", "footer", "aside"]):
            tag.decompose()

        title   = soup.title.string.strip() if soup.title else url
        content = markdownify(str(soup.body or soup), heading_style="ATX")
        content = content[:8000]

        ts        = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_name = url.split("//")[-1].replace("/", "_")[:40]
        filename  = f"harvest_{ts}_{safe_name}.md"

        path = self.write_output(filename, f"# {title}\n\nSource: {url}\n\n{content}")
        self.deposit_ru(RU_PER_PAGE)

        findings = store.get("findings_count", 0)
        store.set("findings_count", findings + 1)

        refs = store.get("harvest_refs", [])
        refs.append({"title": title, "url": url, "file": filename, "ts": ts})
        store.set("harvest_refs", refs[-50:])

        self.mark_complete()
        return {"status": "ok", "file": path, "ru_earned": RU_PER_PAGE}
