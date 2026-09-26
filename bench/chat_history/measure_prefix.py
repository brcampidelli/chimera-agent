"""How much of each chat request a prefix cache could reuse, flattened against real history.

Offline, deterministic, US$ 0. The same ten-turn conversation goes through a real `ChatSession` and a
real `Agent` twice, once per arm, with a scripted model:
- **flat**: `ChatSession(real_history=False)`, today's default. Each turn is one user message: the
  turn context, then the profile, the recalled facts, the last six turns as prose, the message.
- **real**: `ChatSession(real_history=True)`. Earlier turns are the model's own messages, tool
  calls included; the profile and the facts ride in the turn context at the head of the new message.

Both arms send the same conversation: the same user words, the same recalled facts, the same tool
calls and the same replies. The clock is pinned to advance two minutes per turn, as a real
conversation's does, and stays fixed within a turn.

A provider caches the longest prefix a request shares with one it has already seen. This renders
every request as a chat template would (role, then content, then tool calls) and counts the
characters it shares with the best earlier request of the same arm.

    python bench/chat_history/measure_prefix.py [--json] [--dump requests.json]

`--dump` writes the exact message lists both arms send, which `replay_live.py` sends to a provider.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import chimera.prompts.context as context_module  # noqa: E402
from chimera.core.agent import Agent, AgentConfig  # noqa: E402
from chimera.interface.profile import UserProfile, render_profile  # noqa: E402
from chimera.interface.session import ChatSession  # noqa: E402
from chimera.memory.models import MemoryItem  # noqa: E402
from chimera.providers.gateway import CompletionResult, ToolCall  # noqa: E402
from chimera.tools.builtin import EchoTool  # noqa: E402
from chimera.tools.registry import ToolRegistry  # noqa: E402

#: Registered in PREREGISTRATION.md. Ten turns, so the six-turn window fills at turn 7 and slides
#: for turns 8-10. `steps` is how many tool calls the scripted model makes before it answers: chat
#: is mostly talk, with a tool now and then.
TURNS: list[dict[str, Any]] = [
    {"ask": "what's on my calendar tomorrow?", "steps": 1,
     "facts": ["the owner's calendar is the Google one", "the owner is in UTC-03:00"]},
    {"ask": "move the dentist to Friday", "steps": 0,
     "facts": ["the dentist is Dr. Lima", "Fridays are usually free after 3pm"]},
    {"ask": "how did PassaPro do this week?", "steps": 2,
     "facts": ["PassaPro metrics come from the Supabase project", "weekly means Monday to Sunday"]},
    {"ask": "compare that with last month", "steps": 0,
     "facts": ["last month had a promotion on the 12th", "the owner reads numbers as tables"]},
    {"ask": "draft a short note to the team about it", "steps": 1,
     "facts": ["the team channel is #geral", "notes to the team are in Portuguese"]},
    {"ask": "make it friendlier", "steps": 0,
     "facts": ["the owner signs notes as Bruno", "the team is five people"]},
    {"ask": "what's the eToro position summary?", "steps": 2,
     "facts": ["eToro numbers come from the deterministic script only", "the mandate caps a trade at 2%"]},
    {"ask": "any position near its stop?", "steps": 0,
     "facts": ["a stop loss is always set", "three losses in a row stop the day"]},
    {"ask": "remind me to review it at 6pm", "steps": 1,
     "facts": ["reminders go to the Discord DM", "6pm is 18:00 local time"]},
    {"ask": "thanks, that's all for now", "steps": 0,
     "facts": ["the owner prefers short closings", "nothing else is pending today"]},
]

#: The stable profile a chat session carries (`_session_profile` renders the same shape).
PROFILE = render_profile(UserProfile(
    name="Bruno",
    preferences=["answer in Brazilian Portuguese", "short answers unless asked for detail"],
    projects=["PassaPro", "LeFran", "VirtualSector", "Chimera"],
    contexts=["timezone America/Sao_Paulo", "works on Windows and a Linux VPS"],
))

#: A chat reply of ordinary length, the same text in both arms.
_REPLY = (
    "Here is what I found. The first item is settled, the second needs a decision from you, and "
    "nothing else changed since we last looked. If you want, I can take the next step now, or leave "
    "it for later and remind you. Just say which."
)

_CLOCK = {"minute": 0}


def _pinned_environment(cwd: Any = None, **_kwargs: Any) -> str:
    """The environment block with a pinned clock: two minutes per turn, fixed within a turn."""
    return (
        "Environment at the start of this turn (a snapshot; it does not update during the turn):\n"
        f"- date: Thursday 2026-09-25, 10:{_CLOCK['minute']:02d} (UTC-03:00)\n"
        "- system: Linux 6.6; shell: bash"
    )


class _Memory:
    """Recall that returns the facts registered for the message, in order."""

    def search(self, query: str, *, k: int = 5) -> list[MemoryItem]:
        for turn in TURNS:
            if turn["ask"] == query:
                return [MemoryItem(id=f"{query}:{i}", content=f) for i, f in enumerate(turn["facts"])]
        return []


class _Scripted:
    """A model that makes each turn's registered tool calls, then answers; records every request."""

    def __init__(self) -> None:
        self.requests: list[list[dict[str, Any]]] = []
        self.turn = 0
        self.left = 0

    def start_turn(self, index: int) -> None:
        self.turn = index
        self.left = TURNS[index]["steps"]

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        self.requests.append([dict(m) if isinstance(m, dict) else m.as_dict() for m in messages])
        if self.left > 0:
            step = TURNS[self.turn]["steps"] - self.left
            self.left -= 1
            return CompletionResult(content="", model="m", tool_calls=[ToolCall(
                id=f"call_{self.turn}_{step}", name="echo",
                arguments={"text": f"lookup {self.turn}.{step}"},
            )])
        return CompletionResult(content=f"{_REPLY} (turn {self.turn + 1})", model="m")


