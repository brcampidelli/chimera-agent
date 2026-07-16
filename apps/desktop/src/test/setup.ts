// Vitest setup, loaded before every test file (see `test.setupFiles` in vite.config.ts).
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Unmount anything a test rendered and drop any persisted UI state, so tests can't leak into each
// other (VersionBadge, for one, reads localStorage on mount).
afterEach(() => {
  cleanup();
  localStorage.clear();
});
