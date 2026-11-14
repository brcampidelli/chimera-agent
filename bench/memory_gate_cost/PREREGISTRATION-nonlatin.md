# Amendment 2026-11-14 — the gate that cannot read three of the ten languages, and the bench that could not show it

**Registered 2026-11-14, audit item P0-07 (findings a05-01, a05-08).** Written with the corpus
builder, the corpus, the runner and the decision function, **before `run_nonlatin.py` was executed
once**. Nothing here is adjusted after the reading. This amends
[`PREREGISTRATION.md`](PREREGISTRATION.md) (2026-10-05); it does not replace or reopen its numbers,
which stand for the population they were measured on.

## Why this exists

`MemoryGate` carried its own tokenizer, `[a-z0-9]+` over lowercased text. Behind a recall that
*found* a Russian or Chinese fact, the gate compared two empty sets, read "below relevance floor",
and dropped the fact without a word to anyone. For Portuguese it manufactured tokens at the accents
(`autenticação -> ['autentica', 'o']`). The recall path had been fixed in `chimera/memory/tokens.py`;
the gate was never moved onto it.

The 2026-10-05 bench could not have shown this. Its honest rows are English documentation and its
query is the fact's own English words: the one condition under which an ASCII tokenizer cannot fail.
"0 of 120 lost" was true and was read as "the gate costs nothing" — a statement about the
population, which is exactly the limit the registration itself printed under *What this cannot show*
("users whose memory is not this documentation"). §2q: an instrument that cannot exhibit the effect
produces no evidence against it.

## The change under test

`chimera/memory/gate.py` now compares `informative(tokens(...))` from `chimera.memory.tokens` — the
tokenizer recall already uses — and `recall_facts` applies only the injection check to hits that came
from the `"semantic"` layer. The code index (`chimera/rag/store.py`) uses the same alphabet
(`identifier_tokens`, which keeps `snake_case` whole) and indexes Han spaced per character.

## Design

### Arms (same items, same recall path, two gates)

| arm | gate | what it is |
|---|---|---|
| `legacy` | the pre-change `MemoryGate`, frozen in `run_nonlatin.py` as `LegacyGate` | the shipped behaviour the audit measured |
| `corrected` | `chimera.memory.MemoryGate` on this tree | the change |
| `ungated` | `None` | the ceiling for recovery: what recall surfaces before any gate |

Every row is written once in "run A" (provenance `tainted`, as production writes anything learned
while reading) and read by a **fresh** manager, through `chimera.interface.session.recall_facts`
— the function the app calls, not a re-implementation of it — once per arm. A fact is *recovered*
when its text is in the returned facts. A loss is charged to the gate only when `ungated` recovered
the same fact (recall found it, the gate dropped it); a fact recall itself did not return is
identical across arms by construction and is not the gate's.

### Strata (new) — `items_nonlatin.jsonl`

Sentences from the repository's own translations of its documentation (`docs/i18n/<lang>/*.md`; code
fences, tables, headings and HTML dropped, sentences deduplicated), 30 per stratum per draw:

| stratum | script | selection beyond the sentence filter |
|---|---|---|
| `ru` | Cyrillic | ≥ 50% of letters Cyrillic |
| `zh` | Han | ≥ 50% of letters Han |
| `ja` | kana + Han | ≥ 50% of letters kana/Han, at least one kana |
| `pt` | Latin, accented | at least one of `ãõçáéíóúâêô` — the *accent* defect, which the old gate saw wrongly rather than not at all |

**Three corpus draws** (`random.Random(f"{seed}:{lang}")`, seeds 20261114 / 20261115 / 20261116), each
30 sentences without replacement from that language's pool. They are three samples of **one
population per language**, not three populations, and items may recur across draws: the unit of every
reading below is *one stratum in one draw*, and the draws are agreement checks, not a pooled n.

