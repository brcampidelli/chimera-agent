"""A local model that reasons in tags dumps the reasoning into the answer.

`message.content` is read raw, so `<think>…</think>` reaches the terminal, the desktop transcript,
and whatever consumes the agent's answer next. Ollama is a declared first-class path here, so this
is a shape the project actually meets.

The tests below are ordered by how easy each case is to get wrong, not by importance — the split tag
and the code fence are the two that a `str.replace` implementation fails silently.
"""

from __future__ import annotations

from chimera.providers.thinking import MAX_HELD_CHARS, ThinkFilter, strip_think


def stream(*chunks: str) -> str:
    f = ThinkFilter()
    return "".join(f.feed(c) for c in chunks) + f.flush()


def test_a_reasoning_block_does_not_reach_the_answer() -> None:
    assert strip_think("<think>let me see</think>The answer is 4.") == "The answer is 4."


def test_text_on_both_sides_survives() -> None:
    assert strip_think("Before.<think>noise</think>After.") == "Before.After."


def test_a_tag_split_across_two_deltas_is_still_a_tag() -> None:
    """The case a per-chunk filter cannot see at all.

    Streaming hands over `<thi` and `nk>` separately, so anything that looks at one delta in
    isolation finds no tag and passes the reasoning straight through.
    """
    assert stream("<thi", "nk>hidden</thi", "nk>shown") == "shown"


def test_a_tag_split_one_character_at_a_time() -> None:
    text = "<think>hidden</think>visible"
    assert stream(*text) == "visible"


def test_nesting_is_counted_not_matched() -> None:
    """A single `</think>` must not end two levels.

    Depth rather than a boolean: with a flag, the inner close re-opens the answer and the outer
    block's remaining reasoning is printed.
    """
    assert strip_think("<think>a<think>b</think>c</think>real") == "real"


def test_a_think_tag_inside_a_code_fence_is_left_alone() -> None:
    """The guard the original design did not ask for.

    A task that says "write a parser for <think>" would otherwise have its own diff corrupted by the
    filter meant to tidy the output — a failure that is silent and lands in the artifact rather than
    on the screen.
    """
    src = "Here:\n```python\nif tag == '<think>':\n    pass\n</think>\n```\ndone"
    assert strip_think(src) == src


def test_a_fence_opened_inside_reasoning_does_not_leak_it() -> None:
    """The mirror case, and the reason the fence toggle is checked after the depth.

    A model that writes a code block INSIDE its reasoning must not have that block escape just
    because a fence appeared.
    """
    out = strip_think("<think>let me try\n```py\nx=1\n```\nno</think>answer")
    assert out == "answer"


def test_an_unclosed_tag_never_swallows_the_answer() -> None:
    """The failure mode of the obvious implementation.

    Drop-until-close turns one unclosed tag — a truncated stream, a false positive on prose — into
    an empty answer with no error anywhere. Showing reasoning is a blemish; returning "" is a bug.
    """
    # Byte-exact, tag included. Once the filter decides a block was not one, it must hand back what
    # it was given rather than a cleaned-up version — a filter that edits text it has just declared
    # ordinary is doing the thing it exists to prevent.
    assert stream("<think>reasoning that never ends") == "<think>reasoning that never ends"


def test_a_runaway_block_is_released_rather_than_held_forever() -> None:
    huge = "x" * (MAX_HELD_CHARS + 10)
    out = stream(f"<think>{huge}")
    assert huge in out, "the ceiling must release what it held, not discard it"


def test_ordinary_text_is_returned_byte_for_byte() -> None:
    """The false-positive half. A filter that mangles normal prose is worse than no filter."""
    for text in (
        "The answer is 4.",
        "Use `<` and `>` carefully.",
        "if a < b and c > d: pass",
        "```\nfor i in range(3): print(i)\n```",
        "A generic List<think> is not a tag in C#... but it is close enough to matter.",
    ):
        # The last one IS taken as a tag outside a fence, which is the honest limit of a lexical
        # filter — asserted below rather than pretended away.
        if "List<think>" in text:
            continue
        assert strip_think(text) == text


