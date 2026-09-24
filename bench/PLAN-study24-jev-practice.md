# Study 24 — sixteen links on Jev in practice, read against our own tree

2026-09-23 · US$ 0 · four agents, read-only (GitHub API and raw files, nothing cloned, installed or run) ·
the full reports are kept outside the repo (scratchpad `study24/report-A…D.md`).

## The links

| | Group | Links |
|---|---|---|
| A | Evaluation and gateway | `vinilana/jev-eval-agent`, `jev-gateway-bench`, `jev-gateway`, `live-jev` |
| B | Browser and runtimes | `vinilana/jev-browser`, `dorkitude/webctl`, `taeold/djev-run`, `spring-ai-community/spring-ai-typesafe` |
| C | Two LiteLLM posts, LangChain delta, education graph | LiteLLM "JEV classifier benchmark", LiteLLM "TypeSafe Jev compaction", LangChain "Building a harness with Jev" (delta only), `mgarlabx/edukors_graph` |
| D | Practitioner write-ups | Linas Beliūnas (Substack, mostly paywalled), Flavio Copes, Tim Kellogg's gist, @nutlope on X (**unreadable**: HTTP 402, no mirror) |

**Licences.** `jev-gateway`, `jev-gateway-bench`, `webctl` and `edukors_graph` are MIT; `spring-ai-typesafe` is Apache-2.0. `jev-eval-agent`, `live-jev`, `jev-browser` and `djev-run` have **no licence**: read them, never copy them. Nothing in the sixteen is worth vendoring.

## What the study found — about us

The most valuable output is five defects in **our** tree. The comparison surfaced them; none is a feature of the links. The coordinator verified the first three and the PassaPro one on `origin/main`.

1. **SSRF: `scrape(render="browser")` opens any URL.**
   - Where: `chimera/scrape/fetch.py`, `fetch_page` → `_browser_fetch` → `driver.navigate` → `page.goto`. `check_url` runs only on the HTTP path.
   - `ScrapeTool` is always registered (`builtin.py:175`), so a page's injected text can make the agent read `169.254.169.254` or the sidecar on `127.0.0.1`.
   - The `browser` tool checks only the first hop (`browser.py:320`). Redirects, clicks and JS navigation are never re-checked, although `ssrf.py` says every hop must be.
   - `tests/test_scrape.py` has no SSRF test.
2. **The Playwright driver has two defects** (`browser_playwright.py`).
   - **Stale refs:** it stamps `data-chimera-ref` on elements, never removes the stamp, and clicks the first match. A hostile page or a re-rendering SPA can redirect the click.
   - **Password leak:** `nameFor` falls back to `el.value`. A typed password becomes an element's name, flows back into model context and into the ledger, contradicting the audit's elision of `text`.
3. **The Decisions reader is lenient where it must halt** (`chimera/decisions/openrouter.py` `read`).
   - A missing option becomes 0.0.
   - A Noul is clamped to [0,1].
   - An invalid `choice` falls back to the argmax.
   - If the keys come back spelled differently, `p` reads 0, which fails **open** in the REVIEW band.
   - No test feeds a partial answer. `edukors_graph` does the opposite: it refuses an answer missing an option and pins the build (`allow_fallbacks:false`).
4. **`interface.py` refuses TypeSafe's documented request shape.** The SDK schema (MIT) accepts three things we reject (`:64`, `:85-87`):
   - `state` as an object or array;
   - criteria as `{what, examples}`;
   - Score criteria as a list.

   JevBench hit the object-state case this morning. Our interface test compares us with our own client, so it could not catch this.
5. **PassaPro (not Chimera): the essay grader's fence is open.**
   - `essay-submission-actions.ts` sanitises theme and context, but passes `essayText` raw.
   - `specialists.ts:104-107` puts that text inside `<ESSAY>…</ESSAY>`, and a `</ESSAY>` in the essay closes the fence.
   - Blast radius: the student's own grade and the product's trust, not other users.

