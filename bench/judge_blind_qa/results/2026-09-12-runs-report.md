# judge_blind — 341 pipeline runs over 38 items, US$ 0.6282

corpus composition: {'1 right / 2 wrong': 29, '2 right / 1 wrong': 9}

| arm | runs | passed | Wilson 95% | judge names a vendor | mean tokens/run | US$ |
|---|---:|---:|---|---:|---:|---:|
| `named` | 227 | **141/227** (0.62) | [0.56, 0.68] | 1/227 | 11,270 | 0.4394 |
| `blind` | 114 | **83/114** (0.73) | [0.64, 0.80] | 0/114 | 9,675 | 0.1888 |

- **primary** per-item majority, named → blind: 0.55 → 0.74 (Δ +0.18, Newcombe 95% [+0.03, +0.23]; discordant 9: blind-only 8, named-only 1; significant); per-item mean accuracy named 0.622, blind 0.728

### Vendor effect (named runs): accuracy when a correct answer carried this name

| name on a correct answer | runs | passed | Wilson 95% |
|---|---:|---:|---|
| `claude-opus-5` | 94 | 63 (0.67) | [0.57, 0.76] |
| `gpt-5.5` | 94 | 65 (0.69) | [0.59, 0.78] |
| `gemini-3.8-flash` | 93 | 58 (0.62) | [0.52, 0.72] |

### Position effect (named runs): accuracy when a correct answer sat at this position

| position of a correct answer | runs | passed | Wilson 95% |
|---|---:|---:|---|
| 1 | 93 | 62 (0.67) | [0.57, 0.75] |
| 2 | 94 | 61 (0.65) | [0.55, 0.74] |
| 3 | 94 | 63 (0.67) | [0.57, 0.76] |

### Position effect (blind runs): accuracy when a correct answer sat at this position

| position of a correct answer | runs | passed | Wilson 95% |
|---|---:|---:|---|
| 1 | 42 | 34 (0.81) | [0.67, 0.90] |
| 2 | 54 | 40 (0.74) | [0.61, 0.84] |
| 3 | 45 | 33 (0.73) | [0.59, 0.84] |

### Ten judge analyses that name a vendor (named runs) — read these

- `sqa-43` r1 o1 · passed=True · …Consensus: - Two of the three candidates (Claude Opus and GPT-5.5) agree on **Kylian Mbappé**. - All answers are short, direct, and unhedged, matching the requested format. Contradictions: - Answer 1 gives **Neymar Jr.**, directly conflicting with the other two answers. - The dispute is a clear binary factual disagreement: Neymar vs. Mbappé. Partial coverage: - All answers prov…

