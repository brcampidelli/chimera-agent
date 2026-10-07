# Multi-agent policy

A decision record, written 2026-10-02 from study 28 (items MA9 and MA4) so the next study does not
reopen what was already measured. It says what Chimera does by default when one agent could become
several, what it refuses to build, which guarantees live in code rather than in prose, and what
would reopen each line. Every claim cites a bench in this repository or a paper by arXiv id.

## The rule

**The topology is flat: one strong agent does the work.** A second agent is allowed in four forms
only, each for the reason it was measured to pay, each turned on by an explicit request with its
cost shown first, and each answerable to a single-agent arm at equal cost — the same model calls
or the same US$, with the pre-registration saying which ([`bench/PROTOCOL.md`](../bench/PROTOCOL.md) §10).

## What we measured

The numbers below are quoted from the files in the source column; a test
(`tests/test_the_multi_agent_policy_quotes_its_benches.py`) fails if a quoted figure leaves its file.

| question | measured | source |
|---|---|---|
| Does a hierarchy beat one agent given the same number of calls? | Not on a `3B` backbone on every role over 30 synthetic read-heavy extraction tasks, and only as a direction: `pass^3` `−26.7 pp`, interval `[−36.2, −4.2]`, discordant `2 for the hierarchy, 10 for the single agent`, with the point estimate inside the `33–47%` flip floor — so the registered sentence stays "one agent that re-reads the documents does as well or better on tasks like these". The arms held equal *calls*, not tokens: the single agent spent `13,611` tokens per task against the hierarchy's `1,962`, about 7× | [hierarchy_equal_calls](../bench/hierarchy_equal_calls/RESULTS.md) |
| Where does the hierarchy lose? | On a weak synthesiser: with `3B` on every role, hierarchy `0.28` / `0.10` against workers alone `0.44` / `0.23` (`pass@1` / `pass^3`, 30 tasks). With the production synthesiser `claude-opus-5` over the same `3B` workers, the hierarchy scored `0.63` / `0.50` on ten tasks, level with the workers alone (`0.57` / `0.50`); no single-agent arm at equal calls has run on that tier | [hierarchy_equal_calls](../bench/hierarchy_equal_calls/RESULTS.md) |
| Does context isolation pay? | Multi-step reading of large documents: token reduction `+66.5%`, pass rate `100%` in both arms (n=6) | [hierarchy_multistep](../bench/hierarchy_multistep/RESULTS.md) |
| …and in single-shot reading of small documents? | No: the split cost more, a token "reduction" of `−46.9%` | [hierarchy](../bench/hierarchy/RESULTS.md) |
| Are three panel models three votes? | No: `34 of 50 items unanimous where 16.5 were expected`, `1.46 independent votes`; the replay finds mean error correlation `+0.535` (pairwise correctness κ `+0.348`, `+0.522`, `+0.714`) | [panel_correlation](../bench/panel_correlation/RESULTS.md), [fusion_admissibility](../bench/fusion_admissibility/RESULTS.md) |
| Can an LLM Manager gate an attempt no executable check decided? | It rejects most correct work: approves `47/246 = 0.19` true successes and `2/139 = 0.01` false ones with the diff, `5/246 = 0.02` with prose only. Of the 385 rows, `123` showed the Manager a "no productive change" that was a blind spot of the reconstruction; on the `262` with real evidence it approves `47/197 = 0.24` — a correct attempt rejected three times in four | [manager_diff](../bench/manager_diff/RESULTS.md), [manager_p](../bench/manager_p/RESULTS.md) |
| Does a planner role help the solve loop? | No: `+0.003` on the oracle, `[−0.022, +0.029]`, 23 tasks × 8 arms × k=3 | [harness_bench](../bench/harness_bench/RESULTS.md) |
| Does a cross-family reviewer find real defects? | Yes, and it also flags clean code: recall `39/40 = 97.5%`, `8.5` findings per ten clean diffs | [review_reviewer](../bench/review_reviewer/RESULTS.md) |
| Is a sequential retry an independent attempt? | No: the second attempt recovered `4 of 21` where independence predicts `12.5` | [retry_contamination](../bench/retry_contamination/RESULTS.md) |
| Does the blind fusion judge keep faith with its sources? | Yes on the corpora tried: `240/240` named, `119/120` blind; `0 / 180` propagations on prose | [judge_blind](../bench/judge_blind/RESULTS.md), [judge_blind_prose](../bench/judge_blind_prose/RESULTS.md) |
| Does fusion beat one model at equal budget? | Not measured: the pilot sat at ceiling, `5/5` in every arm but the clock-bound one | [fusion_paired](../bench/fusion_paired/RESULTS.md) |

