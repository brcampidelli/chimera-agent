"""OpenAI-compatible ``/v1/chat/completions`` — point any LLM client or benchmark at the agent.

The endpoint speaks the OpenAI chat-completions wire format, but the thing behind it is the **whole
Chimera loop** (tools, steps, retries), not a single model call. That makes every harness built for
LLMs — LiveBench, lm-eval-harness, anything using the ``openai`` SDK — usable to measure the *agent*::

    from openai import OpenAI
    client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="<CHIMERA_SERVER_TOKEN or 'none'>")
    client.chat.completions.create(model="chimera/openrouter/...", messages=[...])

Two honesty notes, both load-bearing:

* ``usage`` reports the tokens the **entire turn** spent — every step of the loop, not one call. A
  Chimera turn legitimately costs several times a single completion; reporting only the last call
  would understate the price of the answer. Any cost comparison against a raw model must use this
  number, which is exactly why it is summed here rather than taken from the final step.
* Requests are **stateless**, like the real API: each call gets a fresh ephemeral session seeded with
  the ``messages`` the client sent. Nothing is persisted and no server-side history leaks between
  calls, so a benchmark's items stay independent (a shared transcript would silently make later items
  easier and inflate the score).

Model selection follows the ``model`` field: ``chimera/<slug>`` runs the loop over ``<slug>``; a bare
``chimera`` / ``chimera-agent`` uses the configured default; anything else is treated as a slug.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from chimera.telemetry import get_logger

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI

    from chimera.api.sessions import SessionManager
    from chimera.interface.session import ChatSession, TurnReport

_log = get_logger("api.openai")

# Model names that mean "use whatever model is configured" rather than naming a slug.
_DEFAULT_ALIASES = {"chimera", "chimera-agent", "default", ""}
_PREFIX = "chimera/"


class ChatMessage(BaseModel):
    """One OpenAI chat message. ``content`` may be a string or the multimodal part list."""

    model_config = ConfigDict(extra="allow")

    role: str = "user"
    content: Any = ""


class ChatCompletionRequest(BaseModel):
    """The subset of the OpenAI request we act on.

    ``extra="allow"`` is deliberate: real clients send ``temperature``, ``top_p``, ``seed``,
    ``max_tokens``, ``stream_options`` and more. Rejecting unknown fields with a 422 would break every
    harness for no benefit, so unsupported knobs are accepted and ignored rather than faked — the loop
    decides its own sampling per step, and pretending to honour ``temperature`` would be a lie.
    """

    model_config = ConfigDict(extra="allow")

    model: str = "chimera"
    messages: list[ChatMessage] = Field(default_factory=list)
    stream: bool = False


def resolve_model(name: str) -> str | None:
    """Map the request's ``model`` to a Chimera model slug, or None for "use the configured default"."""
    slug = (name or "").strip()
    if slug.lower() in _DEFAULT_ALIASES:
        return None
    if slug.startswith(_PREFIX):
        inner = slug[len(_PREFIX) :].strip()
        return inner or None
    return slug


