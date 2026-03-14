import os
from dotenv import load_dotenv

_HERE = os.path.dirname(os.path.abspath(__file__))

load_dotenv(os.path.join(_HERE, ".env"))

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MOTHERSHIP_MODEL  = os.getenv("MOTHERSHIP_MODEL", "claude-sonnet-4-6")
OUTPUT_DIR        = os.getenv("OUTPUT_DIR", os.path.join(_HERE, "outputs"))
STATE_DIR         = os.getenv("STATE_DIR",  os.path.join(_HERE, "state"))

RU_THRESHOLD      = int(os.getenv("RU_HARVEST_THRESHOLD", 500))
MAX_COLLECTORS    = int(os.getenv("MAX_COLLECTORS", 4))
MAX_RESEARCHERS   = int(os.getenv("MAX_RESEARCHERS", 2))
MAX_DESTROYERS    = int(os.getenv("MAX_DESTROYERS", 2))

def validate():
    if not ANTHROPIC_API_KEY or not ANTHROPIC_API_KEY.startswith("sk-"):
        raise ValueError(
            "ANTHROPIC_API_KEY missing or invalid.\n"
            "Copy .env.example → .env and set your key."
        )
