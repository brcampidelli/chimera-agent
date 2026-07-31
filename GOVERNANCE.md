# Governance

Chimera is maintained by one person. That is the project's largest risk — larger than any bug in
this repo — and this document exists to make it addressable rather than merely admitted.

## The honest status

| | Today |
|---|---|
| Maintainers with commit rights | **1** (@brcampidelli) |
| Maintainers who can publish a release | **1** |
| Bus factor | **1** |
| Funding | donations only ([Stripe](https://donate.stripe.com/9B63cofM491m4SBfe177O00)) |
| Governance model | BDFL, by default rather than by design |

If the maintainer stops, the project stops: no one else can merge a security fix, cut a release, or
rotate the PyPI trusted-publisher configuration. Users should weigh that before depending on Chimera
for anything load-bearing. Low adoption and a bus factor of one are the honest weaknesses; this file
is the plan to change them.

## Decision-making

While there is one maintainer, decisions are made by that maintainer, in public:

- **Design and architecture** — argued in an issue or a PR description before the code lands. The
  reasoning goes in the commit message, not just the diff; see the commit history for the standard.
- **Anything measured** — benchmark numbers, security claims — follows the rules in each suite's
  `bench/*/PREREGISTRATION.md` and in `MUTATION.md`. A number that is not reproducible from a
  committed artifact does not go in the README.
- **Security posture** — changes to defaults that weaken a boundary need an explicit rationale in
  `SECURITY.md`, dated, including who is expected to be affected.

This section is written to survive the transition to more than one maintainer: it describes how
decisions are *justified*, which does not change when the number of people does.

## How a pull request is handled

The section above says how a decision is justified. This one says what happens to *your* change,
which is the question you actually have.

**Triage is weekly.** Issues and pull requests are looked at once a week. Expect a first response
within seven days. That is a cadence, not a service level — but a published cadence is the difference
between waiting and wondering, and wondering is what makes people leave.

**What gets merged.** It does what the description says, the whole suite is green, new logic has a
test, and it stays inside the scope agreed on the issue. `CONTRIBUTING.md` splits the tree into
*open*, *agreed in an issue first*, and *protected*; a change in the open set needs no permission at
all.

**How something is refused.** In writing, with a reason, and with a label — `declined: out of scope`
or `declined: core`. Never by silence. A closed pull request with an explanation costs you an
afternoon; an open one nobody answers costs you the afternoon *and* the month you spent checking on
it. If a change is refused because of where it lands rather than what it does, that is said plainly,
and the underlying problem is still worth an issue.

**What is refused by default.** An unsolicited patch to a protected path (the security kernel, the
sandbox, generated artifacts, pre-registered numbers, the CI gates). Not because the problem is
unwelcome — a bug report or a proof-of-concept against any of them is *actively wanted*, and security
findings go through `SECURITY.md` and get answered. What is refused is landing a change to an
invariant before agreeing that it is one.

**Licensing.** Contributions are licensed inbound under Apache-2.0, per section 5 of the licence.
There is no CLA and no DCO bot: one signature ceremony would not make the licence any more binding
than it already is, and it would turn away the first-time contributor this project needs most.

## Becoming a maintainer

There is no application process and no probation period, because with one maintainer that would be
theatre. The practical path:

1. **Land a few changes.** `CONTRIBUTING.md` has a "good first issues" table pairing each task with
   a concrete pattern to copy. Anything from a bug fix to a test that kills a surviving mutant.
2. **Show the standard holds.** The bar is not volume; it is that your changes explain *why*, keep
   the suite green, and do not widen the gap between what the docs claim and what the code does.
3. **Ask.** Open an issue titled "maintainer interest" saying which area you want to own (fusion,
   memory, security, desktop, benchmarks). The maintainer will say yes or explain what is missing.

A new maintainer gets commit rights first. Release/publish rights follow once they have shipped a
release with the current maintainer watching — the point is that a second person has *done* it, not
merely been granted permission to.

### Areas that most need a second pair of hands

| Area | Why it needs someone |
|---|---|
| Security enforcement | The defence is real but the audit surface is wide; an independent reviewer is worth more here than anywhere else |
| Benchmarks | The methodology is the project's moat and it should not be graded solely by its author |
| Desktop app | TypeScript/React skills the maintainer has least of |
| Provider layer | Breaks whenever an upstream (litellm, a provider API) moves |

## Security response

Reporting instructions live in `SECURITY.md`. The commitments:

| Stage | Target |
|---|---|
| Acknowledge a report | 3 business days |
| Initial assessment (severity, affected versions) | 7 days |
| Fix or documented mitigation for a critical issue | 30 days |
| Public disclosure | coordinated with the reporter, after a fix ships |

These are targets from a single maintainer, not an SLA backed by an organisation — if you need a
guaranteed response window, Chimera cannot honestly offer one today. That is precisely the gap a
second maintainer closes, and why the table above exists.

## Continuity

Concrete steps so a stalled maintainer is a delay rather than an ending:

- **Everything needed to build and publish is in this repo.** Release steps are in `RELEASING.md`;
  publishing uses PyPI Trusted Publishing (OIDC) configured against this repository, so it does not
  depend on a token held by one person.
- **The desktop updater signing key is the exception** — it is *not* in the repo (by design), and
  losing it breaks updates for installed apps. It must be backed up outside this machine.
- **If the maintainer is unreachable for 6 months**, the project should be considered unmaintained:
  fork it. Apache-2.0 exists exactly for that, and an honest fork is better than waiting.

## What this document is not

This is scaffolding, not a solution. Writing down how to become a maintainer does not produce a
maintainer, and a response-time table does not add hours to one person's day. The bus factor changes
when someone else shows up and is given real rights — everything above only removes the excuses for
that not happening.
