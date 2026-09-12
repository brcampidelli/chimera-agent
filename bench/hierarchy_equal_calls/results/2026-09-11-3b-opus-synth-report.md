# hierarchy_equal_calls — 60 trials, backbone openrouter/meta-llama/llama-3.2-3b-instruct, synthesiser openrouter/anthropic/claude-opus-5, US$ 0.0000

| arm | calls/task (mean) | pass@1 | pass^k | flip rate | tokens/task (mean) | US$ |
|---|---:|---:|---:|---:|---:|---:|
| `hierarchy` | 3.5 | 0.63 | 0.50 | 0.30 | 3,375 | 0.0000 |
| `hierarchy_verbatim` | 3.5 | 0.67 | 0.50 | 0.40 | 2,897 | 0.0000 |

| arm | synthesis completion tokens (mean) | synthesis US$ (total) |
|---|---:|---:|
| `hierarchy` | 404 | 0.4058 |
| `hierarchy_verbatim` | 344 | 0.3360 |

## follow-up: the synthesis asked to carry the figures verbatim

```
arm                 pass@1   pass^3   flip   ICC(1)   mechanism-active
hierarchy           63.3%   50.0%    30%   +0.60    not marked
hierarchy_verbatim  66.7%   50.0%    40%   +0.44    not marked

paired on pass^3   Δ +0.0%  95% CI [-16.2%, +16.2%]  discordant hierarchy_verbatim +1 / hierarchy +1
verdict             not significant (CI includes 0); |Δ| 0.0% vs floor 40.0%: INSIDE the noise floor — a task moves this much with nothing changed
runs per task       k=3: decides — three or more runs bound the noise the comparison is read against
```

| task | `hierarchy` | `hierarchy_verbatim` |
|---|---|---|
| benchmarks | fPf | PPf |
| capacity | fff | Pff |
| contracts | PPP | PPP |
| dependencies | PPP | ffP |
| incidents | PPP | PPP |
| limits | PPP | PPP |
| policies | fPP | PPP |
| releases | fff | fPf |
| teams | PPP | PPP |
| vendors | Pff | fff |
