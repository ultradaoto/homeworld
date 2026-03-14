/* FleetBridge.h
 * Bidirectional JSON bridge between the Python Mothership TUI and the
 * Homeworld C engine. No external dependencies — pure stdio + string.
 *
 * Python → C : fleet_state.json   (agent fleet, RU balance, objective)
 * C → Python : engine_state.json  (game RU balance, active flag, timestamp)
 */
#ifndef FLEET_BRIDGE_H
#define FLEET_BRIDGE_H

#define BRIDGE_MAX_SHIPS  64
#define BRIDGE_MAX_OBJ    256
#define BRIDGE_MAX_SPAWNS 8

/* Paths relative to the game working directory (HomeworldSDL/build/).
   Mothership state dir is two levels up at ../../mothership/state/. */
#define BRIDGE_STATE_FILE  "../../mothership/state/fleet_state.json"
#define BRIDGE_ENGINE_FILE "../../mothership/state/engine_state.json"
#define BRIDGE_CMD_FILE    "../../mothership/state/engine_cmd.json"

typedef struct {
    char id[32];
    char type[24];
    char status[16];
} BridgeShip;

typedef struct {
    char ship_class[32]; /* e.g. "LightInterceptor" */
    int  count;          /* how many to queue        */
} BridgeSpawnRequest;

typedef struct {
    int        ru_balance;
    int        ship_count;
    BridgeShip ships[BRIDGE_MAX_SHIPS];
    char       objective[BRIDGE_MAX_OBJ];
    int        findings;
    int        enemy_count;                          /* Phase 6: error threats  */
    int        spawn_count;                          /* Phase 5: pending spawns */
    BridgeSpawnRequest spawn_queue[BRIDGE_MAX_SPAWNS];
    int        valid;       /* 1 if last read succeeded */
} FleetState;

void        bridgeInit(void);
void        bridgeTick(int gameRU, int gameShips, int gameScouts,
                       unsigned int *scoutIDs); /* call once per tick */
FleetState *bridgeGetState(void);
void        bridgeProcessCommand(void);  /* consume engine_cmd.json if present */

#endif /* FLEET_BRIDGE_H */
