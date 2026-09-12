# judge_blind — 342 pipeline runs over 38 items, US$ 0.0865

corpus composition: {'1 right / 2 wrong': 29, '2 right / 1 wrong': 9}

| arm | runs | passed | Wilson 95% | judge names a vendor | mean tokens/run | US$ |
|---|---:|---:|---|---:|---:|---:|
| `named` | 228 | **162/228** (0.71) | [0.65, 0.77] | 0/228 | 3,621 | 0.0589 |
| `blind` | 114 | **80/114** (0.70) | [0.61, 0.78] | 0/114 | 3,393 | 0.0276 |

- **primary** per-item majority, named → blind: 0.68 → 0.71 (Δ +0.03, Newcombe 95% [-0.07, +0.10]; discordant 5: blind-only 3, named-only 2; not significant); per-item mean accuracy named 0.711, blind 0.702

### Vendor effect (named runs): accuracy when a correct answer carried this name

| name on a correct answer | runs | passed | Wilson 95% |
|---|---:|---:|---|
| `claude-opus-5` | 94 | 70 (0.74) | [0.65, 0.82] |
| `gpt-5.5` | 94 | 74 (0.79) | [0.69, 0.86] |
| `gemini-3.8-flash` | 94 | 69 (0.73) | [0.64, 0.81] |

### Position effect (named runs): accuracy when a correct answer sat at this position

| position of a correct answer | runs | passed | Wilson 95% |
|---|---:|---:|---|
| 1 | 94 | 72 (0.77) | [0.67, 0.84] |
| 2 | 94 | 71 (0.76) | [0.66, 0.83] |
| 3 | 94 | 70 (0.74) | [0.65, 0.82] |

### Position effect (blind runs): accuracy when a correct answer sat at this position

| position of a correct answer | runs | passed | Wilson 95% |
|---|---:|---:|---|
| 1 | 57 | 41 (0.72) | [0.59, 0.82] |
| 2 | 43 | 31 (0.72) | [0.57, 0.83] |
| 3 | 41 | 31 (0.76) | [0.61, 0.86] |

### Ten judge analyses that name a vendor (named runs) — read these


