"""Measure Chimera's fusion judge against human-labelled review comments.

Read `PREREGISTRATION.md` first — the sample, the metric, the baseline and the decision rule were
fixed there before this file called a model. This runner only executes that plan.

    python bench/review_judge/run_judge.py --fetch     # cache the diffs (no model calls)
    python bench/review_judge/run_judge.py --dry-run   # build every prompt, call nothing
    python bench/review_judge/run_judge.py             # the pilot

The judge is the model this install actually uses for fusion, because measuring one we do not ship
would answer a question nobody asked.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

#: `gh` on PATH, or its Windows executable when this runs under WSL against a Windows checkout.
GH = shutil.which("gh") or shutil.which("gh.exe") or "gh"

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "aacr.jsonl"
DIFFS = HERE / "data" / "diffs"
OUT = HERE / "results"

SEED = 20260819
PER_LABEL = 60
SLICE = "Diff Level"

#: How much of the file's patch the judge is shown. The whole patch can be thousands of lines; the
#: comment points at a line range, and a judge that has to find the needle is being measured on
#: retrieval rather than on judgement.
WINDOW = 60


@dataclass
class Item:
    """One labelled comment, with the diff it was written about."""

    row_id: int
    repo: str
    pr: int
    # The dataset's field names invert the API's: `pr_source_commit` is the PR's BASE (verified
    # against `repos/…/pulls/N` — the PR reported it as `base.sha`) and `pr_target_commit` is a
    # commit on the branch. Compared the wrong way round, GitHub answers with 300 unrelated files
    # and the commented file is not among them; the right way round it answers with 15 and it is.
    base: str   # pr_source_commit
    head: str   # pr_target_commit
    path: str
    from_line: int
    to_line: int
    note: str
    label: int  # 1 = a correct comment, 0 = a false positive
    source_model: str
    language: str
    patch: str = ""


# --- the sample ----------------------------------------------------------------------------------


def load_rows() -> list[dict[str, Any]]:
    if not DATA.exists():
        sys.exit(f"missing {DATA} — run the download step in the README first")
    return [json.loads(line) for line in DATA.read_text(encoding="utf-8").splitlines() if line.strip()]


def sample(rows: list[dict[str, Any]]) -> list[Item]:
    """The pre-registered draw: Diff Level only, balanced, stratified by source model, seeded."""
    pool = [r for r in rows if r.get("context") == SLICE]
    rng = random.Random(SEED)

    picked: list[dict[str, Any]] = []
    for label in (1, 0):
        by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in pool:
            if row["label"] == label:
                by_model[row.get("source_model") or "?"].append(row)
        for group in by_model.values():
            rng.shuffle(group)

        # Round-robin across source models so one model's failure mode cannot fill the arm.
        models = sorted(by_model)
        rng.shuffle(models)
        chosen: list[dict[str, Any]] = []
        while len(chosen) < PER_LABEL:
            before = len(chosen)
            for model in models:
                if by_model[model] and len(chosen) < PER_LABEL:
                    chosen.append(by_model[model].pop())
            if len(chosen) == before:  # pool exhausted
                break
        picked += chosen

    items = []
    for row in picked:
        owner_repo, _, pr = str(row["pr_url"]).partition("/pull/")
        items.append(
            Item(
                row_id=int(row.get("__index__", 0)),
                repo=owner_repo.replace("https://github.com/", ""),
                pr=int(pr),
                base=str(row["pr_source_commit"]),
                head=str(row["pr_target_commit"]),
                path=str(row["path"]),
                from_line=int(row["from_line"]),
                to_line=int(row["to_line"]),
                note=str(row["note"]),
                label=int(row["label"]),
                source_model=str(row.get("source_model") or "?"),
                language=str(row.get("project_main_language") or "?"),
            )
        )
    return items


# --- the diffs -----------------------------------------------------------------------------------


def fetch_patches(items: list[Item], *, report=print) -> None:
    """Cache the diff each comment was actually written about, keyed by the dataset's own commits.

    Not the pull request's CURRENT files, which is what the first version asked for and is the wrong
    question: a PR that was rebased or force-pushed after the dataset was collected no longer
    contains the file the comment points at, and 28 of 120 sampled rows came back empty for exactly
    that reason. Worse than the drop rate is what a partial fix would have done — served the judge
    today's version of the file and graded its answer about code the comment was never written for.

    `compare/<target>...<source>` reproduces the historical state. It paginates, and it must: the
    first PR checked had 300+ files and the one being asked about was not on page one.
    """
    DIFFS.mkdir(parents=True, exist_ok=True)
    wanted = sorted({(it.repo, it.base, it.head) for it in items})
    report(f"[diffs] {len(wanted)} commit ranges to cache")

    for n, (repo, base, head) in enumerate(wanted, 1):
        cache = DIFFS / f"{repo.replace('/', '__')}__{base[:10]}__{head[:10]}.json"
        if cache.exists():
            continue
        merged: list[dict[str, Any]] = []
        for page in range(1, 11):  # 1000 files is far past any diff a comment is written about
            done = subprocess.run(
                [GH, "api", f"repos/{repo}/compare/{base}...{head}?per_page=100&page={page}",
                 "--jq", "[.files[]? | {filename, patch}]"],
                capture_output=True, text=True, timeout=180,
            )
            if done.returncode != 0 or not (done.stdout or "").strip():
                if page == 1:
                    report(f"[diffs] {repo} {base[:8]}...{head[:8]}: {(done.stderr or '').strip()[:100]}")
                break
            try:
                chunk = json.loads(done.stdout)
            except json.JSONDecodeError:
                break
            merged += chunk
            if len(chunk) < 100:
                break
            time.sleep(0.2)
        cache.write_text(json.dumps(merged), encoding="utf-8")
        if n % 10 == 0:
            report(f"[diffs] {n}/{len(wanted)}")
        time.sleep(0.2)


def attach_patches(items: list[Item]) -> tuple[list[Item], list[Item]]:
    """Split into (usable, dropped). A row whose diff could not be fetched is DROPPED and counted —
    never silently replaced with another draw, which would quietly re-roll the sample."""
    usable, dropped = [], []
    for item in items:
        cache = DIFFS / f"{item.repo.replace('/', '__')}__{item.base[:10]}__{item.head[:10]}.json"
        files = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else []
        patch = next((f.get("patch") or "" for f in files if f.get("filename") == item.path), "")
        if not patch:
            dropped.append(item)
            continue
        item.patch = window(patch, item.from_line, item.to_line)
        usable.append(item)
    return usable, dropped


def window(patch: str, from_line: int, to_line: int) -> str:
    """The hunk around the commented lines. Falls back to the head of the patch when the line numbers
    cannot be located, which is honest: the judge then sees less, and that shows up as a wrong answer
    rather than as a crash."""
    lines = patch.splitlines()
    current = 0
    anchor = None
    for i, line in enumerate(lines):
        header = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", line)
        if header:
            current = int(header.group(1))
            continue
        if line.startswith("-"):
            continue
        if from_line <= current <= to_line and anchor is None:
            anchor = i
        current += 1
    if anchor is None:
        return "\n".join(lines[: WINDOW * 2])
    start = max(0, anchor - WINDOW // 2)
    return "\n".join(lines[start : start + WINDOW])


# --- the question --------------------------------------------------------------------------------

_HEAD = (
    "You are reviewing a code-review comment, not the code. Decide whether the comment identifies a "
    "REAL defect in the diff shown.\n\n"
)

#: The variable under test, and the only thing that differs between the arms. Fixed in
#: PREREGISTRATION-arms.md before arm B ran.
_STANCE = {
    "cautious": (
        "The two mistakes are not equally bad. Keeping a wrong comment costs a reader a few seconds. "
        "Rejecting a correct one destroys a real finding and nobody sees it again. When your evidence "
        "falls short, APPROVE.\n\n"
    ),
    "neutral": "Judge the comment on its merits.\n\n",
}

_TAIL = (
    "Reject only when you can point at the reason: the code the comment describes is not in this "
    "diff, or a line of the diff contradicts its central claim.\n\n"
    "Answer with JSON and nothing else, with the keys in this order:\n"
    '{"reason": "<one line, the evidence>", "verdict": "approve" | "reject"}'
)

#: Arm C's rubric. Two grounds added, both from reading the 41 comments arms A and B both approved:
#: twelve of them were TRUE and not defects — praise, wording, scope — and the old footer had no way
#: to reject those, so approving them was obligatory rather than mistaken. See
#: PREREGISTRATION-rubric.md, written before this ran.
_GROUND_NO_DEFECT = (
    "  - it asserts no defect at all — it praises the change, restates what the diff does, or "
    "expresses a preference about naming, wording, style or structure;\n"
)
_GROUND_PRE_EXISTING = (
    "  - what it reports was already there before this diff and was not introduced by it.\n"
)
_NOT_A_FINDING = (
    "A true statement is not a finding. A suggestion to add something is not evidence that its "
    "absence is a defect.\n\n"
)

_ANSWER = (
    "Answer with JSON and nothing else, with the keys in THIS order — the counterargument is written "
    "before the verdict because it has to inform it, not decorate it:\n"
    '{"strongest_counterargument": "<the best case that this comment is NOT a real defect, citing '
    'the diff; write it even when you end up approving>", "reason": "<one line>", '
    '"verdict": "approve" | "reject"}'
)


def _rubric(*, no_defect: bool, pre_existing: bool) -> str:
    """The split rubric, with either extra ground switched off.

    Arm C carried both and produced 60.4% recall at 38.5% false rejection — a real axis at an
    unusable operating point, and no way to tell which ground bought the catches and which destroyed
    the correct findings. D and E take that apart; assembling them from the same pieces is what makes
    them comparable to C rather than to a rewrite. See PREREGISTRATION-grounds.md.
    """
    grounds = (
        "  - the code the comment describes is not in this diff;\n"
        "  - a line of the diff contradicts its central claim;\n"
    )
    if no_defect:
        grounds += _GROUND_NO_DEFECT
    if pre_existing:
        grounds += _GROUND_PRE_EXISTING
    return (
        "Two questions, and the second is the one that decides:\n"
        "  1. Is the comment's premise TRUE of this diff?\n"
        "  2. Does it report a DEFECT introduced by this diff?\n\n"
        "Reject when any of these holds:\n" + grounds + "\n"
        + (_NOT_A_FINDING if no_defect else "")
        + _ANSWER
    )


_TAIL_SPLIT = _rubric(no_defect=True, pre_existing=True)


def system_prompt(arm: str) -> str:
    """The prompt for one arm.

    Arms A and B differ only in stance and share `_TAIL`; arm C keeps A's stance and replaces the
    rubric, because the reading of what A and B both missed put the blame there rather than on the
    model. One variable per arm — a rule this file has kept since arm B, and the reason its result
    could be read at all.
    """
    if arm == "split":
        return _HEAD + _STANCE["cautious"] + _TAIL_SPLIT
    if arm == "nodefect":
        return _HEAD + _STANCE["cautious"] + _rubric(no_defect=True, pre_existing=False)
    if arm == "preexisting":
        return _HEAD + _STANCE["cautious"] + _rubric(no_defect=False, pre_existing=True)
    return _HEAD + _STANCE[arm] + _TAIL


def ask(judge: Any, item: Item, model: str, arm: str) -> tuple[str, str, str, dict[str, int]]:
    """Returns (verdict, reason, counterargument, usage). `verdict` is approve/reject/unparsed."""
    user = (
        f"File: {item.path}\n"
        f"The comment is attached to lines {item.from_line}-{item.to_line} of the new file.\n\n"
        f"--- diff ---\n{item.patch}\n--- end diff ---\n\n"
        f"Review comment:\n{item.note}\n"
    )
    reply = judge.complete(
        [{"role": "system", "content": system_prompt(arm)}, {"role": "user", "content": user}],
        model=model,
        temperature=0.0,
    )
    text = (getattr(reply, "content", None) or str(reply)).strip()
    usage = {
        "prompt": int(getattr(reply, "prompt_tokens", 0) or 0),
        "completion": int(getattr(reply, "completion_tokens", 0) or 0),
    }

    # The whole answer is kept. It used to be cut at 200 characters, and all three readers of the
    # first two arms said the same thing: they had judged a first sentence, not a chain of reasoning.
    # The truncation cost nothing at the provider and removed the only evidence an audit could use.
    match = re.search(r'"verdict"\s*:\s*"(approve|reject)"', text, re.I)
    if match:
        reason = re.search(r'"reason"\s*:\s*"([^"]*)"', text)
        against = re.search(r'"strongest_counterargument"\s*:\s*"([^"]*)"', text)
        return match.group(1).lower(), (reason.group(1) if reason else text), (against.group(1) if against else ""), usage
    # A bare word is accepted too — the parser's job is to read the model, not to fail it on syntax.
    bare = re.search(r"\b(approve|reject)\b", text, re.I)
    if bare:
        return bare.group(1).lower(), text, "", usage
    return "unparsed", text, "", usage


# --- the report ----------------------------------------------------------------------------------


def report_results(rows: list[dict[str, Any]], dropped: list[Item], *, report=print) -> dict[str, Any]:
    from chimera.eval.anytime import wilson_bounds

    graded = [r for r in rows if r["verdict"] in ("approve", "reject")]
    unparsed = [r for r in rows if r["verdict"] == "unparsed"]

    bad = [r for r in graded if r["label"] == 0]
    good = [r for r in graded if r["label"] == 1]
    caught = sum(1 for r in bad if r["verdict"] == "reject")
    wrongly = sum(1 for r in good if r["verdict"] == "reject")

    recall = caught / len(bad) if bad else 0.0
    false_rej = wrongly / len(good) if good else 0.0
    reject_rate = sum(1 for r in graded if r["verdict"] == "reject") / len(graded) if graded else 0.0

    lo_r, hi_r = wilson_bounds(caught, len(bad)) if bad else (0.0, 0.0)
    lo_f, hi_f = wilson_bounds(wrongly, len(good)) if good else (0.0, 0.0)

    verdict = (
        "DISCRIMINATES" if recall >= 0.60 and false_rej <= 0.20
        else "WEAK" if recall >= 0.30 and false_rej <= 0.20
        else "DOES NOT DISCRIMINATE"
    )
    uninformative = []
    if graded and len(unparsed) / len(rows) > 0.10:
        uninformative.append(f"unparsed {len(unparsed)}/{len(rows)} > 10% — the harness is what was measured")
    if graded and (reject_rate < 0.02 or reject_rate > 0.98):
        uninformative.append(f"reject rate {reject_rate:.0%} — the judge answered the same thing to everything")

    summary = {
        "n_graded": len(graded),
        "n_unparsed": len(unparsed),
        "n_dropped_no_diff": len(dropped),
        "bad_comments": len(bad),
        "caught": caught,
        "rejection_recall": round(recall, 3),
        "rejection_recall_ci": [round(lo_r, 3), round(hi_r, 3)],
        "good_comments": len(good),
        "wrongly_rejected": wrongly,
        "false_rejection_rate": round(false_rej, 3),
        "false_rejection_ci": [round(lo_f, 3), round(hi_f, 3)],
        "reject_rate_overall": round(reject_rate, 3),
        "approve_everything_accuracy": round(len(good) / len(graded), 3) if graded else 0.0,
        "verdict": verdict,
        "uninformative": uninformative,
        "by_source_model": {
            model: {
                "n": sum(1 for r in graded if r["source_model"] == model),
                "rejected": sum(1 for r in graded if r["source_model"] == model and r["verdict"] == "reject"),
            }
            for model in sorted({r["source_model"] for r in graded})
        },
    }

    report("")
    report(f"  graded {summary['n_graded']}  unparsed {summary['n_unparsed']}  dropped {summary['n_dropped_no_diff']}")
    report(f"  rejection recall   {recall:.1%}  ({caught}/{len(bad)} bad comments caught)  CI [{lo_r:.1%}, {hi_r:.1%}]")
    report(f"  false rejection    {false_rej:.1%}  ({wrongly}/{len(good)} good comments rejected)  CI [{lo_f:.1%}, {hi_f:.1%}]")
    report(f"  reject rate        {reject_rate:.1%}   (a judge answering the same thing every time is caught here)")
    report(f"  approve-everything would score {summary['approve_everything_accuracy']:.1%} accuracy on this sample")
    report(f"  VERDICT: {verdict}")
    for line in uninformative:
        report(f"  UNINFORMATIVE: {line}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch", action="store_true", help="Cache the diffs and exit (no model calls).")
    parser.add_argument("--dry-run", action="store_true", help="Build every prompt, call nothing.")
    parser.add_argument("--limit", type=int, default=0, help="Grade only the first N items (smoke test).")
    parser.add_argument("--arm", choices=[*sorted(_STANCE), "split", "nodefect", "preexisting"], default="cautious",
                        help="Which stance the judge is given. See PREREGISTRATION-arms.md.")
    args = parser.parse_args()

    items = sample(load_rows())
    print(f"sample: {len(items)} items ({sum(1 for i in items if i.label == 1)} correct, "
          f"{sum(1 for i in items if i.label == 0)} incorrect) from the {SLICE} slice, seed {SEED}")

    fetch_patches(items)
    if args.fetch:
        return

    usable, dropped = attach_patches(items)
    print(f"with diffs: {len(usable)}  dropped (no diff): {len(dropped)}")
    # Shuffled with the same seed: the sample is built label-by-label, so grading it in order would
    # put every correct comment first and make a partial run look like a one-sided judge.
    random.Random(SEED).shuffle(usable)
    if args.limit:
        usable = usable[: args.limit]

    if args.dry_run:
        sizes = [len(i.patch) for i in usable]
        print(f"dry run — {len(usable)} prompts built, patch chars: "
              f"min {min(sizes)} / median {sorted(sizes)[len(sizes)//2]} / max {max(sizes)}")
        print("\n--- one prompt ---")
        print(system_prompt(args.arm)[:260], "...\n")
        print(usable[0].patch[:400])
        return

    from chimera.config import get_settings
    from chimera.providers.gateway import LLMGateway

    settings = get_settings()
    model = os.environ.get("CHIMERA_FUSION_JUDGE") or settings.fusion_judge
    gateway = LLMGateway()
    print(f"judge: {model} · arm: {args.arm}")

    out = OUT / args.arm
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    tokens = Counter()
    started = time.time()

    for n, item in enumerate(usable, 1):
        try:
            verdict, reason, against, usage = ask(gateway, item, model, args.arm)
        except Exception as exc:  # a provider failure is not a verdict
            verdict, reason, against = "unparsed", f"call failed: {exc}", ""
            usage = {"prompt": 0, "completion": 0}
        tokens.update(usage)
        rows.append({**asdict(item), "patch": "", "verdict": verdict, "reason": reason,
                     "counterargument": against, **usage})
        mark = "ok " if (verdict == "reject") == (item.label == 0) else "MISS"
        print(f"  {n:>3}/{len(usable)} {mark} label={item.label} verdict={verdict:<8} {item.repo}#{item.pr}")

    manifest = {
        "judge_model": model,
        "seed": SEED,
        "slice": SLICE,
        "per_label": PER_LABEL,
        "window_lines": WINDOW,
        "temperature": 0.0,
        "arm": args.arm,
        "prompt_sha": __import__("hashlib").sha256(system_prompt(args.arm).encode()).hexdigest()[:12],
        "chimera_version": __import__("importlib.metadata", fromlist=["version"]).version("chimera-agent"),
        "seconds": round(time.time() - started, 1),
        "tokens": dict(tokens),
    }
    summary = report_results(rows, dropped)

    (out / "details.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nwrote {out}/summary.json · tokens {dict(tokens)}")


if __name__ == "__main__":
    main()
