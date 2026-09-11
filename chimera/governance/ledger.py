"""Per-run capability ledger + heuristic taint tracking (issues #2 and #5).

Two pieces of community feedback on the governance writeup converged here:

* **#2 — capability ledger + replay** (u/Dependent_Policy1307, u/Far-Stable2591): record
  *what each action did* — what was fetched, which files it wrote, what it executed — so a
  reviewer can reconstruct a run, and so the policy can reason across a *sequence* of
  individually-harmless steps.
* **#5 — taint tracking** (u/zoharel, u/Dependent_Policy1307): mark content fetched from the
  web / external sources as **tainted**, propagate that taint into the files it produces, and
  escalate to ``review`` when an action *executes or self-modifies based on tainted input* —
  the "downloaded X, then ran X" flow that walks past a memoryless lexical rule.

**What this is NOT** (kept honest on purpose): this is *heuristic, reference/flow* taint —
it catches a tainted URL or file path that reappears in a later command, or fetched content
that flows verbatim into a file that is then run. It does **not** solve the data-vs-instructions
problem: a model laundering tainted content (paraphrasing, re-encoding) defeats substring
matching. It is **observability + sequence-aware review**, layered on top of — not a
replacement for — the sandbox, which is still the real containment boundary. It only ever
*escalates to review*; it never hard-blocks a benign action.

**Provenance is not authority** (arXiv 2608.29942; `bench/injection/RESULTS.md`, 2026-09-08). Every
fetch taints, and the coarse narrowing keyed on that bit escalated a write whose value came from a
page the user asked for exactly as it escalated the attack — 10/10 against 10/10. The ledger now
records *who asked* for each fetch (:attr:`CapabilityEvent.requested_by`, derived strictly from the
user's own instruction via :meth:`TaintLedger.set_instruction`), and a mode switch
(``CHIMERA_TAINT_AUTHORITY=authority``) lets the narrowing ignore a fetch the user named. The mode
ships **off**, and the same results file records why: with the user having asked to summarise the
poisoned page, the flow matcher below is all that remains between the page and the sinks, and it
sees a whole snippet or a source ref — never a fragment. The signal is recorded in every mode, so a
reviewer can read it off the ledger either way.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from chimera.governance.policy import Decision
from chimera.telemetry import get_logger

_log = get_logger("governance.ledger")


class SharedTaint:
    """A thread-safe cross-agent taint view for a fan-out of sub-agents.

    Each worker in a ``solve-batch`` / ``crew-isolated`` run gets its own :class:`TaintLedger`, but they
    all hold ONE ``SharedTaint``. The moment any worker consumes untrusted content, every worker's
    :meth:`TaintLedger.run_tainted` flips True — so the dangerous-tool narrowing (``narrow_on_taint``)
    arms in worker B *before* B runs its sink, even though B's own ledger never saw the fetch.

    That converts the canonical split flow (A fetches untrusted, B exfiltrates it) from something the
    post-hoc :class:`~chimera.governance.aggregate_monitor.AggregateMonitor` could only *warn* about into
    a live gate. Honest limit: workers run in parallel, so B's sink can still precede A's ``publish`` by
    a scheduling instant — the aggregate monitor stays as the backstop for that racy tail.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tainted = False

    def publish_tainted(self) -> None:
        with self._lock:
            self._tainted = True

    @property
    def tainted(self) -> bool:
        with self._lock:
            return self._tainted

