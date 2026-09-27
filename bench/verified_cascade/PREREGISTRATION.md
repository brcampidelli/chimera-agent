# Pre-registration — a decision-gated cascade for grounded answers ("Jev-verified cascade")

**Registered 2026-09-26, before any paid call and before any model call.** No model has seen an item of this bench, and nothing in this file was read off a model's output. The only thing that has run is `build_items.py`, which is stdlib-only and offline, and `power.py`, which is arithmetic. The spend cap was set by the owner on 2026-09-27 at **US$ 20.00**, admission stopping at US$ 18.00 (§10, Amendment 0); the first version of this file proposed US$ 5.00. This file follows `bench/PROTOCOL.md`; the rules it satisfies are cited by number, and §14 maps them.

## 0. What is being tested, and why now

**The intervention** is OpenRouter's cookbook recipe "Jev-verified cascade":
1. a cheap model drafts an answer **only from provided excerpts** (non-streaming);
2. `typesafe/jev-1.13`, through OpenRouter's Decisions API (`POST /api/alpha/decisions`), answers a **Choice** over `{supported, unsupported, declined}` about the state `{excerpts, question, answer}`;
3. routing: `supported` at confidence ≥ 0.8 → send; `unsupported` → escalate to a frontier model and verify again; `declined`, or both fail → hand off to a human.

**Their own result does not favour it.** On their 50 questions the cascade shipped 0 wrong answers at US$ 0.012, frontier-only 2 wrong at US$ 0.175, and cheap-only **0 wrong at US$ 0.004**. The cascade did not beat the cheap model alone, and they say a 2-answer difference is noise. §6 shows why: at n = 50, **no** outcome of an exact McNemar can reach p < 0.05 unless the cheap model ships at least 6 wrong answers that the cascade removes, and with a few percent of wrong answers it ships 1 or 2.

**What Chimera already has.** `chimera/fusion/cascade.py` (`CascadeBackend`, FrugalGPT-style, off by default, `CHIMERA_CASCADE=1` / `--cascade`) climbs weak → mid → fusion, and accepts a tier's answer through `default_gate`: non-empty and no refusal marker (`_REFUSAL_MARKERS`) in its first 160 characters, after a k = 2 self-consistency vote (`chimera.fusion.consistency.majority`, difflib ratio ≥ 0.85). Its only run, `bench/cascade/RESULTS.md` (2026-07-08, `chimera/eval/cascade_bench.py`, n = 12), missed its registered criterion (cascade 92% against mid-only 100%), and the one miss (`sister_age`) was a plausible wrong answer from the weak tier that the **lexical gate passed**: "a free lexical gate cannot catch a confident-wrong answer."

**So the question here** is phase 2 of that design, restricted to grounded answers: replace or augment the lexical `CheapGate` with a **decision gate** (the Choice above, asked through the configured decision backend, `chimera/decisions/factory.py::build_decider`), and give the cascade a third outcome that it lacks today: *the sources do not cover this → hand off*, instead of always climbing. The primary baseline stays **the cheap model alone**, because that is what the cookbook's cascade failed to beat.

## 1. Scope: where a verified cascade could apply in the product

**In scope:** a turn whose answer should be grounded in sources the product hands the model, and nothing else:
- a chat or Code turn with **attached documents** (`chimera/api/attachments.py`: the extracted text is fenced as data);
- answers from **recalled memory** (`chimera/memory/`);
- answers over **retrieved chunks** (`chimera/rag/`, `chimera find`; `bench/rag`) and **research results** (`chimera/tools/research.py`).

**Out of scope, explicitly: tool-using agent turns.** `bench/tool_router/RESULTS.md` (B4) measured a router in front of the agent loop and it made **every** executor worse (oracle score −0.087 / −0.194 / −0.307 on weak / strong / Sol; pass rate 74% → 30% on `gpt-6-sol`). Nothing in this bench says anything about gating tool calls, and no result here may be cited for them.

**A finding from reading the code, before any run (US$ 0).** The shipped `CascadeBackend` would never let the weak tier answer a grounded turn. `RoutingPolicy.fuse_reason` (`chimera/fusion/router.py`) returns `"length"` for any user turn of 280 characters or more (`min_chars = 280`), and `_route` then sets `start_at_mid`. Every item here carries four excerpts of about 640 words in the user turn. Run unmodified, the lexical arm would be a mid → fusion arm. §4 therefore runs the cascade's **gate code** (`default_gate`, `majority`) inside the same two-rung structure as the decision arms, and the report prints the `fuse_reason` census over all items as a mechanical check (expected: `length` on 100%).

## 2. What the decision can change, and what it cannot

- **Can change**, in a separate PR and only if a candidate passes §8: a decision gate for `CascadeBackend` on grounded turns (an opt-in flag, or a recommended default for grounded surfaces only), with the hand-off outcome.
- **Cannot change:** the agent's default model (`openrouter/openai/gpt-6-luna`, chosen by `bench/default_model`), `_DEFAULT_JUDGE` (`chimera/config.py:62`), the shipped calibration map (`chimera/decisions/maps.py`), the governance kernel, or any tool-turn routing.
- **A null changes nothing**: no flag is added and the lexical gate stays as it is (§8).

## 3. Items (ground truth by construction; the pool is fixed now, at US$ 0)

### 3.1 Source

The project's own documentation, which exists in English and in Brazilian Portuguese: the 11 files that are in both `docs/*.md` and `docs/i18n/pt/*.md`, plus `README.md` / `README.pt-BR.md`. `docs/commands.md` is left out because it has no Portuguese version. The repository is Apache-2.0, the files are offline, and nothing is downloaded. The PT files carry the `source_sha256` of the English version they translate, and `results/sources.json` records it beside each file's own sha256.

**Why not a public dataset.** SQuAD 2.0 (CC BY-SA 4.0) has human answerability labels and adversarially written unanswerable questions. It was considered and **not used**: it is English only, it is Wikipedia (the drafting models know most answers from pre-training, which turns "answered from the excerpts" into "answered from memory" without anything in the transcript saying which), and nothing was to be downloaded for this file. A later replication on it would need its license and an offline copy verified first. The docs corpus has the opposite weakness (§15): it is small, and it is about this product.

### 3.2 The builder (`build_items.py`, committed with its output)

