"""Every rule in the policy died on the second line of a shell script.

`GovernedTool.run` built the string the rules read as `f"{self.name} {kwargs}"`. Interpolating a
dict calls `repr` on it, and `repr` escapes a newline into the two characters `\\` and `n` — so the
`n` fused with the word after it (`\\nrm` arrived as the text `nrm`) and destroyed the `\\b` that
every rule in `chimera.governance.policy` opens with. Measured on the branch before this, with the
default ruleset:

    review  git_force_push   'git push --force origin main'
    allow   default          'echo hi\\ngit push --force origin main'
    block   rm_rf_root       'rm -rf /var/lib/data'
    allow   default          'set -e\\nrm -rf /var/lib/data'
    block   rm_rf_root       write_file {'path': 'd.md', 'content': 'roda `rm -rf /tmp/x`'}

The protection was inverted. Every real shell script has more than one line, so the two-line form of
a blocked command walked straight through, while a markdown file that merely *quoted* `rm -rf` was
hard-blocked. Both halves are the same mistake made twice: the rules were reading a rendering of the
call instead of its values, and reading document bodies as though they were commands.

Two further failures fell out of measuring rather than assuming, and are pinned here too:
`rm -rf .` never fired at all (its `$` anchor sat behind the dict repr's closing `'}`), and once
values went in raw it still only fired on the last line, because `$` without `re.MULTILINE` means
end-of-script rather than end-of-command.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest

from chimera.governance.audit import AuditLog
from chimera.governance.governed_tool import GovernedTool, render_action
from chimera.governance.kernel import TrustKernel
from chimera.governance.policy import Decision, RuleSet, Scope
from chimera.tools.base import Tool


class _Runner(Tool):
    """Stands in for `run_shell`: its argument is executed, so command rules apply to it."""

    name = "run_shell"
    description = "run a command"
    parameters = {"type": "object", "properties": {"command": {"type": "string"}}}

    def run(self, **kwargs: Any) -> str:
        return "ran"


class _Writer(Tool):
    """Stands in for `write_file`: `content` is a body it stores, never a command it runs."""

    name = "write_file"
    description = "write a file"
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
    }

    def run(self, **kwargs: Any) -> str:
        return "wrote"


def _decide(tool: Tool, **kwargs: Any) -> Decision:
    """The kernel's verdict for one governed call, without running anything."""
    seen: list[Any] = []
    governed = GovernedTool(tool, TrustKernel(), approve=lambda v, a: seen.append(v) or False)
    out = governed.run(**kwargs)
    if "BLOCKED" in out:
        return Decision.BLOCK
    if seen:
        return Decision.REVIEW
    return Decision.ALLOW


# --- the reported defect ----------------------------------------------------

@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("git push --force origin main", Decision.REVIEW),
        ("echo hi\ngit push --force origin main", Decision.REVIEW),
        ("rm -rf /var/lib/data", Decision.BLOCK),
        ("set -e\nrm -rf /var/lib/data", Decision.BLOCK),
    ],
)
def test_a_second_line_does_not_hide_a_command(command: str, expected: Decision) -> None:
    """The four measured cases. Rows 2 and 4 were `allow`/`default` before this."""
    assert _decide(_Runner(), command=command) is expected


def test_the_newline_survives_into_the_action() -> None:
    """The mechanism, pinned directly: a real newline, not the two characters `\\` and `n`.

    Asserting on the verdict alone would keep passing if someone "fixed" this by loosening the
    rules' word boundaries instead, which would buy the false positives back.
    """
    action, _ = render_action("run_shell", {"command": "set -e\nrm -rf /var/lib/data"})
    assert "\n" in action
    assert "\\n" not in action
    assert "nrm" not in action  # what the escaped newline used to fuse into


# --- the other half: prose that quotes a command is not a command -----------

@pytest.mark.parametrize(
    "content",
    [
        "roda `rm -rf /tmp/x`",
        "git push --force is dangerous",
        "never run `mkfs.ext4 /dev/sda`",
    ],
)
def test_a_document_that_quotes_a_command_is_not_blocked(content: str) -> None:
    """`write_file` of a markdown file was hard-blocked for mentioning a command."""
    assert _decide(_Writer(), path="doc.md", content=content) is Decision.ALLOW