# Tool-name → capability-kind classification. Overridable, but these are the built-ins.
FETCH_TOOLS = frozenset(
    {"http_get", "fetch_url", "web_search", "arxiv_search", "youtube_transcript", "read_email",
     "calendar_events", "browser", "scrape", "extract", "map", "crawl", "download_media"}
)
EXEC_TOOLS = frozenset({"run_shell", "execute_code", "code_interpreter"})
WRITE_TOOLS = frozenset({"write_file", "edit_file", "apply_patch", "edit_batch"})
READ_TOOLS = frozenset({"read_file", "read_document", "transcribe_audio"})
# Non-idempotent external side effects: firing the SAME call twice does real double harm
# (a duplicate email/message/payment). A retry loop must not re-execute these — see the
# idempotency guard in LedgeredTool (M15-A5). File writes are excluded: rewriting the same
# content is harmless, and they are already covered by verify-or-revert.
SIDE_EFFECT_TOOLS = frozenset(
    {"send_email", "send_message", "http_post", "post_webhook", "create_issue", "send_sms"}
)

_URL_KEYS = ("url", "uri", "link")
_QUERY_KEYS = ("query", "q", "search")
_PATH_KEYS = ("path", "file", "filename", "filepath")
# Content keys include patch/diff shapes: an apply_patch/edit_file carrying its payload under
# ``patch``/``diff``/``new_text`` must not slip past taint detection with an empty ("") content.
_CONTENT_KEYS = ("content", "text", "data", "body", "patch", "diff", "new_text", "new_str", "contents")
_COMMAND_KEYS = ("command", "cmd", "code", "script")

# Files whose tainted content means "self-modification based on untrusted input".
_CODE_SUFFIXES = (".py", ".sh", ".bash", ".zsh", ".js", ".ts", ".rb", ".pl", ".ps1")
# Files another system EXECUTES/INTERPRETS — tainted content here is self-modification too, even
# without a code suffix: a poisoned scheduler config, CI workflow, Dockerfile or shell dotfile runs
# a payload on the next tick/build/login.
_SELF_EXEC_SUFFIXES = (".yml", ".yaml", ".toml", ".service")
_SELF_EXEC_NAMES = frozenset(
    {"jobs.json", "crontab", "dockerfile", "makefile", ".bashrc", ".bash_profile",
     ".profile", ".zshrc", ".zprofile", ".env"}
)


def _is_self_executing(path: str) -> bool:
    """True if a write to ``path`` is code the agent (or another system) will later run."""
    p = path.strip().lower()
    base = p.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return p.endswith(_CODE_SUFFIXES) or p.endswith(_SELF_EXEC_SUFFIXES) or base in _SELF_EXEC_NAMES

# Below this length a tainted snippet is too generic to treat as a flow match (avoids
# escalating on a stray shared word); above it, a verbatim reappearance is a real signal.
_MIN_FLOW_CHARS = 40

# Who asked for a fetch: the user (its target is named in the user's own instruction), the agent
# (it chose the target itself), or unknown (the ledger was never told the instruction). Recorded on
# every fetch whatever the mode; only the ``authority`` mode acts on it.
REQUESTED_BY = ("user", "agent", "unknown")
# What the coarse narrowing keys on. ``provenance`` — the default, and the shipped behaviour to the
# byte — arms it on any tainted event. ``authority`` does not arm it on a tainted fetch or read whose
# target the user named. Measured before it existed: `bench/injection/RESULTS.md` (2026-09-08).
AUTHORITY_MODES = ("provenance", "authority")

# Characters that continue a URL or a path. A target found in the instruction with one of these on
# either side is a FRAGMENT of something longer the user wrote, not the thing the user named.
_TARGET_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789/._-~%?&=#@+:")
# ...except that sentences end: "read config.json." or "see https://a/b?" still names the target.
_SENTENCE_PUNCT = ".?:,;!"


def _normalise(text: str) -> str:
    """Stripped, case-folded, backslashes to slashes — the one form both sides are compared in."""
    return text.strip().lower().replace("\\", "/")


