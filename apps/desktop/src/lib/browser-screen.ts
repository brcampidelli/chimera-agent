/**
 * The browser-screen pairing constants the API client and the code prompt share
 * (`chimera/api/browser_screen.py`). Kept out of `@/lib/api` on purpose: a suite that mocks that module
 * would otherwise have to know about an event name it never uses.
 */

/** The response header the server sets on a refusal that a browser's code prompt could answer. */
export const NEEDS_SCREEN_HEADER = "x-chimera-needs-screen";

/** Fired on `window` when a screen-only action is refused in a tab that holds no screen. */
export const SCREEN_NEEDED_EVENT = "chimera:screen-needed";
