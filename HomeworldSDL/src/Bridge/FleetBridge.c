/* FleetBridge.c
 * Dependency-free bidirectional JSON bridge for the Python Mothership.
 *
 * Phase 7: serial build queue with real wall-clock timer.
 * When a build_ship command arrives, the ship enters gBuildQueue[].
 * bridgeBuildTick() counts down each frame; on completion it writes
 * engine_built.json for Python and signals UnivUpdate to call clWrapBuildShip.
 */
#include "FleetBridge.h"
#include "ConsoleOverlay.h"   /* Phase 14B: in-game console chat */

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#ifdef _WIN32
#include <windows.h>
#endif

static FleetState gState    = {0};
static time_t     gLastTick = 0;

/* Phase 10A: agent-type → Homeworld ship mesh mapping */
const AgentShipMapping AGENT_SHIP_MAP[] = {
    {"probe",           0,  0, "Probe",             "claude-haiku-4-5"},
    {"scout",           0,  1, "Scout",             "claude-haiku-4-5"},
    {"interceptor",     0,  2, "Interceptor",       "claude-haiku-4-5"},
    {"corvette",        0,  4, "Corvette",          "claude-sonnet-4-6"},
    {"salvage",         0,  5, "Salvage Corvette",  "claude-sonnet-4-6"},
    {"repair",          0,  6, "Repair Corvette",   "claude-haiku-4-5"},
    {"resource",        0,  7, "Resource Collector","claude-haiku-4-5"},
    {"frigate",         0,  9, "Assault Frigate",   "claude-sonnet-4-6"},
    {"destroyer",       0, 11, "Destroyer",         "claude-sonnet-4-6"},
    {"carrier",         0, 12, "Carrier",           "claude-opus-4-6"},
    {"taiidan_probe",   1,  0, "[ENEMY] Probe",     ""},
    {"taiidan_scout",   1,  1, "[ENEMY] Scout",     ""},
    {"taiidan_bomber",  1,  3, "[ENEMY] Bomber",    ""},
    {"taiidan_frigate", 1,  9, "[ENEMY] Frigate",   ""},
    {NULL, 0, 0, NULL, NULL}
};

/* Phase 7: build queue */
BuildQueueEntry gBuildQueue[MAX_BUILD_QUEUE];
int             gBuildQueueLen = 0;
static time_t   gBuildStart   = 0;   /* wall-clock start of current build */

/* Resolved absolute paths — set once in bridgeInit() */
static char gStateFile   [512] = BRIDGE_STATE_FILE;
static char gEngineFile  [512] = BRIDGE_ENGINE_FILE;
static char gCmdFile     [512] = BRIDGE_CMD_FILE;
static char gBuiltFile   [512] = BRIDGE_BUILT_FILE;
static char gSectorFile  [512] = BRIDGE_SECTOR_FILE;
static char gCombatFile  [512] = BRIDGE_COMBAT_FILE;
static char gOverlayFile [512] = BRIDGE_OVERLAY_FILE;
static char gEntryFile   [512] = BRIDGE_ENTRY_FILE;

/* Phase 11: per-ship sector-entry rate-limit (one write per 2 s per ship) */
#define BRIDGE_ENTRY_COOLDOWN 2
static struct { char id[16]; time_t last_entry; } gEntryTimes[BRIDGE_MAX_SHIPS];
static int gEntryTimesLen = 0;

/* Phase 10A: sector visual and ship overlay arrays */
SectorVisual gSectorVisuals[MAX_SECTOR_VISUALS];
int          gSectorVisualCount = 0;
ShipOverlay  gShipOverlays [MAX_SHIP_OVERLAYS];
int          gShipOverlayCount  = 0;

/* Phase 8: sector + asteroid counts loaded from sector_map.json */
static int gSectorCount   = 0;
static int gAsteroidCount = 0;
static int gScoutedCount  = 0;

/* -----------------------------------------------------------------------
 * Minimal JSON helpers
 * ----------------------------------------------------------------------- */