def _target_forms(target: str, workspace: str) -> set[str]:
    """The exact spellings of ``target`` a user could have written.

    As given; with and without a leading ``./``; and, for a path when the workspace is known, the
    workspace-relative and the absolute form. Nothing looser — no basename, no parent, no prefix.
    """
    given = _normalise(target)
    if not given:
        return set()
    forms = {given}
    if "://" in given:
        return forms
    relative = given[2:] if given.startswith("./") else given
    absolute = relative.startswith("/") or (len(relative) > 1 and relative[1] == ":")
    root = workspace.rstrip("/")
    if absolute:
        if root and relative.startswith(root + "/"):
            inside = relative[len(root) + 1 :]
            forms.update({inside, "./" + inside})
    else:
        forms.update({relative, "./" + relative})
        if root:
            forms.add(root + "/" + relative)
    return {form for form in forms if form}


def _named_in(instruction: str, target: str) -> bool:
    """True if ``target`` occurs in ``instruction`` as a whole URL or path, bounded on both sides.

    A match that continues into more URL/path characters is a fragment of something longer the user
    wrote (``config.json`` inside ``src/config.json``; ``https://docs.example`` inside
    ``https://docs.example/upgrade``) and is not a match. Sentence punctuation followed by whitespace
    or the end of the text is a boundary, so ``read config.json.`` still names the file.
    """
    start = instruction.find(target)
    while start != -1:
        end = start + len(target)
        before = instruction[start - 1] if start else ""
        after = instruction[end] if end < len(instruction) else ""
        ends_cleanly = after not in _TARGET_CHARS or (
            after in _SENTENCE_PUNCT
            and (end + 1 >= len(instruction) or instruction[end + 1].isspace())
        )
        if before not in _TARGET_CHARS and ends_cleanly:
            return True
        start = instruction.find(target, start + 1)
    return False


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]


