# blind_audit — 1035 auditor calls, US$ 0.6730

## auditor `openrouter/mistralai/mistral-small-3.2-24b-instruct`

| arm | position | items | FAIL (majority) | Wilson 95% | flip rate | mean tokens/item | US$ |
|---|---|---:|---:|---|---:|---:|---:|
| `shipped` | head | 23 | **5/23** | [0.10, 0.42] | 1/23 | 5,454 | 0.0286 |
| `shipped` | middle | 23 | **4/23** | [0.07, 0.37] | 2/23 | 5,463 | 0.0286 |
| `shipped` | none | 23 | **0/23** | [0.00, 0.14] | 0/23 | 5,416 | 0.0284 |
| `blind` | head | 23 | **13/23** | [0.37, 0.74] | 10/23 | 9,450 | 0.0685 |
| `blind` | middle | 23 | **19/23** | [0.63, 0.93] | 5/23 | 8,782 | 0.0609 |
| `blind` | none | 23 | **11/23** | [0.29, 0.67] | 9/23 | 9,769 | 0.0709 |
| `blind` | middle_clause | 23 | **17/23** | [0.54, 0.87] | 5/23 | 8,656 | 0.0596 |
| `shipped_dropped_only` | head | 23 | **6/23** | [0.13, 0.46] | 3/23 | 5,422 | 0.0283 |
| `shipped_dropped_only` | middle | 23 | **23/23** | [0.86, 1.00] | 0/23 | 5,454 | 0.0286 |
| `shipped_dropped_only` | none | 23 | **5/23** | [0.10, 0.42] | 0/23 | 5,385 | 0.0281 |
| `shipped_dropped_only` | middle_clause | 23 | **20/23** | [0.68, 0.95] | 0/23 | 5,445 | 0.0285 |
| `production_2call` | head | 23 | **10/23** | [0.26, 0.63] | 8/23 | 11,727 | 0.0728 |
| `production_2call` | middle | 23 | **23/23** | [0.86, 1.00] | 0/23 | 5,455 | 0.0286 |
| `production_2call` | none | 23 | **16/23** | [0.49, 0.84] | 6/23 | 12,463 | 0.0775 |
| `production_2call` | middle_clause | 23 | **20/23** | [0.68, 0.95] | 3/23 | 6,505 | 0.0352 |

- **head** paired FAIL, shipped → blind: 0.22 → 0.57 (Δ +0.35, Newcombe 95% [+0.05, +0.47]; discordant 12: blind-only 10, shipped-only 2; significant)
- **middle** paired FAIL, shipped → blind: 0.17 → 0.83 (Δ +0.65, Newcombe 95% [+0.34, +0.72]; discordant 17: blind-only 16, shipped-only 1; significant)
- **none** paired FAIL, shipped → blind: 0.00 → 0.48 (Δ +0.48, Newcombe 95% [+0.23, +0.48]; discordant 11: blind-only 11, shipped-only 0; significant)
- **head** paired FAIL, shipped → shipped_dropped_only: 0.22 → 0.26 (Δ +0.04, Newcombe 95% [-0.08, +0.11]; discordant 3: shipped_dropped_only-only 2, shipped-only 1; not significant)
- **middle** paired FAIL, shipped → shipped_dropped_only: 0.17 → 1.00 (Δ +0.83, Newcombe 95% [+0.55, +0.83]; discordant 19: shipped_dropped_only-only 19, shipped-only 0; significant)
- **none** paired FAIL, shipped → shipped_dropped_only: 0.00 → 0.22 (Δ +0.22, Newcombe 95% [+0.03, +0.22]; discordant 5: shipped_dropped_only-only 5, shipped-only 0; significant)
- discrimination `shipped` (middle FAIL − none FAIL): +0.17
- discrimination `blind` (middle FAIL − none FAIL): +0.35
- discrimination `shipped_dropped_only` (middle FAIL − none FAIL): +0.78
- discrimination `production_2call` (middle FAIL − none FAIL): +0.30
- **middle_clause** paired FAIL, dropped_only → blind: 0.87 → 0.74 (Δ -0.13, Newcombe 95% [-0.20, +0.05]; discordant 5: blind-only 1, dropped_only-only 4; not significant)