static int jsonGetString(const char *json, const char *key,
                         char *out, int outLen)
{
    char search[72];
    snprintf(search, sizeof(search), "\"%s\"", key);
    const char *p = strstr(json, search);
    if (!p) return 0;
    p += strlen(search);
    while (*p && *p != ':') p++;
    if (!*p) return 0;
    p++;
    while (*p == ' ' || *p == '\t') p++;
    if (*p != '"') return 0;
    p++;
    int i = 0;
    while (*p && *p != '"' && i < outLen - 1)
        out[i++] = *p++;
    out[i] = '\0';
    return 1;
}

static int jsonGetInt(const char *json, const char *key, int *out)
{
    char search[72];
    snprintf(search, sizeof(search), "\"%s\"", key);
    const char *p = strstr(json, search);
    if (!p) return 0;
    p += strlen(search);
    while (*p && *p != ':') p++;
    if (!*p) return 0;
    p++;
    while (*p == ' ' || *p == '\t') p++;
    *out = atoi(p);
    return 1;
}

/* -----------------------------------------------------------------------
 * Write engine state for Python TUI to read
 * ----------------------------------------------------------------------- */

static void bridgeWriteEngineState(int gameRU, int gameShips, int gameScouts,
                                   unsigned int *scoutIDs)
{
    char tmp[520];
    snprintf(tmp, sizeof(tmp), "%s.tmp", gEngineFile);

    FILE *f = fopen(tmp, "w");
    if (!f) return;

    /* Build scout ID array string: [4,7,12] */
    char idBuf[256] = "[";
    for (int i = 0; i < gameScouts && i < BRIDGE_MAX_SHIPS; i++)
    {
        char num[24];
        snprintf(num, sizeof(num), "%s%u", i ? "," : "", scoutIDs[i]);
        strncat(idBuf, num, sizeof(idBuf) - strlen(idBuf) - 2);
    }
    strncat(idBuf, "]", sizeof(idBuf) - strlen(idBuf) - 1);

    /* Build queue info */
    const char *building_callsign = "";
    if (gBuildQueueLen > 0)
        building_callsign = gBuildQueue[0].callsign;

    fprintf(f,
        "{\"ru_balance\":%d,\"game_active\":1,"
        "\"game_ships\":%d,\"game_scouts\":%d,\"scout_ids\":%s,"
        "\"queue_depth\":%d,\"building\":\"%s\","
        "\"map_sectors\":%d,\"map_asteroids\":%d,\"map_scouted\":%d,"
        "\"timestamp\":%.0f}\n",
        gameRU, gameShips, gameScouts, idBuf,
        gBuildQueueLen, building_callsign,
        gSectorCount, gAsteroidCount, gScoutedCount,
        (double)time(NULL));
    fclose(f);

    remove(gEngineFile);
    rename(tmp, gEngineFile);
}

/* -----------------------------------------------------------------------
 * Public API
 * ----------------------------------------------------------------------- */

