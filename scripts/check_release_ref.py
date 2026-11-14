"""Refuse a PyPI publish that did not start where a release should start.

`publish.yml` mints an OIDC token that PyPI trusts for this project, so WHICH ref and WHICH version
reach that step is the whole security question. Two ways to get it wrong used to be silent:

* `workflow_dispatch` runs from whatever branch the dispatcher picks. A feature branch, a fork's
  branch pushed to this repo, an old commit: the token was minted for all of them and the wheel built
  from them went to PyPI under the project's name.
* A release tag can point at a commit that was never on `main` (a feature branch, a fork's branch
  pushed here). A tag that matches the version string says nothing about WHERE the commit lives, so
  the commit must also be an ancestor of `refs/remotes/origin/main`.
* A release tagged `v0.90.0` over a tree whose `pyproject.toml` says `0.89.0` publishes 0.89.0 again
  (PyPI refuses the duplicate, at best) or an unreleased 0.90.0 under the wrong tag (at worst). Nothing
  compared the tag to the version the wheel carries.

Two modes, one failure convention (exit 1 with the reason on stderr):

    python scripts/check_release_ref.py ref            # before any job runs
    python scripts/check_release_ref.py dist dist/     # after `uv build`, on the files to upload

This is a second line, not the first: the script is read from the same ref as the workflow that runs
it, so whoever can push that ref can edit both. What holds against that is outside the repository —
the `pypi` environment's deployment-branch rule and a tag ruleset on `v*`.

Everything comes from the environment (`GITHUB_EVENT_NAME`, `GITHUB_REF`, `RELEASE_TAG`), never from
an interpolated `${{ }}` in the shell line: a release tag is attacker-chosen text.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN_REF = "refs/heads/main"


def project_version(pyproject: Path | None = None) -> str:
    data = tomllib.loads((pyproject or ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def check_ref(env: Mapping[str, str], version: str) -> list[str]:
    """Reasons this run must not publish. Empty means it may."""
    event = env.get("GITHUB_EVENT_NAME", "")
    ref = env.get("GITHUB_REF", "")
    if event == "workflow_dispatch":
        if ref != MAIN_REF:
            return [f"a manual publish must run from {MAIN_REF}, not {ref or '(no ref)'}"]
        return []
    if event == "release":
        tag = env.get("RELEASE_TAG", "")
        if not tag or ref != f"refs/tags/{tag}":
            return [f"the release tag {tag!r} does not match the ref {ref!r}"]
        if tag.removeprefix("v") != version:
            return [f"tag {tag} is not v{version}, the version in pyproject.toml"]
        return []
    return [f"publishing is not allowed on a {event or 'unknown'} event"]


#: Fully qualified on purpose. `origin/main` is ambiguous to git: a TAG with that name beats
#: `refs/remotes/origin/main` in its resolution order, so a tag pushed on a feature commit would have
#: been "main" for this check.
MAIN_TRACKING_REF = "refs/remotes/origin/main"
_SHA = re.compile(r"^[0-9a-f]{40}$")


def check_ancestry(sha: str, repo: Path | None = None, main_ref: str = MAIN_TRACKING_REF) -> list[str]:
    """The commit being published must already be on main. Fails closed: a missing or malformed sha,
    a missing `origin/main` (a shallow checkout) and a non-ancestor all refuse, each with its reason."""
    if not _SHA.fullmatch(sha or ""):
        return [f"GITHUB_SHA {sha!r} is not a full commit id, so it cannot be placed on main"]
    cwd = str(repo or ROOT)

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)

    # Both sides are resolved to commit ids first, then compared as ids: `merge-base` has no
    # `--end-of-options`, and an id cannot be mistaken for a name or an option.
    main = git("rev-parse", "--verify", "--quiet", "--end-of-options", f"{main_ref}^{{commit}}")
    if main.returncode != 0:
        return [f"{main_ref} is not available; the checkout needs fetch-depth: 0"]
    commit = git("rev-parse", "--verify", "--quiet", "--end-of-options", f"{sha}^{{commit}}")
    if commit.returncode != 0:
        return [f"commit {sha} is not in this checkout"]
    if git("merge-base", "--is-ancestor", commit.stdout.strip(), main.stdout.strip()).returncode != 0:
        return [f"commit {sha} is not on {main_ref}; publish only what main already contains"]
    return []


# `chimera_agent-0.89.0-py3-none-any.whl` and `chimera_agent-0.89.0.tar.gz`
_DIST_NAME = re.compile(r"^chimera_agent-(?P<v>.+?)(?:-[^-]+-[^-]+-[^-]+\.whl|\.tar\.gz)$")


def check_dist(files: list[Path], version: str) -> list[str]:
    """Every file about to be uploaded must carry the version in pyproject.toml.

    Zero files is a failure, not a pass: a glob that matched nothing is how a check ends up
    verifying an empty set.
    """
    if not files:
        return ["no distribution files to check"]
    problems: list[str] = []
    for f in files:
        m = _DIST_NAME.match(f.name)
        if m is None:
            problems.append(f"{f.name} is not a chimera_agent distribution")
        elif m["v"] != version:
            problems.append(f"{f.name} carries {m['v']}, pyproject.toml says {version}")
    return problems


def main(argv: list[str]) -> int:
    version = project_version()
    if argv[:1] == ["ref"]:
        problems = check_ref(os.environ, version) or check_ancestry(os.environ.get("GITHUB_SHA", ""))
    elif argv[:1] == ["dist"] and len(argv) == 2:
        problems = check_dist(sorted(p for p in Path(argv[1]).iterdir() if p.is_file()), version)
    else:
        print(__doc__, file=sys.stderr)
        return 2
    for p in problems:
        print(f"refusing to publish: {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
