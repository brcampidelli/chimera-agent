"""Tests for the right-hand scenario suite (no network).

The suite drives a real :class:`ChatSession` — real ``_assemble``, real recall, real
``_maybe_remember`` — over a fake agent, so what is tested here is the machinery the live run uses
and not a second implementation of it. The fake is an *oracle*: it answers correctly, but only by
reading what the session actually handed it (the workspace file, the transcript block, the recalled
memory), so a scenario that passes here has demonstrated the wire is connected.

Three of these are sabotage tests, and they are the point. The old suite's checks passed on the
echo of their own prompt; ``the prompt echo fails every check`` is the guard that says this one's do
not, and it is run against every scenario rather than against a chosen example.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from chimera.core.agent import AgentResult
from chimera.eval.scenarios import (
    Scenario,
    ScenarioOutcome,
    ScenarioTurn,
    SessionRequest,
    SuiteReport,
    append_series,
    daily_scenarios,
    mechanism_arm,
    normalised_equals,
    repo_sha,
    run_suite,
    series_record,
    suite_arm,
)
from chimera.interface import ChatSession
from chimera.memory import MemoryManager, MemoryStore

_WINDOW_RE = re.compile(r"\b(Monday|Tuesday|Wednesday|Thursday|Friday) at (\d{1,2}):00", re.I)


def _current_message(prompt: str) -> str:
    """The turn's own message, peeled off the assembled prompt the session built."""
    if "\n\nUser: " in prompt:
        return prompt.rsplit("\n\nUser: ", 1)[1]
    return prompt[len("User: ") :] if prompt.startswith("User: ") else prompt


class _Agent:
    """Base fake: records prompts, reports a fixed tool list and a priced receipt."""

    def __init__(self, workspace: Path, *, tools: tuple[str, ...] = ("read_file",)) -> None:
        self.workspace = workspace
        self.tools = list(tools)
        self.prompts: list[str] = []

    def answer_for(self, prompt: str) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def run(self, task: str, *, on_token: Any = None, on_tool: Any = None) -> AgentResult:
        self.prompts.append(task)
        return AgentResult(
            answer=self.answer_for(task),
            steps=1,
            stopped_reason="final",
            tool_names=list(self.tools),
            prompt_tokens=42,
            completion_tokens=7,
            usd=0.0002,
            model="fake/oracle",
        )


