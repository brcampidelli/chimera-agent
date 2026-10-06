"""Tests for per-skill usage metrics + retirement signal (A6)."""

from __future__ import annotations

from pathlib import Path

from chimera.evolution import CardRetriever, LearnedSkill, SkillStore
from chimera.evolution.skill_store import stats_fingerprint


def _skill(name: str) -> LearnedSkill:
    return LearnedSkill(
        name=name,
        description=f"{name} desc",
        trigger="t",
        do=f"apply {name}",
        check="verify output",
        triggers=[name],
    )


def test_statistics_fingerprint_is_bound_to_model_and_tools(tmp_path: Path) -> None:
    assert stats_fingerprint("model-a", ["read", "write"]) == stats_fingerprint(
        "model-a", ["write", "read"]
    )
    assert stats_fingerprint("model-a", ["read"]) != stats_fingerprint(
        "model-b", ["read"]
    )
    assert stats_fingerprint("model-a", ["read"]) != stats_fingerprint(
        "model-a", ["write"]
    )
    assert SkillStore(tmp_path / "skills.json").evolution_enabled is False


def test_record_use_accumulates(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills.json")
    store.add(_skill("s1"))
    store.record_use("s1", success=True)
    store.record_use("s1", success=False)
    store.record_use("s1", success=True)
    row = store.stats()[0]
    assert row["uses"] == 3 and row["successes"] == 2 and row["rate"] == 0.667


def test_record_use_unknown_skill_is_noop(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills.json")
    store.record_use("ghost", success=True)  # must not crash or create an entry
    assert store.stats() == []


def test_readd_preserves_counters(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills.json")
    store.add(_skill("s1"))
    store.record_use("s1", success=True)
    store.add(_skill("s1"))  # a refinement re-adds the same name
    row = store.stats()[0]
    assert row["uses"] == 1 and row["successes"] == 1  # track record survives


def test_counters_survive_reload(tmp_path: Path) -> None:
    path = tmp_path / "skills.json"
    store = SkillStore(path)
    store.add(_skill("s1"))
    store.record_use("s1", success=True)
    row = SkillStore(path).stats()[0]  # fresh load from disk
    assert row["uses"] == 1 and row["successes"] == 1

def test_stats_are_isolated_by_model_and_tool_fingerprint(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills.json")
    store.add(_skill("scoped"))
    store.record_use("scoped", success=False, model="model-a", tools=["read", "write"])
    store.record_use("scoped", success=True, model="model-b", tools=["read", "write"])
    store.record_use("scoped", success=True, model="model-a", tools=["read"])

    model_a = store.stats(model="model-a", tools=["write", "read"])[0]
    model_b = store.stats(model="model-b", tools=["read", "write"])[0]
    other_tools = store.stats(model="model-a", tools=["read"])[0]
    assert (model_a["uses"], model_a["successes"]) == (1, 0)
    assert (model_b["uses"], model_b["successes"]) == (1, 1)
    assert (other_tools["uses"], other_tools["successes"]) == (1, 1)


def test_retirement_candidates_need_uses_and_low_rate(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills.json")
    for name in ("loser", "winner", "young"):
        store.add(_skill(name))
    for _ in range(6):
        store.record_use("loser", success=False)
        store.record_use("winner", success=True)
    store.record_use("young", success=False)  # only 1 use — too early to judge
    assert store.retirement_candidates() == ["loser"]


def test_retriever_credits_outcome_to_retrieved_cards(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills.json")
    store.add(_skill("relevant"))
    store.add(_skill("unrelated-zzz"))
    retriever = CardRetriever(store, k=1)
    context = retriever.card_context("apply relevant please")
    assert "relevant" in context and retriever.last_retrieved == ["relevant"]

    retriever.record_outcome(True)
    rows = {r["name"]: r for r in store.stats()}
    assert rows["relevant"]["uses"] == 1 and rows["relevant"]["successes"] == 1
    assert rows["unrelated-zzz"]["uses"] == 0  # only injected cards get credited
    assert retriever.last_retrieved == []  # consumed — no double counting


def test_retire_excludes_from_retrieval_but_keeps_skill(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills.json")
    store.add(_skill("stale"))
    assert store.retire("stale") is True
    assert store.retired()[0].name == "stale"
    # Retrieval only takes active skills, so a retired one stops being injected...
    retriever = CardRetriever(store, k=1)
    assert "stale" not in retriever.card_context("apply stale please")
    # ...but the skill is not deleted — it survives a reload as 'retired'.
    assert SkillStore(tmp_path / "skills.json").skills(status="retired")[0].name == "stale"


def test_approve_reactivates_a_retired_skill(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills.json")
    store.add(_skill("comeback"))
    store.retire("comeback")
    assert store.approve("comeback") is True  # un-retire
    assert store.retired() == []
    retriever = CardRetriever(store, k=1)
    assert "comeback" in retriever.card_context("apply comeback please")


def test_retire_unknown_skill_is_false(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills.json")
    assert store.retire("ghost") is False


def test_autonomous_run_records_card_outcome(tmp_path: Path) -> None:
    from chimera.core.agent import AgentResult
    from chimera.core.autonomous import AutonomousAgent, AutonomousConfig

    store = SkillStore(tmp_path / "skills.json")
    store.add(_skill("helper"))
    retriever = CardRetriever(store, k=1)

    class OkWorker:
        def run(self, task: str) -> AgentResult:
            return AgentResult(answer="done", steps=0, transcript=[], stopped_reason="done")

    agent = AutonomousAgent(
        OkWorker(),
        cards=retriever,
        config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=False),
    )
    result = agent.run("use the helper skill")
    assert result.success
    row = store.stats()[0]
    assert row["uses"] == 1 and row["successes"] == 1


# --- persistence hardening + demote responsiveness (12th adversarial review) -------------


def test_skill_store_save_is_atomic_no_temp_left(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills.json")
    store.add(_skill("s1"))
    store.record_use("s1", success=True)
    assert (tmp_path / "skills.json").exists()
    assert not (tmp_path / "skills.json.tmp").exists()  # temp cleaned up by the atomic replace


def test_skill_store_skips_malformed_entries(tmp_path: Path) -> None:
    p = tmp_path / "skills.json"
    p.write_text('[{"name": "good", "status": "active"}, "junk", {"noname": 1}]', encoding="utf-8")
    store = SkillStore(p)
    assert store.names() == ["good"]  # one bad record must not brick the whole library


def test_skill_store_corrupt_file_loads_empty_not_crash(tmp_path: Path) -> None:
    p = tmp_path / "skills.json"
    p.write_text("{ this is not valid json", encoding="utf-8")
    assert len(SkillStore(p)) == 0  # truncated/corrupt file -> empty store, not a crash on init


def test_promote_resets_counters_so_demote_tracks_recent_regression(tmp_path: Path) -> None:
    from chimera.evolution.lifecycle_policy import SkillLifecyclePolicy

    store = SkillStore(tmp_path / "skills.json")
    skill = _skill("s1")
    skill.status = "provisional"  # type: ignore[assignment]
    store.add(skill)
    for _ in range(4):
        store.record_use("s1", success=True)  # strong probation record: 4/5
    store.record_use("s1", success=False)
    store.promote("s1")  # provisional -> active, counters reset
    assert store.stats()[0]["uses"] == 0  # lifetime history no longer masks a regression

    # Now it regresses: 5 straight failures post-promotion -> demote fires promptly.
    for _ in range(5):
        store.record_use("s1", success=False)
    decisions = SkillLifecyclePolicy().decide(store.stats())
    assert "s1" in decisions.demote  # would have needed ~8 failures under the old cumulative rate
