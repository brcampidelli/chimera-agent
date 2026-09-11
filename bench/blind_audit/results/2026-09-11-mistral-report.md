# blind_audit — 414 auditor calls, US$ 0.2858

## auditor `openrouter/mistralai/mistral-small-3.2-24b-instruct`

| arm | position | items | FAIL (majority) | Wilson 95% | flip rate | mean tokens/item | US$ |
|---|---|---:|---:|---|---:|---:|---:|
| `shipped` | head | 23 | **5/23** | [0.10, 0.42] | 1/23 | 5,454 | 0.0286 |
| `shipped` | middle | 23 | **4/23** | [0.07, 0.37] | 2/23 | 5,463 | 0.0286 |
| `shipped` | none | 23 | **0/23** | [0.00, 0.14] | 0/23 | 5,416 | 0.0284 |
| `blind` | head | 23 | **13/23** | [0.37, 0.74] | 10/23 | 9,450 | 0.0685 |
| `blind` | middle | 23 | **19/23** | [0.63, 0.93] | 5/23 | 8,782 | 0.0609 |
| `blind` | none | 23 | **11/23** | [0.29, 0.67] | 9/23 | 9,769 | 0.0709 |

- **head** paired FAIL, shipped → blind: 0.22 → 0.57 (Δ +0.35, Newcombe 95% [+0.05, +0.47]; discordant 12: blind-only 10, shipped-only 2; significant)
- **middle** paired FAIL, shipped → blind: 0.17 → 0.83 (Δ +0.65, Newcombe 95% [+0.34, +0.72]; discordant 17: blind-only 16, shipped-only 1; significant)
- **none** paired FAIL, shipped → blind: 0.00 → 0.48 (Δ +0.48, Newcombe 95% [+0.23, +0.48]; discordant 11: blind-only 11, shipped-only 0; significant)
- discrimination `shipped` (middle FAIL − none FAIL): +0.17
- discrimination `blind` (middle FAIL − none FAIL): +0.35
- shipped-arm FAIL lines on middle items (all reps): {'INVENTED': 11, 'DROPPED': 10, 'CONTRADICT': 10}

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