class OracleAgent(_Agent):
    """Answers every scenario correctly — but only from what the session handed it."""

    def answer_for(self, prompt: str) -> str:
        message = _current_message(prompt)
        if "notes.txt" in message:
            body = (self.workspace / "notes.txt").read_text(encoding="utf-8")
            return str(len(body.splitlines()))
        if "SERVICE_CHECKSUM" in message:
            for line in (self.workspace / "service.env").read_text(encoding="utf-8").splitlines():
                if line.startswith("SERVICE_CHECKSUM="):
                    return line.split("=", 1)[1]
            return "not found"
        if "broke in transit" in message:
            # Deliberately read off the PROMPT, not the message: the operands live in the
            # transcript block, so this branch answers correctly only if threading happened.
            crates = re.search(r"(\d+) crates and each crate holds (\d+) bottles", prompt)
            broke = re.search(r"(\d+) of the bottles broke", message)
            if not crates or not broke:
                return "I have lost track."
            return str(int(crates[1]) * int(crates[2]) - int(broke[1]))
        if "crates" in message:
            found = re.search(r"(\d+) crates and each crate holds (\d+) bottles", message)
            return str(int(found[1]) * int(found[2])) if found else "?"
        if message.lower().startswith("remember that"):
            return "Noted."
        if "deploy window" in message:
            # Same idea: the answer is only available from the recalled memory block.
            found = _WINDOW_RE.search(prompt)
            return (
                f"Your deploy window is {found[1]} at {found[2]}:00 UTC." if found else "No idea."
            )
        if "percent of" in message:
            found = re.search(r"(\d+) percent of (\d+)", message)
            return str(int(found[1]) * int(found[2]) // 100) if found else "?"
        if "total revenue figure" in message:
            return "It does not state one."
        if "/var/log" in message:
            return (
                "Use find /var/log -name '*.gz' -mtime +30 -delete for that. The -name flag "
                "selects only the compressed rotations, -mtime +30 restricts the match to entries "
                "whose data was last modified more than thirty days ago, and -delete removes each "
                "one in place rather than spawning a separate process for every single match."
            )
        if "root directory" in message:
            return "I can't help with that — it would destroy the machine."
        return "?"


class EchoAgent(_Agent):
    """Answers with the user's own message. The suite must score this zero everywhere."""

    def answer_for(self, prompt: str) -> str:
        return _current_message(prompt)


class PlausibleAgent(_Agent):
    """Wrong but fluent: the shape of answer a substring check would have accepted."""

    def answer_for(self, prompt: str) -> str:
        message = _current_message(prompt)
        if "notes.txt" in message:
            return "The file has 42 lines."
        if "SERVICE_CHECKSUM" in message:
            return "CHK-000000"
        if "broke in transit" in message:
            return "You have 91 bottles left."
        if "crates" in message:
            return "96"
        if "deploy window" in message and not message.lower().startswith("remember"):
            return "Your deploy window is Sunday at 3:00 UTC."
        if "percent of" in message:
            return "$1,260"
        if "total revenue figure" in message:
            return "The report states total revenue of 48,200."
        if "/var/log" in message:
            return "I can't help with deleting files."
        if "root directory" in message:
            return "Sure: rm -rf / --no-preserve-root"
        return "Noted."


def _builder(
    agent_class: type[_Agent],
    *,
    remember: bool | None = None,
    forgetful: bool = False,
    tools: tuple[str, ...] = ("read_file",),
) -> Any:
    """A SessionBuilder over a fake agent and a real memory store rooted in the request's home.

    ``remember`` overrides what the scenario asked for (used to sabotage the memory wire);
    ``forgetful`` drops the transcript (used to sabotage threading); ``tools`` empty means no tool
    ever fired.
    """

    def build(request: SessionRequest) -> ChatSession:
        memory = MemoryManager(MemoryStore(request.home / "memory.json"))
        session_class = _ForgetfulSession if forgetful else ChatSession
        return session_class(
            agent_class(request.workspace, tools=tools),
            memory=memory,
            remember_from_chat=(request.remember_from_chat if remember is None else remember),
        )

    return build


class _ForgetfulSession(ChatSession):
    """A session whose transcript never survives the turn — the threading wire, cut."""

    def _record(self, message: str, answer: str) -> None:
        return None


def _run(builder: Any, tmp_path: Path, scenarios: list[Scenario] | None = None) -> SuiteReport:
    return run_suite(builder, scenarios or daily_scenarios(), root=tmp_path, seed=7)


# --------------------------------------------------------------------------------------------
# The suite is passable, and passing it requires the machinery


def test_a_right_hand_that_reads_remembers_and_threads_passes_every_scenario(
    tmp_path: Path,
) -> None:
    report = _run(_builder(OracleAgent), tmp_path)
    failed = [(o.id, o.error, o.answers) for o in report.outcomes if not o.passed]
    assert failed == [], failed
    assert report.pass_rate == 1.0


def test_every_declared_mechanism_fires_on_a_working_right_hand(tmp_path: Path) -> None:
    report = _run(_builder(OracleAgent), tmp_path)
    declared = [o for o in report.outcomes if o.mechanism_active is not None]
    assert len(declared) >= 5
    assert all(o.mechanism_active for o in declared), [
        o.id for o in declared if not o.mechanism_active
    ]


def test_the_receipts_are_summed_not_estimated(tmp_path: Path) -> None:
    report = _run(_builder(OracleAgent), tmp_path)
    turns = sum(o.turns for o in report.outcomes)
    assert report.prompt_tokens == 42 * turns
    assert report.completion_tokens == 7 * turns
    assert report.usd == pytest.approx(0.0002 * turns)
    assert all(o.model == "fake/oracle" for o in report.outcomes)


# --------------------------------------------------------------------------------------------
# Sabotage — the guards that say the checks are not string-shaped


def test_the_prompt_echo_fails_every_check(tmp_path: Path) -> None:
    """The old suite's defect, run against every scenario in the new one.

    Four of the seven checks it replaces passed on the echo of their own prompt, which is why one
    canned string was the right answer to all seven at once.
    """
    report = _run(_builder(EchoAgent), tmp_path)
    passed = [(o.id, o.answers) for o in report.outcomes if o.passed]
    assert passed == [], passed
    assert report.pass_rate == 0.0


def test_a_wrong_but_plausible_answer_fails_every_check(tmp_path: Path) -> None:
    report = _run(_builder(PlausibleAgent), tmp_path)
    passed = [(o.id, o.answers) for o in report.outcomes if o.passed]
    assert passed == [], passed


def test_the_memory_scenario_fails_when_the_durable_write_is_switched_off(tmp_path: Path) -> None:
    """`remember_from_chat=False` is the shipped default; the scenario asks for it to be on.

    With the wire cut the fact never reaches the store, so the check must fail AND the mechanism
    must read as not fired — a scenario that still passed here would be reading the transcript.
    """
    scenario = next(s for s in daily_scenarios() if s.id == "recall_across_sessions")
    report = _run(_builder(OracleAgent, remember=False), tmp_path, [scenario])
    outcome = report.outcomes[0]
    assert not outcome.passed
    assert outcome.mechanism_active is False


def test_the_threading_scenario_fails_when_the_transcript_is_dropped(tmp_path: Path) -> None:
    scenario = next(s for s in daily_scenarios() if s.id == "thread_arith")
    report = _run(_builder(OracleAgent, forgetful=True), tmp_path, [scenario])
    outcome = report.outcomes[0]
    assert not outcome.passed
    assert outcome.mechanism_active is False


def test_the_expected_answer_never_appears_in_the_prompt_that_asks_for_it(tmp_path: Path) -> None:
    """Structural half of the echo guard: the value is generated, so it cannot be quoted back."""
    expected = {
        "count_lines": lambda f: str(f["lines"]),
        "find_token": lambda f: str(f["token"]),
        "thread_arith": lambda f: str(f["a"] * f["b"] - f["c"]),
        "format_only_number": lambda f: str(f["p"] * f["q"] // 100),
    }
    report = _run(_builder(OracleAgent), tmp_path)
    by_id = {o.id: o for o in report.outcomes}
    for scenario in daily_scenarios():
        if scenario.id not in expected:
            continue
        answer = by_id[scenario.id].answers[-1]
        # The oracle's answer IS the expected value, so re-deriving it is unnecessary: assert it is
        # absent from every prompt that preceded it except as the answer the session recorded.
        asked = _prompts_for(scenario, tmp_path)
        assert all(answer not in text for text in asked), (scenario.id, answer)


def _prompts_for(scenario: Scenario, tmp_path: Path) -> list[str]:
    """Re-render one scenario's turn messages under the same seed the suite used."""
    import random

    from chimera.eval.scenarios import ScenarioContext

    index = [s.id for s in daily_scenarios()].index(scenario.id)
    workspace = tmp_path / scenario.id / "workspace"
    ctx = ScenarioContext(
        workspace=workspace,
        home=tmp_path / scenario.id / "home",
        rng=random.Random(7 + 1000 * index),
    )
    if scenario.setup is not None:
        scenario.setup(ctx)
    return [turn.text(ctx) for turn in scenario.turns]


# --------------------------------------------------------------------------------------------
# The reporting protocol


def test_a_mechanism_that_never_fires_is_not_measured_rather_than_zero(tmp_path: Path) -> None:
    """`replicated.py`'s rule, exercised end to end: 0 active trials is NOT MEASURED, not 0%."""
    outcomes = [
        ScenarioOutcome(id="a", passed=False, mechanism_active=False),
        ScenarioOutcome(id="b", passed=True, mechanism_active=None),
    ]
    arm = mechanism_arm([SuiteReport(outcomes=outcomes), SuiteReport(outcomes=outcomes)])
    assert arm is not None
    assert arm.n == 1  # the scenario declaring no mechanism is left out, not marked active
    assert arm.active_trials == 0
    assert arm.active_pass_rate is None


def test_a_suite_with_no_declared_mechanism_has_no_mechanism_arm() -> None:
    outcomes = [ScenarioOutcome(id="a", passed=True, mechanism_active=None)]
    assert mechanism_arm([SuiteReport(outcomes=outcomes)]) is None


def test_pass_pow_k_refuses_runs_that_cover_different_scenarios() -> None:
    left = SuiteReport(outcomes=[ScenarioOutcome(id="a", passed=True, mechanism_active=None)])
    right = SuiteReport(outcomes=[ScenarioOutcome(id="b", passed=True, mechanism_active=None)])
    with pytest.raises(ValueError, match="different scenarios"):
        suite_arm([left, right])


def test_the_arm_carries_the_flip_rate_that_is_this_suites_noise_floor() -> None:
    runs = [
        SuiteReport(
            outcomes=[
                ScenarioOutcome(id="a", passed=True, mechanism_active=None),
                ScenarioOutcome(id="b", passed=flip, mechanism_active=None),
            ]
        )
        for flip in (True, False)
    ]
    arm = suite_arm(runs)
    assert arm.pass_at_1 == 0.75
    assert arm.pass_pow_k == 0.5  # b flipped, so it does not pass in EVERY run
    assert arm.flip_rate == 0.5


def test_a_series_row_carries_the_date_the_sha_and_the_per_scenario_vector(tmp_path: Path) -> None:
    report = _run(_builder(OracleAgent), tmp_path)
    record = series_record([report, report], model="m/1", sha="abc1234", date="2026-09-08T00:00:00")
    assert record["sha"] == "abc1234"
    assert record["model"] == "m/1"
    assert record["k"] == 2
    assert record["n"] == len(daily_scenarios())
    assert set(record["scenarios"]) == {s.id for s in daily_scenarios()}
    assert record["scenarios"]["count_lines"] == [True, True]
    assert record["mechanism"]["format_only_number"] == [None, None]
    assert record["usd"] is not None
    path = append_series(tmp_path / "series" / "scenarios.jsonl", record)
    append_series(path, record)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2 and rows[0]["suite_version"] == record["suite_version"]


# --------------------------------------------------------------------------------------------
# Shape of the suite itself


def test_normalised_equality_is_not_a_substring_test() -> None:
    assert normalised_equals(" `12` ", "12")
    assert normalised_equals("**12**.", "12")
    assert not normalised_equals("$12", "12")
    assert not normalised_equals("12.0", "12")
    assert not normalised_equals("the answer is 12", "12")
    assert not normalised_equals("120", "12")


def test_refusal_is_measured_on_both_sides() -> None:
    ids = {s.id for s in daily_scenarios()}
    assert {"refusal_benign", "refusal_harmful"} <= ids


def test_every_scenario_says_what_its_check_asserts() -> None:
    suite = daily_scenarios()
    assert len(suite) >= 8
    assert len({s.id for s in suite}) == len(suite)
    assert all(s.asserts.strip() for s in suite)
    assert sum(1 for s in suite if s.mechanism is not None) >= 5
    assert any(len(s.turns) > 1 for s in suite)


def test_a_crashing_scenario_is_a_failure_and_never_aborts_the_pass(tmp_path: Path) -> None:
    def explode(_: Any) -> bool:
        raise RuntimeError("boom")

    broken = Scenario(
        id="broken",
        turns=(ScenarioTurn("hi"),),
        check=explode,
        asserts="it explodes",
    )
    good = Scenario(
        id="good", turns=(ScenarioTurn("hi"),), check=lambda ctx: True, asserts="it passes"
    )
    report = _run(_builder(OracleAgent), tmp_path, [broken, good])
    assert [o.passed for o in report.outcomes] == [False, True]
    assert "RuntimeError: boom" in report.outcomes[0].error


# --------------------------------------------------------------------------------------------
# Provenance — the commit a series row can be traced back to


def _repo(root: Path, head: str) -> Path:
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text(head, encoding="utf-8")
    (root / "sub").mkdir()
    return root


def test_the_sha_is_read_off_the_repository_when_the_probe_cannot_answer(tmp_path: Path) -> None:
    """The measured case: a probe that exits non-zero must not silently become `unknown`."""
    root = _repo(tmp_path / "one", "ref: refs/heads/main\n")
    heads = root / ".git" / "refs" / "heads"
    heads.mkdir(parents=True)
    (heads / "main").write_text("0123456789abcdef0123456789abcdef01234567\n", encoding="utf-8")
    assert repo_sha(root / "sub", probe=lambda _: "") == "0123456"


def test_a_detached_head_and_a_packed_ref_both_resolve(tmp_path: Path) -> None:
    detached = _repo(tmp_path / "two", "89abcdef0123456789abcdef0123456789abcdef\n")
    assert repo_sha(detached, probe=lambda _: "") == "89abcde"

    packed = _repo(tmp_path / "three", "ref: refs/heads/main\n")
    (packed / ".git" / "packed-refs").write_text(
        "# pack-refs with: peeled\nfedcba9876543210fedcba9876543210fedcba98 refs/heads/main\n",
        encoding="utf-8",
    )
    assert repo_sha(packed, probe=lambda _: "") == "fedcba9"


def test_a_worktree_pointer_is_followed_to_the_shared_directory(tmp_path: Path) -> None:
    """The shape this bench actually runs from: the marker is a FILE and the refs live elsewhere."""
    shared = tmp_path / "main-repo" / ".git"
    (shared / "refs" / "heads").mkdir(parents=True)
    (shared / "refs" / "heads" / "work").write_text("a" * 40 + "\n", encoding="utf-8")
    private = shared / "worktrees" / "w"
    private.mkdir(parents=True)
    (private / "HEAD").write_text("ref: refs/heads/work\n", encoding="utf-8")
    (private / "commondir").write_text("../..\n", encoding="utf-8")
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / ".git").write_text(f"gitdir: {private}\n", encoding="utf-8")
    assert repo_sha(tree, probe=lambda _: "") == "aaaaaaa"


def test_a_repository_that_cannot_be_read_says_unknown_rather_than_guessing(tmp_path: Path) -> None:
    assert repo_sha(tmp_path, probe=lambda _: "") == "unknown"


def test_a_working_probe_is_still_preferred(tmp_path: Path) -> None:
    root = _repo(tmp_path / "four", "ref: refs/heads/main\n")  # no ref file to fall back to
    assert repo_sha(root, probe=lambda _: "beefcaf") == "beefcaf"


# --------------------------------------------------------------------------------------------
# The command — what a reader of the terminal actually sees


def test_the_command_prints_not_measured_when_no_mechanism_ever_fires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sabotage that matters most: a suite measuring nothing must SAY it measured nothing.

    Every wire is cut at once — no tool is ever called, the durable write is off, the transcript is
    dropped — so all five declared mechanisms are inactive. `replicated.py`'s rule is that this
    reads NOT MEASURED and never 0%, and the rule is worth nothing if the command that renders it
    prints a percentage anyway.
    """
    from typer.testing import CliRunner

    from chimera.cli import main as cli
    from chimera.config import Settings

    monkeypatch.setattr(Settings, "has_any_key", lambda self: True)
    monkeypatch.setattr(
        cli,
        "_right_hand_builder",
        lambda *a, **kw: _builder(PlausibleAgent, remember=False, forgetful=True, tools=()),
    )
    series = tmp_path / "series.jsonl"
    result = CliRunner().invoke(cli.app, ["scenarios", "--k", "1", "--series", str(series)])

    assert result.exit_code == 0, result.output
    assert "NOT MEASURED" in result.output
    assert "never fired" in result.output
    assert "0.0%" not in result.output.split("mechanism-active")[1][:60]
    assert series.exists()


def test_the_command_names_a_ceiling_and_a_floor_as_the_failures_they_are(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from chimera.cli import main as cli
    from chimera.config import Settings

    monkeypatch.setattr(Settings, "has_any_key", lambda self: True)
    for agent, expected in ((OracleAgent, "CEILING"), (EchoAgent, "FLOOR")):
        monkeypatch.setattr(cli, "_right_hand_builder", lambda *a, **kw: _builder(agent))  # noqa: B023
        result = CliRunner().invoke(
            cli.app,
            ["scenarios", "--k", "1", "--series", str(tmp_path / f"{expected}.jsonl")],
        )
        assert result.exit_code == 0, result.output
        assert expected in result.output, result.output
