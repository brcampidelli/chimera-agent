"""`use_planner` is a switch on every autonomous entrance, and one policy decides its default (P1-01).

It was `True` everywhere and reachable only through `chimera solve --no-plan`: Kanban lanes, the app's
autonomous run and workflow solve steps had no way to skip the planning call. These tests hold the
shape — an explicit word wins, `None` means the policy, and the policy ships OFF (on, i.e. unchanged)
until `bench/planner_ab` meets its pre-registered rule.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.api.app import RunRequest
from chimera.config import get_settings
from chimera.core import planner_policy
from chimera.core.planner_policy import resolve_use_planner
from chimera.core.registry import AgentDef
from chimera.kanban.lanes import AgentLane, SolveLane
from chimera.kanban.models import KanbanCard
from chimera.providers.gateway import CompletionResult
from chimera.workflow.executors import _optional_bool, build_executors
from chimera.workflow.models import WorkflowStep


class _Stop(Exception):
    """Raised by the fake loop once it has its config, before any model call."""


class _CaptureLoop:
    configs: list[Any] = []

    def __init__(self, _worker: Any, **kwargs: Any) -> None:
        _CaptureLoop.configs.append(kwargs.get("config"))

    def run(self, *_args: Any, **_kwargs: Any) -> None:
        raise _Stop()


class _Gateway:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def complete(self, *_args: Any, **_kwargs: Any) -> CompletionResult:
        return CompletionResult(content="", model="fake")


@pytest.fixture
def configs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_PREFIX_NONCE", "")
    get_settings.cache_clear()
    _CaptureLoop.configs = []
    monkeypatch.setattr("chimera.core.AutonomousAgent", _CaptureLoop)
    monkeypatch.setattr("chimera.providers.LLMGateway", _Gateway)
    return _CaptureLoop.configs


def _card(verify: str | None = None) -> KanbanCard:
    return KanbanCard(id="c1", title="t", action="fix the parser", verify=verify)


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ws


# --- the policy -----------------------------------------------------------------------------


def test_the_policy_ships_unarmed_so_the_default_is_unchanged() -> None:
    assert planner_policy.PLANNER_OFF_WHEN_VERIFIED is False
    assert resolve_use_planner(None, has_verifier=True) is True
    assert resolve_use_planner(None, has_verifier=False) is True


def test_an_explicit_word_beats_the_policy_in_both_directions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(planner_policy, "PLANNER_OFF_WHEN_VERIFIED", True)
    assert resolve_use_planner(True, has_verifier=True) is True
    assert resolve_use_planner(False, has_verifier=False) is False


def test_armed_policy_skips_the_planner_only_when_there_is_a_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(planner_policy, "PLANNER_OFF_WHEN_VERIFIED", True)
    assert resolve_use_planner(None, has_verifier=True) is False
    assert resolve_use_planner(None, has_verifier=False) is True


# --- the entrances --------------------------------------------------------------------------


def test_the_solve_lane_forwards_the_switch(tmp_path: Path, configs: list[Any]) -> None:
    with pytest.raises(_Stop):
        SolveLane(workspace=_ws(tmp_path), use_planner=False).run(_card())
    assert [c.use_planner for c in configs] == [False]


def test_the_solve_lane_default_is_still_on(tmp_path: Path, configs: list[Any]) -> None:
    with pytest.raises(_Stop):
        SolveLane(workspace=_ws(tmp_path)).run(_card(verify="pytest -q"))
    assert [c.use_planner for c in configs] == [True]


def test_the_solve_lane_reads_the_policy_for_a_card_with_a_verifier(
    tmp_path: Path, configs: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(planner_policy, "PLANNER_OFF_WHEN_VERIFIED", True)
    with pytest.raises(_Stop):
        SolveLane(workspace=_ws(tmp_path)).run(_card(verify="pytest -q"))
    with pytest.raises(_Stop):
        SolveLane(workspace=_ws(tmp_path)).run(_card())
    assert [c.use_planner for c in configs] == [False, True]


def test_for_workspace_keeps_the_switch(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    assert SolveLane(workspace=ws, use_planner=False).for_workspace(ws).use_planner is False
    lane = AgentLane(AgentDef(id="a"), workspace=ws, use_planner=False)
    assert lane.for_workspace(ws).use_planner is False


def test_the_workflow_solve_step_forwards_the_switch(tmp_path: Path, configs: list[Any]) -> None:
    step = WorkflowStep(name="s", uses="solve", **{"with": {"task": "t", "use_planner": False}})
    with pytest.raises(_Stop):
        build_executors(workspace=_ws(tmp_path))["solve"](step)
    plain = WorkflowStep(name="s", uses="solve", **{"with": {"task": "t"}})
    with pytest.raises(_Stop):
        build_executors(workspace=_ws(tmp_path))["solve"](plain)
    assert [c.use_planner for c in configs] == [False, True]


def test_a_workflow_typo_does_not_pick_a_side() -> None:
    assert _optional_bool(False) is False
    assert _optional_bool("Off") is False
    assert _optional_bool("yes") is True
    assert _optional_bool("flase") is None
    assert _optional_bool(None) is None
    assert _optional_bool(0) is None


def test_the_app_run_request_carries_the_switch_unset_by_default() -> None:
    assert RunRequest(task="x").use_planner is None
    assert RunRequest(task="x", use_planner=False).use_planner is False
