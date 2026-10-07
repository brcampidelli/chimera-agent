# S30-60 — verbatim survival of user constraints in hierarchy decomposition: results

Run 2026-10-06 on the shipped `HierarchicalOrchestrator.decompose()` with
`ollama_chat/qwen3:4b`, one pass over the 30 registered tasks (60 constraint sentences), US$ 0.
Before the run, a smoke call through the same gateway returned a readable answer. A direct
`format=json` call to Ollama returns an empty `response` from qwen3; `ollama_chat` through
LiteLLM does not. Raw decomposer responses and parsed specs are in `results/run.json`.

## Registered readout

**0 of 60 constraint sentences survived verbatim** (survival rate 0.0). All 30 decompositions
succeeded (status `ok`, one or two specs each), so no constraint was lost to a failed parse.

## Unregistered readings, for context only

These do not replace the registered number. They show what the zero is made of.

| reading | survived |
|---|---:|
| registered: case- and punctuation-sensitive substring | **0/60** |
| case- and punctuation-insensitive substring | 4/60 |
| at least half of the sentence's content words appear in some spec (paraphrase proxy) | 49/60 |

So most constraints reach a worker as a **paraphrase**, a few nearly verbatim but with
punctuation changed ("Do not remove any existing examples;" for "...examples."), and about one
in six not at all. Example: task 1 asked not to change the public API and to leave migration
files untouched. Neither appears in either spec, whose boundaries speak only of session storage.

## Decision

The registered rule is: survival below 1.0 means a deterministic pass-through of the original
constraint sentences ships **enabled by default** in a subsequent change. The rule triggers on
the registered reading and on every unregistered reading above. The pass-through is a separate
change, as registered.

## What this run cannot show

- Whether workers would have obeyed a paraphrase, since no sub-task was executed.
- Any model other than local qwen3:4b, or any task outside these 60 registered sentences.
- A 1.0 on a future run would not establish general reliability either. The registration says so.
