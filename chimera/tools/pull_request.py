"""``open_pull_request``: push the workspace's branch to origin and open a pull request — asked every time.

Registered only when the owner switched ``CHIMERA_PULL_REQUESTS`` on (`tools/builtin.py`). Every call
is a REVIEW that only a person can answer: the tool asks before it pushes anything, through
:func:`chimera.governance.approval.always_ask`, which reads ``CHIMERA_APPROVAL_MODE=allow`` as
``ask``. So no approval mode, posture, governance mode (``observe`` says yes to the kernel's REVIEWs,
never to this one) or earlier answer releases it. A surface that has a screen hands the tool its own
question through :attr:`OpenPullRequestTool.approve` (the Code screen's card, the TUI's modal); every
other surface — cron, the bots, ``POST /api/runs`` — gets the durable question on the owner's
channel, where silence refuses.

What the person is shown is what will be published: the branch and its base, where the push goes
(origin's push URL, credential removed), whether it updates a branch that already exists there, the
commits, the diff summary, the title, the WHOLE description, and how many changed files are NOT in
the push. What runs on a yes is :func:`chimera.core.pull_request.open_pull_request`,
which pushes the exact commit that was shown, never the default branch, never with force, and never
through a shell.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chimera.governance.policy import Decision, Verdict
from chimera.tools.base import Tool, refusal

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chimera.core.pull_request import Readiness

#: The rule name on the question's record line (`pending.FACTS`), so the history can count these.
RULE = "open_pull_request"

REASON = (
    "opening a pull request pushes this branch to origin and publishes the title and description "
    "below; it is asked every time, under every approval setting"
)


class OpenPullRequestTool(Tool):
    name = "open_pull_request"
    description = (
        "Push the workspace's current git branch to the `origin` remote and open a pull request "
        "with the GitHub CLI. Commit your work on a branch other than the default branch first: the "
        "default branch is never pushed, nothing is ever force-pushed, and uncommitted changes are "
        "not included. EVERY call asks the owner, who sees the branch, the commits, the diff summary, "
        "the title and the description; if they decline or do not answer, nothing is pushed — do not "
        "report it as done. Returns the pull request's URL."
    )
    parameters = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "The pull request's title (one line)."},
            "body": {
                "type": "string",
                "description": "The pull request's description, in Markdown: what changed and why.",
            },
            "base": {
                "type": "string",
                "description": "The branch to merge into. Default: origin's default branch.",
            },
            "draft": {"type": "boolean", "description": "Open it as a draft. Default false."},
        },
        "required": ["title"],
    }

    def __init__(
        self,
        workspace: Path | None = None,
        *,
        approve: Callable[..., bool] | None = None,
        gh: Sequence[str] | None = None,
    ) -> None:
        self.workspace = Path(workspace or Path.cwd()).resolve()
        self.approve = approve
        """Who is asked. None: :func:`_default_approver`, resolved per call from the settings."""
        self._gh = list(gh) if gh is not None else None

    def run(self, **kwargs: Any) -> str:
        from chimera.core.pull_request import (
            check_text,
            open_pull_request,
            readiness,
            sentence,
        )

        title = str(kwargs.get("title") or "").strip()
        body = str(kwargs.get("body") or "")
        base = str(kwargs.get("base") or "").strip() or None
        draft = bool(kwargs.get("draft", False))
        problem = check_text(title, body)
        if problem:
            return f"error: cannot open a pull request: {problem}"
        state = readiness(self.workspace, base=base, gh=self._gh)
        if not state.ready:
            return f"error: cannot open a pull request: {sentence(state.reason)}"
        action = self._card(state, title, body, draft)
        approver = self.approve if self.approve is not None else _default_approver()
        if not approver(Verdict(Decision.REVIEW, REASON, RULE), action):
            return refusal(
                "[pull request: needs the owner's yes] The owner did not approve opening this pull "
                "request, or nobody could be asked. Nothing was pushed and no pull request exists. "
                "Do not report it as done."
            )
        result = open_pull_request(
            self.workspace,
            title=title,
            body=body,
            expect_head=state.head,
            branch=state.branch,
            base=state.base,
            draft=draft,
            gh=self._gh,
            # What the card showed: a push URL or a remote branch that changed while the question
            # waited (up to the approval timeout) is refused rather than pushed to.
            expect_remote=state.remote,
            expect_remote_head=state.remote_head,
        )
        if not result["ok"]:
            return f"error: the pull request was not opened: {result['error']}"
        return f"Opened pull request {result['url']} ({state.branch} -> {state.base})."

    @staticmethod
    def _card(state: Readiness, title: str, body: str, draft: bool) -> str:
        """The action line the person reads. First line ``open_pull_request: …`` — the tool name
        before the colon is what `approval._facts_of` records as the tool.

        What is published comes first — the warning about what is left out, the title, the
        description — and the commit list and diff summary, which can run to thousands of
        characters, come last. `always_ask` shows every surface the whole card; the order is for a
        person reading it on a phone, so the part they are approving is the part they read first.

        The description is on the card WHOLE. It used to be its first 600 characters and a length:
        an agent under injection could write 600 harmless characters and then paste what it had
        read, and the owner would approve a summary and publish the rest.
        """
        commits = "\n".join(f"  {line}" for line in state.commits) or "  (none listed)"
        more = f" (+{state.ahead - len(state.commits)} more)" if state.ahead > len(state.commits) else ""
        lines = [
            f"open_pull_request: {state.branch} -> {state.base} on {state.remote}"
            + (" (draft)" if draft else "")
        ]
        if state.remote_head:
            lines.append(
                f"UPDATES the branch {state.branch} that already exists on origin: "
                f"{state.remote_head[:12]} -> {state.head[:12]}; whoever reads that branch sees "
                "these commits at once, before any review"
            )
        if state.uncommitted:
            lines.append(f"{state.uncommitted} changed file(s) in the workspace are NOT included")
        lines += [
            f"title: {title}",
            "description:",
            body or "(empty)",
            f"commit {state.head[:12]}",
            f"{state.ahead} commit(s){more}:",
            commits,
            state.diffstat or "(no diff summary)",
        ]
        return "\n".join(lines)


def _default_approver() -> Callable[..., bool]:
    """The question for a surface that brought none: cron, the bots, ``POST /api/runs``.

    The durable one, sent where this deployment sends approval questions and answerable with
    ``chimera approve`` (or a terminal prompt, in a process with a person at its console).
    """
    from chimera.config import get_settings
    from chimera.governance.approval import always_ask, deliverer_for

    settings = get_settings()
    return always_ask(
        settings.home, mode=settings.approval_mode, deliver=deliverer_for(settings)
    )
