/*
 * ConsoleOverlay.c — In-game AI chat terminal overlay (Phase 14B, rewrite)
 *
 * Uses the game's native prim2d/font 2D rendering API instead of SDL_Renderer.
 * HTTP to the Mothership Python server runs in a detached SDL thread.
 *
 * Key:  backtick (`) opens/closes | Enter sends | Backspace deletes | Esc closes
 */

#include "ConsoleOverlay.h"

/* Game engine headers — prim2d/font 2D drawing */
#include "prim2d.h"   /* primModeSet2, primModeClear2, primRectTranslucent2, ... */
#include "font.h"     /* fontPrintf */
#include "Color.h"    /* colRGB, colRGBA, color */
#include "main.h"     /* MAIN_WindowWidth, MAIN_WindowHeight */

#include <SDL2/SDL.h>
#include <SDL2/SDL_keycode.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef HAVE_LIBCURL
  #include <curl/curl.h>
#endif

/* ── Config ─────────────────────────────────────────────────────────────────── */

#define CO_PORT          3000
#define CO_MAX_LINES     24
#define CO_MAX_LINE_LEN  300
#define CO_MAX_INPUT     256
#define CO_VISIBLE_ROWS  16      /* message rows shown at once */
#define CO_ROW_H         14      /* pixels per text row */
#define CO_PAD            6      /* inner padding */
#define CO_PANEL_W_PCT   0.40f   /* 40 % of screen width */

/* ── Internal state ─────────────────────────────────────────────────────────── */

static int  g_open       = 0;
static char g_ship_id  [64]  = "";
static char g_ship_type[32]  = "";

/* Ring buffer of displayed lines (each already formatted as "[Who] text") */
static char g_lines [CO_MAX_LINES][CO_MAX_LINE_LEN];
static int  g_line_head  = 0;   /* index of next slot to write */
static int  g_line_count = 0;   /* total lines stored (≤ CO_MAX_LINES) */

/* Input line */
static char g_input    [CO_MAX_INPUT] = "";
static int  g_input_len = 0;

/* Pending reply delivered from HTTP thread */
static char     g_pending [CO_MAX_LINE_LEN] = "";
static SDL_mutex *g_mutex  = NULL;

/* ── Internal helpers ───────────────────────────────────────────────────────── */

static void _push_line(const char *prefix, const char *msg)
{
    char *slot = g_lines[g_line_head % CO_MAX_LINES];
    snprintf(slot, CO_MAX_LINE_LEN, "%s%s", prefix, msg);
    g_line_head++;
    if (g_line_count < CO_MAX_LINES) g_line_count++;
}

static void _flush_pending(void)
{
    if (!g_mutex) return;
    SDL_LockMutex(g_mutex);
    if (g_pending[0]) {
        _push_line("[Mothership] ", g_pending);
        g_pending[0] = '\0';
    }
    SDL_UnlockMutex(g_mutex);
}

/* ── HTTP thread (libcurl) ──────────────────────────────────────────────────── */

#ifdef HAVE_LIBCURL

typedef struct { char ship_id[64]; char ship_type[32]; char msg[CO_MAX_INPUT]; } SendArgs;

/* curl write callback — accumulates into a malloc'd buffer (passed as void**) */
typedef struct { char *buf; size_t len; } CurlBuf;

static size_t _curl_write(void *ptr, size_t sz, size_t nm, void *ud)
{
    CurlBuf *b  = (CurlBuf *)ud;
    size_t bytes = sz * nm;
    char  *tmp   = realloc(b->buf, b->len + bytes + 1);
    if (!tmp) return 0;
    b->buf = tmp;
    memcpy(b->buf + b->len, ptr, bytes);
    b->len += bytes;
    b->buf[b->len] = '\0';
    return bytes;
}

static const char *_json_str_val(const char *json, const char *key,
                                  char *out, size_t out_sz)
{
    char search[80];
    snprintf(search, sizeof(search), "\"%s\":\"", key);
    const char *p = strstr(json, search);
    if (!p) return NULL;
    p += strlen(search);
    size_t i = 0;
    while (*p && *p != '"' && i < out_sz - 1) {
        if (*p == '\\' && *(p+1)) {
            p++;
            out[i++] = (*p == 'n') ? '\n' : (*p == 't') ? '\t' : *p;
        } else {
            out[i++] = *p;
        }
        p++;
    }
    out[i] = '\0';
    return out;
}