| `production_2call`, by position | items | recovered by the check's own sentence (SPOT) | recovered by the two-call audit behind a silent pass (AUDIT) | nothing recovered |
|---|---:|---:|---:|---:|
| head | 23 | 6 | 4 | 13 |
| middle | 23 | 23 | 0 | 0 |
| none | 23 | 5 | 11 | 7 |
| middle_clause | 23 | 20 | 0 | 3 |
- shipped-arm FAIL lines on middle items (all reps): {'INVENTED': 11, 'DROPPED': 10, 'CONTRADICT': 10}
- shipped_dropped_only-arm FAIL lines on middle items (all reps): {'DROPPED': 69}

### Every shipped-arm PASS on a middle item (majority), one reply each — read these

- `dependency_audits-0` · INVENTED: PASS DROPPED: PASS CONTRADICTION: PASS The summary accurately reflects the raw output without any inventions, omissions, or contradictions.
- `dependency_audits-3` · INVENTED: PASS DROPPED: PASS CONTRADICTION: PASS The summary accurately reflects the raw output.
- `dependency_audits-5` · INVENTED: PASS DROPPED: PASS CONTRADICTION: PASS The summary accurately reflects the raw output.
- `dependency_audits-7` · INVENTED: PASS DROPPED: PASS CONTRADICTION: PASS The summary accurately reflects the raw output.
- `interviews-1` · INVENTED: PASS DROPPED: PASS CONTRADICTION: PASS The summary accurately reflects the raw output without any inventions, omissions, or contradictions.
- `interviews-2` · INVENTED: PASS DROPPED: PASS CONTRADICTION: PASS The summary accurately reflects the raw output without any inventions, omissions, or contradictions.
- `interviews-3` · INVENTED: PASS DROPPED: PASS CONTRADICTION: PASS The summary accurately reflects the raw output without any inventions, omissions, or contradictions.
- `interviews-4` · INVENTED: PASS DROPPED: PASS CONTRADICTION: PASS The summary accurately reflects the raw output without any inventions, omissions, or contradictions.
- `interviews-5` · INVENTED: PASS DROPPED: PASS CONTRADICTION: PASS The summary accurately reflects the raw output without any inventions, omissions, or contradictions.
- `interviews-6` · INVENTED: PASS DROPPED: PASS CONTRADICTION: PASS The summary accurately reflects the raw output without any inventions, omissions, or contradictions.
- `interviews-7` · INVENTED: PASS DROPPED: PASS CONTRADICTION: PASS The summary accurately reflects the raw output without any inventions, omissions, or contradictions.
- `logs-0` · INVENTED: PASS DROPPED: PASS CONTRADICTION: PASS The summary accurately reflects the raw output without any inventions, omissions, or contradictions.
- `logs-1` · INVENTED: PASS DROPPED: PASS CONTRADICTION: PASS The summary accurately reflects the raw output without any inventions, omissions, or contradictions.
- `logs-6` · INVENTED: PASS DROPPED: PASS CONTRADICTION: PASS The summary accurately reflects the raw output without any inventions, omissions, or contradictions.
- `postmortems-1` · INVENTED: PASS DROPPED: PASS CONTRADICTION: PASS The summary accurately reflects the raw output without any inventions, omissions, or contradictions.
- `postmortems-4` · INVENTED: PASS DROPPED: PASS CONTRADICTION: PASS The summary accurately reflects the raw output without any inventions, omissions, or contradictions.
- `postmortems-5` · INVENTED: PASS DROPPED: PASS CONTRADICTION: PASS The summary accurately reflects the raw output without any inventions, omissions, or contradictions.
- `postmortems-6` · INVENTED: PASS DROPPED: PASS CONTRADICTION: PASS The summary accurately reflects the raw output without any inventions, omissions, or contradictions.
- `postmortems-7` · INVENTED: PASS DROPPED: PASS CONTRADICTION: PASS The summary accurately reflects the raw output without any inventions, omissions, or contradictions.

