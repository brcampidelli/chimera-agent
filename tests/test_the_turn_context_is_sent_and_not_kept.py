"""What changes per turn travels in the turn's own message, and the system message stays one string.

Study 25, wave 2 (`bench/PLAN-study25-system-prompts.md` §5.1). The system message used to carry:
- recalled facts;
- job and work notes;
- the approved plan;
- the skills retrieved for the task.

Each of those changes from turn to turn, and a provider caches only the prefix two requests share.
So on the Code screen that prefix ended about 318 tokens in, whatever came after it.

With `AgentConfig.turn_context` on:
- that material heads the turn's user message;
- the system message is the same bytes on every turn;
- within a run the message list only grows;
- the transcript keeps the user's words bare, so nothing stale is stored.

The environment facts every vendor prompt carries (date, system, working directory, git) ride in the
same block.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from chimera.core.agent import Agent, AgentConfig
from chimera.prompts.context import (
    TURN_CONTEXT_CLOSE,
    TURN_CONTEXT_OPEN,
    environment_facts,
    facts_block,
    turn_context,
)
from chimera.providers.gateway import CompletionResult, ToolCall
from chimera.skills.retrieval import SKILLS_HEADER
from chimera.tools.builtin import EchoTool
from chimera.tools.registry import ToolRegistry


class _Recorder:
    """Answers from a script and keeps a copy of every message list it was sent."""

    def __init__(self, replies: list[CompletionResult]) -> None:
        self.replies = list(replies)
        self.sent: list[list[dict[str, Any]]] = []

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        self.sent.append([dict(m) if isinstance(m, dict) else m.as_dict() for m in messages])
        return self.replies.pop(0)


def _done() -> CompletionResult:
    return CompletionResult(content="done", model="fake")


def _agent(backend: _Recorder, tmp_path: Path, **config: Any) -> Agent:
    registry = ToolRegistry()
    registry.register(EchoTool())
    return Agent(  # type: ignore[arg-type]
        backend, registry, AgentConfig(prefix_nonce="", project_root=tmp_path, turn_context=True, **config)
    )


def test_the_turn_context_heads_the_turn_and_the_transcript_keeps_it_bare(tmp_path: Path) -> None:
    backend = _Recorder([_done()])
    result = _agent(backend, tmp_path, turn_notes="NOTE-FOR-THIS-TURN").run("fix the parser")

    sent_user = backend.sent[0][-1]["content"]
    assert sent_user.startswith(TURN_CONTEXT_OPEN) and sent_user.endswith("\n\nfix the parser")
    assert "NOTE-FOR-THIS-TURN" in sent_user and "working directory" in sent_user
    kept = [m for m in result.transcript if isinstance(m, dict) and m.get("role") == "user"]
    assert kept == [{"role": "user", "content": "fix the parser"}]


def test_the_system_message_is_the_same_bytes_whatever_the_turn_carries(tmp_path: Path) -> None:
    first, second = _Recorder([_done()]), _Recorder([_done()])
    _agent(first, tmp_path, turn_notes="facts about turn one").run("summarise the repository")
    _agent(second, tmp_path, turn_notes="a job finished; a plan was approved").run("write the tests")
    assert first.sent[0][0] == second.sent[0][0]
    assert first.sent[0][-1] != second.sent[0][-1]


def test_skills_leave_the_system_for_the_turn_context(tmp_path: Path) -> None:
    backend = _Recorder([_done()])
    _agent(backend, tmp_path).run("fix a bug in this code")  # matches the built-in fix_code skill
    system, turn = backend.sent[0][0]["content"], backend.sent[0][-1]["content"]
    assert SKILLS_HEADER not in system
    assert SKILLS_HEADER in turn


def test_within_a_run_every_step_resends_the_previous_step_as_its_prefix(tmp_path: Path) -> None:
    backend = _Recorder([
        CompletionResult(content="", model="fake", tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "x"})]),
        _done(),
    ])
    _agent(backend, tmp_path).run("echo x then finish")
    step_one, step_two = backend.sent
    assert step_two[: len(step_one)] == step_one


def test_off_by_default_nothing_moves(tmp_path: Path) -> None:
    backend = _Recorder([_done()])
    Agent(backend, ToolRegistry(), AgentConfig(prefix_nonce="", project_root=tmp_path)).run("hi")  # type: ignore[arg-type]
    assert backend.sent[0][-1] == {"role": "user", "content": "hi"}


def test_the_environment_block_says_when_and_where(tmp_path: Path) -> None:
    now = datetime(2026, 9, 25, 17, 48, tzinfo=timezone(timedelta(hours=-3)))
    text = environment_facts(tmp_path, now=now)
    assert "Friday 2026-09-25, 17:48 (UTC-03:00)" in text
    assert f"working directory: {tmp_path}" in text
    assert "git:" not in text  # not a repository: left out, never guessed


def test_the_environment_block_reads_git_when_there_is_a_repository(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is not installed here, and the block leaves git out when it cannot read it")
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    (tmp_path / "new.txt").write_text("x", encoding="utf-8")
    assert "git: branch main (no commits yet), 1 changed file" in environment_facts(tmp_path)


def test_facts_are_labelled_as_recall_and_nothing_empty_is_sent() -> None:
    assert facts_block([]) == ""
    assert "possibly stale" in facts_block(["the user prefers pytest"])
    assert turn_context("", "  ") == ""
    block = turn_context("A", "B")
    assert block.startswith(TURN_CONTEXT_OPEN) and block.endswith(TURN_CONTEXT_CLOSE)
