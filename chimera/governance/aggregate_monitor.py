"""Aggregate cross-agent monitor (FakeLab, arXiv 2607.07368).

A per-worker monitor is **insufficient under fan-out**: when work is split across sub-agents, each
agent's actions can look benign in isolation while the *aggregate* is malicious. The canonical case is
split exfiltration — agent A fetches untrusted content (tainted in A's ledger) and agent B executes or
exfiltrates it. B's own :class:`~chimera.governance.ledger.TaintLedger` never saw the fetch, so B's
per-action :func:`~chimera.governance.ledger.assess_action` finds nothing tainted and allows it. The
malicious flow lives *between* agents, where no single-agent monitor can see it.

This monitor sits one level up: given the per-agent capability events from an orchestration run, it
looks for patterns visible only in aggregate:

- **cross-agent-taint** — one agent consumed untrusted content (a ``fetch``) and a *different* agent
  performed a sink (exec / escalation / a write into an executable file). The split-flow collusion.
- **fan-out-volume** — the total number of sink actions across all agents exceeds a budget, even when
  each agent individually stays under it (fan-out amplification of an otherwise-throttled capability).

Like the per-agent ledger, it only ever **escalates to review** — it never hard-blocks. It is a pure
function of the events, so it is deterministic and testable; an orchestrator (crew / hierarchy) feeds
it each sub-agent's ``TaintLedger.events`` after the run.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from chimera.governance.ledger import _CODE_SUFFIXES, CapabilityEvent

DEFAULT_MAX_SINKS = 10  # total sink actions across all agents before fan-out-volume escalates


@dataclass
class CollusionFinding:
    """One aggregate anti-pattern found across agents (a review escalation, never a block)."""

    kind: str  # "cross-agent-taint" | "fan-out-volume"
    detail: str
    agents: list[str] = field(default_factory=list)


def _is_sink(event: CapabilityEvent) -> bool:
    """A sink is an action that can cause external effect: exec, escalation, or an executable write."""
    if event.kind in ("exec", "escalation"):
        return True
    return event.kind == "write" and event.ref.endswith(_CODE_SUFFIXES)


def _consumed_untrusted(events: Sequence[CapabilityEvent]) -> bool:
    """True if this agent fetched external/untrusted content this run."""
    return any(event.kind == "fetch" for event in events)


class AggregateMonitor:
    """Escalate collusion patterns visible only across a fan-out of sub-agents."""

    def __init__(self, *, max_sinks: int = DEFAULT_MAX_SINKS) -> None:
        self.max_sinks = max_sinks

    def assess(self, agents: Mapping[str, Sequence[CapabilityEvent]]) -> list[CollusionFinding]:
        """Return aggregate findings across ``{agent_id: capability_events}``. Empty if clean."""
        findings: list[CollusionFinding] = []

        tainted_agents = sorted(a for a, evs in agents.items() if _consumed_untrusted(evs))
        sink_agents = sorted(a for a, evs in agents.items() if any(_is_sink(e) for e in evs))

        # cross-agent-taint: an untrusted-consuming agent and a DIFFERENT sink agent — the split flow a
        # per-agent monitor cannot see (the fetch and the sink live in separate ledgers).
        cross = sorted({(t, s) for t in tainted_agents for s in sink_agents if t != s})
        if cross:
            involved = sorted({a for pair in cross for a in pair})
            pairs = ", ".join(f"{t}→{s}" for t, s in cross)
            findings.append(
                CollusionFinding(
                    "cross-agent-taint",
                    f"untrusted content entered via one agent and a different agent performed a sink ({pairs}) "
                    "— a split flow no single-agent monitor sees",
                    involved,
                )
            )

        # fan-out-volume: aggregate sink count over budget even if each agent is individually under it.
        total_sinks = sum(sum(1 for e in evs if _is_sink(e)) for evs in agents.values())
        if total_sinks > self.max_sinks:
            findings.append(
                CollusionFinding(
                    "fan-out-volume",
                    f"{total_sinks} sink actions across {len(agents)} agents exceed the aggregate budget "
                    f"of {self.max_sinks} (fan-out amplification)",
                    sink_agents,
                )
            )
        return findings
