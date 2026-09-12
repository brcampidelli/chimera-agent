# judge_blind — 360 pipeline runs over 40 items, US$ 0.1168

corpus composition: {'2 right / 1 wrong': 5, '1 right / 2 wrong': 35}

| arm | runs | passed | Wilson 95% | judge names a vendor | mean tokens/run | US$ |
|---|---:|---:|---|---:|---:|---:|
| `named` | 240 | **159/240** (0.66) | [0.60, 0.72] | 2/240 | 4,742 | 0.0836 |
| `blind` | 120 | **78/120** (0.65) | [0.56, 0.73] | 0/120 | 3,805 | 0.0332 |

- **primary** per-item majority, named → blind: 0.68 → 0.68 (Δ +0.00, Newcombe 95% [-0.09, +0.09]; discordant 6: blind-only 3, named-only 3; not significant); per-item mean accuracy named 0.662, blind 0.650

### Vendor effect (named runs): accuracy when a correct answer carried this name

| name on a correct answer | runs | passed | Wilson 95% |
|---|---:|---:|---|
| `claude-opus-5` | 90 | 60 (0.67) | [0.56, 0.76] |
| `gpt-5.5` | 90 | 63 (0.70) | [0.60, 0.78] |
| `gemini-3.8-flash` | 90 | 56 (0.62) | [0.52, 0.72] |

### Position effect (named runs): accuracy when a correct answer sat at this position

| position of a correct answer | runs | passed | Wilson 95% |
|---|---:|---:|---|
| 1 | 90 | 59 (0.66) | [0.55, 0.75] |
| 2 | 90 | 59 (0.66) | [0.55, 0.75] |
| 3 | 90 | 61 (0.68) | [0.58, 0.77] |

### Position effect (blind runs): accuracy when a correct answer sat at this position

| position of a correct answer | runs | passed | Wilson 95% |
|---|---:|---:|---|
| 1 | 41 | 31 (0.76) | [0.61, 0.86] |
| 2 | 43 | 27 (0.63) | [0.48, 0.76] |
| 3 | 51 | 31 (0.61) | [0.47, 0.73] |

### Ten judge analyses that name a vendor (named runs) — read these

- `sqa2-213` r1 o2 · passed=True · …**Consensus:** Two of the three answers (GPT-5.5 and Gemini) agree on December 20, 2000. **Contradictions:** The answers split on the date: December 20, 2000 vs. March 27, 2002. **Partial coverage:** None of the answers provide additional context or source reasoning; all are bare dates. **Unique insights:** No answer offers a distinguishing rationale or caveat. **Blind spots:**…
- `sqa2-33` r0 o0 · passed=False · …## Consensus - No consensus: the three answers disagree on the winner. - Two answers (GPT-5.5, Gemini) name Gerhard Richter; one (Claude) names Maria Lassnig. ## Contradictions - Claude vs. GPT/Gemini: Maria Lassnig vs. Gerhard Richter. - Both named artists are real Oskar Kokoschka Prize laureates, but in different years — Lassnig won in 1988, Richter in 1985 — so neither is co…