void bridgeInit(void)
{
    memset(&gState, 0, sizeof(gState));

#ifdef _WIN32
    char exePath[512];
    if (GetModuleFileNameA(NULL, exePath, sizeof(exePath)))
    {
        char *last = strrchr(exePath, '\\');
        if (last) *(last + 1) = '\0';

        snprintf(gStateFile,  sizeof(gStateFile),
                 "%s..\\..\\mothership\\state\\fleet_state.json",  exePath);
        snprintf(gEngineFile, sizeof(gEngineFile),
                 "%s..\\..\\mothership\\state\\engine_state.json", exePath);
        snprintf(gCmdFile,    sizeof(gCmdFile),
                 "%s..\\..\\mothership\\state\\engine_cmd.json",   exePath);
        snprintf(gBuiltFile,  sizeof(gBuiltFile),
                 "%s..\\..\\mothership\\state\\engine_built.json", exePath);
        snprintf(gSectorFile,  sizeof(gSectorFile),
                 "%s..\\..\\mothership\\state\\sector_map.json",    exePath);
        snprintf(gCombatFile,  sizeof(gCombatFile),
                 "%s..\\..\\mothership\\state\\combat_event.json",  exePath);
        snprintf(gOverlayFile, sizeof(gOverlayFile),
                 "%s..\\..\\mothership\\state\\ship_overlays.json", exePath);
        snprintf(gEntryFile,   sizeof(gEntryFile),
                 "%s..\\..\\mothership\\state\\sector_entry.json",  exePath);

        /* Phase 11: override all paths with HW_Bridge_State env var if set */
        {
            const char *bstate = getenv("HW_Bridge_State");
            if (bstate && bstate[0])
            {
                snprintf(gStateFile,   sizeof(gStateFile),   "%s\\fleet_state.json",   bstate);
                snprintf(gEngineFile,  sizeof(gEngineFile),  "%s\\engine_state.json",  bstate);
                snprintf(gCmdFile,     sizeof(gCmdFile),     "%s\\engine_cmd.json",    bstate);
                snprintf(gBuiltFile,   sizeof(gBuiltFile),   "%s\\engine_built.json",  bstate);
                snprintf(gSectorFile,  sizeof(gSectorFile),  "%s\\sector_map.json",    bstate);
                snprintf(gCombatFile,  sizeof(gCombatFile),  "%s\\combat_event.json",  bstate);
                snprintf(gOverlayFile, sizeof(gOverlayFile), "%s\\ship_overlays.json", bstate);
                snprintf(gEntryFile,   sizeof(gEntryFile),   "%s\\sector_entry.json",  bstate);
                printf("[BRIDGE] Using HW_Bridge_State: %s\n", bstate);
            }
        }
    }
#endif
}

/* -----------------------------------------------------------------------
 * Phase 9: write combat_event.json when ships enter weapon range
 * Called by game combat code (UnivUpdate / Tactics) when engagement starts.
 * ----------------------------------------------------------------------- */

void bridgeWriteCombatEvent(const char *playerShortId,
                             const char *enemyId,
                             const char *sectorId)
{
    /* Don't overwrite a pending event the watcher hasn't consumed yet */
    FILE *check = fopen(gCombatFile, "r");
    if (check) { fclose(check); return; }

    FILE *f = fopen(gCombatFile, "w");
    if (!f) return;
    fprintf(f,
        "{\"player_ship\":\"%s\","
        "\"enemy_id\":\"%s\","
        "\"sector_id\":\"%s\"}",
        playerShortId, enemyId, sectorId);
    fclose(f);
    printf("[BRIDGE] Combat event: %s vs %s\n", playerShortId, enemyId);
}

/* -----------------------------------------------------------------------
 * Phase 11: write sector_entry.json when a ship enters an unscouted sector.
 * Call from ship position update loop, e.g. once per game tick per ship.
 * Rate-limited to one write per BRIDGE_ENTRY_COOLDOWN seconds per ship.
 * ----------------------------------------------------------------------- */

