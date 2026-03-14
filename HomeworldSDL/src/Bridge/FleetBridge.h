/* FleetBridge.h
 * Reads Python Mothership fleet state from a JSON file written by
 * mothership/bridge/state_file.py once per second.
 * No external dependencies — pure stdio + string.
 */
#ifndef FLEET_BRIDGE_H
#define FLEET_BRIDGE_H

#define BRIDGE_MAX_SHIPS  64
#define BRIDGE_MAX_OBJ    256

/* Path is relative to the game working directory (assets/).
   The mothership writes to mothership/state/fleet_state.json
   which sits one level up from assets/. */
#define BRIDGE_STATE_FILE "../mothership/state/fleet_state.json"
#define BRIDGE_CMD_FILE   "../mothership/state/engine_cmd.json"

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
    int        valid;   /* 1 if last file read succeeded */
} FleetState;

void        bridgeInit(void);
void        bridgeTick(void);           /* call once per game tick        */
FleetState *bridgeGetState(void);
void        bridgeProcessCommand(void); /* consume engine_cmd.json if present */

#endif /* FLEET_BRIDGE_H */
