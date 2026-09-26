# Walls v2: the wall detector after its two fixes, on live pages — results

*2026-09-26 (UTC) · US$ 0 · `PREREGISTRATION-walls-v2.md` committed before the run (73521eaf), the
detector frozen at b0c3ca80 · the product's `PlaywrightDriver` (SSRF guard on), Playwright 1.63,
headless, from WSL · rows in `results/walls_v2.jsonl`, summary in `results/walls_v2_summary.json`,
the M2 replay in `../browser_situation/results/walls_v2_check.json`, the diagnosis in
`results/walls_v2_probe.jsonl` (`probe_walls_v2_misses.py`).*

## Verdict under the registered rule: set A fit, set B not fit — not a candidate for default-on

| | set A (in-sample) | set B (fresh) | bar |
|---|---:|---:|---|
| pages / loaded | 28 / 23 | 41 / 35 | — |
| ordinary pages loaded | 14 | 13 | — |
| **false handovers** | **0** | **0** | ≤ 1 |
| excluded (no element after the wait) | 1 (nytimes, DataDome — handed over) | 3 (CNN, IMDb 403, Booking — none handed over) | — |
| valid walls / found with their kind | 6 / **6** | 15 / **9** | all login and captcha |
| v1, read on the same load | 3 / 6 | 6 / 15 | — |
| **fit** | **yes** | **no** | both |

- **M2 validity replay: passed.** 32 pages of the harm check's site (every page its ON arm requested,
  and every authored page): 0 handovers, 0 differing observations, 0 errors; the M2 `--dry-run` passed
  48 of 48. M2 still describes the module — but with set B not fit, that does not make it a candidate.
- **The flag stays off**, as the rule says, and the recommendation is **not to turn it on by default**
  yet. What would change it is below.

## The two fixes did what they were built for — in and out of sample

Each wall v2 found and v1 did not, on the same load, and the channel that found it:

| page | set | channel | note |
|---|---|---|---|
| accounts.hcaptcha.com/demo | A | HTML after the wait | the first run's miss |
| demo.turnstile.workers.dev | A | **frame tree only** | the first run's miss (v1 said login, for the form around it) |
| google.com/recaptcha/api2/demo | A | HTML after the wait | v1 caught it last time; under this run's load the frame came after DCL |
| **nopecha.com/demo/turnstile** | B | **frame tree only** | fresh: a Turnstile the HTML never showed |
| **nopecha.com/demo/recaptcha** | B | HTML after the wait | fresh |
| **patrickhlauke.github.io/recaptcha** | B | HTML after the wait | fresh |

And the frame tree did not turn hidden widgets into stops: two ordinary fresh pages held a provider
frame in the tree — Stripe's docs (its invisible hCaptcha) and Atlassian's Jira page (an invisible
reCAPTCHA Enterprise) — and neither handed over. **No false handover on 27 ordinary pages across both
sets.**

## Why set B failed: six misses, three of them the controls, not the detector

Diagnosed after the run (`probe_walls_v2_misses.py`: the same page at the look, after the wait, and 5 s
later), not a second measurement:

| page | expected | why it missed | whose defect |
|---|---|---|---|
| nopecha.com/demo/hcaptcha | captcha | the frame arrived just **after** `load`: the product looked at `load` (+0.1 s) and saw nothing; the bench's own look, milliseconds later, saw it | **detector** — `load` is not "the widget is drawn" |
| medium.com | captcha | served Cloudflare's **block page** ("Attention Required!", 2 elements), which carries none of the challenge markers | **detector** — block pages are not recognised |
| login.salesforce.com | login | the page is now **account-first**: an email field, the password on the next step | the control — a documented limit, and my table called it "user and password on one page" |
| 2captcha.com/demo/hcaptcha | captcha | **no request to hCaptcha at all**, even 5 s later: the widget is never loaded | the control — counted valid only because its marketing title contains "captcha" (below) |
| 2captcha.com/demo/recaptcha-v2 | captcha | reCAPTCHA's script loaded, **no widget frame** was ever drawn | the control — nothing to hand over on arrival |
| stripe.github.io/elements-examples | payment | now redirects to a Stripe docs page with no card field | the control (payment is not in the bar) |

