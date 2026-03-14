from ships.base_ship import BaseShip


class ScoutShip(BaseShip):
    ship_type = "scout"

    async def run(self, task: dict) -> dict:
        # Phase 4: poll RSS feeds, sitemaps, GitHub trending
        self.mark_complete()
        return {"status": "stub", "note": "Scout fully implemented in Phase 4"}
