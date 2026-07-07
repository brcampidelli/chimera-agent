"""The skill-evolution loop: propose -> test -> keep/discard, and refine.

When the agent succeeds at a task, the evolver asks a model to generalize it into a
reusable :class:`LearnedSkill`, then **tests** that skill before keeping it — the same
verify-or-revert discipline as the autonomous loop, applied to the agent's own skills.
Refinement improves a skill's template from its failure examples (continuous learning).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from chimera.evolution.edit_diagnostic import classify_edit
from chimera.evolution.learned_skill import LearnedSkill, SkillKind
from chimera.governance.validator import SkillValidator
from chimera.providers.gateway import Message, SupportsComplete
from chimera.telemetry import get_logger

_log = get_logger("evolution.evolver")

_PROPOSE_SYSTEM = (
    "You convert a successfully completed task into a REUSABLE skill with a TRS reasoning "
    "card. Reply with ONLY a JSON object with keys: "
    '"name" (snake_case), "description" (one line), '
    '"prompt_template" (a reusable instruction with {placeholder} variables for inputs), '
    '"trigger" (when this applies), "do" (the minimal recipe), "avoid" (anti-patterns), '
    '"check" (must-verify constraints), "risk" (edge cases), '
    '"triggers" (a JSON list of 5-15 retrieval keywords). '
    "Do NOT include instance-specific constants or full code; steps must be executable/checkable."
)
_PROPOSE_ANTIPATTERN_SYSTEM = (
    "You convert a RECURRING FAILURE into an anti-pattern reasoning card that warns future "
    "attempts. Reply with ONLY a JSON object with keys: "
    '"name" (snake_case), "description" (one line naming the mistake), '
    '"trigger" (the situation where this mistake happens), "do" (the correct approach instead), '
    '"avoid" (the specific mistake to avoid), "check" (how to verify you did not repeat it), '
    '"risk" (why it is tempting / when it recurs), "triggers" (JSON list of 5-15 keywords). '
    "Do NOT include a prompt_template, instance-specific constants, or full code."
)
_REFINE_SYSTEM = (
    "Improve a skill's prompt_template given examples of how it failed. Reply with ONLY "
    'a JSON object: {"prompt_template": "the improved template"}.'
)
_DISTILL_SYSTEM = (
    "You are given a task, a FAILED attempt, and a later PASSED attempt at the SAME task. Extract "
    "the SPECIFIC correction that turned the failure into a success, as a reusable anti-pattern "
    "reasoning card. Reply with ONLY a JSON object with keys: "
    '"name" (snake_case), "description" (one line naming the fix), '
    '"trigger" (the situation where the mistake happens), "do" (the corrective step that fixed it), '
    '"avoid" (the specific mistake the failed attempt made), "check" (how to verify the fix), '
    '"risk" (when the mistake tends to recur), "triggers" (JSON list of 5-15 retrieval keywords). '
    "Do NOT include a prompt_template, instance-specific constants, or full code."
)
_JSON = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json(text: str) -> dict[str, Any] | None:
    match = _JSON.search(text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _card_fields(data: dict[str, Any]) -> dict[str, Any]:
    raw_triggers = data.get("triggers", [])
    triggers = [str(t) for t in raw_triggers] if isinstance(raw_triggers, list) else []
    return {
        "trigger": str(data.get("trigger", "")),
        "do": str(data.get("do", "")),
        "avoid": str(data.get("avoid", "")),
        "check": str(data.get("check", "")),
        "risk": str(data.get("risk", "")),
        "triggers": triggers,
    }


def _bump(version: str) -> str:
    parts = version.split(".")
    try:
        parts[-1] = str(int(parts[-1]) + 1)
    except (ValueError, IndexError):
        return version
    return ".".join(parts)


class SkillEvolver:
    """Proposes, tests and refines learned skills with a model backend."""

    def __init__(self, backend: SupportsComplete, model: str | None = None) -> None:
        self.backend = backend
        self.model = model

    def _build_skill(self, data: dict[str, Any], *, kind: SkillKind) -> LearnedSkill:
        return LearnedSkill(
            name=str(data["name"]),
            description=str(data["description"]),
            prompt_template=str(data.get("prompt_template", "")),
            kind=kind,
            backend=self.backend,
            model=self.model,
            **_card_fields(data),
        )

    def propose(self, task: str, solution: str) -> LearnedSkill | None:
        user = f"Task:\n{task}\n\nSuccessful approach/solution:\n{solution}"
        raw = self.backend.complete(
            [Message(role="system", content=_PROPOSE_SYSTEM), Message(role="user", content=user)],
            model=self.model,
            temperature=0.2,
        ).content
        data = _parse_json(raw)
        if not data or not all(k in data for k in ("name", "description", "prompt_template")):
            _log.debug("proposal could not be parsed")
            return None
        return self._build_skill(data, kind="pattern")

    def propose_failure_card(self, task: str, detail: str) -> LearnedSkill | None:
        """Distill a recurring failure into an advisory anti-pattern card (no template).

        Returns None unless the card carries both Do and Check — the TRS rule that an
        anti-pattern lesson is useless without a corrective action and a way to verify it.
        """
        user = f"Task:\n{task}\n\nWhat went wrong (recurring failure):\n{detail}"
        raw = self.backend.complete(
            [
                Message(role="system", content=_PROPOSE_ANTIPATTERN_SYSTEM),
                Message(role="user", content=user),
            ],
            model=self.model,
            temperature=0.2,
        ).content
        data = _parse_json(raw)
        if not data or not all(k in data for k in ("name", "description")):
            _log.debug("anti-pattern proposal could not be parsed")
            return None
        card = self._build_skill(data, kind="anti_pattern")
        if not (card.do.strip() and card.check.strip()):
            _log.debug("discarded anti-pattern card %s (missing Do/Check)", card.name)
            return None
        return card

    def distill_correction(
        self, task: str, failed: str, passed: str
    ) -> LearnedSkill | None:
        """Distill the fix that turned a FAILED attempt into a PASSED one into an anti-pattern card.

        This is CrewAI's ``train()`` distillation mechanic with the human replaced by the eval: the
        (failed, passed) pair is a *verified* correction (the tests said so), so no human feedback is
        needed. Returns an advisory card (no template) carrying both Do (the fix) and Check, or None.
        """
        user = f"Task:\n{task}\n\nFAILED attempt:\n{failed}\n\nPASSED attempt:\n{passed}"
        raw = self.backend.complete(
            [Message(role="system", content=_DISTILL_SYSTEM), Message(role="user", content=user)],
            model=self.model,
            temperature=0.2,
        ).content
        data = _parse_json(raw)
        if not data or not all(k in data for k in ("name", "description")):
            _log.debug("correction distillation could not be parsed")
            return None
        card = self._build_skill(data, kind="anti_pattern")
        if not (card.do.strip() and card.check.strip()):
            _log.debug("discarded correction card %s (missing Do/Check)", card.name)
            return None
        return card

    def test_skill(
        self,
        skill: LearnedSkill,
        test_input: dict[str, str],
        check: Callable[[str], bool],
    ) -> bool:
        result = skill.execute(**test_input)
        return result.ok and check(result.output)

    def evolve(
        self,
        task: str,
        solution: str,
        *,
        test_input: dict[str, str],
        check: Callable[[str], bool],
        validator: SkillValidator | None = None,
    ) -> LearnedSkill | None:
        """Propose a skill and keep it only if it validates and passes the test.

        When a ``validator`` is given, a proposal that fails static validation
        (the constrained edit surface) is rejected before it is ever run.
        """
        skill = self.propose(task, solution)
        if skill is None:
            return None
        if validator is not None and not validator.validate(skill.to_dict()).accepted:
            _log.debug("rejected learned skill %s (failed validation)", skill.name)
            return None
        if self.test_skill(skill, test_input, check):
            _log.debug("kept learned skill %s", skill.name)
            return skill
        _log.debug("discarded learned skill %s (failed test)", skill.name)
        return None

    def refine(self, skill: LearnedSkill, failures: list[str]) -> LearnedSkill:
        user = "Template:\n" + skill.prompt_template + "\n\nFailures:\n" + "\n".join(failures)
        raw = self.backend.complete(
            [Message(role="system", content=_REFINE_SYSTEM), Message(role="user", content=user)],
            model=self.model,
            temperature=0.2,
        ).content
        data = _parse_json(raw)
        if data and "prompt_template" in data:
            new_template = str(data["prompt_template"])
            # Telemetry label only (EvoPolicyGym): did this refinement change the mechanism
            # or just tweak constants? Meaningful only for code-bearing templates; prose
            # templates report 'unknown'. Never used as an accept/reject gate.
            _log.debug("refine %s edit class: %s", skill.name, classify_edit(skill.prompt_template, new_template))
            return LearnedSkill(
                name=skill.name,
                description=skill.description,
                prompt_template=new_template,
                version=_bump(skill.version),
                backend=self.backend,
                model=self.model,
            )
        return skill
