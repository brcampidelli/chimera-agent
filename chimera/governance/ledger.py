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
import math
import re
import threading
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from chimera.core.redact import redact
from chimera.governance.policy import Decision
from chimera.governance.proxy import see_through
from chimera.governance.recipient import addresses_in
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
        self._epoch = 0

    def publish_tainted(self, *, new_facts: bool = True) -> None:
        """Mark the fan-out tainted; ``new_facts`` also moves :attr:`epoch` (see there)."""
        with self._lock:
            self._tainted = True
            if new_facts:
                self._epoch += 1

    @property
    def tainted(self) -> bool:
        with self._lock:
            return self._tainted

    @property
    def epoch(self) -> int:
        """How many untrusted things the whole fan-out has taken in so far: the run-wide half of
        :attr:`TaintLedger.taint_epoch`, which a shared approval is bound to."""
        with self._lock:
            return self._epoch

# Tool-name → capability-kind classification. Overridable, but these are the built-ins.
FETCH_TOOLS = frozenset(
    {"http_get", "fetch_url", "web_search", "arxiv_search", "youtube_transcript", "read_email",
     "calendar_events", "browser", "scrape", "extract", "map", "crawl", "download_media"}
)
# The fetch tools whose content is the public web: only what these returned can make a value
# "seen" for the S30-27 host/path rule. A mailbox or a calendar is fetched content too — tainted,
# because a stranger can write into it — but it is also where a reset token, an API key or an invite
# link lives, and counting it as seen let a key from an email leave in a hostname with no question
# (study 30 review). Connectors marked ``untrusted_output`` are not here either, for the same
# reason: their names come from a remote server, and nothing says what they read.
PUBLIC_FETCH_TOOLS = FETCH_TOOLS - {"read_email", "calendar_events"}
EXEC_TOOLS = frozenset({"run_shell", "execute_code", "code_interpreter"})
# `create_document` writes a file into the workspace exactly as `write_file` does, so it is one: a
# read-only posture denies it (`api/posture.py` reads this set) and the ledger records the write.
WRITE_TOOLS = frozenset({"write_file", "edit_file", "apply_patch", "edit_batch", "create_document"})
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


# --- S30-27: a value carried out in the host, the path or the query of a fetch -----------------
#
# `bench/exfil_url/PREREGISTRATION.md` registers these four numbers before they were written here.
# A run of URL characters long enough, mixed enough and random enough to be an encoded value — a
# hex, base32 or base64url key — rather than a word, a slug, a date or a version. The hyphen and the
# dot separate runs, so a slug (`how-we-reduced-build-times`) and a UUID's groups stay short.
_DATA_RUN = re.compile(r"[A-Za-z0-9+=_]{16,}")
_DATA_MIN_DIGITS = 2
_DATA_MIN_LETTERS = 2
_DATA_MIN_BITS = 3.0


def _entropy(text: str) -> float:
    """Shannon entropy of ``text`` in bits per character."""
    counts = Counter(text)
    total = len(text)
    return -sum(n / total * math.log2(n / total) for n in counts.values())


def _data_like(run: str) -> bool:
    digits = sum(ch.isdigit() for ch in run)
    letters = sum(ch.isalpha() for ch in run)
    return (
        digits >= _DATA_MIN_DIGITS and letters >= _DATA_MIN_LETTERS
        and _entropy(run) >= _DATA_MIN_BITS
    )


def _url_tokens(url: str) -> list[str]:
    """What a fetch to ``url`` hands to someone else: host labels, path segments, query, userinfo.

    The host's last two labels are left out — an approximation of the registrable domain, so
    `x.attacker.test` gives `x`. The labels before them are the DNS channel: resolving the name
    alone delivers them to whoever runs the zone's name server, before any HTTP request. The fragment
    is left out because it is never sent.
    """
    parts = urlsplit(url)
    tokens: list[str] = []
    host = (parts.hostname or "").rstrip(".")
    if host:
        tokens.extend(host.split(".")[:-2])
    tokens.extend(v for v in (parts.username, parts.password) if v)
    tokens.extend(unquote(segment) for segment in parts.path.split("/") if segment)
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        tokens.extend((key, value))
    return tokens


