"""Three surfaces asked to be able to ask a person, and the parameter was dropped one layer down.

`chimera/scheduler/job_runner.py`, `chimera/server/manager.py` and `chimera/kanban/lanes.py` all call
`governed_profile(..., home=settings.home)`. One of them says why in a comment on the line above:
*"Governance on the path that runs unattended."* `home` is what `approver_for` reads to opt into
asking somebody who is not at the keyboard — the whole mechanism in `chimera/governance/pending.py`,
tested in isolation and complete.

`governed_profile` used the parameter for one thing (`AuditLog(home / "audit.jsonl")`) and never
handed it on to `govern_step`, which is what builds the approver. So on all three surfaces every
REVIEW was refused with nobody asked, the operator who set `CHIMERA_APPROVAL_MODE=ask` believed they
would be told before anything risky, and the only trace was a line in `audit.jsonl`.

The parameter was named in the signature. That is what made the omission read as wiring rather than
as a decision, and it is why this file exists: the guard is not "does `ask_durably` work" — it did —
but "does a home given to the profile reach the approver".

The second half is the part that would have made forwarding it alone a regression. `ask_durably`
waits fifteen minutes, and no call site has ever passed a `deliver`, so the question would have been
a JSON file nobody was told about. `pending.py` names that outcome itself: *"pretending otherwise
would park a worker for fifteen minutes to reach the same refusal."* So the durable ask travels only
with somewhere to send it, and without one the refusal is immediate and names the setting.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.config import Settings
from chimera.governance.profile import governed_profile
from chimera.tools.base import is_refusal
from chimera.tools.registry import ToolRegistry
from chimera.tools.shell import RunShellTool

#: A command `policy.py` classifies as REVIEW — legitimate in some hands, so it needs a person.
REVIEWED = "git push --force origin main"


class _Sandbox:
    """Runs nothing. The gate is what is under test; whether a shell works is not."""

    def run(self, command: str, timeout: float = 0, cwd: Any = None) -> Any:
        from chimera.sandbox.base import SandboxResult

        return SandboxResult(exit_code=0, stdout="pushed", stderr="", timed_out=False)

    def is_isolated(self) -> bool:
        return True


def _settings(tmp_path: Path, **extra: Any) -> Settings:
    return Settings(
        CHIMERA_HOME=str(tmp_path / "home"),
        CHIMERA_GOVERNANCE="enforce",
        CHIMERA_APPROVAL_MODE="ask",
        **extra,
    )


def _registry(tmp_path: Path) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(RunShellTool(tmp_path, _Sandbox(), confirm=None))
    return registry


@pytest.fixture(autouse=True)
def _no_terminal(monkeypatch: Any) -> None:
    """Every surface in this file runs unattended. Pinned rather than inherited from the runner."""
    monkeypatch.setattr(
        "chimera.governance.approval.nobody_is_at_a_terminal", lambda: True, raising=True
    )
    monkeypatch.setattr(
        "chimera.governance.profile.nobody_is_at_a_terminal", lambda: True, raising=False
    )


def _asked(monkeypatch: Any) -> list[dict[str, Any]]:
    """Capture what would have been asked, without waiting fifteen minutes to find out."""
    seen: list[dict[str, Any]] = []

    def fake(home: Any, action: str, reason: str, *, deliver: Any = None, **_kw: Any) -> bool:
        seen.append({"home": home, "action": action, "reason": reason, "deliver": deliver})
        if deliver is not None:
            deliver(f"needs a decision: {action}")
        return False  # silence refuses, which is the rule everywhere else too

    monkeypatch.setattr("chimera.governance.pending.ask_durably", fake, raising=True)
    return seen


# --- the defect ----------------------------------------------------------------------------------


def test_a_home_given_to_the_profile_reaches_the_approver(tmp_path: Path, monkeypatch: Any) -> None:
    """The whole bug in one assertion. Before: this list stayed empty and the call was refused."""
    asked = _asked(monkeypatch)
    settings = _settings(tmp_path, CHIMERA_APPROVAL_WEBHOOK="https://example.invalid/hook")
    registry, _ = governed_profile(
        _registry(tmp_path), settings=settings, home=settings.home, surface="cron:test"
    )

    registry.run("run_shell", command=REVIEWED)

    assert len(asked) == 1, "the question never reached the durable approver"
    assert asked[0]["home"] == settings.home


def test_the_question_is_actually_delivered_somewhere(tmp_path: Path, monkeypatch: Any) -> None:
    """A question nobody is told about is a fifteen-minute pause before the same refusal."""
    sent: list[str] = []
    asked = _asked(monkeypatch)
    monkeypatch.setattr(
        "chimera.scheduler.delivery.deliver_to_webhook",
        lambda url, text, **_kw: (sent.append(text), _Delivered())[1],
        raising=True,
    )
    settings = _settings(tmp_path, CHIMERA_APPROVAL_WEBHOOK="https://example.invalid/hook")
    registry, _ = governed_profile(
        _registry(tmp_path), settings=settings, home=settings.home, surface="cron:test"
    )

    registry.run("run_shell", command=REVIEWED)

    assert asked[0]["deliver"] is not None, "the approver was given no way to reach anyone"
    assert sent and "needs a decision" in sent[0]


class _Delivered:
    ok = True
    detail = ""


# --- the degradation, which is the half that keeps the fix from being a regression -----------------


def test_with_nowhere_to_send_it_the_refusal_is_immediate(tmp_path: Path, monkeypatch: Any) -> None:
    """No webhook: refuse now. Writing a question nobody sees and waiting reaches the same answer."""
    asked = _asked(monkeypatch)
    settings = _settings(tmp_path)  # no CHIMERA_APPROVAL_WEBHOOK
    registry, _ = governed_profile(
        _registry(tmp_path), settings=settings, home=settings.home, surface="cron:test"
    )

    out = registry.run("run_shell", command=REVIEWED)

    assert asked == [], "it waited on a question nobody would ever see"
    assert is_refusal(out)


def test_the_refusal_names_the_setting_that_would_let_it_ask(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """"Nobody approved it" is true here and actionable in none of its three causes."""
    _asked(monkeypatch)
    settings = _settings(tmp_path)
    registry, _ = governed_profile(
        _registry(tmp_path), settings=settings, home=settings.home, surface="cron:test"
    )

    out = registry.run("run_shell", command=REVIEWED)

    assert "CHIMERA_APPROVAL_WEBHOOK" in out
    assert "refused identically" in out, "it must not invite a retry that cannot work"


def test_a_surface_that_never_asked_for_it_is_unchanged(tmp_path: Path, monkeypatch: Any) -> None:
    """No `home` means the caller did not opt in, and a webhook alone would not change that.

    The refusal must not name a setting that would not have helped: with no home, `approver_for`
    denies whatever the webhook says, so pointing at one would send the reader after the wrong fix.
    """
    _asked(monkeypatch)
    settings = _settings(tmp_path, CHIMERA_APPROVAL_WEBHOOK="https://example.invalid/hook")
    registry, _ = governed_profile(
        _registry(tmp_path), settings=settings, home=settings.home, surface="cron:test"
    )
    # Same construction, but the profile is what carries `home` — so read the other case through
    # `govern_step`, which is the seam the API uses and which receives no home at all.
    from chimera.governance.audit import AuditLog
    from chimera.governance.profile import govern_step

    step = govern_step(
        _registry(tmp_path),
        settings=settings,
        audit=AuditLog(tmp_path / "audit.jsonl"),
        surface="api",
        attended=False,
    )
    out = step.registry.run("run_shell", command=REVIEWED)
    assert "CHIMERA_APPROVAL_WEBHOOK" not in out
    assert "over the API" in out, "the unattended reason is the right one for that surface"


# --- what must not have moved ----------------------------------------------------------------------


def test_an_ordinary_command_still_runs(tmp_path: Path, monkeypatch: Any) -> None:
    """The gate is for the reviewed few. Everything else is untouched by all of the above."""
    _asked(monkeypatch)
    settings = _settings(tmp_path, CHIMERA_APPROVAL_WEBHOOK="https://example.invalid/hook")
    registry, _ = governed_profile(
        _registry(tmp_path), settings=settings, home=settings.home, surface="cron:test"
    )

    out = registry.run("run_shell", command="echo hello")

    assert not is_refusal(out)


def test_a_hard_block_is_still_refused_without_asking_anyone(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A BLOCK is a fixed signature: no approver can release it, and none should be consulted."""
    asked = _asked(monkeypatch)
    settings = _settings(tmp_path, CHIMERA_APPROVAL_WEBHOOK="https://example.invalid/hook")
    registry, _ = governed_profile(
        _registry(tmp_path), settings=settings, home=settings.home, surface="cron:test"
    )

    out = registry.run("run_shell", command="rm -rf /")

    assert is_refusal(out)
    assert asked == [], "a refusal is not a question"


