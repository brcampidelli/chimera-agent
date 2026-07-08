"""Tests for the FrugalGPT cascade + route log (M16-A6)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.fusion.cascade import CascadeBackend, CascadeConfig, default_gate
from chimera.fusion.route_log import (
    RouteRecord,
    append_route,
    format_route_summary,
    load_routes,
    summarize_routes,
)
from chimera.fusion.router import RoutingPolicy
from chimera.providers.gateway import CompletionResult, MessageLike

WEAK = "weak/model:free"
MID = "mid/model"


class ScriptedGateway:
    """Answers per model slug from a script; records every call."""

    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers
        self.calls: list[str] = []

    def complete(
        self, messages: list[MessageLike], *, model: str | None = None, **kwargs: Any
    ) -> CompletionResult:
        self.calls.append(model or "?")
        return CompletionResult(
            content=self.answers.get(model or "", "answer"),
            model=model or "?",
            prompt_tokens=100,
            completion_tokens=50,
        )


class ScriptedFusion:
    def __init__(self, answer: str = "fused answer") -> None:
        self.answer = answer
        self.calls = 0

    def complete(self, messages: list[MessageLike], **kwargs: Any) -> CompletionResult:
        self.calls += 1
        return CompletionResult(
            content=self.answer, model="fusion", prompt_tokens=900, completion_tokens=300
        )


def _cascade(
    gateway: ScriptedGateway,
    fusion: ScriptedFusion,
    tmp_path: Path | None = None,
    *,
    entry: str = "weak",
    agreement_k: int = 1,
    mode: str = "auto",
) -> CascadeBackend:
    config = CascadeConfig(
        weak=WEAK, mid=MID, entry=entry, agreement_k=agreement_k,
        log_path=(tmp_path / "routes.jsonl") if tmp_path else None,
    )
    return CascadeBackend(
        gateway, fusion, config, policy=RoutingPolicy(mode=mode),  # type: ignore[arg-type]
    )


def _user(text: str) -> list[MessageLike]:
    return [{"role": "user", "content": text}]


def test_easy_turn_accepted_at_weak_tier(tmp_path: Path) -> None:
    gateway = ScriptedGateway({WEAK: "the capital is Paris"})
    fusion = ScriptedFusion()
    backend = _cascade(gateway, fusion, tmp_path)
    result = backend.complete(_user("capital of France?"))
    assert result.content == "the capital is Paris"
    assert gateway.calls == [WEAK]
    assert fusion.calls == 0
    record = load_routes(tmp_path / "routes.jsonl")[0]
    assert record.accepted_tier == "weak"
    assert record.tiers_tried == ["weak"]


def test_weak_refusal_climbs_to_mid(tmp_path: Path) -> None:
    gateway = ScriptedGateway({WEAK: "I'm sorry, I cannot help with that.", MID: "42"})
    fusion = ScriptedFusion()
    backend = _cascade(gateway, fusion, tmp_path)
    result = backend.complete(_user("meaning of life?"))
    assert result.content == "42"
    assert gateway.calls == [WEAK, MID]
    record = load_routes(tmp_path / "routes.jsonl")[0]
    assert record.tiers_tried == ["weak", "mid"]
    assert record.accepted_tier == "mid"


def test_mid_refusal_climbs_to_fusion(tmp_path: Path) -> None:
    gateway = ScriptedGateway({WEAK: "", MID: ""})  # both fail the gate
    fusion = ScriptedFusion("fused: definitive")
    backend = _cascade(gateway, fusion, tmp_path)
    result = backend.complete(_user("hard question"))
    assert result.content == "fused: definitive"
    assert fusion.calls == 1
    record = load_routes(tmp_path / "routes.jsonl")[0]
    assert record.tiers_tried == ["weak", "mid", "fusion"]
    assert record.accepted_tier == "fusion"


def test_tool_turns_bypass_weak_straight_to_mid(tmp_path: Path) -> None:
    gateway = ScriptedGateway({MID: "tool call issued"})
    fusion = ScriptedFusion()
    backend = _cascade(gateway, fusion, tmp_path)
    result = backend.complete(
        _user("read the file"), tools=[{"type": "function", "function": {"name": "read"}}]
    )
    assert result.content == "tool call issued"
    assert gateway.calls == [MID]  # weak never touched
    assert load_routes(tmp_path / "routes.jsonl")[0].accepted_tier == "mid"


def test_policy_flagged_turn_skips_weak(tmp_path: Path) -> None:
    gateway = ScriptedGateway({MID: "7 * 11 = 77"})
    fusion = ScriptedFusion()
    backend = _cascade(gateway, fusion, tmp_path)
    result = backend.complete(_user("compute 7 * 11"))  # precision keyword + arithmetic
    assert result.content == "7 * 11 = 77"
    assert gateway.calls == [MID]
    record = load_routes(tmp_path / "routes.jsonl")[0]
    assert record.fuse_reason in ("precision", "arithmetic")


def test_mode_always_goes_straight_to_fusion(tmp_path: Path) -> None:
    gateway = ScriptedGateway({})
    fusion = ScriptedFusion()
    backend = _cascade(gateway, fusion, tmp_path, mode="always")
    backend.complete(_user("anything"))
    assert gateway.calls == []
    assert fusion.calls == 1


def test_auto_entry_at_mid_skips_weak(tmp_path: Path) -> None:
    gateway = ScriptedGateway({MID: "mid answer"})
    fusion = ScriptedFusion()
    backend = _cascade(gateway, fusion, tmp_path, entry="mid")
    backend.complete(_user("hello there"))
    assert gateway.calls == [MID]  # cost_mode auto: prioritize the mid tier


def test_weak_agreement_k_samples_and_disagreement_climbs(tmp_path: Path) -> None:
    class FlipFlopGateway(ScriptedGateway):
        def __init__(self) -> None:
            super().__init__({})
            self.n = 0

        def complete(
            self, messages: list[MessageLike], *, model: str | None = None, **kwargs: Any
        ) -> CompletionResult:
            self.calls.append(model or "?")
            if model == WEAK:
                self.n += 1
                # Two samples with no shared tokens at all -> zero agreement.
                text = "alpha bravo charlie delta" if self.n == 1 else "zulu yankee xray whiskey"
                return CompletionResult(
                    content=text, model=WEAK, prompt_tokens=10, completion_tokens=10,
                )
            return CompletionResult(content="mid consensus", model=MID,
                                    prompt_tokens=10, completion_tokens=10)

    gateway = FlipFlopGateway()
    fusion = ScriptedFusion()
    backend = _cascade(gateway, fusion, tmp_path, agreement_k=2)
    result = backend.complete(_user("tricky one"))
    assert result.content == "mid consensus"
    assert gateway.calls.count(WEAK) == 2  # two samples
    record = load_routes(tmp_path / "routes.jsonl")[0]
    assert record.agreement == 0.0
    assert record.accepted_tier == "mid"


def test_route_record_stores_hash_not_prompt(tmp_path: Path) -> None:
    gateway = ScriptedGateway({WEAK: "fine"})
    backend = _cascade(gateway, ScriptedFusion(), tmp_path)
    secret = "my api key is sk-super-secret"
    backend.complete(_user(secret))
    line = (tmp_path / "routes.jsonl").read_text(encoding="utf-8")
    assert "sk-super-secret" not in line
    record = load_routes(tmp_path / "routes.jsonl")[0]
    assert record.prompt_chars == len(secret)
    assert len(record.prompt_sha) == 64


def test_default_gate() -> None:
    assert default_gate(CompletionResult(content="a real answer", model="m")) is True
    assert default_gate(CompletionResult(content="", model="m")) is False
    assert default_gate(CompletionResult(content="I'm sorry, I cannot do that", model="m")) is False


def test_cascade_bench_four_arms_offline(tmp_path: Path) -> None:
    """The 4-arm bench harness runs fully scripted: pass rates + honest hop-sum tokens."""
    from chimera.eval.cascade_bench import ARMS, run_cascade_bench
    from chimera.eval.continuous import EvalTask

    gateway = ScriptedGateway({WEAK: "I'm sorry, I cannot.", MID: "It is Paris."})
    fusion = ScriptedFusion("It is Paris.")
    # Non-arithmetic prompt so the policy does NOT skip the weak tier.
    tasks = [EvalTask("capital", "Name the capital of France.", lambda o: "paris" in o.lower())]
    report = run_cascade_bench(
        gateway, fusion, tasks, weak=WEAK, mid=MID, log_dir=tmp_path
    )
    row = report.rows[0]
    assert row.ok == {"weak": False, "mid": True, "cascade": True, "fusion": True}
    # Cascade cost = SUM over hops — including BOTH weak samples (agreement_k=2
    # by default), not just the accepted answer: 2x150 weak + 150 mid.
    assert row.tokens["cascade"] == 450
    assert row.tokens["mid"] == 150
    summary = report.summary()
    assert summary["n"] == 1
    for arm in ARMS:
        assert f"{arm}_pass_rate" in summary
    assert summary["cascade_pass_rate"] == 1.0
    assert summary["weak_pass_rate"] == 0.0


def test_summarize_and_format_routes(tmp_path: Path) -> None:
    path = tmp_path / "routes.jsonl"
    append_route(path, RouteRecord(accepted_tier="weak", tiers_tried=["weak"],
                                   tokens_by_tier={"weak": 100}))
    append_route(path, RouteRecord(accepted_tier="fusion",
                                   tiers_tried=["weak", "mid", "fusion"],
                                   tokens_by_tier={"weak": 100, "mid": 200, "fusion": 900}))
    summary = summarize_routes(load_routes(path))
    assert summary["n"] == 2
    assert summary["accepted_by_tier"] == {"weak": 1, "fusion": 1}
    assert summary["escalation_rate"] == 0.5
    assert summary["total_tokens"] == 1300
    text = format_route_summary(summary)
    assert "escalated past first tier: 50%" in text
    assert format_route_summary({"n": 0}) == "no routed turns yet"
