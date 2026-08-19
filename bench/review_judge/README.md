# review_judge — does Chimera's fusion judge tell a real finding from a plausible one?

The judge in `chimera/fusion/engine.py` reads the panel's answers on every fused turn and the
synthesiser writes from its analysis. This repository has 17 benchmark suites and none of them
measured it, so every published number about fusion rested on an untested assumption.

- **`PREREGISTRATION.md`** — the plan, committed before the first model call.
- **`RESULTS.md`** — the pilot. Negative, and published for that reason.

## Running it

```bash
# 1. the dataset (Apache-2.0, 2145 labelled comments on 200 real PRs)
python - <<'PY'
import json, urllib.request, pathlib, time
BASE = "https://datasets-server.huggingface.co/rows?dataset=Alibaba-Aone%2Faacr-bench&config=default&split=train"
rows = []
for off in range(0, 2145, 100):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(f"{BASE}&offset={off}&length=100", timeout=90) as r:
                rows += [x["row"] for x in json.load(r)["rows"]]
            break
        except Exception:
            time.sleep(3 * (attempt + 1))
p = pathlib.Path("bench/review_judge/data"); p.mkdir(parents=True, exist_ok=True)
(p / "aacr.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
print(len(rows), "rows")
PY

# 2. the diffs each comment was written about (needs `gh`; no model calls)
python bench/review_judge/run_judge.py --fetch

# 3. check the prompts without spending anything
python bench/review_judge/run_judge.py --dry-run

# 4. the pilot — 105 items, about US$ 0.40 on deepseek-r1
python bench/review_judge/run_judge.py
```

The judge is whatever `CHIMERA_FUSION_JUDGE` resolves to, because measuring a model this install
does not use would answer a question nobody asked.

## Attribution

Dataset: [`Alibaba-Aone/aacr-bench`](https://huggingface.co/datasets/Alibaba-Aone/aacr-bench),
Apache-2.0, from the [open-code-review](https://github.com/alibaba/open-code-review) project
(arXiv:2601.19494). 1505 correct and 640 incorrect review comments, labelled by senior engineers;
the incorrect ones are real false positives from frontier models, not synthetic noise.

The system prompt's cost asymmetry — *keeping a wrong comment costs seconds, removing a right one
destroys a finding* — is their idea, reimplemented here in our own words.