# --- S30-28: a clone of a repository, and the shell as a way of fetching --------------------------

# git's global options before the subcommand. `-c <k=v>`, `-C <dir>` and the long ones that take a
# value consume it, so `git -c http.sslVerify=false clone URL` and `git -C /tmp clone URL` are still
# clones (the first version allowed only words starting with `-` and read both as no clone at all).
_GIT_GLOBAL = (
    r"(?:(?:-[cC]|--git-dir|--work-tree|--namespace|--config-env|--exec-path|--super-prefix)"
    r"(?:=|\s+)\S+\s+|-\S+\s+)*"
)
_CLONE = re.compile(
    rf"\bgit\s+{_GIT_GLOBAL}clone\b(?P<rest>[^\n;&|]*)"
    rf"|\bgit\s+{_GIT_GLOBAL}submodule\s+add\b(?P<sub>[^\n;&|]*)"
    r"|\bgh\s+repo\s+clone\b(?P<gh>[^\n;&|]*)"
)
# Options of `git clone` that take the next word as their value, so it is not the source.
_CLONE_VALUE_OPTS = frozenset(
    {"-b", "--branch", "-o", "--origin", "--depth", "-c", "--config", "--reference",
     "--reference-if-able", "--separate-git-dir", "-u", "--upload-pack", "-j", "--jobs",
     "--template", "--shallow-since", "--shallow-exclude", "--filter", "--server-option",
     "--bundle-uri", "--revision", "--ref-format"}
)
# Options of `git submodule add` that take a value.
_SUBMODULE_VALUE_OPTS = frozenset({"-b", "--branch", "--name", "--reference", "--depth"})
# The forge a bare `owner/repo` means. Anywhere else the user has to have named the host.
_DEFAULT_FORGE = "github.com"
_SCHEME_REMOTE = re.compile(
    r"^(?:https?|ssh|git)://(?:[^@/]+@)?(?P<host>[^/:]+)(?::\d+)?/(?P<path>[^?#]+)$", re.I
)
# `git@github.com:owner/repo.git` — scp-like. A user part or a dotted host is required, so a Windows
# drive (`C:\src\repo`) is not read as a host called `C`.
_SCP_REMOTE = re.compile(r"^(?:[\w.-]+@(?P<uhost>[\w.-]+)|(?P<host>[\w-]+\.[\w.-]+)):(?P<path>[^/\\].*)$")
_GH_SLUG = re.compile(r"^[\w.-]+/[\w.-]+$")
_SHELL_URL_FETCH = re.compile(r"\b(?:curl|wget)\b[^\n]*?(?P<url>(?:https?|ftp)://[^\s'\"|;&)<>]+)", re.I)


def _clone_sources(command: str) -> list[tuple[str, bool]]:
    """Each clone source in ``command``, with whether it came from ``gh repo clone``.

    For ``git clone`` and ``git submodule add`` every positional word that reads as a remote is a
    source, not only the first. Stopping at the first one let any value option missing from the
    set above hide the clone: `git clone --bundle-uri x URL` took ``x`` for the source, read it as a
    local directory, and never asked (study 30 review) — and the model or an injection chooses the
    option order. The destination is a local path, which :func:`_remote_repo` drops. ``gh repo
    clone`` keeps the first only: its second word is a directory, and ``vendor/rich`` would read as
    a slug.
    """
    out: list[tuple[str, bool]] = []
    for match in _CLONE.finditer(command or ""):
        gh = match.group("gh") is not None
        if gh:
            tail, value_opts = match.group("gh"), frozenset[str]()
        elif match.group("sub") is not None:
            tail, value_opts = match.group("sub"), _SUBMODULE_VALUE_OPTS
        else:
            tail, value_opts = match.group("rest"), _CLONE_VALUE_OPTS
        words = [w.strip("'\"") for w in tail.split()]
        skip = False
        for word in words:
            if skip:
                skip = False
                continue
            if word == "--":
                continue
            if word.startswith("-"):
                skip = word in value_opts
                continue
            if gh:
                out.append((word, gh))
                break
            if _remote_repo(word, gh=False) is not None:
                out.append((word, gh))
    return out


