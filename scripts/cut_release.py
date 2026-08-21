"""Cut a release: bump the version everywhere, open the pull request, and stop there.

`main` is protected, so a release can no longer be a direct push — which is an improvement rather
than an obstacle, because the version bump now passes CI *before* it lands instead of after. The
extra steps are the reason this exists: nobody should have to remember six files and two different
version formats to ship a release candidate.

    python scripts/cut_release.py 0.48.0rc10
    python scripts/cut_release.py 0.48.0rc10 --merge     # wait for CI, then merge

What it does NOT do is publish. Merging the PR changes numbers in a repository; creating the
GitHub Release is what pushes a wheel to PyPI and builds installers, and that stays a separate,
deliberate act by a person.

Run from the repository root, in an environment where `chimera` is installed — the snapshots below
read the version from the *package metadata*, not from `pyproject.toml`, which is the trap this
script is most careful about.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PYPROJECT = ROOT / "pyproject.toml"
CARGO = ROOT / "apps" / "desktop" / "src-tauri" / "Cargo.toml"
TAURI = ROOT / "apps" / "desktop" / "src-tauri" / "tauri.conf.json"

#: Generated, not edited. Each embeds the version it was produced for, so a release regenerates
#: them rather than substituting a string — a hand-edited "generated" file is correct exactly once.
SNAPSHOTS: dict[Path, str] = {
    ROOT / "chimera" / "_cli_snapshot.json": "chimera.cli.schema_dump",
    ROOT / "chimera" / "_benchmark_snapshot.json": "chimera.eval.benchmark_snapshot",
    ROOT / "chimera" / "_maturity_snapshot.json": "chimera.eval.maturity_snapshot",
}

#: The one that writes to stdout instead of to its own file.
_STDOUT_SNAPSHOT = ROOT / "chimera" / "_cli_snapshot.json"

_PEP440 = re.compile(r"^(\d+\.\d+\.\d+)(?:(a|b|rc)(\d+))?$")
_PRE_NAMES = {"a": "alpha", "b": "beta", "rc": "rc"}


class Stop(SystemExit):
    """A refusal written to be read by the person who ran this."""

    def __init__(self, message: str) -> None:
        super().__init__(f"\n  {message}\n")


def run(*args: str, capture: bool = True) -> str:
    result = subprocess.run(
        args, cwd=ROOT, capture_output=capture, text=True, check=False, encoding="utf-8"
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise Stop(f"`{' '.join(args)}` failed:\n  {detail}")
    return (result.stdout or "").strip()


def semver(version: str) -> str:
    """The Tauri/Cargo spelling of a PEP 440 version.

    `0.48.0rc9` and `0.48.0-rc.9` are the same release written two ways, and keeping them in step
    by hand across two files is exactly the kind of transcription that is right until it is not.
    """
    match = _PEP440.match(version)
    if match is None:
        raise Stop(
            f"{version!r} is not a version this script can convert. Expected e.g. 0.48.0 or "
            "0.48.0rc10 — post and dev releases have no clean SemVer spelling, so they are refused "
            "rather than guessed at."
        )
    base, kind, number = match.groups()
    return base if kind is None else f"{base}-{_PRE_NAMES[kind]}.{number}"


def current_version() -> str:
    for line in PYPROJECT.read_text(encoding="utf-8").splitlines():
        if line.startswith("version = "):
            return line.split("=", 1)[1].strip().strip('"')
    raise Stop("no `version = ` line in pyproject.toml")


def installed_version() -> str:
    """What the package metadata says — which is what the snapshots will stamp."""
    return run(sys.executable, "-c", "import chimera; print(chimera.__version__)")


def check_clean_and_on_main() -> None:
    # TRACKED changes only. Untracked files are not part of any commit — the release adds its six
    # files by name — and every real checkout has some: scratch scripts, bench output, a venv. The
    # first run of this script on a real machine was refused by its own guard for exactly that.
    if run("git", "status", "--porcelain", "--untracked-files=no"):
        raise Stop("the working tree has uncommitted changes. Commit or stash them first.")
    branch = run("git", "rev-parse", "--abbrev-ref", "HEAD")
    if branch != "main":
        raise Stop(f"on branch {branch!r}. Cut releases from main.")
    run("git", "fetch", "origin", "main")
    behind = run("git", "rev-list", "--count", "HEAD..origin/main")
    if behind != "0":
        raise Stop(f"main is {behind} commit(s) behind origin. Pull first.")


def write_versions(version: str) -> None:
    tauri_version = semver(version)

    text = PYPROJECT.read_text(encoding="utf-8")
    # Anchored to the line, not to the string: the version number appears in dependency pins too,
    # and a blanket replace would quietly rewrite one of those.
    text, count = re.subn(r'^version = ".*"$', f'version = "{version}"', text, count=1, flags=re.M)
    if count != 1:
        raise Stop("could not find the version line in pyproject.toml")
    PYPROJECT.write_text(text, encoding="utf-8")

    text = CARGO.read_text(encoding="utf-8")
    text, count = re.subn(
        r'^version = ".*"$', f'version = "{tauri_version}"', text, count=1, flags=re.M
    )
    if count != 1:
        raise Stop("could not find the version line in Cargo.toml")
    CARGO.write_text(text, encoding="utf-8")

    # Substituted on the line, not parsed and re-dumped. `json.dumps` normalises formatting it
    # was never asked to touch: the first real cut with this expanded a compact
    # `"targets": ["nsis", "dmg", ...]` onto five lines, so a commit that promises to change one
    # version number arrived with eleven lines of noise around it.
    text = TAURI.read_text(encoding="utf-8")
    text, count = re.subn(
        r'^(\s*"version"\s*:\s*)"[^"]*"',
        rf'\g<1>"{tauri_version}"',
        text,
        count=1,
        flags=re.M,
    )
    if count != 1:
        raise Stop("could not find the version line in tauri.conf.json")
    TAURI.write_text(text, encoding="utf-8")


def refresh_install() -> None:
    """Rebuild the editable install's metadata so it reports the version just written.

    Done here rather than demanded of the caller, because demanding it is impossible: the metadata
    can only be rebuilt from a pyproject that already says the new version, and a script that
    stopped at this point would have reverted that file on its way out. "Reinstall, then run
    again" described a loop with no entrance.

    `--no-deps` keeps it to what is actually stale — the dist-info of the install this script is
    already writing into. Dependencies are not part of a version bump, and resolving them here
    would turn a thirty-second release into a several-minute one.
    """
    print("  refreshing the editable install so the snapshots stamp the new version...",
          file=sys.stderr)
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", ".", "--no-deps", "-q"],
        cwd=ROOT,
        check=False,
    )


def regenerate_snapshots(version: str) -> None:
    """Regenerate the three generated files, and refuse if they came out stamped with the old version.

    They read ``chimera.__version__``, which comes from the INSTALLED package metadata rather than
    from ``pyproject.toml``. So bumping the file and regenerating in an environment that still has
    the previous version installed produces snapshots stamped with the version before this one —
    a release commit that looks complete, passes locally, and is wrong in three files.
    """
    if installed_version() != version:
        refresh_install()
    stale = installed_version()
    if stale != version:
        raise Stop(
            f"the installed package still reports {stale}, so the snapshots would be stamped "
            f"with it instead of {version}, and this could not refresh it. Do it by hand and "
            f"run again:\n    pip install -e . --no-deps          (or: uv sync)"
        )

    for target, module in SNAPSHOTS.items():
        if target == _STDOUT_SNAPSHOT:
            target.write_text(run(sys.executable, "-m", module) + "\n", encoding="utf-8")
        else:
            run(sys.executable, "-m", module)

    wrong = {
        target.name: json.loads(target.read_text(encoding="utf-8")).get("generated_for")
        for target in SNAPSHOTS
        if json.loads(target.read_text(encoding="utf-8")).get("generated_for") != version
    }
    if wrong:
        raise Stop(f"these snapshots came out stamped for another version: {wrong}")


def check_only_expected_files_changed(version: str) -> list[str]:
    changed = sorted(run("git", "diff", "--name-only").splitlines())
    expected = sorted(
        str(path.relative_to(ROOT)).replace("\\", "/")
        for path in [PYPROJECT, CARGO, TAURI, *SNAPSHOTS]
    )
    if changed != expected:
        raise Stop(
            "a release should touch exactly the six version files. This run changed:\n    "
            + "\n    ".join(changed or ["(nothing — is that already the version?)"])
            + "\n  expected:\n    "
            + "\n    ".join(expected)
        )
    return changed


def open_pull_request(version: str, changed: list[str]) -> str:
    branch = f"release/{version}"
    run("git", "checkout", "-b", branch)
    run("git", "add", *changed)
    run("git", "commit", "-m", f"chore(release): {version}")
    run("git", "push", "-u", "origin", branch)

    body = (
        f"Version bump to `{version}` (`{semver(version)}` for the desktop shell).\n\n"
        "Cut by `scripts/cut_release.py`: the three snapshots are regenerated rather than "
        "string-replaced, and the script refuses to continue if they come out stamped for a "
        "different version than the one being cut.\n\n"
        "Merging this changes numbers in the repository. Publishing is a separate act: create the "
        "GitHub Release afterwards, which is what triggers the wheel and the installers.\n"
    )
    url = run("gh", "pr", "create", "--title", f"chore(release): {version}", "--body", body)
    return url.splitlines()[-1] if url else ""


def wait_and_merge(version: str) -> None:
    print("  waiting for CI…", file=sys.stderr)
    subprocess.run(["gh", "pr", "checks", "--watch", "--interval", "30"], cwd=ROOT, check=False)
    # Read the verdict rather than trusting the watch's exit code: `gh pr checks` exits non-zero
    # for a pending run too, and a release that merges on a misread is the one thing worse than a
    # release that waits.
    failed = run("gh", "pr", "checks", "--json", "state,name", "--jq",
                 '[.[] | select(.state == "FAILURE" or .state == "ERROR") | .name] | join(", ")')
    if failed:
        raise Stop(f"CI is red: {failed}. Not merging.")
    run("gh", "pr", "merge", "--squash", "--delete-branch")
    print(f"  merged {version}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="the new version, PEP 440 (e.g. 0.48.0rc10)")
    parser.add_argument(
        "--merge", action="store_true", help="wait for CI and merge the PR when it is green"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="do the bump and the checks, print what would be committed, then put the tree back",
    )
    args = parser.parse_args()

    version = args.version.strip()
    semver(version)  # validate before touching anything
    check_clean_and_on_main()

    previous = current_version()
    if version == previous:
        raise Stop(f"{version} is already the current version.")
    print(f"  {previous} → {version}  ({semver(version)} for the desktop shell)", file=sys.stderr)

    write_versions(version)
    try:
        regenerate_snapshots(version)
        changed = check_only_expected_files_changed(version)
    except SystemExit:
        # Leave the tree as it was: a half-applied bump is a trap for whoever runs `git status`
        # next and sees three of six files changed.
        run("git", "checkout", "--", ".")
        raise

    if args.dry_run:
        # Everything above is the part that can be wrong. Stopping here exercises it for real —
        # the rewrite, the regeneration, the version-stamp check and the six-file check — without
        # putting a branch on the remote to prove it.
        print("  would commit:\n    " + "\n    ".join(changed), file=sys.stderr)
        run("git", "checkout", "--", ".")
        # The install too, not just the tree. Verifying the snapshot stamping means really
        # refreshing the metadata, and a dry run that left the environment reporting a version
        # the repository does not have is not dry — it made the next `pytest` fail on a
        # snapshot that was perfectly correct.
        refresh_install()
        print("  dry run - tree and install restored, nothing pushed", file=sys.stderr)
        return

    url = open_pull_request(version, changed)
    print(f"  opened {url}", file=sys.stderr)
    if args.merge:
        wait_and_merge(version)
    else:
        print("  merge it when CI is green, then create the GitHub Release to publish.", file=sys.stderr)


if __name__ == "__main__":
    main()
