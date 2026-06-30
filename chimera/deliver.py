"""Deliverable Mode — produce a polished, self-contained artifact, not a chat reply.

Where ``run``/``chat`` answer conversationally, ``deliver`` asks the model for a
complete, well-structured document (a report, plan, spec, README, ...) and the CLI
writes it to a file. Optionally routed through fusion for higher quality.
"""

from __future__ import annotations

from chimera.providers.gateway import Message, MessageLike, SupportsComplete

_FORMATS = {
    "md": "Markdown",
    "txt": "plain text",
    "html": "a complete, standalone HTML document",
}


def deliverable_system_prompt(fmt: str) -> str:
    label = _FORMATS.get(fmt, "Markdown")
    return (
        f"You are producing a polished, self-contained deliverable in {label}. "
        "Give it a clear title and well-structured sections, complete and ready to "
        "hand off as-is. Output ONLY the document itself — no preamble, no closing "
        "commentary, and do not wrap the whole thing in a code fence."
    )


def produce_deliverable(
    backend: SupportsComplete,
    request: str,
    *,
    fmt: str = "md",
    model: str | None = None,
) -> str:
    """Generate a complete deliverable document for ``request`` in ``fmt``."""
    messages: list[MessageLike] = [
        Message(role="system", content=deliverable_system_prompt(fmt)),
        Message(role="user", content=request),
    ]
    return backend.complete(messages, model=model, temperature=0.4).content
