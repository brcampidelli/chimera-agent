from __future__ import annotations

from typing import Any

from chimera.core import Agent, AgentConfig
from chimera.core import agent as agent_module
from chimera.core.failure_class import ToolErrorClass, classify_tool_error
from chimera.providers import CompletionResult, ToolCall
from chimera.tools import ToolRegistry
from chimera.tools.base import Tool, refusal


class _Answer:
    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        self.asked = False

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        if self.asked:
            return CompletionResult(content="done", model="fake")
        self.asked = True
        return CompletionResult(
            content="", model="fake",
            tool_calls=[ToolCall(id="c1", name=self.tool_name, arguments={})],
        )


class _SequenceTool(Tool):
    name = "probe"
    description = "test"
    parameters = {"type": "object", "properties": {}}

    def __init__(self, outputs: list[Any]) -> None:
        self.outputs = outputs
        self.calls = 0

    def run(self, **kwargs: Any) -> str:
        result = self.outputs[min(self.calls, len(self.outputs) - 1)]
        self.calls += 1
        if isinstance(result, BaseException):
            raise result
        return str(result)


def _run(tool: _SequenceTool, *, notices: list[tuple[str, str]] | None = None) -> None:
    registry = ToolRegistry()
    registry.register(tool)
    Agent(_Answer(tool.name), registry, AgentConfig(max_steps=3)).run(
        "go", on_notice=(lambda code, text, _data: notices.append((code, text)))
        if notices is not None else None,
    )


def test_tool_error_families_are_distinguished_with_evidence() -> None:
    cases = {
        "error: connection reset by peer": ToolErrorClass.NETWORK,
        "error: HTTP 503 Service Unavailable": ToolErrorClass.GATEWAY,
        "error: wrote 5030 bytes": ToolErrorClass.UNKNOWN,
        "error: operation timed out": ToolErrorClass.TIMEOUT,
        "error: rate limit exceeded (429)": ToolErrorClass.LIMIT,
        "error: PermissionError: access denied": ToolErrorClass.PERMISSION,
        "error: probe: unknown argument 'offset'; accepted: path": ToolErrorClass.ARGUMENT,
        "error: file not found: missing.txt": ToolErrorClass.MISSING_RESOURCE,
    }
    for error, expected in cases.items():
        result = classify_tool_error(error)
        assert result.cls is expected
        assert result.evidence == error

def test_refusal_output_is_classified_as_a_permission_failure() -> None:
    output = refusal("the requested action is not permitted")

    result = classify_tool_error(output)

    assert not output.startswith("error:")
    assert result.cls is ToolErrorClass.PERMISSION
    assert result.evidence == output


def test_refusal_tool_output_is_treated_as_a_failure() -> None:
    tool = _SequenceTool([refusal("the requested action is not permitted")])
    tool.name = "write_file"
    notices: list[tuple[str, str]] = []

    _run(tool, notices=notices)

    assert tool.calls == 1
    assert [code for code, _ in notices] == ["tool_permission"]


def test_only_allowlisted_read_tools_retry_transient_families(monkeypatch: Any) -> None:
    waits: list[float] = []
    monkeypatch.setattr(agent_module, "_retry_sleep", waits.append)
    read = _SequenceTool(["error: connection reset by peer", "read succeeded"])
    read.name = "read_file"
    read.parameters = {"type": "object", "properties": {}}
    notices: list[tuple[str, str]] = []
    _run(read, notices=notices)
    assert read.calls == 2
    assert waits == [1.0]
    assert [code for code, _ in notices] == ["tool_retry"]


def test_permission_failures_are_not_retried_and_raise_a_notice() -> None:
    write = _SequenceTool([PermissionError("permission denied"), "must not run"])
    write.name = "write_file"
    notices: list[tuple[str, str]] = []
    _run(write, notices=notices)
    assert write.calls == 1
    assert [code for code, _ in notices] == ["tool_permission"]


def test_non_idempotent_tools_do_not_retry_network_failures(monkeypatch: Any) -> None:
    waits: list[float] = []
    monkeypatch.setattr(agent_module, "_retry_sleep", waits.append)
    write = _SequenceTool(["error: connection reset by peer", "must not run"])
    write.name = "write_file"
    notices: list[tuple[str, str]] = []
    _run(write, notices=notices)
    assert write.calls == 1
    assert waits == []
    assert notices == []


def test_generation_tools_are_retryable_but_other_tools_are_not(monkeypatch: Any) -> None:
    waits: list[float] = []
    monkeypatch.setattr(agent_module, "_retry_sleep", waits.append)
    generation = _SequenceTool(["error: gateway error 503", "generated"])
    generation.name = "generate_image"
    notices: list[tuple[str, str]] = []
    _run(generation, notices=notices)
    assert generation.calls == 2
    assert waits == [1.0]
    assert [code for code, _ in notices] == ["tool_retry"]


def test_successful_tool_output_with_permission_words_has_no_notice() -> None:
    tool = _SequenceTool(["file contents: access denied is an example phrase"])
    notices: list[tuple[str, str]] = []
    _run(tool, notices=notices)
    assert tool.calls == 1
    assert notices == []


def test_missing_tool_observation_keeps_the_existing_message() -> None:
    registry = ToolRegistry()
    registry.register(_SequenceTool([]))
    agent = Agent(_Answer("missing"), registry, AgentConfig(max_steps=2))
    output = agent._run_tool("missing", {})
    assert output == "error: unknown tool 'missing'"


