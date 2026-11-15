"""What the governance layers granted and refused, per surface, over a window — counted by code.

Audit P0-15. The unattended surfaces run with the kernel and the taint ledger OFF by default, and
the move to ``observe`` (and later ``enforce``) is the owner's decision. That decision needs a
number the owner has never been shown: how often each layer WOULD have said no, on the real
schedule, not on a corpus. This is that number. It reads only; it writes nothing and it calls no
model — a figure that reaches a person goes through code, never through a model that might round it.

**Where the numbers come from, and what each source can and cannot say**

* ``audit.jsonl`` — the kernel's verdicts (``governance``), the taint ledger's questions
  (``taint_review`` / ``taint_narrowed`` / ``taint_write_warned`` / ``recipient_unseen``), the
  owner's floor (``owner_floor``), the hooks' denials (``hook``), the allowlist's exclusions
  (``tool_allowlist``) and the per-assembly ``governance_mode`` line. Entries carry a ``ts`` only
  since 2026-11-14; older ones are counted as ``undated`` and left out of the window — never placed
  in a week they may not belong to. Kernel and taint lines carry the ``surface`` only from the same
  change; a line without one is reported under :data:`NO_SURFACE`.
* ``approvals/history.jsonl`` — how each question that went through the durable approval queue ended,
  dated and with its surface. This is the only place an ANSWER is recorded: a question answered at
  a terminal prompt is not in it, and the taint/kernel lines above say a question was asked, not how
  it ended.

**What it cannot see, said here and in the text it prints**: the host-execution confirm
(``CHIMERA_HOST_EXEC``) writes to the process log and not to any file this reads, so its grants and
refusals are NOT in the report. A table with no host-exec row reads as "host-exec refused nothing", so
the text says it.

**``observe``** refuses nothing: the kernel's ``review`` verdicts under it reach an approver that
says yes. Those are counted apart as ``would_refuse`` — the price of turning ``enforce`` on — and
are never added to ``refused``. A ``block`` is refused in every mode and is counted as refused.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

#: How far back the report looks unless told otherwise.
WINDOW_DAYS = 7

#: Where a line goes when it names no surface (written before the field existed, or by a caller
#: that has none). Its own row, so the omission is visible instead of folded into a real surface.
NO_SURFACE = "(sem superfície)"

Lang = Literal["pt", "en"]

#: The counters, in the order the text prints them. Each is a count of audit lines or history rows.
GRANTED = "granted"            # the kernel let it through (only logged where allows are logged)
WARNED = "warned"              # allowed with a warning: kernel `warn`, a write after untrusted input
REFUSED = "refused"            # kernel BLOCK (every mode), hook deny — an outcome, never a raise to review
WOULD_REFUSE = "would_refuse"  # observe: what `enforce` would have refused OR put to an approver (kernel REVIEW, taint card)
ASKED = "asked"                # a question put to an approver (kernel REVIEW under `enforce`/unknown mode, taint)
APPROVED = "approved"          # a question that ended in yes, from approvals/history.jsonl
DECLINED = "declined"          # a question that ended in no, from approvals/history.jsonl
TIMED_OUT = "timed_out"        # a question nobody answered
BYPASSED = "bypassed"          # released by the owner's bypass switch
EXCLUDED = "excluded"          # tools the allowlist took out of an assembly
OUTPUT_HELD = "output_held"    # a post_tool hook withheld the output of an action that had already run

COUNTERS: tuple[str, ...] = (
    GRANTED, WARNED, REFUSED, WOULD_REFUSE, ASKED, APPROVED, DECLINED, TIMED_OUT, BYPASSED, EXCLUDED, OUTPUT_HELD,
)

#: Which layer a count belongs to, so the owner can read "the taint ledger would have asked 40 times"
#: and not only "40". The keys are what the lines are called in the audit.
LAYERS: tuple[str, ...] = ("kernel", "taint", "hook", "floor", "allowlist", "approvals", "bypass")


@dataclass
class SurfaceReport:
    """Everything counted for one surface: ``layers[layer][counter]``."""

    layers: dict[str, Counter[str]] = field(default_factory=dict)
    modes: set[str] = field(default_factory=set)
    """The kernel modes the surface's assemblies announced in the window (``governance_mode``)."""

    def add(self, layer: str, counter: str, n: int = 1) -> None:
        self.layers.setdefault(layer, Counter())[counter] += n

    def total(self, counter: str) -> int:
        return sum(c.get(counter, 0) for c in self.layers.values())


