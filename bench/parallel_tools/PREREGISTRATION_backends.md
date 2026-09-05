# Was the 23-point spread between families, or between backends? — pre-registration

**Written before any run. No outcome was seen first.**

[`RESULTS.md`](RESULTS.md) closed the parallel-tool-call question on a measurement of tool-latency,
and carried one finding forward as the thing worth keeping:

> `deepseek-v4-flash-0731` and `gemini-3.8-flash` differ by 23 percentage points in how they use a
> tool-calling API, and any measurement of loop behaviour that averages over models is averaging
> over that.

`bench/context_rot` then found that a slug is not a machine: three consecutive calls to
`openrouter/deepseek/deepseek-v4-flash-0731` were answered by **`Wafer`, `Inceptron` and
`DigitalOcean`**. OpenRouter routes per call. So "the model batches 23.3% of the time" was measured
over a *pool*, and the census could not have known which member answered — nothing recorded it.

This asks the question that decides whether that carried-forward finding survives.

---

## What is not being done, and why

**Not a re-run of the census.** That census read 137 runs accumulated over weeks of ordinary use, and
none of them carry a provider. Retrofitting is impossible and reproducing that corpus would take
weeks and buy the same confound at a different scale.

The confound is testable much more cheaply, because it needs only **one** slug: if backends serving
a single model batch at different rates, then a spread measured *between* slugs cannot be attributed
to the slugs. One model, many backends, one question.

## Design

N short exploratory agent runs against a fixed small workspace, all on
`openrouter/deepseek/deepseek-v4-flash-0731` — the slug the original census reported at 23.3%, and
the one already observed rotating across at least five backends.

Each step records the batch size (`len(tool_calls)`) and the serving provider, which
`StepRecord.provider` now carries. Tasks are read-only exploration, because that is where the
original census found its multi-call batches (`list_dir + read_file`, `grep + grep`, `glob + glob`).

## Primary outcome and decision rule, fixed now

**The share of tool-calling steps carrying two or more calls, per backend**, over backends with at
least **30** tool-calling steps.

- **THE FAMILY ATTRIBUTION IS UNSAFE** if the max−min spread between qualifying backends reaches
  **≥ 10 percentage points**. Ten, not twenty-three: if backends inside one slug already differ by
  under half the between-family figure, that figure cannot be read as a fact about families, and
  `RESULTS.md` must be amended to say so.
- **THE FAMILY ATTRIBUTION SURVIVES** if the spread is under 10 points — backends of one slug agree,
  so a between-slug difference is about the slug.
- **NO CLAIM** if fewer than two backends reach 30 steps. A spread computed over one backend, or over
  cells of five, is a spread about nothing.

## Gates before the aggregate is believed

- **How many backends answered, and how the steps split between them.** A run that lands 95% on one
  backend cannot compare backends, whatever the totals say.
- **Batch shapes, printed per backend.** Two backends at the same *rate* that batch entirely
  different tool pairs are not agreeing, and a single percentage would hide that.
- **The steps that carry no provider** are counted and reported, never folded into a backend.

## What this cannot show

- **One slug, one task shape.** Read-only exploration on a small workspace. A backend that differs on
  writing, or under a long context, is invisible here.
- **It cannot rehabilitate the old census.** Whatever this finds, the 137 runs behind
  `RESULTS.md` have no provider and never will. This can only say whether the finding they carried
  is safe to keep quoting.
- **Backend identity is a name from the router**, not a machine. Two names may be one operator, or
  one name may front several.
- **Routing is not under control.** Which backends appear, and in what proportion, is OpenRouter's
  decision on the day. A backend absent today is not a backend that does not exist.

## Cost

N ≈ 120 short runs on a cent-per-million model. Estimated **under one dollar**, reported as measured.

## Result

`bench/parallel_tools/RESULTS_backends.md`, and an amendment to `RESULTS.md` if the rule says the
attribution is unsafe.

---

## Amendment: observing the router cannot sample backends

Made after the observational run and before any comparison existed.

120 consecutive runs produced **321 tool-calling steps, all from one backend (`Baidu`)**. By the rule
above that is NO CLAIM — fewer than two backends reached thirty steps — and it is also a fact about
the instrument: routing is per-call in one session and sticky across another, so *watching* it does
not sample the pool. `bench/context_rot` saw five backends rotate across a handful of calls; this saw
one across a hundred and twenty.

The router can be **pinned** instead. OpenRouter accepts `provider: {order: [...],
allow_fallbacks: false}`, verified working: asking for `Baidu` returned `Baidu` and asking for
`Wafer` returned `Wafer`.

So the design changes from observing routing to **fixing it**: the same task set run against several
named backends, one at a time. That is a stronger comparison than the original — same items, same
ruler, only the backend varies — and it is the only one available, since observation cannot reach
more than one cell.

**What had been seen when this was written.** One number: `Baidu` batched on 65.4% of its 321 steps.
No between-backend comparison existed, which is what the decision rule is about, so nothing about the
outcome under test had been observed. It is stated anyway.

That 65.4% is **not comparable to the 23.3% the original census reported for this slug**, and the
reason is worth writing down rather than discovering later: this bench runs read-only exploration on
a five-file package, chosen precisely because that is where batches happen, while the census read
real mixed usage including writes. Different items, different mix — comparing them would be the
error this project keeps finding in its own work.

### The rule, restated for pinned arms

Unchanged in substance. Over backends with at least 30 tool-calling steps **in this run**, on the
same tasks:

- **UNSAFE** if max−min reaches **≥ 10 percentage points**.
- **SURVIVES** if under 10.
- **NO CLAIM** if fewer than two backends yield 30 steps — a backend that rate-limits or refuses is
  reported as absent, never as a zero.