## The literature, by what each paper measured

- **2609.04217** — at an equal number of model calls, a Planner-Executor-Critic team scored 0.769
  against a single agent's 0.754 (p = 0.80) on ALFWorld; all realised value was in the executor.
- **2609.35875** — 23 *small* language models, 5,500+ runs: at matched budget, debate ties or
  loses to self-consistency at 3.4× the tokens; personas reduce accuracy; mixed-model teams lose to
  the majority vote of their own members; nearly all of debate's benefit is in the first exchange;
  and a silent context overflow had made debate's deficit (−1.8 points became parity once fixed).
- **2609.03718** — with information access and repair budget fixed, one generic harness scored
  96.4% against 88.2% for multi-agent systems on FoamBench; execution-feedback repair was the lift
  (71.8% → 96.4%).
- **2609.13890** — hierarchical collaboration over a single agent: +2.4 points on the easiest third
  of 614 coding problems, +21.1 on the hardest, at about ten times the tokens. (Its +4.1 for
  difficulty-aware selection is measured against *always-hierarchical*, not against one agent.)
- **2609.19759** — multi-agent helps on long-horizon tasks with sparse dependencies; a single agent
  is better on tightly coupled sequential work; more agents or deeper recursion did not
  consistently help.
- **2609.17464** — each delegation hop keeps C = 0.571 of what the level below found (16,082 hops);
  flat is optimal for yield.
- **2609.14767** — a Manager allowed to send work back lost to a flat team on Utility (d = 0.42,
  p = 0.009) and cost 51.5% more tokens for no quality gain.
- **2609.04270** — same-model self-review falsely rejected 35% of its own correct answers; a
  cross-family reviewer, 2% — and lifted accuracy from 52% to 64%.
- **2605.07073** (TeamBench) — LLM verifiers approved 49.4% of submissions the deterministic grader
  failed; with roles enforced only by prompt, verifiers tried to edit the executor's code 3.6× as
  often.
- **2609.17306** — enlarging a model pool often fell below the best single model; selection within
  one family did best.
- **2608.24069**, **2609.05663**, **2608.27734** — multi-agent trading: no architecture was
  inherently robust to a poisoned signal; in six months of two production fleets the operating layer
  set behaviour more than strategy text, neither fleet had a directional edge, and frontier models'
  decision quality was indistinguishable; honest evaluation rejected every LLM-discovered strategy.

## The four sanctioned forms

1. **A read-only sub-agent for context isolation.** It reads, the caller keeps its conclusion, it
   cannot write and cannot spawn. The regime that pays — multi-step reading over large documents,
   against single-shot reading where the split costs tokens (above) — was measured on the
   hierarchy's bounded workers, not on the explorer or `spawn_subagent`, which no bench measures
   (study 28 MA5); carrying it over is an inference by analogy. Today this is `ExploreRepositoryTool`, off by default on
   the Code screen; it becomes a default only if a census of real turns finds the paying regime often
   enough (study 28 MA5). `spawn_subagent` (only with `solve --subagents`) is wider than this form —
   the caller may grant it write tools — and stays opt-in and unmeasured.
2. **Parallel independent attempts with an executable verifier, as escalation.** Independent
   because sequential retries are not (`retry_contamination`); with an executable verifier because
   an LLM verifier approves work a grader fails (2605.07073); as escalation — after a first attempt
   fails, or on a task known to be hard — because the gain lives in the hardest third (2609.13890).
   The result is **one verified attempt landed whole**, never a merge of two verified attempts that
   no verifier ran together — which is what `IsolatedCrew` does today when two workers pass: it
   copies each one's non-conflicting files and runs no verify on the result (tracked as study 28
   MA1). Not measured against the three-attempt `solve` at equal cost; until it is, it is not a
   default.
