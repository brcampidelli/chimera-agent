"""The clock closes the turn context, so a prefix cache can reuse everything written before it.

Study 28, P1. A provider caches the longest prefix a request shares with one it has already seen,
and stops at the first byte that differs. The turn context opened with the date and the time to the
minute, so the first bytes of every turn's message changed every minute and nothing after them was
ever reused:
- a scheduled job runs the same task with the same skills on every dispatch, and paid for all of it
  every time;
- the flattened chat re-sent its turn context, profile and replayed conversation after the clock.

Now the block runs from the most stable line to the most volatile: system, shell and working
directory; then the skills and cards; then the notes; then git and the clock.

The header also claimed "the user's message follows the closing tag". In the flattened chat the
profile, the recalled facts and the replayed conversation sit between the tag and the new message,
so the claim was false on the Discord bot and the terminal chat.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

import chimera.prompts.context as context_module
from chimera.core.agent import Agent, AgentConfig
from chimera.interface.session import ChatSession
from chimera.prompts.context import TURN_CONTEXT_CLOSE, TURN_CONTEXT_OPEN
from chimera.providers.gateway import CompletionResult
from chimera.skills.retrieval import SKILLS_HEADER
from chimera.tools.builtin import EchoTool
from chimera.tools.registry import ToolRegistry

ROOT = Path(__file__).resolve().parent.parent
_ZONE = timezone(timedelta(hours=-3))


class _Recorder:
    """Answers every request at once and keeps a copy of each message list it was sent."""

    def __init__(self) -> None:
        self.sent: list[list[dict[str, Any]]] = []

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        self.sent.append([dict(m) if isinstance(m, dict) else m.as_dict() for m in messages])
        return CompletionResult(content=f"answer {len(self.sent)}", model="fake")


def _agent(backend: _Recorder, root: Path | None, **config: Any) -> Agent:
    registry = ToolRegistry()
    registry.register(EchoTool())
    return Agent(  # type: ignore[arg-type]
        backend, registry, AgentConfig(prefix_nonce="", project_root=root, turn_context=True, **config)
    )


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, datetime]:
    """A clock the test moves by hand; the turn context reads it as the local time."""
    state = {"now": datetime(2026, 9, 25, 10, 0, tzinfo=_ZONE)}

    class _Pinned(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> Any:  # noqa: ARG003 -- the signature `datetime.now` has
            return state["now"]

    monkeypatch.setattr(context_module, "datetime", _Pinned)
    return state


def _shared(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def test_the_stable_lines_open_the_turn_context_and_the_clock_closes_it(tmp_path: Path) -> None:
    backend = _Recorder()
    _agent(backend, tmp_path, turn_notes="NOTE-FOR-THIS-TURN").run("fix a bug in this code")
    sent = backend.sent[0][-1]["content"]
    order = [
        sent.index("- working directory:"),
        sent.index(SKILLS_HEADER),
        sent.index("NOTE-FOR-THIS-TURN"),
        sent.index("At the start of this turn"),
        sent.index("- date: "),
        sent.index(TURN_CONTEXT_CLOSE),
    ]
    assert order == sorted(order)
    block = sent[: sent.index(TURN_CONTEXT_CLOSE)].rstrip("\n")
    assert block.splitlines()[-1].startswith("- date: "), "the clock is the block's last line"


def test_two_runs_of_the_same_task_minutes_apart_share_everything_before_the_clock(
    tmp_path: Path, clock: dict[str, datetime]
) -> None:
    """A scheduled job's next run: same task, same skills, a later minute. The measurement is the
    characters the two turn messages share from the start, which is what a prefix cache can reuse
    of them; before the fix it ended inside the block's second line."""
    backend = _Recorder()
    agent = _agent(backend, tmp_path)
    task = "fix a bug in this code"
    agent.run(task)
    clock["now"] = datetime(2026, 9, 25, 10, 2, tzinfo=_ZONE)
    agent.run(task)
    first, second = backend.sent[0][-1]["content"], backend.sent[1][-1]["content"]
    assert first != second  # the clock did move
    shared = _shared(first, second)
    assert shared > second.index(SKILLS_HEADER), "the skills are inside the reusable prefix"
    assert shared >= second.index("- date: "), "the prefix runs up to the clock line itself"
    assert backend.sent[0][0] == backend.sent[1][0]  # the system message, unchanged


def test_the_header_is_true_where_the_flattened_chat_puts_the_conversation_in_between() -> None:
    """The header says where the user's words are, and both chat forms have to make it true: after
    the closing tag, in the same message. In the flattened form the profile comes first, so a header
    saying the message *follows* the tag was false there."""
    for real in (False, True):
        backend = _Recorder()
        session = ChatSession(
            _agent(backend, None, inject_skill_context=False), gate=None, profile="PROFILE-TEXT",
            real_history=real,
        )
        session.send("first question")
        session.send("second question")
        message = backend.sent[-1][-1]["content"]
        assert message.startswith(TURN_CONTEXT_OPEN)
        assert message.index(TURN_CONTEXT_CLOSE) < message.rindex("second question"), real
        after_tag = message[message.index(TURN_CONTEXT_CLOSE) + len(TURN_CONTEXT_CLOSE):].lstrip()
        if not real:
            assert after_tag.startswith("PROFILE-TEXT")  # not the user's words
    assert "follows the closing tag" not in TURN_CONTEXT_OPEN
    assert "after the closing tag, in this same message" in TURN_CONTEXT_OPEN


def _measure_prefix() -> Any:
    path = ROOT / "bench" / "chat_history" / "measure_prefix.py"
    spec = importlib.util.spec_from_file_location("chat_history_measure_prefix", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_prefix_bench_pins_the_clock_it_measures() -> None:
    """`bench/chat_history/measure_prefix.py` pins the clock by replacing the functions the agent
    looks up. When the clock moved, the function it replaced stopped existing, and an assignment to
    a missing name adds an attribute nobody reads: the bench would have measured the real clock and
    printed a number with the same face. So the pinned date is read back from the requests."""
    bench = _measure_prefix()
    requests, firsts = bench.run_arm(False)
    turn_two = requests[firsts[1]][-1]["content"]
    assert "- date: Friday 2026-09-25, 10:02 (UTC-03:00)" in turn_two
    assert turn_two.index("- system: Linux 6.6; shell: bash") < turn_two.index("- date: ")
    rendered = bench.render(requests[firsts[1]])
    reusable = bench.measure(requests, firsts)["first_request_of_each_turn"][1]["reusable"]
    assert reusable >= rendered.index("- date: ")
