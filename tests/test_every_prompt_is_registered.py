"""Every prompt the product sends is registered, and its exact text is kept as a snapshot.

Study 25 (`bench/PLAN-study25-system-prompts.md`, wave 0). The registry in :mod:`chimera.prompts` is
only worth having if nothing escapes it. So this file does three things:

1. It scans the package for prompt-shaped module constants and fails on one that is not listed.
2. It resolves every pointer, so a renamed function cannot leave a dead entry behind.
3. It compares every registered text, byte for byte, with `tests/prompt_snapshots/`.

After an intended edit to a prompt, run `python scripts/prompt_snapshots.py --write`. The snapshot
diff is what a reviewer reads.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import get_args

import pytest

from chimera.prompts import NOT_PROMPTS, SECTIONS, SITUATIONS
from chimera.prompts.registry import Layer, Status
from chimera.prompts.snapshots import render_all

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOTS = ROOT / "tests" / "prompt_snapshots"
#: The names a prompt constant has been given in this tree. Wide on purpose: a false match costs one
#: line in NOT_PROMPTS with a reason, a miss costs an unregistered prompt.
PROMPT_NAME = re.compile(
    r"(SYSTEM|PROMPT|NOTE|NUDGE|JUDGE_TEXT|TEMPLATE|INSTRUCTIONS?|REASON|SYSTEM_TEXT|VERBATIM|"
    r"ROLE|RECIPE|DESCRIPTION|ADVISORY|HEADER|FENCE_OPEN|FENCE_CLOSE)$"
)


def _prompt_shaped_constants() -> set[str]:
    found: set[str] = set()
    for path in sorted((ROOT / "chimera").rglob("*.py")):
        module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                target, value = node.target, node.value
            else:
                continue
            if not isinstance(target, ast.Name) or not PROMPT_NAME.search(target.id):
                continue
            is_text = isinstance(value, ast.Constant) and isinstance(value.value, str)
            if is_text or isinstance(value, (ast.BinOp, ast.JoinedStr)):
                found.add(f"{module}:{target.id}")
    return found


def test_ids_are_unique_and_well_formed() -> None:
    ids = [s.id for s in SECTIONS]
    assert len(ids) == len(set(ids)), "duplicate ids in the registry"
    for section_id in ids:
        assert re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z0-9_]+)+", section_id), section_id


def test_every_entry_names_a_real_layer_situation_and_status() -> None:
    for s in SECTIONS:
        assert s.layer in get_args(Layer), s.id
        assert s.status in get_args(Status), s.id
        assert all(code in SITUATIONS for code in s.situations), s.id


def test_a_claim_about_evidence_names_the_evidence() -> None:
    """`measured` and `null` are claims that a run happened, so each one says which run."""
    for s in SECTIONS:
        if s.status != "unmeasured":
            assert s.evidence.strip(), f"{s.id} is {s.status} but names no bench, test or run"


def test_a_null_names_the_model_it_was_null_on() -> None:
    """`null` is a result on one model, and the label has to say which.

    Every null here came from a bench run on a single model (deepseek-v3.2 for `harness_bench`,
    mistral-small-3.2-24b for the learning and retry benches). Harness effects flip sign across
    models (arXiv 2610.00917: one harness change took one model from 57.1 to 30.2 and another from
    49.2 to 60.3), and a planner that did nothing on a strong model added 11.6 points on a weak one
    (arXiv 2609.20804). An unscoped "null" reads as "this prompt does nothing", which is the
    sentence that gets a prompt deleted for a model nobody measured.
    """
    unscoped = [
        s.id for s in SECTIONS
        if s.status == "null" and not re.search(r"\bmeasured on \S+", s.evidence)
    ]
    assert not unscoped, f"null with no model scope ('measured on <model>'): {unscoped}"


def test_a_null_measured_on_two_models_names_both() -> None:
    # The regex above passes on any word after "measured on", so it cannot catch a scope that is
    # too narrow. bench/skillcard ran on two models (mistral-small-3.2-24b, n=12; and
    # deepseek-chat-v3.1, n=24, +12.5 pp, discordant pairs 3-0, CI crossing 0) and read the effect
    # as "never negative ... across two models". A label naming only the first tells a reader the
    # null covers one model.
    results = (Path(__file__).resolve().parents[1] / "bench/skillcard/RESULTS.md").read_text(
        encoding="utf-8"
    )
    assert "deepseek/deepseek-chat-v3.1" in results and "mistral-small-3.2-24b" in results
    cards = next(s for s in SECTIONS if s.id == "loop.cards_instruction")
    assert "mistral-small-3.2-24b" in cards.evidence
    assert "deepseek-chat-v3.1" in cards.evidence


def test_every_pointer_still_resolves() -> None:
    for s in SECTIONS:
        s.resolve()  # raises on a stale module or attribute


def test_no_prompt_shaped_constant_escapes_the_registry() -> None:
    registered = {s.source for s in SECTIONS}
    found = _prompt_shaped_constants()
    missing = sorted(found - registered - set(NOT_PROMPTS))
    assert not missing, (
        "prompt-shaped constants not in chimera/prompts/registry.py (register them, or list them in "
        f"NOT_PROMPTS with the reason no model reads them): {missing}"
    )


def test_the_exclusions_are_real_and_not_also_registered() -> None:
    found = _prompt_shaped_constants()
    registered = {s.source for s in SECTIONS}
    for source, reason in NOT_PROMPTS.items():
        assert source in found, f"{source} is excluded but no longer exists"
        assert source not in registered, f"{source} is both registered and excluded"
        assert reason.strip()


@pytest.mark.parametrize("name", sorted(render_all()))
def test_the_snapshot_holds_the_exact_bytes(name: str) -> None:
    path = SNAPSHOTS / f"{name}.txt"
    assert path.exists(), f"no snapshot for {name}: run python scripts/prompt_snapshots.py --write"
    assert path.read_text(encoding="utf-8") == render_all()[name], (
        f"{name} changed. If that was intended, run python scripts/prompt_snapshots.py --write and "
        "commit the snapshot diff with the change."
    )


def test_no_snapshot_is_left_without_a_prompt() -> None:
    wanted = set(render_all())
    orphans = sorted(p.stem for p in SNAPSHOTS.glob("*.txt") if p.stem not in wanted)
    assert not orphans, f"snapshots with no registered prompt behind them: {orphans}"


def test_the_assembled_loop_prompt_keeps_its_order() -> None:
    """The order is what the plan changes, so it is asserted in words as well as in bytes: the base
    comes first, then the task-list sentence, then the project's conventions, then the owner, who is
    read last (plan §5.1, rule 4)."""
    from chimera.core.agent import DEFAULT_SYSTEM_PROMPT, TODO_PROMPT

    text = render_all()["assembled.loop_project_owner_todo"]
    positions = [
        text.index(DEFAULT_SYSTEM_PROMPT),
        text.index(TODO_PROMPT),
        text.index("Project instructions"),
        text.index("Instructions from the person who runs you"),
    ]
    assert positions == sorted(positions)