void bridgeCheckSectorEntry(const char *shipShortId,
                             const char *shipType,
                             float shipX, float shipY, float shipZ)
{
    if (!shipShortId || !shipShortId[0]) return;

    /* Rate limit: find existing entry or add new one */
    time_t now = time(NULL);
    int slot = -1;
    for (int i = 0; i < gEntryTimesLen; i++)
    {
        if (strncmp(gEntryTimes[i].id, shipShortId, 15) == 0)
        {
            if (now - gEntryTimes[i].last_entry < BRIDGE_ENTRY_COOLDOWN)
                return;   /* too soon for this ship */
            slot = i;
            break;
        }
    }
    if (slot < 0 && gEntryTimesLen < BRIDGE_MAX_SHIPS)
    {
        slot = gEntryTimesLen++;
        strncpy(gEntryTimes[slot].id, shipShortId, 15);
        gEntryTimes[slot].id[15] = '\0';
    }
    if (slot < 0) return;   /* table full */

    /* Don't overwrite a pending entry Python hasn't consumed yet */
    {
        FILE *chk = fopen(gEntryFile, "r");
        if (chk) { fclose(chk); return; }
    }

    /* Check proximity to every unscouted sector */
    for (int i = 0; i < gSectorVisualCount; i++)
    {
        SectorVisual *sv = &gSectorVisuals[i];
        if (sv->scouted || sv->is_pod) continue;

        float dx   = shipX - (float)sv->x;
        float dy   = shipY - (float)sv->y;
        float dz   = shipZ - (float)sv->z;
        float dist = (float)sqrt((double)(dx*dx + dy*dy + dz*dz));
        float threshold = (float)sv->cloud_radius * 0.8f;

        if (dist < threshold)
        {
            FILE *f = fopen(gEntryFile, "w");
            if (!f) return;
            fprintf(f,
                "{\"ship_id\":\"%s\","
                "\"sector_id\":\"%s\","
                "\"ship_type\":\"%s\"}",
                shipShortId,
                sv->id,
                shipType ? shipType : "unknown");
            fclose(f);

            gEntryTimes[slot].last_entry = now;
            printf("[BRIDGE] Sector entry: %s → %s (%.0f < %.0f)\n",
                   shipShortId, sv->id, dist, threshold);
            return;   /* one entry event per tick per ship */
        }
    }
}

/* -----------------------------------------------------------------------
 * Phase 8: sector map reader — counts sectors and asteroids
 * ----------------------------------------------------------------------- */

void bridgeLoadSectorMap(void)
{
    FILE *f = fopen(gSectorFile, "r");
    if (!f) return;

    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    rewind(f);
    if (len <= 0 || len > 1024 * 1024) { fclose(f); return; }  /* max 1 MB */

    char *buf = (char *)malloc((size_t)len + 1);
    if (!buf) { fclose(f); return; }
    fread(buf, 1, (size_t)len, f);
    buf[len] = '\0';
    fclose(f);

    /* Count sectors: occurrences of "\"id\":" at the top-level array level.
       Quick-and-dirty heuristic — good enough for a HUD counter. */
    int sectors   = 0;
    int asteroids = 0;
    int scouted   = 0;
    const char *p = buf;
    while ((p = strstr(p, "\"rel_path\"")) != NULL) { sectors++;   p++; }
    p = buf;
    while ((p = strstr(p, "\"filename\"")) != NULL) { asteroids++; p++; }
    /* Count scouted asteroids: "\"scouted\":1" */
    p = buf;
    while ((p = strstr(p, "\"scouted\":1")) != NULL) { scouted++;  p++; }

    if (sectors != gSectorCount || asteroids != gAsteroidCount)
    {
        gSectorCount   = sectors;
        gAsteroidCount = asteroids;
        gScoutedCount  = scouted;
        printf("[MAP] %d sectors  %d file-asteroids  %d scouted\n",
               sectors, asteroids, scouted);
    }
    else
    {
        /* Update scouted count even if totals unchanged */
        gScoutedCount = scouted;
    }

    free(buf);
}

int bridgeGetSectorCount(void)   { return gSectorCount;   }
int bridgeGetAsteroidCount(void) { return gAsteroidCount; }
int bridgeGetScoutedCount(void)  { return gScoutedCount;  }

/* -----------------------------------------------------------------------
 * Phase 10A: load sector visuals from sector_map.json for dust-cloud render
 * Fills gSectorVisuals[] with id, label, position, threat, scouted.
 * ----------------------------------------------------------------------- */

