"""Explicit prompt-cache breakpoints (M17): make Chimera actually USE caching.

The gateway captures provider cache-token reports; this module adds explicit breakpoints for
Anthropic/Claude. Automatic-cache providers receive messages unchanged. The original
:func:`apply_cache_control` marks only the last system message; the gateway uses
:func:`apply_rolling_cache_control` to additionally mark the history tail, within Anthropic's
four-block limit. The Code-turn system prompt is not stable across turns because it carries recalled
facts and notes, so a system-only marker has little value there; the rolling marker covers steps
within the turn.

Honest status (probe measured 2026-10-24): with prompt-cache markers enabled, the second
identical call read about 26.5k tokens from cache on ``openrouter/anthropic/claude-haiku-5.5``
through the default, Anthropic, and Amazon Bedrock routes, including both ``complete`` and
``stream_complete``. The marker works; the earlier zero was because cache control was not applied
on the path used by the probe. ``acomplete`` now applies the same marker as well.
"""

from __future__ import annotations

from typing import Any

_NEEDS_EXPLICIT = ("anthropic", "claude")


def needs_explicit_cache_control(model: str) -> bool:
    """True for families that require a request-side cache breakpoint (Anthropic)."""
    low = model.lower()
    return any(key in low for key in _NEEDS_EXPLICIT)


def _has_content(message: dict[str, Any]) -> bool:
    content = message.get("content")
    return bool(content) if isinstance(content, str) else isinstance(content, list) and bool(content)


def _has_marker(message: dict[str, Any]) -> bool:
    content = message.get("content")
    return isinstance(content, list) and any(
        isinstance(block, dict) and "cache_control" in block for block in content
    )


def _mark(message: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``message`` with an ephemeral cache breakpoint on its content."""
    content = message.get("content")
    if isinstance(content, str):
        blocks: list[Any] = [
            {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
        ]
    elif isinstance(content, list) and content:
        blocks = [dict(block) if isinstance(block, dict) else block for block in content]
        if isinstance(blocks[-1], dict):
            blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
        else:  # pragma: no cover - malformed provider block
            return message
    else:
        return message
    return {**message, "content": blocks}


def apply_cache_control(messages: list[dict[str, Any]], model: str) -> list[dict[str, Any]]:
    """Mark only the last system message for explicit-marker models (legacy behavior)."""
    if not needs_explicit_cache_control(model):
        return messages
    last_sys = max(
        (i for i, message in enumerate(messages) if message.get("role") == "system"),
        default=None,
    )
    if last_sys is None:
        return messages
    return [_mark(message) if i == last_sys else message for i, message in enumerate(messages)]


def apply_rolling_cache_control(
    messages: list[dict[str, Any]], model: str
) -> list[dict[str, Any]]:
    """Mark the last system message and latest non-empty history message.

    Existing cache markers count toward Anthropic's maximum of four blocks. Content that is
    absent, ``None``, or empty is skipped; this includes assistant tool-call messages without text.
    """
    if not needs_explicit_cache_control(model):
        return messages

    system_index = max(
        (
            i
            for i, message in enumerate(messages)
            if message.get("role") == "system" and _has_content(message)
        ),
        default=None,
    )
    tail_index = next(
        (
            i
            for i in range(len(messages) - 1, -1, -1)
            if _has_content(messages[i])
        ),
        None,
    )
    out: list[dict[str, Any]] = []
    marker_count = 0
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            out.append(message)
            continue
        blocks: list[Any] = []
        for block in content:
            if isinstance(block, dict) and "cache_control" in block:
                if marker_count >= 4:
                    block = {key: value for key, value in block.items() if key != "cache_control"}
                else:
                    marker_count += 1
            blocks.append(block)
        out.append({**message, "content": blocks})

    for index in dict.fromkeys(i for i in (system_index, tail_index) if i is not None):
        if _has_marker(out[index]) or marker_count >= 4:
            continue
        marked_message = _mark(out[index])
        if marked_message is out[index]:
            continue
        out[index] = marked_message
        marker_count += 1
    return out


__all__ = [
    "apply_cache_control",
    "apply_rolling_cache_control",
    "needs_explicit_cache_control",
]
