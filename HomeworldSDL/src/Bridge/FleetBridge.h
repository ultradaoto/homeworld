/* FleetBridge.h
 * Bidirectional JSON bridge between the Python Mothership TUI and the
 * Homeworld C engine. No external dependencies — pure stdio + string.
 *
 * Python → C : fleet_state.json    (agent fleet, RU balance, objective)
 * C → Python : engine_state.json   (game RU balance, active flag, timestamp)
 * Python → C : engine_cmd.json     (build orders, enemy events)
 * C → Python : engine_built.json   (build completion notification)
 * Python → C : sector_map.json     (codebase spatial index for dust clouds)
 * Python → C : ship_overlays.json  (Phase 10A: per-ship display info)
 */
#ifndef FLEET_BRIDGE_H
#define FLEET_BRIDGE_H

#define BRIDGE_MAX_SHIPS     64
#define BRIDGE_MAX_OBJ       256
#define MAX_BUILD_QUEUE      8
#define MAX_SECTOR_VISUALS   128
#define MAX_SHIP_OVERLAYS    64

/* Paths relative to the game working directory (HomeworldSDL/build/).
   Resolved to absolute paths in bridgeInit() on Windows. */
#define BRIDGE_STATE_FILE   "../../mothership/state/fleet_state.json"
#define BRIDGE_ENGINE_FILE  "../../mothership/state/engine_state.json"
#define BRIDGE_CMD_FILE     "../../mothership/state/engine_cmd.json"
#define BRIDGE_BUILT_FILE   "../../mothership/state/engine_built.json"
#define BRIDGE_SECTOR_FILE  "../../mothership/state/sector_map.json"
#define BRIDGE_COMBAT_FILE  "../../mothership/state/combat_event.json"
#define BRIDGE_OVERLAY_FILE "../../mothership/state/ship_overlays.json"
#define BRIDGE_ENTRY_FILE   "../../mothership/state/sector_entry.json"

typedef struct {
    char id[32];
    char type[24];
    char status[16];
} BridgeShip;

/* Phase 7: serial build queue entry (one ship at a time, real timer) */
typedef struct {
    char short_id [16];
    char callsign [32];
    char ship_type[24];
    int  build_secs_remaining;
} BuildQueueEntry;

/* Phase 10A: sector visual — drawn as threat-level dust cloud ring.
   r/g/b/a  = cloud color (0-255); density_pct = 0-100 (0.0-1.0 scaled).
   locked   = 1 if a ship has a file lock on this sector (shows force field).
   is_pod   = 1 if this entry represents an escape pod beacon (golden pulsing). */
typedef struct {
    char  id         [9];
    char  label      [32];
    int   x, y, z;
    int   threat;        /* 0–10 */
    int   scouted;
    int   beacons;
    int   r, g, b, a;   /* cloud color from SQLite */
    int   density_pct;  /* 0-100 maps to cloud opacity/ring count */
    int   cloud_radius;  /* sector entry detection radius (world units) */
    int   locked;        /* file lock active — draw force-field ring */
    int   is_pod;        /* escape pod beacon — draw golden ring */
} SectorVisual;

/* Phase 10A: agent-type → ship mesh mapping */
typedef struct {
    const char *agent_type;
    int         race_id;       /* 0=Kushan, 1=Taiidan */
    int         ship_class_idx;
    const char *display_name;
    const char *model_hint;
} AgentShipMapping;

extern const AgentShipMapping AGENT_SHIP_MAP[];

/* Phase 10A/10B: per-ship overlay — callsign, task, Docker container ID */
typedef struct {
    char short_id    [16];
    char callsign    [32];
    char ship_type   [24];
    char status      [16];
    char sector      [48];
    char task_desc   [64];
    char container_id[24];   /* Phase 10B: Docker container ID for SIGTERM */
    int  ctx_used;
    int  ctx_budget;
    int  armor;
    int  max_armor;
    int  kills;
} ShipOverlay;

typedef struct {
    int        ru_balance;
    int        ship_count;
    BridgeShip ships[BRIDGE_MAX_SHIPS];
    char       objective[BRIDGE_MAX_OBJ];
    int        findings;
    int        enemy_count;         /* Phase 6: active error threats   */

    /* Phase 7: pending build completion — set by bridgeBuildTick()    */
    int        pending_build_ready;
    char       pending_build_type    [24];
    char       pending_build_callsign[32];
    char       pending_build_id      [16];

    int        escape_pod_count;    /* Phase 10A: unrecovered pods      */
    int        valid;               /* 1 if last state read succeeded  */
} FleetState;

/* Phase 7: build queue globals — defined in FleetBridge.c */
extern BuildQueueEntry gBuildQueue[MAX_BUILD_QUEUE];
extern int             gBuildQueueLen;

/* Phase 10A: sector visual + ship overlay arrays — defined in FleetBridge.c */
extern SectorVisual gSectorVisuals[MAX_SECTOR_VISUALS];
extern int          gSectorVisualCount;
extern ShipOverlay  gShipOverlays [MAX_SHIP_OVERLAYS];
extern int          gShipOverlayCount;

void        bridgeInit(void);
void        bridgeTick(int gameRU, int gameShips, int gameScouts,
                       unsigned int *scoutIDs);
FleetState *bridgeGetState(void);
void        bridgeProcessCommand(void);        /* consume engine_cmd.json  */
void        bridgeProcessBuildCommand(const char *buf); /* parse build_ship */
void        bridgeBuildTick(void);             /* tick build timer, call each frame */
void        bridgeLoadSectorMap(void);         /* count sectors/asteroids from map  */
int         bridgeGetSectorCount(void);
int         bridgeGetAsteroidCount(void);
int         bridgeGetScoutedCount(void);
/* Phase 9: write combat_event.json when ships engage in weapon range */
void        bridgeWriteCombatEvent(const char *playerShortId,
                                   const char *enemyId,
                                   const char *sectorId);
/* Phase 10A: load visual arrays from JSON for sensor-manager rendering */
void        bridgeLoadSectorVisuals(void);   /* sector_map.json → gSectorVisuals */
void        bridgeLoadShipOverlays(void);    /* ship_overlays.json → gShipOverlays */
ShipOverlay *bridgeFindOverlay(const char *short_id);
/* Phase 11: write sector_entry.json when a ship enters a dark sector */
void        bridgeCheckSectorEntry(const char *shipShortId,
                                   const char *shipType,
                                   float shipX, float shipY, float shipZ);

#endif /* FLEET_BRIDGE_H */
