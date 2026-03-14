/* FleetBridge.c
 * Dependency-free JSON state reader for the Python Mothership bridge.
 * Polls fleet_state.json at most once per second.
 */
#include "FleetBridge.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static FleetState gState   = {0};
static time_t     gLastRead = 0;

/* -----------------------------------------------------------------------
 * Minimal JSON helpers — only what we need, no full parser required.
 * ----------------------------------------------------------------------- */

/* Copy the string value for `key` into `out` (NUL-terminated, max outLen). */
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

/* Read the integer value for `key` into `*out`. */
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
 * Public API
 * ----------------------------------------------------------------------- */

void bridgeInit(void)
{
    memset(&gState, 0, sizeof(gState));
}

void bridgeTick(void)
{
    time_t now = time(NULL);
    if (now - gLastRead < 1) return;   /* poll at most once per second */
    gLastRead = now;

    FILE *f = fopen(BRIDGE_STATE_FILE, "r");
    if (!f) { gState.valid = 0; return; }

    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    rewind(f);

    if (len <= 0 || len > 65536) { fclose(f); return; }   /* sanity cap */

    char *buf = (char *)malloc((size_t)len + 1);
    if (!buf) { fclose(f); return; }

    fread(buf, 1, (size_t)len, f);
    buf[len] = '\0';
    fclose(f);

    jsonGetInt(buf, "ru_balance", &gState.ru_balance);
    jsonGetInt(buf, "findings",   &gState.findings);
    jsonGetString(buf, "objective", gState.objective, BRIDGE_MAX_OBJ);

    /* Parse ships array — scan for { objects inside the "ships" array */
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
    FILE *f = fopen(BRIDGE_CMD_FILE, "r");
    if (!f) return;

    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    rewind(f);

    if (len <= 0 || len > 4096) { fclose(f); remove(BRIDGE_CMD_FILE); return; }

    char *buf = (char *)malloc((size_t)len + 1);
    if (!buf) { fclose(f); return; }
    fread(buf, 1, (size_t)len, f);
    buf[len] = '\0';
    fclose(f);
    remove(BRIDGE_CMD_FILE);   /* consume — one-shot */

    /* Handle add_ru command: {"type":"add_ru","amount":500} */
    if (strstr(buf, "add_ru"))
    {
        int amount = 0;
        if (jsonGetInt(buf, "amount", &amount) && amount > 0)
            gState.ru_balance += amount;
    }

    free(buf);
}