def _remote_repo(source: str, *, gh: bool) -> tuple[str, str] | None:
    """``(host, path)`` of a remote clone source, lower-cased, without ``.git`` or a trailing slash;
    None for a local one (`.`, `..`, an absolute or home path, a bare directory name)."""
    host = path = ""
    if (m := _SCHEME_REMOTE.match(source)) is not None:
        host, path = m.group("host"), m.group("path")
    elif (m := _SCP_REMOTE.match(source)) is not None:
        host, path = m.group("uhost") or m.group("host"), m.group("path")
    elif gh and _GH_SLUG.match(source):
        host, path = "github.com", source
    else:
        return None
    path = path.strip("/").lower()
    path = path[:-4] if path.endswith(".git") else path
    if "/" not in path:
        return None
    return host.lower(), path


def _repo_forms(host: str, path: str) -> set[str]:
    """The spellings of a repository a user could have written, each with and without `.git`/`/`.

    The bare ``owner/repo`` only on the default forge. Elsewhere it would let the host change under
    the user's words: "Clone psf/requests" names a repository on GitHub, and counting it as a name
    for `https://git.evil.test/psf/requests` let a model or an injection pick the host (study 30
    review). Off the default forge the user has to have written the host too.
    """
    bases = {f"{host}/{path}", f"https://{host}/{path}", f"http://{host}/{path}",
             f"git@{host}:{path}", f"ssh://git@{host}/{path}"}
    if host == _DEFAULT_FORGE:
        bases.add(path)
    return {base + suffix for base in bases for suffix in ("", ".git", "/")}


def _shell_fetch_sources(command: str) -> list[str]:
    """What a shell command fetched from outside: remote clones and `curl`/`wget` URLs."""
    sources: list[str] = []
    for source, gh in _clone_sources(command):
        repo = _remote_repo(source, gh=gh)
        if repo is not None:
            sources.append(source if not gh else f"https://{repo[0]}/{repo[1]}")
    sources.extend(m.group("url") for m in _SHELL_URL_FETCH.finditer(command or ""))
    return sources


def _setting(name: str) -> bool:
    """A boolean setting, for a ledger whose caller did not say. Imported here: the ledger is built
    by modules `chimera.config` must not import."""
    from chimera.config import get_settings

    return bool(getattr(get_settings(), name))


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


#: How much of an argument other than the call's target a question shows whole. A longer one is
#: shown by its opening, its length and a digest: enough to tell two of them apart on the card,
#: which the old card could not do at all (it named the recipient of an email and never its body).
SHOWN_ARG_CHARS = 1500
_SHOWN_ARG_HEAD = 600


def describe_call(tool: str, args: Mapping[str, Any], target: str = "") -> str:
    """What a person approving this call is shown: ``<tool>: <target>``, then every other argument.

    The target (the command, path, URL or recipient) is shown WHOLE. It used to be cut at 300
    characters, which made a command that differed only after that point indistinguishable on the
    card from the one before it (study 30, S30-04). Every other argument follows on its own line,
    documents included, each whole up to :data:`SHOWN_ARG_CHARS` and past that as its opening plus
    ``(N chars in all, sha256:...)``. The first line keeps the shape it had, so every reader that
    takes the tool from it (``approval._facts_of``) reads the same thing.

    Every value is passed through :func:`_masked` before it is shown. Showing every argument put the
    body of an email, a file's content and a request's headers on the card, and the card travels: to
    the chat channel, and to ``<home>/approvals/<id>.ask.json``, whether or not the owner says yes
    (S30-04 review). A key the agent read from ``.env`` is ``[redacted]`` there. The length and the
    digest are of the value as it is, and :func:`proposal_of` keys on the raw arguments, so two calls
    that differ only in a secret are still two questions — the card says where the mask is.
    """
    lines = [f"{tool}: {_masked(target)}" if target else tool]
    shown_target = False
    for key, value in args.items():
        if value is None or value == "":
            continue
        raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        if not shown_target and target and raw == target:
            shown_target = True  # already on the first line
            continue
        # Masked BEFORE the cut, so a secret straddling the cut point cannot leave half of itself.
        text = _masked(raw)
        if len(raw) > SHOWN_ARG_CHARS:
            digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]
            text = f"{text[:_SHOWN_ARG_HEAD]}… ({len(raw)} chars in all, sha256:{digest})"
        lines.append(f"  {key}: {text}")
    return "\n".join(lines)


