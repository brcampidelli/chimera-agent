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

// Radix primitives measure and position their popovers on mount. jsdom implements none of that, so
// without these stubs every test that imports a Dialog, Select, Tooltip or Menu throws before it
// renders. Stubbing them is not a fidelity claim — jsdom does no layout, so positioning is
// untestable here either way; these tests assert behaviour and semantics.
if (!globalThis.ResizeObserver) {
  globalThis.ResizeObserver = class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  };
}
if (!globalThis.DOMRect) {
  globalThis.DOMRect = class {
    constructor(
      readonly x = 0,
      readonly y = 0,
      readonly width = 0,
      readonly height = 0,
    ) {}
    get top() {
      return this.y;
    }
    get left() {
      return this.x;
    }
    get right() {
      return this.x + this.width;
    }
    get bottom() {
      return this.y + this.height;
    }
    static fromRect(r?: DOMRectInit) {
      return new DOMRect(r?.x, r?.y, r?.width, r?.height);
    }
    toJSON() {
      return { ...this };
    }
  } as unknown as typeof DOMRect;
}
// jsdom fires pointer events but doesn't implement these two, which Radix calls while deciding
// whether a press came from touch.
if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => {};
  Element.prototype.releasePointerCapture = () => {};
}
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}

// CodeMirror measures text to place the cursor and the selection. jsdom's Range has no
// `getClientRects`, so every editor test printed a stack trace per measure pass — hundreds of lines
// of noise around a green run, which is how a REAL error goes unread. Same reasoning as the Radix
// stubs above: jsdom does no layout, so these coordinates are untestable here either way, and the
// editor tests assert document state rather than geometry.
if (!Range.prototype.getClientRects) {
  Range.prototype.getClientRects = () =>
    Object.assign([] as DOMRect[], { item: () => null }) as unknown as DOMRectList;
  Range.prototype.getBoundingClientRect = () => new DOMRect();
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