3. **A cross-family reviewer reading the diff, advisory.** `chimera review` with a reviewer from
   another family (`review_reviewer`; 2609.04270). Its findings inform; they decide only when
   anchored on a deterministic check (a failing test, a line the diff touched). At 8.5 findings per
   ten clean diffs, a reviewer with a veto would block clean work.
4. **Batch fan-out over independent units in worktrees.** Units the user names (`solve-batch`,
   parallel kanban lanes), one worktree each, each mergeable on its own, conflicts reported, never
   resolved by a model. This is parallelism of independent work, not a team on one task.

## What is out, and why

| out | the measured reason |
|---|---|
| An LLM manager or supervisor with a veto where nothing executes | rejects about three in four correct attempts on the rows with real evidence (`manager_diff`); flat beat loop-back authority (2609.14767) |
| A planner role in front of a strong executor | `+0.003`, inside the noise, at a cost (`harness_bench`); 2609.04217 |
| Debate or multi-round peer review | ties or loses to self-consistency at 3.4× tokens, on small language models (2609.35875) |
| Personas or "personality" roles | reduce accuracy on small language models (2609.35875); useful diversity is structural |
| A bigger or more mixed panel as more independent votes | three models carry 1.46 votes (`panel_correlation`); 2609.17306 |
| Deep hierarchies | each hop keeps 0.571 (2609.17464); on a weak (3B) synthesiser the synthesis step is where our values were lost |
| A multi-agent trading desk on the eToro mandate | 2608.24069, 2609.05663, 2608.27734; trading numbers stay in deterministic scripts |
| Proactive fan-out in the Discord bot or cron jobs | no measurement asks for it, and every hop is a place a number can be corrupted |

## Invariants that are code, not prose

1. **No permission laundering between agents.** A sub-agent draws its tools from the caller's
   governed, ledgered registry at call time, gets a subset of the caller's allowlist, and never gets
   the spawn tool (depth 1): `chimera/core/subagent.py`, `tests/test_subagent.py`,
   `tests/test_guards_that_did_not_cover.py`. *Open:* the explorer builds its own read-only registry,
   so its inner reads are not governed (stated in `chimera/api/code_api.py`).
2. **Cost never rises by itself.** Every delegated call lands on the bill and the ceiling of the run
   that started it (`run_nested` in `chimera/core/agent.py`; `tests/test_the_explorer_is_on_the_bill.py`,
   `tests/test_crew_respects_the_ceiling.py`, `tests/test_the_crew_bill_arrives.py`). Modes that
   multiply calls are off until turned on: `CHIMERA_AUTO_FUSE` defaults to false, the explorer to
   off, `spawn_subagent` to absent. *Open:* the solve loop's planner and Manager still run by
   default (`use_planner`, `use_manager`), both measured above (study 28 MA2).
3. **Multi-agent only on explicit request, cost shown first.** Every multi-agent mode is a flag or
   a screen the user opens, and the Orchestration screen shows the workers, the token budget per
   worker and the measured sentence from `hierarchy_equal_calls` before anything runs
   (`apps/desktop/src/components/orchestration/PlanPreview.tsx`). Apart from the solve loop's
   planner and Manager (2, open), no surface adds agents on its own initiative; one that starts to
   must ask first and state the cost.
4. **A worker's output carries taint like external data.** A hierarchy envelope is stamped tainted
   when its worker's ledger saw untrusted input (`chimera/orchestration/hierarchy.py`); crew workers
   on one task share a taint view (`tests/test_shared_taint.py`); a sub-agent's fetches land on the
   caller's ledger because it uses the caller's tools. Passing through a second agent never cleans
   taint — the explorer gap in 1 being the stated exception.

## What reopens a line

A row in "What is out" moves only on a pre-registered run with a single-agent arm at equal cost
whose interval excludes zero (`bench/PROTOCOL.md` §10). A paper is a reason to run that bench, not
a substitute for it.