static int _http_thread(void *arg)
{
    SendArgs *a = (SendArgs *)arg;

    /* Escape double-quotes in message */
    char safe[CO_MAX_INPUT * 2];
    int  j = 0;
    for (int i = 0; a->msg[i] && j < (int)sizeof(safe) - 2; i++) {
        if (a->msg[i] == '"') safe[j++] = '\\';
        safe[j++] = a->msg[i];
    }
    safe[j] = '\0';

    char url  [128];
    char body [CO_MAX_INPUT * 2 + 128];
    snprintf(url, sizeof(url), "http://127.0.0.1:%d/api/console", CO_PORT);
    snprintf(body, sizeof(body),
             "{\"ship_id\":\"%s\",\"ship_type\":\"%s\",\"message\":\"%s\"}",
             a->ship_id, a->ship_type, safe);

    CurlBuf resp = { malloc(1), 0 };
    if (resp.buf) resp.buf[0] = '\0';

    CURL *curl = curl_easy_init();
    if (curl) {
        struct curl_slist *hdrs = curl_slist_append(NULL,
                                      "Content-Type: application/json");
        curl_easy_setopt(curl, CURLOPT_URL,           url);
        curl_easy_setopt(curl, CURLOPT_POSTFIELDS,    body);
        curl_easy_setopt(curl, CURLOPT_HTTPHEADER,    hdrs);
        curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, _curl_write);
        curl_easy_setopt(curl, CURLOPT_WRITEDATA,     &resp);
        curl_easy_setopt(curl, CURLOPT_TIMEOUT,       30L);

        CURLcode rc = curl_easy_perform(curl);
        if (rc == CURLE_OK && resp.len > 0) {
            char reply[CO_MAX_LINE_LEN] = "";
            if (_json_str_val(resp.buf, "reply", reply, sizeof(reply)) && reply[0]) {
                if (g_mutex) {
                    SDL_LockMutex(g_mutex);
                    strncpy(g_pending, reply, CO_MAX_LINE_LEN - 1);
                    g_pending[CO_MAX_LINE_LEN - 1] = '\0';
                    SDL_UnlockMutex(g_mutex);
                }
            }
        } else {
            if (g_mutex) {
                SDL_LockMutex(g_mutex);
                strncpy(g_pending, "(Mothership unreachable — is server running?)",
                        CO_MAX_LINE_LEN - 1);
                SDL_UnlockMutex(g_mutex);
            }
        }
        curl_slist_free_all(hdrs);
        curl_easy_cleanup(curl);
    }
    if (resp.buf) free(resp.buf);
    free(a);
    return 0;
}

static void _send(const char *msg)
{
    _push_line("[You] ", msg);

    SendArgs *a = (SendArgs *)malloc(sizeof(SendArgs));
    if (!a) return;
    strncpy(a->ship_id,   g_ship_id,   sizeof(a->ship_id)   - 1);
    strncpy(a->ship_type, g_ship_type, sizeof(a->ship_type) - 1);
    strncpy(a->msg,       msg,         sizeof(a->msg)       - 1);
    a->ship_id  [sizeof(a->ship_id)   - 1] = '\0';
    a->ship_type[sizeof(a->ship_type) - 1] = '\0';
    a->msg      [sizeof(a->msg)       - 1] = '\0';

    _push_line("[Mothership] ", "(thinking...)");

    SDL_Thread *t = SDL_CreateThread(_http_thread, "co_http", a);
    if (t) SDL_DetachThread(t);
    else   free(a);
}

#else  /* HAVE_LIBCURL not defined */

static void _send(const char *msg)
{
    _push_line("[You] ", msg);
    _push_line("[System] ", "libcurl not linked — use Python TUI for chat.");
}

#endif /* HAVE_LIBCURL */

/* ── Public API ─────────────────────────────────────────────────────────────── */

void ConsoleOverlay_Init(void)
{
    g_mutex      = SDL_CreateMutex();
    g_open       = 0;
    g_line_head  = 0;
    g_line_count = 0;
    g_input[0]   = '\0';
    g_input_len  = 0;
    g_pending[0] = '\0';
}

