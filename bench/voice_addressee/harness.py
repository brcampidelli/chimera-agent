"""Deterministic measurement harness helpers; model and spoken-path effects are injectable."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from chimera.decisions.contract import Reading

CORPUS = Path(__file__).with_name("corpus.jsonl")


@dataclass(frozen=True)
class Item:
    id: str
    family: str
    label: str
    transcript: str


@dataclass(frozen=True)
class SpokenOutcome:
    tool_call: bool
    answer: str


class AddresseeBackend(Protocol):
    def ask_choice(self, transcript: str) -> Reading: ...

    def spoken_turn(self, transcript: str) -> SpokenOutcome: ...


def load_corpus(path: Path = CORPUS) -> list[Item]:
    """Load and validate the fixed, hand-labelled corpus."""
    items = [Item(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len({item.id for item in items}) != len(items):
        raise ValueError("voice addressee corpus ids must be unique")
    if any(item.label not in {"for_me", "not_for_me"} for item in items):
        raise ValueError("voice addressee labels must be for_me or not_for_me")
    return items


def evaluate(backend: AddresseeBackend, items: list[Item]) -> list[dict[str, object]]:
    """Run both injected arms and return one inspectable record per item."""
    rows: list[dict[str, object]] = []
    for item in items:
        spoken_started = time.perf_counter()
        spoken_error = ""
        try:
            spoken = backend.spoken_turn(item.transcript)
        except Exception as exc:  # noqa: BLE001 — retain failed rows rather than biasing the denominator
            spoken = SpokenOutcome(tool_call=False, answer="")
            spoken_error = type(exc).__name__
        spoken_seconds = time.perf_counter() - spoken_started
        choice_started = time.perf_counter()
        choice_error = ""
        try:
            reading = backend.ask_choice(item.transcript)
        except Exception as exc:  # noqa: BLE001 — failures are data, not implicit negative labels
            reading = Reading(choice=None, shares=None, p=None)
            choice_error = type(exc).__name__
        choice_seconds = time.perf_counter() - choice_started
        words = len(spoken.answer.split())
        rows.append({
            "id": item.id,
            "family": item.family,
            "label": item.label,
            "transcript": item.transcript,
            "tool_call": spoken.tool_call,
            "answer_words": words,
            "long_answer": words >= 40,
            "baseline_response": spoken.tool_call or words >= 40,
            "spoken_error": spoken_error,
            "spoken_seconds": spoken_seconds,
            "choice": reading.choice,
            "shares": reading.shares,
            "choice_halt": reading.choice is None,
            "choice_error": choice_error,
            "choice_seconds": choice_seconds,
            "choice_mass": reading.mass,
            "choice_logprobs_came": reading.logprobs_came,
            "resolved_model": reading.resolved_model,
            "choice_raw": reading.raw,
        })
    return rows


def summarize(rows: list[dict[str, object]]) -> dict[str, int | float]:
    """Report the preregistered absolute counts and percentages."""
    non_directed = [row for row in rows if row["label"] == "not_for_me"]
    controls = [row for row in rows if row["label"] == "for_me"]
    baseline = sum(bool(row["baseline_response"]) for row in non_directed)
    shadow_false_positive = sum(row["choice"] == "for_me" for row in non_directed)
    shadow_retained = sum(row["choice"] == "for_me" for row in controls)
    return {
        "not_for_me_n": len(non_directed),
        "baseline_non_directed_responses": baseline,
        "baseline_non_directed_response_rate": baseline / len(non_directed) if non_directed else 0.0,
        "tool_calls_not_for_me": sum(bool(row["tool_call"]) for row in non_directed),
        "long_answers_not_for_me": sum(bool(row["long_answer"]) for row in non_directed),
        "choice_for_me_not_for_me": shadow_false_positive,
        "choice_false_positive_rate": shadow_false_positive / len(non_directed) if non_directed else 0.0,
        "for_me_n": len(controls),
        "choice_for_me_controls": shadow_retained,
        "choice_control_retention_rate": shadow_retained / len(controls) if controls else 0.0,
        "spoken_failures": sum(bool(row["spoken_error"]) for row in rows),
        "choice_failures": sum(bool(row["choice_error"]) for row in rows),
    }


class FakeBackend:
    """Explicit test instrument; deterministic and incapable of network or tool access."""

    def __init__(self, readings: dict[str, str], outcomes: dict[str, SpokenOutcome] | None = None) -> None:
        self.readings = readings
        self.outcomes = outcomes or {}
        self.choice_calls: list[str] = []
        self.spoken_calls: list[str] = []

    def ask_choice(self, transcript: str) -> Reading:
        self.choice_calls.append(transcript)
        choice = self.readings[transcript]
        return Reading(choice=choice, shares={"for_me": float(choice == "for_me"), "not_for_me": float(choice == "not_for_me")}, p=float(choice == "for_me"))

    def spoken_turn(self, transcript: str) -> SpokenOutcome:
        self.spoken_calls.append(transcript)
        return self.outcomes.get(transcript, SpokenOutcome(tool_call=False, answer="Acknowledged."))
