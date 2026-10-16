"""Read the state of a pull request without giving the reader a way to act on it.

The Git panel could open a pull request and then had no safe answer to "what happened to it?".
This module asks ``gh`` only for state and checks, never for a comment, merge, rerun or edit. A short
process-local cache keeps a visible card from turning each repaint into a GitHub API request; cache
entries are keyed by resolved workspace and PR number, and expire rather than becoming a stale
claim about a merge.

Three rules that each closed a defect of the first draft:

* **One gh call per read, not two.** The draft ran ``gh auth status`` before every ``pr view``, which
  doubled the requests the cache existed to spare and still could not tell a signed-out gh from a
  network failure any better than ``pr view``'s own stderr does. A signed-out gh says so there
  (``gh auth login``), so that is where it is read.
* **An error is never cached.** Caching a failure turned one dropped connection into a minute of
  "could not read" after the network was back, and the card's refresh button could not get past it.
  Only an answer GitHub actually gave is kept.
* **The cache is bounded.** Keyed by folder and number, it grew by one entry per PR ever looked at in
  the life of the process; it now drops its oldest entries past :data:`_CACHE_MAX`.

Check names are written by whoever controls the repository's workflows, so they pass through
``approval.visible`` on the way in: a name cannot carry a terminal escape into a later ``print``.
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chimera.core.pull_request import gh_command, run_quiet
from chimera.governance.approval import visible

_CACHE_SECONDS = 60.0
#: Past this many entries the oldest are dropped. A person looks at a handful of PRs at a time; the
#: bound exists so a long-running process does not keep every PR it ever saw.
_CACHE_MAX = 64
_CACHE: OrderedDict[tuple[str, int], tuple[float, PullRequestStatus]] = OrderedDict()
_CACHE_LOCK = threading.Lock()

#: What gh prints when it has no usable credentials. Matched case-insensitively in ``pr view``'s
#: stderr — the exit code alone is the same for "signed out" and "no such PR".
_AUTH_HINTS = ("gh auth login", "not logged in", "authentication", "http 401", "bad credentials")


@dataclass(frozen=True)
class PullRequestStatus:
    state: str = ""
    mergeable: str = ""
    passed: int = 0
    failed: int = 0
    pending: int = 0
    failing_names: tuple[str, ...] = ()
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "mergeable": self.mergeable,
            "passed": self.passed,
            "failed": self.failed,
            "pending": self.pending,
            "failing_names": list(self.failing_names),
            "error": self.error,
        }


def _check_status(check: object) -> str:
    if not isinstance(check, dict):
        return "pending"
    conclusion = str(check.get("conclusion") or "").upper()
    status = str(check.get("status") or "").upper()
    legacy_state = str(check.get("state") or "").upper()
    if conclusion in {"SUCCESS", "NEUTRAL", "SKIPPED"} or legacy_state in {"SUCCESS", "EXPECTED"}:
        return "passed"
    if conclusion in {"FAILURE", "TIMED_OUT", "ACTION_REQUIRED", "CANCELLED", "STARTUP_FAILURE"}:
        return "failed"
    if legacy_state in {"FAILURE", "ERROR"}:
        return "failed"
    if status == "SUCCESS":
        return "passed"
    return "pending"


def parse_status(payload: object) -> PullRequestStatus:
    """Parse GitHub's JSON shape conservatively; unknown checks remain pending, not green."""
    if not isinstance(payload, dict):
        raise ValueError("GitHub returned an invalid pull request response")
    state = str(payload.get("state") or "").upper()
    if state not in {"OPEN", "CLOSED", "MERGED"}:
        raise ValueError("GitHub returned an unknown pull request state")
    mergeable = str(payload.get("mergeable") or "UNKNOWN").upper()
    if mergeable not in {"MERGEABLE", "CONFLICTING", "UNKNOWN"}:
        mergeable = "UNKNOWN"
    raw_checks = payload.get("statusCheckRollup")
    checks = raw_checks if isinstance(raw_checks, list) else []
    buckets = [_check_status(check) for check in checks]
    failed_names: list[str] = []
    for check, result in zip(checks, buckets, strict=True):
        if result != "failed" or not isinstance(check, dict):
            continue
        name = visible(str(check.get("name") or check.get("context") or "unknown check"))[:200]
        if name not in failed_names:
            failed_names.append(name)
    return PullRequestStatus(
        state=state.lower(),
        mergeable=mergeable.lower(),
        passed=buckets.count("passed"),
        failed=buckets.count("failed"),
        pending=buckets.count("pending"),
        failing_names=tuple(failed_names),
    )


def clear_status_cache() -> None:
    """Clear cached reads (also the deterministic seam for tests)."""
    with _CACHE_LOCK:
        _CACHE.clear()


def _read(command: Sequence[str], root: Path, number: int) -> PullRequestStatus:
    viewed = run_quiet(
        [*command, "pr", "view", str(number), "--json", "state,mergeable,statusCheckRollup"],
        root,
    )
    if viewed.returncode != 0:
        said = f"{viewed.stderr or ''} {viewed.stdout or ''}".lower()
        if any(hint in said for hint in _AUTH_HINTS):
            return PullRequestStatus(error="GitHub CLI (gh) is not authenticated; run `gh auth login`")
        return PullRequestStatus(error="Could not read pull request from GitHub")
    try:
        return parse_status(json.loads(viewed.stdout))
    except (ValueError, TypeError):  # json.JSONDecodeError is a ValueError
        return PullRequestStatus(error="GitHub returned an invalid pull request response")


def pull_request_status(
    workspace: Path,
    number: int,
    *,
    gh: Sequence[str] | None = None,
    now: float | None = None,
) -> PullRequestStatus:
    """Read one PR, with an explicit error rather than an invented empty/green status."""
    if number < 1:
        return PullRequestStatus(error="Pull request number must be positive")
    root = Path(workspace).resolve()
    key = (str(root), number)
    at = time.monotonic() if now is None else now
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and at - cached[0] < _CACHE_SECONDS:
            return cached[1]
    command = list(gh) if gh is not None else gh_command()
    if not command:
        return PullRequestStatus(error="GitHub CLI (gh) is not installed")
    result = _read(command, root, number)
    if result.error:
        return result  # never cached: the next read, or the refresh button, asks again
    with _CACHE_LOCK:
        _CACHE[key] = (at, result)
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
    return result
