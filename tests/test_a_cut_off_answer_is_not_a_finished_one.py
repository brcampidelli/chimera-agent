"""A response the provider truncated was indistinguishable from one the model finished.

`finish_reason` appears six times in this package and every one of them is in `openai_compat.py`,
where Chimera EMITS the field for its own clients. Nothing in `chimera/providers/` ever read it. So a
call that hit the provider's output ceiling came back 200 OK, and the loop treated the fragment as
the model's answer.

The damage is a chain, and every link blames the model for something the provider did:

1. the truncated argument string fails `json.loads`, and the call was appended anyway with
   `arguments={}` — the `append` sits outside the `except`, on both the batch and the stream path;
2. the tool then runs with no arguments, fails, and the loop breaker counts that as the model
   repeating itself;
3. and if the cut removed the call entirely, `tool_calls_made == 0` fires the action nudge, which
   accuses the model of describing a plan instead of doing it — when it was cut mid-sentence.

Reading one field turns a silent, mis-attributed failure into a named one. This is the family the
project's own notes call the expensive kind: nothing errors, the number comes out, and the
apparatus cannot show the defect.
"""

from __future__ import annotations

from typing import Any

from chimera.providers.gateway import LLMGateway, _finalize_stream_tool_calls


class _Fn:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _Call:
    def __init__(self, name: str, arguments: str, id: str = "c1") -> None:
        self.id = id
        self.function = _Fn(name, arguments)


class _Msg:
    def __init__(self, content: str = "", tool_calls: list[_Call] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, message: _Msg, finish_reason: str | None = "stop") -> None:
        self.message = message
        self.finish_reason = finish_reason


class _Resp:
    def __init__(self, choice: _Choice, usage: Any = None) -> None:
        self.choices = [choice]
        self.usage = usage


def _normalizar(finish_reason: str | None = "stop", **kwargs: Any) -> Any:
    return LLMGateway._normalize(_Resp(_Choice(_Msg(**kwargs), finish_reason)), "m")  # noqa: SLF001


# ------------------------------------------------------------------ the field nobody read


def test_a_truncated_answer_says_it_was_truncated() -> None:
    """The whole point. `length` is the provider saying "I stopped because I ran out of room"."""
    assert _normalizar("length", content="A resposta começa e").truncated is True


def test_a_finished_answer_does_not() -> None:
    assert _normalizar("stop", content="pronto").truncated is False


def test_a_tool_call_stop_is_not_truncation() -> None:
    """`tool_calls` is the ordinary end of a step that asked for a tool, and reading it as truncation
    would flag most of a healthy run."""
    assert _normalizar("tool_calls", content="").truncated is False


def test_a_provider_that_reports_nothing_is_not_called_truncated() -> None:
    """Three states, not two. A provider that omits the field has told us nothing, and inventing
    `False` there is the same mistake as inventing `True` — this is the rule `cache_read_tokens`
    already follows one struct over."""
    assert _normalizar(None, content="pronto").truncated is False
    assert _normalizar(None, content="pronto").finish_reason == ""


def test_the_raw_reason_survives() -> None:
    """A boolean answers "was it cut"; the string answers "by what", which is what a provider's own
    vocabulary carries — `content_filter` and `length` are both not-`stop` and need opposite
    responses."""
    assert _normalizar("content_filter", content="").finish_reason == "content_filter"


# ------------------------------------------------------------------ the argument it half-wrote


def test_a_half_written_argument_is_not_served_as_an_empty_one() -> None:
    """Link 2 of the chain, batch path. `{"path": "src/ap` is a cut string, not a call with no
    arguments — and running a tool with `{}` produces an error the model gets blamed for."""
    resultado = _normalizar("length", tool_calls=[_Call("write_file", '{"path": "src/ap')])

    assert resultado.tool_calls is None


def test_a_whole_argument_is_still_parsed() -> None:
    """The guard against fixing this by refusing every call."""
    resultado = _normalizar("tool_calls", tool_calls=[_Call("read_file", '{"path": "x.py"}')])

    assert resultado.tool_calls is not None
    assert resultado.tool_calls[0].arguments == {"path": "x.py"}


def test_a_call_with_genuinely_no_arguments_still_works() -> None:
    """`{}` from the provider is a real call to a no-argument tool, and dropping it would break
    every such tool — which is the over-correction this test exists to catch."""
    resultado = _normalizar("tool_calls", tool_calls=[_Call("get_time", "{}")])

    assert resultado.tool_calls is not None
    assert resultado.tool_calls[0].arguments == {}


def test_the_stream_path_refuses_the_same_fragment() -> None:
    """Link 2, stream path. The same code twice, in two places, is how one of them stops matching —
    and the streaming path is the one the desktop app actually uses."""
    assert _finalize_stream_tool_calls({0: {"name": "write_file", "arguments": '{"path": "src/ap'}}) is None


def test_the_stream_path_still_parses_a_whole_one() -> None:
    acabado = _finalize_stream_tool_calls({0: {"id": "c1", "name": "read_file", "arguments": '{"path": "x"}'}})

    assert acabado is not None
    assert acabado[0].arguments == {"path": "x"}


def test_one_broken_call_does_not_discard_the_good_ones() -> None:
    """A step can declare several calls and only the last one is cut. Throwing away the whole step
    would turn one truncated fragment into a wasted round-trip that was mostly usable."""
    resultado = _normalizar(
        "length",
        tool_calls=[_Call("read_file", '{"path": "a.py"}', "c1"), _Call("write_file", '{"pa', "c2")],
    )

    assert resultado.tool_calls is not None
    assert [c.name for c in resultado.tool_calls] == ["read_file"]
