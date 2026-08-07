"""Two axes, nine pairs, and every one of them checked against the mechanism it claims.

The value of a posture control is entirely in whether the machine agrees with the label. A "read
only" that leaves `write_file` in the registry is worse than no control at all, because the user
stops watching. So the table below is exhaustive over the nine (reach × approval) pairs rather than
sampled: the whole point of an orthogonal design is that the combinations are not special cases, and
a test that only walks the diagonal would never notice if one of them were.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.api.posture import Approval, Posture, Reach, describe, resolve
from chimera.config import Settings
from chimera.governance.ledger import EXEC_TOOLS, WRITE_TOOLS

REACHES: list[Reach] = ["read_only", "workspace", "workspace_shell"]
APPROVALS: list[Approval] = ["always", "suspicious", "never"]
PAIRS = [(r, a) for r in REACHES for a in APPROVALS]
_IDS = [f"{r}+{a}" for r, a in PAIRS]


@pytest.mark.parametrize(("reach", "approval"), PAIRS, ids=_IDS)
def test_every_pair_resolves_to_exactly_the_mechanism_it_names(reach: Reach, approval: Approval) -> None:
    resolved = resolve(Posture(reach=reach, approval=approval))
    denied = set(resolved.deny_tools)

    # Reach, stated as set membership rather than as a name list, so a tool added to WRITE_TOOLS or
    # EXEC_TOOLS tomorrow is covered by this assertion without anyone remembering to come back.
    assert (denied >= WRITE_TOOLS) is (reach == "read_only")
    assert (denied >= EXEC_TOOLS) is (reach in {"read_only", "workspace"})
    if reach == "workspace_shell":
        assert not denied

    # Approval. Exactly one pause trigger is armed, and "never" arms neither.
    assert resolved.pause_always is (approval == "always")
    assert resolved.pause_on_taint is (approval == "suspicious")
    assert resolved.needs_thread is (approval != "never")

    # The two axes do not leak into each other: that is what makes them axes.
    assert resolved.narrow_on_taint == resolved.pause_on_taint


def test_read_only_removes_the_write_tools_from_a_real_registry(tmp_path: Any) -> None:
    """Asserted through the registry the run would actually get. A posture the tool assembly does
    not consult is a label, and a label is what people trust when they stop checking."""
    from chimera.api.code_api import CodeSeams, assemble_registry
    from chimera.providers import LLMGateway

    ws = tmp_path / "ws"
    ws.mkdir()
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    seams = CodeSeams(posture=Posture(reach="read_only", approval="never"))
    registry, _ = assemble_registry(seams, ws, settings, LLMGateway(), steps=8)

    names = set(registry.names())
    assert not (WRITE_TOOLS & names) and not (EXEC_TOOLS & names)
    assert "read_file" in names  # reading is the whole point of read-only, not a side effect


def test_a_posture_and_an_explicit_denylist_are_unioned_not_replaced(tmp_path: Any) -> None:
    """Two ways of saying "not this tool" must not cancel each other out — the stricter of two
    stated intentions has to survive, in both directions."""
    from chimera.api.code_api import CodeSeams, assemble_registry
    from chimera.providers import LLMGateway

    ws = tmp_path / "ws"
    ws.mkdir()
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    seams = CodeSeams(
        posture=Posture(reach="workspace", approval="never"), deny_tools=["read_file"]
    )
    registry, _ = assemble_registry(seams, ws, settings, LLMGateway(), steps=8)

    names = set(registry.names())
    assert "read_file" not in names  # the explicit denial survived the posture
    assert not (EXEC_TOOLS & names)  # and the posture's survived the explicit denial


def test_no_posture_means_no_posture_not_the_default_one(tmp_path: Any) -> None:
    """A caller that never heard of postures must keep the behaviour it had. The DEFAULT posture
    denies the exec tools, so applying it silently would break every existing client in a way that
    reads as the agent having got worse at its job."""
    from chimera.api.code_api import CodeSeams, assemble_registry
    from chimera.providers import LLMGateway

    ws = tmp_path / "ws"
    ws.mkdir()
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    registry, _ = assemble_registry(CodeSeams(), ws, settings, LLMGateway(), steps=8)

    assert "run_shell" in set(registry.names())


def test_the_described_facts_say_the_shell_does_not_run_at_all_under_read_only(tmp_path: Any) -> None:
    facts = describe(
        Posture(reach="read_only", approval="never"), tmp_path, Settings(CHIMERA_HOME=str(tmp_path))
    )
    assert facts.writes == "nothing" and facts.shell == "none" and facts.pauses == "never"


def test_the_facts_report_the_HOST_when_docker_was_asked_for_but_is_not_there(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """The reason this is generated rather than echoed back.

    Someone who configured a Docker sandbox and lost the daemon is in the one situation where the
    honest answer contradicts their setup — and the setting still says "docker". Reading the config
    here is exactly how "I thought it was sandboxed" happens.
    """
    monkeypatch.setattr(
        "chimera.sandbox.confirm.sandbox_is_isolated", lambda _s: False, raising=True
    )
    settings = Settings(CHIMERA_HOME=str(tmp_path), CHIMERA_SANDBOX="docker", CHIMERA_HOST_EXEC="allow")

    facts = describe(Posture(reach="workspace_shell", approval="never"), tmp_path, settings)
    assert facts.shell == "host"
    assert facts.fell_back_to_host, "a fallen-back docker sandbox must be reported, not hidden"


def test_a_host_exec_deny_is_reported_as_refused_not_as_running(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "chimera.sandbox.confirm.sandbox_is_isolated", lambda _s: False, raising=True
    )
    settings = Settings(CHIMERA_HOME=str(tmp_path), CHIMERA_HOST_EXEC="deny")

    facts = describe(Posture(reach="workspace_shell", approval="never"), tmp_path, settings)
    assert facts.shell == "refused"
    assert not facts.fell_back_to_host  # nothing fell back — local was what was asked for


def test_a_conversation_turn_reports_that_it_cannot_pause(tmp_path: Any) -> None:
    """The approval axis resolves to `pause_on_taint` for both surfaces, but only the run wires a
    checkpointer and a taint ledger — `build_agent` for a turn passes neither. So a turn cannot stop
    and ask, whatever the user selected, and saying "pauses when tainted" there described a
    capability that surface does not have.

    This line exists precisely so nobody has to read the source to know what the agent may do to
    their files. A sentence that is wrong on the surface people use most is worse than no sentence.
    """
    from chimera.api.posture import Posture, describe
    from chimera.config import Settings

    settings = Settings(CHIMERA_HOME=str(tmp_path))
    posture = Posture(reach="workspace", approval="suspicious")

    assert describe(posture, tmp_path, settings).pauses == "tainted"
    assert describe(posture, tmp_path, settings, can_pause=False).pauses == "never"


def test_the_other_facts_are_unchanged_by_the_surface(tmp_path: Any) -> None:
    """Only the pause differs. Reach and the shell are the same on both surfaces, and quietly
    changing them here would make one screen's sentence a different claim than the other's."""
    from chimera.api.posture import Posture, describe
    from chimera.config import Settings

    settings = Settings(CHIMERA_HOME=str(tmp_path))
    posture = Posture(reach="read_only", approval="always")

    run = describe(posture, tmp_path, settings)
    turn = describe(posture, tmp_path, settings, can_pause=False)

    assert run.writes == turn.writes == "nothing"
    assert run.shell == turn.shell
    assert run.pauses == "always" and turn.pauses == "never"


# --- The chat's missing guard, said out loud ------------------------------------------------
#
# The chat and the coding turn talk to the same agent over the same base tools, and until now only
# one was protected: no write region, no denylist, no registry restriction, no taint ledger. So the
# chat kept the execution tools the coding turn removes, and none of the tools that refuse once a run
# has read untrusted content. The default stays permissive on purpose — the registry is shared with
# the messaging gateway, and arming it silently would take shell away from agents already running —
# which is exactly why the sentence has to admit it.


def test_a_chat_without_the_guard_says_it_is_unguarded(tmp_path: Any) -> None:
    from chimera.api.posture import Posture, describe

    settings = Settings(CHIMERA_HOME=str(tmp_path), CHIMERA_GUARD_CHAT=False)
    facts = describe(Posture(), tmp_path, settings, guarded=False)
    assert facts.unguarded is True


def test_the_coding_turn_is_never_reported_as_unguarded(tmp_path: Any) -> None:
    # It assembles its registry through the ledger on every request, with no switch to turn off.
    from chimera.api.posture import Posture, describe

    settings = Settings(CHIMERA_HOME=str(tmp_path))
    assert describe(Posture(), tmp_path, settings).unguarded is False


def test_the_guard_denies_the_execution_tools_and_wraps_the_rest(tmp_path: Any) -> None:
    # Applied to an ALREADY-BUILT registry on purpose: the chat's registry carries MCP tools that a
    # from-scratch rebuild would drop, and a guard that covers only the tools we wrote is not a guard.
    from chimera.api.posture import guard_chat_registry
    from chimera.governance.ledger import EXEC_TOOLS
    from chimera.tools import default_registry

    before = default_registry(tmp_path)
    assert EXEC_TOOLS & set(before.names())  # the tools the chat has always had

    after, ledger = guard_chat_registry(before)
    assert not (EXEC_TOOLS & set(after.names()))
    assert ledger.run_tainted() is False  # a fresh run is clean until something untrusted arrives
