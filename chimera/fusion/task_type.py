"""Task-type classification for aggregation routing (MALLM, arXiv 2607.05477).

MALLM's finding: the best way to *aggregate* a multi-model panel depends on the task. For a task
with a single verifiable answer (arithmetic, counting, multiple-choice, true/false), **voting** on
the panel's answers wins — a correct minority answer must not be averaged away by a synthesizer, and
synthesis can inject errors a clean vote wouldn't. For an open-ended / knowledge task, **synthesis**
wins — fold in unique insights, resolve contradictions.

Chimera already has both aggregators (``majority`` in :mod:`chimera.fusion.consistency` for the vote,
the judge→synthesizer path in :mod:`chimera.fusion.engine` for synthesis). This module supplies the
routing signal: a cheap, deterministic **lexical** classifier — not a trained model. It is deliberately
conservative: it returns ``"logic"`` only on a strong single-answer signal, and defaults to
``"knowledge"`` (today's synthesize-everything behaviour) otherwise. So even when task-typed
aggregation is enabled, the only behaviour change is "a *logic* task on which the panel reached a
clear majority returns that majority instead of synthesizing it" — safe, because a wrong vote would
require a majority cluster to actually form.
"""

from __future__ import annotations

import re
from typing import Literal

from chimera.providers.gateway import Message, MessageLike

TaskType = Literal["logic", "knowledge"]

# Strong single-verifiable-answer signals. Each is a canonical short-answer task where lexical
# majority voting over the panel is meaningful (a number, a letter, yes/no, a single value).
_LOGIC_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\btrue or false\b", re.I),
    re.compile(r"\byes or no\b", re.I),
    re.compile(r"\bwhich of the following\b", re.I),
    re.compile(r"\bmultiple[- ]choice\b", re.I),
    re.compile(r"\bchoose the (?:correct|right|best)\b", re.I),
    re.compile(r"^\s*[a-eA-E][)\.]\s", re.M),  # option lines: "a) ...", "B. ..."
    re.compile(r"\bhow many\b", re.I),
    re.compile(r"\bhow much (?:is|are|does)\b", re.I),
    re.compile(r"\b(?:calculate|compute|evaluate)\b", re.I),
    re.compile(r"\bwhat is the (?:value|sum|product|result|remainder|average|mean|median|percentage)\b", re.I),
    re.compile(r"\bsolve for\b", re.I),
    re.compile(r"\d+\s*[+\-*/x×÷]\s*\d+"),  # an explicit arithmetic expression
    re.compile(r"\bwhat is\s+\d"),  # "what is 12% of ..."
)

# Signals that a task is open-ended even if a logic keyword slipped in — these VETO a "logic" call.
# Code has many valid phrasings, so lexical majority is wrong for it (synthesis is better); the same
# holds for explanatory / generative asks.
_KNOWLEDGE_VETO: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bwrite (?:a|an|the|some)\b", re.I),
    re.compile(r"\b(?:explain|describe|discuss|summari[sz]e|compare|analy[sz]e|essay|outline)\b", re.I),
    re.compile(r"\b(?:function|code|program|script|implement|refactor|algorithm)\b", re.I),
    re.compile(r"```"),
)


def _last_user_text(messages: list[MessageLike]) -> str:
    """The last user turn's text — the task being classified."""
    for message in reversed(messages):
        data = message.as_dict() if isinstance(message, Message) else message
        if data.get("role") == "user":
            return str(data.get("content", ""))
    # No explicit user turn: fall back to the concatenation of all content.
    parts = [
        str((m.as_dict() if isinstance(m, Message) else m).get("content", "")) for m in messages
    ]
    return "\n".join(p for p in parts if p)


def classify_task_type(messages: list[MessageLike]) -> TaskType:
    """Classify the task as ``"logic"`` (vote-aggregate) or ``"knowledge"`` (synthesize).

    Conservative and deterministic: returns ``"logic"`` only when a strong single-answer signal is
    present AND no open-ended/code signal vetoes it; ``"knowledge"`` otherwise (the default).
    """
    text = _last_user_text(messages)
    if not text.strip():
        return "knowledge"
    if any(p.search(text) for p in _KNOWLEDGE_VETO):
        return "knowledge"
    if any(p.search(text) for p in _LOGIC_PATTERNS):
        return "logic"
    return "knowledge"
