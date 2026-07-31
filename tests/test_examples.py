"""Every worked example under `examples/` loads and refers only to capabilities that exist.

`examples/` is an *open* contribution area: a recipe is a YAML file, it touches nothing central, and
anyone can send one. What was missing is the half that makes that cheap for the maintainer — until
now nothing checked a contributed recipe, so reviewing one meant reading YAML against the executor
table by eye and hoping.

The most valuable assertion here is the `uses:` check. A recipe that invents a verb parses perfectly,
looks entirely reasonable in review, and fails only when someone runs it — quite possibly a newcomer
following the docs, which is the worst audience to discover it. The verb list comes from
`build_executors` itself rather than a copy, so adding an executor cannot leave this test behind.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
YAMLS = sorted(p for p in EXAMPLES.rglob("*.yaml") if p.is_file())
_IDS = [str(p.relative_to(EXAMPLES)).replace("\\", "/") for p in YAMLS]


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _verbs() -> set[str]:
    """The executor verbs, read off the real builder — never a hand-kept copy."""
    from chimera.workflow.executors import build_executors

    return set(build_executors(workspace=ROOT).keys())


def test_examples_were_found() -> None:
    assert YAMLS, "no example YAML found — examples/ is advertised as a contribution surface"


@pytest.mark.parametrize("path", YAMLS, ids=_IDS)
def test_is_parseable_yaml_with_a_name(path: Path) -> None:
    data = _load(path)
    assert isinstance(data, dict), f"{path.name}: top level should be a mapping"
    assert str(data.get("name", "")).strip(), f"{path.name}: needs a name"


@pytest.mark.parametrize("path", YAMLS, ids=_IDS)
def test_validates_against_its_model(path: Path) -> None:
    """An example is one of three shapes, and each is validated by the loader that will run it.

    Going through the real loaders rather than re-describing the schema here means "it passed the
    test" and "it will load at runtime" are the same statement — and that a change to a model cannot
    leave this test asserting against a shape the code no longer accepts.
    """
    data = _load(path)
    if "steps" in data:  # a designed loop, run by `chimera workflow`
        from chimera.workflow import load_workflow

        assert load_workflow(path).steps, f"{path.name}: a workflow with no steps does nothing"
    elif "requirements" in data:  # a drift spec, checked by `chimera drift`
        from chimera.governance import load_spec

        assert load_spec(path).requirements, f"{path.name}: a spec with no requirements checks nothing"
    elif "topics" in data:  # a brief recipe, run by `chimera brief`
        from chimera.orchestration.brief import load_brief

        assert load_brief(path).topics, f"{path.name}: a brief with no topics has nothing to do"
    else:
        pytest.fail(
            f"{path.name}: not a workflow (steps:), a spec (requirements:) or a brief (topics:). "
            f"If this is a new kind of recipe, teach this test about it."
        )


@pytest.mark.parametrize("path", YAMLS, ids=_IDS)
def test_every_step_uses_a_verb_that_exists(path: Path) -> None:
    data = _load(path)
    if "steps" not in data:
        pytest.skip("not a workflow")
    known = _verbs()
    unknown = [
        s.get("uses") for s in data["steps"] if isinstance(s, dict) and s.get("uses") not in known
    ]
    assert not unknown, (
        f"{path.name} uses verbs that no executor provides: {unknown}. Available: {sorted(known)}"
    )


@pytest.mark.parametrize("path", YAMLS, ids=_IDS)
def test_every_step_is_named(path: Path) -> None:
    """Step names are what the runner reports progress and failures against.

    An unnamed step turns a failure report into "step 3 of 5 failed", which is exactly the moment
    someone needs the name most.
    """
    data = _load(path)
    if "steps" not in data:
        pytest.skip("not a workflow")
    unnamed = [i for i, s in enumerate(data["steps"]) if not str((s or {}).get("name", "")).strip()]
    assert not unnamed, f"{path.name}: steps at {unnamed} have no name"


def test_each_example_directory_explains_itself() -> None:
    """A recipe with no prose is a puzzle. Anyone can read YAML; far fewer can guess the intent.

    Scoped to directories, since the two loose files at the top of `examples/` are covered by
    `examples/README.md` and both carry a header comment.
    """
    missing = [
        d.name
        for d in sorted(EXAMPLES.iterdir())
        if d.is_dir() and any(d.glob("*.yaml")) and not (d / "README.md").exists()
    ]
    assert not missing, f"these example directories have no README.md: {missing}"
