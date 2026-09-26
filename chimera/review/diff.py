"""The change under review: collected from git, parsed, and rendered with new-file line numbers.

The reviewer is asked to cite ``file:line``. A raw unified diff does not carry line numbers on its
lines, only on its hunk headers, so a model citing a line has to count, and counting is where a
finding lands three lines away from its defect. Every line of the new version is rendered here with
its number, which turns the citation into a copy.

The same parse answers the one objective question a filter can ask about a finding: is the line it
names in this diff at all? That is the first ground of the cautious verifier
(`bench/review_judge`, arm A), and here it is decided by arithmetic rather than by a model.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from chimera.core.worktree import _git, is_git_repo

#: Lines of context git shows around each change. Git's own default is 3, which leaves a reviewer
#: looking at a changed condition without the function it sits in.
DEFAULT_CONTEXT = 10

#: How far outside a hunk a cited line may fall and still count as "in the diff". Deliberately
#: loose: the filter this serves drops a finding only when the code it describes is not shown, and a
#: citation off by a line or two is a finding about the shown code.
ANCHOR_SLACK = 3

#: Branches tried, in order, when no ``--base`` is given.
DEFAULT_BASES = ("main", "origin/main", "master", "origin/master")

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


class DiffError(RuntimeError):
    """The change could not be collected: not a repository, or a base git does not know."""


@dataclass
class Line:
    tag: str  # "+", "-" or " "
    text: str
    new: int | None  # the line's number in the new version; None for a removed line
    anchor: int  # the new-version position a finding about this line would cite


@dataclass
class Hunk:
    header: str
    new_start: int
    new_len: int
    lines: list[Line] = field(default_factory=list)


@dataclass
class FileDiff:
    path: str
    status: str = "modified"  # modified | added | deleted | renamed | binary
    old_path: str = ""
    hunks: list[Hunk] = field(default_factory=list)

    def render(self) -> list[str]:
        """The file's diff with the new version's line numbers down the left edge."""
        out = [f"### {self.path} ({self.status})"]
        for hunk in self.hunks:
            out.append(hunk.header)
            for line in hunk.lines:
                number = "" if line.new is None else str(line.new)
                out.append(f"{number:>6} {line.tag} {line.text}")
        return out

    def anchors(self, line: int) -> bool:
        """Whether a finding citing ``line`` of this file points at code this diff shows."""
        if self.status == "deleted":
            return True  # a finding about a removed file has no new-version line to cite
        for hunk in self.hunks:
            low = hunk.new_start - ANCHOR_SLACK
            high = hunk.new_start + max(hunk.new_len, 1) - 1 + ANCHOR_SLACK
            if low <= line <= high:
                return True
        return False

    def window(self, line: int, radius: int = 30) -> str:
        """The rendered lines around ``line``: what the verifier reads about one finding."""
        rendered: list[tuple[int, str]] = []
        for hunk in self.hunks:
            rendered.append((hunk.new_start, hunk.header))
            for entry in hunk.lines:
                number = "" if entry.new is None else str(entry.new)
                rendered.append((entry.anchor, f"{number:>6} {entry.tag} {entry.text}"))
        if not rendered:
            return f"### {self.path} ({self.status})"
        closest = min(range(len(rendered)), key=lambda i: abs(rendered[i][0] - line))
        start = max(0, closest - radius)
        body = [text for _, text in rendered[start : closest + radius + 1]]
        return "\n".join([f"### {self.path} ({self.status})", *body])


@dataclass
class ReviewDiff:
    """Every file the change touches, plus where it came from."""

    files: list[FileDiff]
    base: str = ""
    target: str = "working tree"
    base_label: str = ""

    def file(self, path: str) -> FileDiff | None:
        wanted = _normal(path)
        for f in self.files:
            if _normal(f.path) == wanted:
                return f
        return None


def _normal(path: str) -> str:
    path = path.strip().replace("\\", "/")
    for prefix in ("a/", "b/", "./"):
        if path.startswith(prefix):
            path = path[len(prefix) :]
    return path


def parse(text: str) -> list[FileDiff]:
    """Parse ``git diff`` output, or any unified diff with ``---``/``+++`` headers.

    A hunk is read by its counts, not by the first character of each line: a removed line whose
    text starts with ``-- `` is printed as ``--- …``, and only the counts tell it from the next
    file's header.
    """
    files: list[FileDiff] = []
    current: FileDiff | None = None
    hunk: Hunk | None = None
    new_no = old_left = new_left = 0
    minus_seen = False  # this file already had its "--- " header, so another one starts a file
    for raw in text.splitlines():
        if hunk is not None and (old_left > 0 or new_left > 0):
            tag, body = (raw[0], raw[1:]) if raw else (" ", "")
            if tag == "\\":
                continue  # "\ No newline at end of file"
            if tag in ("+", "-", " "):
                if tag == "-":
                    hunk.lines.append(Line(tag, body, None, new_no))
                    old_left -= 1
                else:
                    hunk.lines.append(Line(tag, body, new_no, new_no))
                    new_no += 1
                    new_left -= 1
                    old_left -= 1 if tag == " " else 0
                continue
        hunk = None
        if raw.startswith("diff --git "):
            current = FileDiff(path=_normal(raw.split(" b/", 1)[-1]))
            files.append(current)
            minus_seen = False
        elif raw.startswith("--- "):
            if current is None or current.hunks or minus_seen:
                current = FileDiff(path="")
                files.append(current)
            minus_seen = True
            old = raw[4:].split("\t", 1)[0]
            current.old_path = "" if old == "/dev/null" else _normal(old)
            if old == "/dev/null":
                current.status = "added"
        elif raw.startswith("+++ ") and current is not None:
            new = raw[4:].split("\t", 1)[0]
            if new == "/dev/null":
                current.status = "deleted"
                current.path = current.path or current.old_path
            else:
                current.path = _normal(new)
        elif current is None:
            continue
        elif raw.startswith("new file mode"):
            current.status = "added"
        elif raw.startswith("deleted file mode"):
            current.status = "deleted"
        elif raw.startswith("rename from "):
            current.status, current.old_path = "renamed", raw[len("rename from ") :]
        elif raw.startswith("Binary files ") or raw.startswith("GIT binary patch"):
            current.status = "binary"
        elif match := _HUNK.match(raw):
            old_left = int(match.group(2)) if match.group(2) is not None else 1
            new_no = int(match.group(3))
            new_left = int(match.group(4)) if match.group(4) is not None else 1
            hunk = Hunk(raw, new_no, new_left)
            current.hunks.append(hunk)
    return [f for f in files if f.path]


def _run(repo: Path, args: list[str]) -> str:
    try:
        done = _git(["-c", "core.quotepath=off", *args], repo)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DiffError(f"git failed: {exc}") from exc
    if done.returncode != 0:
        raise DiffError((done.stderr or done.stdout or "git failed").strip()[:300])
    return done.stdout or ""


def merge_base(repo: Path, base: str | None) -> tuple[str, str]:
    """The commit a working-tree review compares against, and a label saying how it was chosen."""
    candidates = (base,) if base else DEFAULT_BASES
    for ref in candidates:
        if ref is None:
            continue
        try:
            found = _run(repo, ["merge-base", "HEAD", ref]).strip()
        except DiffError:
            continue
        if found:
            return found, f"merge base with {ref}"
    if base:
        raise DiffError(f"git does not know {base!r} (or it shares no history with HEAD)")
    raise DiffError("no main or master branch to compare against; pass --base")


def collect(
    repo: Path,
    *,
    base: str | None = None,
    revision_range: str | None = None,
    context: int = DEFAULT_CONTEXT,
) -> ReviewDiff:
    """The change to review: a revision range, or the working tree against a merge base.

    Untracked files are left out. A working tree usually holds scratch files nobody meant to ship,
    and reviewing them would bury the change under them; the report names how many were skipped.
    """
    if not is_git_repo(repo):
        raise DiffError(f"{repo} is not inside a git repository")
    flags = ["diff", "--no-color", "--no-ext-diff", "-M", f"--unified={max(0, context)}"]
    if revision_range:
        text = _run(repo, [*flags, revision_range])
        return ReviewDiff(parse(text), base="", target=revision_range, base_label=revision_range)
    commit, label = merge_base(repo, base)
    text = _run(repo, [*flags, commit])
    return ReviewDiff(parse(text), base=commit, target="working tree", base_label=label)


def untracked(repo: Path) -> list[str]:
    """Files git does not track and does not ignore: the ones a working-tree review skips."""
    try:
        text = _run(repo, ["ls-files", "--others", "--exclude-standard"])
    except DiffError:
        return []
    return [line for line in text.splitlines() if line.strip()]
