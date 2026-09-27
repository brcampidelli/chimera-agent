"""An answer written from attached documents is checked against them before it ships (study 26).

`bench/verified_cascade/RESULTS.md` measured one policy on 400 paired items — read the draft as a
``supported / unsupported / declined`` Choice; ship it when supported at p >= 0.8; ship a decline as
it is; otherwise escalate to the strong model and read that answer the same way — and the owner
chose the variant that ships the decline instead of handing off. These tests hold the product to
that policy step by step, to the bench's instrument byte for byte, to the fallback chain the owner
asked for (local down → Jev with a key → the lexical gate, always named on the receipt), and to the
scope: no sources, no gate; a tool-using turn is never gated.

Nothing here touches the network, the real ``.env`` or ``~/.chimera``: every verifier is a fake
slot or a backend that is never asked, and every Settings reads no env file and a ``tmp_path`` home.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bench.verified_cascade.harness import DRAFT_SYSTEM as BENCH_DRAFT_SYSTEM
from bench.verified_cascade.harness import decision_question, decision_state
from bench.verified_cascade.harness import draft_messages as bench_messages
from chimera.config import Settings
from chimera.core.agent import AgentResult
from chimera.core.code_session import CodeSession
from chimera.fusion import verified
from chimera.fusion.verified import (
    DECLINE_TEXT,
    DRAFT_SYSTEM,
    MAX_SOURCE_CHARS,
    QUESTION,
    GroundedAnswers,
    GroundedTurn,
    GroundedVerifier,
    LexicalSlot,
    Read,
    Redraft,
    badge,
    build_verifier,
    check_answer,
    draft_messages,
    escalation_model,
    grounded_state,
)
from chimera.interface import ChatSession

EXCERPTS = ["The pool runs 4 workers by default.", "Set CHIMERA_POOL to change it."]
TURN = GroundedTurn.make(EXCERPTS, "How many workers does the pool run?", ["attachments"])
DRAFT = "It runs 4 workers by default."
STRONG = "The pool runs 4 workers by default [1]."


class FakeSlot:
    """A verifier that answers from a script: one reading per answer text, in order."""

    def __init__(self, reads: dict[str, Read], *, backend: str = "local_logprob", model: str = "qwen3:4b",
                 unavailable: str = "") -> None:
        self.reads = reads
        self.backend = backend
        self.model = model
        self._unavailable = unavailable
        self.asked: list[str] = []

    def unavailable(self) -> str:
        return self._unavailable

    def read(self, turn: GroundedTurn, answer: str) -> Read:
        self.asked.append(answer)
        return self.reads[answer]


def sup(p: float | None) -> Read:
    return Read(label="supported", p=p, usd=0.0001)


def escalate_to(text: str = STRONG, *, error: str = "") -> Any:
    calls: list[int] = []

    def go() -> Redraft:
        calls.append(1)
        return Redraft(text=text, model="openrouter/openai/gpt-6-sol", usd=0.012, error=error)

    go.calls = calls  # type: ignore[attr-defined]
    return go


def run(reads: dict[str, Read], escalate: Any = None, threshold: float = 0.8) -> Any:
    assert TURN is not None
    return GroundedVerifier([FakeSlot(reads)], threshold=threshold).verify(TURN, DRAFT, escalate=escalate)


# --- the instrument is the bench's, byte for byte -------------------------------------------------

def test_the_question_the_state_and_the_escalation_prompt_are_the_ones_the_bench_measured() -> None:
    bench_q = decision_question()
    assert (QUESTION.key, QUESTION.instructions, QUESTION.options) == (bench_q.key, bench_q.instructions, bench_q.options)
    assert QUESTION.criteria == bench_q.criteria and QUESTION.event == bench_q.event
    assert grounded_state(EXCERPTS, "q?", "a.") == decision_state(EXCERPTS, "q?", "a.")
    assert list(json.loads(grounded_state(EXCERPTS, "q?", "a.")))[:3] == ["excerpts", "question", "answer"]
    assert DRAFT_SYSTEM == BENCH_DRAFT_SYSTEM
    assert TURN is not None
    assert draft_messages(TURN) == bench_messages(list(TURN.excerpts), TURN.question)


# --- the policy, label by label and at the threshold's edge ---------------------------------------

def test_a_supported_draft_at_exactly_the_threshold_ships_without_escalating() -> None:
    esc = escalate_to()
    result = run({DRAFT: sup(0.8)}, esc)

    assert (result.text, result.outcome, result.escalated) == (DRAFT, "supported", False)
    assert esc.calls == []


def test_a_supported_draft_just_under_the_threshold_escalates() -> None:
    result = run({DRAFT: sup(0.7999), STRONG: sup(0.95)}, escalate_to())

    assert (result.text, result.outcome, result.escalated) == (STRONG, "escalated", True)
    assert result.withheld == [DRAFT]


def test_supported_without_a_probability_is_not_accepted() -> None:
    # A local read whose label token collided has a choice and no p: the bench's `accepts` needs both.
    result = run({DRAFT: sup(None), STRONG: sup(0.9)}, escalate_to())

    assert result.outcome == "escalated"


def test_an_unsupported_draft_escalates_and_the_escalated_answer_is_read_again() -> None:
    slot = FakeSlot({DRAFT: Read("unsupported", 0.1), STRONG: sup(0.9)})
    assert TURN is not None
    result = GroundedVerifier([slot]).verify(TURN, DRAFT, escalate=escalate_to())

    assert slot.asked == [DRAFT, STRONG]  # verified twice, by the same verifier, as measured
    assert (result.text, result.outcome, result.escalated_label, result.escalated_p) == (STRONG, "escalated", "supported", 0.9)
    assert result.receipt()["usd_extra"] == pytest.approx(0.0001 + 0.012)


def test_a_declined_draft_ships_the_decline_and_never_escalates() -> None:
    esc = escalate_to()
    result = run({DRAFT: Read("declined", 0.02)}, esc)

    assert (result.text, result.outcome, result.decline_shipped) == (DRAFT, "declined", True)
    assert esc.calls == []


def test_when_the_escalated_answer_fails_too_the_decline_ships_and_both_answers_are_kept() -> None:
    result = run({DRAFT: Read("unsupported", 0.1), STRONG: Read("unsupported", 0.2)}, escalate_to())

    assert (result.text, result.outcome, result.decline_shipped) == (DECLINE_TEXT, "declined", True)
    assert result.withheld == [DRAFT, STRONG]


def test_a_strong_model_that_declines_ships_its_own_decline() -> None:
    result = run({DRAFT: Read("unsupported", 0.1), STRONG: Read("declined", 0.1)}, escalate_to())

    assert (result.text, result.outcome) == (STRONG, "declined")
    assert result.withheld == [DRAFT]


def test_a_failed_escalation_ships_the_decline_with_the_reason() -> None:
    result = run({DRAFT: Read("unsupported", 0.1)}, escalate_to("", error="APIError: 502"))

    assert (result.text, result.outcome) == (DECLINE_TEXT, "declined")
    assert result.halt == "escalation: APIError: 502"


def test_a_threshold_the_owner_moved_is_the_one_read() -> None:
    assert run({DRAFT: sup(0.6)}, escalate_to(), threshold=0.5).outcome == "supported"
    assert run({DRAFT: sup(0.6), STRONG: sup(0.95)}, escalate_to(), threshold=0.9).outcome == "escalated"


# --- a verifier that fails never costs the answer and never passes for a verdict --------------------

def test_a_verifier_halt_ships_the_draft_marked_unverified() -> None:
    esc = escalate_to()
    result = run({DRAFT: Read(None, None, halt="ConnectError: refused")}, esc)

    assert (result.text, result.outcome, result.halt) == (DRAFT, "unverified", "ConnectError: refused")
    assert esc.calls == []
    assert "not verified" in badge(result.receipt())


def test_a_halt_on_the_escalated_read_ships_the_strong_answer_unverified() -> None:
    result = run({DRAFT: Read("unsupported", 0.1), STRONG: Read(None, None, halt="timeout")}, escalate_to())

    assert (result.text, result.outcome, result.escalated) == (STRONG, "unverified", True)


def test_an_unexpected_error_in_the_check_ships_the_draft_with_the_error_on_the_receipt() -> None:
    def boom() -> GroundedAnswers:
        raise RuntimeError("verifier exploded")

    answer, block, extra = check_answer(boom, TURN, DRAFT, tool_names=[], stopped_reason="final")

    assert (answer, extra) == (DRAFT, 0.0)
    assert block is not None and block["outcome"] == "unverified" and "verifier exploded" in block["halt"]


# --- the fallback chain, and the receipt says which verifier ran ----------------------------------

def test_the_local_verifier_down_falls_back_to_the_next_and_the_receipt_names_both() -> None:
    local = FakeSlot({}, unavailable="ollama_unreachable")
    jev = FakeSlot({DRAFT: sup(0.97)}, backend="openrouter_decisions", model="typesafe/jev-1.13")
    assert TURN is not None
    receipt = GroundedVerifier([local, jev, LexicalSlot()]).verify(TURN, DRAFT, escalate=None).receipt()

    assert receipt["verifier"]["model"] == "typesafe/jev-1.13"
    assert receipt["verifier"]["fell_back_from"] == [
        {"backend": "local_logprob", "model": "qwen3:4b", "reason": "ollama_unreachable"}
    ]
    assert local.asked == []


def test_a_local_read_that_halts_falls_through_too() -> None:
    local = FakeSlot({DRAFT: Read(None, None, halt="HTTPStatusError: 404 model not found")})
    assert TURN is not None
    receipt = GroundedVerifier([local, LexicalSlot()]).verify(TURN, DRAFT, escalate=None).receipt()

    assert receipt["verifier"]["backend"] == "lexical"
    assert receipt["verifier"]["fell_back_from"][0]["reason"].startswith("halt: HTTPStatusError")


def test_the_lexical_fallback_is_never_called_verified() -> None:
    local = FakeSlot({}, unavailable="model_absent")
    assert TURN is not None
    result = GroundedVerifier([local, LexicalSlot()]).verify(TURN, DRAFT, escalate=None)

    assert result.outcome == "lexical"
    assert "lexical check only" in badge(result.receipt())
    assert "verified" not in badge(result.receipt()).split("—")[0]


def _settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **env: Any) -> Settings:
    for key in ("OPENROUTER_API_KEY", "CHIMERA_OPENROUTER_KEYS", "CHIMERA_DECISION_BACKEND", "CHIMERA_DECISION_MODEL",
                "CHIMERA_VERIFIED_ANSWERS", "CHIMERA_VERIFIED_ANSWERS_THRESHOLD",
                "CHIMERA_VERIFIED_ANSWERS_ESCALATE_MODEL"):
        monkeypatch.delenv(key, raising=False)
    return Settings(_env_file=None, CHIMERA_HOME=str(tmp_path / "home"), **env)  # type: ignore[call-arg]


def _chain(verifier: GroundedVerifier | None) -> list[tuple[str, str]]:
    assert verifier is not None
    return [(s.backend, s.model) for s in verifier.chain]


def test_the_default_chain_is_local_then_jev_with_a_key_then_lexical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with_key = build_verifier(_settings(tmp_path, monkeypatch, OPENROUTER_API_KEY="sk-test"))
    without = build_verifier(_settings(tmp_path, monkeypatch))

    assert _chain(with_key) == [("local_logprob", "qwen3:4b"), ("openrouter_decisions", "typesafe/jev-1.13"),
                                ("lexical", "default_gate")]
    assert _chain(without) == [("local_logprob", "qwen3:4b"), ("lexical", "default_gate")]


def test_a_backend_the_owner_chose_has_no_silent_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    chosen = build_verifier(_settings(
        tmp_path, monkeypatch, OPENROUTER_API_KEY="sk-test", CHIMERA_DECISION_BACKEND="openrouter_decisions",
        CHIMERA_DECISION_MODEL="jaredpalmer/kev-4b",
    ))
    unbuildable = build_verifier(_settings(tmp_path, monkeypatch, CHIMERA_DECISION_BACKEND="openrouter_decisions"))

    assert _chain(chosen) == [("openrouter_decisions", "jaredpalmer/kev-4b")]
    # No key: the chosen backend cannot exist. It is named as skipped, and only the lexical gate runs.
    assert _chain(unbuildable) == [("openrouter_decisions", ""), ("lexical", "default_gate")]
    assert TURN is not None and unbuildable is not None
    receipt = unbuildable.verify(TURN, DRAFT, escalate=None).receipt()
    assert receipt["verifier"]["fell_back_from"][0]["reason"] == "not_buildable"


def test_the_setting_off_means_no_verifier_and_the_old_behaviour(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path, monkeypatch, CHIMERA_VERIFIED_ANSWERS="false")

    assert build_verifier(settings) is None
    answer, block, extra = check_answer(
        lambda: verified.build_grounded_answers(settings, gateway=None), TURN, DRAFT,
        tool_names=[], stopped_reason="final",
    )
    assert (answer, block, extra) == (DRAFT, None, 0.0)


def test_the_local_probe_names_why_and_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.providers import ollama

    asked: list[str] = []

    def installed(base: str, *, timeout_s: float) -> Any:
        asked.append(base)
        return ollama.InstalledModels(base, True, models=("llama3:8b",))

    monkeypatch.setattr(ollama, "installed_models", installed)
    monkeypatch.setattr(verified, "_probe_cache", {})
    clock = [100.0]
    assert verified.local_probe("http://h:1", "qwen3:4b", now=lambda: clock[0]) == "model_absent"
    assert verified.local_probe("http://h:1", "qwen3:4b", now=lambda: clock[0]) == "model_absent"
    assert asked == ["http://h:1"]
    clock[0] += verified.PROBE_TTL_S + 1
    monkeypatch.setattr(ollama, "installed_models", lambda base, *, timeout_s: ollama.InstalledModels(base, False, reason="unreachable"))
    assert verified.local_probe("http://h:1", "qwen3:4b", now=lambda: clock[0]) == "ollama_unreachable"


def test_the_escalation_is_the_measured_model_when_a_key_reaches_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert escalation_model(_settings(tmp_path, monkeypatch, OPENROUTER_API_KEY="sk-test")) == "openrouter/openai/gpt-6-sol"
    no_key = _settings(tmp_path, monkeypatch)
    monkeypatch.setattr(type(no_key), "configured_providers", lambda self: ["anthropic"])
    assert escalation_model(no_key) == no_key.tier_ladder().top
    explicit = _settings(tmp_path, monkeypatch, CHIMERA_VERIFIED_ANSWERS_ESCALATE_MODEL="openrouter/x/y")
    assert escalation_model(explicit) == "openrouter/x/y"


# --- the scope: no sources, no gate; tool turns are never gated ------------------------------------

def test_no_sources_no_gate() -> None:
    assert GroundedTurn.make([], "q?", ["attachments"]) is None
    assert GroundedTurn.make(["  ", ""], "q?", ["attachments"]) is None
    assert GroundedTurn.make(["text"], "  ", ["attachments"]) is None


def _answers(slot: FakeSlot) -> GroundedAnswers:
    return GroundedAnswers(GroundedVerifier([slot]), lambda turn: escalate_to()())


@pytest.mark.parametrize(
    ("tools", "stopped", "excerpts", "reason"),
    [
        (["read_file"], "final", EXCERPTS, "tool_calls"),
        ([], "max_steps", EXCERPTS, "not_final"),
        ([], "final", ["x" * (MAX_SOURCE_CHARS + 1)], "sources_too_long"),
    ],
)
def test_a_turn_outside_the_measured_shape_is_not_gated_and_says_why(
    tools: list[str], stopped: str, excerpts: list[str], reason: str,
) -> None:
    slot = FakeSlot({})
    turn = GroundedTurn.make(excerpts, "q?", ["attachments"])
    answer, block = _answers(slot).check(turn, DRAFT, tool_names=tools, stopped_reason=stopped)

    assert (answer, block) == (DRAFT, {"outcome": "not_applied", "reason": reason, "sources": ["attachments"]})
    assert slot.asked == []  # no verifier call for a turn it does not apply to


# --- the terminal's chat session ---------------------------------------------------------------------

class _Agent:
    def __init__(self, answer: str, tools: list[str] | None = None) -> None:
        self.answer = answer
        self.tools = tools or []
        self.tasks: list[str] = []

    def run(self, task: str, **_: Any) -> AgentResult:
        self.tasks.append(task)
        return AgentResult(answer=self.answer, steps=1, stopped_reason="final", tool_names=list(self.tools),
                           model="m", usd=0.001)


def test_a_chat_turn_with_a_document_ships_what_the_check_decided_and_records_it() -> None:
    slot = FakeSlot({"It runs 9 workers.": Read("unsupported", 0.1), STRONG: Read("unsupported", 0.3)})
    agent = _Agent("It runs 9 workers.")
    session = ChatSession(agent, gate=None, grounded_answers=lambda: _answers(slot))

    report = session.send_verbose("How many workers?", documents=[("pool.md", EXCERPTS[0])])

    assert report.answer == DECLINE_TEXT
    assert report.grounded is not None and report.grounded["outcome"] == "declined"
    assert report.grounded["withheld"] == ["It runs 9 workers.", STRONG]
    assert session.turns[-1].assistant == DECLINE_TEXT  # the next turn's history is what shipped
    assert "Attached document `pool.md`" in agent.tasks[0] and verified.GROUNDED_NOTE in agent.tasks[0]
    assert report.usd == pytest.approx(0.001 + 0.012)  # the escalation is on the turn's bill


def test_a_chat_turn_that_used_a_tool_is_not_gated() -> None:
    slot = FakeSlot({})
    session = ChatSession(_Agent("It runs 4.", tools=["read_file"]), gate=None, grounded_answers=lambda: _answers(slot))

    report = session.send_verbose("How many?", documents=[("pool.md", EXCERPTS[0])])

    assert report.answer == "It runs 4."
    assert report.grounded == {"outcome": "not_applied", "reason": "tool_calls", "sources": ["attachments"]}
    assert slot.asked == []


def test_a_chat_turn_without_documents_never_builds_the_check() -> None:
    built: list[int] = []
    agent = _Agent("hi")
    session = ChatSession(agent, gate=None, grounded_answers=lambda: built.append(1))  # type: ignore[arg-type,func-returns-value]

    report = session.send_verbose("hello")

    assert (report.answer, report.grounded, built) == ("hi", None, [])
    assert agent.tasks[0].endswith("User: hello")  # byte-identical to a session with no check at all


# --- the stored conversation holds what shipped -------------------------------------------------------

def test_the_code_session_replaces_only_a_final_answer() -> None:
    session = CodeSession(agent=None)  # type: ignore[arg-type]
    session.messages = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "draft"}]
    assert session.replace_last_answer("shipped") is True
    assert session.messages[-1] == {"role": "assistant", "content": "shipped"}

    session.messages.append({"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]})
    assert session.replace_last_answer("x") is False


def test_the_terminal_badge_says_each_outcome_differently() -> None:
    lines = {
        o: badge({"outcome": o, "verifier": {"model": "qwen3:4b"}, "escalated_model": "sol", "halt": "down"})
        for o in ("supported", "escalated", "declined", "lexical", "unverified")
    }
    assert len(set(lines.values())) == 5
    assert lines["supported"].startswith("✓ verified")
    assert lines["declined"] == "∅ the sources provided don't cover this"
    assert "not verified (down)" in lines["unverified"]
    assert badge(None) == ""


# --- the desktop's coding turn -----------------------------------------------------------------------

class _CodeAgent:
    """Answers once, with or without a tool call, and records the task it was handed."""

    def __init__(self, answer: str, tools: list[str] | None = None) -> None:
        from chimera.core.context_budget import RunState

        self.answer = answer
        self.tools = tools or []
        self.tasks: list[str] = []
        self.run_state = RunState()

    def run(self, task: str, *, history: list[Any] | None = None, **_: Any) -> AgentResult:
        self.tasks.append(task)
        transcript = [*(history or []), {"role": "user", "content": task}, {"role": "assistant", "content": self.answer}]
        return AgentResult(answer=self.answer, steps=1, stopped_reason="final", transcript=transcript,
                           tool_names=list(self.tools), model="test/model", usd=0.002)


def _code_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: _CodeAgent, slot: FakeSlot,
) -> tuple[Any, dict[str, Any], list[Any]]:
    pytest.importorskip("fastapi")
    pytest.importorskip("sse_starlette")
    from fastapi.testclient import TestClient

    import chimera.core
    from chimera.api import build_api_app

    monkeypatch.delenv("CHIMERA_VERIFIED_ANSWERS", raising=False)
    configs: list[Any] = []

    def build(*args: Any, **_k: Any) -> _CodeAgent:
        configs.append(args[2])  # Agent(gateway, registry, AgentConfig(...), ...)
        return agent

    monkeypatch.setattr(chimera.core, "Agent", build, raising=True)
    monkeypatch.setattr(verified, "build_grounded_answers", lambda settings, gateway: _answers(slot))
    ws = tmp_path / "ws"
    ws.mkdir()
    settings = Settings(_env_file=None, CHIMERA_HOME=str(tmp_path / "home"))  # type: ignore[call-arg]
    client = TestClient(build_api_app(lambda: ChatSession(_Agent("x")), workspace=ws, settings=settings))
    upload = client.post("/api/attachments", files={"file": ("pool.md", EXCERPTS[0].encode(), "text/markdown")})
    assert upload.status_code == 200, upload.text
    response = client.post(
        "/api/code/turn", json={"message": "How many workers?", "attachments": [upload.json()["id"]]}
    )
    frames = []
    event = ""
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: "):]
        elif line.startswith("data: "):
            frames.append((event, json.loads(line[len("data: "):])))
    done = next(data for name, data in frames if name == "done")
    done["session_id"] = next(data for name, data in frames if name == "session")["session_id"]
    return client, done, configs


def test_a_coding_turn_with_a_document_ships_the_decline_and_stores_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot = FakeSlot({"It runs 9 workers.": Read("unsupported", 0.05), STRONG: Read("unsupported", 0.2)})
    client, done, _ = _code_turn(tmp_path, monkeypatch, _CodeAgent("It runs 9 workers."), slot)

    assert done["answer"] == DECLINE_TEXT
    assert done["grounded"]["outcome"] == "declined" and done["grounded"]["withheld"][0] == "It runs 9 workers."
    assert done["usd"] == pytest.approx(0.002 + 0.012)
    # A reopened conversation shows what shipped, and the next turn's history is built on it.
    stored = client.get(f"/api/code/sessions/{done['session_id']}")
    assert stored.status_code == 200, stored.text
    assert DECLINE_TEXT in stored.text and "It runs 9 workers." not in json.dumps(stored.json().get("messages"))


def test_a_coding_turn_that_called_a_tool_keeps_its_answer_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot = FakeSlot({})
    _, done, _ = _code_turn(tmp_path, monkeypatch, _CodeAgent("It runs 4.", tools=["read_file"]), slot)

    assert done["answer"] == "It runs 4."
    assert done["grounded"] == {"outcome": "not_applied", "reason": "tool_calls", "sources": ["attachments"]}
    assert slot.asked == []


def test_a_coding_turn_is_told_the_rule_it_is_checked_by(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _CodeAgent("The pool runs 4 workers by default.")
    slot = FakeSlot({"The pool runs 4 workers by default.": sup(0.97)})
    _, done, configs = _code_turn(tmp_path, monkeypatch, agent, slot)

    assert done["grounded"]["outcome"] == "supported"
    assert done["answer"] == "The pool runs 4 workers by default."
    assert verified.GROUNDED_NOTE in configs[0].turn_notes  # in the turn context, not the stored message
    assert verified.GROUNDED_NOTE not in agent.tasks[0]


# --- the terminal's /attach -----------------------------------------------------------------------------

def test_attach_reads_a_document_for_the_next_message_through_the_app_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chimera.cli import main

    doc = tmp_path / "pool.md"
    doc.write_text(EXCERPTS[0], encoding="utf-8")
    settings = _settings(tmp_path, monkeypatch)
    pending: list[tuple[str, str]] = []

    main._attach_document(str(doc), settings, pending)
    main._attach_document(str(tmp_path / "missing.md"), settings, pending)
    main._attach_document(str(tmp_path / "photo.png"), settings, pending)

    assert [name for name, _ in pending] == ["pool.md"]
    assert EXCERPTS[0] in pending[0][1] and "<<" in pending[0][1]  # sanitized and fenced, as the app does
    assert "/attach" in [c.name for c in main._chat_commands()]
    assert "/attach" in [c.name for c in main._assist_commands()]


def test_the_terminal_prints_the_badge_under_the_reply() -> None:
    from chimera.interface import render
    from chimera.interface.session import TurnReport

    declined = TurnReport(answer=DECLINE_TEXT, grounded={"outcome": "declined", "verifier": {}})
    skipped = TurnReport(answer="x", grounded={"outcome": "not_applied", "reason": "tool_calls"})

    assert "the sources provided don't cover this" in render.grounded_line(declined)
    assert render.grounded_line(skipped).startswith("[dim]") and "used tools" in render.grounded_line(skipped)
    assert render.grounded_line(TurnReport(answer="x")) == ""
