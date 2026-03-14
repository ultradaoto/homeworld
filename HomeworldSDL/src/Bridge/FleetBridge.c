/* FleetBridge.c
 * Dependency-free bidirectional JSON bridge for the Python Mothership.
 * Reads fleet_state.json (Python → C) and writes engine_state.json (C → Python)
 * at most once per second.
 */
#include "FleetBridge.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#ifdef _WIN32
#include <windows.h>
#endif

static FleetState gState    = {0};
static time_t     gLastTick = 0;

/* Resolved absolute paths — set once in bridgeInit() */
static char gStateFile [512] = BRIDGE_STATE_FILE;
static char gEngineFile[512] = BRIDGE_ENGINE_FILE;
static char gCmdFile   [512] = BRIDGE_CMD_FILE;

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

    fprintf(f,
        "{\"ru_balance\":%d,\"game_active\":1,"
        "\"game_ships\":%d,\"game_scouts\":%d,\"scout_ids\":%s,"
        "\"timestamp\":%.0f}\n",
        gameRU, gameShips, gameScouts, idBuf, (double)time(NULL));
    fclose(f);

    /* rename() is atomic on POSIX; on Windows it overwrites if dest exists */
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
    /* Resolve state dir relative to the executable, not the CWD.
     * Exe is at <project>/HomeworldSDL/build/homeworld.exe
     * State dir is at <project>/mothership/state/             */
    char exePath[512];
    if (GetModuleFileNameA(NULL, exePath, sizeof(exePath)))
    {
        /* Strip filename → get exe directory */
        char *last = strrchr(exePath, '\\');
        if (last) *(last + 1) = '\0';

        snprintf(gStateFile,  sizeof(gStateFile),  "%s..\\..\\mothership\\state\\fleet_state.json",  exePath);
        snprintf(gEngineFile, sizeof(gEngineFile), "%s..\\..\\mothership\\state\\engine_state.json", exePath);
        snprintf(gCmdFile,    sizeof(gCmdFile),    "%s..\\..\\mothership\\state\\engine_cmd.json",   exePath);
    }
#endif
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

    jsonGetInt(buf, "ru_balance", &gState.ru_balance);
    jsonGetInt(buf, "findings",   &gState.findings);
    jsonGetString(buf, "objective", gState.objective, BRIDGE_MAX_OBJ);

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

    bridgeProcessCommand();
}

FleetState *bridgeGetState(void)
{
    return &gState;
}

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

    /* Phase 5: build a real in-game ship from TUI command */
    if (strstr(buf, "build_ship"))
    {
        char cls[32] = {0};
        int  count   = 1;
        jsonGetString(buf, "class", cls, sizeof(cls));
        jsonGetInt(buf, "count", &count);
        if (count < 1) count = 1;
        if (count > 9) count = 9;
        if (cls[0] && gState.spawn_count < BRIDGE_MAX_SPAWNS)
        {
            BridgeSpawnRequest *req = &gState.spawn_queue[gState.spawn_count++];
            strncpy(req->ship_class, cls, sizeof(req->ship_class) - 1);
            req->count = count;
            printf("[BRIDGE] QUEUED BUILD: %dx%s  (queue=%d)\n",
                   count, req->ship_class, gState.spawn_count);
        }
    }

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

    free(buf);
}
