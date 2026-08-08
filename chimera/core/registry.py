"""The agents you have, beyond the one you talk to.

Everything needed to define an agent was already written twice — ``orchestration.roles.Role`` (name,
system prompt, model, tool allowlist, with the allowlist enforced by filtering the registry before
the loop) and ``ecosystem.meta_agent.AgentBlueprint`` (name, role prompt, tools, model). Neither was
persisted, so an agent existed only for the length of the call that built it. That is the whole gap
between "this framework can run several specialised agents" and "I can have several agents".

**The default agent is not in here.** ``chimera.core.instructions`` holds the identity of the agent
you converse with; this holds the ones you *dispatch work to*. They are different things and folding
them together would make the list of workers include the thing doing the dispatching — which reads
fine until you try to send it a card. The two share their field names on purpose, so an agent can be
promoted or copied between them without a translation layer.

An entry's ``id`` is its handle everywhere else: it is what a Kanban card names in ``lane``, which is
why it is a slug rather than free text. ``KanbanCard.lane`` is already a plain string and
``dispatch()`` already takes its runners as an injected mapping, so an agent can become a lane
without the dispatcher learning anything new.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, field_validator

from chimera.telemetry import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chimera.orchestration.roles import Role

_log = get_logger("core.registry")

#: Where the registry lives, under ``Settings.home``. Plural, beside the singular ``agent.json``.
REGISTRY_FILE = "agents.json"

#: Ids are handles, not prose: they name a Kanban lane and appear in receipts and logs.
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")

#: Same budget as the conversational agent's instructions — an agent is an agent.
MAX_INSTRUCTION_CHARS = 4_000


class AgentDef(BaseModel):
    """One agent you can send work to."""

    id: str
    """Its handle. Lowercase slug, because it names a Kanban lane and shows up in logs."""

    name: str = ""
    """What to call it on screen. Empty falls back to the id, so an entry is never nameless."""

    instructions: str = ""
    """Its role, in your words — the same field the conversational agent has, deliberately."""

    model: str = ""
    """The model it runs on. Empty inherits the ladder, which is what most entries should do:
    pinning a model per agent is how a registry quietly becomes a bill nobody planned."""

    allowed_tools: list[str] = []
    """The tools it may use. **Empty means no restriction**, not "no tools".

    That is the opposite of ``Role.allowed_tools``, where ``[]`` is a fully locked worker — and it
    is deliberate: every other list in this project's configuration (``CHIMERA_TOOL_ALLOWLIST``,
    the seams' ``allow_tools``) reads empty as "no allowlist in force", and a registry that inverted
    it would hand someone a toolless agent for leaving a field blank. :func:`as_role` performs the
    translation, in one place, with the conversion visible."""

    @field_validator("id")
    @classmethod
    def _slug(cls, value: str) -> str:
        value = value.strip().lower()
        if not ID_PATTERN.match(value):
            raise ValueError(
                "id must be a lowercase slug: letters, digits, - and _, starting with a letter or "
                "digit, at most 40 characters"
            )
        return value

    @property
    def label(self) -> str:
        return self.name.strip() or self.id


def load(home: Path) -> list[AgentDef]:
    """The registry, in the order it was written. Never raises.

    A file that cannot be read or parsed is an empty registry, not a broken run — the same
    discipline every optional store here follows. An individual entry that no longer validates is
    dropped with a log line rather than taking the rest of the list with it: one bad row should not
    make every other agent disappear.
    """
    path = Path(home) / REGISTRY_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except Exception as exc:  # noqa: BLE001 — a malformed registry must never break a run
        _log.debug("agent registry unreadable (%s): %s", path, exc)
        return []
    if not isinstance(raw, list):
        _log.debug("agent registry is not a list (%s)", path)
        return []
    out: list[AgentDef] = []
    for entry in raw:
        try:
            out.append(AgentDef.model_validate(entry))
        except Exception as exc:  # noqa: BLE001
            _log.debug("skipping malformed agent entry: %s", exc)
    return out


def save(home: Path, agents: list[AgentDef]) -> list[AgentDef]:
    """Persist the registry, trimming the bounded fields. Returns what was stored."""
    stored = [
        AgentDef(
            id=a.id,
            name=a.name.strip(),
            instructions=a.instructions.strip()[:MAX_INSTRUCTION_CHARS],
            model=a.model.strip(),
            allowed_tools=[t.strip() for t in a.allowed_tools if t.strip()],
        )
        for a in agents
    ]
    path = Path(home) / REGISTRY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps([a.model_dump() for a in stored], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(path)  # atomic: a half-written registry would be silently ignored by load()
    return stored


def upsert(home: Path, agent: AgentDef) -> list[AgentDef]:
    """Add an agent, or replace the one with the same id. Returns the whole registry.

    Replace rather than merge: a PUT that quietly kept fields the caller omitted would make clearing
    a model or an allowlist impossible, and "I removed that and it came back" is the shape of bug
    nobody reports because they assume they did it wrong.
    """
    agents = [a for a in load(home) if a.id != agent.id]
    agents.append(agent)
    return save(home, agents)


def remove(home: Path, agent_id: str) -> list[AgentDef]:
    """Forget an agent. Cards already filed under its lane are NOT touched.

    They keep their lane name and simply stop being dispatched, which is recoverable — deleting the
    work because its worker was deleted is not, and nothing about "remove this agent" says "and
    throw away what it was asked to do".
    """
    return save(home, [a for a in load(home) if a.id != agent_id])


def get(home: Path, agent_id: str) -> AgentDef | None:
    return next((a for a in load(home) if a.id == agent_id), None)


def as_role(agent: AgentDef) -> Role:
    """The registry entry as an ``orchestration.roles.Role``, which is what actually runs.

    The one place the allowlist convention is translated: empty here means "no restriction", and
    ``Role`` spells that ``None`` — an empty LIST there is a fully locked worker. Doing this
    conversion at every call site is how the two conventions would eventually meet in the middle and
    lock somebody out of their own tools.
    """
    from chimera.orchestration.roles import Role

    return Role(
        name=agent.label,
        system_prompt=agent.instructions,
        model=agent.model or None,
        allowed_tools=agent.allowed_tools or None,
    )