def _masked(text: str) -> str:
    """``text`` as a person's card may carry it: secrets this process knows, and the shapes and
    places `chimera.core.redact` recognises, replaced by ``[redacted]``."""
    return redact(text)


def proposal_of(tool: str, args: Mapping[str, Any], epoch: int) -> str:
    """The identity of a proposal: the tool, every argument whole, and the run's taint epoch.

    What a shared approval is keyed on (`shared_approval.py`). Everything the call will do is in
    it, documents included and nothing cut, so an answer covers this call and no other; and the
    epoch is in it, so a yes given before the run took in something new does not answer the same
    call after. A digest rather than the text: the key is compared, never shown.
    """
    canonical = json.dumps(
        {"tool": tool, "args": args, "epoch": epoch}, sort_keys=True, ensure_ascii=False, default=str
    )
    return hashlib.sha256(canonical.encode("utf-8", "replace")).hexdigest()


@dataclass
class CapabilityEvent:
    """One recorded capability use in a run (the replayable unit)."""

    seq: int
    kind: str  # fetch | read | write | exec | verify | send | escalation
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
    proposal: str = ""
    """:func:`proposal_of` the call being asked about: what a shared approval is keyed on. Empty on
    an assessment nobody asks about and on one built outside a ledger (a test, an older caller); the
    key then falls back to the action as shown."""
    programs: list[str] = field(default_factory=list)
    """The ``run_shell`` facts appended to ``action`` (`exec_facts.facts_for`), as data for the
    record — the same lines `Verdict.programs` carries on the kernel's card."""