`python bench/verified_cascade/build_items.py` (stdlib, offline; `--check` rebuilds and compares):
- cuts each file at `##`/`###` headings, and each section into chunks of about 60–220 words at blank lines, never inside a fenced block; each chunk keeps its heading as its first line;
- **language split:** an English question may only be written from a section with an even index in its file, a Portuguese one only from an odd index, so the two halves never test one passage twice through a translation;
- **gold chunks:** every chunk of the language's parity with ≥ 50 words, a heading, and a checkable token (a digit, a `--flag`, a `CHIMERA_` variable or inline code), in sha256(id) order;
- **excerpt sets:** for each gold chunk, its 4 nearest chunks of the same language by TF-IDF cosine, same file first. **ANS** shows the gold chunk and the 3 nearest, with the gold's position drawn from sha256. **NCR** ("retrieval miss") shows the 4 nearest and not the gold, and the correct behaviour is to decline.

Output, as committed:

| | EN | PT |
|---|---:|---:|
| chunks in the pool | 181 (median 122 words) | 184 (median 133) |
| gold chunks = question slots | 69, from 11 files | 75, from 11 files |
| gold position over 0/1/2/3 | 17/17/15/20, χ²(3) = 0.74 | 22/21/16/16, χ²(3) = 1.64 |

- ANS and NCR excerpt sets have the same size (4) and the same length (median 632 against 640 words), so no superficial feature separates the two families (lessons §2u).
- sha256: `excerpts.jsonl` `e620fa3c6fc3…`, `skeleton.jsonl` `e6fda693826f…`, `sources.json` `66a2cb3ff676…`.

### 3.3 What is written against the skeleton, and the rules it must pass (Amendment 0)

The question texts do not exist yet. They are written **after this commit and before any paid call**, and committed as **Amendment 0**. That amendment carries the frozen `items.jsonl`, its sha256, and the output of a `freeze` step added to `build_items.py`. No model output may be seen by the author before the freeze. Per gold chunk, the author writes:
- a **question** in the chunk's language, answerable from the gold chunk alone, not by quoting its heading;
- a **reference answer** of one to three sentences;
- **key facts**: the one to three strings the answer must contain to be correct (a number, a flag, a variable, a name), each a verbatim substring of the gold chunk.

And, separately, **NCP** ("premise") questions: 56 per language, on the topic of a file, that the documentation does **not** answer but that a model could plausibly answer from general knowledge (a default port, a supported OS, a limit that is never stated). Each comes with a one-sentence **tempting answer**, the fabrication a model is likely to produce. Their excerpts are the 4 chunks nearest to the question text.

**Checks at the freeze, mechanical; an item that fails is dropped and counted, never edited after the freeze:**
1. Every key fact is a substring of its gold chunk.
2. **No key fact appears in any NCR excerpt.** If one does, that excerpt is replaced by the next spare neighbour (`spare_neighbours` in the skeleton); if all spares fail too, the NCR item is dropped.
3. No NCP question's key terms (the tempting answer's number or name) appear in its excerpts.
4. The number check (§5.2) does **not** fire on any reference answer against its own ANS excerpts.
5. Items per family and language are printed, and ANS/NCR/NCP are compared on excerpt length and question length (§2u).

**Expected size:** 144 ANS + 144 NCR + 112 NCP ≈ **400 items**, from about **256 distinct questions**, before drops. §6 argues why this is enough and n = 50 is not. The **unit** for the tests is the item, but ANS and NCR share a question, so the decision also requires a question-clustered interval (§8).

**Authorship is part of the apparatus.** If the questions are written by a Claude session, the item author is an Anthropic model. That is why no grader (§5) and no arm is Anthropic, and the report names the author.

### 3.4 The verifier slice (V): labelled triples, no drafting model involved

A drafting model may ship few wrong answers, which leaves a verifier's recall unmeasured on the end-to-end items. So the verifier is also read on **constructed** `(excerpts, question, answer)` triples whose label is known by construction. The freeze step builds them from the authored fields:

| construction | built from | label |
|---|---|---|
| **V-gold** | ANS item, answer = reference | supported |
| **V-num** | ANS item, one digit of a numeric key fact moved (d → d+1 mod 10), and the new number is checked to be absent from the excerpts | unsupported |
| **V-offtopic** | ANS or NCR item, answer = the reference of **another** question whose gold chunk is among these excerpts: grounded, but it answers a different question (available for 123 of 144 ANS sets, 134 of 144 NCR sets) | unsupported |
| **V-extra** | ANS item, reference + one sentence taken from the reference of a question from **another file** | unsupported |
| **V-fabricated** | NCR item, answer = the true reference, but the gold chunk is not shown | unsupported |
| **V-tempt** | NCP item, answer = the tempting answer | unsupported |
| **V-decline** | every item, answer = one of 3 fixed decline sentences per language, rotated by sha256 | declined |

About **1,150 triples**. On ANS items V-decline is labelled `declined`, which is right about the answer but routes a correct question to a human. That is the over-refusal path, and it is reported as such.

## 4. Arms (all paired on the same items; frozen)

### 4.1 Calls, made once per item and shared by every arm (a replay design)

Every arm is a **routing policy over the same logged calls**, so the arms differ only in which call's output they would ship, and pairing is exact.

| call | model / backend | pinned route | price (USD per M in / out) | per item |
|---|---|---|---|---|
| draft `d1`, `d2` | `openrouter/openai/gpt-6-luna` ("luna", the default model) | OpenAI standard endpoint, fallbacks off (as `bench/default_model` pinned it) | 0.10 / 0.50 | 2 draws, sampled (luna takes no temperature) |
| frontier draft `f1` | `openrouter/openai/gpt-6-sol` ("Sol") | OpenAI, fallbacks off | 2.00 / 10.00 | only where a policy escalates, or C covers the item (§10) |
| decision, hosted | `typesafe/jev-1.13` through `chimera/decisions/openrouter.py` (`OpenRouterDecisionsBackend`) | the endpoint's single provider; the resolved build is recorded | 0.042 / 0 | on `d1`, `d2` and each `f1` |
| decision, local | `qwen3:4b` through `chimera/decisions/local.py` (`LocalLogprobBackend`, Ollama, decision-first, T = 0) | this machine; `<tag>@<quantization>` recorded | US$ 0 | on `d1`, `d2` and each `f1` |

