/*
 * ConsoleOverlay.h — In-game AI chat terminal overlay (Phase 14B, rewrite)
 *
 * Renders using the game's native prim2d/font 2D drawing API.
 * No SDL_Renderer or SDL_TTF required — compatible with the OpenGL pipeline.
 *
 * Integration:
 *   Game init         → ConsoleOverlay_Init()
 *   rndFlush()        → ConsoleOverlay_Draw() before SDL_GL_SwapWindow
 *   HandleEvent()     → SDL_KEYDOWN: ConsoleOverlay_HandleKey()
 *                       SDL_TEXTINPUT: ConsoleOverlay_HandleText()
 *
 * Trigger: SDL_SCANCODE_GRAVE (backtick `) opens / closes the overlay.
 */

#ifndef CONSOLE_OVERLAY_H
#define CONSOLE_OVERLAY_H

/* Call once during game init (after SDL_Init). Creates internal mutex. */
void ConsoleOverlay_Init(void);

/* Open for a ship. Calling again with same ship_id closes (toggle). */
void ConsoleOverlay_Open(const char *ship_id, const char *ship_type);

/* Close immediately. */
void ConsoleOverlay_Close(void);

/* Non-zero when overlay is visible (suppress game key processing). */
int  ConsoleOverlay_IsOpen(void);

/*
 * Draw using prim2d/font.  Call from rndFlush() before the GL buffer swap.
 * The function calls primModeSet2() / primModeClear2() internally.
 */
void ConsoleOverlay_Draw(int screen_w, int screen_h);

/* Route a SDL_KEYDOWN keycode (Enter, Backspace, Escape, grave). */
void ConsoleOverlay_HandleKey(int sdl_keycode);

/* Route a SDL_TEXTINPUT character string. */
void ConsoleOverlay_HandleText(const char *text);

#endif /* CONSOLE_OVERLAY_H */
