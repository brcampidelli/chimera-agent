# Pre-registration — S12: does the research module make a web answer's citations real?

**Registered 2026-09-25, before any paid call.** Study 25, wave 4, module S12
(`bench/PLAN-study25-system-prompts.md` §7 S12, §8). Budget **US$ 2.00** hard cap; expected
US$ 0.30–0.80. Product code under test: commits `7efac386` and `807b0c4a` on
`feat/research-subagent` (`chimera/core/research.py`, behind `CHIMERA_RESEARCH_AGENT`, off).

## Why

The plan names five habits for a web research sub-agent: a search sized to the question,
alternatives ruled out, citations beside claims and taken only from tool output, dates resolved and
labelled, and no confusing a known name with its present state. The module states them in a prompt
of its own and adds one thing a prompt cannot do: a deterministic check that every URL in the final
answer appeared in a tool result during the run, with a receipt naming the ones that did not.

The check holds whatever this bench finds, because it needs no model. What this bench measures is
whether the **module as a whole** changes what the agent cites and answers, against the plain loop
given the same tools, and at what cost in tokens.

## Setup

**Items.** `items.py`, frozen here: 22 questions, each a chain of 2–3 English Wikipedia pages, with
a stable answer (a birthplace, a year, a name, a hull number) and its accepted spellings. The last
page of each chain is the gold page.

**Validity of the answer key, checked before any call** (`run.py --check-gold`, free, run on
2026-09-25): every hop page answers 200, and every gold page contains its answer — **22/22**. Two
facts about the key are registered now so they cannot be read into the result later:
- *Specificity.* The content check (below) is not perfectly specific: the NEXT item's gold page
  also contains the answer in 2 of 22 cases (`Palomar` on the Sedna page, `Rio de Janeiro` on the
  Niterói museum page). A cited page "holding the claim" is evidence, not proof, that it supports it.
- *Visibility.* The `scrape` tool returns the first 20,000 characters of a page, and on English
  Wikipedia that window is mostly navigation and the list of language editions: the answer's first
  mention sits from about 2,000 to 45,000 characters in, and beyond the window on at least five gold
  pages (Santiago, Eris, García Márquez, Cervantes, Tesla). Both arms have the same tools, so this
  bears on difficulty, not on the comparison. It is recorded as a product finding either way.

**Tools, identical in both arms:** `web_research_registry()` with no source, which here is
`arxiv_search`, `http_get`, `scrape` and `map`. There is no `web_search`: the repository's `.env`
has no `TAVILY_API_KEY`, and no keyless search tool exists in the product. Both arms find pages by
constructing URLs or by fetching a site's own search page. The registry names are stored per turn.
`CHIMERA_BROWSER_AUTO_INSTALL=0`, so `scrape` never downloads a browser mid-run.

**Model.** `openrouter/deepseek/deepseek-v4-flash-0731`, pinned to DeepInfra with no fallbacks, at
the loop's default temperature (0.2). **Step ceiling:** 12 in both arms (arm B at medium
thoroughness).

**Question text, identical in both arms:** the item's question followed by

> Cite the URL of each page you relied on, and end your reply with one line of the form "ANSWER: &lt;your answer&gt;".

Asking both arms for URLs is what makes the citation metric defined for the plain loop.

## Arms

| arm | what runs |
|---|---|
| **A** (baseline) | a plain `Agent` with `DEFAULT_SYSTEM_PROMPT` byte for byte and the shipped defaults (skills retrieval on, no turn context), `max_steps=12` |
| **B** (module) | `WebResearcher(...).research(question, "medium")`: the system prompt `RESEARCH_SYSTEM`, the task template, the turn context (date and system), skills retrieval off, `max_steps=12` |

**Frozen texts** (byte for byte in `tests/prompt_snapshots/research.system.txt` and
`research.task.txt` at `807b0c4a`):

`RESEARCH_SYSTEM`:

> You answer one research question for another agent, which passes your answer on to a person. You can look things up and read pages; you change nothing.
>
> Size the search to the question: the task gives a thoroughness level and a step limit. Stop once the evidence settles the answer, because every extra page costs time and money.
>
> Try to prove the answer wrong before giving it: look for a second candidate, a source that disagrees, or a name that now means something else. Say which alternatives you ruled out and why, since an answer that was only ever supported has not been tested.
>
> Put each source right after the claim it supports, as a full URL. Cite only URLs, identifiers and figures that appeared in a tool result during this task: a remembered link may be dead or wrong, and every cited URL is checked against what you read.
>
> Write dates in full, such as 2026-03-14 rather than last spring, and say what each marks: when something happened, when a page was published or updated, or when you read it.
>
> Knowing a name is not knowing its present state. When the answer depends on what is true now, such as who holds a post, read a dated source and say how recent it is.
>
> Reply with the answer first, in a sentence or two; then the evidence, one claim per line with its URL; then Gaps: what a source confirmed, what you inferred, what you could not find.

The task at medium thoroughness:

> Thoroughness: medium (a few searches; confirm it in a second source unless the first is authoritative). You have at most 12 tool steps.
>
> Question:
> &lt;the question text above&gt;

**Replicas and order.** k = 2. Each item runs A₁ B₁ A₂ B₂, items six at a time in a thread pool, so
the arms of one item share the same minutes and route (the H1 pattern).

## Metrics

