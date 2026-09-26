# Walls v2: the wall detector after its two fixes, on live pages — pre-registration

*2026-09-26, written and committed before any page of set B below was loaded by anyone on this branch,
and before the measurement below ran on set A. Study 25, S11, walls v2. US$ 0, no model: the product's
`PlaywrightDriver` (SSRF guard on) and `BrowserTool(situation=BrowserSituation())`, one `navigate` per
page, a fresh driver per page, headless, from WSL. Runner: `run_walls_v2.py`. The detector is frozen at
b0c3ca80 (`feat(browser): walls v2 …`), the commit before this file. The runner's `--smoke`
(example.com, in neither set) was the only page it loaded before this commit.*

## What changed since the first run (`PREREGISTRATION-situation.md`, `RESULTS-situation.md`)

The first run judged the detector **not fit**: 0 false stops on 17 ordinary pages, and 6 of 9 walls
handed over, 4 with the expected kind. It named two causes, and walls v2 closes each:

1. **Late widgets.** hCaptcha injects its frame after `domcontentloaded`, the moment every action
   returns at. Now an action that can load a document (`navigate`, `click`, `back`, a read with a
   `url`) is followed by a wait of **at most 2.0 s** for the page's `load` event before the look.
2. **Frames in shadow roots.** Turnstile draws its frame inside a shadow root the serialised DOM does
   not show. Now the driver's **frame tree** is read beside the HTML; a known provider's frame from it
   counts when drawn at least 20 × 20 px.

The task asked for a probe "that walks open shadow roots". The design probe (below) found the Turnstile
demo's root **closed**, which no page script can walk; the frame tree holds its frame anyway, so that is
what was built. Input fields inside shadow roots stay unseen (neither the HTML nor the ref stamp
reaches them).

A third change, outside the detector: `chimera solve` now ends a run that stopped at a wall as
`ending="handover"` instead of grading and retrying it. It does not touch what this bench measures.

## Why two page sets

**Set A is in-sample.** It is the first run's pages, and the fix was designed on it: the design probe
(`scratchpad`, not committed) loaded 9 of its pages — the hCaptcha, Turnstile and reCAPTCHA demos,
github.com/login, Wikipedia, BBC, g1, Mercado Livre, Hugging Face — to see when the hCaptcha frame
appears (0.18 s after `domcontentloaded`, at `load`), where Turnstile lives (a closed root; in the frame
tree at 300 × 65), and what a `load` wait costs (0.0–0.25 s on seven of nine, 3.1 s on g1). A pass on A
says the fix does what it was built to do on the pages it was built on, and nothing more.

**Set B is fresh**: pages chosen for this registration and not loaded before it. It is the
out-of-sample test, and the one the adoption decision rests on.

## Pages (fixed now)

**Set A:** the 20 pages of `run.py` and the 8 controls of `PREREGISTRATION-situation.md`, unchanged.

