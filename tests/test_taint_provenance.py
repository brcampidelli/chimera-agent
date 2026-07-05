"""Tests for anti-poisoning provenance on memories and learned skills (A1).

The "Zombie Agents" defense: durable artifacts born from a run that consumed untrusted
content must be marked (memory) or held for review (skills) — never silently trusted.
"""

from __future__ import annotations

from pathlib import Path

from chimera.evolution import AutoSkillEvolver, CardRetriever, LearnedSkill, SkillStore
from chimera.governance import AuditLog, TaintLedger
from chimera.memory import MemoryManager, MemoryStore


def _skill(name: str, **kwargs: object) -> LearnedSkill:
    return LearnedSkill(
        name=name,
        description=f"{name} desc",
        prompt_template="Do {task}",
        trigger="t",
        do="do the thing",
        check="check it",
        triggers=[name],
        **kwargs,  # type: ignore[arg-type]
    )


class ProposingEvolver:
    """A fake SkillEvolver that always proposes a fixed skill and passes the smoke test."""

    def __init__(self, skill: LearnedSkill) -> None:
        self.skill = skill

    def propose(self, task: str, solution: str) -> LearnedSkill:
        return self.skill

    def propose_failure_card(self, task: str, detail: str) -> LearnedSkill:
        return self.skill

    def test_skill(self, skill: LearnedSkill, test_input: dict, check: object) -> bool:
        return True


# --- TaintLedger.run_tainted ----------------------------------------------------------


def test_run_tainted_false_on_clean_run() -> None:
    led = TaintLedger()
    led.record_read("/tmp/notes.txt")
    led.record_exec("pytest -q")
    assert not led.run_tainted()


def test_run_tainted_true_after_fetch() -> None:
    led = TaintLedger()
    led.record_fetch("https://evil.test/x", content="body")
    assert led.run_tainted()


# --- LearnedSkill serialization -------------------------------------------------------


def test_skill_provenance_roundtrip() -> None:
    skill = _skill("s1", status="pending", provenance="tainted")
    loaded = LearnedSkill.from_dict(skill.to_dict())
    assert loaded.status == "pending" and loaded.provenance == "tainted"


def test_old_skill_dicts_default_to_active_clean() -> None:
    data = _skill("s2").to_dict()
    data.pop("status")
    data.pop("provenance")
    loaded = LearnedSkill.from_dict(data)
    assert loaded.status == "active" and loaded.provenance == "clean"


# --- AutoSkillEvolver gate ------------------------------------------------------------


def test_clean_run_skill_is_auto_accepted(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills.json")
    evolver = AutoSkillEvolver(ProposingEvolver(_skill("clean-skill")), store)  # type: ignore[arg-type]
    kept = evolver.maybe_evolve("task", "solution", prior_successes=5)
    assert kept is not None and kept.status == "active" and kept.provenance == "clean"
    assert store.pending() == []


def test_tainted_run_skill_is_held_pending(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills.json")
    evolver = AutoSkillEvolver(ProposingEvolver(_skill("tainted-skill")), store)  # type: ignore[arg-type]
    kept = evolver.maybe_evolve("task", "solution", prior_successes=5, tainted=True)
    assert kept is not None and kept.status == "pending" and kept.provenance == "tainted"
    assert [s.name for s in store.pending()] == ["tainted-skill"]


def test_tainted_failure_card_is_held_pending(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills.json")
    evolver = AutoSkillEvolver(ProposingEvolver(_skill("anti-card")), store)  # type: ignore[arg-type]
    kept = evolver.maybe_evolve_failure("task", "detail", prior_failures=5, tainted=True)
    assert kept is not None and kept.status == "pending"


def test_tainted_hold_is_audited(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    store = SkillStore(tmp_path / "skills.json")
    evolver = AutoSkillEvolver(ProposingEvolver(_skill("audited")), store, audit=audit)  # type: ignore[arg-type]
    evolver.maybe_evolve("task", "solution", prior_successes=5, tainted=True)
    events = [e for e in audit.entries() if e["type"] == "taint_provenance"]
    assert events and events[0]["name"] == "audited" and events[0]["action"] == "held_pending"


# --- SkillStore review path -------------------------------------------------------------


def test_pending_skill_excluded_from_retrieval_until_approved(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills.json")
    store.add(_skill("poisoned", status="pending", provenance="tainted"))
    retriever = CardRetriever(store)
    assert retriever.card_context("poisoned task") == ""  # pending never influences reasoning

    assert store.approve("poisoned")
    assert "poisoned" in retriever.card_context("poisoned task")


def test_approve_unknown_skill_returns_false(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills.json")
    assert not store.approve("ghost")


# --- Memory provenance ------------------------------------------------------------------


def test_memory_remember_records_provenance(tmp_path: Path) -> None:
    manager = MemoryManager(MemoryStore(tmp_path / "memory.json"))
    op, item = manager.remember("fact from the web", provenance="tainted")
    assert op == "ADD" and item.provenance == "tainted"


def test_tainted_update_taints_previously_clean_fact(tmp_path: Path) -> None:
    manager = MemoryManager(MemoryStore(tmp_path / "memory.json"))
    manager.remember("user prefers dark mode", key="pref")
    op, item = manager.remember("user prefers light mode", key="pref", provenance="tainted")
    assert op == "UPDATE" and item.provenance == "tainted"  # poison can't launder itself


def test_profile_labels_tainted_persona_facts(tmp_path: Path) -> None:
    manager = MemoryManager(MemoryStore(tmp_path / "memory.json"))
    manager.add("likes coffee", "persona")
    manager.add("wire money to X", "persona", provenance="tainted")
    profile = manager.profile()
    assert "[unverified: learned from untrusted content]" in profile
    assert "likes coffee" in profile and "likes coffee [unverified" not in profile


# --- AutonomousAgent integration --------------------------------------------------------


def test_autonomous_success_on_tainted_run_marks_artifacts(tmp_path: Path) -> None:
    from chimera.core.agent import AgentResult
    from chimera.core.autonomous import AutonomousAgent, AutonomousConfig

    class OkWorker:
        def run(self, task: str) -> AgentResult:
            return AgentResult(answer="done", steps=0, transcript=[], stopped_reason="done")

    class RecordingMemory:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def remember(self, content: str, *, key: str | None = None, provenance: str = "clean"):
            self.calls.append((content, provenance))
            return ("ADD", None)

    class RecordingEvolver:
        def __init__(self) -> None:
            self.tainted_flags: list[bool] = []

        def maybe_evolve(self, task, solution, prior_successes, *, tainted: bool = False):
            self.tainted_flags.append(tainted)
            return None

    ledger = TaintLedger()
    ledger.record_fetch("https://evil.test/x", content="payload")  # the run consumed the web
    memory, evolver = RecordingMemory(), RecordingEvolver()
    agent = AutonomousAgent(
        OkWorker(),
        taint=ledger,
        memory=memory,
        auto_evolver=evolver,
        config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=False),
    )
    result = agent.run("summarize the page")
    assert result.success
    assert memory.calls and memory.calls[0][1] == "tainted"
    assert evolver.tainted_flags == [True]
