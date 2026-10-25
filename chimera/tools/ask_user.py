"""A typed question for the person at the Code screen.

Unlike an approval, the answer is ordinary user data: it cannot authorize an action or satisfy a
policy gate. The tool is constructed only for a turn whose screen can announce and answer it; a
headless caller must not park a worker for fifteen minutes asking a person who cannot see it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from chimera.governance.pending import ask_user_durably
from chimera.tools.base import Tool

#: The longest label shown on a question card. Longer text is shortened, visibly, rather than
#: making the whole question fail validation.
LABEL_MAX = 80
DESCRIPTION_MAX = 2000
OPTION_LABEL_MAX = 120


def _shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _label_of(option: str) -> str:
    return _shorten(option, OPTION_LABEL_MAX)


class AskUserTool(Tool):
    """Pause this attended turn for bounded choices, or return an honest timeout."""

    name = "ask_user"
    description = (
        "Ask the user for a decision instead of guessing. Ask 1–4 questions, each with a short label, "
        "a description, and 2–4 options. Questions are asked in order; the turn pauses for each answer. "
        "Use an option labelled Other/Outra only when the user may enter free text. In the legacy "
        "question form, options are optional (up to six); without options, the user may answer freely. "
        "If timed_out is true, do not assume an answer to the unanswered question; use any answers "
        "already returned, and ask again only if the missing information is still needed."
    )
    parameters = {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array", "minItems": 1, "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "Short heading, at most 80 characters."},
                        "description": {"type": "string", "description": "Explain what the choice means."},
                        "options": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 4},
                    },
                    "required": ["label", "description", "options"],
                    "additionalProperties": False,
                },
            },
            # Keep the former one-question call shape alive for existing prompts and skills.
            "question": {"type": "string", "description": "The question to show the user."},
            "options": {
                "type": "array", "items": {"type": "string"}, "maxItems": 6,
                "description": "Optional list of up to six short answer labels.",
            },
            "allow_free_text": {
                "type": "boolean", "default": True,
                "description": "Whether the user may enter an answer other than a listed option.",
            },
        },
        # The schema stays compatible with providers that reject top-level combinators. The
        # runtime supports the legacy question as a header alongside a non-empty questions list.
        "additionalProperties": False,
    }

    def __init__(
        self, home: Path, announce: Any, *, wait_seconds: float | None, run_id: str = "",
    ) -> None:
        self.home = home
        self.announce = announce
        self.wait_seconds = wait_seconds
        self.run_id = run_id

    def run(self, **kwargs: Any) -> str:
        has_question = "question" in kwargs
        has_questions = "questions" in kwargs
        questions: Any = kwargs.get("questions")
        if isinstance(questions, str):
            try:
                questions = json.loads(questions)
            except json.JSONDecodeError:
                return (
                    "error: `questions` must be a JSON list of 1–4 question objects; "
                    "send the list directly or provide valid JSON"
                )
        if (not has_question and not has_questions) or (
            has_question and has_questions and not isinstance(questions, list)
        ) or (has_question and has_questions and not questions):
            # Preserve this exact response for existing callers that send neither form or an empty
            # new-form list alongside a legacy question.
            return "error: provide exactly one of question or questions"
        legacy = has_question and not has_questions
        free = kwargs.get("allow_free_text", True)
        if legacy:
            question, options = kwargs.get("question"), kwargs.get("options", [])
            if not isinstance(question, str) or not question.strip():
                return "error: question must not be empty"
            if not isinstance(options, list) or len(options) > 6:
                return "error: options must be a list of at most six labels"
            legacy_label = _shorten(question, LABEL_MAX)
            questions = [{"label": legacy_label, "description": question, "options": options}]
        else:
            legacy_options = kwargs.get("options", [])
            if has_question and legacy_options:
                return (
                    "error: send the new `questions` list with its per-question options; "
                    "drop legacy `options` when using `questions`"
                )
        if not isinstance(questions, list) or not 1 <= len(questions) <= 4:
            return "error: questions must contain 1–4 questions"
        if not isinstance(free, bool):
            return "error: allow_free_text must be a boolean"
        from chimera.tools.write_region import (
            authorize_repair_test_writes,
            claim_repair_authorization_question,
        )

        # During a repair, only one question may be put to the owner per turn (study 35, V3+).
        if claim_repair_authorization_question() is False:
            return "error: the repair already asked the user about protected test writes; do not ask again"
        answers: list[dict[str, str]] = []
        for item in questions:
            if not isinstance(item, dict):
                return "error: each question must have a label, description, and options"
            label, description, options = item.get("label"), item.get("description"), item.get("options")
            if not isinstance(label, str) or not label.strip() or not isinstance(description, str) or not description.strip():
                return "error: each question needs a non-empty label and description"
            if not isinstance(options, list) or (
                not 0 <= len(options) <= 6 if legacy else not 2 <= len(options) <= 4
            ):
                return "error: legacy questions allow up to six options; new questions require 2–4"
            if any(not isinstance(option, str) or not option.strip() for option in options):
                return "error: each option must be a non-empty label"
            if len({option.strip().casefold() for option in options}) != len(options):
                return "error: options must be distinct"
            short_label = _shorten(label, LABEL_MAX)
            short_description = _shorten(description, DESCRIPTION_MAX)
            if not short_description:
                return "error: each question needs a non-empty description"
            labels = [_label_of(option) for option in options]
            if len({option.casefold() for option in labels}) != len(labels):
                return "error: options must be distinct within their first 120 characters"
            full = {shown: " ".join(original.split()) for shown, original in zip(labels, options, strict=True)}
            details = {shown: text for shown, text in full.items() if shown != text}
            user_answer = ask_user_durably(
                self.home, short_description, labels, free, announce=self.announce,
                wait_seconds=self.wait_seconds if self.wait_seconds is not None else float("inf"), option_details=details, label=short_label,
                description=short_description,
                run_id=self.run_id,
            )
            if user_answer is None:
                # Keep already received answers visible instead of silently discarding user input.
                if answers:
                    return json.dumps({"answers": answers, "timed_out": True}, ensure_ascii=False)
                return "No response."
            selected = full.get(user_answer, user_answer)
            authorize_repair_test_writes(selected)
            answers.append({"label": short_label, "answer": selected})
        if legacy:
            return json.dumps({"answer": answers[0]["answer"]}, ensure_ascii=False)
        return json.dumps({"answers": answers}, ensure_ascii=False)


def is_question_id(request_id: str) -> bool:
    """Keep untrusted route ids from becoming paths in the pending directory."""
    return bool(re.fullmatch(r"[0-9a-f]{12}", request_id or ""))
