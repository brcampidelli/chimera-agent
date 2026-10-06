# The stale-consent probes

Study 31, G31-03: measure stale consent in durable approvals **before** building any revalidation
of the effect. Four probes against the real `ask_durably`, no model, US$ 0.

Run:

```bash
uv run python bench/stale_consent/run.py
```

Writes `results/run.json` and prints the summary. `PREREGISTRATION.md` is the contract (with its
dated amendment — the first S3 probe lied, and the amendment records that); `RESULTS.md` is the
reading. The headline: the synchronous durable path's consent-to-effect gap is one poll interval
by construction and ≤ 2.4 ms on the real clock, so no revalidation is built for it; the plan-gate
drift join is committed and waits for the first recorded plan-gated turn.
