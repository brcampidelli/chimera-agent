"""How much of each request a provider could serve from its prefix cache, before and after wave 2.

A provider caches the longest prefix one request shares with an earlier one. This replays the same
Code-screen conversation twice, through the real `Agent` and a scripted model, and measures that
prefix for every request.

- **before**: the notes that change per turn (recalled facts, a finished job, an approved plan)
  appended to the system message, which is how `build_agent` placed them until wave 2.
- **after**: the same notes in `turn_notes`, with `turn_context` on.

The script is deterministic and costs nothing: no network, no model, the same conversation and the
same tool calls in both arms. What it measures is the mechanism, the bytes a cache could reuse. It
does not measure the provider's own accounting (see `RESULTS.md` for that and why it is separate),
and it does not measure whether answers change (that is the success-parity arm, run separately).

    python bench/turn_context/measure_prefix.py [--turns 6] [--steps 3] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from chimera.core.agent import DEFAULT_SYSTEM_PROMPT, Agent, AgentConfig  # noqa: E402
from chimera.prompts.context import facts_block  # noqa: E402
from chimera.providers.gateway import CompletionResult, ToolCall  # noqa: E402
from chimera.tools.builtin import EchoTool  # noqa: E402
from chimera.tools.registry import ToolRegistry  # noqa: E402

#: One conversation: what the person asks, what memory recalls for it, and the per-turn notes the
#: Code screen adds. Facts change with the question, a job finishes on turn 3, a plan is approved on
#: turn 4, which is the ordinary shape of a working session.
TURNS: list[dict[str, Any]] = [
    {"ask": "read the parser and tell me what it does", "facts": ["the project uses pytest"], "note": ""},
    {"ask": "fix the bug in the tokenizer", "facts": ["the owner prefers small commits", "tokenizer lives in lex.py"], "note": ""},
    {"ask": "run the test suite", "facts": ["tests take about two minutes"],
     "note": "Background jobs that finished since your last turn:\n- job 7f3a finished (exit 0): pytest -q — log: jobs/7f3a.log"},
    {"ask": "now refactor the error handling", "facts": ["errors are logged with get_logger"],
     "note": "Approved plan:\n1. read errors.py\n2. replace bare excepts\n3. run the tests"},
    {"ask": "write the changelog entry", "facts": ["the changelog follows keep-a-changelog"], "note": ""},
    {"ask": "summarise what we did today", "facts": ["the owner reads summaries in Portuguese"], "note": ""},
]


class _Scripted:
    """A model that calls `echo` a fixed number of times and then answers, recording every request."""

    def __init__(self, steps: int) -> None:
        self.steps = steps
        self.requests: list[str] = []
        self._left = steps

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        self.requests.append(json.dumps([m if isinstance(m, dict) else m.as_dict() for m in messages],
                                        ensure_ascii=False, sort_keys=True))
        if self._left > 0:
            self._left -= 1
            return CompletionResult(content="", model="m", tool_calls=[
                ToolCall(id=f"c{len(self.requests)}", name="echo", arguments={"text": f"step {len(self.requests)}"})
            ])
        self._left = self.steps
        return CompletionResult(content=f"answer {len(self.requests)}", model="m")


def _common_prefix(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def run_arm(after: bool, turns: int, steps: int) -> dict[str, Any]:
    backend = _Scripted(steps)
    registry = ToolRegistry()
    registry.register(EchoTool())
    history: list[Any] = []
    for turn in TURNS[:turns]:
        notes = "\n\n".join(p for p in (facts_block(turn["facts"]), turn["note"]) if p)
        if after:
            config = AgentConfig(inject_skill_context=False, prefix_nonce="", turn_context=True, turn_notes=notes)
        else:
            system = DEFAULT_SYSTEM_PROMPT + (f"\n\n{notes}" if notes else "")
            config = AgentConfig(inject_skill_context=False, prefix_nonce="", system_prompt=system)
        result = Agent(backend, registry, config).run(turn["ask"], history=history)  # type: ignore[arg-type]
        history = [m for m in result.transcript if not (isinstance(m, dict) and m.get("role") == "system")]
    reqs = backend.requests
    reused = [0] + [_common_prefix(reqs[i - 1], reqs[i]) for i in range(1, len(reqs))]
    first_of_turn = [i for i in range(len(reqs)) if i % (steps + 1) == 0]
    return {
        "requests": len(reqs),
        "chars_sent": sum(len(r) for r in reqs),
        "chars_reusable": sum(reused),
        "reusable_share": round(sum(reused) / sum(len(r) for r in reqs), 3),
        "first_step_of_each_turn": [
            {"turn": k + 1, "sent": len(reqs[i]), "reusable": reused[i]} for k, i in enumerate(first_of_turn)
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="prefix a cache could reuse, before and after wave 2")
    parser.add_argument("--turns", type=int, default=len(TURNS))
    parser.add_argument("--steps", type=int, default=3, help="tool calls per turn before the answer")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    before, after = run_arm(False, args.turns, args.steps), run_arm(True, args.turns, args.steps)
    if args.json:
        print(json.dumps({"before": before, "after": after}, indent=2))
        return 0
    print(f"{args.turns} turns x ({args.steps} tool steps + 1 answer) = {before['requests']} requests per arm\n")
    print(f"{'':28}{'before':>12}{'after':>12}")
    for key in ("chars_sent", "chars_reusable", "reusable_share"):
        print(f"{key:28}{before[key]:>12}{after[key]:>12}")
    print("\nfirst request of each turn (the one a new turn pays for):")
    print(f"{'turn':>4}{'sent (b)':>12}{'reusable (b)':>14}{'sent (a)':>12}{'reusable (a)':>14}")
    for b, a in zip(before["first_step_of_each_turn"], after["first_step_of_each_turn"], strict=True):
        print(f"{b['turn']:>4}{b['sent']:>12}{b['reusable']:>14}{a['sent']:>12}{a['reusable']:>14}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
