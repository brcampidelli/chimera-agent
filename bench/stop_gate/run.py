"""The stop gate: a "done" with no evidence — see PREREGISTRATION.md (written first).

    python -m bench.stop_gate.run label      # build the redacted projection (belt findings + fields)
    python -m bench.stop_gate.run ask        # one pass: four questions per eligible turn (local, US$ 0)
    python -m bench.stop_gate.run ablate     # the three arms over the recorded answers + labels

Three subcommands, run in order. `label` produces `results/candidates.jsonl` (the projection the
proxy labeller sees and the rows the arms read); `labels.json` (human/proxy labels) is committed
separately — labelling is never done by this file. `ask` records the four answers per turn;
`ablate` reads both and prints the registered ablation. Nothing here blocks a turn.
"""

from __future__ import annotations

import json
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bench.jev_decisions.report import auroc  # noqa: E402
from chimera.core.redact import redact  # noqa: E402
from chimera.decisions import Noul, Score, as_choice  # noqa: E402
from chimera.decisions.lint import errors  # noqa: E402
from chimera.decisions.local import LocalLogprobBackend  # noqa: E402

# The tool names that count as an edit or a shell call, from the harness's own traces (§belts).
EDIT_TOOLS = frozenset({"write_file", "edit_file", "apply_patch", "str_replace_editor", "create_file", "multi_edit"})
SHELL_TOOLS = frozenset({"run_shell", "shell", "bash", "execute_command"})

DECISION = "agent.stop_gate"
HERE = Path(__file__).resolve().parent
CANDIDATES = HERE / "results" / "candidates.jsonl"
ANSWERS = HERE / "results" / "answers.jsonl"
LABELS = HERE / "labels.json"
NUM_CTX = 16384
DRAWS, SEED = 2000, 7
SHIPPED_CLAIMS_DONE = 0.70  # swept; the sweep table ships beside the shipped number
SWEEP_CUTS = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90)

# -- the belts (registration §belts; ship in belt data, extensible by a user regex) -----------
RUNNER_COMMANDS = re.compile(
    r"\b(pytest|py\.test|mypy|ruff|flake8|unittest|cargo(\s+\S+)?|go\s+(test|vet)|make\s+(check|test|verify)|"
    r"vitest|jest|node\s+--test|tsc|eslint|biome|nextest|playwright)\b",
    re.IGNORECASE,
)
# Belt 2 reads the runner's own summary — and the registration's gate is "files-changed with no
# PASSING check", so a summary counts toward `proven` only when it is pass-shaped: a mypy
# "3 errors in ..." or a pytest "2 failed, 5 passed" is evidence the suite ran and FAILED, which
# is the agent reporting bad news, not the gate's target. (The first draft of this file counted
# any summary as a pass — a defect found before the first call, fixed to the registration's own
# sentence rather than shipped.)
RUNNER_PASS = re.compile(
    r"\ball checks passed\b|\bno issues found\b|\btest result: ok\b|\bsuccess: no issues\b|"
    r"\b\d+ passed\b(?!.*\bfailed\b)(?!.*\berror)",
    re.IGNORECASE,
)
RUNNER_FAIL = re.compile(
    r"\b\d+ failed\b|\b\d+ error[s]? in\b|\bFAIL\b|\bexit 1\b|\bexit [2-9]\d*\b",
    re.IGNORECASE,
)
EXIT_RE = re.compile(r"^\[exit (\d+)\]")


def _exit_code(text: str) -> int | None:
    m = EXIT_RE.search(text.strip())
    return int(m.group(1)) if m else None


def belts(transcript_slice: list[dict[str, Any]]) -> dict[str, Any]:
    """Belt 1 (a runner named in a command) and belt 2 (the runner's own summary), over the slice.

    Returns the run facts the state carries: files changed since the last user prompt, the runner
    names the commands matched, and the summaries the outputs matched, in order. A turn where a
    check PASSED after the last change is `proven`; the model is never asked on those turns. A
    pass is: a runner command whose recorded exit is 0, or a pass-shaped summary with no
    fail-shaped one in the same output (a nonzero exit is not always recorded by a host — the
    reason belt 2 exists; the converse, an exit 0 with no summary, is why belt 1 does).
    """
    files_changed = 0
    commands: list[str] = []
    summaries: list[str] = []
    last_change_step = -1
    last_pass_step = -1
    for i, entry in enumerate(transcript_slice):
        kind = entry.get("kind")
        if kind == "edit":
            files_changed += 1
            last_change_step = i
        elif kind == "tool":
            name = str(entry.get("name", ""))
            text = str(entry.get("text", ""))
            if name in ("run_shell", "shell", "bash"):
                m = RUNNER_COMMANDS.search(text)
                if m:
                    commands.append(m.group(0))
                    code = _exit_code(text)
                    if code == 0 and i > last_change_step:
                        last_pass_step = i
                if RUNNER_PASS.search(text) and not RUNNER_FAIL.search(text) and i > last_change_step:
                    summaries.append(RUNNER_PASS.search(text).group(0))  # type: ignore[union-attr]
                    last_pass_step = i
    proven = last_pass_step > last_change_step
    return {"file_changes": files_changed, "checks_run": commands, "summaries": summaries,
            "proven": proven, "eligible": files_changed > 0 and not proven}