void bridgeLoadSectorVisuals(void)
{
    FILE *f = fopen(gSectorFile, "r");
    if (!f) return;

    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    rewind(f);
    if (len <= 0 || len > 2 * 1024 * 1024) { fclose(f); return; }

    char *buf = (char *)malloc((size_t)len + 1);
    if (!buf) { fclose(f); return; }
    fread(buf, 1, (size_t)len, f);
    buf[len] = '\0';
    fclose(f);

    gSectorVisualCount = 0;
    const char *p = buf;

    /* Each sector object starts with {"id": ... */
    while ((p = strstr(p, "\"id\":")) != NULL && gSectorVisualCount < MAX_SECTOR_VISUALS)
    {
        SectorVisual *sv = &gSectorVisuals[gSectorVisualCount];
        memset(sv, 0, sizeof(*sv));

        /* id */
        const char *q = p + 5;
        while (*q == ' ' || *q == '"') q++;
        int i = 0;
        while (*q && *q != '"' && i < (int)sizeof(sv->id) - 1)
            sv->id[i++] = *q++;
        sv->id[i] = '\0';

        /* skip malformed / nested id fields (asteroids also have "id") — only
           accept 8-char hex IDs which are sector IDs */
        if (i != 8) { p++; continue; }

        jsonGetString(p, "label",         sv->label, sizeof(sv->label));
        jsonGetInt   (p, "x",            &sv->x);
        jsonGetInt   (p, "y",            &sv->y);
        jsonGetInt   (p, "z",            &sv->z);
        jsonGetInt   (p, "threat",       &sv->threat);
        jsonGetInt   (p, "scouted",      &sv->scouted);
        jsonGetInt   (p, "beacons",      &sv->beacons);
        jsonGetInt   (p, "cloud_r",      &sv->r);
        jsonGetInt   (p, "cloud_g",      &sv->g);
        jsonGetInt   (p, "cloud_b",      &sv->b);
        jsonGetInt   (p, "cloud_a",      &sv->a);
        jsonGetInt   (p, "cloud_density",&sv->density_pct);
        jsonGetInt   (p, "radius",       &sv->cloud_radius);
        if (sv->cloud_radius <= 0) sv->cloud_radius = 2000;  /* safe default */
        jsonGetInt   (p, "locked",       &sv->locked);
        jsonGetInt   (p, "is_pod",       &sv->is_pod);

        gSectorVisualCount++;
        p++;
    }

    free(buf);
}

/* -----------------------------------------------------------------------
 * Phase 10A: load ship overlay info from ship_overlays.json
 * ----------------------------------------------------------------------- */

void bridgeLoadShipOverlays(void)
{
    FILE *f = fopen(gOverlayFile, "r");
    if (!f) { gShipOverlayCount = 0; return; }

    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    rewind(f);
    if (len <= 0 || len > 65536) { fclose(f); gShipOverlayCount = 0; return; }

    char *buf = (char *)malloc((size_t)len + 1);
    if (!buf) { fclose(f); return; }
    fread(buf, 1, (size_t)len, f);
    buf[len] = '\0';
    fclose(f);

    gShipOverlayCount = 0;
    const char *p = buf;

    while ((p = strstr(p, "\"short_id\"")) != NULL
           && gShipOverlayCount < MAX_SHIP_OVERLAYS)
    {
        ShipOverlay *ov = &gShipOverlays[gShipOverlayCount];
        memset(ov, 0, sizeof(*ov));

        /* find the enclosing object by walking back to '{' */
        const char *obj = p;
        while (obj > buf && *obj != '{') obj--;

        jsonGetString(obj, "short_id",   ov->short_id,    sizeof(ov->short_id));
        jsonGetString(obj, "callsign",   ov->callsign,    sizeof(ov->callsign));
        jsonGetString(obj, "ship_type",  ov->ship_type,   sizeof(ov->ship_type));
        jsonGetString(obj, "status",     ov->status,      sizeof(ov->status));
        jsonGetString(obj, "sector",     ov->sector,      sizeof(ov->sector));
        jsonGetString(obj, "task",       ov->task_desc,   sizeof(ov->task_desc));
        jsonGetString(obj, "container",  ov->container_id,sizeof(ov->container_id));
        jsonGetInt   (obj, "ctx_used",   &ov->ctx_used);
        jsonGetInt   (obj, "ctx_budget", &ov->ctx_budget);
        jsonGetInt   (obj, "armor",      &ov->armor);
        jsonGetInt   (obj, "max_armor",  &ov->max_armor);
        jsonGetInt   (obj, "kills",      &ov->kills);

        if (ov->short_id[0]) gShipOverlayCount++;
        p++;
    }

    free(buf);
}

