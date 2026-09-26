# Results — S12: the research module against the plain loop

**Run 2026-09-25**, as pre-registered in `PREREGISTRATION.md` (commit `c776eaeb`) and its Amendment 1
(commit `21ac8fa1`, written after the pilot and before the main run). Raw data:
`results/run.json`. Spend: **US$ 0.171** at catalogue prices (pilot 0.004, main run 0.167), against
a cap of US$ 2.00. No provider errors, no stop rule.

## Decision under the frozen rule: uninformative, and the module is not recommended

The ceiling rule fired. The plain loop already cited only pages it had read on **66 of 72** turns
(91.7%, the rule's bar is 90%), so the bench had no room to show the module doing better, and by
the registered rule it makes **no decision about the prompt**. `research.system` stays
`unmeasured` in the registry, with this run named in its note.

The rest of the rule is read out anyway, because it is two-sided and one side is decisive:

| prediction | result |
|---|---|
| P1: A's primary between 50% and 90% | **failed**: 91.7%, the ceiling |
| P2: B beats A on the primary, p < 0.05 | **failed**: 67/72 against 66/72; 6 pairs only B, 5 only A; exact McNemar p = 1 |
| P3: B right on ≥ A's right turns − 6 | **held**: 71/72 each |
| P4: B's tokens per answer ≤ 1.5× A's | **failed**: 67,094 against 13,810, **4.9×** |

So on this model and this environment the module buys nothing measurable on citations or accuracy,
and costs about five times the tokens (4.2× the money, since B's cache-read share is higher).
`CHIMERA_RESEARCH_AGENT` stays off. The harness receipt is unaffected: it needs no measurement to be
correct, and it caught what it was built to catch in both arms (below).

## Numbers

Pooled over both strata, 36 items × k = 2, pinned `deepseek-v4-flash-0731` on DeepInfra, 12 steps.

| metric | A (plain loop) | B (module) | only A | only B | exact McNemar p |
|---|---:|---:|---:|---:|---:|
| **primary**: every cited URL read in the turn (≥ 1 cited) | 66/72 | 67/72 | 5 | 6 | 1.0 |
| accuracy (last `ANSWER:` line) | 71/72 | 71/72 | 1 | 1 | 1.0 |
| a cited page holds the claimed answer | 71/72 | 71/72 | 1 | 1 | 1.0 |
| right, and a read, cited page holds it | 71/72 | 71/72 | 1 | 1 | 1.0 |
| cites at least one URL | 72/72 | 71/72 | 1 | 0 | 1.0 |

| per arm | A | B |
|---|---:|---:|
| cited URLs | 102 | 148 |
| of them read in the turn | 95 (93.1%) | 144 (97.3%) |
| tokens per answer | 13,810 | 67,094 |
| cache-read share of those tokens | 34.2% | 66.0% |
| mean steps | 3.2 | 6.2 |
| turns that hit the 12-step ceiling | 0 | 4 |
| cost, catalogue-priced | US$ 0.032 | US$ 0.135 |
| cost per right answer | US$ 0.0005 | US$ 0.0019 |

**By stratum.** The registered 22 items: primary 41/44 in both arms, accuracy 44/44 in both.
Amendment 1's 14 "hard" items: primary A 25/28, B 26/28; accuracy 27/28 in both; tokens per answer
15,111 against 81,600 (5.4×). The hard stratum was not hard: with fetch tools and no search engine,
the model navigated Wikipedia by constructing URLs and found the obscure details as readily as the
famous ones.

**Floors** (replica 1 against replica 2 of the same item): on the primary A 4/36, B 5/36; on
accuracy 1/36 in each arm. The whole difference between the arms on the primary (one turn) is
smaller than either arm's own replica disagreement.

## What the check found (descriptive, not decided on)

Eleven cited URLs across 144 turns were marked unverified. Fetched again afterwards:
- **Ten are real pages that were not read in that turn.** Most are intermediate hops cited from
  memory (A: `Fram` twice, `Grace_Hopper` twice, `Tesla_(unit)`; B: `Neptune`, a Portuguese
  Wikipedia page, an ArchDaily article, `A214_road`). Seven of the ten do hold the claimed answer.
  "Unverified" meant "not read", exactly as the pre-registration warned, and not "invented".
- **One is a dead link:** `westminster-abbey.org/…/robert-fitzroy` (404), in arm A, on a turn that
  also gave no `ANSWER:` line. It is the only citation in the run that points at nothing.

So on this model, fabricated URLs are rare (1 of 250 cited), and the common failure the check
catches is citing a page the agent did not open this time. The receipt says precisely that.

**Did B follow its own prompt?** A count of the answers, not pre-registered: 68 of 72 B answers
have a gaps section and 59 of 72 name alternatives considered or ruled out; A's answers have
neither (0/72). The format took; it did not move any registered outcome. The two rules about dates
and present state were not exercised by stable questions, as registered.

## Surprises

1. **An empty final answer at "final", not at the step limit.** One B turn (`gjoa_builder_village`,
   replica 2) ran 7 tool calls in 6 steps and then returned empty text with no tool call, which the
   loop accepts as a finished run (`stopped_reason = "final"`). The empty-closing-reply nudge (#619)
   covers the step limit and the loop breaker, not this path. The research tool reports it honestly
   ("the research sub-agent gave no answer", and a receipt saying nothing is cited), but the loop
   would pass an empty answer from any surface. 1 of 144 turns here.
2. **`scrape` on English Wikipedia returns mostly navigation.** The tool keeps the first 20,000
   characters, and on a long article much of that is the menu and the list of language editions
   (Nikola Tesla's page reaches its first "Croatian" at character 11,461, in that list); the answer
   sits past the window on 6 of 36 gold pages. The agents found the answers anyway (71/72), by a
   route this run did not record per tool, but every `scrape` user pays for that chrome.
3. **No keyless search exists in the product.** With no `TAVILY_API_KEY` there is no search tool at
   all; both arms built URLs from the question. That the model managed 71/72 says more about the
   model knowing Wikipedia's URL scheme than about search.

## What this cannot show

- Other models, providers or days (one of each).
- A keyed search engine: with `web_search`, the plain loop might cite search results it did not open,
  which is the case the check exists for.
- The tool inside a main agent: whether a main agent relays the answer and the receipt faithfully.
- The date and present-state rules (no question needed them), injection (no adversarial page), and
  the web beyond English Wikipedia.
- The explorer's contract, which this bench does not touch.
- Whether a harder bench (one where the plain loop cites unread pages often) would separate the arms.
  The instrument could only show a large effect from a baseline below 90%, and this baseline was not
  below 90%.