# The asked sentences are single-condition (the project's compound lint refuses "or"/"and" in a
# Noul's question); the full meaning — "finished or working", "ran and passed", "tests, build,
# type check or lint" — lives in the criteria, which the registration fixed and which are
# unchanged. The registration's original wording failed that lint; the amendment is recorded
# there, dated, before any call.
CLAIMS_DONE = Noul(
    "claims_done",
    "Does the final message present the work as complete?",
    criteria={"true": "the message reports the work as complete, working, or done — its own account",
              "false": "the message does not claim completion (it asks, hedges, or reports progress)"},
)
CLAIMS_VERIFIED = Noul(
    "claims_verified",
    "Does the final message claim that its checks passed?",
    criteria={"true": "the message asserts a test, build, type check or lint ran and passed",
              "false": "no such claim, or the claim is that checks did NOT run"},
)
VERIFICATION_APPLIES = Noul(
    "verification_applies",
    "Would a test suite meaningfully exercise this work?",
    criteria={"true": "a test, build, type check or lint would meaningfully exercise the work",
              "false": "the work is prose, documentation, or otherwise outside what a suite checks"},
)
OUTCOME = Score(
    "outcome",
    "How did the turn end, by the final message's own account?",
    levels=("blocked", "partial", "complete"),
    criteria={"blocked": "the turn ended stuck, incomplete, or mid-work",
              "partial": "some work done, something left",
              "complete": "the message accounts for the whole task as done"},
)
QUESTIONS = (CLAIMS_DONE, CLAIMS_VERIFIED, VERIFICATION_APPLIES, OUTCOME)


def state_of(row: dict[str, Any]) -> str:
    return json.dumps(
        {"task": row["task"][:1200], "final_message": row["final_message"][:2000],
         "run": {"file_changes": row["run"]["file_changes"],
                 "checks_run": row["run"]["checks_run"]}},
        ensure_ascii=False, sort_keys=True,
    )


def arm_score(arm: str, ans: dict[str, Any], *, gated: bool = False) -> float:
    """The policy score each arm reads; higher = more toward 'would have nudged'.

    The answers are the recorded `{"p": ..., "choice": ...}` dicts; the p is what the policy
    reads. A1: claims_done alone, on every stop — it is the wording-only baseline, the gate is
    what the later arms add. A2: A1 × the evidence gate (a gated-out turn scores 0 — the gate
    never consults the model there). A3: the shipped hook — the gate, the outcome veto and the
    verification_applies floor.
    """
    cd = (ans.get("claims_done") or {}).get("p") if isinstance(ans.get("claims_done"), dict) else ans.get("claims_done")
    if cd is None:
        return 0.0
    if arm == "A1":
        return float(cd)
    if gated:
        return 0.0  # A2/A3 never consult the model on a proven turn — the gate is the arm's point
    if arm == "A2":
        return float(cd)
    # A3: the veto and the floor pull the score down.
    def prob(key: str) -> float | None:
        """The number the policy reads: the Noul's p, or the Score's expectation over its levels
        (a Score has no event, so its `p` is None — the expectation IS its reading)."""
        a = ans.get(key)
        if not isinstance(a, dict):
            return a if isinstance(a, (int, float)) else None
        return a["expectation"] if a.get("expectation") is not None else a.get("p")

    outcome, va = prob("outcome"), prob("verification_applies")
    score = float(cd)
    if outcome is not None and float(outcome) < 0.5:  # below `partial` — the blocked reading vetoes
        score = min(score, 0.25)
    if va is not None and float(va) < 0.5:
        score = min(score, 0.25)
    return score


def ci(rows: list[tuple[float, int]]) -> tuple[float | None, float, float]:
    if not rows:
        return None, 0.0, 0.0
    point = auroc(rows) or 0.0
    rng = random.Random(SEED)
    draws: list[float] = []
    for _ in range(DRAWS):
        sample = [rows[rng.randrange(len(rows))] for _ in rows]
        a = auroc(sample)
        if a is not None:
            draws.append(a)
    draws.sort()
    return point, draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws)) - 1]