Without the three control defects set B would read 9 of 12, still **not fit**: the two detector misses
stand on their own, so the verdict does not rest on the apparatus.

## Against the predictions

| prediction | outcome |
|---|---|
| set A: hCaptcha and Turnstile as captcha, 0 false handovers, nytimes excluded, **fit** | **confirmed** (6/6; nytimes excluded and handed over as DataDome) |
| set B: 0 or 1 false handovers | **confirmed** (0 of 13) |
| set B: every valid login found | **refuted** by Salesforce, which turned out account-first |
| set B: captcha misses would be widgets drawn later than `load` + 2 s | **half**: the timing miss was a widget just after `load`, well inside 2 s; the others were widgets never drawn, and a block page nobody predicted |
| median wait ≤ 0.5 s on ordinary pages, one heavy page at the bound | **confirmed**: median 0.001 s (A) and 0.43 s (B); 1 and 4 pages at the bound |

## Time, and why only the wait is reported

- **The wait, on ordinary pages:** set A median 0.001 s, p90 0.85 s, max 2.08 s; set B median 0.43 s,
  p90 2.05 s, max 2.18 s. On one wall page (gitlab.com's Cloudflare challenge) it ran **6.0 s**: the
  bound is Playwright's own timeout, and on a page running a challenge's script under load it overran.
- **The module's whole overhead is not reported as its cost.** The registered measure (the action's time
  minus the navigation) includes the bench's own extra `page_html` capture, and the run shared WSL with
  other benches — load average 20–28 on 12 cores, 11 of 69 pages timing out at 30 s, one overhead of
  45 s on a docs page whose wait was 0.0 s. A cost figure needs a quiet machine and a bench that times
  only the product.

## Defects in this bench, found in the results

- **The validity shortcut.** "A control served a bot check is valid" used the bot-check label, whose
  title pattern includes `captcha` — so every captcha demo whose title says "captcha" was valid without
  its provider request being checked. It made two 2captcha controls count as walls that never drew a
  widget. The provider-request check alone would have marked the hCaptcha one invalid (no request); the
  reCAPTCHA one requested its script and drew nothing, which no request-based check can see.
- **The overhead measure**, above.
- **Load.** Six set-B pages (crates.io, UOL, Estadão, YouTube, Shopify, DigitalOcean) and four set-A
  pages timed out and are reported as failures, not replaced, as registered. Nothing says the misses
  above are load artefacts: each was reproduced in the diagnosis.

## What would make it a candidate

Each needs a fix and then another new page set, as the registration says:

1. **A look that does not stop at `load`.** One more bounded probe on the first read-only action
   already exists (every `read` looks again); the gap is the arrival. Waiting on the provider's own
   frame appearing (bounded) would close the race without charging ordinary pages.
2. **Block pages.** Cloudflare's "Attention Required" / "Sorry, you have been blocked", Akamai's
   "Access Denied", a bare 403 — a stop with its own wording ("the site refused this browser"), since
   the person can pass it only in their own browser.
3. **Controls that are verified to show the widget on arrival**, chosen by the request check without
   the title shortcut, and login controls checked for a visible password field by a signal the
   detector does not read (a screenshot, or the page text naming the field next to it).

## What this cannot show

- Whether a model follows a handover well (M2), or `chimera solve` end to end.
- Two-step code pages, walls behind a login, account-first sign-ins, inputs in shadow roots.
- Another network or day. Set B is one hand-picked draw: 0 false stops in 13 loaded ordinary pages
  bounds the rate only loosely (a 95% upper bound of ~23% by the rule of three; ~11% over both sets'
  27).
