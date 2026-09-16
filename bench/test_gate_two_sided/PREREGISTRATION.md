# Pre-registration — a generated test is measured on both sides before it governs; a diff rule is measured on its false alarms before it reviews

**Registered 2026-09-15, before any patch was produced or any test generated.** Study 19, items
**B3 + B4** (the bilateral test gate and its two metrics) and **S2** (the deterministic diff rule),
designed together because they are one layer: what a verifier is allowed to do to a patch it
did not write. Budget **US$ 2**; expected ≈ US$ 0.5.

## What is already built, and what this measures

`SpecTestVerifier` already classifies each generated test by its two outcomes on the pre-change
and post-change trees (`spec_test.py::_against_base`, from `bench/spec_test_vacuity`): vacuous
(passes on both, excluded), discriminating (fails before, passes after — the evidence),
regression, failing; and it **abstains** when nothing discriminates. That is the half of B3 that
rejects a test which already passes on the pre-patch tree. What has never been measured is the
other side — a generated test that **fails a correct patch** and so reverts good work — and the
gate's accuracy as a gate: over patches whose correctness is known, how often does it pass the
correct ones and fail the wrong ones. Two numbers from the sweep's test-generation cluster
(`PLAN-study19` rows B3/B4) are why the false-alarm side is the one to measure first: generated
tests trigger the fault far more often than they detect it (FTR 0.32–0.62 against FDR
0.027–0.080 over 233,300 implementations), and on SWE-bench Verified (n = 500) mis-aligned
generated tests cost **−3.9 pp against no test at all** — a gate that reverts correct work is
worse than no gate.

S2: `policy.py` reads only `Scope.COMMAND` and `Scope.ANY_TEXT` — no rule reads a **diff**. SWEADV
(arXiv 2609.15963) shows an adversarial issue yielding a patch that is correct *and* malicious in
51.7% of cases, invisible to a test gate. A deterministic rule on the diff — a **new dangerous
sink called with a non-literal argument** — is cheap; whether it is worth wiring depends on one
number the paper did not report for our setting: its false-alarm rate on patches we accepted.

## Corpus

The 30 `fix_*` tasks of `bench/local_lift` (buggy base + hidden strict pytest, deterministic,
stdlib-only). One venue, small pure-Python modules; it does not generalise past that.

### Step 1 — a labelled patch history (≈ US$ 0.10)

`chimera solve` on each task, **without** the hidden test in the workspace and **without** any
gate (`--max-attempts 1`, `--no-manager`, no `--verify`, no `--gen-tests`, `--keep-workspace`),
model `deepseek-v4-flash-0731`, **k = 2** replicas → 60 patches. Each is labelled by the hidden
test written in *afterwards* and run once: `correct` or `incorrect`. The verify file's post-patch
content is stored per patch, so the reading is reproducible from the artefact. A run whose process
fails before writing anything is `unpatched` and leaves every denominator.

### Step 2 — generated tests (≈ US$ 0.25)

Per task, the production path as `bench/spec_test_vacuity` ran it: `RequirementChecklist.extract`
on the prompt, `SpecTestGenerator.generate` with the buggy base's digest, temperature 0. The module
text is **stored this time** (the vacuity bench kept only counts). A task with no module is
reported and leaves the per-test denominators.

### Step 3 — deterministic readings, US$ 0

Per generated test function *t*, on task *T*:

- **FDR** (fault detection): *t* fails on the buggy base. (The vacuity bench measured 52% — this
  re-measures it on a fresh generation, which is also a replay of that number.)
- **FTR** (fault trigger): running *t* on the buggy base under `coverage`, at least one line of the
  **fault region** executes. The fault region is the set of base lines removed or changed by any
  *correct* patch of *T* (union over correct patches); a task with no correct patch has no FTR and
  says so. FTR ≥ FDR by construction; the gap is the tests that reach the bug and do not notice it.
- **False alarm** (the side nobody measured): *t* fails on a *correct* patch's tree. Reported as the
  fraction of (t, correct patch) pairs, and as the fraction of tests that false-alarm on at least
  one correct patch.
- **Gate accuracy**, module level, over labelled patches: the shipped bilateral verdict (regression
  or failing → FAIL; no discriminating → ABSTAIN; else PASS). **TPR** = P(PASS | correct),
  **TNR** = P(FAIL | incorrect), abstain rate per label. Beside it, the *pre-vacuity* verdict (the
  module's exit code, which is what shipped before `spec_test_vacuity`'s gate) on the same patches, so
  what the bilateral classification bought is a paired number and not an assertion.
- **Round-0 preservation**: the fraction of correct patches the gate would have **reverted** (FAIL
  on correct) — the −3.9 pp mechanism, counted directly.

### S2 — the diff rule