ShipOverlay *bridgeFindOverlay(const char *short_id)
{
    for (int i = 0; i < gShipOverlayCount; i++)
        if (strcmp(gShipOverlays[i].short_id, short_id) == 0)
            return &gShipOverlays[i];
    return NULL;
}

void bridgeTick(int gameRU, int gameShips, int gameScouts, unsigned int *scoutIDs)
{
    time_t now = time(NULL);
    if (now - gLastTick < 1) return;   /* at most once per second */
    gLastTick = now;

    /* --- C → Python: write current game state --- */
    bridgeWriteEngineState(gameRU, gameShips, gameScouts, scoutIDs);

    /* --- Python → C: read agent fleet state --- */
    FILE *f = fopen(gStateFile, "r");
    if (!f) { gState.valid = 0; return; }

    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    rewind(f);

    if (len <= 0 || len > 65536) { fclose(f); return; }

    char *buf = (char *)malloc((size_t)len + 1);
    if (!buf) { fclose(f); return; }

    fread(buf, 1, (size_t)len, f);
    buf[len] = '\0';
    fclose(f);

    jsonGetInt(buf, "ru_balance",    &gState.ru_balance);
    jsonGetInt(buf, "findings",      &gState.findings);
    jsonGetInt(buf, "escape_pods",   &gState.escape_pod_count);
    jsonGetString(buf, "objective",   gState.objective, BRIDGE_MAX_OBJ);

    gState.ship_count = 0;
    const char *arr = strstr(buf, "\"ships\"");
    if (arr)
    {
        arr = strchr(arr, '[');
        while (arr && *arr && gState.ship_count < BRIDGE_MAX_SHIPS)
        {
            const char *obj = strchr(arr, '{');
            if (!obj) break;
            BridgeShip *s = &gState.ships[gState.ship_count];
            jsonGetString(obj, "id",     s->id,     sizeof(s->id));
            jsonGetString(obj, "type",   s->type,   sizeof(s->type));
            jsonGetString(obj, "status", s->status, sizeof(s->status));
            if (s->id[0]) gState.ship_count++;
            arr = strchr(obj, '}');
            if (arr) arr++;
        }
    }

    gState.valid = 1;
    free(buf);

    /* Phase 8: refresh sector map counts once per second */
    bridgeLoadSectorMap();

    /* Phase 10A: refresh sector visuals + ship overlays for SM render */
    bridgeLoadSectorVisuals();
    bridgeLoadShipOverlays();

    bridgeProcessCommand();
}

FleetState *bridgeGetState(void)
{
    return &gState;
}

/* -----------------------------------------------------------------------
 * Phase 7: serial build queue
 * ----------------------------------------------------------------------- */

void bridgeProcessBuildCommand(const char *buf)
{
    if (!strstr(buf, "\"build_ship\"")) return;

    char ship_type [24] = {0};
    char short_id  [16] = {0};
    char callsign  [32] = {0};
    int  build_secs     = 30;

    jsonGetString(buf, "ship_type",  ship_type,  sizeof(ship_type));
    jsonGetString(buf, "short_id",   short_id,   sizeof(short_id));
    jsonGetString(buf, "callsign",   callsign,   sizeof(callsign));
    jsonGetInt   (buf, "build_secs", &build_secs);

    if (!ship_type[0]) return;   /* malformed command */

    if (gBuildQueueLen >= MAX_BUILD_QUEUE)
    {
        printf("[BUILD] Queue full, rejecting %s\n",
               callsign[0] ? callsign : ship_type);
        return;
    }

    BuildQueueEntry *e = &gBuildQueue[gBuildQueueLen++];
    strncpy(e->ship_type,  ship_type,  sizeof(e->ship_type)  - 1);
    strncpy(e->short_id,   short_id,   sizeof(e->short_id)   - 1);
    strncpy(e->callsign,   callsign,   sizeof(e->callsign)   - 1);
    e->build_secs_remaining = build_secs;

    printf("[BUILD QUEUE] %s '%s' queued (%ds)  depth=%d\n",
           ship_type, callsign, build_secs, gBuildQueueLen);
}