def test_the_path_is_still_judged_even_though_the_body_is_not() -> None:
    """Dropping bodies must not drop the whole call: the arguments that identify it still count."""
    action, document = render_action("write_file", {"path": ".env", "content": "x = 1"})
    assert ".env" in action
    assert "x = 1" not in action
    assert document == "x = 1"


# --- what must NOT be lost by scoping ---------------------------------------

#: Assembled at import, never spelled out, so this file holds no credential-shaped literal —
#: `gitleaks` failed this repo's CI twice on earlier fixtures, once for a written-out bearer token
#: and once because an identifier alone read as a key. See the note in
#: `tests/test_governance_on_the_api_path.py`; the rule under test sees the assembled value anyway.
_FAKE_TOKEN = "sk-" + "B" * 16 + "5678"


def test_a_credential_in_a_document_body_is_still_noticed() -> None:
    """The reason scoping is per-rule rather than a blanket "skip document bodies".

    Skipping them wholesale scored better than the status quo on the corpus and still opened two
    holes: a key does not become safe by being written to a file instead of exported in a shell.
    `secret_material` is the one default rule marked `Scope.ANY_TEXT`, and this is why.
    """
    assert _decide(_Writer(), path=".env", content=f"OPENAI_API_KEY={_FAKE_TOKEN}\n") is Decision.ALLOW
    verdict = TrustKernel().evaluate("write_file\n.env", document=_FAKE_TOKEN)
    assert verdict.decision is Decision.WARN
    assert verdict.rule == "secret_material"


def test_only_any_text_rules_read_the_document() -> None:
    """A command rule handed the same body must stay silent — otherwise scoping is decorative."""
    rules = RuleSet()
    assert all(r.scope is Scope.COMMAND for r in rules.rules if r.name == "rm_rf_root")
    assert rules.evaluate("write_file\nd.md", document="run `rm -rf /`") is None
    assert rules.evaluate("run_shell\nrm -rf /", document="") is not None


# --- the two failures that measuring turned up ------------------------------

@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("rm -rf .", Decision.BLOCK),
        ("cd /srv\nrm -rf .\necho ok", Decision.BLOCK),
        ("tar czf b.tgz .\nscp b.tgz dep@host:/backup\necho done", Decision.REVIEW),
    ],
)
def test_an_anchored_rule_fires_off_the_last_line(command: str, expected: Decision) -> None:
    """`$` has to mean end-of-command. `rm -rf .` matched nothing at all before this."""
    assert _decide(_Runner(), command=command) is expected


# --- benign calls must stay benign ------------------------------------------

@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "rm -rf build/",
        "git push origin feature/x",
        "npm ci\nnpm run build",
        "scp dep@host:/var/log/a.log ./logs/",
        "nc -z localhost 5432",
    ],
)
def test_ordinary_commands_are_untouched(command: str) -> None:
    """Splitting values apart could have fused two of them into a signature. It must not."""
    assert _decide(_Runner(), command=command) is Decision.ALLOW


# --- the guarantee this must not undo ---------------------------------------

def test_the_document_reaches_the_rules_but_never_the_audit(tmp_path: pathlib.Path) -> None:
    """Handing bodies to the rules must not hand them to a log the app serves over HTTP.

    That is exactly the hole `record_as` and `audit._redacted` were added to close, and the new
    `document` argument is the shortest path to reopening it.
    """
    marker = "TOTALLY-UNIQUE-BODY-MARKER"
    audit = AuditLog(tmp_path / "audit.jsonl")
    governed = GovernedTool(_Writer(), TrustKernel(audit=audit))
    governed.run(path="notes.md", content=f"{marker} and `rm -rf /tmp/x`")

    raw = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert marker not in raw, "the file body is in a log served over HTTP"
    assert "notes.md" in raw, "the audit lost which file was written"
    assert "chars>" in raw, "the elided rendering was replaced rather than kept"
