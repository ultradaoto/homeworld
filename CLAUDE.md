# Homeworld Codebase Engine — Session Memory

## What this project is
A distributed AI agent harness disguised as the 1999 RTS Homeworld.
Docker containers are ships. SQLite is the mothership brain. FastAPI translates
game clicks into LLM task execution. The C-engine renders fleet state as 3D space.

## Architecture
- mothership/   — Python TUI (main.py) + FastAPI server (api/server.py) + SQLite
- ship/         — Agent loop (agent.py). Polls mothership, calls Claude, handles SIGTERM.
- c_hook/       — SQLite C library reading fleet state into the game engine.
  HomeworldSDL/ — Homeworld SDL2 port. Bridge/ contains FleetBridge.c + telemetry hook.
- docker-compose.yml — Fleet definition. Scale with: docker compose up --scale scout=N

## Two database layers
- fleet.db      (mothership/state/) — Phase 10A/B: TUI-facing store, active_ships + sectors
- homeworld.db  (mothership/state/) — Phase 10C: Phase 10C canonical DB, 6 tables (see below)
  Both are SQLite WAL-mode. The C-engine reads homeworld.db via hw_telemetry_tick().

## Phase 10C database tables (homeworld.db)
  active_ships, asteroids_map, tasks, escape_pods, file_locks, intelligence
  Schema in: mothership/init_db.py — run `python mothership/init_db.py` to (re)init.

## Running the fleet (Docker)
  export CODEBASE_PATH=/path/to/target/project
  cp .env.example .env && nano .env   # add ANTHROPIC_API_KEY
  docker compose up --build           # mothership + 2 scouts
  ./scripts/fleet_status.sh           # check fleet state

## Running the TUI (local dev)
  cd mothership && python main.py     # full TUI with all commands (legacy)
  python start_api.py                 # or: start api from TUI

## homeworld CLI (Phase 14A)
  # Linux/macOS: sudo bash mothership/install.sh
  # Windows:     scripts/homeworld.bat (or add to PATH)
  cd <any-project> && homeworld       # launch or resume campaign
  homeworld status                    # print .homeworld info without booting
  homeworld save                      # manual save to .homeworld
  homeworld reset                     # wipe campaign (confirm prompt)
  homeworld pause / resume            # freeze/unfreeze enemy AI
  # Entry point: mothership/cli/launcher.py
  # Save file:   <project-dir>/.homeworld  (JSON, per-project)

## Queueing work via API
  ./scripts/queue_task.sh "lint" "Check all JS files for errors" 8
  ./scripts/queue_task.sh "research" "Summarize the architecture of /codebase" 5

## Testing escape pods
  ./scripts/test_escape_pod.sh        # full end-to-end demonstration

## Ship models by class
  Scouts:           claude-haiku-4-5-20251001   (cheap, fast)
  Assault frigates: claude-sonnet-4-6            (workhorse)
  Salvage corvette: claude-haiku-4-5-20251001   (recovery, checks pods on boot)
  Mothership:       claude-opus-4-6              (orchestration — rarely called directly)

## Key API routes (Phase 10C server at :3000)
  POST /api/tasks                     — queue a task
  GET  /api/fleet/status              — all ship states
  GET  /api/fleet/{id}/orders         — ship polls for next task (atomic claim)
  POST /api/locks/acquire             — lock a file (409 if taken)
  POST /api/escape_pods/eject         — SIGTERM state dump
  GET  /api/escape_pods/unclaimed     — list recoverable pods
  POST /api/escape_pods/{id}/recover  — recover a pod
  GET  /health                        — liveness check
  GET  /docs                          — Swagger UI

## TUI commands (Phase 10B Docker fleet)
  containers                — list running hw-* containers
  order <callsign> <json>   — POST task to ship's container
  pods                      — list unrecovered escape pods from homeworld.db
  recover <salvage> <pod>   — dispatch salvage ship to recover a pod
  start api                 — launch FastAPI server in background

## C-engine hook (homeworld_telemetry)
  Files:  HomeworldSDL/src/Bridge/homeworld_telemetry.{c,h}
          c_hook/homeworld_telemetry.{c,h}  (canonical source, synced)
  Called from: UnivUpdate.c (init + tick alongside bridgeInit/bridgeTick)
               main.c WindowsCleanup() (shutdown)
  Global struct: hw_fleet_state  (active_ship_count, queued_task_count, etc.)
  DB path:       HW_DB_PATH env var → mothership/state/homeworld.db (Windows default)
  Link: sqlite3 added to meson.build base_deps

## Build
  MSYSTEM=MINGW64 C:/msys64/usr/bin/bash.exe --login -c \
    "meson compile -C /d/Projects/Homeworld/HomeworldSDL/build"

## Phase checklist
  [x] Phase 10A — SQLite visual telemetry (sector dust clouds, ship overlays)
  [x] Phase 10B — Dockerized fleet (launcher, SIGTERM escape pods, TUI commands)
  [x] Phase 10C — Production server + canonical DB + ship agent + C telemetry hook
  [x] Phase 14A — homeworld CLI, .homeworld save/restore, Commander Absence Detection
  [x] Phase 14B — In-game console chat overlay (C/SDL), Mothership AI, voice I/O foundation

## Next steps
  - [ ] smMothershipIntelDraw: add hw_fleet_state data to the Sensors Manager HUD panel
  - [ ] Threat level auto-increment on lint failures (POST /api/map/scan result)
  - [ ] Research vessel class in docker-compose.yml with claude-sonnet-4-6
  - [ ] Hyperspace trigger: git push when all tasks done + tests pass
  - [ ] C-engine: render ship positions from active_ships.sector_id
  - [ ] tests/test_escape_pod.py: pytest SIGTERM integration test