class TaintLedger:
    """Records capability use across a run and tracks tainted artifacts within it."""

    def __init__(
        self,
        *,
        snippet_chars: int = 2000,
        shared: SharedTaint | None = None,
        authority: str = "provenance",
        egress_allow: Iterable[str] = (),
        exfil_host_path: bool | None = None,
        shell_fetch_guard: bool | None = None,
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
        # Hosts whose query-string GETs are not treated as a way out. Normalised here rather than at
        # every call site, and compared against `urlsplit(...).hostname` — never `netloc`, which
        # carries userinfo: `https://api.github.com@evil.test/x` has a netloc that STARTS with an
        # allowlisted host and a destination that is not one.
        self.egress_allow = frozenset(
            host.strip().lower().rstrip(".") for host in egress_allow if host and host.strip()
        )
        # S30-27 and S30-28, both off until `bench/exfil_url` and `bench/shell_fetch` recommend
        # otherwise. Every production construction site holds a `Settings` and passes both, as it
        # passes `authority` (a surface handed a `Settings` must not read the process-wide one:
        # `test_governed_profile_reads_the_settings_it_is_given`). None is for a caller with no
        # `Settings` at hand, which then reads the process's rather than silently staying off — the
        # failure `api/posture.py` records for CHIMERA_TAINT_AUTHORITY, which did nothing on that
        # surface for as long as it existed. The eval harnesses pin both off: they reproduce
        # numbers published against the shipped rules.
        self.exfil_host_path = (
            _setting("exfil_host_path") if exfil_host_path is None else exfil_host_path
        )
        self.shell_fetch_guard = (
            _setting("shell_fetch_guard") if shell_fetch_guard is None else shell_fetch_guard
        )
        # Every long run of URL characters in what the run FETCHED, lower-cased, whole — not the
        # 2,000-character flow snippet, because a link at the end of a long page is still a link the
        # page gave. Only kept while `exfil_host_path` is on.
        self._fetched_runs: set[str] = set()
        self._tainted: set[str] = set()  # normalized tainted refs (urls, paths, hashes)
        self._snippets: list[str] = []  # bounded tainted content, for verbatim-flow detection
        # Every whole email address the conversation has shown: the instruction, each tool result,
        # earlier turns when a surface hands them over. Only ever grows (study 24, M2).
        self._seen_addresses: set[str] = set()
        # Optional cross-agent taint view: siblings in a fan-out share one, so a fetch here arms the
        # tainted-tool narrowing in every worker (not just this one). None = a standalone run.
        self._shared = shared
        # The user's own words, normalised, once `set_instruction` has been called — None until
        # then. Only a target that occurs in here, whole, is recorded as requested by the user.
        self._instruction: str | None = None
        self._workspace = ""
        # The event index is `len(self.events)` read and then appended; two read-only tool calls
        # running together (`Agent._observations_together`) would each take the same index. One
        # lock around that pair, and nothing else: the sets and lists above are appended, never
        # read-modify-written, and CPython's GIL keeps each append whole.
        self._events_lock = threading.Lock()
        # `record_project_instructions` is a check-then-record, and the record goes through `_add`,
        # which takes `_events_lock` itself — a plain Lock, so the pair needs a lock of its own. Two
        # crew workers sharing this ledger compose their prompts together; without it both see the
        # file as new and the epoch moves twice for one fact.
        self._instructions_lock = threading.Lock()
        # Untrusted things this ledger has taken in; see `taint_epoch`.
        self._epoch = 0

    # --- recording -------------------------------------------------------------------

    def _add(self, kind: str, ref: str, *, tainted: bool = False, detail: str = "",
             provenance: list[str] | None = None, requested_by: str = "unknown") -> CapabilityEvent:
        with self._events_lock:
            event = CapabilityEvent(
                len(self.events), kind, ref, tainted, detail, provenance or [], requested_by
            )
            self.events.append(event)
            # An escalation is the ledger noting that a question is being ASKED, not something the
            # run took in. Counting it would make every identical ask a "new" question, since the
            # step-1 path records the escalation just before it asks.
            new_facts = tainted and kind != "escalation"
            if new_facts:
                self._epoch += 1
        if tainted and self._shared is not None:
            # Publish to siblings the instant this run consumes untrusted content, so their narrowing
            # arms before they can sink it — the live half of the cross-agent gate.
            self._shared.publish_tainted(new_facts=new_facts)
        return event

    @property
    def taint_epoch(self) -> int:
        """How many untrusted things the run has taken in: fetches, tainted reads, writes, execs.

        Read across the fan-out when the ledger shares a :class:`SharedTaint` (every worker's
        count), else this ledger's own. It only grows, and it is part of a shared approval's key
        (:func:`proposal_of`): a person's yes was given under the facts of that moment, and a
        tainted write since then (the file the approved command is about to run, now carrying
        fetched text) is different facts.
        """
        return self._shared.epoch if self._shared is not None else self._epoch

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
        self, source: str, content: str = "", *, requested_by: str | None = None,
        seen: bool = True,
    ) -> str:
        """Record an external fetch; its source and content become tainted. Returns the hash.

        ``requested_by`` is derived from ``source`` when not given (see :meth:`requester_of`): the
        one production caller, ``ledger_tool``, passes the URL or the path the tool fetched, so a
        page or a file the user named in the instruction reads ``user``. Tainted either way —
        authority is not trust, and the content is still external.

        ``seen`` is whether ``content`` may exempt a value from :meth:`unseen_data_in_url`. False
        for anything but the public web (:data:`PUBLIC_FETCH_TOOLS`): an email or a calendar entry
        holds private data, and a value in one is not one an attacker could already see. False
        for a shell fetch (:meth:`record_exec`): a command's output is not only what it fetched —
        `cat ~/.aws/credentials; curl -s URL` prints the key and the page together, and nothing in
        the output says which line came from where. Counting it as seen let a local secret through
        S30-27 the moment S30-28 was on (study 30 review).
        """
        digest = _hash(content) if content else ""
        source = (source or "external").strip()
        who = self._label(requested_by, source)
        self._tainted.add(source)
        if digest:
            self._tainted.add(digest)
        if content:
            self._snippets.append(content[: self.snippet_chars])
            if self.exfil_host_path and seen:
                self._fetched_runs.update(run.lower() for run in _DATA_RUN.findall(content))
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

    def record_project_instructions(self, source: str, content: str) -> bool:
        """Take in a repository instructions file the harness put in the prompt; True if it was new.

        Only called when the operator declared the workspace untrusted (``CHIMERA_TRUST_WORKSPACE=0``,
        study 30, S30-26): the file is then what an untrusted ``read_file`` is, an external fetch.
        Two things differ from :meth:`record_fetch` called directly, both on purpose:

        - **``requested_by`` is ``agent``, always.** The harness loaded the file; the person did not
          ask for it. An instruction that says "follow AGENTS.md" names the file, and deriving the
          label would read ``user`` — which the ``authority`` mode then overlooks for the narrowing.
          The words in it are still the repository's, not the person's.
        - **Once per (file, content).** The system prompt is composed per run and again on some
          paths, and the epoch is part of an approval's key: taking the same bytes in twice would
          expire a yes given in between, for no new fact. The detail compared is built exactly as
          :meth:`record_fetch` stores it — empty for empty content, not the hash of the empty
          string, which once made an empty file "new" on every call — and the check and the record
          happen under one lock.
        """
        source = (source or "external").strip()  # as record_fetch normalises it, so the ref matches
        detail = f"sha256:{_hash(content)}" if content else ""
        with self._instructions_lock:
            if any(
                e.kind == "fetch" and e.ref == source and e.detail == detail for e in self.events
            ):
                return False
            self.record_fetch(source, content=content, requested_by="agent")
        self.note_seen(content)
        return True

    def record_write(self, path: str, content: str = "") -> CapabilityEvent:
        """Record a file write; the path inherits taint if the content came from a tainted source."""
        path = (path or "").strip()
        tainted, refs = self._content_is_tainted(content)
        if tainted:
            self._tainted.add(path)
        return self._add("write", path, tainted=tainted, provenance=refs)

    def record_exec(self, command: str, output: str = "") -> CapabilityEvent:
        """Record a command; with ``shell_fetch_guard`` on, a command that fetched is a fetch too.

        ``git clone``, ``curl URL`` and ``wget URL`` bring outside content in exactly as ``http_get``
        does, and ``run_shell`` is not in :data:`FETCH_TOOLS`, so a cloned README or a page ``curl``
        printed left the run clean (study 30, S30-28). The output is the fetched content: it joins
        the flow snippets, so written into a script it is a self-modifying write. A cloned
        repository's files are not tracked one by one — the run is tainted, which is what arms the
        narrowing; a file read from the clone later is not itself a tainted path.

        The output is tainted but never *seen* in the S30-27 sense (``seen=False``): it may hold
        whatever else the command printed, a local secret included.
        """
        _, refs = self._content_is_tainted(command)
        event = self._add("exec", command[:200], tainted=bool(refs), provenance=refs)
        if self.shell_fetch_guard:
            for source in _shell_fetch_sources(command):
                self.record_fetch(source, content=output, seen=False)
                output = ""  # one command's output belongs to one fetch, not to each source again
        return event

    def record_verify(self, command: str, *, source: str, origin: str = "") -> CapabilityEvent:
        """Record the verify command the loop is ABOUT TO RUN, and who authored it. RECORD-ONLY.

        The verifier is built outside the tool registry, so the command it runs on the workspace —
        often on the host — never reached this ledger: the replay of a run showed every shell call
        the agent made and not the one that decided whether the run succeeded (study 30, S30-23).

        Recorded BEFORE the command runs, so a verifier that hangs or raises is still on the
        record; :meth:`settle_verify` then appends what happened (``passed`` / ``failed`` /
        ``abstained`` / ``raised``). An event with no outcome is a command that never returned. An
        ``abstained`` one may never have run at all (a declined host exec, nothing collected).

        ``source`` is ``CommandVerifier.source``, one of
        :data:`chimera.core.verify.VERIFY_SOURCES`: ``user`` (typed with the run, authorised by
        construction) or where else the string came from (``inferred``, ``job``, ``card``,
        ``workflow``, ``spec_test``, ``crew``, ``eval``, ``lifecycle``). The verifier does not know
        WHICH file an inferred command came out of; a caller that does passes it as ``origin``
        (``Makefile``, ``package.json``), and it is recorded beside the source.

        The event names any tainted ref the command carries in ``provenance`` and does NOT set
        ``tainted``, on purpose: a tainted event arms ``run_tainted`` and with it pause-on-taint and
        the durable-provenance gates, and a verify command that pauses a run is a behaviour change no
        measurement has recommended yet. What it buys today is that the question can be asked of
        the record.

        WHICH record: the autonomous loop calls this, and the event becomes durable where that
        loop's ledger is written down — ``chimera solve`` dumps it to ``ledger.jsonl``. Elsewhere
        (the desktop Run) the ledger lives for the run in memory. The Code tab does not call it at
        all: its per-turn ledger is never written down, and the turn's stored receipt already keeps
        the command, its source and its outcome.
        """
        _, refs = self._content_is_tainted(command)
        who = "user" if source == "user" else "unknown"
        detail = f"source={source}" + (f" origin={origin}" if origin else "")
        return self._add(
            "verify", command[:200], detail=detail, provenance=refs, requested_by=who
        )

    def settle_verify(self, event: CapabilityEvent, outcome: str) -> None:
        """Append the outcome to a ``verify`` event :meth:`record_verify` wrote before the run.

        On the same event, not a second one: the replay counts one verify per command run, and a
        reader of the event sees in one line both what was about to run and what came of it.
        """
        with self._events_lock:
            event.detail = f"{event.detail} outcome={outcome}".strip()

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
        # The instruction is replaced; the addresses it named are not forgotten. A chat sets the
        # instruction once per turn, and an address given two turns ago is still one the user gave.
        self.note_seen(text or "")

    # --- recipients: did the conversation ever show this address? (study 24, M2) -------------

    def note_seen(self, *texts: str) -> None:
        """Remember every whole email address in ``texts`` as shown to this run.

        Fed by the instruction, by every tool result (`LedgeredTool`), and by a surface's earlier
        turns. Seen is not intended and not trusted: an address on a fetched page counts as seen,
        because this check asks only whether the address was made up — the taint layer is what asks
        whether it was planted.
        """
        for text in texts:
            if text:
                self._seen_addresses |= addresses_in(text)

    def unseen_addresses(self, values: Iterable[str]) -> list[str]:
        """The email addresses in ``values`` that this run has never been shown, in order, once.

        A value with no address in it (a channel id, a phone number, a URL) contributes nothing:
        there is nothing to compare, so there is nothing to flag.
        """
        out: list[str] = []
        for value in values:
            for address in sorted(addresses_in(value)):
                if address not in self._seen_addresses and address not in out:
                    out.append(address)
        return out

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

    def lineage(self) -> str:
        """The authority label the kernel keys its case law on: ``"tainted"`` once this run has
        consumed any untrusted content, ``""`` before. The plain :meth:`run_tainted` — not the
        ``for_narrowing`` reading — because a precedent is durable, like the provenance of a memory,
        and a value the user asked to fetch is still an external value."""
        return "tainted" if self.run_tainted() else ""

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

    def unseen_data_in_url(self, url: str) -> list[str]:
        """The data-like runs in ``url`` that neither the instruction nor any fetched content holds.

        Case-insensitive, because a hostname is. A value the run read from a local file or command
        is NOT seen here: that is where a secret comes from, and a commit hash from ``git log`` is
        asked about for the same reason (`bench/exfil_url`, the ``local`` class).
        """
        instruction = self._instruction or ""
        out: list[str] = []
        for token in _url_tokens(url):
            for run in _DATA_RUN.findall(token):
                low = run.lower()
                if not _data_like(run) or low in instruction or low in self._fetched_runs:
                    continue
                if run not in out:
                    out.append(run)
        return out

    def unnamed_clones(self, command: str) -> list[str]:
        """Each remote repository ``command`` clones that the user's instruction never named.

        Empty when the ledger was never told the instruction — the ``unknown`` the authority label
        uses — so a surface that does not tell its ledger is unchanged rather than asking about
        every clone. A repository is named when any spelling of it (:func:`_repo_forms`) occurs in
        the instruction whole, bounded as :func:`_named_in` bounds a path: ``r/rich`` inside
        ``vendor/rich`` is a directory the user wrote, not that repository.
        """
        if self._instruction is None:
            return []
        out: list[str] = []
        for source, gh in _clone_sources(command):
            repo = _remote_repo(source, gh=gh)
            if repo is None:
                continue
            forms = _repo_forms(*repo) | {_normalise(source)}
            if not any(_named_in(self._instruction, form) for form in forms):
                out.append(f"{repo[0]}/{repo[1]}")
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

    A call through the deferral proxy is assessed as the tool it runs
    (:func:`~chimera.governance.proxy.see_through`); judged as ``tool_call`` it matched none of the
    three branches below, and a tainted run's ``http_get`` with a query string was ALLOW.
    """
    tool_name, args = see_through(tool_name, args)
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
                action=describe_call(tool_name, args, command),
                sources=sources,
                span=span,
                proposal=proposal_of(tool_name, args, ledger.taint_epoch),
            )
        if ledger.shell_fetch_guard:
            # A clone of a remote the user never named (study 30, S30-28). Clean run or not: the
            # owner a model infers for a repository is wrong most of the time for recent ones
            # (arXiv 2607.07433), and whoever registered that owner serves what the run reads next.
            unnamed = ledger.unnamed_clones(command)
            if unnamed:
                return SequenceAssessment(
                    True, Decision.REVIEW,
                    f"clones {', '.join(unnamed)}, a repository the task never named — a name the "
                    f"model inferred resolves to whoever registered it first, and its README and "
                    f"scripts are what the run reads and runs next",
                    [],
                    action=describe_call(tool_name, args, command),
                    span=", ".join(unnamed),
                    proposal=proposal_of(tool_name, args, ledger.taint_epoch),
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
                action=describe_call(tool_name, args, path),
                sources=sources,
                span=span,
                proposal=proposal_of(tool_name, args, ledger.taint_epoch),
            )
    if tool_name in fetch_tools and ledger.exfil_host_path:
        url = _first(args, _URL_KEYS)
        host = (urlsplit(url).hostname or "").rstrip(".") if url else ""
        unseen = ledger.unseen_data_in_url(url) if url and host not in ledger.egress_allow else []
        if unseen and not (ledger.run_tainted() and urlsplit(url).query):
            # The query-string branch below keeps answering the call it always answered, with the
            # words it always used; this one takes the host, the path and a clean run's query.
            shown = ", ".join(_masked(_excerpt(run, 60)) for run in unseen[:3])
            return SequenceAssessment(
                True, Decision.REVIEW,
                f"fetches {host or url!r} carrying {shown} in its address, a value that appears "
                f"neither in the instruction nor in anything this run fetched — a hostname, a path "
                f"or a query carries data out as easily as a body",
                [],
                action=describe_call(tool_name, args, url),
                sources=ledger.taint_sources(),
                span=shown,
                proposal=proposal_of(tool_name, args, ledger.taint_epoch),
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
        allowed = False
        if parts is not None and ledger.egress_allow:
            # `hostname` and not `netloc`: the latter includes userinfo, so a URL crafted as
            # `https://api.github.com@evil.test/collect?data=…` would match an allowlisted prefix
            # while going somewhere else entirely. It is also already lowercased and port-free.
            host = (parts.hostname or "").rstrip(".")
            allowed = host in ledger.egress_allow
        if parts is not None and parts.query and not allowed:
            sources = ledger.taint_sources()
            return SequenceAssessment(
                True, Decision.REVIEW,
                f"fetches {parts.netloc or url!r} with a query string while this run holds untrusted "
                f"content from {'; '.join(sources) or 'an untrusted read'} — a GET can carry data out "
                f"as easily as a POST",
                [],
                action=describe_call(tool_name, args, url),
                sources=sources,
                span=_excerpt(parts.query),
                proposal=proposal_of(tool_name, args, ledger.taint_epoch),
            )
    return SequenceAssessment(False, Decision.ALLOW)
