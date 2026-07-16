/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

// Same-origin in production (the Python backend serves dist/ and /api together, so no CORS). In dev,
// Vite serves the UI and proxies /api to the running `chimera app` backend on 8765.
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
  // Component tests (Vitest + Testing Library) run against jsdom and reuse the `@` alias above, so a
  // test imports exactly what the app imports. `setup.ts` wires jest-dom's matchers.
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    // No `globals` — tests import `describe`/`it`/`expect`/`vi` explicitly, so `tsc` type-checks them
    // with no extra ambient types. `restoreMocks` drops each test's mock state so files can't leak.
    restoreMocks: true,
  },
});
