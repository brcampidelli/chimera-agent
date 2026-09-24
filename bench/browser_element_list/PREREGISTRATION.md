# M7 — how big is the element list the browser hands the model? Pre-registration

*2026-09-23, written before any page was measured. Study 24, item M7 (`bench/PLAN-study24-jev-practice.md`).
US$ 0, no model: the product's own `PlaywrightDriver` (with #554's SSRF guard) on 20 public pages.*

## Why

`render_elements` gives the model every interactive element whose box is not zero-sized, with no cap:
- **No cap:** `render_text` stops at 20,000 characters; the element list has no limit.
- **Everything else stays in:** `visibility:hidden`, `disabled`, `aria-hidden` and off-screen elements are all listed.

The plan says measure before capping, because a cap changes what the agent can act on.

## Pages (fixed now)

python.org docs (`json`); Wikipedia ("Large language model"); the chimera-agent GitHub repo; MDN (`<input>`); Hacker News; BBC News; PyPI (`httpx`); a Stack Overflow question; python.org; react.dev/learn; FastAPI docs; gov.br; g1.globo.com; mercadolivre.com.br; GitHub Actions docs; arXiv 1706.03762; r/Python; Hugging Face models; nytimes.com; tauri.app.

- **Viewport:** each page is read once, after `domcontentloaded`, in the driver's default viewport.
- **Failures:** a page that fails to load or blocks the headless browser is reported as such, not replaced.

## Outcomes, per page and as median / p90

- **Elements listed** and the **characters** of the rendered list.
- **Among the listed:** how many are **hidden** (`visibility:hidden`, or `aria-hidden` on the element or an ancestor), **disabled**, or **off-screen** (outside the viewport box).
- **Visible text length**, for comparison with the element list.

## Decision rule (for a later change; this step only measures)

A cap or filter is proposed in its own PR if **either**:
- the median list exceeds **150 elements** or its p90 exceeds **400**;
- hidden + disabled make up more than **10%** of the listed elements on the median page.

If neither holds, M7 closes: the list is not the problem the agent's report suspected. Off-screen elements are reported but do not trigger anything, since scrolling legitimately brings them into reach.

## Predictions

- The median list is **over 150** (portals and news sites carry hundreds of links).
- Hidden + disabled stay **under 10%**: a zero-sized box already excludes most hidden elements.

## What this cannot show

- Whether a cap hurts task success. That needs tasks, not pages.
- Pages behind a login.
- Any other viewport or wait condition.
