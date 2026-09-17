# Pre-registration — what does Chimera itself cost to start and to run a turn, outside the model?

**Registered 2026-09-17 against `649a7ab` (v0.58.0 + #499–#504), before the script was run.**
Deterministic apart from the clock; no model, no key, US$ 0. Item 8 of the list audited on
2026-09-16 ("Speed"). The audit found the numbers on the marketing list (4.3 s → 0.9 s, 14× CPU)
are not ours and must not be cited; this is the measurement of our own.

## What is measured, and why these

A person launches the desktop app and waits for the backend before the first screen is useful; then
every turn pays the harness before the model is even asked. Three numbers, on this machine (i7
laptop, Windows 11, Python 3.11 in the repo venv — stated because a latency is of a machine):

1. **Cold start to bound.** `python -m chimera app --no-open --port 0 --emit-port-file F`, from
   process spawn until `F` exists (the server is bound) and then until `GET /api/health` answers
   200. Five runs, median and spread. Warm-cache runs (the OS file cache is warm after the first),
   which is the ordinary launch; the true cold disk read is one run and reported apart.
2. **Where the start goes.** `python -X importtime -c "import chimera.cli.main"` and the same for
   `chimera.api`, the top ten cumulative import costs. An import that is paid at boot and used only
   by a command nobody ran at boot is the candidate to defer.
3. **Harness overhead per turn.** `POST /api/code/turn` against an agent stub that answers at once
   (the shape every API test uses), 20 turns in one session after a warm-up turn: median wall time
   of the request. This is everything a turn pays that is not the model — registry build, memory
   recall, prompt assembly, session save, history record. Reported with `steps=1` so the number is
   the harness's alone.
4. **Idle cost.** RSS of the backend process after boot and after the 20 turns; CPU time consumed
   over 10 idle seconds (from the process's own counters, not a sampler).

## Registered predictions

1. Cold start to bound is between 1.5 s and 4 s on this machine, and the health answer follows
   within 100 ms of the bind. The import of `litellm` (pulled in by the provider gateway at boot)
   is the single largest item, above 0.5 s, and is paid before any request needs a provider.
2. Harness overhead per turn is between 30 and 150 ms with the SQLite memory default; the largest
   part is memory recall plus the entity graph, not the session save.
3. Idle CPU over 10 s is under 0.5 s of process time (the cron daemon is off in the API; nothing
   polls), and RSS after boot is between 120 and 250 MB.

## Decision rule

The worst of the three by proportion is what gets optimised, in the same PR, only if the fix is a
deferral or a cache that changes no behaviour — never a change to what a turn does. If prediction 1
holds on `litellm`, the fix is to import it lazily on first provider use and the bench is re-run to
report the delta. If nothing costs more than 20% of its total, nothing is changed and the numbers
are published as the baseline.

## What this cannot show

The packaged desktop sidecar is a frozen build whose imports are unpacked from an archive; its cold
start differs from the venv's and is not measured here. One machine. No model call is timed.
