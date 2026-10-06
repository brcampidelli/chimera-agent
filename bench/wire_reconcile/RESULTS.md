# Wire reconciliation — synthetic results

**Run:** deterministic fake-backend harness, seed `3061`; US$0; no provider/network calls. 30 trials per fault class and 30 clean controls; each run generated five gateway exchanges. The mutation was injected into the steplog after the gateway wrote its append-only wire log.

| Outcome | Detected / trials | Rate | Two-sided 95% Wilson interval |
|---|---:|---:|---:|
| Omission | 30 / 30 | 100.0% | 88.6%–100.0% |
| Fabrication | 30 / 30 | 100.0% | 88.6%–100.0% |
| Altered copy | 30 / 30 | 100.0% | 88.6%–100.0% |
| **Pooled faults** | **90 / 90** | **100.0%** | **95.9%–100.0%** |
| Clean-control false positives | 0 / 30 | 0.0% | 0.0%–11.4% |

These results meet the expected behavior for the registered synthetic mutations but **do not meet the preregistered rule for enabling the feature**: 30 trials per category is below the required 100, and the individual-fault lower confidence bounds are below 0.90. Keep `CHIMERA_WIRE_LOG` OFF unless a separate validation satisfies every preregistered condition. These data say nothing about real providers, production frequency, shared faults or gateway bypasses.

Reproduce from the repository root:

```cmd
uv run --extra dev --extra desktop python bench/wire_reconcile/run.py
```

## Owed local-Ollama replication

A real local-Ollama replication is still owed; it was not run as part of this US$0 synthetic exercise. On a machine with Ollama running and `llama3.2` installed, run the task through the normal agent command with the opt-in enabled, then audit its artifacts. The trace file is written under the workspace's `.chimera` directory; set `CHIMERA_HOME` to that same directory so the wire file is co-located:

```cmd
set CHIMERA_WIRE_LOG=true&& set CHIMERA_HOME=.chimera&& uv run python -m chimera.cli.main agent "Reply with the word ready." --model ollama_chat/llama3.2 --workspace .&& uv run python -m chimera.cli.main audit reconcile --wire .chimera/wire.jsonl --steplog .chimera/traces.jsonl
```

Run and report this separately; do not count it as completed or mix it with the synthetic results.