void bridgeBuildTick(void)
{
    if (gBuildQueueLen == 0) { gBuildStart = 0; return; }

    BuildQueueEntry *active = &gBuildQueue[0];
    time_t now = time(NULL);

    if (gBuildStart == 0) gBuildStart = now;

    if (now - gBuildStart < (time_t)active->build_secs_remaining)
        return;   /* still building */

    /* Build complete — notify Python */
    FILE *f = fopen(gBuiltFile, "w");
    if (f)
    {
        fprintf(f,
            "{\"short_id\":\"%s\",\"callsign\":\"%s\",\"type\":\"%s\"}",
            active->short_id, active->callsign, active->ship_type);
        fclose(f);
    }

    /* Signal UnivUpdate.c to call clWrapBuildShip */
    gState.pending_build_ready = 1;
    strncpy(gState.pending_build_type,
            active->ship_type, sizeof(gState.pending_build_type) - 1);
    strncpy(gState.pending_build_callsign,
            active->callsign,  sizeof(gState.pending_build_callsign) - 1);
    strncpy(gState.pending_build_id,
            active->short_id,  sizeof(gState.pending_build_id) - 1);

    printf("[BUILD] LAUNCHED: %s '%s'\n", active->ship_type, active->callsign);

    /* Advance queue */
    memmove(&gBuildQueue[0], &gBuildQueue[1],
            sizeof(BuildQueueEntry) * (size_t)(gBuildQueueLen - 1));
    gBuildQueueLen--;
    gBuildStart = 0;
}

/* -----------------------------------------------------------------------
 * Command processor — engine_cmd.json
 * ----------------------------------------------------------------------- */