@dataclass
class GovernanceReport:
    start: datetime
    end: datetime
    days: int
    surfaces: dict[str, SurfaceReport] = field(default_factory=dict)
    undated: dict[str, int] = field(default_factory=dict)
    """Lines a source held that carry no readable date: left out of the window, and counted."""
    missing: tuple[str, ...] = ()
    """Source files that do not exist under the home — a home where nothing was recorded, or another one."""
    unattributed: int = 0
    """Counted lines that named no surface; they are in the :data:`NO_SURFACE` row."""

    def total(self, counter: str) -> int:
        return sum(s.total(counter) for s in self.surfaces.values())


def _when(value: Any) -> float | None:
    """A line's date as epoch seconds: an ISO string (naive read as UTC) or a number; else ``None``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Every line that parses to an object. A torn line is one line lost, as in ``AuditLog.entries``."""
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return rows
    for raw in text.splitlines():
        if not raw.strip():
            continue
        try:
            parsed = json.loads(raw)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def _surface_of(row: dict[str, Any]) -> str:
    name = str(row.get("surface") or "").strip()
    return name or NO_SURFACE


def _count_kernel(report: SurfaceReport, row: dict[str, Any]) -> None:
    decision = str(row.get("decision") or "")
    mode = str(row.get("mode") or "")
    if mode:
        report.modes.add(mode)
    if decision == "allow":
        report.add("kernel", GRANTED)
    elif decision == "warn":
        report.add("kernel", WARNED)
    elif decision == "block":
        # Refused in EVERY mode: a block is returned before any approver is consulted.
        report.add("kernel", REFUSED)
    elif decision == "review":
        # Under `observe` the approver says yes to everything, so this is what enforcement WOULD
        # have refused. Under `enforce` — or a line with no mode — somebody was asked.
        report.add("kernel", WOULD_REFUSE if mode == "observe" else ASKED)


def _count_hook(report: SurfaceReport, row: dict[str, Any]) -> None:
    event = row.get("event")
    if event == "ask":
        # The answer to a hook's question, written after it was put: the outcome. A no is a refusal.
        if row.get("approved") is False:
            report.add("hook", REFUSED)
    elif event == "post_tool":
        # The action already RAN; the hook only withholds its output. Not a refusal.
        if row.get("applied") == "deny":
            report.add("hook", OUTPUT_HELD)
    elif event not in ("load", "updated_input") and row.get("applied") == "deny":
        report.add("hook", REFUSED)  # a veto before the action ran


def _count_audit_row(report: SurfaceReport, row: dict[str, Any]) -> bool:
    """Add one audit line to ``report``. ``False`` for a type this report has no use for."""
    kind = row.get("type")
    if kind == "governance":
        _count_kernel(report, row)
    elif kind in ("taint_review", "taint_narrowed", "recipient_unseen"):
        # A question to an approver — except a `recipient_unseen` that went ahead without a card.
        # Under `observe` the approver says yes to everything: the call ran, and what is counted is
        # what enforcement WOULD have put in front of somebody (would have needed approval).
        if kind != "recipient_unseen" or row.get("card"):
            mode = str(row.get("mode") or "")
            if mode:
                report.modes.add(mode)
            report.add("taint", WOULD_REFUSE if mode == "observe" else ASKED)
    elif kind == "taint_write_warned":
        report.add("taint", WARNED)
    elif kind == "owner_floor":
        # The floor RAISES a verdict to review, before any approver is consulted; it is written
        # whether the owner then says yes or no. Counting it as a refusal booked approved actions as
        # refused. Only the outcome refuses, and the log does not hold it: so it is "raised to
        # review" — a forecast under `observe`, a question otherwise.
        mode = str(row.get("mode") or "")
        if mode:
            report.modes.add(mode)
        report.add("floor", WOULD_REFUSE if mode == "observe" else ASKED)
    elif kind == "hook":
        _count_hook(report, row)
    elif kind == "tool_allowlist":
        excluded = row.get("excluded")
        report.add("allowlist", EXCLUDED, len(excluded) if isinstance(excluded, list) else 0)
    elif kind == "bypass_permissions":
        report.add("bypass", BYPASSED)
    elif kind == "governance_mode":
        report.modes.add(str(row.get("mode") or ""))
    else:
        return False
    return True