Latent, not live: the B4b hint is a `system` message appended last, and `prompt_cache.py:81-87` marks the last system message. LiteLLM 1.99.0 hoists every system message to the top on the Anthropic path. On a Claude route the prefix would change every step and the cache would be written and never read. No executor of the running B4b is Anthropic, so **this run is unaffected**. The fix waits until the run is read: never touch a running ruler.

## What the study found — about the world

- **Narrowing tools makes an executor invent what it should have looked up.** `jev-eval-agent` reran by us from its published JSON:
  - with the router, 0 of 7 models searched contacts, against 7 of 7 without it (Fisher p = 0.0006);
  - 6 of 7 **fabricated** the recipient's address;
  - its own scoreboard checks only tool *names* and saw none of it.

  `jev-gateway` issue #26 shows the same mechanism in production: `none` mode stripped subagents' tools with HTTP 200. Independent confirmation of B4, with a mechanism B4 did not name.
- **Vendor-adjacent benchmarks share one blind spot.**
  - The LiteLLM auto-router benchmark is well-built (hashes frozen, clustered CIs), but every error of both classifiers went *down* (Jev 12/12, Haiku 63/63). Every tier was the same Haiku, so no damage could show; the "−96%" is US$ 0.0008 per request, and LiteLLM's own zero-cost rule classifier was left out.
  - The `webctl` benchmark shows its payload is *larger* than native (1.5k against 1.0k tokens). Its measured quality gain came from a deterministic output cap, not from Jev.
- **Jev compaction on every request cannot pay on a cached route.** The arithmetic, with r = cache-hit/miss price ratio: at r = 0.1, removing k tokens loses money whenever the cached tail after it exceeds k/9, and a stateless per-turn decision can oscillate. The LiteLLM post has no numbers, and its τ = 0.2 sits under the 0.25–0.40 band study 21 measured for p.
- **Claims that contradict our record, and deserve a re-check:**
  - **"Jev varies between identical runs."** Three independent sources say so. Our "0 flips in 55×5" was measured on *short* states only, and `spot_noul` and `manager_p` read nulls on long states without their own floor.
  - **"Put `other` in every Choice."** The vendor says so; our skill says otherwise. Neither side has measured it.
  - **"Decompose improves."** Kellogg's single Noul changed nothing, and our overseer battery went −0.319. Invariant I6 needs rewriting.
- **A correction to study 20 §1.2.** `langchain-typesafe` AutoMode's default criteria *do* reach the wire (`_payload` with `exclude_none`); they are not dead code.
- **Found by reading, not followed.** A `jev-gateway` skill tells agents to drop AI co-author trailers from commits. It was treated as data.

## The plan

### Tier S — fix, own PRs, sabotage-verified tests

