# Chimera Desktop

A local React UI for the Chimera agent. It talks to the Python backend over **HTTP + Server-Sent
Events** — no Electron, no WebSocket, no cloud. The backend (`chimera/api`, the opt-in `[desktop]`
extra) serves this built app *same-origin* alongside the API, so there's no CORS to configure.

Stack: Vite + React + TypeScript + Tailwind CSS + shadcn-style components + TanStack Query.

## Run it — three ways, pick one

### 1. Native app (zero-install) — easiest for non-developers
Download the installer for your OS from the [latest Release](https://github.com/brcampidelli/chimera-agent/releases):
`.exe` (Windows/NSIS), `.dmg` (macOS), or `.AppImage`/`.deb` (Linux). It bundles **everything** — no
system Python, no `pip install` — a small native shell (Tauri, using your OS's webview) over a frozen
copy of the same backend. It is **unsigned for now**, so the first launch shows a SmartScreen (Windows)
or Gatekeeper (macOS) warning — choose *Run anyway* / right-click → *Open*. The terminal CLI stays fully
sovereign; the app is just an optional front door.

### 2. From pip (if you already use Chimera in the terminal)
```bash
pip install 'chimera-agent[desktop]'
chimera app        # serves the bundled UI + API on http://127.0.0.1:8765 and opens your browser
```
The wheel already bundles the built UI, so there's no build step. `chimera app --fuse` routes turns
through LLM-Fusion (no token streaming — the answer arrives whole); `--no-memory` skips long-term
recall; `--model <slug>` overrides the model; `--port 0` binds any free port (a busy port also falls
back automatically).

### 3. Install the running web app as a PWA
With `chimera app` running (option 2), open it in Chrome or Edge and use **Install** (the icon in the
address bar, or ⋮ → *Install Chimera*). It opens in its own window with a taskbar/dock icon — an
app-like experience with no extra runtime. A small service worker caches the static shell for instant
startup; it never touches `/api`, so the chat stream is unaffected.

> **Native app vs PWA?** The native app (1) is the zero-dependency download — pick it if you don't have
> Python. The PWA (3) is the lightest option if you're already running `chimera app`. Both are optional:
> everything the app does has a terminal equivalent.

## Develop

```bash
chimera app --no-open          # terminal 1: the backend/API on :8765
npm --prefix apps/desktop run dev   # terminal 2: Vite dev server (proxies /api → :8765)
```

`npm run build` runs `tsc --noEmit` then `vite build` → `dist/` (what `chimera app` serves).

## What it shows (Fase A)

- **Chat** — three panes mirroring the TUI: a Markdown transcript with syntax-highlighted code, a
  live token buffer streaming as the model writes, and an **activity** sidebar fed by real per-turn
  signals only (tools called with ✓/✗, tokens in/out + cache, `~ $cost` or "unavailable", memory
  facts recalled + which layer). Nothing is fabricated.
- **Sessions** — a persisted conversation list (new / switch / delete); transcripts survive restarts.

Settings (models / API keys / cache / MCP), Memory, Skills, Cron and Tasks screens are Fase B/C.

## Typed API client (no drift)

The API response types in `src/lib/types.ts` are **generated from the backend's OpenAPI schema**, so
the UI can't drift from the server: every endpoint has a Pydantic `response_model` (`chimera/api/
schemas.py`), and the frontend re-exports those shapes. If a backend model changes, regenerate and any
mismatch becomes a TypeScript error at build time. To regenerate:

```bash
# 1. dump the schema from the backend (single source of truth)
python -m chimera.api.schema_dump > apps/desktop/openapi.json
# 2. generate the TypeScript definitions
npm --prefix apps/desktop run gen:api    # openapi-typescript openapi.json → src/lib/api-schema.ts
```

(The chat stream is Server-Sent Events, not a typed HTTP body, so its event payloads stay hand-written
in `types.ts`.)