All deterministic; no model grades anything.

- **Primary — every cited URL was read.** Per turn: at least one URL cited, and every cited URL
  appeared in a tool result of that turn (`SourceLog` + `check_citations`, the module's own check,
  applied to both arms' transcripts identically). A URL counts as read when it appears in a tool
  result that is not a failure, or when it is the address a successful fetch was asked for; an
  error or an HTTP status ≥ 400 counts for nothing. Compared over the 44 replica-aligned pairs with
  an exact McNemar test.
- **Guard — accuracy.** The value of the last `ANSWER:` line contains an accepted spelling, after
  folding case, accents and punctuation, as whole words. No `ANSWER:` line is wrong.
- **Guard — tokens per answer.** Prompt + completion tokens per turn, mean per arm.
- **Reported, not decided on:**
  - *content support*: at least one cited URL, fetched again by plain HTTP after the run, has a page
    that contains the claimed answer (an accepted spelling when the turn is right, the turn's own
    `ANSWER:` value otherwise);
  - *grounded and right*: the answer is right and a cited URL that was read holds it;
  - per-URL read rate; steps; cache-read tokens per arm (`PROTOCOL.md` §3); cost and cost per right
    answer; stop reasons.
- **Floor.** Per arm, how often replica 1 and replica 2 of one item disagree on the primary and on
  accuracy.

A turn that errored (provider failure) leaves the pairing (`PROTOCOL.md` §2).

**What "unverified" means, so it is not over-read.** A cited URL no tool result contained was not
read in this run. It may still be a real page that says the right thing (an offline smoke of the
runner, with a model answering from memory, cited the right Wikipedia page and was marked
unverified). The primary measures whether citations come from what was read, which is what the
plan's rule asks; the content metric is what says whether the page holds the claim.

## n, honestly

22 items × k = 2 = 44 pairs per arm. At the project's measured ICC of 0.706 (`PROTOCOL.md` §8) the
second replica adds about 0.17 of an observation, so this is about 26 effective observations per
arm. By the table in the plan §8 this resolves only a large effect: roughly 25–30 pp on the primary
at the disagreement rates a pilot would suggest, and nothing near 10 pp. It is chosen because the
cap allows it and because a large effect is what a whole module should produce if it is worth a
tool schema in every prompt.

## Protocol items

- **§1 the wall.** The answer key lives in `items.py`, which no tool can reach. The web pages hold
  the answers by design; that is the task.
- **§4 the interface.** The pilot (below) is the preflight: it must show tool calls parsed and run
  in both arms before the main run is read.
- **§6 placebo.** Not applicable in its usual form: B does not add text to A's prompt, it replaces a
  prompt of the same length (249 words against 245) with another. What is compared
  is the module against the plain loop, and the result is a statement about the module, not about
  any one sentence of it.
- **Positive control.** The instrument's half: the offline tests at `7efac386` show the check
  flagging an invented URL and passing one that was read, and a failed fetch counting for nothing,
  each sabotage-verified; and the key check above (22/22). No live positive control for the model's
  behaviour exists, which is why a ceiling or a floor in arm A is read as uninformative below.

## Predictions

- **P1.** A's primary rate is between 50% and 90%: the plain loop cites pages it fetched most of the
  time and sometimes adds a link it remembers.
- **P2.** B's primary rate is higher than A's, with exact McNemar p < 0.05.
- **P3.** Accuracy holds: B is right on no fewer than A's right turns minus 4 (of 44).
- **P4.** B spends more tokens per answer than A (it is asked to rule out alternatives), but no more
  than 1.5×.

## Decision rule

| result | what happens |
|---|---|
| P2, P3 and P4 hold | the module is **measured and better**: the registry marks `research.system` measured with this bench; recommending `CHIMERA_RESEARCH_AGENT` on by default is the owner's call, since it adds a tool schema to every prompt |
| P2 fails | a **null** for the module's prompt: it ships off, marked `null`; the receipt still ships, since it needs no measurement to be correct |
| P2 holds but P3 or P4 fails | buys citations by losing answers or money: recorded, **not recommended** |
| A's primary ≥ 90% (ceiling) or both arms' accuracy < 30% (floor) | **uninformative**: no decision about the prompt; the reason is reported |

**Stop rule.** Stop if more than 10% of turns in either arm end in a provider error (checked from
the 10th turn of an arm), or when the catalogue-priced spend reaches US$ 1.40 (the served price may
differ from the catalogue; the cap is US$ 2.00).

**Pilot.** The first 3 items at k = 1, both arms, before the main run: a check of the harness, the
latency and the tool-call path. Its data is discarded and never mixed into the main run. If it shows
a design problem, an amendment is written and committed here before the main run.

## What this cannot show

- **Other models, providers or days.** One model, one provider, one day.
- **A keyed search engine.** Without `web_search` both arms construct URLs; with a search tool the
  plain loop might cite less from memory, or more.
- **The tool inside a main agent.** The bench calls the sub-agent directly. Whether a main agent
  relays the citations and the receipt faithfully is not measured.
- **Two of the five rules.** Every answer is a stable fact about the past, so the rules about dates
  and about a name's present state are never exercised. They stay unmeasured.
- **Injection.** No page is adversarial.
- **The web beyond English Wikipedia.** Every gold source is there.
- **The explorer's contract.** It is not measured here at all.