def test_deny_mode_still_says_the_owner_denies(tmp_path: Path, monkeypatch: Any) -> None:
    """A webhook does not override a deployment that configured refusal."""
    _asked(monkeypatch)
    settings = Settings(
        CHIMERA_HOME=str(tmp_path / "home"),
        CHIMERA_GOVERNANCE="enforce",
        CHIMERA_APPROVAL_MODE="deny",
        CHIMERA_APPROVAL_WEBHOOK="https://example.invalid/hook",
    )
    registry, _ = governed_profile(
        _registry(tmp_path), settings=settings, home=settings.home, surface="cron:test"
    )

    out = registry.run("run_shell", command=REVIEWED)

    assert "approvals to deny" in out


def test_governance_off_installs_nothing(tmp_path: Path, monkeypatch: Any) -> None:
    """The default install is untouched by any of this."""
    _asked(monkeypatch)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_APPROVAL_MODE="ask")
    registry, _ = governed_profile(
        _registry(tmp_path), settings=settings, home=settings.home, surface="cron:test"
    )

    assert not is_refusal(registry.run("run_shell", command=REVIEWED))


# --- the resolver ------------------------------------------------------------------------------------


def test_no_webhook_means_no_deliverer() -> None:
    from chimera.governance.approval import deliverer_for

    assert deliverer_for(Settings(CHIMERA_HOME="x")) is None