**The drafting prompt** is shared by luna and Sol, byte for byte (sha256 pinned in the harness):
- **system:** "You answer questions using only the excerpts in the user's message. Use no other knowledge. If the excerpts answer the question, answer it concisely; every fact, number, name, flag or setting you state must appear in the excerpts. If the excerpts do not contain the answer, say that the provided excerpts do not cover it, and add no facts or guesses. Answer in the language of the question."
- **user:** `Excerpts:` then `[1]` … `[4]` with each chunk's text, then `Question: <question>`.
- **No tools, no plugins, no `:online` suffix.** Default reasoning effort (as the product runs each model), `max_tokens` 4,000, and **one re-ask on an empty answer** (`bench/jev_decisions/RESULTS.md` §10: a reasoning route spends a small budget and returns nothing). An empty answer after the re-ask is a halt. `usage: {include: true}` gives the billed cost of every call.

**The decision question**, one `Choice` (`chimera/decisions/contract.py`), key `grounded_answer`, options in this order: `supported`, `unsupported`, `declined`.
- **instructions:** "Is this answer supported by the excerpts?"
- **criteria:**
  - `supported`: "The answer addresses the question, and every fact, number and policy it states appears in the excerpts."
  - `unsupported`: "The answer states facts that are not in the excerpts, contradicts the excerpts, or answers a different question."
  - `declined`: "The answer says the excerpts do not cover the question and adds no facts."
- **state:** `json.dumps({"excerpts": [...], "question": q, "answer": a}, ensure_ascii=False)`, keys in that order, one state per call (never batched: `bench/jev_decisions/RESULTS.md` §12 B2 measured batching moving `p` by 0.275).
- The wording is the brief's paraphrase of the cookbook's criteria, not a verbatim copy. The report says so (§15).
- **Confidence** is the Choice's probability of `supported`; the **choice** is the one the backend wrote (the Decisions API) or the argmax (local).
- **No calibration map exists for this decision** on either backend (`maps.py` ships one map, for `governance.danger` on `local_logprob`), so both are read **raw**. The 0.8 threshold is the cookbook's, applied to the raw number, and it is realizable as registered.

### 4.2 The policies

