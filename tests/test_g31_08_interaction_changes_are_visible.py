"""G31-08: observable changes to interaction conditions are surfaced to their user."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.interface.session import TurnReport
from chimera.memory import MemoryItem, MemoryManager
from chimera.memory.store import MemoryStore
from chimera.server import InboundMessage, MessageGateway


class _ReportSession:
    def __init__(self, report: TurnReport) -> None:
        self.report = report

    def send(self, message: str) -> str:
        return self.send_verbose(message).answer

    def send_verbose(self, message: str, *, on_notice: Any = None, **_kw: Any) -> TurnReport:
        return self.report


def _reply(report: TurnReport) -> str:
    gateway = MessageGateway(lambda: _ReportSession(report), warnings_in_reply=True)
    return gateway.on_message(InboundMessage("please help", chat_id="test"))


def test_consolidation_records_merged_facts_in_the_memory_audit_chain(tmp_path: Path) -> None:
    class Audit:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict[str, Any]]] = []

        def record(self, event_type: str, payload: dict[str, Any]) -> None:
            self.events.append((event_type, payload))

    store = MemoryStore(tmp_path / "memory.json")
    store.add(MemoryItem(id="one", content="Prefers tea", kind="semantic"))
    store.add(MemoryItem(id="two", content="Likes tea", kind="semantic"))
    audit = Audit()
    manager = MemoryManager(store, audit=audit)
    outcome = manager.consolidate_outcome(lambda _facts: "Prefers tea", threshold=0.0)
    assert outcome.removed == 1
    events = [event for event in audit.events if event[0] == "memory.consolidated"]
    assert len(events) == 1
    assert events[0][1]["removed"] == 1
    assert events[0][1]["kind"] == "semantic"


def test_bot_reports_memory_facts_merged_after_the_turn() -> None:
    reply = _reply(TurnReport(answer="Done", memory_consolidated=3))
    assert "consolidated 3 redundant memory item(s)" in reply


def test_bot_reports_fusion_fallback_instead_of_presenting_it_as_fused() -> None:
    reply = _reply(TurnReport(
        answer="A panel answer",
        route_meta={
            "kind": "fusion",
            "aggregation": "fallback",
            "fallback_stage": "synth",
            "fallback_reason": "provider unavailable",
        },
    ))
    assert "this is a panel answer, not a fused one" in reply
    assert "synth failed (provider unavailable)" in reply


def test_bot_reports_fusion_fallback_nested_in_cascade_metadata() -> None:
    reply = _reply(TurnReport(answer="A panel answer", route_meta={
        "kind": "cascade",
        "fusion": {
            "kind": "fusion",
            "aggregation": "fallback",
            "fallback_stage": "judge",
            "fallback_reason": "empty response",
        },
    }))
    assert "judge failed (empty response)" in reply


def test_bot_does_not_label_successful_fusion_as_fallback() -> None:
    reply = _reply(TurnReport(answer="Fused", route_meta={
        "kind": "fusion", "aggregation": "synth",
    }))
    assert "panel answer" not in reply
    assert "fusion" not in reply
