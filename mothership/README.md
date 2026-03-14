# Homeworld Mothership — Agent Orchestration System

Three-layer AI fleet management system inspired by Homeworld (1999).

## Architecture

| Layer | Role |
|---|---|
| **Command** | Planning LLM decides build orders from fleet state |
| **Router** | Deterministic dispatch — no LLM, enforces concurrency caps |
| **Memory** | SQLite (structured state) + ChromaDB (vector recall) |

## Setup

```bash
cd D:\Projects\Homeworld\mothership
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env   # then add your ANTHROPIC_API_KEY
python main.py
```

## Commands

| Command | Effect |
|---|---|
| `ru <N>` | Deposit N resource units (simulate harvest) |
| `obj <text>` | Set the current fleet objective |
| `status` | Refresh fleet status panel |
| `exit` | Shut down gracefully |
| *anything else* | Routed to Command Layer (planning LLM) |

## Output

Every commander order produces a timestamped JSON file in `outputs/`.

Phase 2 adds real ship agents:
- **CollectorShip** — web scraper, deposits RUs, writes `.md` harvest files
- **ResearcherShip** — reads harvests, synthesises with Claude, writes reports
- **DestroyerShip** — turns research into polished commander briefings