void bridgeProcessCommand(void)
{
    FILE *f = fopen(gCmdFile, "r");
    if (!f) return;

    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    rewind(f);

    if (len <= 0 || len > 4096) { fclose(f); remove(gCmdFile); return; }

    char *buf = (char *)malloc((size_t)len + 1);
    if (!buf) { fclose(f); return; }
    fread(buf, 1, (size_t)len, f);
    buf[len] = '\0';
    fclose(f);
    remove(gCmdFile);

    if (strstr(buf, "add_ru"))
    {
        int amount = 0;
        if (jsonGetInt(buf, "amount", &amount) && amount > 0)
            gState.ru_balance += amount;
    }

    /* Phase 7: serial build queue */
    if (strstr(buf, "build_ship"))
        bridgeProcessBuildCommand(buf);

    /* Phase 6: error threats become enemy ships */
    if (strstr(buf, "spawn_enemy"))
    {
        char ship_type[32] = {0};
        int  sev = 0;
        jsonGetString(buf, "ship", ship_type, sizeof(ship_type));
        jsonGetInt(buf, "sev", &sev);
        gState.enemy_count++;
        printf("[BRIDGE] ENEMY SPAWNED: %-14s  sev=%d  threats=%d\n",
               ship_type, sev, gState.enemy_count);
    }

    if (strstr(buf, "destroy_enemy"))
    {
        if (gState.enemy_count > 0) gState.enemy_count--;
        printf("[BRIDGE] ENEMY DESTROYED  threats=%d\n", gState.enemy_count);
    }

    /* Phase 9: Python resolves combat → tells engine to kill a ship */
    if (strstr(buf, "set_ship_hp"))
    {
        char short_id[16] = {0};
        char eid     [32] = {0};
        int  hp           = 0;
        jsonGetString(buf, "short_id", short_id, sizeof(short_id));
        jsonGetString(buf, "id",       eid,      sizeof(eid));
        jsonGetInt   (buf, "hp",       &hp);
        if (short_id[0])
        {
            printf("[BRIDGE] SET HP: player ship %s → %d\n", short_id, hp);
            /* Phase 10B: kill the Docker container when HP hits 0.
               SIGTERM triggers the escape pod handler in ship_wrapper.py;
               Docker waits 8 s then sends SIGKILL. */
            if (hp <= 0)
            {
                ShipOverlay *ov = bridgeFindOverlay(short_id);
                if (ov && ov->container_id[0])
                {
                    char cmd[160];
                    snprintf(cmd, sizeof(cmd),
                             "docker stop --time 8 %s 2>/dev/null &",
                             ov->container_id);
                    system(cmd);
                    printf("[BRIDGE] SIGTERM → container %s (%s)\n",
                           ov->container_id, short_id);
                }
            }
        }
        else if (eid[0])
            printf("[BRIDGE] SET HP: enemy %s → %d\n", eid, hp);
        if (hp == 0 && eid[0])
        {
            if (gState.enemy_count > 0) gState.enemy_count--;
        }
    }

    /* Phase 10A: hyperspace jump — clear enemies, signal animation */
    if (strstr(buf, "hyperspace_jump"))
    {
        gState.enemy_count = 0;
        printf("[BRIDGE] HYPERSPACE JUMP — sector cleared, fleet jumping\n");
        /* The C engine can trigger smHyperspace() or the level warp here.
           For now we log and let the game's own NIS/level system handle it. */
    }

    /* Phase 9: enemy breached sector — update threat tracking */
    if (strstr(buf, "enemy_attacks_sector"))
    {
        char sector_id[16] = {0};
        char eid      [32] = {0};
        jsonGetString(buf, "id",        eid,       sizeof(eid));
        jsonGetString(buf, "sector_id", sector_id, sizeof(sector_id));
        printf("[BRIDGE] SECTOR BREACH: enemy %s attacked sector %s\n",
               eid, sector_id);
    }

    free(buf);
}

/* -----------------------------------------------------------------------
 * Phase 14B: Console overlay hooks
 * ----------------------------------------------------------------------- */

/* Last ship the player right-clicked */
static char g_selected_ship_id  [64] = {0};
static char g_selected_ship_type[32] = {0};

void FleetBridge_SetSelectedShip(const char *ship_id, const char *ship_type)
{
    if (!ship_id || !ship_type) return;
    strncpy(g_selected_ship_id,   ship_id,   sizeof(g_selected_ship_id)   - 1);
    strncpy(g_selected_ship_type, ship_type, sizeof(g_selected_ship_type) - 1);
}

/* Called by the right-click context menu when "Open Console" is chosen */
void FleetBridge_OpenConsole(void)
{
    if (!g_selected_ship_id[0]) return;
    ConsoleOverlay_Open(g_selected_ship_id, g_selected_ship_type);
}

/* Called once per frame from rndFlush() — draws overlay using prim2d/font */
void FleetBridge_DrawConsoleOverlay(int screen_w, int screen_h)
{
    ConsoleOverlay_Draw(screen_w, screen_h);
}

/* Forward a SDL_KEYDOWN keycode to the overlay */
void FleetBridge_ConsoleKeyInput(int sdl_keycode)
{
    ConsoleOverlay_HandleKey(sdl_keycode);
}

/* Forward a SDL_TEXTINPUT string to the overlay */
void FleetBridge_ConsoleTextInput(const char *text)
{
    ConsoleOverlay_HandleText(text);
}

/* 1 if the console has focus and game keys should be suppressed */
int FleetBridge_ConsoleHasFocus(void)
{
    return ConsoleOverlay_IsOpen();
}