def _excerpt(text: str, limit: int = 120) -> str:
    """The opening of a tainted span, on one line, cut with a visible ellipsis."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _first(args: Mapping[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


@dataclass
class CapabilityEvent:
    """One recorded capability use in a run (the replayable unit)."""

    seq: int
    kind: str  # fetch | read | write | exec | send | escalation
    """What the agent did. There is no ``env`` kind, and there was one for a while with no producer.

    Nothing ever called ``record_env``, and nothing ever would have: the two real channels by which
    an environment variable reaches an agent are already covered by kinds that fire. Reading a
    dotfile is a ``read``, and ``.env`` is in ``_SELF_EXEC_NAMES``, so it is a read the ledger treats
    as self-modifying; running a command that inherits the process environment is an ``exec``. A
    third kind listed here and never emitted describes a category the ledger appears to watch and
    does not, which is the failure this whole file exists to avoid.
    """
    ref: str  # url / path / command / var — the subject of the action
    tainted: bool = False
    detail: str = ""
    provenance: list[str] = field(default_factory=list)  # tainted refs this derived from
    requested_by: str = "unknown"
    """Who asked for the fetch or read this event records: ``user`` / ``agent`` / ``unknown``.

    Derived by :meth:`TaintLedger.requester_of` from the user's own instruction when a fetch or a
    read is recorded; ``unknown`` on every other kind, and on every ledger that was never told the
    instruction. This is the field the 2026-09-08 authorization run found missing: with
    ``kind / ref / tainted / detail / provenance`` there was nowhere to write "the user asked for
    this", so no rule downstream could consult it.
    """


@dataclass
class SequenceAssessment:
    """The sequence-aware verdict for a single action, given the run so far.

    ``action``, ``sources`` and ``span`` are what a person needs in order to answer the question
    this assessment turns into. Measured 2026-09-11 over the 12 rows of `bench/right_hand_governance`
    before they existed: the question was the tool's name and one fixed sentence — six distinct
    strings for twelve different situations, and on the narrowing path the action was empty in all
    twelve. A person shown "run_shell is restricted" cannot tell a force-push named by a poisoned
    page from a `git status` the user asked for; a person shown the command, the page and the line
    that matched can (arXiv 2609.07162, 2609.08472).
    """

    escalate: bool
    decision: Decision
    reason: str = ""
    tainted_refs: list[str] = field(default_factory=list)
    action: str = ""
    """What is about to run, as ``<tool>: <command | path | url | recipient>``."""
    sources: list[str] = field(default_factory=list)
    """Where the taint came from — each ``<ref> (<who asked>)``, oldest first."""
    span: str = ""
    """The tainted text found inside the action itself, when the escalation is a content flow."""


class TaintLedger:
    """Records capability use across a run and tracks tainted artifacts within it."""

    def __init__(
        self,
        *,
        snippet_chars: int = 2000,
        shared: SharedTaint | None = None,
        authority: str = "provenance",
    ) -> None:
        if authority not in AUTHORITY_MODES:
            raise ValueError(
                f"authority={authority!r}: expected one of {', '.join(AUTHORITY_MODES)} "
                "(CHIMERA_TAINT_AUTHORITY)"
            )
        self.events: list[CapabilityEvent] = []
        self.snippet_chars = snippet_chars
        # Which tainted events arm the coarse narrowing — see `run_tainted(for_narrowing=True)`.
        # The default is the shipped behaviour; construction sites pass `settings.taint_authority`.
        self.authority = authority
        self._tainted: set[str] = set()  # normalized tainted refs (urls, paths, hashes)
        self._snippets: list[str] = []  # bounded tainted content, for verbatim-flow detection
        # Optional cross-agent taint view: siblings in a fan-out share one, so a fetch here arms the
        # tainted-tool narrowing in every worker (not just this one). None = a standalone run.
        self._shared = shared
        # The user's own words, normalised, once `set_instruction` has been called — None until
        # then. Only a target that occurs in here, whole, is recorded as requested by the user.
        self._instruction: str | None = None
        self._workspace = ""

    # --- recording -------------------------------------------------------------------

    def _add(self, kind: str, ref: str, *, tainted: bool = False, detail: str = "",
             provenance: list[str] | None = None, requested_by: str = "unknown") -> CapabilityEvent:
        event = CapabilityEvent(
            len(self.events), kind, ref, tainted, detail, provenance or [], requested_by
        )
        self.events.append(event)
        if tainted and self._shared is not None:
            # Publish to siblings the instant this run consumes untrusted content, so their narrowing
            # arms before they can sink it — the live half of the cross-agent gate.
            self._shared.publish_tainted()
        return event

    def _label(self, requested_by: str | None, target: str) -> str:
        """An explicit label, checked; else the one derived from the instruction."""
        if requested_by is None:
            return self.requester_of(target)
        if requested_by not in REQUESTED_BY:
            raise ValueError(
                f"requested_by={requested_by!r}: expected one of {', '.join(REQUESTED_BY)}"
            )
        return requested_by

    def record_fetch(
        self, source: str, content: str = "", *, requested_by: str | None = None
    ) -> str:
        """Record an external fetch; its source and content become tainted. Returns the hash.

        ``requested_by`` is derived from ``source`` when not given (see :meth:`requester_of`): the
        one production caller, ``ledger_tool``, passes the URL or the path the tool fetched, so a
        page or a file the user named in the instruction reads ``user``. Tainted either way —
        authority is not trust, and the content is still external.
        """
        digest = _hash(content) if content else ""
        source = (source or "external").strip()
        who = self._label(requested_by, source)
        self._tainted.add(source)
        if digest:
            self._tainted.add(digest)
        if content:
            self._snippets.append(content[: self.snippet_chars])
        self._add(
            "fetch", source, tainted=True, detail=f"sha256:{digest}" if digest else "",
            requested_by=who,
        )
        return digest

    def record_read(self, path: str, *, requested_by: str | None = None) -> CapabilityEvent:
        """Record a file read; tainted if the path is (a tainted write landed there earlier).

        Labelled like a fetch, because a read is tainted by its path and the ``authority`` mode
        reads the label on tainted reads too.
        """
        path = (path or "").strip()
        return self._add(
            "read", path, tainted=self.is_tainted(path), requested_by=self._label(requested_by, path)
        )

    def record_write(self, path: str, content: str = "") -> CapabilityEvent:
        """Record a file write; the path inherits taint if the content came from a tainted source."""
        path = (path or "").strip()
        tainted, refs = self._content_is_tainted(content)
        if tainted:
            self._tainted.add(path)
        return self._add("write", path, tainted=tainted, provenance=refs)

    def record_exec(self, command: str) -> CapabilityEvent:
        _, refs = self._content_is_tainted(command)
        return self._add("exec", command[:200], tainted=bool(refs), provenance=refs)

    def record_send(self, tool: str, target: str = "") -> CapabilityEvent:
        """Record a non-idempotent OUTBOUND side effect (send_email/http_post/...).

        This is an exfiltration SINK: the aggregate cross-agent monitor needs to see it to catch a
        split flow (agent A fetches untrusted content, agent B sends it out). Without a recorded
        event these channels were invisible to sink detection, and the split-exfil the monitor
        exists to catch passed clean.
        """
        return self._add("send", (target or tool).strip() or tool)

    def record_escalation(self, tool: str, assessment: SequenceAssessment) -> CapabilityEvent:
        return self._add(
            "escalation", tool, tainted=True, detail=assessment.reason,
            provenance=list(assessment.tainted_refs),
        )

    # --- authority: who asked for a fetch ----------------------------------------------

    def set_instruction(self, text: str, *, workspace: str | Path | None = None) -> None:
        """Tell the ledger the user's own instruction, so a fetch can be recorded as ``user``.

        Called once where a run starts with the task known — `chimera solve`, the batch and crew
        commands, the API's run/turn/lifecycle/crew assembly, the cron jobs and the kanban lanes.
        Replaces an earlier instruction rather than accumulating. ``workspace`` lets a path the
        agent gives absolutely match the relative form the user wrote, and the other way round. A
        ledger never told the instruction labels every fetch ``unknown``, which the narrowing treats
        exactly as it always did.
        """
        self._instruction = _normalise(text or "")
        self._workspace = _normalise(str(workspace)) if workspace is not None else ""

    @property
    def instruction(self) -> str | None:
        """The normalised instruction, or None if the ledger was never told one."""
        return self._instruction

    def requester_of(self, target: str | None) -> str:
        """``user`` if the user named ``target`` — a URL or a path — whole, in the instruction.

        Strict on purpose, and this docstring says why because the temptation is real: a basename
        match (``config.json`` for ``src/config.json``) or a prefix match (``https://docs.example``
        for ``https://docs.example/upgrade``) would label as the user's request exactly the file an
        attacker plants beside the one the user asked for — and the ``authority`` mode would then
        switch the narrowing off for it. A miss fails toward ``agent``, which is the previous
        behaviour. ``unknown`` when no instruction was set; ``agent`` for an empty target, because a
        search query or a bare tool name is not something the user can have named.
        """
        if self._instruction is None:
            return "unknown"
        if not target:
            return "agent"
        forms = _target_forms(target, self._workspace)
        return "user" if any(_named_in(self._instruction, form) for form in forms) else "agent"

    # --- taint queries ---------------------------------------------------------------

    def run_tainted(self, *, for_narrowing: bool = False) -> bool:
        """True if this run has consumed ANY untrusted content (a tainted event exists).

        Coarse by design: it gates *provenance* of durable artifacts (memories, learned
        skills) produced during the run — the "Zombie Agents" self-reinforcing-injection
        surface — not per-action policy, which stays with :func:`assess_action`.

        ``for_narrowing`` is the question the taint-adaptive allowlist asks, and it is the ONE
        place the ``authority`` mode makes a difference: under it, a tainted fetch or read whose
        target the user named (``requested_by == "user"``) does not arm the narrowing. Without the
        flag — durable provenance, pause-on-taint, the query-string rule in :func:`assess_action` —
        the answer is unchanged, because a value the user asked for is still an external value.
        Under ``provenance`` the flag changes nothing.

        Under a shared cross-agent view, this is also True when a *sibling* worker consumed untrusted
        content — so this worker's dangerous-tool narrowing arms against a split flow it never saw.
        A fetch is published to that view whoever asked for it, and the view is one bit with no
        label, so in a fan-out the ``authority`` mode changes nothing for anyone — the worker that
        fetched included. Measured, not designed: the first test of it expected otherwise.
        """
        overlook_users_own = for_narrowing and self.authority == "authority"
        for event in self.events:
            if not event.tainted:
                continue
            if (
                overlook_users_own
                and event.kind in ("fetch", "read")
                and event.requested_by == "user"
            ):
                continue
            return True
        return self._shared is not None and self._shared.tainted

    def taint_sources(self, *, for_narrowing: bool = False) -> list[str]:
        """Which untrusted reads armed this run, each as ``<ref> (<who asked>)``, oldest first.

        The same rule as :meth:`run_tainted` — including the ``authority`` overlook when
        ``for_narrowing`` is set — so the sources named on a question are exactly the ones that made
        it a question. A run tainted only through a sibling worker names the sibling, because the
        shared view is one bit and carries no ref.
        """
        overlook_users_own = for_narrowing and self.authority == "authority"
        out: list[str] = []
        for event in self.events:
            if not event.tainted or event.kind not in ("fetch", "read"):
                continue
            if overlook_users_own and event.requested_by == "user":
                continue
            who = {"user": "as the user asked", "agent": "fetched by the agent"}.get(
                event.requested_by, "requester unknown"
            )
            label = f"{event.ref} ({who})"
            if label not in out:
                out.append(label)
        if not out and self._shared is not None and self._shared.tainted:
            out.append("a sibling worker's untrusted read (shared view)")
        return out

    def describe_refs(self, refs: list[str]) -> list[str]:
        """The sources behind a list of tainted refs, ``<ref> (<who asked>)`` each.

        A content-flow ref is a ``sha256:`` digest; it is mapped back to the fetch that produced it
        so the question names the page, not the hash. A ref that is itself a URL or a path is named
        as it is.
        """
        out: list[str] = []
        for ref in refs:
            label = ref
            for event in self.events:
                if not event.tainted:
                    continue
                if event.ref == ref or (ref.startswith("sha256:") and event.detail == ref):
                    who = {"user": "as the user asked", "agent": "fetched by the agent"}.get(
                        event.requested_by, "requester unknown"
                    )
                    label = f"{event.ref} ({who})"
                    break
            if label not in out:
                out.append(label)
        return out

    def is_tainted(self, ref: str) -> bool:
        return bool(ref) and ref.strip() in self._tainted

    def tainted_refs_in(self, text: str) -> list[str]:
        """Which tainted refs (urls/paths) appear verbatim in the given text."""
        if not text:
            return []
        return sorted(ref for ref in self._tainted if ref and ref in text)

    def _content_is_tainted(self, text: str) -> tuple[bool, list[str]]:
        """True if text references a tainted ref, or a tainted fetch flowed into it verbatim."""
        tainted, refs, _ = self._tainted_span(text)
        return tainted, refs

    def _tainted_span(self, text: str) -> tuple[bool, list[str], str]:
        """As :meth:`_content_is_tainted`, plus the tainted text that was found inside ``text``.

        The span is what the question shows a person: the ref that appears in the command, or the
        opening of the fetched content that flowed into it verbatim. Bounded, because a question is
        read on a phone as often as on a terminal.
        """
        if not text:
            return False, [], ""
        refs = self.tainted_refs_in(text)
        if refs:
            return True, refs, refs[0]
        for snippet in self._snippets:
            probe = snippet.strip()
            if len(probe) >= _MIN_FLOW_CHARS and probe in text:
                return True, [f"sha256:{_hash(snippet)}"], _excerpt(probe)
        return False, [], ""

    # --- replay / summary (issue #2) -------------------------------------------------

    def capability_summary(self) -> dict[str, Any]:
        """A compact per-run capability report for a reviewer."""
        kinds: dict[str, int] = {}
        for event in self.events:
            kinds[event.kind] = kinds.get(event.kind, 0) + 1
        return {
            "events": len(self.events),
            "by_kind": kinds,
            "fetched": [e.ref for e in self.events if e.kind == "fetch"],
            "tainted_writes": [e.ref for e in self.events if e.kind == "write" and e.tainted],
            "escalations": [
                {"tool": e.ref, "reason": e.detail} for e in self.events if e.kind == "escalation"
            ],
        }

    def dump(self, path: Path) -> None:
        """Append the capability events to a JSONL replay record."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for event in self.events:
                handle.write(json.dumps(asdict(event)) + "\n")


def assess_action(
    tool_name: str,
    args: Mapping[str, Any],
    ledger: TaintLedger,
    *,
    exec_tools: frozenset[str] = EXEC_TOOLS,
    write_tools: frozenset[str] = WRITE_TOOLS,
    fetch_tools: frozenset[str] = FETCH_TOOLS,
) -> SequenceAssessment:
    """Sequence-aware review: escalate when an action executes/self-modifies on tainted input.

    Precise by design (low false-positive): only escalates when a tainted ref or a verbatim
    tainted-content flow is actually present in the action — not merely because *some* untrusted
    content was fetched earlier this run. Returns an ALLOW assessment when nothing is tainted.
    """
    if tool_name in exec_tools:
        command = _first(args, _COMMAND_KEYS)
        tainted, refs, span = ledger._tainted_span(command)
        if tainted:
            sources = ledger.describe_refs(refs)
            return SequenceAssessment(
                True, Decision.REVIEW,
                f"executes an artifact derived from untrusted input ({', '.join(refs)}) — "
                f"the command contains text from {'; '.join(sources) or 'an untrusted read'}",
                refs,
                action=f"{tool_name}: {_excerpt(command, 300)}",
                sources=sources,
                span=span,
            )
    if tool_name in write_tools:
        path = _first(args, _PATH_KEYS)
        content = _first(args, _CONTENT_KEYS)
        tainted, refs, span = ledger._tainted_span(content)
        if tainted and _is_self_executing(path):
            sources = ledger.describe_refs(refs)
            return SequenceAssessment(
                True, Decision.REVIEW,
                f"writes untrusted content into an executable/interpreted file {path!r} ({', '.join(refs)}) — "
                f"the content comes from {'; '.join(sources) or 'an untrusted read'}",
                refs,
                action=f"{tool_name}: {path}",
                sources=sources,
                span=span,
            )
    if tool_name in fetch_tools and ledger.run_tainted():
        # The exfiltration that got through every configuration: `http_get` is a fetch tool, so the
        # narrowing set — which names sinks — never looked at it, and a GET carries data out in its
        # query string as easily as a POST carries it in a body. `bench/injection` measured
        # `asr_exfil` at 0.5 with everything else at 0.0 because of exactly this row.
        #
        # A heuristic, and registered as one (`bench/injection/PREREGISTRATION_attended.md`, Arm 2):
        # it cannot tell `?data=SECRET` from `?v=2.4`, so a legitimate query-string GET made after an
        # untrusted read becomes a question rather than a fetch. That cost is measured on two benign
        # rows added to the corpus for the purpose, not assumed away.
        url = _first(args, _URL_KEYS)
        parts = urlsplit(url) if url else None
        if parts is not None and parts.query:
            sources = ledger.taint_sources()
            return SequenceAssessment(
                True, Decision.REVIEW,
                f"fetches {parts.netloc or url!r} with a query string while this run holds untrusted "
                f"content from {'; '.join(sources) or 'an untrusted read'} — a GET can carry data out "
                f"as easily as a POST",
                [],
                action=f"{tool_name}: {_excerpt(url, 300)}",
                sources=sources,
                span=_excerpt(parts.query),
            )
    return SequenceAssessment(False, Decision.ALLOW)
