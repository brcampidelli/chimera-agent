# M7 step 2 — the browser's element list, today against viewport-first, on tasks. Pre-registration

*2026-09-24, written before any paid solve. Study 24, item M7 (`bench/PLAN-study24-jev-practice.md`); step 1 is
`bench/browser_element_list` (#559, US$ 0). Approved by the owner; cap US$ 8.*

## Why

**Step 1 measured the list, not its effect.** On 19 live pages, the median list was 152 elements (p90 772), and
**79%** of listed elements were off-screen. One Wikipedia article handed the model 2,112 elements, 60,165 characters,
on every read.

**The proposal it ranked first:** list the viewport, then count the rest ("… and 1,995 more below; scroll or `find`"),
keeping everything reachable. Step 1's RESULTS say no change is decided until a task-based measurement: the same
browsing tasks, list today against list after, success and tokens. This is that measurement.

## The change under test

`BrowserTool(viewport_first=True)` / `CHIMERA_BROWSER_VIEWPORT_FIRST=true` (this PR).

- **Off by default.** Off, the listing, `find` and the schema shown to the model are byte-identical to today.
  A test pins the exact bytes, and the schema is the class's own object.
- **On, the arm is three changes together:**
  - the listing shows the elements in the viewport, then one line with the count of the rest (below / above / beside)
    and how to reach them;
  - `find` also lists every element whose name matches, by ref, off-screen ones included, with its place;
  - a new `scroll` action (down/up, 85% of a screen) lists the new viewport.
- **The driver places each element** (`Element.where`, from its box against the viewport at snapshot time).
  An element the driver cannot place is listed, never counted away.
- **Tests:** `tests/test_the_browser_lists_the_viewport_first.py`, 20 of them. The last one drives real Chromium
  and skips where it cannot launch.
- **Sabotage-verified.** Each of these turns a test red:
  - listing everything with the flag on (5 red);
  - dropping the element matches from `find` (2 red);
  - rendering viewport-first by default (1 red);
  - reporting every element in view from the page script (1 red).

  Restored byte-exact each time.

## Design

**Pages: eight, authored, not saved.** Step 1 kept only its numbers, so no page from it exists on disk. The pages are
written by `build_site.py`, deterministically, one per kind step 1 measured:

| page | elements | off-screen | list, today | list, viewport-first |
|---|---:|---:|---:|---:|
| encyclopedic article (`wiki`) | 2,038 | 1,943 | 64,768 ch | 2,730 ch |
| library docs with a module sidebar (`docs`) | 695 | 620 | 23,456 ch | 2,599 ch |
| news front page (`news`) | 283 | 235 | 15,334 ch | 2,386 ch |
| package index page (`pkg`) | 153 | 102 | 4,314 ch | 1,793 ch |
| forum thread (`forum`) | 247 | 196 | 5,614 ch | 1,853 ch |
| shop listing, search, product (`shop`) | 177 | 121 | 4,866 ch | 1,660 ch |
| code repository page (`repo`) | 217 | 136 | 5,812 ch | 2,382 ch |
| public-service portal with a form (`gov`) | 161 | 126 | 5,928 ch | 1,458 ch |

Median 232 elements and **79%** off-screen, against step 1's 152 and 79%. `pages/MANIFEST.json` pins their bytes, and
the dry-run refuses to run on a site that differs.

- **The server** (`server.py`) is local and serves the files as they are. It also answers `/go/<site>/<slug>` with a
  page that shows that slug's own code.
- **Every link therefore leads somewhere.** A wrong click lands on a plausible page with a wrong code, not on an error
  that would tell the model it was wrong.
- **One server and one Chromium per solve.** The browser reaches that origin and nothing else: the driver's guard
  allows only it, and the tool's `check_url` answers anything else as an offline machine would.
- **No solve can touch the live web.**

**Tasks: 24, three per page** (`tasks.py`):

| id | page | target | place | hurt-prone | checker |
|---|---|---|---|---|---|
| W1 | wiki | a link in the opening paragraph | in view | | code |
| W2 | wiki | a "See also" link, by name | below | | code |
| W3 | wiki | the **third** book under "Further reading" | below | **yes** | code |
| D1 | docs | "Changelog" in the top navigation | in view | | code |
| D2 | docs | the default timeout of `Widget.fetch()` (read) | below | | number |
| D3 | docs | a module deep in the sidebar, by name | below | | code |
| N1 | news | the top story | in view | | code |
| N2 | news | the **third** headline of the Science section | below | **yes** | code |
| N3 | news | "Corrections" in the footer | below | | code |
| P1 | pkg | "Issues" under Project links | in view | | code |
| P2 | pkg | release 3.8.2 in the history | below | | code |
| P3 | pkg | first 12 characters of the wheel's SHA256 (read) | below | | substring |
| F1 | forum | the asker's profile | in view | | code |
| F2 | forum | the **"reply" of marlow_k's comment** (32 identical "reply" links) | below | **yes** | code |
| F3 | forum | "More comments" | below | | code |
| S1 | shop | search "brass", name the cheapest result (form) | in view | | phrase |
| S2 | shop | "Page 3" of the listing | below | | code |
| S3 | shop | **"Add to cart" for one product** (48 identical buttons) | below | **yes** | code |
| R1 | repo | the Issues tab | in view | | code |
| R2 | repo | `CONTRIBUTING.md` in the file list | below | | code |
| R3 | repo | the discussion forum link in the README | below | | code |
| G1 | gov | "Renew your passport" | in view | | code |
| G2 | gov | the **contact form at the bottom**, filled and sent | below | **yes** | code |
| G3 | gov | "Ombudsman" in the footer | below | | code |

- **Split:** 8 in view, 16 below the fold.
- **Hurt-prone, registered now:** 5 tasks where the target is below AND the goal gives no name `find` could match
  directly. They reach it by position, among identically named elements, or through fields known only by their
  placeholder.
- **Why these were included:** these are where viewport-first could plausibly make things worse, and they are in the
  suite on purpose.

**Checkers** are deterministic and read nothing but the final answer:
- **`code`:** the set of codes in the answer must be exactly {expected}. The right code next to a wrong one fails, so
  listing every code seen does not pay.
- **`number`:** the set of numbers must be exactly {37.5}.
- **`phrase`:** the cheapest product's name appears and no other result's name does.
- **`substring`:** the hash prefix appears and no decoy prefix does.

**Arms:** `today` (`viewport_first=False`) and `viewport` (`viewport_first=True`).
- **Nothing else differs:** same pages, same prompt, same model, same agent.
- **Set only by the constructor.** The environment variable is cleared in each solve.

**Agent** (`solve_one.py`):
- the product's `Agent` with **one tool, the browser**, wrapped only to count calls and characters;
- the default system prompt and temperature (0.2);
- `max_steps=20`, and a per-solve ceiling of US$ 0.25 at the receipt's price;
- its own process, its own fresh `CHIMERA_HOME`;
- the code's defaults, not this machine's: every `CHIMERA_*` variable except credential pools is removed from the
  solve's environment, and the record lists the names removed and the agent config in force;
- the prompt is the goal, plus: "Use the browser tool. Reply with the answer only."

**Model, named now:** `openrouter/deepseek/deepseek-v4-flash-0731`.
- It is the product's default, tools-capable, and the catalogue lists it at 0.04/0.08 per M (also seen at 0.065/0.18).
- The provider route of every step is recorded.

**Replicas:** k = 5, so **24 × 2 × 5 = 240 solves**, in an order shuffled once (seed 20260924) so the arms
interleave in time.

**Ruler:**
- this PR's commit, run from this worktree;
- each record carries the commit, the `chimera` package path it imported, and hashes of the manifest, `tasks.py` and
  `browser.py`.

## The dry-run (US$ 0, passed before this was committed)

`python -m bench.browser_viewport_tasks.run --dry-run` — every check passed; the output is `results/dry_run.json`:
- **Suite:** 24 tasks. Each checker accepts three right answers and rejects three wrong ones: a decoy, the right one
  beside a wrong one, and a non-answer.
- **Site:** the generator rebuilds the pinned bytes, and the files on disk match.
- **Server:** every page is served, a `/go/` page shows its code, and a missing path returns 404.
- **Codes:** the page's JavaScript and the Python checker derive the same code for five keys, compared in Chromium.
- **Placement, measured on the real browser:** every target sits where its row says. A below-fold target named once
  is nowhere in view. N2's anchor, "Science", also names a header link that is in view; the section itself is below.
- **Reachability, both arms:** a scripted solution reaches every task's answer through the product's tool in each
  arm, by name, through `find`, by scrolling, and by typing. The checker accepts all 48.

  Viewport-first makes nothing unreachable.
- **The paid path without a model:** `solve_one.solve` runs end to end with a scripted stand-in backend, once per arm.
  The arm's schema reaches the backend (`scroll` offered only viewport-first), and the receipt is zero.
- **Its guards are not inert.** Loosening the code checker, or registering W2 as in view, turns the dry-run red.

**One thing the dry-run found.** Viewport-first, the scripted walk to "the third headline of Science" first took a
"Most read" link.
- **Why:** that link sits in view in the right-hand column and comes after the section in document order.
- **The fix:** the script now counts only the unbroken run of refs after the anchor.
- **Why it stays in the notes:** a model can make the same mistake. It is one of the things the hurt-prone stratum is
  there to catch.

## Cost, projected before any solve

The scripted paths give the prompt a model would carry. The inputs:
- system prompt, the arm's schema, the task, and each observation, at 4 characters a token;
- one model call per tool call plus one to answer;
- the model's dearest seen rate, 0.065/0.18.

| | today | viewport-first |
|---|---:|---:|
| scripted prompt tokens per solve, mean | 17,148 | 9,122 |
| scripted prompt tokens per solve, max | 69,626 (W3) | 28,888 (N2) |
| **projected US$ per solve (×3 for a model's detours)** | 0.0035 | 0.0020 |

**Projected total: US$ 0.65 for 240 solves.** The cap of US$ 8 would bind only if the projection were off by 12×.

## Stop rules (`run.py`)

- **The cap is hard.**
  - Each receipt is priced at the catalogue row, because the solve's home has no price index. The route has been
    seen 2.25× dearer.
  - Each attempt is therefore charged at the larger of its receipt and its tokens at the dearest seen rate (`cap_usd`).
  - Each solve in flight is reserved at its ceiling × 2.25 = US$ 0.5625.
  - A solve is submitted only if spent + reserves + one more stays within US$ 8. A relaunch re-reads every earlier
    attempt's charge from `results/driver.jsonl`.
- **An error is the apparatus or the provider breaking:** an exception, a missing record, no receipt, no price.
  - A wrong answer is an outcome, never an error.
  - An errored cell is resubmitted once. A cell that errors twice is frozen as **missing** and never run again.
  - Two attempts per cell in total, across relaunches.
- **More than 5% of finished cells missing, after at least 20, halts the run** for the apparatus.
- **No cell is re-run to change its outcome**, and no task is dropped after solves begin.

## Outcomes (`read.py`, written with this file)

The unit is the **task**. A task's success in an arm is the mean over its replicas. Missing cells drop out of that
mean, and a task missing an arm drops out of the pairing; both are reported. CIs are percentile bootstraps over tasks
(10,000 resamples, seed 20260924).

- **Primary:** mean over tasks of (success viewport − success today), with its 95% CI.
- **Secondary:**
  - **prompt tokens per solve**, viewport/today, as a ratio of means over tasks: what the spend follows. The mean of
    per-task ratios is printed beside it and decides nothing.
  - **uncached prompt tokens** (prompt − cache reads), the same ratio. The receipt prices every prompt token at the
    full rate, so a saving that lives only in cached prefix would otherwise read as money it is not.
  - **steps per solve**, the difference;
  - receipt US$ per solve, reported only;
  - by stratum: in view, below, hurt-prone.

## Power, stated before the numbers

- **The floor is unknown for this suite.** Assume the per-task paired difference has SD 0.20: most tasks tie near
  1.0, and a few differ by a replica or two.
- **What that gives:** with 24 tasks, the SE of the mean difference is 0.041 and the 95% CI half-width about 0.08.
- **Where the rule can adopt:** the lower bound clears the −0.10 margin only when the point estimate is about −0.02 or
  better. A true loss of 0.05 will usually read as "stays opt-in".
- **What reads as a tie:** an effect under about 0.08 in either direction. That is the price of 24 tasks.
- **Strata:** with 5 or 8 tasks, they are descriptive. The stratum floor below is a point-estimate guard, not a test.
- **Tokens are different.** The scripted paths put the ratio of means near 0.53, so a real saving of that order is
  far outside the noise.

## Predictions

- **Tokens:** viewport-first cuts prompt tokens per solve to **0.4–0.7** of today's, ratio of means. The saving is
  almost all on `wiki`, `docs` and `news`.
  - The mean of per-task ratios stays near **0.8–1.0**.
  - On the three positional hurt-prone tasks (N2, F2, S3), viewport-first costs **more**. The scripted paths needed
    about 2–2.8× the tokens there, scrolling.
- **Success, overall:** within −0.05 to +0.02, with a CI that includes zero.
- **Success, by stratum:**
  - in view: equal, both near ceiling;
  - below, not hurt-prone: within −0.05;
  - hurt-prone: **−0.10 to −0.30**.
- **Steps:** viewport-first takes **+0.5 to +2** more per solve.
- **Today's arm solves ≥ 80%** of tasks. Otherwise the suite is too hard to show a difference in either direction.

## Decision rule

**Adopt as the default** (in its own PR) only if all hold:
1. the success difference's 95% CI lower bound is **≥ −0.10**;
2. its point estimate is **≥ −0.05**;
3. the prompt-token ratio's 95% CI upper bound is **< 1.0**;
4. the uncached-token ratio is **< 1.0**;
5. **no stratum** (in view, below, hurt-prone) loses more than **0.20** at the point estimate. An average must not
   hide a class of task the variant breaks (Bee §2y).

**Otherwise it stays opt-in.**

- **A loss:** if the success CI lies wholly below zero, it is recorded in the setting's comment and the docstring.
  Removing the flag is the owner's call.
- **Zero deltas cannot adopt.** Identical arms fail criterion 3. `read.py` was checked on synthetic records:
  - null deltas → opt-in;
  - half the tokens at equal success → adopt;
  - half the tokens at −0.4 success → opt-in, loss.

## What this cannot show

- **Real sites.** The pages are authored to step 1's shapes: their sizes, their off-screen share, their sidebars,
  footers and repeated names. Real pages have cookie walls, lazy loading, infinite scroll, sticky overlays and
  layouts no generator imitates.
- **Which of the three changes did it.** The arm bundles a shorter listing, `find` answering with elements, and
  `scroll`. A win or a loss belongs to the bundle.
- **Other models.** One model, at temperature 0.2, on a hosted route that does not reproduce exactly.
- **Other viewports.** Only 1280×720; a phone-sized viewport would list less and hide more.
- **Governance.** The bench runs without taint or cards. Under `--taint`, `scroll` asks for a card like `click` does,
  since it is not in the M8 read exemption: a page can load on scroll. That cost is unmeasured.
- **Agents with other tools.** Here the browser is the only tool. The product's agent also has `scrape` and `http_get`,
  which could route around the list either way.
- **A bigger `find`.** It caps element matches at 40. That binds once here: 48 "Add to cart" buttons, where S3's is
  the 37th and so inside the cap. It would bind on a page with more identical names.
- **Long `read_text`.** It still truncates at 20,000 characters in both arms, which is not what this measures.
- **Other languages.** English pages only.