def test_a_blank_webhook_is_no_webhook() -> None:
    """A trailing-comma-shaped mistake in a text field must not read as a destination."""
    from chimera.governance.approval import deliverer_for

    assert deliverer_for(Settings(CHIMERA_HOME="x", CHIMERA_APPROVAL_WEBHOOK="   ")) is None


def test_a_dead_webhook_costs_the_notification_not_the_gate(monkeypatch: Any) -> None:
    """The question is on disk and `chimera approve --list` finds it either way."""
    from chimera.governance.approval import deliverer_for

    class _Failed:
        ok = False
        detail = "example.invalid: 500"

    monkeypatch.setattr(
        "chimera.scheduler.delivery.deliver_to_webhook",
        lambda *_a, **_k: _Failed(),
        raising=True,
    )
    send = deliverer_for(Settings(CHIMERA_HOME="x", CHIMERA_APPROVAL_WEBHOOK="https://x.invalid/h"))
    assert send is not None
    send("anything")  # must not raise


# --- the switch, and the one next to it that deliberately is not one -------------------------------


def test_the_kernel_switch_is_reachable_from_the_app() -> None:
    """It shipped `off` on every surface with no control anywhere but a text editor."""
    from chimera.api.config_api import is_editable

    assert is_editable("CHIMERA_GOVERNANCE")
    assert is_editable("CHIMERA_APPROVAL_WEBHOOK")


def test_the_approval_mode_is_deliberately_not_editable_from_the_app() -> None:
    """A decision, recorded so it does not get "fixed" as an oversight later.

    Turning the gate ON is safe in every direction, so it belongs on a screen. `approval_mode`
    is the other kind: its third value is `allow`, which approves everything — including a request
    that arrived inside content the run fetched from the web. That one stays a deliberate act in a
    file, not a dropdown two clicks from the model picker.
    """
    from chimera.api.config_api import is_editable

    assert not is_editable("CHIMERA_APPROVAL_MODE")