def build_governance_report(
    home: Path, *, now: float | None = None, days: int = WINDOW_DAYS
) -> GovernanceReport:
    """Read the audit log and the approval history under ``home`` and count the window ending at ``now``.

    Reads only. A missing source is named in ``missing`` and never turned into zeros: no audit log
    is a home where nothing was recorded, not a week in which nothing was refused.
    """
    import time

    home = Path(home)
    end = time.time() if now is None else float(now)
    start = end - timedelta(days=days).total_seconds()
    audit_path = home / "audit.jsonl"
    history_path = home / "approvals" / "history.jsonl"
    report = GovernanceReport(
        start=datetime.fromtimestamp(start, UTC),
        end=datetime.fromtimestamp(end, UTC),
        days=days,
        missing=tuple(p.name for p in (audit_path, history_path) if not p.exists()),
    )

    def surface(name: str) -> SurfaceReport:
        return report.surfaces.setdefault(name, SurfaceReport())

    def dated(source: str, stamp: float | None) -> bool:
        if stamp is None:
            report.undated[source] = report.undated.get(source, 0) + 1
            return False
        return start <= stamp < end

    for row in _read_jsonl(audit_path):
        # Only lines this report counts are subject to the date rule — a `design.export` with no
        # date is not an omission anybody needs told about.
        if row.get("type") not in _AUDIT_TYPES:
            continue
        if not dated("audit.jsonl", _when(row.get("ts"))):
            continue
        name = _surface_of(row)
        if name == NO_SURFACE and row.get("type") != "governance_mode":
            report.unattributed += 1
        _count_audit_row(surface(name), row)

    for row in _read_jsonl(history_path):
        if not dated("approvals/history.jsonl", _when(row.get("asked_at"))):
            continue
        outcome = row.get("outcome")
        counter = {"approved": APPROVED, "refused": DECLINED}.get(str(outcome), TIMED_OUT)
        # `unreadable` and `unverified` are refusals by the system, not a person's no and not a
        # timeout; they are folded into TIMED_OUT's "nobody legitimately answered" and the text
        # says so rather than inventing a fourth column.
        surface(_surface_of(row)).add("approvals", counter)
    return report


#: The audit line types :func:`_count_audit_row` understands, so the date rule is applied to those only.
_AUDIT_TYPES = frozenset({
    "governance", "taint_review", "taint_narrowed", "taint_write_warned", "recipient_unseen",
    "owner_floor", "hook", "tool_allowlist", "bypass_permissions", "governance_mode",
})


# --- the text ------------------------------------------------------------------------------------

