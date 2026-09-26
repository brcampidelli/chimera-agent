# The browser situation's wall detector on live pages — pre-registration

*2026-09-25, written before any page was read with the module on. Study 25, wave 4, S11
(`bench/PLAN-study25-system-prompts.md` §7). US$ 0, no model: the product's `PlaywrightDriver` (SSRF guard
on) and `BrowserTool(situation=BrowserSituation())`, one `navigate` per page, headless.*

## Why this bench, and what the module changes in it

Step 1 of this bench (`PREREGISTRATION.md`) measured the element list the browser hands the model on 20
fixed public pages. With `CHIMERA_BROWSER_SITUATION` on, the same `navigate` can return something else: a
**handover** — the tool's reading that the page needs the person (a sign-in, a two-step code, a captcha,
a payment step) — and the loop then ends the run. With the flag off nothing changes, by construction: the
tool holds no situation and returns the list exactly as step 1 read it (a unit test pins this).

So what the module changes here is one thing per page: **list, or handover**. Every handover on an
ordinary page is a run stopped for nothing, and every missed wall is a run that goes on into a sign-in
page. This bench counts both.

## Pages (fixed now)

- **The 20 pages of step 1** (`run.py`'s `PAGES`), unchanged.
- **Positive controls** — public pages that are walls by construction:

| page | expected |
|---|---|
| `https://github.com/login` | login (a password field) |
| `https://gitlab.com/users/sign_in` | login |
| `https://accounts.google.com/` | login (account-first; no password field on the first page) |
| `https://www.google.com/recaptcha/api2/demo` | captcha (reCAPTCHA checkbox) |
| `https://accounts.hcaptcha.com/demo` | captcha (hCaptcha checkbox) |
| `https://demo.turnstile.workers.dev/` | captcha (Cloudflare Turnstile) |
| `https://checkout.stripe.dev/preview` | payment |
| `https://stripe-payments-demo.appspot.com/` | payment |

A two-step code page cannot be reached without an account, so that kind is covered by the unit tests only.

## Ground truth, independent of the detector

A step-1 page counts as a **wall** only when an independent signal says the site served a bot check
instead of the page: its title matches `just a moment|attention required|verify you are human|access
denied|are you a robot|captcha|um momento`, or its visible text contains `verify you are human`,
`checking your browser`, `enable javascript and cookies to continue`, `press and hold`, `press & hold`,
`are you a robot` or `confirm you are human`. Otherwise it is **ordinary**. The detector reads
different evidence (challenge element ids, the `_cf_chl_opt` script, provider frames, stamped inputs),
so agreement is not by construction. Positive controls are labelled by the table above.

- **Failures:** a page that fails to load is reported as such, not replaced — the rule of step 1.

## Outcomes

- **False handovers:** ordinary pages the module hands over, with the kind and the evidence it gave.
- **Walls found:** among wall pages that loaded (bot checks among the 20, and every positive control),
  how many hand over, and with the expected kind.
- Per page: title, element count (step 1's metric), the handover's kind and evidence, and the label.

## Predictions

- **0 false handovers** on the ordinary pages. The ones most at risk: portals with a visible login box.
- **Every login and captcha control is found** with its kind.
- **Payment controls are uncertain:** a checkout drawn inside `checkout.stripe.com`'s own frame, or by a
  provider not on the list, carries no evidence the detector reads. A miss there is a finding about the
  list, not noise.
- The two sites that served Cloudflare's challenge in step 1 (Stack Overflow, nytimes.com), **if they
  serve it again**, hand over as captcha.

## Decision rule

The flag ships **off** whatever this shows; this decides what the report says about the detector.

- **Fit to offer behind the flag** if false handovers are **at most 1** of the ordinary pages that loaded
  **and** every loaded login and captcha control is found with its kind.
- Otherwise the rule that misfired, or the kind that was missed, is named in the results. Any fix is
  measured again on a **new** page set, never tuned on these pages.

## What this cannot show

- Whether a model follows the handover well: that is `bench/browser_situation`'s harm check.
- Two-step code pages, and any wall behind a login.
- Walls that appear after the first load (a dialog opened on a timer), and other viewports.
- A day other than today: live pages change, and bot checks come and go by IP and time.