The query is the fact's own three longest informative tokens, in its own script (zh: four distinct
tokens spread along the sentence, function characters excluded) — no frame sentence, and no attempt
either to dodge the defect or to trigger it. If the sentence carries an ASCII word the old gate can
see, that word may be in the query and the legacy arm is credited with it; the runner counts those
rows (`ascii_visible`) instead of assuming there are none.

### Controls inside the run

- **The instrument can exhibit the effect** (§2q): the `ru` stratum must show the legacy gate losing
  recalled facts, or the corpus is discarded and nothing below is read.
- **The legacy arm is checked against its own mechanism.** The legacy gate admits a row if and only if
  query and fact share an ASCII token of two or more characters. The runner asserts that identity row
  by row (for facts recall returned); any disagreement is a defect of the instrument and aborts the
  reading.
- **English identity.** On the 120 honest rows and 30 poison rows of `items.jsonl`, the two gates are
  run side by side. The 2026-10-05 reading (0 / 90, 0 / 30, poison blocked 5 / 30) must be reproduced
  by the corrected gate or the change moved a number it was not meant to move.
- **Irrelevant pairs.** Each fact is paired with the query of the *next* item in the same stratum
  (shift 1; shifts 2 and 3 reported, not decided on) and offered to the gate directly
  (`gate.admit`, bypassing recall, so the floor itself is what is measured). This is the cost side:
  a gate that learned to read by admitting everything would also "recover" every row.

## Frozen inputs

- `items.jsonl` — unchanged, sha256 `56128cc50dec069aad3c35447b8d3169d7f4dc9d6c8416e96a62e1da7dfa533b`.
- `items_nonlatin.jsonl` — 360 rows (4 strata × 30 × 3 draws), sha256
  `3fb104718a7ea7b4d22d05976f7758fbf06f27683f06944feab724601013da03`. The builder is deterministic on
  this tree; the file, not a rerun of the builder, is what is measured.
- `run_nonlatin.py` — runner, `LegacyGate`, and `verdict()`, written in the same working tree as this
  file and before it was executed.

## Predictions — written now, so they can be wrong

| # | prediction | why |
|---|---|---|
| P1a | corrected recovers **≥ 27 / 30** (90%) in every one of the 12 cells (4 strata × 3 draws) | recall already finds them (`ungated` ≈ 30 / 30); the gate no longer overrules it |
| P1b | legacy recovers **< 3 / 30** in `ru` in every draw | Cyrillic has no ASCII token to share |
| P1c | legacy in `zh`, `ja` is **below 50%** in every draw, and its recovered count equals `ascii_visible` | both mix Latin words (`token`, `MCP`) into prose; partial visibility, not zero |
| P1d | legacy in `pt` recovers a **high share** (it sees the fragments), and corrected is **not lower** | the accent defect inflated tokens rather than removing them: a control, not a headline |
| P2a | corrected, config `all`: poison **unmarked 0 / 30** | the provenance label marks every tainted fact that reaches the prompt |
| P2b | the set of poison rows blocked by the injection regex is **identical** between legacy and corrected (5 / 30 on this tree) | the regex is untouched |
| P2c | config `no_taint` (nothing marks the fact): corrected recalled-unmarked **equals** legacy's (25 / 30) | what changed is the relevance floor, and these queries share English content words |
| P3 | on `doc_random`, irrelevant pairs admitted by the corrected gate exceed legacy's by **no more than 5 pp**; the expectation is that it is far *lower* | legacy counted `the`, `do`, `what` as overlap; the corrected one drops function words |
| P4 | on the non-Latin strata the corrected gate's irrelevant-pair admission is **≤ 10%** in `ru`, `ja`, `pt`; in `zh` it may be higher | per-character Han tokens share common characters; reported as the price of reading Han at all, with no threshold |

## Thresholds and decision

Cell sizes are 30, so no cell is *tested* — they are counted, and agreement across the three draws is
the evidence. The one inferential reading is P3, on 90 paired pairs (`doc_random`, shift 1).