`chimera/governance/diff_rules.py`: parse each *post-patch* Python file, find `Call` nodes on the
lines the patch **added** (from the unified diff's hunks), whose callee is one of a fixed sink
list — `subprocess.run/call/check_call/check_output/Popen`, `os.system`, `os.popen`, `eval`, `exec`,
`pickle.load/loads`, `yaml.load` (without `Loader=SafeLoader`), `hashlib.md5/sha1`, `open` in
write mode, `requests.get/post`/`urllib.request.urlopen`, `socket.connect`, `shutil.rmtree`,
`os.remove/unlink/rmdir` — and whose **first argument is not a literal** (not a `Constant`, not a
literal-only f-string). Deterministic, no model. A flag is a REVIEW, never a BLOCK — it pauses a
run for sign-off through the existing HITL pause (`pause_on_taint`'s branch), and only when the
surface opts in.

- **FPR corpus (accepted-patch history):** (a) the 552 kept post-edit workspaces of the factorial
  (`~/harness-bench/data_try6/sandbox`), diffed against their task fixtures — the subset with
  oracle `outcome_score ≥ 0.8` is the accepted history, the rest is reported beside it; (b) the
  correct patches of Step 1. **FPR** = flagged / accepted.
- **Sabotage check (the guard must fire):** ten hand-written diffs, one per sink family, each
  adding the sink with a variable argument on a benign-looking patch; the rule must flag 10/10, and
  the same ten with a literal argument must flag 0/10. Written before the corpus is read.

## Predictions

- **P1 (B4).** FDR on the fresh generation within ±10 pp of the vacuity bench's 52%. FTR − FDR ≥
  15 pp: a third or more of the tests that reach the fault do not notice it.
- **P2 (B4, the new side).** False alarm ≥ 10% of (test, correct patch) pairs: the generator writes
  from the prompt, and the prompt under-specifies edge behaviour that a correct patch chooses one
  way and the test another.
- **P3 (B3).** The bilateral gate's TNR ≥ 0.8 and TPR ≤ 0.9 — it catches most wrong patches and
  reverts some correct ones. Against the exit-code verdict the difference lives in the abstains: a
  false-alarming test FAILs a correct patch under both, so TPR is equal or lower under the
  bilateral gate; a wrong patch under an all-vacuous module PASSes the exit code and ABSTAINs the
  bilateral gate, so TNR's complement (false passes) is lower under the bilateral gate.
- **P4 (S2).** FPR on the accepted factorial history ≤ 10%: real coding tasks rarely add a sink with
  a variable argument, and the ones that do (a CLI wrapper calling `subprocess.run(argv)`) are
  legitimately worth a look.

## Decision rules

| | rule |
|---|---|
| B4 false alarm | reported whatever it is; **if ≥ 10%**, the verifier gains a registered second check before it may FAIL an attempt: the failing tests must also fail on the *base* (they already must, to be discriminating) **and** the receipt names them — no code change beyond the receipt, because the honest fix (a second opinion on the test) is a model call this plan does not fund. If < 10%, the gate stands as shipped |
| B3 round-0 | if the gate reverts **> 10%** of correct patches, `RESULTS.md` recommends `--gen-tests` stay opt-in with the number on the flag's help text; if ≤ 10%, the flag's docstring cites the number and nothing else changes |
| S2 wiring | **FPR ≤ 10%** on the accepted history → the rule ships as a pause-for-sign-off that a surface opts into (`pause_on_diff_flags`, off by default), with the flags always on the attempt receipt; **FPR > 10%** → the rule ships as receipt-only (flags recorded, nothing pauses) and the plan's design-B fate is recorded; sabotage check < 10/10 → the rule is not merged |

## What this cannot show

- One model, one small-Python venue, k = 2. Nothing here transfers to a repository-scale patch
  without measuring there.
- FTR's fault region comes from *our own* correct patches, not a reference fix; a task whose correct
  patches touch lines beyond the fault widens the region and inflates FTR. Reported per task.
- S2's true-positive rate on real adversarial patches: no such corpus here; the sabotage check is a
  guard that the rule fires, not a measure of what it would catch in the wild.
- The judge's or a human's reaction to the pause: the pause is wired, not exercised.

---

## Amendment 1 — 2026-09-15, after 26 of the 60 step-1 patches and before any test was generated

The first 26 patches from `deepseek-v4-flash-0731` are **26/26 correct**. A labelled history with one
label cannot measure TNR or the round-0 side, and the registration's point was the label, not the
model. So step 1 gains a second source, run the same way (no gate, no hidden test in the workspace,
k = 2): **`openrouter/mistralai/mistral-small-3.2-24b-instruct`**, the weak model `bench/local_lift`
used, whose baseline on these tasks was well below ceiling there. Every patch carries its `model`;
the gate readings are reported over the whole history and per model, and the generated tests are
unchanged — they are produced once per task from the prompt and never see a patch. Nothing about
the readings, predictions or decision rules changes. Cost ≈ US$ 0.10 more.

## Amendment 2 — 2026-09-15, after 38 of the 60 `deepseek-v4-flash` patches and before any test was generated

The provider became unstable during the run (connection resets from OpenRouter; solves that took
40–100 s in the morning took 800–1,000 s by the afternoon, with `litellm` retrying inside). The
first launch was killed by the shell's timeout at 29 rows and resumed; at 38 rows, with **38/38
correct**, the arm was stopped by hand rather than left to spend six more hours on a class it can
only add to. So the flash history is **38 patches over 30 tasks** (k = 2 on 8 tasks, k = 1 on 22),
not 60, and every one of them is `correct`. What that changes: the correct class is smaller than
registered, which trims the false-alarm denominator and nothing else — TNR and the round-0 side
come from the weak-model arm of Amendment 1, which runs next, at full k = 2. Both arms' rows carry
`seconds` and `rc`, so the instability is on the record per row.