| arm | policy | what it isolates |
|---|---|---|
| **A** | ship `d1` | the baseline: the cheap model alone (the cookbook's "cheap-only") |
| **B** | read `d1` with **Jev**. `supported` and p ≥ 0.8 → ship `d1`; `declined` → hand off; otherwise → `f1`, read with Jev: `supported` and p ≥ 0.8 → ship `f1`, else hand off | the cookbook's cascade |
| **D** | B with the **local** backend in place of Jev, same question, same raw 0.8 | the instrument Chimera ships by default (US$ 0, offline) |
| **L** | the **existing lexical gate** in the same two rungs: `majority([d1, d2], threshold=0.85)`; if there is a winner and `default_gate(winner)` → ship it; else `f1`, and `default_gate(f1)` → ship `f1`, else hand off | the gate, with tiers and structure held equal to B |
| **C** | ship `f1` | the frontier model alone |

- **L's terminal is a hand-off, not fusion.** The shipped cascade would call its fusion rung there. That rung is not run (its cost and quality are unmeasured here), and a hand-off is the terminal B and D use, so the gate is the only difference. L also bypasses `RoutingPolicy` (§1), which is reported.
- **Secondary policies, reported and never deciding:**
  - B and D with `declined` → **ship the decline** (a product may prefer "your sources do not cover this" to a hand-off);
  - B and D at thresholds 0.5 and 0.9, and the full curve;
  - B-cal and D-cal: a Platt map cross-fitted **by source file** (leave-one-file-out, PROTOCOL §7), threshold 0.8 on the mapped number, labelled "requires labels a deployment would have to collect";
  - an **oracle** (ship whichever of `d1`/`f1` is correct), labelled as an upper bound that no policy can realize.
- **Costs per arm** are what the policy would have paid. A = `d1`. B = `d1` + Jev(`d1`) [+ `f1` + Jev(`f1`)]. D = `d1` [+ `f1`]. L = `d1` + `d2` [+ `f1`]. C = `f1`. A hand-off is priced at US$ 0 and counted. Local calls are free, and their latency is reported.

**Why Sol is the frontier arm.** `chimera review` cannot supply it, because its measured reviewer is luna itself (`chimera/review/family.py`, `MEASURED_REVIEWERS`). The `premium` ladder's rungs are `gpt-5.5` (5.00/30.00) and `claude-opus-5` (5.00/25.00) (`chimera/providers/catalog.py`, `_PRESETS`). Sol is the catalogue's "cost-efficient high end of the GPT-6 line" (top tier, 2.00/10.00), at 40% of their input price and a third to two fifths of their output price. This repo has run it: it was the strongest of three executors in `bench/tool_router`. Anthropic is excluded twice over, because an Anthropic session may author the items (§3.3). The cost of this choice: Sol is **the same family as luna**, so the two may fail on the same items. That correlation is part of what an escalation buys or does not buy, and the report gives the overlap of `d1` and `f1` wrong answers.

## 5. Grading: what "a wrong answer shipped" is

### 5.1 Labels

Each shipped response gets one label:
- **wrong:** any specific claim (a fact, number, name, flag, behaviour) not supported by the excerpts or contradicting them, an answer to a different question, or, on NCR and NCP items, any answer that asserts facts instead of declining. **A claim that is true of the product but absent from the excerpts is wrong**: the product's promise on a grounded turn is "from your sources".
- **correct:** ANS, consistent with the reference, with every key fact present and no wrong claim; NCR and NCP, a decline that adds no facts.
- **incomplete:** ANS, no wrong claim, but a key fact is missing.
- **declined:** ANS, a decline.

The primary metric counts **wrong** only. `correct` on ANS is helpfulness.

### 5.2 Who decides

1. **Deterministic number check, first and final.** Every maximal digit run in the answer, after stripping list markers (`^\s*\d+[.)]`) and excerpt citations (`[1]`…`[4]`), must occur in the excerpts or the question; otherwise the label is **wrong**. It is self-tested at the freeze: it must fire on every V-num and on no V-gold (§3.3 check 4).
2. **Two graders, one item per call** (PROTOCOL §5), blind to arm and model, same rubric (the labels above, with the reference and key facts for ANS items), each returning one JSON label:
   - **G1** `openrouter/deepseek/deepseek-v4-flash-0731`, pinned to DeepInfra (the project's measured judge, `_DEFAULT_JUDGE`), `max_tokens` 2,000, one re-ask on empty;
   - **G2** `openrouter/mistralai/mistral-small-3.2-24b-instruct`, pinned to DeepInfra (`max_tokens` 1,000, below the 16,384 route ceiling that `catalog.py` documents).

   Neither is OpenAI (the drafters), typesafe (the verifier), Anthropic (a possible item author) or Qwen (arm D's local verifier).
3. **Agreement → that label. Disagreement → a human adjudicates**, seeing only the excerpts, question, reference and answer. The adjudicator is the owner, or a session that did not author the item. Each adjudication is counted. **If adjudications exceed 15% of graded answers, the graders are not doing the job**: stop, and amend before relaunching.
4. **Per-grader votes are stored**, and the report prints grader disagreement in the band where the arms differ (PROTOCOL §5).

### 5.3 Grader preflight (gate G, before any draft is graded)

The graders read a stratified sample of **240 V triples** (40 per construction type, both languages); V's labels map onto the grading labels (supported → correct, unsupported → wrong, declined → declined).

**Each grader must reach all three:**
- agreement ≥ 90%;
- recall on the four *wrong* constructions ≥ 85%;
- "wrong" on V-gold ≤ 5%.

A grader that fails is replaced by `openrouter/google/gemini-3.8-flash` (0.75/3.75, the fusion panel's Google seat). The replacement goes in an amendment with the cost re-estimated, before any draft is graded (the lesson of `bench/panel_correlation` and of the unpassable 087: a broken grader reads as a result). Cohen's κ between G1 and G2 is reported.

**Grader floor, both halves (PROTOCOL §5):**
- **replay:** 100 graded drafts re-graded in a later session;
- **paraphrase:** 60 re-graded with the excerpt order permuted, which preserves meaning.

## 6. How many items, and why 50 cannot show anything

**The arithmetic of the cookbook's n.** An exact two-sided McNemar with no discordant pair against the cascade (c = 0) gives p = 0.25, 0.125, 0.0625, 0.031, 0.016 at b = 3, 4, 5, 6, 7 (`power.py`). At a wrong-answer rate of 2–5%, 50 items give the cheap model 1–2.5 wrong answers to remove. **Power at n = 50 is 0.00–0.03 in every cell below.** It cannot distinguish the cascade from the cheap model even if the verifier is perfect.

**Power** (exact enumeration, `power.py`): α = 0.025, the first step of Holm over the two deciding comparisons; `p_a` = A's wrong-shipped rate, `r` = the fraction of A's wrong answers the cascade removes, `q` = 0.003 new wrong answers per item.

| p_a | r | n = 200 | n = 400 | n = 600 |
|---|---|---:|---:|---:|
| 0.03 | 0.6 | 0.04 | 0.32 | 0.58 |
| 0.05 | 0.4 | 0.07 | 0.41 | 0.69 |
| 0.05 | 0.6 | 0.28 | **0.79** | 0.95 |
| 0.08 | 0.6 | 0.72 | **0.99** | 1.00 |
| 0.05 | 0.8 | 0.54 | 0.95 | 1.00 |

**Why the item mix is enriched.** On answerable questions with the answer in view, a model like luna ships a wrong answer a few percent of the time. On retrieval misses and on premise questions, the temptation to answer from memory or from a neighbouring excerpt is the whole point. So 256 of about 400 items are NCR or NCP. My assumed mix is ANS 2–6%, NCR 8–20%, NCP 10–30%, overall about 6–12%, where n ≈ 400 has power 0.8–0.99 at r = 0.6. **The rates here are therefore not production rates.** A production stream is mostly answerable, and the report reweights per family to a stated 80/15/5 mix (ANS/NCR/NCP), labelled as a what-if.

**The power that matters is conditional on what A actually shipped.** A's wrong answers are counted before any verifier or frontier money is spent (§9, S3). Given W wrong answers from A, and c ~ Poisson(1.5):

| W | r = 0.4 | r = 0.6 | r = 0.8 |
|---:|---:|---:|---:|
| 10 | 0.01 | 0.10 | 0.32 |
| 15 | 0.12 | **0.46** | 0.81 |
| 20 | 0.34 | 0.79 | 0.97 |
| 30 | 0.76 | 0.99 | 1.00 |

That table is the base-rate gate (§9).

## 7. Metrics (`report.py`, written with the harness)

**Primary: wrong answers shipped per item.** For each candidate X ∈ {B, D, L, C}, paired against A over the items where no call that X or A needs halted:
- the paired difference X − A with a **Newcombe (method 10) 95% interval**;
- the **exact two-sided McNemar** p, Holm-adjusted within its family (§8);
- a **question-clustered bootstrap** 95% interval (10,000 resamples over questions, seed 26), since ANS and NCR share a question.

**Co-primary: cost per item** (billed, the larger of billed and computed), mean per arm, and the paired ratio X/A with a bootstrap 95% CI (resampling items). Cost per **correct answer shipped** is reported beside it.

**Both sides of the trade, per arm:**
- **handoff rate on ANS items**: questions the sources answer, routed to a human. This is the price of the gate;
- **unnecessary handoffs**: handoffs on items where `d1` was correct;
- **correct answers shipped on ANS** (helpfulness), paired against A with Newcombe;
- escalation rate; halts; the fraction of items where a policy's call was missing.

**The verifier as an instrument (Jev and local, raw and separately cross-fitted):**
- the confusion matrix of its choice against the ground-truth label of `d1` and of `f1` (correct/incomplete → supported; wrong → unsupported; declined → declined), and against V's construction labels, **per construction type and per language**;
- **acceptance rate**, `supported` at p ≥ 0.8, on all drafts, and its **rejection rate on wrong drafts**. A verifier that accepts more than 90% of what it is shown, and has a recall on wrong drafts below 50%, is reported as **not verifying** here (lessons §2k);
- AUROC of p(`supported`) for correct versus wrong, raw; Brier and ECE (10 bins) with the simulated floor; the reliability table. Raw and calibrated numbers are **never mixed in a table**;
- **the threshold actually used**: every confidence reading is reported against 0.8 and against the argmax rule, with the lowest p among accepted drafts (lessons §2af);
- local backend only: the label-token mass on the options, and first-token collisions (`_ambiguous_prefix`; the three options share no prefix, so the expected count is 0, and it is checked).

**Mechanics:**
- the drafting prompt identical across models (hash);
- tokens in, out, reasoning and cache read per model (PROTOCOL §3);
- empty answers before and after the re-ask;
- the served provider per call (anything off the pin is counted);
- the resolved Jev build;
- latency p50/p95 per call type;
- `fuse_reason` over all items (§1);
- L's majority outcome (agreed / no majority) and `default_gate` outcomes;
- the drafts that hit a refusal marker.

**Per language and per family (ANS/NCR/NCP):** every primary and secondary number, descriptive, with no separate test.

## 8. The frozen adoption rule

**Family F1, deciding:** {B − A, D − A} on wrong answers shipped, Holm over two.

**A candidate X ∈ {B, D} qualifies as an opt-in feature** (a flag, off by default, grounded turns only) when all of these hold:
1. **Fewer wrong answers:** X − A < 0 with Holm-adjusted exact McNemar **p < 0.05**, **and** the question-clustered 95% interval excludes 0.
2. **Cost:** paired mean cost per item X/A **≤ 5.0** (point estimate; CI reported). At luna's roughly US$ 0.0005 an answer that is about a quarter of a cent per grounded answer, a fraction of one Sol call.
3. **Hand-offs:** hand-offs on ANS items **≤ 15%** (point estimate; Wilson upper bound reported). Removing wrong answers by refusing a sixth of the answerable ones is not the feature.
4. **Eligible:**
   - its instrument gate passed (§9, S1);
   - halts ≤ 5% of its items;
   - at least 99% of its calls on their pinned route.

**It is recommended as the default for grounded surfaces** (in a separate PR, by the owner's rule that a flag the measurement recommends becomes the default there) when 1–4 hold **and**:
- X/A ≤ 3.0;
- ANS hand-offs ≤ 5%;
- correct answers shipped on ANS are **non-inferior** to A: the Newcombe lower bound of X − A ≥ −5 pp.

**If both B and D qualify at the same level:** the one with fewer wrong answers shipped. If B − D on wrong answers is not significant (exact McNemar p ≥ 0.05), **D**: it is US$ 0, offline, the decision instrument Chimera already ships, and it depends on no vendor build.

**Family F2, reported, never deciding** (Holm within it): L − A, B − L, D − L (the gate isolated, with tiers and structure equal), C − A, B − C (the equal-cost question: what the frontier buys at its price against what the gate buys at its price).

**What a null means.** No candidate qualifying means **nothing ships**: no flag, and the lexical gate stays as it is. The result is published in `RESULTS.md` in the same place a positive would have gone.
- A null is read against the power it had: RESULTS states W, the observed r, and the conditional power (§6) beside it.
- "Not significant" is not "no effect".
- If B ships fewer wrong answers than A at a p that misses the bar, that is reported as a direction, not as a result.

**What L can show but not decide.** If L ships as many or more wrong answers than A while costing more (its vote over two sampled free-text answers is expected to rarely agree at 0.85, so it escalates most items), that is a finding about the shipped cascade on grounded turns. It is reported to the coordinator, and this rule changes nothing about it.

## 9. Stages, gates and stop rules

Items run in sha256(item id) order, fixed at the freeze. Every stage's calls are logged with the request body (minus the key), the billed cost and the served provider.

- **S0, US$ 0:**
  - the freeze (Amendment 0);
  - the local backend (D) on all of V;
  - the number check's self-test;
  - the `fuse_reason` census;
  - the request-body check: no `tools`, no `plugins`, no `:online` in any drafting or grading request (the wall, §12).
- **S1, instrument and grader gates, about US$ 0.2:**
  - Jev on all of V; the grader preflight (gate G, §5.3); Jev replay on 100 V triples (expected: at most 1 choice flip; `bench/jev_decisions/RESULTS.md` §3 measured 0/55 at τ 0.5 and 0.8).
  - **Instrument gate, per verifier:** a verifier is dropped from the paid run, and reported, if it accepts (supported, p ≥ 0.8) **more than 90%** of the constructed unsupported triples (it cannot remove wrong answers), or **less than 50%** of V-gold (it would hand off most correct answers, and the cascade degenerates into a hand-off).
  - **If both B and D are dropped, everything stops**, and the V-slice results are published.
- **S2, pilot, the first 40 items, about US$ 0.7, outcome-blind:** every call type on these items.
  - **Gates:**
    - drafts non-empty after the re-ask in ≥ 95% of calls, per model;
    - Jev and the local backend return a readable Choice in 100% (a malformed body is a halt, `openrouter.py` fails closed; more than 2 halts stop the run);
    - local label-token mass ≥ 0.5 in ≥ 90% of readings;
    - every call on its pin.
  - The pilot measures cost per call and escalation rates for the sizing (§10). **No wrong-answer count and no label of any draft is read in the pilot**: `report.py` refuses to print them until S3 is complete. Because the gates read only mechanics and cost, the pilot's items stay in the main run.
- **S3, arm A on all items, about US$ 0.9:** `d1` and `d2` on every item, graded.
  - **Base-rate gate:** W = the number of items where A ships a wrong answer.
    - **If W < 15, stop and publish.** Luna alone ships too few wrong answers here for any verifier to show a gain: conditional power ≤ 0.46 at r = 0.6 (§6). That is the cookbook's own result, reproduced at a power that can say so.
    - **If A's wrong rate exceeds 40%**, the drafting prompt or the interface is broken: stop, and amend.
- **S4, the deciding arms:** Jev and local on `d1` and `d2`; `f1` on every item that **B or D** escalates from `d1`; each `f1` graded and read by both verifiers.
- **S5, replicates** (the floors, §11).
- **S6, the non-deciding coverage, by budget:**
  - `f1` for L's remaining escalations;
  - then C's remaining items, each in frozen order, until the admission stop (§10).
  - L and C are reported on the items they cover, with that n.

**Halts (PROTOCOL §2).** A provider or harness error is re-run once, fresh. A second failure is a halt, and the item leaves every comparison that needs that call. A rate-limit halt is re-queued once at the end of its stage (`bench/default_model` Amendment 5) and is counted apart.

**Amendments.** A design problem found at a launch stops the run. The amendment is committed here before relaunching, and data collected under the flawed design are kept apart and never pooled.

## 10. Budget

**Estimated cost per call.** The token sizes come from the skeleton: 4 excerpts of about 640 words, about 1,300 prompt tokens with the instructions, 3,000 at most. Output and reasoning tokens are **not yet measured** for either OpenAI model, and they are the main uncertainty.

| call | per call | count (n ≈ 400) | subtotal |
|---|---:|---:|---:|
| luna draft (700 out, reasoning included, assumed) | ~US$ 0.0005 | 800 (`d1`, `d2`) + 100 (`d3`) | ~0.45 |
| Sol draft (1,000 out, assumed) | ~US$ 0.013 | B ∪ D escalations, est. 80–160 | ~1.0–2.1 |
| Jev (about 1,500 tokens in, 0 out; the measured 0.000056 at 1,335 tokens) | ~US$ 0.00006 | about 2,300 (V, `d1`, `d2`, `f1`, replay) | ~0.14 |
| graders G1 + G2 | ~US$ 0.0006 | about 1,450 (preflight, drafts, floors) | ~0.85 |
| local qwen3:4b | 0 | — | 0 |
| **core: S1–S5** | | | **~2.5–3.6** |
| S6: L's extra escalations + C's remaining items | ~US$ 0.013 each | up to about 350 | up to ~4.5 |

**The cap is US$ 20.00** (the owner's decision of 2026-09-27, Amendment 0), with admission stopping at **US$ 18.00** of billed-or-computed spend. The first version of this file proposed US$ 5.00, under which L and C could only be covered from the head of the frozen order. **At US$ 20, L and C get full coverage of every item:** `f1` exists on every item, so every arm is paired on the full n (about 400) and F2 (§8) is read at the same n as F1. The estimated total with full coverage is about US$ 7–9 (core ~2.5–3.6, plus `f1`, its two verifier reads and its grading on the remaining ~300 items, ~4.5–5.5); the gap to the admission stop absorbs a 2× miss on the unmeasured output and reasoning tokens (§10's main uncertainty). If the pilot's measured costs project the full design above US$ 18, the sizing rule below applies.

**Sizing, fixed now (after S2).** With mean costs per call measured in the pilot (the larger of billed and computed), and the pilot's escalation rates e_B, e_D, e_L:
- **Steps S3–S5 are projected over all items.** If the projection plus spend-so-far exceeds US$ 18.00, `n` is cut from the tail of the frozen order until it fits.
- **If the cut leaves n < 300**, there is no main run: at the rates assumed in §6, n < 300 loses most of its power. It is reported as unaffordable at this cap, and the owner decides.
- **S6** then spends what remains, L's escalations first, then C; at the US$ 20 cap this is expected to reach every item (full coverage, Amendment 0).

## 11. Floors, replicates and controls

- **Luna's sampling floor:** `d1` and `d2` exist and are graded on every item, which gives the per-item flip rate of "wrong". `d3` on the first 100 items makes three draws there (**two alert, three decide**).
  - **The whole comparison is replicated on `d2`:** B₂, D₂ and L against A₂, where the needed `f1` exists. It is reported beside the primary. If the sign differs from the primary's, the results say so first.
- **Sol's floor:** a second draw on 20 escalated items.
- **Verifier floors:** Jev replay on 100 (expected 0–1 flips); local replay on 100.
- **Grader floors:** replay 100 and paraphrase 60 (§5.3).
- **Reproduction (§2aa).** No published number exists on this instrument. The checks that must reproduce before any difference is read:
  - Jev's determinism (replay flips ≤ 1/100);
  - the local backend's latency (p50 within 2× of the 0.75 s measured in `bench/jev_decisions/RESULTS.md` §7b);
  - the graders passing gate G on V;
  - the number check firing on every V-num.
- **Cache (PROTOCOL §3):** not fixed. Every item's prompt differs, so the cross-item share is the system prompt only. Cache-read tokens are reported per model.
- **A new model is uncalibrated.** No map is applied in the deciding arms (§4.1). The cross-fitted maps are secondary, grouped by source file.

## 12. The wall and contamination (PROTOCOL §1)

- **The model under test has no route to the answer except the excerpts:**
  - the drafting call carries no tools, no plugins and no web suffix, and S0 checks every logged request body for them;
  - the model gets the excerpts and the question, never the reference, the key facts, the label or the gold chunk (on NCR items the gold is absent by construction).
- **What no wall can remove is memory.** The docs are public on GitHub, so a model may know a fact that the excerpts omit. That is exactly the failure a grounded answer must not ship, and it is scored **wrong** (§5.1). The report counts NCR answers that state the hidden gold's key fact (memory or luck), as a descriptive number.
- **The verifier and graders never see which model drafted**, and the graders never see the verifier's reading.
- **The builder, freeze and report run offline.** The only network calls are the logged model calls.

## 13. Predictions (written before any call)

- **S0/S1, the V slice:**
  - Jev accepts V-gold at 80–95%;
  - Jev catches V-fabricated and V-tempt best (70–95% rejected), and V-num worst (40–75%: one digit in a long answer);
  - Jev labels V-decline `declined` in ≥ 90%;
  - Jev AUROC on supported against unsupported constructions 0.85–0.95;
  - raw local qwen3:4b is over-confident, with AUROC 0.70–0.85 and fewer V-num catches than Jev;
  - both pass the instrument gate;
  - G1 passes gate G; G2 is the grader at risk (a 24B model), and I give it a 35% chance of failing on V-offtopic.
- **S3:**
  - luna's wrong-shipped rate: ANS 2–6%, NCR 8–20%, NCP 10–30%, so W ≈ 25–50;
  - the base-rate gate passes (my estimate 75%). NCR and NCP carry most of W.
- **S4, main:**
  - **B** removes 40–65% of A's wrong answers, with hand-offs on ANS of 6–15% (Jev false rejections plus luna declining answerable questions), and a cost ratio B/A of 3–8;
  - **D** removes fewer (25–50%) and hands off more;
  - **L** finds no majority on most items (two sampled free-text answers rarely reach difflib 0.85), escalates 60–90% to Sol, and costs near C. Its wrong-shipped rate sits near C's, and it catches no wrong answer through the gate itself;
  - **C** ships fewer wrong answers than A on ANS, but not clearly on NCR/NCP, where a frontier model is as tempted to answer from memory;
  - **the correlated-family cost:** at least a third of luna's wrong `d1` on NCP items have a wrong `f1` too.
- **Decision:**
  - B qualifies as opt-in and not as default: 35%;
  - D qualifies: 15%;
  - nothing qualifies: 50%, most likely through cost (criterion 2) or hand-offs (criterion 3) rather than through significance;
  - a default-level recommendation: under 10%.
- **Mechanics:** luna and Sol return empty answers before the re-ask in under 3% of calls; no Jev body is malformed; every call is served on its pin.

## 14. PROTOCOL, rule by rule

| rule | how |
|---|---|
| §1 wall probed | request-body check in S0; no tools or plugins; the reference is never in a drafting request (§12) |
| §2 halts | re-run once; a halt leaves the pairing; rate limits counted apart (§9) |
| §3 cache and route | every call pinned, served provider and cache-read tokens recorded (§4.1, §7) |
| §4 interface first | S2's gates: readable Choice 100%, local token mass, non-empty drafts; S0's number-check self-test |
| §5 judge floor | grader replay and paraphrase; one item per call; per-grader votes stored; disagreement in the arms' band; commitment rate (the share of `declined`/`incomplete` labels) reported (§5) |
| §6 placebo | no arm adds prompt text; the drafting prompt is identical |
| §7 grouped splits | the cross-fitted maps are leave-one-file-out; the clustered bootstrap is over questions |
| §8 replicas priced | 3 draws on 100 items, 2 on all; replicas on a subset, the rest spent on items |
| §9 binarised views | the primary is binary by definition (wrong shipped); the AUROC is read on the continuous p |
| standing rules | pre-registered; predictions dated; nulls published; mechanism-active (escalation and hand-off counts per arm); the instrument and grader checks run first; the reproduction checks (§11) before any difference |

## 15. What this cannot show

- **Other corpora, other questions.** The items are this product's documentation, written as questions by one author, in two languages. The rates in any production stream (mostly answerable questions over user documents, not the product's own docs) are not these; §6's reweighting is a what-if, not a measurement.
- **Tool-using turns.** Out of scope (§1, `bench/tool_router`).
- **The cookbook's exact instrument.** The criteria are the brief's paraphrase. The cookbook's own drafting prompt and models are not reproduced, and a reader comparing numbers across the two compares two instruments.
- **The shipped cascade.** L is the shipped gate code in a two-rung structure, without `RoutingPolicy` and without the fusion rung. The shipped backend routes every grounded turn straight to mid (§1), so its own behaviour on these items is not measured beyond that census.
- **Calibration of Jev on this decision beyond these items.** A map fitted here is fitted inside this corpus's distribution. `bench/jev_decisions` saw Jev calibrate on one decision and fail on another (aacr-bench ECE 0.405). A new decision is a new instrument.
- **Pressure and permission framing.** `bench/jev_decisions/RESULTS.md` §6 found Jev refusing benign work under "production is down" framing. None of these items carry framing, so the hand-off rate here is a floor, not what a user's urgent message would produce.
- **Human hand-off cost and quality.** A hand-off is counted, not priced, and what the human would answer is not measured.
- **Latency as the user feels it.** Per-call latency is reported. A cascade's end-to-end latency on an escalated turn (draft + read + draft + read) is computed from it, not measured.
- **Vendor drift.** The Jev build is pinned by name and its resolved build is recorded. A build change between S1 and S4 would show as instrument drift, not be prevented.
- **Other thresholds as decisions.** 0.8 is the one registered; the curve is descriptive.
- **The item author's family.** If an Anthropic session writes the items, no Anthropic model is measured here, and nothing can say whether that author's phrasing favours any arm.

## Amendments

### Amendment 0 — the owner's decisions and the item freeze (2026-09-27, before any paid or model call)

Committed before any spend. Nothing below was read off a model's output: no model has been called.

**A. The owner's decisions.**
1. **Item author.** The questions, reference answers, key facts and premise questions were written by Claude (Anthropic): one Claude Opus 5.5 session that fixed the rules and the checks, and four sub-sessions of the same model family that each wrote one quarter (ANS EN, ANS PT, NCP EN, NCP PT) against the lint below, with no model output of any arm in view. **So no Anthropic model is an arm, a verifier or a grader, and the design already excludes them:** the drafters are OpenAI (`gpt-6-luna`, `gpt-6-sol`), the verifiers typesafe (`jev-1.13`) and Qwen (`qwen3:4b`, local), the graders DeepSeek (`deepseek-v4-flash-0731`) and Mistral (`mistral-small-3.2-24b-instruct`), and the swap grader Google (`gemini-3.8-flash`). The `premium` ladder's `claude-opus-5` rung (§4, "Why Sol") is not used anywhere. The report names the author (§15).
2. **Budget: cap US$ 20.00, admission stops at US$ 18.00.** §10 and the header are updated in place. With it, **arms L and C get full coverage of every item** (`f1` on all items), so F2 is read at the full n, not on a head of the frozen order.
3. **Adoption thresholds kept as proposed** (§8): opt-in when cost ≤ 5× A and ANS hand-offs ≤ 15%; default when cost ≤ 3× A, ANS hand-offs ≤ 5% and correct answers on ANS non-inferior at −5 pp.
4. **Open items keep this file's defaults.** Luna's `declined` path → **hand-off** as primary, "reply that the sources don't cover it" (ship the decline) as the registered secondary variant. Second grader `mistral-small-3.2-24b-instruct` with the `gemini-3.8-flash` swap rule (§5.3). The decision criteria stay the brief's paraphrase as written in §4.1; **the cookbook's literal text can replace them only before S1, and only by amendment.**
5. **Run plan.** The paid run is not run now. It will be driven later from the Chimera desktop app through its MCP server (`chimera mcp desktop`, shipped in 0.62.2/0.62.3); the harness (`run.py`) is equally runnable from a terminal. See the README.

**B. The freeze.** `python bench/verified_cascade/build_items.py freeze` (stdlib, offline, deterministic; `freeze --check` rebuilds and compares) read the authored files and wrote `items.jsonl`, `verifier_slice.jsonl`, `freeze_report.txt` and `manifest.json`. `python bench/verified_cascade/check_items.py` validates all of it (counts per family and language, key facts in gold and reference and absent from every NCR excerpt, NCP tempting terms absent from the excerpts, the number check's self-test, no duplicate or near-duplicate question, a source on every item, and every file against the manifest) and prints `OK`.

| | EN | PT | total |
|---|---:|---:|---:|
| ANS | 69 | 75 | 144 |
| NCR | 69 | 75 | 144 |
| NCP | 56 | 56 | 112 |
| **items** | 194 | 206 | **400** (256 distinct questions) |

- **Drops: none.** Check 2 needed no spare-neighbour replacement (every key fact was chosen, under the lint, to be absent from all four NCR excerpts); checks 1, 3 and 4 dropped nothing.
- Excerpt words, median: ANS 632, NCR 644, NCP 556; question words, median: 15, 15, 13.
- **The V slice: 1,230 triples** — gold 144, num 29, offtopic 257, extra 144, fabricated 144, tempt 112, decline 400. The number check fires on 29/29 V-num and on 0/144 V-gold.

**Manifest** (`results/manifest.json`, **sha256 `d1b4bba0176d37b6ed535ec8d4f1297864a05412004bcfdb42d648b273eee59a`**), which pins:

| file | sha256 |
|---|---|
| `questions_ans.jsonl` (authored) | `bef7146e11ca5304a4cec69aa6d502e6682b19a8493f3dac974525c5bfe6b75d` |
| `questions_ncp.jsonl` (authored) | `14433329785020c334167f9d54a1df5cc07fd5ee3a4bcc2240e049f88f18ca69` |
| `items.jsonl` | `61de7593e69ac702e2d9e067b618b4ea067bb9b1d9e4469018f37472ff4c962a` |
| `verifier_slice.jsonl` | `71342a638dc4703deed313276bd0290799ddc7bbff6258eeb8d4c028d459ec2b` |
| `freeze_report.txt` | `4313198e7e93f3761855be004a96465d274e88f38f29984355d2483db7543ada` |
| `excerpts.jsonl`, `skeleton.jsonl`, `sources.json` | unchanged from §3.2 |

**C. Clarifications the freeze and the harness had to make, fixed here before any call.**
1. **NCP excerpts** are the 4 chunks nearest the question text by the builder's TF-IDF (fitted on the language's pool), **same document first** — the rule ANS and NCR use (§3.2), so the three families share one retrieval.
2. **V-num is smaller than planned: 29 triples.** Following §3.4 literally, only a *numeric key fact* is mutated, and most key facts the lint accepted are flags, variables and names rather than numbers. Not widened after the fact. The grader preflight takes 40 per construction type where available: **all 7 types** (gold, num, offtopic, extra, fabricated, tempt, decline), so 6 × 40 + 29 = **269 triples** instead of the 240 §5.3 named, and "recall on the wrong constructions" is computed over **all five** unsupported types (§5.3 said four; §3.4 defines five).
3. **V labels mapped to grading labels:** supported → correct, unsupported → wrong; **declined → declined on an ANS item and → correct on an NCR or NCP item**, where a decline is the correct behaviour (§5.1).
4. **The number check matches digit runs as runs**: a run in the answer passes only if the same maximal run occurs in the excerpts or the question (so `280` does not pass on `2800`).
5. **Cost.** The gateway returns tokens but not OpenRouter's billed figure for chat calls; the harness records the catalogue-computed price (`price_completion`) and, where the route returns a billed cost (the Decisions API does), the billed one, and admission counts the larger. A per-generation billed lookup is not added, because it is another call per call.
6. **A graded draft on which the number check fires is labelled wrong without a grader call** (§5.2: "first and final").
7. **V-extra** is built as §3.4 says (the reference plus the first sentence of another file's reference, same language), not from a template set.

## Amendment 1 — 2026-09-27, S1 gate G: the second grader swapped as §5.3 registers

**What the gate read** (`gates.json`, S1, after 269 grading calls per grader on the labelled slice):

| | agreement | recall on wrong | wrong on V-gold | gate |
|---|---:|---:|---:|---|
| G1 `deepseek-v4-flash-0731` | 97.4% | 96.3% | 0% | passed |
| G2 `mistral-small-3.2-24b-instruct` | 86.6% | 83.0% | 0% | **failed** (needs ≥90% and ≥85%) |

G2's miss is concentrated in V-extra: the reference plus one sentence from another file's reference. G2 called 26 of 40 of those "correct". It also left 3 unreadable labels on V-fabricated. Both verifiers passed their instrument gate:
- Jev accepts 1.2% of the 686 unsupported constructions and 100% of V-gold, with 0 flips on 100 replayed reads.
- The local verifier accepts 22.2% of the unsupported constructions and 100% of V-gold.

**The change, as §5.3 registers it:**
- G2 is replaced by `openrouter/google/gemini-3.8-flash`, pinned to the `Google AI Studio` route (`harness.PINS`).
- `replay.GRADERS["g2"]` now names it. The grader id stays `g2`, so the report's columns keep their names.

**Data handling:**
- Mistral's 269 grading rows are moved out of `calls.jsonl` into `discarded_g2_mistral_amendment1.jsonl`, next to it in the run directory.
- Otherwise the ledger's resume key (`grade|g2|<target>`) would have served Mistral's labels as Gemini's.
- The US$ 0.0412 they cost stays in the reported total spend. The admission stop (US$ 18.00) is unchanged, because 18.00 + 0.04 is still well under the US$ 20 cap.
- The verifier reads (Jev, local) and G1's grades are kept, and S1 is re-run so that only the new G2 is called.

**Cost re-estimate.**
- Gemini on this route is listed at 0.375–0.75 per M in and 1.875–3.75 per M out, depending on the tier the route serves. That is about US$ 0.001–0.002 per grading call, against the 0.0003 assumed for Mistral.
- Over the main run's grading calls (at most about 2,000 per grader), that adds at most about US$ 4. The projected total stays under the US$ 18 admission stop. S2's pilot re-measures the per-call means, and §10's sizing then applies as registered.

**Not changed:** items, arms, thresholds, metrics, the adoption rule and every other gate. If Gemini also fails gate G, the run stops and the result is published as "no admissible second grader". A third grader would need a new amendment.
