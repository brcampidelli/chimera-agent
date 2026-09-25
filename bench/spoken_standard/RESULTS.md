# Results — H9: the spoken standard, said as what to do

**Run 2026-09-25**, against the pre-registration committed in `eca21281` before any paid call.

- **Calls:** 180. None halted, none was truncated, and the run did not stop on budget.
- **Cost:** US$ 0.0055 priced; the conservative bound is US$ 0.0106, against a cap of US$ 0.30.
- **Model:** `openrouter/deepseek/deepseek-v4-flash-0731`, temperature 0.2, reasoning off.
- **Routing:** 17 providers answered the one slug. The two largest, Sail Research and Relace,
  carried 47 of 72 A answers and 47 of 72 B answers.
- **Data:** the answers are in `results/run.json`; the report's summary is in
  `results/run-summary.json`.

**Decision by the frozen rule: NULL.** Speakable did not rise: it fell by 9.7 pp with p = 0.12. The
length condition failed as well: B's spoken part is 1.56 times A's, against a limit of 1.20.

**The direction is the opposite of the hypothesis.** On the part of the standard both arms state,
B is significantly worse (−23.6 pp, p = 0.0002). The rewrite that names no forbidden form produced
more Markdown, not less.

## Numbers

| | A (shipped) | B (rewrite) | N (no note) |
|---|---:|---:|---:|
| answers | 72 | 72 | 36 |
| **speakable** (primary) | **28 (39%)** | **21 (29%)** | 5 (14%) |
| format-speakable (secondary) | 42 (58%) | 25 (35%) | 5 (14%) |
| answers with any Markdown | 29 | 46 | 30 |
| — inline code | 23 | 32 | 21 |
| — code fence above the line | 11 | 15 | 17 |
| — emphasis (bold/italic) | 1 | 24 | 29 |
| — list | 2 | 14 | 19 |
| — heading | 0 | 1 | 6 |
| — table | 0 | 0 | 2 |
| more than four sentences | 4 | 18 | 22 |
| emoji | 0 | 0 | 1 |
| URL | 10 | 9 | 5 |
| long digits / IDs | 2 | 2 | 1 |
| path | 4 | 5 | 8 |
| empty spoken part | 0 | 0 | 0 |
| used the `---` line | 50% | 72% | 6% |
| spoken words (mean) | 36.4 | 56.9 | 118.8 |
| sentences (mean) | 2.35 | 4.00 | 11.67 |
| completion tokens (mean) | 123 | 184 | 277 |
| latency, median | 2.83 s | 2.74 s | 4.45 s |

**Paired, 72 pairs (request × replica):**

| outcome | both | A only | B only | neither | B − A [Newcombe 95%] | exact McNemar p | request-level sign p |
|---|---:|---:|---:|---:|---|---:|---:|
| speakable | 17 | 11 | 4 | 40 | −9.7 pp [−19.7, +0.6] | 0.1185 | 0.2266 (A better on 8 requests, B on 3) |
| format-speakable | 23 | 19 | 2 | 28 | −23.6 pp [−34.0, −12.1] | **0.0002** | **0.0042** (A 14, B 2) |

- **Replay floor:** 4 of 36 requests flip speakable between A's two replicas; 3 of 36 flip between
  B's.
- **Positive control:** A − N = +25.0 pp, which passes. The note reaches the model, and the checker
  sees a change of form.
- **The instrument:** the checker and corpus hashes in the results file match the committed
  files:
  - `checker.py` `4da541c4…`;
  - `corpus.py` `16f35180…`.

  The file's `git_head` reads `HEAD` because the WSL copy is a fresh repository with no commits.
  The hashes are what pin it.

**Per kind (speakable, A / B / N):**

| kind | A | B | N |
|---|---:|---:|---:|
| files | 4/12 | 1/12 | 0/6 |
| command | 1/12 | 0/12 | 0/6 |
| code | 0/10 | 0/10 | 0/5 |
| url | 0/10 | 1/10 | 0/5 |
| numbers | 8/10 | 6/10 | 1/5 |
| compare | 7/10 | 5/10 | 1/5 |
| chat | 8/8 | 8/8 | 3/4 |

**Per language (speakable, A / B / N):**

| language | A | B | N |
|---|---:|---:|---:|
| Portuguese | 15/36 | 8/36 | 1/18 |
| English | 13/36 | 13/36 | 4/18 |

The split by language is descriptive; the pre-registration names it but registers no test on it.

- All seven Portuguese discordant pairs favour A (exact p = 0.016, exploratory). In English they
  split 4 to 4.
- B's spoken part in Portuguese averages 58.6 words, against 31.9 for A.

**What B's losses are made of.** In the 11 A-only pairs, B's violations were:

| violation | count |
|---|---:|
| inline code | 6 |
| emphasis | 6 |
| more than four sentences | 4 |
| list | 3 |
| code fence | 1 |
| path | 1 |

Not one was a digit or URL violation. In the 4 B-only pairs, A had one each of inline code, URL,
path and more than four sentences.