void ConsoleOverlay_Open(const char *ship_id, const char *ship_type)
{
    if (g_open && strncmp(g_ship_id, ship_id, sizeof(g_ship_id)) == 0) {
        g_open = 0;   /* toggle closed */
        return;
    }
    strncpy(g_ship_id,   ship_id,   sizeof(g_ship_id)   - 1);
    strncpy(g_ship_type, ship_type, sizeof(g_ship_type) - 1);
    g_ship_id  [sizeof(g_ship_id)   - 1] = '\0';
    g_ship_type[sizeof(g_ship_type) - 1] = '\0';
    g_open       = 1;
    g_input[0]   = '\0';
    g_input_len  = 0;

    if (g_line_count == 0)
        _push_line("[System] ", "Fleet Console ready.  Type and press Enter.");
}

void ConsoleOverlay_Close(void) { g_open = 0; }
int  ConsoleOverlay_IsOpen(void) { return g_open; }

void ConsoleOverlay_Draw(int screen_w, int screen_h)
{
    if (!g_open) return;

    _flush_pending();

    primModeSet2();

    int panW = (int)(screen_w * CO_PANEL_W_PCT);
    int panH = CO_PAD * 3 + CO_ROW_H * (CO_VISIBLE_ROWS + 2);  /* +2: title + input */
    int panX = screen_w - panW - 2;
    int panY = (screen_h - panH) / 2;

    /* ── Background ── */
    rectangle bg;
    bg.x0 = panX;         bg.y0 = panY;
    bg.x1 = panX + panW;  bg.y1 = panY + panH;
    primRectTranslucent2(&bg, colRGBA(4, 10, 28, 215));

    /* ── Border ── */
    primRectOutline2(&bg, 1, colRGBA(55, 130, 210, 200));

    /* ── Title bar ── */
    rectangle title;
    title.x0 = panX;        title.y0 = panY;
    title.x1 = panX + panW; title.y1 = panY + CO_ROW_H + CO_PAD;
    primRectSolid2(&title, colRGBA(10, 38, 95, 245));

    fontPrintf(panX + CO_PAD, panY + 3,
               colRGBA(100, 190, 255, 255),
               "FLEET CONSOLE  [%s]  (T to close)",
               g_ship_id[0] ? g_ship_id : "MOTHERSHIP");

    /* ── Message history ── */
    int ty = panY + CO_ROW_H + CO_PAD + 2;

    int start = (g_line_count > CO_VISIBLE_ROWS)
                ? g_line_count - CO_VISIBLE_ROWS : 0;

    for (int i = start; i < g_line_count; i++) {
        const char *line = g_lines[i % CO_MAX_LINES];
        color c;
        if (strncmp(line, "[You]",       5) == 0)
            c = colRGBA(170, 215, 255, 230);
        else if (strncmp(line, "[Mothership]", 12) == 0)
            c = colRGBA( 90, 240, 130, 230);
        else
            c = colRGBA(150, 150, 150, 180);
        fontPrintf(panX + CO_PAD, ty, c, "%s", line);
        ty += CO_ROW_H;
    }

    /* ── Input separator ── */
    int inputY = panY + panH - CO_ROW_H - CO_PAD - 2;
    primLine2(panX + 1, inputY - 2, panX + panW - 1, inputY - 2,
              colRGBA(55, 100, 155, 160));

    /* ── Input field ── */
    fontPrintf(panX + CO_PAD, inputY,
               colRGBA(220, 240, 255, 255),
               "> %s_", g_input);

    primModeClear2();
}

void ConsoleOverlay_HandleKey(int sdl_keycode)
{
    if (!g_open) return;

    SDL_Keycode k = (SDL_Keycode)sdl_keycode;

    if (k == SDLK_ESCAPE || k == SDLK_t) {
        ConsoleOverlay_Close();
        return;
    }
    if (k == SDLK_RETURN || k == SDLK_KP_ENTER) {
        if (g_input_len > 0) {
            _send(g_input);
            g_input[0]  = '\0';
            g_input_len = 0;
        }
        return;
    }
    if (k == SDLK_BACKSPACE) {
        if (g_input_len > 0)
            g_input[--g_input_len] = '\0';
        return;
    }
}

void ConsoleOverlay_HandleText(const char *text)
{
    if (!g_open || !text || !text[0]) return;
    /* SDL_TEXTINPUT gives UTF-8; accept printable ASCII only */
    unsigned char ch = (unsigned char)text[0];
    if (ch >= 0x20 && ch < 0x7f && g_input_len < CO_MAX_INPUT - 1) {
        g_input[g_input_len++] = (char)ch;
        g_input[g_input_len]   = '\0';
    }
}
