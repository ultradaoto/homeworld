import subprocess
import sys
from memory import store

BENTUSI_CATALOG = {
    "numpy":      {"cost": 200, "capability": "numerical_computing"},
    "pandas":     {"cost": 300, "capability": "data_analysis"},
    "playwright": {"cost": 500, "capability": "js_scraping"},
    "firecrawl":  {"cost": 400, "capability": "deep_crawl"},
    "faiss-cpu":  {"cost": 600, "capability": "vector_search"},
    "docker":     {"cost": 800, "capability": "containerization"},
}


def visit_bentusi(package: str) -> dict:
    """Trade RUs to acquire a package capability."""
    item = BENTUSI_CATALOG.get(package.lower())
    if not item:
        return {"error": f"Bentusi do not carry '{package}'"}

    ru = store.get("ru_balance", 0)
    if ru < item["cost"]:
        return {"error": f"Insufficient RUs. Need {item['cost']}, have {ru}."}

    store.set("ru_balance", ru - item["cost"])

    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", package, "--quiet"],
        capture_output=True, text=True
    )

    if proc.returncode != 0:
        store.set("ru_balance", ru)  # refund on failure
        return {"error": proc.stderr[:300]}

    unlocked = store.get("unlocked_capabilities", [])
    unlocked.append(item["capability"])
    store.set("unlocked_capabilities", list(set(unlocked)))
    store.log_action("bentusi", "trade", f"{package} → {item['capability']}")

    return {
        "status":     "traded",
        "package":    package,
        "capability": item["capability"],
        "ru_spent":   item["cost"],
        "ru_balance": store.get("ru_balance", 0),
    }


def list_catalog() -> dict:
    return BENTUSI_CATALOG