def test_unknown_failure_family_is_not_guessed() -> None:
    result = classify_tool_error("error: upstream returned an unusual response")
    assert result.cls is ToolErrorClass.UNKNOWN
    assert result.evidence == "error: upstream returned an unusual response"



#: Refusals this package's own validators write: a call that never ran, by a stable prefix.
_LOCAL_ERRORS = (
    "error: the operations are not valid: 0.page.add.page: Field required",
    "error: the operations are not valid: 1.node.add.node.type: Input should be 'text', 'shape', 'image' or 'group'",
    "error: the tokens are not a valid design system: colors.x: value is not a valid color",
)
#: The registry's own refusal of an undeclared argument (chimera.tools.base.unknown_arguments_error).
_REGISTRY_ERRORS = (
    "error: probe: unknown argument 'offset'; accepted: path",
    "error: remote: unknown arguments 'y', 'z'; accepted: x",
)
#: A validator the tool does not own. Its words say a field is wrong; they do not prove a refusal.
_REMOTE_SCHEMA_ERRORS = (
    "error: remote: 1 validation error for Args\nextra_field\n  Extra inputs are not permitted",
    "error: remote: Field required",
)


def test_local_validation_and_registry_refusals_are_arguments() -> None:
    for error in (*_LOCAL_ERRORS, *_REGISTRY_ERRORS):
        result = classify_tool_error(error)
        assert result.cls is ToolErrorClass.ARGUMENT, error
        assert result.evidence == error


def test_a_remote_validator_message_is_never_a_permission_failure() -> None:
    # "not permitted" names a field here, not a forbidden action. It is UNKNOWN: not ARGUMENT, since
    # no refusal of ours produced it, and not PERMISSION, which would stop a retry the status earns.
    for error in _REMOTE_SCHEMA_ERRORS:
        result = classify_tool_error(error)
        assert result.cls is ToolErrorClass.UNKNOWN, error
        assert result.cls is not ToolErrorClass.PERMISSION


def test_an_unknown_argument_in_a_remote_tools_prose_is_not_a_registry_refusal() -> None:
    # The registry's refusal is anchored: the tool's name, then the words, then "accepted:". Prose
    # that merely contains "unknown argument" is a remote tool's own text and is not classified so.
    assert classify_tool_error(
        "error: fetch: the remote page said unknown argument support; accepted: none"
    ).cls is ToolErrorClass.UNKNOWN


def test_a_refusal_with_schema_words_is_still_a_permission_failure() -> None:
    # The owner's gate writes a ⛔ refusal. Its prose may contain schema words; the refusal stays a
    # refusal, because the ⛔ prefix is checked before any wording.
    output = refusal("the owner forbade this write; Input should be a valid path; validation error")

    assert classify_tool_error(output).cls is ToolErrorClass.PERMISSION


def test_a_gateway_502_with_validation_words_stays_a_gateway_failure() -> None:
    error = "error: HTTP 502 Bad Gateway: 1 validation error for Args; Input should be a valid string"

    result = classify_tool_error(error)

    assert result.cls is ToolErrorClass.GATEWAY
    assert result.evidence == error


def test_a_rate_limit_with_validation_words_stays_a_limit() -> None:
    result = classify_tool_error("error: 429 Too Many Requests: Field required in the quota body")

    assert result.cls is ToolErrorClass.LIMIT


def test_a_502_with_validation_words_is_still_retried_for_an_allowlisted_read_tool(
    monkeypatch: Any,
) -> None:
    # The argument rule must not take a gateway failure's retry away from a read tool.
    monkeypatch.setattr(agent_module, "_retry_sleep", lambda _seconds: None)
    tool = _SequenceTool([
        "error: HTTP 502 Bad Gateway: 1 validation error for Args; Input should be a valid string",
        "page body",
    ])
    tool.name = "http_get"
    notices: list[tuple[str, str]] = []

    _run(tool, notices=notices)

    assert tool.calls == 2
    codes = [code for code, _ in notices]
    assert "tool_retry" in codes
    assert "tool_argument" not in codes


class _Recorder:
    """A model that calls one tool, then records what the tool observation told it."""

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        self.asked = False
        self.observations: list[str] = []

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        if self.asked:
            self.observations = [m["content"] for m in messages if m.get("role") == "tool"]
            return CompletionResult(content="done", model="fake")
        self.asked = True
        return CompletionResult(
            content="", model="fake",
            tool_calls=[ToolCall(id="c1", name=self.tool_name, arguments={})],
        )


def test_a_local_argument_failure_is_hinted_to_review_the_arguments_without_retrying() -> None:
    error = _LOCAL_ERRORS[0]
    tool = _SequenceTool([error])
    tool.name = "design"
    registry = ToolRegistry()
    registry.register(tool)
    model = _Recorder("design")
    notices: list[tuple[str, str]] = []

    Agent(model, registry, AgentConfig(max_steps=3)).run(
        "go", on_notice=lambda code, text, _data: notices.append((code, text)),
    )

    assert tool.calls == 1
    assert [code for code, _ in notices] == ["tool_argument"]
    assert len(model.observations) == 1
    assert model.observations[0].startswith(error)
    # A heuristic does not get to claim the call did not run; the hint asks for a review instead.
    assert "did not run" not in model.observations[0]
    assert "Review them against the tool's schema" in model.observations[0]
