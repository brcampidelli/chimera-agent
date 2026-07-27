// Vitest setup, loaded before every test file (see `test.setupFiles` in vite.config.ts).
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// jsdom has no matchMedia, and the theme/motion layer queries it on mount — without this stub every
// test that renders App throws before it renders anything. Defaults to "no match", i.e. dark theme
// and full motion; a test that cares mocks `window.matchMedia` itself.
if (!window.matchMedia) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    // Deprecated, but React and some libraries still reach for them.
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}

// Unmount anything a test rendered and drop any persisted UI state, so tests can't leak into each
// other (VersionBadge, for one, reads localStorage on mount).
afterEach(() => {
  cleanup();
  localStorage.clear();
  // The theme layer writes these to <html>; left behind they'd style the next test's DOM.
  delete document.documentElement.dataset.theme;
  delete document.documentElement.dataset.motion;
});
