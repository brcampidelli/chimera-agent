"""The external-agent branch of a coding turn.

Kept out of :mod:`chimera.api.code_api` so the endpoint's diff stays small and the shared parts stay
shared: the checkpoint, the verifier, the revert offer and the receipt are the same on both branches
because they are the point. What changes is only who does the work.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any

from chimera.acp.agents import AcpAgentSpec, spec_for
from chimera.acp.client import AcpError
from chimera.acp.registry import SessionKey, registry
from chimera.acp.turn import AcpTurnResult
from chimera.tools.write_region import WriteRegion


class ProviderError(ValueError):
    """The requested provider cannot be run, and the message says what to do about it."""


def resolve_provider(key: str, command: str | None) -> tuple[AcpAgentSpec, list[str]]:
    """The spec and argv for a provider key, or a :class:`ProviderError` explaining why not.

    A custom provider's command is split with :func:`shlex.split` and run WITHOUT a shell, so a
    stray pipe is an argument rather than a second command. That is the same discipline the verify
    command gets, and the same trust level: the person typing it owns the machine.
    """
    spec = spec_for(key)
    if spec is None:
        raise ProviderError(f"unknown provider: {key!r}")
    if spec.key == "custom":
        parts = shlex.split(command or "", posix=False)
        if not parts:
            raise ProviderError("a custom provider needs a command")
        return spec, [p.strip('"') for p in parts]
    return spec, list(spec.argv)


def run_external_turn(
    *,
    provider: str,
    command: str | None,
    message: str,
    workspace: Path,
    session_id: str,
    images: list[str] | None = None,
    write_region: WriteRegion | None = None,
    on_token: Callable[[str], None] | None = None,
    on_tool: Callable[[str, dict[str, Any], bool, str], None] | None = None,
    on_edit: Callable[[str, str], None] | None = None,
) -> AcpTurnResult:
    """Run one turn through an external agent and return what it did.

    The connection is held by conversation (:mod:`chimera.acp.registry`), because a `session/prompt`
    is one message in a conversation the AGENT is keeping. A fresh process per turn would make every
    turn turn one, and "no, the other one" would be answered with "which one?".
    """
    spec, argv = resolve_provider(provider, command)
    key = SessionKey(session_id=session_id, provider=spec.key, workspace=str(Path(workspace).resolve()))
    turn = registry().get(
        key,
        spec,
        argv=argv,
        write_region=write_region,
        on_token=on_token,
        on_tool=on_tool,
        on_edit=on_edit,
    )
    return turn.prompt(message, images=images)


def done_payload(result: AcpTurnResult, *, provider: str, tainted: bool) -> dict[str, Any]:
    """The `done` frame for an external turn.

    Fields the native loop reports and this one cannot are ``None``, never zero. A zero step count
    reads as "it did nothing"; ``None`` reads as "this number does not exist here", which is the
    truth — the steps happened inside somebody else's loop and it did not tell us how many.
    """
    return {
        "answer": result.answer,
        "steps": None,
        "stopped_reason": result.stop_reason,
        "tool_names": list(result.tool_names),
        "model": provider,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "usd": result.usd,
        "context_peak_tokens": None,
        "route_meta": {"provider": provider, "external": True},
        "tainted": tainted,
        "memory_facts_used": 0,
        "memory_layer": "",
        "fused": False,
        # The two facts that are specific to driving somebody else's agent, and that the screen
        # needs in order to describe this turn honestly.
        "external": provider,
        "auto_approved": list(result.auto_approved),
        "refused_writes": list(result.refused),
    }


def failure_message(exc: Exception) -> str:
    """What to show when an external turn fails.

    The agent's own words when we have them. "the coding turn failed" is what the native branch says
    because its failures are ours to debug; an adapter that could not authenticate, or that is not
    installed, has already said something more useful than anything we could invent.
    """
    if isinstance(exc, (AcpError, ProviderError)):
        return str(exc) or "the external agent failed"
    return "the external agent failed"
