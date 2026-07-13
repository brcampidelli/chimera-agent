# Chimera Desktop

A local React UI for the Chimera agent. It talks to the Python backend over **HTTP + Server-Sent
Events** — no Electron, no WebSocket, no cloud. The backend (`chimera/api`, the opt-in `[desktop]`
extra) serves this built app *same-origin* alongside the API, so there's no CORS to configure.

Stack: Vite + React + TypeScript + Tailwind CSS + shadcn-style components + TanStack Query.

## Run it (users)

```bash
pip install 'chimera-agent[desktop]'
pnpm --dir apps/desktop install && pnpm --dir apps/desktop build   # or: npm --prefix apps/desktop ...
chimera app        # serves the UI + API on http://127.0.0.1:8765 and opens your browser
```

`chimera app --fuse` routes turns through LLM-Fusion (no token streaming — the answer arrives whole);
`--no-memory` skips long-term recall; `--model <slug>` overrides the model.

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

The hand-written types in `src/lib/types.ts` are a placeholder — the fast-follow generates them from
the backend's OpenAPI schema (`/api/openapi.json`) via `openapi-typescript`, so the contract can't
drift.
