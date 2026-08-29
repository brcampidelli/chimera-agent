"""The list that decides what counts as a document body had drifted from the tools it describes.

`_DOCUMENT_ARGS` has two consumers that read it for different reasons: `elide_values()` keeps bodies
out of `audit.jsonl` — which `GET /api/governance/audit` serves onto the Security screen — and
`render_action()` keeps bodies from being judged by shell rules. Both were reading a hand-written
list, and a hand-written list against tools that keep changing drifts by construction.

Measured against every `Tool` subclass in the package, before this: the list named five arguments
**no tool has** (`data`, `diff`, `new_str`, `old_str`, `new_text`) and missed four that carry whole
bodies — `old`/`new` on `edit_file`, `code` on `execute_code` and `code_interpreter`, and `spec` on
`render_chart`. So `edit_file(new="rm -rf /")` was judged as a shell command *and* had its entire
replacement text written to a log the app serves over HTTP.

This file is the guard that stops it drifting again, and it does it by forcing a decision rather than
by guessing: every argument every shipped tool declares must be on one side of the line, and a new
one fails the build until somebody says which side it is on.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import Any

import chimera
from chimera.governance.governed_tool import (
    _DOCUMENT_ARGS,
    _EXECUTED_DOCUMENT_ARGS,
    _NESTED_DOCUMENT_ARGS,
    elide_values,
    render_action,
)
from chimera.tools.base import Tool

#: Arguments that say *which* thing is acted on, or *what to look for*. They stay in the audit line
#: and in the command half, because they are the half of a record worth reading: `run_shell` with a
#: command, `edit_file` with a path, `send_email` with a recipient.
#:
#: Two entries here are judgement calls rather than obvious ones, named so the next reader can
#: disagree on purpose: `task` (`spawn_subagent`) is a paragraph of prose, and `subject`
#: (`send_email`) is author-written text — but both are the *identity* of the action from an
#: auditor's point of view ("what did it delegate?", "what did it send?"), and eliding them would
#: leave a line that records a delegation nobody can identify. Neither is a place a secret goes by
#: accident, which is the criterion `_DOCUMENT_ARGS` exists to serve.
_IDENTIFIERS = frozenset({
    "action",
    # `mcp_call`/`mcp_describe`: which server tool. The identity of the action — an audit line
    # reading "called <120 chars>" would record that something happened and nothing about what.
    "tool",
    # `skill_view`: which installed skill, and which file inside it. Both name a thing; the file's
    # CONTENT comes back in the result, which is where the untrusted-output marker lives.
    "file_path",
    "name",
    "allow_invalid",
    "audio_only",
    "command",
    "cwd",
    "exclude",
    "fields",
    "format",
    "glob",
    "include",
    "include_links",
    "language",
    "limit",
    "max_depth",
    "max_results",
    "out",
    "out_dir",
    "path",
    "pattern",
    "prompt",
    "query",
    "ref",
    "render",
    "replace_all",
    "reset",
    "respect_robots",
    "resume",
    "same_domain",
    "search",
    "selectors",
    "size",
    "subject",
    "task",
    "timeout",
    "to",
    "tools",
    "url",
    "video",
    "voice_id",
})


def _declared_arguments() -> dict[str, set[str]]:
    """Every argument every `Tool` subclass in the package declares, keyed by argument name.

    Walked from the package rather than from `default_registry`, deliberately: a tool that is only
    registered under an optional extra (the media and browser families) still ships, still runs when
    that extra is installed, and its body arguments leak exactly the same. Reading the registry would
    have made this guard's coverage depend on which extras the test environment happened to have.
    """
    found: dict[str, set[str]] = {}
    for module in pkgutil.walk_packages(chimera.__path__, "chimera."):
        try:
            loaded = importlib.import_module(module.name)
        except Exception:  # noqa: BLE001 — an optional extra that is not installed here
            continue
        for _, obj in inspect.getmembers(loaded, inspect.isclass):
            if not (issubclass(obj, Tool) and obj is not Tool):
                continue
            properties: dict[str, Any] = (getattr(obj, "parameters", None) or {}).get(
                "properties"
            ) or {}
            name = getattr(obj, "name", "") or ""
            for argument in properties:
                found.setdefault(argument, set()).add(str(name) or obj.__name__)
    return found


def test_every_argument_a_shipped_tool_declares_is_classified() -> None:
    """The guard. A new tool argument fails the build until somebody decides what it is.

    The criterion, so this is extended by rule and not by anecdote: a **body** is text the tool
    writes to disk, executes, or transmits whole — the kind of value that can carry an entire secret.
    An **identifier** says which thing is being acted on, or what to look for.
    """
    declared = _declared_arguments()
    assert len(declared) > 30, f"the walk found only {len(declared)} arguments; it is not walking"

    classified = _DOCUMENT_ARGS | _NESTED_DOCUMENT_ARGS | _IDENTIFIERS
    unclassified = {arg: sorted(tools) for arg, tools in declared.items() if arg not in classified}
    assert not unclassified, (
        "new tool arguments nobody has classified as a document body or an identifier: "
        f"{unclassified}. Add each to _DOCUMENT_ARGS (it can carry a whole secret) or to "
        "_IDENTIFIERS in this file (it says which thing was acted on)."
    )


def test_the_list_names_no_argument_that_no_tool_has() -> None:
    """Five dead names were in there, and a dead name is not harmless.

    It cannot be tested, it gives false assurance that `diff` is covered, and it costs real safety
    the day a tool uses `data` as an identifier — that argument would silently drop out of the
    command scope and out of the audit at once.
    """
    declared = set(_declared_arguments())
    for name, group in (
        ("_DOCUMENT_ARGS", _DOCUMENT_ARGS),
        ("_NESTED_DOCUMENT_ARGS", _NESTED_DOCUMENT_ARGS),
        ("_EXECUTED_DOCUMENT_ARGS", _EXECUTED_DOCUMENT_ARGS),
    ):
        orphans = sorted(group - declared)
        assert not orphans, f"{name} names arguments no shipped tool declares: {orphans}"


# --- the two behaviours the split exists for ------------------------------------------------------


def test_an_edit_body_is_neither_logged_nor_read_as_a_command() -> None:
    """`edit_file(new=…)` was both, and it is the case that started this.

    `old`/`new` were missing from the list, so the replacement text went into the audit line whole
    and was judged by every shell rule — which is how a legitimate edit that happens to contain
    `rm -rf` got blocked while its content was written to a file the app serves.
    """
    kwargs = {"path": "deploy.sh", "old": "rm -rf ./build", "new": "rm -rf /var/lib/data"}
    command, document = render_action("edit_file", kwargs)
    logged = str(elide_values(kwargs))

    assert "rm -rf /var/lib/data" not in command, "an edit body is still judged as a shell command"
    assert "rm -rf /var/lib/data" in document, "the body must still be read for credentials"
    assert "rm -rf /var/lib/data" not in logged, "the edit body reaches a log served over HTTP"
    assert "deploy.sh" in logged, "the path was elided too, so the line says nothing useful"


def test_an_executed_body_is_kept_out_of_the_log_and_still_judged() -> None:
    """The one place the two consumers must disagree, and the reason there are two lists.

    `execute_code(code=…)` is a body: the whole program lands in the audit line otherwise. But it is
    a body that then RUNS, so taking it out of the command half would stop every shell rule from
    reading it. Both halves are asserted here, because a single list could only get one of them
    right — and getting the second one wrong turns `os.system('rm -rf /')` from BLOCK into ALLOW.
    """
    program = "import os\nos.system('rm -rf /')\n"
    kwargs = {"code": program, "timeout": 30}
    command, _document = render_action("execute_code", kwargs)
    logged = str(elide_values(kwargs))

    assert "rm -rf /" in command, "an executed body left the command scope: the shell rules are blind"
    assert program not in logged, "the program body reaches a log served over HTTP"

    # And end to end, because the assertion above is about the rendering rather than the verdict.
    from chimera.governance.kernel import TrustKernel

    verdict = TrustKernel().evaluate(command)
    assert verdict.decision.value == "block", (
        f"executed code stopped being judged: {verdict.decision.value} ({verdict.rule})"
    )


def test_a_nested_body_is_elided_without_losing_which_files_were_touched() -> None:
    """`edit_batch(edits=[…])` is why nesting is a third set rather than a third entry.

    Treating `edits` as a plain document elides the whole array, and with it every `path` — on the
    one tool that writes to SEVERAL files at once, which is exactly when "which file was touched" is
    worth the most.
    """
    kwargs = {
        "edits": [
            {"path": "a.py", "old": "x", "new": "SECRET_BODY_ONE"},
            {"path": "b.py", "old": "y", "new": "SECRET_BODY_TWO"},
        ]
    }
    logged = str(elide_values(kwargs))

    assert "SECRET_BODY_ONE" not in logged and "SECRET_BODY_TWO" not in logged
    assert "a.py" in logged and "b.py" in logged, "the paths went with the bodies"
