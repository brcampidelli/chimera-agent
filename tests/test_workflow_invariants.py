"""The CI configuration's own invariants, asserted instead of remembered.

Everything here was already true when this file was written. That is the point: these are properties
whose *absence* is silent. A workflow with no `permissions:` block looks exactly like one with the
right permissions until the day the repository default is widened in a web UI nobody diffs. A
`pull_request_target` trigger looks like a one-word change and hands a fork's code the base
repository's token. A `@v4` tag looks pinned and is not.

None of these are hypothetical classes of bug — they are the standard way a public repository that
accepts outside pull requests gets compromised. They cost nothing to hold and everything to rebuild
after the fact, which is exactly the shape of thing a test should carry rather than a maintainer's
attention.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WORKFLOWS = sorted((Path(__file__).resolve().parent.parent / ".github" / "workflows").glob("*.yml"))

_IDS = [w.name for w in WORKFLOWS]

#: `uses: owner/repo@ref` — captures the ref so we can insist it is a commit, not a tag.
_USES = re.compile(r"^\s*-?\s*uses:\s*([^\s@]+)@([^\s#]+)", re.M)
_SHA = re.compile(r"^[0-9a-f]{40}$")

#: Actions whose published entrypoint is a branch or moving ref rather than a versioned tag. Pinning
#: these to a SHA is still possible but breaks the upstream's own update story, and both are used
#: only in release workflows that a pull request cannot reach.
_REF_EXEMPT = {"dtolnay/rust-toolchain", "pypa/gh-action-pypi-publish"}


def test_workflows_were_found() -> None:
    """A glob that silently matched nothing would make every test below vacuously true."""
    assert len(WORKFLOWS) >= 4, f"expected the workflow files, found {_IDS}"


@pytest.mark.parametrize("wf", WORKFLOWS, ids=_IDS)
def test_no_pull_request_target(wf: Path) -> None:
    """The trigger that runs fork code with the base repo's token and secrets.

    There is a legitimate use for it (labelling, size checks) and no legitimate use that also checks
    out the PR's code — which is the combination that leaks. Rather than trying to test for the safe
    subset, refuse the trigger: this project has no need for it, and "we needed it for X" is a
    conversation to have in a PR review, not a default.
    """
    # Match the trigger key, not the word: the workflows document *why* this trigger is refused, and
    # a substring check would flag its own explanation. (It did, the first time this test ran.)
    triggers = [ln for ln in wf.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#")]
    offending = [ln for ln in triggers if re.match(r"\s*pull_request_target\s*:", ln)]
    assert not offending, f"{wf.name} uses pull_request_target — see the hard rule in AGENTS.md"


@pytest.mark.parametrize("wf", WORKFLOWS, ids=_IDS)
def test_declares_permissions(wf: Path) -> None:
    """Without an explicit block the token carries the repository default, set outside the repo."""
    assert re.search(r"^permissions:", wf.read_text(encoding="utf-8"), re.M), (
        f"{wf.name} has no top-level permissions: block — declare least privilege explicitly"
    )


@pytest.mark.parametrize("wf", WORKFLOWS, ids=_IDS)
def test_actions_are_pinned_to_a_commit(wf: Path) -> None:
    floating = [
        f"{owner}@{ref}"
        for owner, ref in _USES.findall(wf.read_text(encoding="utf-8"))
        if owner not in _REF_EXEMPT and not _SHA.match(ref)
    ]
    assert not floating, (
        f"{wf.name} uses mutable tags: {floating}. Pin to the commit SHA with the version in a "
        f"trailing comment; dependabot still proposes the bumps."
    )


@pytest.mark.parametrize("wf", WORKFLOWS, ids=_IDS)
def test_every_job_bounds_its_runtime(wf: Path) -> None:
    """A job with no `timeout-minutes` inherits a six-hour default.

    Counting `runs-on` rather than parsing YAML keeps this dependency-free and is exact here: every
    job declares exactly one, and a reusable-workflow call (which has none) would not match.
    """
    text = wf.read_text(encoding="utf-8")
    jobs, bounded = len(re.findall(r"^\s+runs-on:", text, re.M)), len(
        re.findall(r"^\s+timeout-minutes:", text, re.M)
    )
    assert bounded >= jobs, f"{wf.name}: {jobs} jobs but only {bounded} declare timeout-minutes"


def test_no_secret_is_reachable_from_a_pull_request() -> None:
    """A job reading `secrets.` must be unreachable from `pull_request`.

    Parsed structurally rather than by eyeball: find the jobs that reference a secret, and require
    each to carry an `if:` that pins it to a push, a schedule or a manual dispatch. `GITHUB_TOKEN` is
    excluded — it is minted per run and is already read-only for a fork PR.
    """
    offenders: list[str] = []
    for wf in WORKFLOWS:
        text = wf.read_text(encoding="utf-8")
        if not re.search(r"^on:\n(?:.*\n)*?\s*pull_request:", text, re.M):
            continue  # a workflow a PR cannot trigger at all
        # Split into jobs on the two-space-indented job keys, then inspect each block.
        blocks = re.split(r"\n  (?=[A-Za-z0-9_-]+:\n)", text.split("\njobs:\n", 1)[-1])
        for block in blocks:
            uses_secret = re.search(r"secrets\.(?!GITHUB_TOKEN)", block)
            gated = re.search(r"^\s+if:.*(github\.event_name|github\.ref)", block, re.M)
            if uses_secret and not gated:
                offenders.append(f"{wf.name}:{block.splitlines()[0].strip()}")
    assert not offenders, (
        f"these jobs read a repository secret and are reachable from a pull request: {offenders}"
    )