def flatten_content(content: Any) -> str:
    """Render OpenAI message content (string, or a list of parts) as plain text.

    Non-text parts (images) are dropped rather than described: this endpoint is text-in/text-out, and
    inventing a placeholder like "[image]" would let a caller believe an image was considered.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(p.get("text", ""))
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return "" if content is None else str(content)


def split_messages(messages: list[ChatMessage]) -> tuple[str, list[tuple[str, str]], str]:
    """Split an OpenAI ``messages`` array into ``(system_preamble, history_pairs, final_user)``.

    History pairs are (user, assistant) exchanges that precede the final user message; they are
    replayed into the ephemeral session so multi-turn benchmarks behave, without any cross-request
    state. A trailing assistant message (a prefill request) is not something the loop can continue,
    so it is treated as history and the turn runs with an empty final user message.
    """
    system_parts: list[str] = []
    exchanges: list[tuple[str, str]] = []
    pending_user: str | None = None
    final_user = ""

    for msg in messages:
        text = flatten_content(msg.content)
        role = (msg.role or "user").lower()
        if role == "system":
            if text:
                system_parts.append(text)
        elif role == "user":
            if pending_user is not None:
                # Two users in a row: the earlier one got no reply — keep it as an unanswered turn.
                exchanges.append((pending_user, ""))
            pending_user = text
        elif role == "assistant":
            exchanges.append((pending_user or "", text))
            pending_user = None

    if pending_user is not None:
        final_user = pending_user
    return "\n\n".join(system_parts), exchanges, final_user


def _finish_reason(report: TurnReport) -> str:
    """Map the loop's stop reason onto OpenAI's vocabulary, honestly.

    Only a genuine budget/step exhaustion is reported as ``length``; everything else is ``stop``. We
    never report ``content_filter`` (we do not run one), and a refusal from the model is a normal stop.
    """
    reason = (report.stopped_reason or "").lower()
    if any(k in reason for k in ("budget", "max_steps", "max steps", "token_limit", "truncat")):
        return "length"
    return "stop"


def _usage(report: TurnReport) -> dict[str, int]:
    """Whole-turn token usage. Cache tokens are surfaced in the OpenAI-standard nested field."""
    prompt = int(report.prompt_tokens)
    completion = int(report.completion_tokens)
    usage: dict[str, Any] = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }
    if report.cache_read_tokens:
        usage["prompt_tokens_details"] = {"cached_tokens": int(report.cache_read_tokens)}
    return usage  # type: ignore[return-value]


def build_completion(report: TurnReport, *, model: str, completion_id: str, created: int) -> dict[str, Any]:
    """The non-streaming ``chat.completion`` object."""
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        # Report the model that ACTUALLY answered when the loop knows it, so a caller comparing runs
        # is not misled by an alias they sent (e.g. "chimera").
        "model": report.model or model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": report.answer},
                "finish_reason": _finish_reason(report),
            }
        ],
        "usage": _usage(report),
        # Non-standard, clearly namespaced: what the loop did to earn this answer. Ignored by every
        # OpenAI client, and the only place a harness can see that this was an agent, not one call.
        "chimera": {
            "steps": report.steps,
            "tools": list(report.tool_names),
            "usd": report.usd,  # None when the model's price is unknown — never a guessed number
            "stopped_reason": report.stopped_reason,
        },
    }


def _chunk(completion_id: str, created: int, model: str, delta: dict[str, Any], finish: str | None) -> str:
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(payload)}\n\n"


def register_openai_compat(
    app: FastAPI,
    guard: Any,
    manager: SessionManager,
    *,
    run_turn: Any = None,
) -> None:
    """Mount ``/v1/chat/completions`` and ``/v1/models`` on ``app``.

    ``run_turn(session, message) -> TurnReport`` is injectable so tests can drive the endpoint without
    a provider key; it defaults to ``ChatSession.send_verbose``.
    """
    from fastapi import HTTPException
    from fastapi.responses import StreamingResponse

    def _default_run_turn(session: ChatSession, message: str) -> TurnReport:
        return session.send_verbose(message)

    turn_fn = run_turn or _default_run_turn

    def _prepare(req: ChatCompletionRequest) -> tuple[ChatSession, str]:
        """Build the ephemeral session for this request and return it with the final user message."""
        system, exchanges, final_user = split_messages(req.messages)
        session = manager.ephemeral()
        if system:
            # The session's persona preamble is exactly "text applied to every turn" — the right home
            # for a system message, rather than smuggling it into the user turn.
            session.profile = system
        if exchanges:
            from chimera.interface.session import ChatTurn

            session.turns = [ChatTurn(user=u, assistant=a) for u, a in exchanges]
        session.set_model(resolve_model(req.model))
        return session, final_user

    @app.post("/v1/chat/completions", dependencies=[guard])
    def chat_completions(req: ChatCompletionRequest) -> Any:
        if not req.messages:
            raise HTTPException(status_code=400, detail="messages must not be empty")
        session, message = _prepare(req)
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        created = int(time.time())

        if not req.stream:
            try:
                report = turn_fn(session, message)
            except Exception as exc:  # noqa: BLE001 — surface as a 500 with no internals leaked
                _log.warning("openai-compat turn failed: %s", exc)
                raise HTTPException(status_code=500, detail="the agent turn failed") from exc
            return build_completion(report, model=req.model, completion_id=completion_id, created=created)

        def stream() -> Iterator[str]:
            # The loop is run to completion first, then emitted as one content chunk: a Chimera turn is
            # multi-step, so there is no honest token-by-token stream to forward for the *final* answer
            # (intermediate steps are not the answer). Clients still get a valid stream + [DONE].
            try:
                report = turn_fn(session, message)
            except Exception as exc:  # noqa: BLE001
                _log.warning("openai-compat stream turn failed: %s", exc)
                yield _chunk(completion_id, created, req.model, {}, "stop")
                yield "data: [DONE]\n\n"
                return
            model = report.model or req.model
            yield _chunk(completion_id, created, model, {"role": "assistant", "content": ""}, None)
            yield _chunk(completion_id, created, model, {"content": report.answer}, None)
            final = _chunk(completion_id, created, model, {}, _finish_reason(report))
            # Attach usage to the terminal chunk (what stream_options.include_usage asks for).
            payload = json.loads(final[len("data: ") :])
            payload["usage"] = _usage(report)
            yield f"data: {json.dumps(payload)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/v1/models", dependencies=[guard])
    def list_models() -> dict[str, Any]:
        """The catalog, as OpenAI-shaped model ids — so `client.models.list()` works."""
        from chimera.providers.catalog import CATALOG

        data = [
            {"id": f"{_PREFIX}{entry.slug}", "object": "model", "created": 0, "owned_by": entry.vendor}
            for entry in CATALOG
        ]
        data.insert(0, {"id": "chimera", "object": "model", "created": 0, "owned_by": "chimera"})
        return {"object": "list", "data": data}
