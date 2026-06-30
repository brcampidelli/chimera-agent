"""The meta-agent — an agent that designs other agents (recursive self-improvement).

Two safeguards from the Meta-Agent Challenge are baked in:

* **Isolation** — a designed agent's tools are filtered to an allowed list, so the
  meta-agent cannot grant arbitrary capabilities.
* **Hidden-test separation** — evaluation runs against a *visible* check and a
  separate *hidden* check. Passing the visible one but failing the hidden one flags
  likely reward-hacking/overfitting instead of crediting it as success.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from chimera.orchestration.roles import Role, RoleAgent
from chimera.providers.gateway import Message, SupportsComplete
from chimera.telemetry import get_logger

_log = get_logger("ecosystem.meta_agent")

_DESIGN_SYSTEM = (
    "You design a specialized agent for a task. Reply with ONLY a JSON object: "
    '{"name": "snake_case_name", "role_prompt": "the agent system prompt", '
    '"tools": ["tool names chosen ONLY from the allowed list"]}.'
)
_JSON = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json(text: str) -> dict[str, object] | None:
    match = _JSON.search(text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


@dataclass
class AgentBlueprint:
    name: str
    role_prompt: str
    tools: list[str] = field(default_factory=list)
    model: str | None = None


@dataclass
class MetaEvalReport:
    visible_passed: bool
    hidden_passed: bool

    @property
    def reward_hacking_suspected(self) -> bool:
        """Passed the visible check but failed the hidden one."""
        return self.visible_passed and not self.hidden_passed

    @property
    def genuine_success(self) -> bool:
        return self.visible_passed and self.hidden_passed


class MetaAgent:
    """Designs, builds and evaluates specialized agents."""

    def __init__(
        self,
        backend: SupportsComplete,
        *,
        allowed_tools: list[str],
        model: str | None = None,
    ) -> None:
        self.backend = backend
        self.allowed_tools = set(allowed_tools)
        self.model = model

    def design(self, task: str) -> AgentBlueprint | None:
        user = f"Task to build an agent for:\n{task}\n\nAllowed tools: {sorted(self.allowed_tools)}"
        raw = self.backend.complete(
            [Message(role="system", content=_DESIGN_SYSTEM), Message(role="user", content=user)],
            model=self.model,
            temperature=0.2,
        ).content
        data = _parse_json(raw)
        if not data or "name" not in data or "role_prompt" not in data:
            _log.debug("meta-agent design could not be parsed")
            return None
        requested_raw = data.get("tools")
        requested = requested_raw if isinstance(requested_raw, list) else []
        tools = [t for t in requested if isinstance(t, str) and t in self.allowed_tools]
        return AgentBlueprint(
            name=str(data["name"]),
            role_prompt=str(data["role_prompt"]),
            tools=tools,
            model=self.model,
        )

    def build(self, blueprint: AgentBlueprint, backend: SupportsComplete | None = None) -> RoleAgent:
        role = Role(blueprint.name, blueprint.role_prompt, blueprint.model)
        return RoleAgent(role, backend or self.backend)

    @staticmethod
    def evaluate(
        blueprint: AgentBlueprint,
        run: Callable[[AgentBlueprint], str],
        *,
        visible_check: Callable[[str], bool],
        hidden_check: Callable[[str], bool],
    ) -> MetaEvalReport:
        """Run the designed agent and grade it on a visible AND a hidden check."""
        output = run(blueprint)
        return MetaEvalReport(
            visible_passed=bool(visible_check(output)),
            hidden_passed=bool(hidden_check(output)),
        )
