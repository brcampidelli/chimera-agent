---
name: chimera-state-what-you-did-not-check
description: A ranking built from part of the system reads as a ranking of the system — write the scope and the exclusions next to the findings, not after them.
version: 0.1.0
kind: pattern
stage: review
topic: research
triggers:
- writing a review or audit
- listing the top risks
- reporting findings
- ranking issues by severity
- someone asks if it is safe
provenance: clean
status: active
license: Apache-2.0
---

## Trigger

You are delivering something that reads as a survey: a code review, a security pass, "the three
biggest problems", a prioritised backlog, a comparison of options. Anything where you looked at
some of a space and produced an ordering.

It does not apply to answering a bounded question — "does this function handle an empty list?" —
where the scope is already the whole of what was asked. The risk here is specific to output whose
*form* is a list of the worst things, because that form carries an implied "of everything" the
reader will supply for free.

## Do

1. Open with the scope, before the findings. Name what you actually read: paths or globs, the
   commit or branch, and whether you ran anything or only read. `Reviewed: src/api/*.py at
   4f2a1c9, static read only, no tests executed.`
2. List the exclusions with their reason, and keep the reasons concrete: no access to the
   migrations, the integration suite needs credentials I do not have, the frontend was out of
   budget for this pass.
3. Scope every superlative to what you read. Not "the worst issue is the missing auth check" but
   "the worst of the six handlers I read". The two sentences cost the same and mean different
   things.
4. Separate "checked and clean" from "not checked". A reader treats silence as a clean bill;
   only one of those two states earns it.
5. Name the unchecked thing most likely to outrank your current number one, so the reader knows
   what the next pass should buy. If nothing plausible could, say that too — it is a real
   finding.

## Avoid

Reporting "no SQL injection found" after a pass that never opened the database layer. The
sentence is literally true and functions as a clearance. Write "did not review `db/` — no access
to the migration files" and the same pass now says what it knows.

Avoid putting the scope in a closing paragraph under a heading like *Limitations*. Readers act on
the top of the document; a caveat below the ranking arrives after the decision it was supposed to
qualify.

Avoid the ordering that spans checked and unchecked work. Ranking three reviewed modules against
one you skimmed produces a list whose positions mean different things at different rows, and
nothing in the layout tells the reader which is which.

## Check

Ask one binary question of the finished document: can a reader who was not present name the files
you read and the ones you did not, without asking you? If not, the scope is missing regardless of
how careful the findings are.

Second, reread every superlative — *worst*, *main*, *top*, *only*, *no* — and confirm each one
carries its qualifier in the same sentence. These words are where the partial quietly becomes
global.

## Risk

Exclusion lists can grow into blanket immunity: a document that disclaims everything asserts
nothing, and reviewers who write them stop being accountable for the part they did cover. Keep
the list to exclusions a reader would otherwise assume were included.

Stating scope also invites "then go check the rest", which is sometimes the wrong call — a
deliberately partial pass on the highest-risk surface can be the correct use of the time. If so,
say why that surface was chosen, or the honest disclosure gets read as an unfinished job.

And scope is not a substitute for depth. "I read these six files" does not mean the six were read
well, and a precise boundary around shallow work still makes shallow findings.