**Set B, ordinary (24):** chosen to include what the fix could break — heavy ad pages with many frames
(the frame tree and the wait's cost), commercial pages that load Stripe.js or invisible captchas on
their forms (a hidden provider frame in the tree), Brazilian portals and shops, and documentation.
They are `FRESH_PAGES` in `run_walls_v2.py`: rust-lang.org, go.dev (Effective Go), nodejs.org (about),
npmjs.com (express), crates.io (serde), Django docs (views), Kubernetes docs (overview), W3C WCAG 2.1,
The Guardian, CNN, UOL, Estadão, IMDb top 250, YouTube, Stripe pricing, Shopify pricing, Cloudflare
plans, HubSpot CRM, DigitalOcean pricing, Atlassian Jira, Magazine Luiza, KaBuM!, Medium, Booking.com.

**Set B, positive controls (17)** — public pages that are walls by construction:

| kind | pages |
|---|---|
| captcha, hCaptcha | 2captcha.com/demo/hcaptcha · nopecha.com/demo/hcaptcha · democaptcha.com (hCaptcha form) |
| captcha, Turnstile | 2captcha.com/demo/cloudflare-turnstile · nopecha.com/demo/turnstile |
| captcha, reCAPTCHA | 2captcha.com/demo/recaptcha-v2 · nopecha.com/demo/recaptcha · patrickhlauke.github.io/recaptcha |
| login (user and password on one page) | news.ycombinator.com/login · pypi.org/account/login · npmjs.com/login · huggingface.co/login · login.salesforce.com · id.heroku.com/login · discord.com/login · account.proton.me/login |
| payment | stripe.github.io/elements-examples |

## Ground truth, independent of the detector

- **Ordinary pages:** the first run's label, unchanged — a bot check by its title
  (`just a moment|attention required|verify you are human|access denied|are you a robot|captcha|um momento`)
  or visible text (`verify you are human`, `checking your browser`, `enable javascript and cookies to
  continue`, `press and hold`, `press & hold`, `are you a robot`, `confirm you are human`), else ordinary.
- **One addition, registered now:** a page with **no interactive element even after the wait** is
  **excluded** from both counts and reported. The first run's DataDome page (nytimes.com) had 0 elements
  and none of the phrases, so the label called it ordinary and the detector's correct stop counted as a
  false one; its results recommended this signal. It excludes rather than labels a wall because the
  label cannot tell a block page from a page that did not render, and neither is a page a run could
  browse. Set A is also reported under the first run's label alone, for comparison.
- **Controls** take the kind in the table, except a control **served a bot check** instead of its page
  (by the label above) is expected as **captcha** — what was served. The first run met this on
  gitlab.com; its verdict counted it as a miss. Set A is reported both ways.
- **Control validity**, by signals the detector does not read: a captcha control is valid when the page
  requested its provider (a host under `hcaptcha.com`, `challenges.cloudflare.com` or `recaptcha.net`,
  or a `google.com`/`gstatic.com` request with `recaptcha` in its path); a login control when its
  visible text names a password (`password|senha|passwort|mot de passe|contrase`); a payment control
  when the page requested `js.stripe.com`, Braintree or Adyen; any control served a bot check is valid.
  An **invalid control is reported and left out of the bar**, as a page that failed to load is — the
  first run's mislabelled Stripe control is why.
- **Failures:** a page that fails to load is reported, not replaced.

## Outcomes, per set

- **False handovers:** ordinary pages the module hands over, with kind and evidence.
- **Walls found with their kind:** among loaded, valid wall pages (controls, and bot checks the label
  finds among the ordinary pages).
- **Diagnostics, on the same load:** what v1 would have said (the page at `domcontentloaded`, without
  the frame tree, captured as the wait begins); which channel found each wall (HTML after the wait,
  frame tree only); excluded and invalid pages.
- **Time cost** on the ordinary pages: the wait (`settle_s`) and the module's whole overhead (the
  action's time minus the driver's navigation): median, p90, max, and how many reach the 2.0 s bound.

## Predictions

- **Set A:** hCaptcha and Turnstile demos hand over as **captcha**; the other walls as before;
  **0 false handovers**; nytimes.com, if DataDome serves it again, is **excluded**. **Fit.**
- **Set B:** **0 or 1 false handovers**; every valid login control found as **login**; captcha controls
  found as **captcha** where the widget is drawn by `load` + 2 s. The most likely misses are controls
  that draw their widget later than that (single-page apps), which v2 still cannot see on arrival.
- **Time:** median wait **≤ 0.5 s** on ordinary pages, and at least one heavy portal at the 2.0 s bound.

## Decision rule (frozen; the first run's bar, applied to each set)

- A set is **fit** if false handovers are **at most 1** of its loaded ordinary pages **and** every
  loaded, valid login and captcha wall is found **with its expected kind**.
- The module becomes a **candidate for default-on** if **set B is fit**, **set A is fit**, and the
  **M2 validity check** below passes. This run does not flip the flag: it reports the recommendation.
- If set B is not fit, the rule that misfired or the kind that was missed is named, and any further fix
  is measured on another new page set, never on these.

## M2 validity check (US$ 0), before any recommendation

The harm check (`bench/browser_situation`, 44/48 on against 45/48 off, 0 handovers) ran the v1
module. v2 changes no prompt text, and on a page with no wall it returns the same observation, so M2
still describes v2 **if v2 finds no wall on M2's site**. `bench/browser_situation/check_walls_v2.py`
replays every page the M2 ON arm requested, plus every authored page, through the v2 ON tool and the
OFF tool on one Chromium. **Valid** if there are 0 handovers, 0 differing observations and 0 errors;
the M2 `--dry-run` must also pass. Otherwise M2 no longer covers v2, and a new paid harm check is
needed before any default-on (not in this task's US$ 0.50 cap).

## What this cannot show

- Whether a model follows a handover well (M2's business), or how `chimera solve` fares end to end.
- Two-step code pages, walls behind a login, account-first sign-ins beyond the named identity hosts,
  and input fields inside shadow roots.
- Widgets drawn later than `load` + 2 s: the wait is a bound, and single-page apps can exceed it.
- Other networks, days and IPs: bot checks come and go. Set B is one draw of pages chosen by hand, not
  a sample of the web, so "0 false stops in 24" bounds the rate only loosely (a 95% upper bound near
  12%).
