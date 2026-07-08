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