def render(messages: list[dict[str, Any]]) -> str:
    """One request as a chat template lays it out: role, content, then any tool calls or call id."""
    parts = []
    for m in messages:
        parts.append(f"<|{m.get('role')}|>\n{m.get('content') or ''}")
        if m.get("tool_calls"):
            parts.append(json.dumps(m["tool_calls"], ensure_ascii=False))
        if m.get("tool_call_id"):
            parts.append(f"[id {m['tool_call_id']}]")
    return "\n".join(parts)


def _common_prefix(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def run_arm(real: bool) -> tuple[list[list[dict[str, Any]]], list[int]]:
    """The requests one arm sends, and the first request index of each turn."""
    backend = _Scripted()
    registry = ToolRegistry()
    registry.register(EchoTool())
    agent = Agent(backend, registry, AgentConfig(  # type: ignore[arg-type]
        model="m", inject_skill_context=False, prefix_nonce="", turn_context=True, project_root=None,
    ))
    session = ChatSession(agent, memory=_Memory(), gate=None, profile=PROFILE, real_history=real)
    original = context_module.environment_facts
    context_module.environment_facts = _pinned_environment  # type: ignore[assignment]
    firsts: list[int] = []
    try:
        for index, turn in enumerate(TURNS):
            _CLOCK["minute"] = 2 * index
            backend.start_turn(index)
            firsts.append(len(backend.requests))
            session.send(turn["ask"])
    finally:
        context_module.environment_facts = original  # type: ignore[assignment]
    return backend.requests, firsts


def measure(requests: list[list[dict[str, Any]]], firsts: list[int]) -> dict[str, Any]:
    texts = [render(r) for r in requests]
    reused = [max((_common_prefix(texts[j], t) for j in range(i)), default=0) for i, t in enumerate(texts)]
    sent = sum(len(t) for t in texts)
    window = [
        {"turn": k + 1, "sent": len(texts[i]), "reusable": reused[i]} for k, i in enumerate(firsts)
    ]
    return {
        "requests": len(texts),
        "chars_sent": sent,
        "chars_reusable": sum(reused),
        "chars_not_reusable": sent - sum(reused),
        "reusable_share": round(sum(reused) / sent, 4),
        "first_request_of_each_turn": window,
        "first_requests_turns_2_7_reusable": sum(w["reusable"] for w in window[1:7]),
        "first_requests_turns_8_10_reusable": sum(w["reusable"] for w in window[7:]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="prefix a cache could reuse: flat vs real history")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dump", type=Path, help="write both arms' requests for replay_live.py")
    args = parser.parse_args(argv)
    arms = {}
    dumped = {}
    for name, real in (("flat", False), ("real", True)):
        requests, firsts = run_arm(real)
        arms[name] = measure(requests, firsts)
        dumped[name] = {"requests": requests, "firsts": firsts}
    if args.dump:
        args.dump.write_text(json.dumps(dumped, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.json:
        print(json.dumps(arms, indent=2))
        return 0
    flat, real = arms["flat"], arms["real"]
    print(f"{len(TURNS)} turns; requests per arm: flat {flat['requests']}, real {real['requests']}\n")
    print(f"{'':30}{'flat':>12}{'real':>12}")
    for key in ("chars_sent", "chars_reusable", "chars_not_reusable", "reusable_share",
                "first_requests_turns_2_7_reusable", "first_requests_turns_8_10_reusable"):
        print(f"{key:30}{flat[key]:>12}{real[key]:>12}")
    print("\nfirst request of each turn (the one a new turn pays for):")
    print(f"{'turn':>4}{'sent (flat)':>14}{'reusable':>10}{'sent (real)':>14}{'reusable':>10}")
    for f, r in zip(flat["first_request_of_each_turn"], real["first_request_of_each_turn"], strict=True):
        print(f"{f['turn']:>4}{f['sent']:>14}{f['reusable']:>10}{r['sent']:>14}{r['reusable']:>10}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