_TEXT: dict[Lang, dict[str, str]] = {
    "pt": {
        "title": "Governança — concedidos e recusados, {start} a {end} (UTC, {days} dias)",
        "totals": "Total: {refused} recusada(s), {would} que `enforce` teria recusado ou posto diante de um aprovador (observe), "
        "{asked} pergunta(s) a um aprovador, {warned} com aviso, {granted} concedida(s) registrada(s).",
        "approvals": "Respostas (approvals/history.jsonl): {approved} aprovada(s), {declined} recusada(s), "
        "{timed_out} sem resposta válida.",
        "surface": "- {name} [{modes}]: {parts}",
        "none": "nenhum evento no período",
        "modes_none": "modo não anunciado no período",
        "kernel": "kernel",
        "taint": "taint",
        "hook": "hook",
        "floor": "piso do dono",
        "allowlist": "allowlist",
        "approvals_layer": "aprovações",
        "bypass": "bypass",
        "granted": "concedeu {n}",
        "warned": "avisou {n}",
        "refused": "recusou {n}",
        "would_refuse": "teria recusado/exigido aprovação {n}",
        "asked": "elevou a revisão {n}",
        "approved": "aprovadas {n}",
        "declined": "recusadas {n}",
        "timed_out": "sem resposta {n}",
        "bypassed": "liberou por bypass {n}",
        "excluded": "retirou {n} ferramenta(s) na montagem",
        "output_held": "reteve a saída já executada {n} vez(es) (um registro por hook)",
        "undated": "Linhas sem data legível, fora da conta: {list}. O audit.jsonl só passou a datar as linhas "
        "em 2026-11-14.",
        "unattributed": "{n} linha(s) sem superfície, no grupo \"(sem superfície)\" — escritas antes de o campo existir.",
        "missing": "Fonte ausente neste home (não é \"zero\"): {list}.",
        "empty": "Nenhum evento de governança datado nos últimos {days} dias.",
        "limits": "Fora desta contagem: a confirmação de execução no host (CHIMERA_HOST_EXEC) só escreve no log do "
        "processo, e uma pergunta respondida num terminal não vai para approvals/history.jsonl — "
        "\"elevou a revisão\" diz que o aprovador foi consultado, não como terminou.",
        "footer": "Números contados por `chimera report governance`, sem modelo.",
    },
    "en": {
        "title": "Governance — granted and refused, {start} to {end} (UTC, {days} days)",
        "totals": "Total: {refused} refused, {would} that `enforce` would have refused or put to an approver (observe), "
        "{asked} question(s) put to an approver, {warned} with a warning, {granted} grant(s) logged.",
        "approvals": "Answers (approvals/history.jsonl): {approved} approved, {declined} declined, "
        "{timed_out} with no valid answer.",
        "surface": "- {name} [{modes}]: {parts}",
        "none": "no events in the period",
        "modes_none": "mode not announced in the period",
        "kernel": "kernel",
        "taint": "taint",
        "hook": "hook",
        "floor": "owner floor",
        "allowlist": "allowlist",
        "approvals_layer": "approvals",
        "bypass": "bypass",
        "granted": "granted {n}",
        "warned": "warned {n}",
        "refused": "refused {n}",
        "would_refuse": "would have refused/needed approval {n}",
        "asked": "raised to review {n}",
        "approved": "approved {n}",
        "declined": "declined {n}",
        "timed_out": "unanswered {n}",
        "bypassed": "released by bypass {n}",
        "excluded": "removed {n} tool(s) at assembly",
        "output_held": "withheld an already-run output {n} time(s) (one record per hook)",
        "undated": "Lines with no readable date, left out: {list}. audit.jsonl only began dating its lines "
        "on 2026-11-14.",
        "unattributed": "{n} line(s) with no surface, under \"(sem superfície)\" — written before the field existed.",
        "missing": "Source absent from this home (not \"zero\"): {list}.",
        "empty": "No dated governance events in the last {days} days.",
        "limits": "Not counted here: the host-execution confirm (CHIMERA_HOST_EXEC) writes only to the process "
        "log, and a question answered at a terminal does not reach approvals/history.jsonl — "
        "\"raised to review\" says the approver was consulted, not how it ended.",
        "footer": "Numbers counted by `chimera report governance`, no model.",
    },
}

_LAYER_KEY = {"approvals": "approvals_layer"}


def _surface_line(name: str, surf: SurfaceReport, t: dict[str, str]) -> str:
    parts: list[str] = []
    for layer in LAYERS:
        counts = surf.layers.get(layer)
        if not counts:
            continue
        said = [t[c].format(n=counts[c]) for c in COUNTERS if counts.get(c)]
        if said:
            parts.append(f"{t[_LAYER_KEY.get(layer, layer)]}: " + ", ".join(said))
    modes = ", ".join(sorted(m for m in surf.modes if m)) or t["modes_none"]
    return t["surface"].format(name=name, modes=modes, parts="; ".join(parts) or t["none"])


def render_governance_report(report: GovernanceReport, lang: Lang = "pt") -> str:
    """The report as plain text. Every figure was counted above; nothing is estimated here."""
    t = _TEXT[lang]
    lines = [t["title"].format(
        start=report.start.strftime("%Y-%m-%d"), end=report.end.strftime("%Y-%m-%d"), days=report.days
    )]
    active = {name: s for name, s in report.surfaces.items() if s.layers or s.modes}
    if not active:
        lines.append(t["empty"].format(days=report.days))
    else:
        lines.append(t["totals"].format(
            refused=report.total(REFUSED), would=report.total(WOULD_REFUSE), asked=report.total(ASKED),
            warned=report.total(WARNED), granted=report.total(GRANTED),
        ))
        lines.append(t["approvals"].format(
            approved=report.total(APPROVED), declined=report.total(DECLINED),
            timed_out=report.total(TIMED_OUT),
        ))
        lines.extend(_surface_line(name, active[name], t) for name in sorted(active))
    if report.unattributed:
        lines.append(t["unattributed"].format(n=report.unattributed))
    if report.undated:
        listed = ", ".join(f"{src}: {n}" for src, n in sorted(report.undated.items()))
        lines.append(t["undated"].format(list=listed))
    if report.missing:
        lines.append(t["missing"].format(list=", ".join(report.missing)))
    lines.append(t["limits"])
    lines.append(t["footer"])
    return "\n".join(lines)