def test_the_honest_limit_of_a_lexical_filter() -> None:
    """`List<think>` in prose reads as an opening tag, and the filter has no way to know better.

    Pinned deliberately: the mitigation is the code fence (where it would be written in practice)
    plus the unclosed-tag release, so the text comes back at flush rather than disappearing.
    """
    text = "A generic List<think> in prose"
    assert strip_think(text) == text, "released at flush, because nothing ever closed it"


def test_flush_is_safe_when_nothing_was_filtered() -> None:
    f = ThinkFilter()
    assert f.feed("plain") == "plain"
    assert f.flush() == ""


# --- the wiring, which is a separate claim from the filter working ---------------------------


class _Chunk:
    """One streaming delta, in the shape litellm hands over."""

    def __init__(self, text: str) -> None:
        self.choices = [type("C", (), {"delta": type("D", (), {"content": text})()})()]


def test_the_streaming_path_actually_uses_the_filter(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The filter passing its own tests says nothing about whether the gateway calls it.

    Earlier today a set of tests in this repository passed with the production line deleted, because
    they exercised the helper against their own fixture instead of the path that uses it. This one
    drives `stream_complete` and asserts on what it RETURNS — the accumulated content, not just what
    reached `on_delta`, since the accumulated text is what goes back into the next turn's prompt.
    """
    import litellm

    from chimera.providers.gateway import LLMGateway

    monkeypatch.setattr(
        litellm,
        "completion",
        lambda **_: iter([_Chunk("<think>hid"), _Chunk("den</think>"), _Chunk("answer")]),
    )
    gateway = LLMGateway()
    monkeypatch.setattr(gateway, "_require_credentials", lambda *_: None)

    seen: list[str] = []
    result = gateway.stream_complete(
        [{"role": "user", "content": "q"}], model="openrouter/x/y", on_delta=seen.append
    )

    assert result.content == "answer", "the reasoning stayed in the accumulated content"
    assert "hidden" not in "".join(seen)


def test_keep_think_turns_the_filter_off(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A filter with no way off is worse than the noise it removes."""
    import litellm

    from chimera.config import Settings
    from chimera.providers.gateway import LLMGateway

    monkeypatch.setattr(litellm, "completion", lambda **_: iter([_Chunk("<think>x</think>y")]))
    # A frozen override rather than an env var: it also pins that `_think_filter` reads the
    # gateway's own settings, so a caller who passed one is not overruled by the process-wide state.
    gateway = LLMGateway(Settings(CHIMERA_KEEP_THINK=True))  # type: ignore[arg-type]
    monkeypatch.setattr(gateway, "_require_credentials", lambda *_: None)
    result = gateway.stream_complete([{"role": "user", "content": "q"}], model="openrouter/x/y")
    assert result.content == "<think>x</think>y"


def test_the_raw_stream_generator_uses_it_too(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`stream()` is a different path from `stream_complete()`, and it feeds different screens.

    The live terminal, the messaging gateway and the A2A `message/stream` endpoint all build on this
    one. Wiring three paths and testing one is how a guard ends up covering four callers of five —
    which is the bug this project has spent the day removing from other modules.
    """
    import litellm

    from chimera.providers.gateway import LLMGateway

    monkeypatch.setattr(
        litellm, "completion", lambda **_: iter([_Chunk("<think>hid"), _Chunk("den</think>ok")])
    )
    gateway = LLMGateway()
    monkeypatch.setattr(gateway, "_require_credentials", lambda *_: None)

    out = "".join(gateway.stream([{"role": "user", "content": "q"}], model="openrouter/x/y"))
    assert out == "ok"


def test_the_non_streaming_path_uses_it_too() -> None:
    """A local model reasons in tags whether or not anyone asked for streaming.

    `_normalize` is where every non-streamed response becomes a `CompletionResult`, so a filter that
    only runs on deltas leaves the whole `chimera solve` path untouched.
    """
    from chimera.providers.gateway import LLMGateway

    message = type("M", (), {"content": "<think>reasoning</think>the answer", "tool_calls": None})()
    response = type("R", (), {"choices": [type("C", (), {"message": message})()], "usage": None})()

    assert LLMGateway._normalize(response, "openrouter/x/y").content == "the answer"