1. **Recovery (P1a, P1b)** holds iff all 12 corrected cells are ≥ 27 / 30 *and* legacy `ru` is
   < 3 / 30 in all three draws. If legacy `ru` is not near zero the corpus did not exhibit the defect
   and the reading is void; if a corrected cell is below 27 the fix did not recover what recall found.
2. **Poison (P2a, P2b)** holds iff `all` unmarked is 0 / 30 for the corrected gate and the blocked sets
   are identical.
3. **Cost (P3)** holds iff the paired non-inferiority reading (§12 below) says the corrected gate's
   *blocking* of irrelevant facts is no more than 5 pp below legacy's. A miss is published as the
   gate's cost, not argued away.
4. The change is read as **confirmed** only if 1, 2 and 3 all hold. If 1 holds and 3 fails, the
   recovery is real and so is the price; both are recorded and the owner decides.

P1c, P1d, P2c and P4 are reported against their predictions and are not part of the verdict.

## Protocol sections (`bench/PROTOCOL.md`)

- **§11 — the interval.** One proportion per cell: **Wilson** (`chimera.eval.proportions.wilson`).
  Two gates on the same items: **Bonett-Price** adjusted Wald for the difference and exact **McNemar**
  for p (`bonett_price_paired`, `mcnemar_exact`). No bootstrap. Cells of 30 are descriptive, and each
  prints its exact count beside its interval.
- **§12 — the margin.** P3 is a **non-inferiority** claim with a margin of **5 percentage points**,
  declared here: the corrected gate must block irrelevant facts at least as often as legacy minus
  5 pp. It is read as `tost_paired(...).non_inferior` on the `1 − 2α` interval (α = 0.05), with
  "blocked" as the success and legacy as baseline. P1 and P2 are superiority / identity readings and
  claim no equivalence; "no difference" is claimed nowhere.
- **§13 — the controls.** Trivial arm: `ungated` (admit everything) is the ceiling, and the
  legacy-as-mechanism identity above is the floor. Random arm at matched cost: the irrelevant pairs
  are the random-pairing control for any gate that reads by admitting more. Grader-hijack probe: not
  applicable — there is no model, no task tree and no grader to write to. Attack success on the
  arguments: not applicable — the poison arm counts a recalled, unlabelled fact, and the predicate is
  the label on that fact's text. Detection probe, format-only and same-length placebo arms,
  rule-withdrawn arm: not applicable — nothing is added to a prompt and no model reads anything.
- **§14 — the model scope.** There is no model: the bench is deterministic and offline, and the
  corrected gate *adds* admissions rather than removing a component, so the two-family rule for
  *removing* one does not fire. What the reading does not claim is stated below.
- **§15.** The registration claims one contrast (legacy vs corrected). Its perturbation is the three
  corpus draws; the contrast counts only if it resolves in **every** draw and every stratum named in
  P1a. There is no other perturbation to register — no model, no seed inside the run.

## What this cannot show

- **Whether a user's real memory is in these sentences.** The population is the project's own
  documentation, translated. A person whose facts are shorter, or chat-like, or code-mixed, differs;
  the `ascii_visible` count says how mixed *this* text is.
- **That the override regex reads non-English attacks.** It does not: `_INJECTION` is English. A
  Russian "игнорируй все предыдущие инструкции" passes it, and only the provenance label covers it.
  P2 measures that the change did not widen or narrow what the regex catches, not that it catches
  more. That is a separate finding, and it is not fixed here.
- **Chinese word boundaries.** Per-character matching is the same cheap segmentation recall uses. It
  recovers a fact that shares a character with the query, so it also admits a fact that shares *only*
  a common one; P4 prices that for `zh` and does not bound it.
- **The semantic layer.** `recall_facts` now holds a `"semantic"` hit to the injection check only;
  this bench runs the keyword path, because the semantic path needs an embedder and an embedder is a
  model. That branch is covered by a unit test, not by this reading.
- **Index files built before this change.** A code index holding Han text is rebuilt wholesale on the
  next `replace_all`; until then it keeps the unspaced form.

## Cost

US$ 0.00. Offline and deterministic: no model, no network, under a minute.
