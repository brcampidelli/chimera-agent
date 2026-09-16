"""Run the compaction bench described in `PREREGISTRATION.md`.

    python bench/compaction/run.py --pilot     # apparatus only: does a compaction fire?
    python bench/compaction/run.py             # the paired run

The conversation is shaped like the Code screen, because that is where the failure lives: a fresh
`Agent` per turn with the previous transcript as history, so `run_state.task` holds THIS turn's
message and a convention from turn one has nothing restoring it.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from chimera.config import get_settings  # noqa: E402
from chimera.core import Agent, AgentConfig  # noqa: E402
from chimera.providers.gateway import LLMGateway  # noqa: E402
from chimera.tools.builtin import default_registry  # noqa: E402

MODEL = "openrouter/deepseek/deepseek-v4-flash-0731"
BUDGET = 0.0025  # amendment 2: the fraction the calibration measured as the one that fires
MARKER = "[earlier conversation, compacted]"


@dataclass
class Convention:
    """A rule stated once, in words, and checkable on a file without judgement."""

    name: str
    said: str
    check: object  # (str) -> bool


CONVENTIONS = [
    Convention(
        "copyright-first-line",
        "In this project every Python file must begin with the exact line '# (c) Bruno 2026'.",
        lambda text: text.lstrip().startswith("# (c) Bruno 2026"),
    ),
    Convention(
        "author-constant",
        "In this project every Python module must define a module-level constant AUTOR = \"Bruno\".",
        lambda text: re.search(r'^AUTOR\s*=\s*"Bruno"', text, re.M) is not None,
    ),
    Convention(
        "end-marker",
        "In this project every Python file must end with the exact line '# fim'.",
        lambda text: text.rstrip().endswith("# fim"),
    ),
    Convention(
        "function-prefix",
        "In this project every function name must start with 'bee_'.",
        lambda text: bool(re.findall(r"^def\s+(\w+)", text, re.M))
        and all(n.startswith("bee_") for n in re.findall(r"^def\s+(\w+)", text, re.M)),
    ),
    Convention(
        "no-print",
        "In this project the print() builtin is banned; return values instead of printing them.",
        lambda text: "print(" not in text,
    ),
    Convention(
        "future-import",
        "In this project every Python file must have 'from __future__ import annotations' as its "
        "first import.",
        lambda text: "from __future__ import annotations" in text,
    ),
]

THEMES = [
    ("shapes", ["area.py", "perimeter.py", "volume.py"], "geometry helpers"),
    ("strings", ["slug.py", "titlecase.py", "trim.py"], "string helpers"),
    ("money", ["vat.py", "discount.py", "rounding.py"], "money helpers"),
    ("time", ["weekday.py", "elapsed.py", "parse_date.py"], "date helpers"),
    ("lists", ["chunk.py", "dedupe.py", "flatten.py"], "list helpers"),
]

FINAL_FILE = "final.py"


@dataclass
class Pair:
    convention: Convention
    theme: tuple[str, list[str], str]

    def turns(self) -> list[str]:
        _, files, what = self.theme
        first = (
            f"{self.convention.said} With that in mind, create {files[0]} with one small function "
            f"for {what}."
        )
        middle = [f"Now create {name} with one more small function for {what}." for name in files[1:]]
        last = f"Now create {FINAL_FILE} with one more small function for {what}."
        return [first, *middle, last]


@dataclass
class Outcome:
    pair_id: str
    arm: str
    compacted: bool = False
    honoured: bool = False
    file_written: bool = False
    usd: float = 0.0
    seconds: float = 0.0
    turns: int = 0
    summaries: list[str] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)  # the provider that served each turn's first step (#484)
    halted: str = ""  # a transport error that survived one retry; the pair is void (PROTOCOL §2)


def run_conversation(pair: Pair, arm: str) -> Outcome:
    """One conversation, retried once from scratch on a transport error: the second run of this
    bench died at pair 13 on a connection reset the loop did not catch. A conversation that fails
    twice is recorded as halted — never as a miss — and its pair is void."""
    last = ""
    for attempt in (1, 2):
        try:
            return _run_conversation(pair, arm)
        except Exception as exc:  # noqa: BLE001 — the provider's failure is not the arm's
            last = f"{type(exc).__name__}: {str(exc)[:200]}"
            print(f"    transport error on {pair.convention.name}/{pair.theme[0]} {arm} (attempt {attempt}): {last}", flush=True)
            time.sleep(30)
    return Outcome(pair_id=f"{pair.convention.name}/{pair.theme[0]}", arm=arm, halted=last)


def _run_conversation(pair: Pair, arm: str) -> Outcome:
    workspace = Path(tempfile.mkdtemp(prefix="compact-bench-"))
    out = Outcome(pair_id=f"{pair.convention.name}/{pair.theme[0]}", arm=arm)
    settings = get_settings()
    gateway = LLMGateway(settings)
    history: list[object] = []
    started = time.time()
    try:
        for task in pair.turns():
            # A fresh Agent per turn, as the Code screen builds one per request. That is what makes
            # `run_state.task` this turn's message rather than the conversation's opening.
            agent = Agent(
                gateway,
                default_registry(workspace),
                AgentConfig(
                    model=MODEL,
                    context_budget=BUDGET,
                    max_steps=6,
                    max_usd=0.06,
                    summarise_compaction=(arm == "rules"),
                ),
            )
            result = agent.run(task, history=list(history))  # type: ignore[arg-type]
            out.turns += 1
            out.usd += result.usd or 0.0
            first_step = result.steplog.steps[0] if getattr(result.steplog, "steps", None) else None
            out.routes.append(str(getattr(first_step, "provider", "") or ""))
            history = [
                m for m in result.transcript
                if not (isinstance(m, dict) and m.get("role") == "system")
            ]
            for message in history:
                content = str(message.get("content") or "") if isinstance(message, dict) else ""
                if MARKER in content:
                    out.compacted = True
                    if content not in out.summaries:
                        out.summaries.append(content)
        final = workspace / FINAL_FILE
        out.file_written = final.is_file()
        if out.file_written:
            out.honoured = bool(pair.convention.check(final.read_text(encoding="utf-8")))
    finally:
        out.seconds = time.time() - started
        shutil.rmtree(workspace, ignore_errors=True)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot", action="store_true", help="apparatus only; outcomes not printed")
    parser.add_argument("--out", default="bench/compaction/outcomes.jsonl")
    parser.add_argument("--start", type=int, default=1, help="first pair index to run (resume after a kill)")
    args = parser.parse_args()

    pairs = [Pair(c, t) for c in CONVENTIONS for t in THEMES]
    if args.pilot:
        # Six, one per convention, control arm only. Prints whether a compaction fired and what the
        # note replaced the span with — and DELIBERATELY not whether the convention survived. Reading
        # that here would make the decision rule a rule chosen after seeing data.
        print("PILOT — apparatus only. Outcomes are not read.\n")
        for pair in pairs[:: len(THEMES)]:
            out = run_conversation(pair, "note")
            print(f"  {out.pair_id:34} compactou={out.compacted}  turnos={out.turns}  "
                  f"arquivo={out.file_written}  usd={out.usd:.4f}  {out.seconds:.0f}s")
            if out.summaries:
                print(f"     vao: {out.summaries[0][:150]}")
        return 0

    # One JSON line per conversation, written as it finishes: the first run of this bench was
    # killed at pair 15 by the shell's timeout and left nothing but its log, because everything was
    # held in memory for a single write at the end — the summaries the fabrication count needs died
    # with it. Append-only, so `--start` resumes after a kill.
    out_path = Path(args.out)
    with out_path.open("a", encoding="utf-8") as handle:
        for index, pair in enumerate(pairs, 1):
            if index < args.start:
                continue
            for arm in ("note", "rules"):
                out = run_conversation(pair, arm)
                handle.write(json.dumps(vars(out), ensure_ascii=False) + "\n")
                handle.flush()
                print(f"  [{index:2}/{len(pairs)}] {arm:5} {out.pair_id:34} "
                      f"compactou={out.compacted} honrou={out.honoured} usd={out.usd:.4f}", flush=True)
    print(f"\n  gravado em {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
