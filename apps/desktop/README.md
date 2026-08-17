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

## What it shows

Five destinations, in the rail's own order (`NAV` in `src/components/IconRail.tsx`), plus
**Settings** at the foot of the rail. There is no separate chat screen — the conversation lives
inside **Code**.

- **Code** — the conversation and what it produces. A session list on the left (persisted, filed
  under the project each conversation was about — new / resume / switch project), the transcript in
  the middle (Markdown with syntax-highlighted code; the answer streams into the transcript itself,
  not a scratch pane), and the **activity** inspector on the right, fed by real per-turn signals
  only (tools called with ✓/✗, tokens in/out + cache, `~ $cost` or "unavailable", memory facts
  recalled + which layer). Nothing is fabricated. Opening a file adds a viewer with an opt-in
  editor; who does the work (provider / roles) and the posture line saying what the agent may touch
  sit with the composer, before you send.
- **Editor** — a real code editor (CodeMirror, loaded on demand — see the note in `App.tsx`) with
  the file tree in the shell's left slot.
- **Work** — what a run *did*: **Runs** (one task, verify-or-revert), the **git** diff it produced,
  and **worth** (whether the expensive profile earned its cost).
- **Knowledge** — what the agent knows: **Memory**, **Profile**, **Skills**.
- **Automation** — what it does unprompted: **Schedule** (cron), **Tasks** (the board), **Agents**
  (the registry the board dispatches against).
- **Settings** — **General** (models / API keys / cache), **Connections** (MCP, servers, tools),
  **Usage**, **Security** (governance).

Two screens exist but are not rail destinations, which is exactly where documentation goes wrong:

- **Onboarding** is a first-run gate, not a place you navigate to. With no provider key configured,
  `App.tsx` renders it *instead of* the app; skipping it drops you in Settings.
- **Maturity** reports the Chimera project's OWN test coverage, so it needs a source checkout —
  in an installed build it would render empty. Both `IconRail.tsx` and `App.tsx` gate it behind
  `import.meta.env.DEV`, so it appears **only under the Vite dev server**
  (`npm --prefix apps/desktop run dev`). `chimera app` serves the production build in `dist/`, and
  the native installers bundle that same build — neither one shows it.

> This list has now drifted twice, in opposite directions. It first called Settings/Memory/Skills/
> Cron/Tasks "Fase B/C" (not yet built) long after they shipped; then it went on naming them as rail
> destinations after fifteen icons were grouped into five, listed a **Chat** screen that no longer
> exists, and called dev-only **Maturity** reachable from the rail. The rail is data — one array in
> `IconRail.tsx` — so checking this list costs one file. If you add, group, or gate a screen, fix
> this list in the same commit.

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