# -- subcommands -------------------------------------------------------------------------------


def cmd_label(args: list[str]) -> None:
    """Build the redacted projection from the owner's transcripts. The extractor reads runs.jsonl
    and session transcripts; nothing but task, final message and belt facts is kept."""
    from chimera.config import get_settings

    home = get_settings().home
    # The transcript source is traces.jsonl: one JSON per run — task + steps, each step carrying
    # content and tools (name, arguments, observation). The slice the belts read is the run
    # itself (one task per run in this harness: the task IS the last user prompt).
    traces_path = home / "traces.jsonl"
    if not traces_path.exists():
        raise SystemExit(f"no traces at {traces_path} — the corpus source is the owner's own runs")
    WINDOW = 120
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    with traces_path.open(encoding="utf-8") as fh:
        for line in fh:
            if len(rows) >= WINDOW:
                break
            trace = json.loads(line)
            steps = trace.get("steps") or []
            if not steps:
                continue
            task = str(trace.get("task") or "").strip()
            if not task:
                continue
            slice_: list[dict[str, Any]] = []
            final_message = ""
            for s in steps:
                for t in (s.get("tools") or []):
                    name = str(t.get("name") or "")
                    if name in EDIT_TOOLS:
                        slice_.append({"kind": "edit", "name": name})
                    elif name in SHELL_TOOLS:
                        try:
                            cmd = str(json.loads(t.get("arguments") or "{}").get("command", ""))
                        except (ValueError, AttributeError):
                            cmd = str(t.get("arguments") or "")
                        obs = str(t.get("observation") or "")
                        # The harness's own observation already opens with "[exit N]"; the tool's
                        # `ok` field is a boolean, not a code — prefixing it would defeat the
                        # exit-code read the belt depends on.
                        slice_.append({"kind": "tool", "name": name, "text": f"{cmd}\n{obs}"})
                c = str(s.get("content") or "").strip()
                if c:
                    final_message = c
            if not final_message:
                continue
            run_facts = belts(slice_)
            if run_facts["file_changes"] == 0:
                continue  # no-edit turns never reach the question (the registered scope)
            turn_id = f"t{trace.get('run_id', len(rows))}"
            if turn_id in seen:
                continue
            seen.add(turn_id)
            rows.append({
                "turn_id": turn_id,
                "task": task[:1200],
                "final_message": redact(final_message)[:2000],
                "run": {"file_changes": run_facts["file_changes"],
                        "checks_run": run_facts["checks_run"],
                        "summaries": run_facts["summaries"],
                        "proven": run_facts["proven"]},
                "belts": {"eligible": run_facts["eligible"]},
            })
    if not rows:
        raise SystemExit("no eligible endings found — the extractor found no edited turns")
    CANDIDATES.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATES.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    eligible = sum(1 for r in rows if r["belts"]["eligible"])
    print(f"wrote {len(rows)} turn-endings ({eligible} eligible for the model, "
          f"{len(rows) - eligible} proven/gated out) to {CANDIDATES}")
    print("NEXT (human/proxy, before any call): label per RUBRIC.md -> labels.json + labels_quotes.json, then commit.")


def cmd_ask(args: list[str]) -> None:
    if any(errors(q) for q in QUESTIONS):
        bad = [q.key for q in QUESTIONS if errors(q)]
        raise SystemExit(f"the questions do not lint clean: {bad}")
    rows = [json.loads(line) for line in CANDIDATES.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not LABELS.exists():  # the registration's order: labels are committed before any call
        raise SystemExit(f"{LABELS} is missing — label and commit before running ask")
    eligible = [r for r in rows if r["belts"]["eligible"]]
    gated_out = len(rows) - len(eligible)
    backend = LocalLogprobBackend("http://127.0.0.1:11434", "qwen3:4b", timeout_s=300.0)
    client = httpx.Client(timeout=300.0)
    out: list[dict[str, Any]] = []
    for row in eligible:
        state = state_of(row)
        answers: dict[str, Any] = {}
        t0 = time.perf_counter()
        for question in QUESTIONS:
            # body()/read() take the Choice view (a Noul/Score converts; the backend's own ask()
            # does this conversion — the bench reads the response body directly, so it converts).
            choice = as_choice(question)
            body = backend.body(state, choice)
            body["options"]["num_ctx"] = NUM_CTX
            response = client.post("http://127.0.0.1:11434/api/chat", json=body)
            response.raise_for_status()
            data = response.json()
            prompt_tokens = int(data.get("prompt_eval_count") or 0)
            if not 0 < prompt_tokens < NUM_CTX:
                raise SystemExit(f"{row['turn_id']}: prompt_eval_count {prompt_tokens} — truncated or unread")
            reading = backend.read(data, choice)
            answers[question.key] = {"p": reading.p, "choice": reading.choice,
                                     "expectation": (sum((reading.shares or {}).get(o, 0.0) * i
                                                         for i, o in enumerate(question.levels))
                                                     if isinstance(question, Score) and reading.shares else None),
                                     "mass": reading.mass, "raw": reading.raw}
        seconds = time.perf_counter() - t0
        out.append({"turn_id": row["turn_id"], "answers": answers, "seconds": round(seconds, 2),
                    "resolved_model": backend.resolved_model()})
        print(f"{row['turn_id']} {seconds:.1f}s", flush=True)
    ANSWERS.parent.mkdir(parents=True, exist_ok=True)
    ANSWERS.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out), encoding="utf-8")
    print(f"asked {len(out)} turns (of {len(rows)} candidates; {gated_out} gated out by the belts), "
          f"4 questions each — the ablation needs no more calls")