## The predictions, scored

| | prediction | outcome |
|---|---|---|
| P1 | A speakable on ≥ 7/8 chat, least on files/url/numbers | **Half right.** Chat 8/8. The least speakable were code 0/10, url 0/10 and command 1/12. Numbers was one of the best, at 8/10. |
| P2 | Markdown and emoji rare in A (< 10%); B − A on format-speakable within the replay floor | **Wrong, both halves.** A has Markdown in 40% of answers, mostly inline code and fences. B is 23.6 pp *worse* on format, far outside the floor. |
| P3 | Any B gain comes from digits/path/URL | **Wrong.** There was no gain there: URL 10 → 9, digits 2 → 2, path 4 → 5. |
| P4 | B uses the `---` line at least as often | **Right**: 72% against 50%. But B writes more above the line: 39 of its 52 answers with the line are still unspeakable. |
| P5 | N at least 20 pp below A | **Right**: 25 pp. |
| overall | B − A between 0 and +15 pp | **Wrong sign**: −9.7 pp. |

## What it shows

1. **On this model, naming the forbidden forms is what holds them back.**
   - A's list of "no headings, no lists, …, no Markdown, no emoji" nearly eliminates emphasis
     (1 answer), lists (2) and headings (0).
   - B, which never names them, lets the model's chat defaults back in: emphasis in 24 answers,
     lists in 14, long spoken parts in 18.
   - The priming finding the plan cites (2601.08070, naming a forbidden word primes it) did not
     transfer to format prohibitions on `deepseek-v4-flash`. The plan's S4 line, "said as what to
     do, not five 'no's", is not supported by this measurement, and §2.3 should record the null
     here as a counter-example.
2. **The added content bought nothing measurable.**
   - The numbers clause had little room. Under both notes the model already wrote thousands-grouped
     numbers (`31.536.000`, `4,294,967,296`) and dates in words ("April 2027"). The only digit
     failures were the IP ranges, identical in both arms.
   - Listing links, paths and IDs as below-the-line material did not move URLs (10 against 9).
3. **Where the shipped note fails.** A's failures are not the 09-17 kind (headings, lists, emoji).
   They are:
   - inline code, such as back-ticked file names (23 answers);
   - a code fence above the line (11);
   - a URL said inline (10).

   Two of the three are already softened by the reader: `plainForSpeech` keeps inline code's text
   and reduces a URL to its host. Exploratory, not registered: if inline code and URLs are
   excused, speakable is A 54/72 (75%), B 36/72 (50%), N 5/36. The ordering is the same (21 A-only
   pairs against 3 B-only, p = 0.0003).

## Recommendation to the coordinator

- **Keep `SPOKEN_NOTE` as shipped.** This PR does not touch it.
- **Do not generalise the do-not-say-don't rule to format constraints** without a per-model
  measurement. On the product default, the explicit prohibitions are load-bearing.
- **If the standard is revisited,** the measured failure modes point to a different edit: one that
  *adds* to A and removes nothing. Candidates:
  - "file names without back-ticks";
  - "code, commands and links go below the line".

  That edit would need its own arm; this run does not license it.

## What this cannot show

- **Framing versus content.** B also added "the gist first" always, a numbers clause, and a longer
  list of below-the-line material. The loss sits in Markdown and sentence count, not in the
  content keys, which points at the dropped prohibitions. But no arm drops them and adds nothing
  else.
- **Other models, and the work path.** This is one model and one day, on the talk path only
  (reasoning off, no tools).
  - The whole family of findings that a prohibition primes the forbidden thing may hold on other
    models. The overlay mechanism (§6) is where that would live.
  - The work model, with thinking as configured, tools and a workspace, is not covered.
- **Paraphrase floor.** Only the replay floor was measured (8–11% of requests flip). A neutral
  rewording of A was not run. The 23.6 pp format gap is well outside the replay floor, but "any
  rewording of A loses some Markdown discipline" is not ruled out.
- **Clustering.** The pair-level McNemar treats the two replicas of a request as independent. The
  request-level sign test agrees in direction on both outcomes: p = 0.23 on speakable, 0.004 on
  format-speakable.
- **Audio.**
  - This is text, not audio. The reader rescues part of what the checker counts (see the
    exploratory line above).
  - Time to first audio was not measured, because the call is not streamed. Median latency to the
    full answer was the same for A and B.
- **Real speech.** The requests are clean transcripts, with no mis-hearings, history or memory
  facts.
- **Outside the metric:** one Portuguese answer in each arm drifted into Spanish below the line
  ("Contiene credenciales", "archivo generado").

## A defect found while building it

`LLMGateway.complete` accepts `thinking=` and does not forward it. Only `stream_complete` builds
the request with `_provider_kwargs(resolved, thinking=thinking)`. So
`chimera/decisions/hosted.py`, which calls `complete(..., thinking=False)`, has been sending requests
with reasoning at the model's default. This bench passes the reasoning switch explicitly. The fix is
out of this arm's scope and was flagged as a separate task.
