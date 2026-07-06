"""Tests for the ACE incremental delta-playbook (M14 C2)."""

from __future__ import annotations

from chimera.evolution.playbook import (
    BackendDeltaProposer,
    Delta,
    Playbook,
    PlaybookCurator,
    PlaybookItem,
    _parse_deltas,
)
from chimera.providers.gateway import CompletionResult

# --- Playbook edits ----------------------------------------------------------------------


def test_add_creates_bullet_with_id() -> None:
    pb = Playbook()
    item = pb.add("always snapshot before editing", section="strategy")
    assert item is not None
    assert item.id == "strategy-1"
    assert pb.active() == [item]


def test_add_dedupes_into_reinforce() -> None:
    pb = Playbook()
    first = pb.add("check the exit code")
    again = pb.add("Check the exit code")  # same content, different case/spacing
    assert again is first  # reinforced, not duplicated
    assert first.helpful == 1
    assert len(pb.active()) == 1


def test_add_ignores_empty_content() -> None:
    assert Playbook().add("   ") is None


def test_reinforce_and_deprecate_move_counters() -> None:
    pb = Playbook(deprecate_at=2)
    item = pb.add("prefer joins over N+1 queries")
    assert pb.apply(Delta(op="reinforce", target=item.id)) is True
    assert item.helpful == 1
    pb.apply(Delta(op="deprecate", target=item.id))
    pb.apply(Delta(op="deprecate", target=item.id))
    pb.apply(Delta(op="deprecate", target=item.id))  # score now -2 -> deprecated
    assert item.status == "deprecated"
    assert item not in pb.active()


def test_apply_unknown_target_is_noop() -> None:
    assert Playbook().apply(Delta(op="reinforce", target="missing-9")) is False


def test_curate_never_replaces_existing_items() -> None:
    # The anti-context-collapse guarantee: applying deltas only adds/edits, never wipes.
    pb = Playbook()
    kept = pb.add("existing hard-won lesson")
    pb.apply_all([Delta(op="add", content="a new lesson", section="strategy")])
    contents = {i.content for i in pb.active()}
    assert "existing hard-won lesson" in contents  # survived
    assert "a new lesson" in contents
    assert kept.status == "active"


# --- grow-and-refine ---------------------------------------------------------------------


def test_refine_merges_duplicates() -> None:
    pb = Playbook()
    a = pb.add("validate inputs with a schema")
    # Force a raw duplicate past the add-dedupe by appending directly, then refine.
    pb.items.append(PlaybookItem(id="strategy-99", content="Validate inputs with a schema", helpful=3))
    pb.refine()
    assert len([i for i in pb.active()]) == 1
    assert a.helpful >= 3  # counters merged into the survivor


def test_refine_caps_size_by_deprecating_weakest() -> None:
    pb = Playbook(max_items=2)
    weak = pb.add("weak bullet")
    mid = pb.add("mid bullet")
    strong = pb.add("strong bullet")
    pb.apply(Delta(op="reinforce", target=strong.id))
    pb.apply(Delta(op="reinforce", target=mid.id))
    pb.refine()
    active_ids = {i.id for i in pb.active()}
    assert len(active_ids) == 2
    assert strong.id in active_ids and mid.id in active_ids
    assert weak.id not in active_ids  # lowest score dropped over the cap


# --- render ------------------------------------------------------------------------------


def test_render_orders_by_score_and_hides_ids_by_default() -> None:
    pb = Playbook()
    low = pb.add("low")
    high = pb.add("high")
    assert low is not None
    pb.apply(Delta(op="reinforce", target=high.id))
    text = pb.render()
    assert text.index("high") < text.index("low")  # higher score first
    assert low.id not in text and high.id not in text  # ids hidden for agent injection


def test_render_with_ids_for_curation() -> None:
    pb = Playbook()
    item = pb.add("bullet")
    assert item.id in pb.render(with_ids=True)


def test_render_empty_is_blank() -> None:
    assert Playbook().render() == ""


# --- persistence -------------------------------------------------------------------------


def test_roundtrips_through_dict() -> None:
    pb = Playbook(max_items=7, deprecate_at=3)
    pb.add("keep me", section="check")
    pb.apply(Delta(op="reinforce", target="check-1"))
    restored = Playbook.from_dict(pb.to_dict())
    assert restored.max_items == 7 and restored.deprecate_at == 3
    assert restored.active()[0].content == "keep me"
    assert restored.active()[0].helpful == 1
    assert restored._seq == 1  # sequence recovered from ids so new adds don't collide


# --- delta parsing + curator -------------------------------------------------------------


def test_parse_deltas_filters_malformed() -> None:
    raw = (
        'noise {"deltas": ['
        '{"op":"add","content":"good","section":"strategy"},'
        '{"op":"bogus","content":"x"},'
        '{"op":"reinforce","target":"strategy-1"},'
        '"not a dict"'
        ']} trailing'
    )
    deltas = _parse_deltas(raw)
    assert [d.op for d in deltas] == ["add", "reinforce"]


def test_parse_deltas_degrades_on_garbage() -> None:
    assert _parse_deltas("no json here") == []
    assert _parse_deltas('{"deltas": "not a list"}') == []


class _FixedProposer:
    def __init__(self, deltas: list[Delta]) -> None:
        self.deltas = deltas
        self.seen_playbook = ""

    def propose(self, task: str, outcome: str, playbook_text: str) -> list[Delta]:
        self.seen_playbook = playbook_text
        return self.deltas


def test_curator_applies_and_sees_ids() -> None:
    pb = Playbook()
    seeded = pb.add("seeded bullet")
    proposer = _FixedProposer(
        [
            Delta(op="add", content="learned this run", section="strategy"),
            Delta(op="reinforce", target=seeded.id),
        ]
    )
    applied = PlaybookCurator(proposer).curate(pb, "task", "it worked")
    assert applied == 2
    assert seeded.helpful == 1
    assert any(i.content == "learned this run" for i in pb.active())
    assert seeded.id in proposer.seen_playbook  # curator rendered ids for the model


def test_backend_proposer_parses_model_json() -> None:
    class _Backend:
        def complete(self, messages: object, **kwargs: object) -> CompletionResult:
            return CompletionResult(content='{"deltas":[{"op":"add","content":"x"}]}', model="fake")

    deltas = BackendDeltaProposer(_Backend()).propose("t", "o", "")
    assert len(deltas) == 1 and deltas[0].content == "x"


def test_backend_proposer_degrades_on_error() -> None:
    class _Boom:
        def complete(self, messages: object, **kwargs: object) -> CompletionResult:
            raise RuntimeError("curator down")

    assert BackendDeltaProposer(_Boom()).propose("t", "o", "") == []  # no update, no crash


# --- integration: the playbook is injected into the agent's context ----------------------


def test_playbook_reaches_the_worker_prompt() -> None:
    from chimera.core.agent import AgentResult
    from chimera.core.autonomous import AutonomousAgent, AutonomousConfig

    class _RecordingWorker:
        def __init__(self) -> None:
            self.prompt = ""

        def run(self, task: str) -> AgentResult:
            self.prompt = task
            return AgentResult(answer="done", steps=1, transcript=[], stopped_reason="done")

    pb = Playbook()
    pb.add("always snapshot before a risky edit", section="strategy")
    worker = _RecordingWorker()
    agent = AutonomousAgent(
        worker,
        playbook=pb,
        config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=False),
    )
    agent.run("do a risky edit")
    assert "always snapshot before a risky edit" in worker.prompt  # advisory guidance injected
