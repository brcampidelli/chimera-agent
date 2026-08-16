"""The skill a run just learned, erased by the run that learned it.

`SkillStore` had the atomic write and not the re-read, and those cover different failures.
Atomicity means a crash cannot truncate `skills.json`. It says nothing about a second holder of the
same file publishing a snapshot taken before your change — and there is always a second holder:
`evolution/context.py` builds one instance for the evolver (:128) and one for the card retriever
(:158), over the same path, in the same process.

    autonomous.py:1018   evolver copy    -> add(new skill)        skills.json = [old, NEW]
    autonomous.py:1095   retriever copy  -> record_use(old)       skills.json = [old]

No exception, no log line, no truncated file. The library is intact and the newest thing in it is
gone.

Why this matters beyond correctness: that configuration — evolution on AND cards on — is what
`bench/learning_lift` has measured since run 3, the one that added `--skill-cards` to the learning
arm. So it is a mechanism that produces the null those runs found. It is **not** proof that it
explains the null: the suites' 84-92% ceiling is a documented rival explanation and a ceiling alone
produces a null. What these tests establish is that the mechanism is real, not that the mystery is
solved.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

from chimera.evolution.learned_skill import LearnedSkill
from chimera.evolution.skill_store import SkillStore


def _skill(name: str) -> LearnedSkill:
    return LearnedSkill(name=name, description=f"what {name} knows")


def _names(path: Path) -> set[str]:
    return {s.name for s in SkillStore(path).skills()}


# --- the bug, in the shape the product actually has it --------------------------------------------


def test_crediting_a_card_does_not_erase_the_skill_just_learned(tmp_path: Path) -> None:
    """The whole defect in six lines, with the two instances `context.py` really builds."""
    path = tmp_path / "skills.json"
    SkillStore(path).add(_skill("older"))

    evolver_copy = SkillStore(path)  # context.py:128
    retriever_copy = SkillStore(path)  # context.py:158

    evolver_copy.add(_skill("just_learned"))  # autonomous.py:1018
    retriever_copy.record_use("older", success=True)  # autonomous.py:1095

    assert _names(path) == {"older", "just_learned"}, "the run erased what it learned"


def test_the_telemetry_it_was_writing_still_lands(tmp_path: Path) -> None:
    """The fix must not trade one silent loss for another: the use count is the input to retirement,
    so dropping it would make `retirement_candidates` judge on a record that never accumulated."""
    path = tmp_path / "skills.json"
    SkillStore(path).add(_skill("older"))
    evolver_copy, retriever_copy = SkillStore(path), SkillStore(path)

    evolver_copy.add(_skill("just_learned"))
    retriever_copy.record_use("older", success=True)

    stats = {row["name"]: row for row in SkillStore(path).stats()}
    assert stats["older"]["uses"] == 1
    assert stats["older"]["successes"] == 1


def test_a_refine_does_not_lose_a_sibling_skill(tmp_path: Path) -> None:
    """`add` on an existing name is how a skill is refined. Two holders refining different skills
    must both survive — the counters-preserving branch in `add` reads the dict it was handed, so it
    has to be handed a fresh one."""
    path = tmp_path / "skills.json"
    seed = SkillStore(path)
    seed.add(_skill("alpha"))
    seed.add(_skill("beta"))

    first, second = SkillStore(path), SkillStore(path)
    first.record_use("alpha", success=True)
    second.add(_skill("beta"))  # refine beta from a snapshot taken before alpha's use

    stats = {row["name"]: row for row in SkillStore(path).stats()}
    assert set(stats) == {"alpha", "beta"}
    assert stats["alpha"]["uses"] == 1, "the refine rolled back alpha's telemetry"


# --- the lifecycle commands, which are the cross-process case -------------------------------------


def test_approving_a_skill_does_not_roll_back_the_library(tmp_path: Path) -> None:
    """`skills-approve` is a separate process from `serve`. Pre-fix it republished whatever the
    library held when the command started."""
    path = tmp_path / "skills.json"
    SkillStore(path).add(_skill("pending_one"))

    reviewer = SkillStore(path)  # the CLI command, snapshot taken at startup
    SkillStore(path).add(_skill("minted_meanwhile"))  # the daemon, mid-review

    assert reviewer.approve("pending_one") is True

    assert _names(path) == {"pending_one", "minted_meanwhile"}


def test_retiring_and_promoting_are_the_same_shape(tmp_path: Path) -> None:
    path = tmp_path / "skills.json"
    SkillStore(path).add(_skill("a"))
    SkillStore(path).add(_skill("b"))

    retirer, promoter = SkillStore(path), SkillStore(path)
    SkillStore(path).add(_skill("c"))  # a third party mints while both hold snapshots
    retirer.retire("a")
    promoter.promote("b")

    assert _names(path) == {"a", "b", "c"}


def test_an_unknown_name_is_still_reported_as_unknown(tmp_path: Path) -> None:
    """The re-read must not turn "not found" into a silent success — the CLI prints that verdict."""
    path = tmp_path / "skills.json"
    SkillStore(path).add(_skill("real"))

    store = SkillStore(path)

    assert store.approve("does_not_exist") is False
    assert store.retire("does_not_exist") is False
    assert store.promote("does_not_exist") is False


def test_a_name_minted_after_this_object_was_built_is_found(tmp_path: Path) -> None:
    """The mirror, and the reason the presence check moved inside the lock: asking the snapshot
    whether a skill exists is how `skills-approve` calls something unknown a second after another
    process minted it."""
    path = tmp_path / "skills.json"
    SkillStore(path).add(_skill("first"))
    reviewer = SkillStore(path)

    SkillStore(path).add(_skill("minted_after"))

    assert reviewer.approve("minted_after") is True, "the stale snapshot decided it did not exist"


# --- two real processes ---------------------------------------------------------------------------


_WRITER = """
import sys
from pathlib import Path
from chimera.evolution.learned_skill import LearnedSkill
from chimera.evolution.skill_store import SkillStore

path, tag = Path(sys.argv[1]), sys.argv[2]
store = SkillStore(path)
for i in range(25):
    store.add(LearnedSkill(name=f"{tag}{i}", description="x"))
"""


def test_two_processes_learning_at_once_keep_every_skill(tmp_path: Path) -> None:
    """`serve` mints skills while an operator runs `chimera` over SSH. The in-process tests prove
    the re-read; only this proves the file lock."""
    path = tmp_path / "skills.json"
    script = tmp_path / "writer.py"
    script.write_text(textwrap.dedent(_WRITER), encoding="utf-8")

    procs = [
        subprocess.Popen([sys.executable, str(script), str(path), tag], cwd=str(Path.cwd()))
        for tag in ("A", "B")
    ]
    for proc in procs:
        assert proc.wait(timeout=180) == 0

    assert len(_names(path)) == 50
