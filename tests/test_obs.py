"""Optional OpenTelemetry observability — no-op by default, records when a tracer is present."""

from __future__ import annotations

from typing import Any

import chimera.obs as obs


def test_span_is_noop_when_tracing_off() -> None:
    # Default state: no tracer configured. span() must not import/require opentelemetry and must not
    # crash; its handle's .set() is inert.
    with obs.span("tool.run", **{"tool.name": "echo"}) as sp:
        sp.set(**{"tool.ok": True})  # no tracer → does nothing, no error


def test_record_llm_metrics_is_noop_when_off() -> None:
    obs.record_llm_metrics(model="m", prompt_tokens=10, completion_tokens=5, usd=0.001)  # no crash


class _FakeSpan:
    def __init__(self) -> None:
        self.attrs: dict[str, Any] = {}

    def set_attribute(self, key: str, value: Any) -> None:
        self.attrs[key] = value


class _FakeTracer:
    def __init__(self) -> None:
        self.spans: list[_FakeSpan] = []

    def start_as_current_span(self, name: str) -> Any:
        span = _FakeSpan()
        self.spans.append(span)

        class _Ctx:
            def __enter__(self_inner) -> _FakeSpan:
                return span

            def __exit__(self_inner, *exc: object) -> bool:
                return False

        return _Ctx()


def test_span_records_attributes_with_a_tracer(monkeypatch: Any) -> None:
    tracer = _FakeTracer()
    monkeypatch.setattr(obs, "_tracer", tracer)
    with obs.span("tool.run", **{"tool.name": "grep"}) as sp:
        sp.set(**{"tool.ok": True, "tool.output_chars": 42, "skip.none": None})
    assert len(tracer.spans) == 1
    attrs = tracer.spans[0].attrs
    assert attrs["tool.name"] == "grep"
    assert attrs["tool.ok"] is True
    assert attrs["tool.output_chars"] == 42
    assert "skip.none" not in attrs  # None-valued attributes are dropped


class _FakeCounter:
    def __init__(self) -> None:
        self.total = 0.0
        self.tags: dict[str, Any] = {}

    def add(self, amount: float, tags: dict[str, Any]) -> None:
        self.total += amount
        self.tags = tags


def test_record_llm_metrics_adds_to_counters(monkeypatch: Any) -> None:
    prompt, completion, cost = _FakeCounter(), _FakeCounter(), _FakeCounter()
    monkeypatch.setattr(obs, "_prompt_ctr", prompt)
    monkeypatch.setattr(obs, "_completion_ctr", completion)
    monkeypatch.setattr(obs, "_cost_ctr", cost)
    obs.record_llm_metrics(model="gpt", prompt_tokens=100, completion_tokens=40, usd=0.02)
    assert prompt.total == 100
    assert completion.total == 40
    assert cost.total == 0.02
    assert prompt.tags == {"llm.model": "gpt"}


def test_record_llm_metrics_skips_cost_when_unknown(monkeypatch: Any) -> None:
    prompt, completion, cost = _FakeCounter(), _FakeCounter(), _FakeCounter()
    monkeypatch.setattr(obs, "_prompt_ctr", prompt)
    monkeypatch.setattr(obs, "_completion_ctr", completion)
    monkeypatch.setattr(obs, "_cost_ctr", cost)
    obs.record_llm_metrics(model="m", prompt_tokens=1, completion_tokens=1, usd=None)
    assert cost.total == 0.0  # unknown price → no fabricated cost datapoint


class _Settings:
    otel = False


def test_configure_otel_disabled_returns_false(monkeypatch: Any) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setattr(obs, "_configured", False)
    monkeypatch.setattr(obs, "_tracer", None)
    assert obs.configure_otel(_Settings()) is False


def test_tool_registry_run_is_transparent() -> None:
    # The tool.run span must not change what the registry returns.
    from chimera.tools.builtin import EchoTool
    from chimera.tools.registry import ToolRegistry

    reg = ToolRegistry()
    reg.register(EchoTool())
    assert reg.run("echo", text="hello") == "hello"
