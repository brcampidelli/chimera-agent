"""Cut a release: bump the version everywhere, open the pull request, and stop there.

`main` is protected, so a release can no longer be a direct push — which is an improvement rather
than an obstacle, because the version bump now passes CI *before* it lands instead of after. The
extra steps are the reason this exists: nobody should have to remember eight files and two different
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
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PYPROJECT = ROOT / "pyproject.toml"
CARGO = ROOT / "apps" / "desktop" / "src-tauri" / "Cargo.toml"
TAURI = ROOT / "apps" / "desktop" / "src-tauri" / "tauri.conf.json"
#: Not a version file by appearance, and one by behaviour: it records this package's own
#: version, so a release that skips it leaves the repository contradicting itself.
LOCK = ROOT / "uv.lock"
CARGO_LOCK = ROOT / "apps" / "desktop" / "src-tauri" / "Cargo.lock"

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


def run(*args: str, capture: bool = True, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        args,
        cwd=ROOT,
        capture_output=capture,
        text=True,
        check=False,
        encoding="utf-8",
        # Not decoration. Every command here used to be git, whose output is ASCII; `uv lock` is the
        # first that is not, and on a Windows console it emits cp1252 bytes. Without this the reader
        # thread dies on a byte 0x97, prints a traceback nobody can act on, and — worse — leaves
        # `stderr` empty, so a command that FAILED would be reported with no reason attached.
        errors="replace",
        env={**os.environ, **env} if env else None,
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
    # TRACKED changes only. Untracked files are not part of any commit — the release adds its eight
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

    # And the Cargo lock, which records this package's own version alongside its dependencies'.
    # The same omission `refresh_lock` documents for `uv.lock`, found the same way: it said 0.46.0
    # while Cargo.toml said 0.48.0-rc.33, two releases apart. Nothing broke, because cargo rewrites
    # it on the next build — which is why nobody looked, and why every desktop build since carried
    # an unexplained one-line diff that belonged to a release nobody was cutting at the time.
    #
    # Edited textually rather than by running `cargo update`: a release cut must not require a Rust
    # toolchain on the machine cutting it, and the target is one line inside this package's own
    # block. The name is part of the pattern for that reason — every one of the file's dependencies
    # has a `version` line of its own, and an anchor on the line alone would rewrite the first.
    text = CARGO_LOCK.read_text(encoding="utf-8")
    text, count = re.subn(
        r'(name = "chimera-desktop"\nversion = )".*"',
        lambda m: m.group(1) + f'"{tauri_version}"',
        text,
        count=1,
    )
    if count != 1:
        raise Stop("could not find chimera-desktop's own version line in Cargo.lock")
    CARGO_LOCK.write_text(text, encoding="utf-8")

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


def refresh_lock(version: str) -> None:
    """Re-resolve ``uv.lock`` so it names the version just written.

    Left out of the original six, and it drifted five releases before anyone looked: the lock said
    0.43.0 while ``pyproject.toml`` said 0.48.0rc30. Nothing broke, which is exactly why nobody
    looked — the release workflow runs a bare ``uv sync``, which re-locks silently. But "re-locks
    silently" is a property of today's workflow rather than a guarantee, and a repository whose
    lock disagrees with its own manifest fails ``uv sync --locked`` for everyone who uses it.

    Conservative by construction: ``uv lock`` keeps every pin that still satisfies its constraint,
    so this rewrites the two lines describing THIS package and nothing else. It is not a dependency
    upgrade wearing a release's clothes — which is the reason it can be part of a release at all.
    """
    print("  re-resolving uv.lock so it names the new version...", file=sys.stderr)
    run("uv", "lock")
    if 'version = "' + version + '"' not in LOCK.read_text(encoding="utf-8"):
        raise Stop(
            "`uv lock` ran but " + LOCK.name + " still does not name " + version + ". Cutting the "
            "release would leave the lock disagreeing with pyproject.toml, which is the drift this "
            "exists to end."
        )


def check_only_expected_files_changed(version: str) -> list[str]:
    changed = sorted(run("git", "diff", "--name-only").splitlines())
    expected = sorted(
        str(path.relative_to(ROOT)).replace("\\", "/")
        for path in [PYPROJECT, CARGO, CARGO_LOCK, TAURI, LOCK, *SNAPSHOTS]
    )
    if changed != expected:
        raise Stop(
            "a release should touch exactly the eight version files. This run changed:\n    "
            + "\n    ".join(changed or ["(nothing — is that already the version?)"])
            + "\n  expected:\n    "
            + "\n    ".join(expected)
        )
    return changed


def preflight() -> None:
    """Everything this needs, checked before anything is written.

    `gh` is not optional here — the pull request is the whole point, and `main` is protected so
    there is no fallback path. Checking late means bumping eight files, regenerating three snapshots
    and only then finding out the release cannot be opened. Which is what happened the first time
    this ran under WSL, where `gh` is simply not installed.
    """
    if shutil.which("gh") is None:
        raise Stop(
            "`gh` is not on PATH. This opens the pull request with it, and `main` is protected, "
            "so there is no way to finish without it.\n"
            "  In WSL with gh installed on the Windows side, put a `gh` SHIM on PATH — this "
            "looks for `gh`, not `gh.exe`, so inheriting the Windows PATH is not enough:\n"
            "    mkdir -p /tmp/shims && ln -sfn '/mnt/c/Program Files/GitHub CLI/gh.exe' "
            "/tmp/shims/gh && export PATH=/tmp/shims:$PATH\n"
            "  Do NOT just run this from Windows instead. The snapshots stamp the version from "
            "the INSTALLED package metadata, so they carry whatever THAT interpreter has — and "
            "if it imports chimera from the source tree, cutting a release would install the "
            "package there as a side effect of cutting it."
        )
    if subprocess.run(["gh", "auth", "status"], capture_output=True, check=False).returncode != 0:
        raise Stop("`gh` is installed but not logged in. Run `gh auth login` first.")


def push_branch(branch: str) -> None:
    """Push using gh's own credential helper, and never wait on a prompt.

    Git on a machine with no credential helper does not fail — it BLOCKS, asking for a username on
    a stdin nobody is watching, and a release script that hangs looks like a slow release. Two
    changes: `gh auth git-credential` lends the token this script is already authenticated with
    (passed as a helper, so it never appears in a command line or a process list), and
    `GIT_TERMINAL_PROMPT=0` turns anything still unauthenticated into an error you can read.
    """
    run(
        "git",
        "-c",
        "credential.helper=",  # drop whatever is configured, so the next one is the only one
        "-c",
        "credential.helper=!gh auth git-credential",
        "push",
        "-u",
        "origin",
        branch,
        env={"GIT_TERMINAL_PROMPT": "0"},
    )


def open_pull_request(version: str, changed: list[str]) -> str:
    branch = f"release/{version}"
    run("git", "checkout", "-b", branch)
    run("git", "add", *changed)
    run("git", "commit", "-m", f"chore(release): {version}")
    push_branch(branch)

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
    preflight()
    check_clean_and_on_main()

    previous = current_version()
    if version == previous:
        raise Stop(f"{version} is already the current version.")
    print(f"  {previous} → {version}  ({semver(version)} for the desktop shell)", file=sys.stderr)

    write_versions(version)
    try:
        regenerate_snapshots(version)
        refresh_lock(version)
        changed = check_only_expected_files_changed(version)
    except SystemExit:
        # Leave the tree as it was: a half-applied bump is a trap for whoever runs `git status`
        # next and sees three of eight files changed.
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
