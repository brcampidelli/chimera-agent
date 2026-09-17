# Results — what Chimera itself costs to start and to run a turn, outside the model

**Run 2026-09-17 against `649a7ab`, as registered in `PREREGISTRATION.md`.** i7 laptop, Windows 11,
Python 3.11 in the repo venv; no model, US$ 0. Raw tables: `results/table_before.json` (the tree as
registered) and `results/table_after.json` (with the one fix below).

## The numbers, before anything was changed

| what | measured |
|---|---|
| spawn → socket bound (port file written), warm cache, median of 4 | **1.02 s** (1.01–1.03); first run 1.06 s |
| bound → `/api/health` answers 200 | **+0.53 s** |
| spawn → health, warm median | **1.55 s**; first run 1.61 s |
| imports at boot, every top-level import summed | **0.90 s** — `chimera.cli.main` 454 ms, `httpcore` 131 ms (the price-cache thread's `httpx`), `fastapi` 123 ms, `chimera.api.code_api` 93 ms, `uvicorn` 31 ms |
| inside `chimera.cli.main` | `chimera.config` 140 ms (pydantic-settings 79, of which `importlib.metadata` 71; keyring 48 for the credential vault), `chimera.providers.gateway` 137 ms (asyncio 38), typer 48, rich 28 |
| `litellm` at boot | **not imported** — already lazy on first provider use |
| harness overhead per turn (instant agent, 20 turns, in-process) | median **16.4 ms**, p95 19.0, min 14.1 |
| RSS after boot / after 20 turns | 91 MB / 241 MB (the in-process figure includes the test client and the bench) |
| idle CPU over 10 s | **0.0 s** |

## Predictions against the numbers

1. **Cold start 1.5–4 s, health within 100 ms of the bind — half held.** 1.55 s to health is inside
   the range, but the health answer came **530 ms** after the bind, not 100. And the named culprit
   was wrong: `litellm` is not imported at boot at all. The boot *is* imports — 0.90 of the 1.02 s to
   bind — spread over the CLI module, FastAPI and the API routes, with no single item above 15%.
2. **Turn overhead 30–150 ms — wrong, lower.** 16 ms. The memory recall, registry build, prompt
   assembly, session save and history record together cost less than a frame of video.
3. **Idle under 0.5 s CPU, RSS 120–250 MB — held on CPU, RSS is below the range.** 0.0 s and 91 MB.

## Where the half second went — and it was not the server

The main thread was idle in the event loop 10 ms after the port file was written (sampled at
10 ms intervals), and a request sent 1.5 s after the bind was answered in 12 ms. The 530 ms was on
the **client's** side of the connect: `_bind_app_socket` bound the socket but did not `listen()`;
the port file was written; uvicorn called `listen()` ~20 ms later. A connect made in that window
met a bound-but-not-listening socket, and on Windows that SYN is **dropped, not refused** — the
client sat on the 500 ms retransmit timer, and the second SYN found the socket listening. On
POSIX the same SYN is refused, and the desktop shell's `wait_for_listening` sleeps 150 ms before
trying again.

This is the desktop's launch path exactly: the Tauri shell reads the port file and connects at
once (`wait_for_listening`, `connect_timeout(500 ms)`, retry every 150 ms), so **every launch paid
~0.5 s on Windows and ~150–650 ms elsewhere** between "the backend reported its port" and "the
window loaded". Found by timing a raw-socket client from inside the process
(`probe_connect.py`, kept beside this file): `connect()` took 0.51 s, the request 6 ms.

## The fix, and the number after it

`_bind_app_socket` now calls `listen(128)` before returning, so the socket is listening when the
port file is written; a connect made that instant is queued in the backlog and served the moment
uvicorn accepts. One line; uvicorn's own `listen()` on an already-listening socket is a no-op.

| what | before | after | Δ |
|---|---:|---:|---:|
| bound → health, warm median | 0.53 s | **0.02 s** | −0.51 s |
| spawn → health, warm median | 1.55 s | **1.03 s** | **−34%** |
| spawn → bound | 1.02 s | 1.01 s | — (unchanged, as it should be) |
| turn overhead, median | 16.4 ms | 15.0 ms | noise |

Pinned by `test_the_app_socket_listens_before_the_port_is_announced`: a connect to the returned
socket succeeds within 400 ms with no server accepting; with `listen()` removed the test fails on
Windows (2 s timeout) and on POSIX (refused) — sabotage-verified on Windows.

## Not changed, and why

- **Imports (0.90 s).** No single import is above 20% of the boot: the largest deferrable ones are
  `keyring` (48 ms, but it loads the credential vault that the first turn needs) and
  `importlib.metadata` (71 ms, but pydantic-settings imports it regardless, so a lazy `__version__`
  would save nothing). The registered rule says nothing under 20% moves, and nothing did.
- **The shell's port-file poll (200 ms).** Up to 200 ms, ~100 ms on average, of the remaining
  1.03 s is the Tauri side reading the port file every 200 ms. A 7–10% item, below the rule; noted
  for the shell.
- **The packaged sidecar** was not measured (a frozen build unpacks its imports from an archive);
  the listen-before-announce fix applies to it identically, because the port-file sequence is the
  same code.

## What this cannot show

One machine, one OS. The 500 ms is Windows' SYN retransmit timer; on macOS and Linux the saving is
the shell's retry interval instead. No model call was timed, so nothing here says what a turn
costs — only what the harness adds to one.