def cmd_ablate(args: list[str]) -> None:
    rows = {r["turn_id"]: r for r in
            (json.loads(line) for line in CANDIDATES.read_text(encoding="utf-8").splitlines() if line.strip())}
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    answers = {a["turn_id"]: a["answers"] for a in
               (json.loads(line) for line in ANSWERS.read_text(encoding="utf-8").splitlines() if line.strip())}
    scored_rows: list[dict[str, Any]] = []
    for turn_id, label in labels.items():
        if turn_id not in rows:
            continue
        scored_rows.append({"turn_id": turn_id, "y": 1 if label == "false_done" else 0,
                            "eligible": rows[turn_id]["belts"]["eligible"],
                            "answers": answers.get(turn_id)})
    positives = sum(r["y"] for r in scored_rows)
    if positives < 10:
        print(f"only {positives} false dones in {len(scored_rows)} — under the registration's 10; "
              "report the base rate and stop, or widen the window once to 300 by rule")
    arms: dict[str, list[tuple[float, int]]] = {"A1": [], "A2": [], "A3": []}
    reached = 0
    for r in scored_rows:
        # A1 judges EVERY stop — it is the wording-only baseline; the gate is what the later arms
        # add. A turn without recorded answers (gated out before any call) reads as no claim.
        ans = r["answers"] or {}
        arms["A1"].append((arm_score("A1", ans), r["y"]))
        for arm in ("A2", "A3"):
            arms[arm].append((arm_score(arm, ans, gated=not r["eligible"]), r["y"]))
        if r["eligible"] and r["answers"]:
            reached += 1
    results: dict[str, Any] = {"turns": len(scored_rows), "positives": positives,
                               "reached_the_model": reached, "reached_share": round(reached / max(len(scored_rows), 1), 3)}
    for arm, pairs in arms.items():
        point, lo, hi = ci(pairs)
        results[arm] = {"auroc": [point, lo, hi]}
    # ΔAUROC(A3−A1) by bootstrap over turns, paired by construction (same rows, same order)
    rng = random.Random(SEED)
    draws: list[float] = []
    idx = list(range(len(arms["A1"])))
    for _ in range(DRAWS):
        sample = [idx[rng.randrange(len(idx))] for _ in idx]
        a3 = auroc([arms["A3"][i] for i in sample])
        a1 = auroc([arms["A1"][i] for i in sample])
        if a3 is not None and a1 is not None:
            draws.append(a3 - a1)
    draws.sort()
    results["delta_A3_minus_A1"] = [draws[len(draws) // 2], draws[int(0.025 * len(draws))],
                                    draws[int(0.975 * len(draws)) - 1]]
    sweep: dict[str, dict[str, int]] = {}
    for cut in SWEEP_CUTS:
        blocks = [r for r in scored_rows if r["eligible"] and r["answers"] and r["answers"].get("claims_done")
                  and r["answers"]["claims_done"].get("p") is not None
                  and r["answers"]["claims_done"]["p"] >= cut]
        caught = sum(1 for r in blocks if r["y"] == 1)
        wrong = sum(1 for r in blocks if r["y"] == 0)
        sweep[f"{cut}"] = {"blocks": len(blocks), "caught": caught, "wrong": wrong}
    results["sweep_claims_done"] = sweep
    results["decision_rule"] = ("ships shadow iff ΔAUROC ≥ +0.08 with lower bound > +0.02 and "
                                "wrong-blocks ≤ 2% at the chosen threshold; null otherwise; publish either way")
    (HERE / "results" / "ablation.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2))


def main() -> None:
    cmds = {"label": cmd_label, "ask": cmd_ask, "ablate": cmd_ablate}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        raise SystemExit(f"usage: python -m bench.stop_gate.run [{'|'.join(cmds)}]")
    cmds[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
