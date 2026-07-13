import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

// Same-origin in production (the Python backend serves dist/ and /api together, so no CORS). In dev,
// Vite serves the UI and proxies /api to the running `chimera app` backend on 8765.
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
});
