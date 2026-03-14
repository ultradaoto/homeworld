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

/* Paths relative to the game working directory (assets/).
   Mothership state dir is one level up at ../mothership/state/. */
#define BRIDGE_STATE_FILE  "../mothership/state/fleet_state.json"
#define BRIDGE_ENGINE_FILE "../mothership/state/engine_state.json"
#define BRIDGE_CMD_FILE    "../mothership/state/engine_cmd.json"

typedef struct {
    char id[32];
    char type[24];
    char status[16];
} BridgeShip;

typedef struct {
    int        ru_balance;
    int        ship_count;
    BridgeShip ships[BRIDGE_MAX_SHIPS];
    char       objective[BRIDGE_MAX_OBJ];
    int        findings;
    int        valid;   /* 1 if last read succeeded */
} FleetState;

void        bridgeInit(void);
void        bridgeTick(int gameRU);      /* call once per game tick        */
FleetState *bridgeGetState(void);
void        bridgeProcessCommand(void);  /* consume engine_cmd.json if present */

#endif /* FLEET_BRIDGE_H */
