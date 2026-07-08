# Morning brief — parallel research through the hierarchy

Topics in, one synthesized digest out. This is the validated sweet spot for
hierarchical orchestration (parallel, read-heavy research): each topic runs as
an independent **mid-tier worker** under a token budget and a delegation
contract; the **top tier** only synthesizes the verified summaries.

```bash
# One-off, with the sample topics:
chimera brief

# Your own topics + write the digest to a file:
chimera brief --recipe my-brief.yaml --out brief.md
```

Edit `brief.yaml`: each `topics:` entry becomes one parallel worker. The recipe
IS the decomposition — no top-model decompose call is spent.

## Scheduling it

Any scheduler that can run a command works:

```bash
# Linux/macOS cron — weekday mornings at 07:00:
0 7 * * 1-5  cd /path/to/project && chimera brief --out brief.md

# Windows Task Scheduler: action = `chimera brief --out brief.md`, daily 07:00.
```

## What it costs (measured, not claimed)

Every delegation writes a receipt with the **counterfactual in the same row**
(what the same work would have cost inline on the top model). After a run:

```bash
chimera delegations
```

prints measured tokens vs the counterfactual and the net saving. If a run ever
OVERSPENDS vs inline, the summary says exactly that — the honest number is the
point. Fill in your own numbers here after a real run:

| run | topics | measured tokens | inline counterfactual | net |
|---|---|---|---|---|
| (yours) | 3 | — | — | — |

Notes:
- Workers run on the **mid tier** of your ladder (`chimera models`) — swap
  vendors freely (`chimera models set mid <slug>`).
- Free-tier models often report no token usage; those rows are flagged as
  estimated (chars/4) in the receipts, never silently mixed with measured ones.

## When the hierarchy actually engages (and when it shouldn't)

`chimera orchestrate` (and this brief) only fan out where the measured curve says
it pays — **2+ distinct sources + read intent**, the guaranteed-gain region where
token saving = (D−1)/D (50% at 2 docs, 67% at 3, 75% at 4 — see
[`bench/hierarchy_sweep`](../../bench/hierarchy_sweep/README.md)). One source, or a
write/coding task, falls back to a single agent — fanning out there only adds
overhead. The classifier detects distinct sources deterministically (files, URLs,
`doc A`/`report B`), so even a terse "compare a.md and b.md" routes correctly.

**The dollar asterisk:** those savings are *token* counts. If your provider does
prompt caching, the single-agent baseline's repeated context is billed at ~0.1×, so
the *dollar* win is smaller — and over a long chat it can invert. Chimera captures
`cache_read`/`cache_write` tokens from provider usage and ships a
[model that quantifies this](../../bench/hierarchy_sweep/cache_cost.py); run
`python bench/hierarchy_sweep/cache_cost.py` to see the token-vs-dollar gap.
