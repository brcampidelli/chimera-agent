# M7 — the element list the browser hands the model: results

*2026-09-23 · US$ 0 · `PREREGISTRATION.md` committed before any page was read · the product's `PlaywrightDriver` (SSRF guard on) · `python -m bench.browser_element_list.run` reprints it (live pages move).*

## Verdict: the registered trigger fires on size; the waste is off-screen, not hidden

| | median | p90 | registered trigger |
|---|---:|---:|---|
| elements listed | **152** | **772** | median > 150 **or** p90 > 400 → **fires** (p90 by far) |
| hidden + disabled, share of the list | **1.2%** | — | > 10% → does not fire |
| **off-screen, share of the list** | **79%** | — | reported only, by the registration |
| rendered characters of the list | 4,156 | — | — |

**Pages:** 20 fixed pages, 19 read. Reddit failed: the page navigated itself mid-read.
- **Bot challenges:** Stack Overflow and nytimes.com returned Cloudflare's "Just a moment…" challenge, 0 elements, and mercadolivre.com.br a near-empty shell with 3 elements. A probe with the SSRF guard **off** returned the same, and the guard refused no host on these pages. They are bot walls, not our guard. They stay in the table as what the agent actually gets.
- **The extremes:** Wikipedia ("Large language model") lists **2,112 elements, 60,165 characters**, 1,995 of them off-screen. MDN lists 772. docs.github.com lists 295, of which 241 are `aria-hidden` (a collapsed navigation tree).

## Against the predictions

| prediction | outcome |
|---|---|
| median over 150 | **152** — confirmed, by two elements; the p90 (772) is what carries the trigger |
| hidden + disabled under 10% | **1.2%** — confirmed; the zero-size box already drops most hidden elements, with docs.github.com (82%) the exception |

## What follows (a proposal, for its own PR — this step only measured)

The registered rule says: propose a cap or filter. The data says which one. **Off-screen elements are four in five of what the model reads**, and one long article is 60 k characters of links. The model pays for the list on every read and clicks almost none of it.

Three candidates, ranked:
1. **List the viewport first, then a count of the rest** ("… and 1,995 more below; scroll or `find`"). The model can still reach everything through `find` and scrolling.
2. A hard cap (e.g. 150), with a truncation flag, as jev-browser does. Simpler, but blind to position.
3. Dropping `aria-hidden` subtrees. It only matters on pages like docs.github.com.

**None of them is decided by this step.** Changing what the agent can see needs a task-based measurement: the same browsing tasks, list today against list after, success and tokens. The registration says as much.

## What this cannot show

- Whether any cap hurts task success.
- Pages behind a login.
- Any viewport other than the default.
- A day other than today: the live pages change.
