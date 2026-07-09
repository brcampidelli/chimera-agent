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
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from chimera.governance.policy import Decision
from chimera.telemetry import get_logger

_log = get_logger("governance.ledger")

# Tool-name → capability-kind classification. Overridable, but these are the built-ins.
FETCH_TOOLS = frozenset(
    {"http_get", "fetch_url", "web_search", "arxiv_search", "youtube_transcript", "read_email",
     "calendar_events", "browser", "scrape", "extract", "map", "crawl", "download_media"}
)
EXEC_TOOLS = frozenset({"run_shell", "execute_code", "code_interpreter"})
WRITE_TOOLS = frozenset({"write_file", "edit_file", "apply_patch"})
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
_CONTENT_KEYS = ("content", "text", "data", "body")
_COMMAND_KEYS = ("command", "cmd", "code", "script")

# Files whose tainted content means "self-modification based on untrusted input".
_CODE_SUFFIXES = (".py", ".sh", ".bash", ".zsh", ".js", ".ts", ".rb", ".pl", ".ps1")

# Below this length a tainted snippet is too generic to treat as a flow match (avoids
# escalating on a stray shared word); above it, a verbatim reappearance is a real signal.
_MIN_FLOW_CHARS = 40


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]


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
    kind: str  # fetch | read | write | exec | env | escalation
    ref: str  # url / path / command / var — the subject of the action
    tainted: bool = False
    detail: str = ""
    provenance: list[str] = field(default_factory=list)  # tainted refs this derived from


@dataclass
class SequenceAssessment:
    """The sequence-aware verdict for a single action, given the run so far."""

    escalate: bool
    decision: Decision
    reason: str = ""
    tainted_refs: list[str] = field(default_factory=list)


class TaintLedger:
    """Records capability use across a run and tracks tainted artifacts within it."""

    def __init__(self, *, snippet_chars: int = 2000) -> None:
        self.events: list[CapabilityEvent] = []
        self.snippet_chars = snippet_chars
        self._tainted: set[str] = set()  # normalized tainted refs (urls, paths, hashes)
        self._snippets: list[str] = []  # bounded tainted content, for verbatim-flow detection

    # --- recording -------------------------------------------------------------------

    def _add(self, kind: str, ref: str, *, tainted: bool = False, detail: str = "",
             provenance: list[str] | None = None) -> CapabilityEvent:
        event = CapabilityEvent(len(self.events), kind, ref, tainted, detail, provenance or [])
        self.events.append(event)
        return event

    def record_fetch(self, source: str, content: str = "") -> str:
        """Record an external fetch; its source and content become tainted. Returns the hash."""
        digest = _hash(content) if content else ""
        source = (source or "external").strip()
        self._tainted.add(source)
        if digest:
            self._tainted.add(digest)
        if content:
            self._snippets.append(content[: self.snippet_chars])
        self._add("fetch", source, tainted=True, detail=f"sha256:{digest}" if digest else "")
        return digest

    def record_read(self, path: str) -> CapabilityEvent:
        path = (path or "").strip()
        return self._add("read", path, tainted=self.is_tainted(path))

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

    def record_env(self, var: str) -> CapabilityEvent:
        return self._add("env", (var or "").strip())

    def record_escalation(self, tool: str, assessment: SequenceAssessment) -> CapabilityEvent:
        return self._add(
            "escalation", tool, tainted=True, detail=assessment.reason,
            provenance=list(assessment.tainted_refs),
        )

    # --- taint queries ---------------------------------------------------------------

    def run_tainted(self) -> bool:
        """True if this run has consumed ANY untrusted content (a tainted event exists).

        Coarse by design: it gates *provenance* of durable artifacts (memories, learned
        skills) produced during the run — the "Zombie Agents" self-reinforcing-injection
        surface — not per-action policy, which stays with :func:`assess_action`.
        """
        return any(event.tainted for event in self.events)

    def is_tainted(self, ref: str) -> bool:
        return bool(ref) and ref.strip() in self._tainted

    def tainted_refs_in(self, text: str) -> list[str]:
        """Which tainted refs (urls/paths) appear verbatim in the given text."""
        if not text:
            return []
        return sorted(ref for ref in self._tainted if ref and ref in text)

    def _content_is_tainted(self, text: str) -> tuple[bool, list[str]]:
        """True if text references a tainted ref, or a tainted fetch flowed into it verbatim."""
        if not text:
            return False, []
        refs = self.tainted_refs_in(text)
        if refs:
            return True, refs
        for snippet in self._snippets:
            probe = snippet.strip()
            if len(probe) >= _MIN_FLOW_CHARS and probe in text:
                return True, [f"sha256:{_hash(snippet)}"]
        return False, []

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
) -> SequenceAssessment:
    """Sequence-aware review: escalate when an action executes/self-modifies on tainted input.

    Precise by design (low false-positive): only escalates when a tainted ref or a verbatim
    tainted-content flow is actually present in the action — not merely because *some* untrusted
    content was fetched earlier this run. Returns an ALLOW assessment when nothing is tainted.
    """
    if tool_name in exec_tools:
        command = _first(args, _COMMAND_KEYS)
        tainted, refs = ledger._content_is_tainted(command)
        if tainted:
            return SequenceAssessment(
                True, Decision.REVIEW,
                f"executes an artifact derived from untrusted input ({', '.join(refs)})",
                refs,
            )
    if tool_name in write_tools:
        path = _first(args, _PATH_KEYS)
        content = _first(args, _CONTENT_KEYS)
        tainted, refs = ledger._content_is_tainted(content)
        if tainted and path.endswith(_CODE_SUFFIXES):
            return SequenceAssessment(
                True, Decision.REVIEW,
                f"writes untrusted content into an executable file {path!r} ({', '.join(refs)})",
                refs,
            )
    return SequenceAssessment(False, Decision.ALLOW)