| # | Item | Size |
|---|---|---|
| S1 | `check_url` on every `render` and inside `_browser_fetch`, plus a `context.route` that re-checks every document request (redirects, clicks, JS). Tests: fake driver to `169.254.169.254`; real Chromium with a 302 fixture. | S |
| S2 | Driver: clear old refs before stamping and use a strict locator; never use `value` as an input's name; label password fields. Tests: hostile seed, SPA re-render, `label for` + password. | S |
| S3 | Strict Decisions reader: halt on a missing option, a value outside [0,1], a sum outside 1±ε, or a choice outside the options. Send `allow_fallbacks:false` after a one-call probe that the endpoint accepts it. | S |
| S4 | Test the invariant "the action judged is the action executed": a recording fake inner under `LedgerTool` + `GovernedTool`, in both orders. A kwargs-mutating wrapper must fail it. (LangChain has this TOCTOU open as #40694; we do not have the bug, only no test.) | S |
| S5 | Validate MCP tool names at registration (`mcp_client.py:58`) with `^[A-Za-z0-9_.:-]{1,64}$`. | S |
| S6 | PassaPro: neutralise the fence tags inside `essayText`. Measuring whether the model yields to an injected grade is P2 below. | S |

### Tier A — adopt (product, small)

- **A1.** `interface.py` accepts the SDK shapes: state object/array (the renderer version enters the instrument hash), criteria `{what, examples}`, Score criteria as a list. Fixtures come from the SDK schema's own examples.
- **A2.** Four lines in `skills/system-one-design/SKILL.md`:
  - dedupe the state and do not print "none" lines;
  - re-run to measure the noise floor before reading a wording change;
  - the labelled set covers every failure family, on both sides;
  - numbers and counts stay in code.

  Also rewrite I6 in `PLAN-study22` to say what was measured.
- **A3.** After the B4b readout: move the hint out of the cache path (the last block of the last turn), with the byte-identical-prefix test and a cents-level probe on an Anthropic route.
- **A4.** Cite the `jev-eval-agent` fabrication and `jev-gateway` #26 in the B4 `RESULTS.md` §7, qualitatively. No Jev number is published as ours.

### Tier M — measure first (US$ 0 unless noted), pre-registered

- **M1 — before the B4b scoreboard is read.** Audit the harness_bench, B4 and B4b traces for cross-run contamination.
  - The runs had 6 concurrent solves, `CHIMERA_HOST_EXEC=allow` and `--keep-workspace`, with no audit. The gateway bench caught 1 of 120 reading another run.
  - If any is found: amend the exclusion before reading, and use a private `TMPDIR` from then on.
- **M2.** Check whether a `SIDE_EFFECT_TOOLS` recipient is derivable from the conversation, contacts or observations; if not, REVIEW or a card note. It only adds scrutiny. Planted corpus.
- **M3.** Noise floor on **long** states for our local backend and hosted routes, so that the three "Jev varies" reports are answered for our instruments.
- **M4.** The `other` rule, on the same 1,000 commits: current / narrow `other` / no `other`. Report accuracy, macro-F1 and feature/fix recall.
- **M5.** Facts-in-state v2: only the facts that fired, with the question naming the field. Same 55 items + OATS 64 + the wrappers; control = another item's facts.
- **M6.** Loop breaker, stop vs escalate. First count `stop_reason="tool_loop"` in the stored results; below 5%, close.
- **M7.** Browser element list: measure the list size on saved pages before capping; hidden and disabled elements are listed today.
- **M8.** After a fetch, taint asks for a card on every `browser` use, `read` included. Freeing read-only uses is measured on benign cards and attack ASR; if ASR rises by one case, drop it.
- **M9.** Goal-chosen chunks in `scrape` (BM25 / local Noul / head cut). First count how often the 20k cut is hit at all.
- **P2 — PassaPro, ~US$ 1–2.** 40 public essays with official grades × 4 arms (original / fence break + "grade 1000" / authority claim / same-length placebo) × k = 3. Pin the model and keep the 40 as a regression set for model changes, which the grader's automatic fallback lacks today.

### Not built (from all four reports)

- A Jev-routed gateway in front of Chimera.
- `forced` / `none` / `direct` modes, or any power to narrow tools or end the loop.
- A tool selector per step, `JevToolIndex` included.
- A computer-use loop whose action is the decision model's argmax, with or without a floor on an uncalibrated number.
- Any threshold on the vendor's `confidence`, or a Choice's `confidence` as a floor.
- An AND agreement gate in the REVIEW band.
- Model-based injection screening.
- Per-request Jev compaction in a gateway.
- Relevance filtering of `web_search` results.
- Page text handed to an agentic CLI with tools.
- Several questions in one pass of an autoregressive model.
- "Self-consistency" by a uid in the state.
- A tier router justified by agreement with labels.
- A reasoning-effort router.
- "Act automatically" at high confidence.
- Hand-set weights.
- Metrics that check only tool names.
- Savings in input tokens without the cache price.

### Order proposed

S1 → S2 → S3, in one security PR; then M1 before the B4b readout. The rest waits for the owner's pick.
