# S30-56 — baseline readout

Run against the pre-change deterministic product path on 2026-10-06. No model, provider, or network was used; model spend: **US$ 0**. Machine-readable case-level output: [`BASELINE.json`](BASELINE.json). Frozen model cases: [`items.py`](items.py).

## Deterministic supersession slice

Method: for each of 45 frozen rows, seed its old statement using `MemoryManager.add`, then submit the new wording through today's `MemoryManager.remember` with no key or semantic embedder, and inspect both stored records and a downstream query. This tests whether the currently shipped no-model `remember` path links/replaces facts; it does not call the model extraction module. Rows include 23 Type I explicit wording and 22 Type II implicit/paraphrased wording.

| Measure | Result |
|---|---:|
| Cases | 45 |
| New fact saved | 45/45 |
| New fact found by downstream query | 42/45 |
| Old fact still stored | 45/45 |
| Old fact also found by downstream query | 43/45 |
| Stale old version remains answerable (the gate-relevant wrong-current metric) | 43/45 (Type I 23/23; Type II 20/22) |
| Model cost | US$ 0 |

Readout: deterministic `remember` adds a new unkeyed fact but does not supersede the previous record. The probe can retrieve both versions in 43 cases, and misses the new value in 3. These are observed counts for this hand-authored diagnostic slice, not a generalization estimate. Since all cases have an asserted later correction, returning the old record as current is classified as a wrong update: 43/45. Old records remain physically stored in 45/45 cases, but two are not returned by the probe.

## Harness check

`uv run python bench/memory_staleness/run.py --check-fake` passed over all 75 items × 2 replicas: 150 fake responses, with no model/network path. Prepared set size: 45 updates (23 Type I, 22 Type II) and 30 opinion items. The pytest fake-backend test also exercises the exact harness fixture. The real-model harness is intentionally not enabled/run in this task.

## Ship status

Supersession and semantic-near-fact lookup remain **OFF** by default. The model portion and the 48-item extraction safety gate were not run; see preregistration. This baseline alone does not meet the ship rule.

## Commands still owed

Model evaluation after separate authorization (not run): `uv run python bench/memory_staleness/run.py --model --backend qwen3:4b --replicas 2 --confirm-model-run --out bench/memory_staleness/results/model.json`. This invokes the real provider gateway and is intentionally gated by the explicit confirmation flag.

Existing 48-item deterministic positive-control only if its no-model behavior is independently verified: `uv run python bench/memory_extraction/run.py --check`. It was not run during this task. The strict ship check still requires a model-backed Type I comparison plus **zero wrong updates** over those 48 existing cases; this task did not establish that gate.
