# The browser situation's wall detector on live pages — results

*2026-09-26 (UTC) · US$ 0 · `PREREGISTRATION-situation.md` committed before any page was read with the module on
(29d3454f) · the product's `PlaywrightDriver` (SSRF guard on), Playwright 1.63, headless, from WSL ·
`python -m bench.browser_element_list.run_situation` reprints it (live pages move) · rows in
`results/situation.jsonl`, summary in `results/situation_summary.json`.*

## Verdict under the registered rule: not fit — no false stops on ordinary pages, but three walls missed or misnamed

| | registered bar | outcome |
|---|---|---|
| false handovers on ordinary pages | at most 1 | **1** by the registered label (nytimes.com), **0** in fact (see below) |
| every loaded login and captcha control found, with its kind | all | **no** — hCaptcha missed, gitlab.com and the Turnstile demo named with another kind |

**The flag ships off either way**, as registered. What this changes is the report: the detector is safe on
ordinary pages and incomplete on walls, and each miss below has a cause the diagnosis pins.

## The 20 pages of step 1

- **19 loaded; Reddit failed again**, as in step 1 (the page navigated itself mid-read).
- **17 ordinary pages, 0 handovers.** Among them: Wikipedia (2,112 elements), MDN (772), BBC, gov.br,
  g1, Mercado Livre, Hugging Face, docs.github.com. No login box, invisible captcha or payment script on
  any of them stopped the run.
- **Stack Overflow served Cloudflare's "Just a moment…"** again, and handed over as a captcha (a
  bot-check page). The independent label agreed.
- **nytimes.com served a DataDome challenge** this time (step 1 saw Cloudflare's). The detector handed
  over as a captcha, on a `geo.captcha-delivery.com/captcha/` frame; the page had **0 interactive elements**
  and a title of just "nytimes.com". The registered label read it as ordinary because DataDome's page
  carries none of the phrases the label looks for. **By the registered rule this is the one false
  handover; on the evidence it is a correct one** — the label, not the detector, missed the wall. Both
  readings pass the first bar.

## The positive controls

| page | expected | handover | why |
|---|---|---|---|
| github.com/login | login | **login** (a password field) | — |
| accounts.google.com | login | **login** (an identity provider's sign-in page) | account-first: no password field; the host list caught it |
| google.com/recaptcha/api2/demo | captcha | **captcha** (a reCAPTCHA checkbox) | — |
| gitlab.com/users/sign_in | login | captcha (a bot-check page) | the site served Cloudflare's challenge instead of its sign-in: a handover, of the right kind **for what was served** |
| demo.turnstile.workers.dev | captcha | login (a password field) | the demo is a login form; the Turnstile widget never appeared in the serialised DOM, even 5 s later (see diagnosis) |
| accounts.hcaptcha.com/demo | captcha | **none** | the checkbox frame is injected after `domcontentloaded` |
| checkout.stripe.dev/preview | payment | none | the page is an integrations explorer with no card field on load — **the control was mislabelled** when registered |
| stripe-payments-demo.appspot.com | payment | none | loaded with 12 elements and no card evidence; timed out on the diagnostic reload |

**Walls that led to a handover at all: 6 of 9** loaded wall pages (the eight controls and Stack Overflow;
nytimes.com is not among them, being labelled ordinary). **With the expected kind: 4 of 9.** A handover of another kind still stops the run
and names the page, so for the person the difference is the label on the line, not the stop.

## Diagnosis (after the registered run; not a second measurement)

`probe_situation_misses.py`, output in `results/situation_probe.jsonl`: the same driver, each page loaded and
scanned at once, then read and scanned again 5 s later.

- **hCaptcha:** no frame at `domcontentloaded`; after 5 s and a `read`, the `#frame=checkbox` frame is there
  and the detector returns **captcha**. So the miss is timing, and the module already covers it one action
  later: every `read`, `click`, `type` and `back` probes again. A run that meets an hCaptcha form hands over
  on its next look at the page, not on arrival.
- **Turnstile:** no frame in `page.content()` even after 5 s. Recent Turnstile renders inside a shadow root,
  which the serialised DOM does not include. The HTML scan cannot see it; the page still handed over, for its
  password field.
- **Stripe:** neither demo showed a card frame. No real payment page was reached, so **payment detection on
  live pages is unvalidated** — covered only by the unit tests' fake pages.
- **nytimes.com:** the DataDome frame is present at load and 5 s later, with 0 elements on the page.

## What follows

- **Ship behind the flag, off**, as built. The one property that matters most for leaving it on a real run —
  no false stops on ordinary pages — held on 17 of 17.
- **Two gaps, each for its own PR, measured on a new page set** (the registration forbids tuning on these):
  1. **Late widgets.** A short settle (e.g. one `networkidle` wait, bounded) before the first probe, or a
     second probe on the first read-only action, would catch hCaptcha on arrival.
  2. **Shadow DOM.** A driver-side probe that walks open shadow roots would see Turnstile and other widgets
     the serialised DOM hides; closed roots stay out of reach.
- **Payment needs a real control**: a public page that mounts a card field on load. None was found here.
- **The label is weaker than the detector** on DataDome pages; a future run should add "0 interactive
  elements" as a label signal, registered before it runs.

## What this cannot show

- Whether a model follows a handover well: `bench/browser_situation` is the harm check.
- Two-step code pages (no public one without an account); walls behind a login.
- A day other than this one, from another network: bot checks come and go by IP and time.
