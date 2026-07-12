"""SKILL.md interop + progressive disclosure (M15-A2).

Anthropic's Agent Skills became an open cross-platform standard (agentskills.io): a skill is a
directory with a ``SKILL.md`` — YAML frontmatter (metadata) + a markdown body (instructions) —
plus optional ``scripts/``/``references/``/``assets/``. CrewAI and others adopted it. Speaking that
format makes a Chimera skill portable to/from the whole ecosystem.

Two things this module adds beyond a plain parser:

1. **Progressive disclosure** (L1/L2/L3). Retrieval only needs the *metadata* (name + description +
   triggers) to decide relevance; the *instructions* load when the skill is chosen; the *resources*
   load only if the procedure actually needs them. Loading the cheapest level first is a direct
   attack on the token cost of carrying skills in context.
2. **Provenance + taint in the frontmatter.** A Chimera skill records whether it was distilled from a
   clean or a tainted run (Zombie-Agents lineage). Nobody else crosses a skill marketplace with a
   security label — a tainted imported skill stays ``pending`` until a human approves it.

Round-trips losslessly with :class:`chimera.evolution.learned_skill.LearnedSkill`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from chimera.evolution.learned_skill import LearnedSkill

_CARD_SECTIONS = ("Trigger", "Do", "Avoid", "Check", "Risk")


class Disclosure(IntEnum):
    """Progressive disclosure level — load the cheapest that answers the question."""

    METADATA = 1  # name + description + triggers — for retrieval/relevance
    INSTRUCTIONS = 2  # + the how-to body — when the skill is selected
    RESOURCES = 3  # + resource pointers — only when the procedure needs them


@dataclass
class SkillManifest:
    """The L1 metadata block (YAML frontmatter of a SKILL.md)."""

    name: str
    description: str
    version: str = "0.1.0"
    kind: str = "pattern"
    provenance: str = "clean"  # clean | tainted — Chimera's security lineage
    status: str = "active"  # active | pending | retired
    triggers: list[str] = field(default_factory=list)
    license: str | None = None
    allowed_tools: list[str] = field(default_factory=list)  # CrewAI/Anthropic pre-approval field


@dataclass
class SkillMd:
    """A parsed SKILL.md: metadata (L1) + instructions body (L2) + resource pointers (L3)."""

    manifest: SkillManifest
    instructions: str = ""
    resources: list[str] = field(default_factory=list)

    def disclose(self, level: Disclosure = Disclosure.INSTRUCTIONS) -> str:
        """Render only up to ``level`` — the progressive-disclosure token-cost lever."""
        m = self.manifest
        parts = [f"{m.name}: {m.description}"]
        if m.triggers:
            parts.append("triggers: " + ", ".join(m.triggers))
        if level >= Disclosure.INSTRUCTIONS and self.instructions.strip():
            parts.append(self.instructions.strip())
        if level >= Disclosure.RESOURCES and self.resources:
            parts.append("resources: " + ", ".join(self.resources))
        return "\n\n".join(parts)


def render_skill_md(skill: SkillMd) -> str:
    """Render a SkillMd as SKILL.md text (YAML frontmatter + markdown body)."""
    m = skill.manifest
    front: dict[str, object] = {"name": m.name, "description": m.description, "version": m.version, "kind": m.kind}
    if m.triggers:
        front["triggers"] = m.triggers
    front["provenance"] = m.provenance
    front["status"] = m.status
    if m.license:
        front["license"] = m.license
    if m.allowed_tools:
        front["allowed_tools"] = m.allowed_tools
    fm = yaml.safe_dump(front, sort_keys=False, allow_unicode=True).strip()
    body = skill.instructions.strip()
    return f"---\n{fm}\n---\n\n{body}\n" if body else f"---\n{fm}\n---\n"


def parse_skill_md(text: str) -> SkillMd:
    """Parse SKILL.md text into a :class:`SkillMd`. Body without frontmatter is all instructions."""
    front: dict[str, object] = {}
    body = text
    if text.lstrip().startswith("---"):
        stripped = text.lstrip()
        end = stripped.find("\n---", 3)
        if end != -1:
            raw = stripped[3:end]
            body = stripped[end + 4 :]
            try:
                loaded = yaml.safe_load(raw)
            except yaml.YAMLError:  # malformed frontmatter (untrusted import) -> treat as body-only
                loaded = None
            if isinstance(loaded, dict):
                front = loaded
    triggers = front.get("triggers") or []
    allowed = front.get("allowed_tools") or []
    manifest = SkillManifest(
        name=str(front.get("name", "unnamed")),
        description=str(front.get("description", "")),
        version=str(front.get("version", "0.1.0")),
        kind="anti_pattern" if front.get("kind") == "anti_pattern" else "pattern",
        provenance="tainted" if front.get("provenance") == "tainted" else "clean",
        status=str(front.get("status", "active")),
        triggers=[str(t) for t in triggers] if isinstance(triggers, list) else [],
        license=str(front["license"]) if front.get("license") else None,
        allowed_tools=[str(t) for t in allowed] if isinstance(allowed, list) else [],
    )
    return SkillMd(manifest=manifest, instructions=body.strip())


def from_learned(skill: LearnedSkill) -> SkillMd:
    """Export a LearnedSkill to a SkillMd (frontmatter + a card/template instructions body)."""
    manifest = SkillManifest(
        name=skill.name,
        description=skill.description,
        version=skill.version,
        kind=skill.kind,
        provenance=skill.provenance,
        status=skill.status,
        triggers=list(skill.triggers),
    )
    sections = []
    for label in _CARD_SECTIONS:
        value = getattr(skill, label.lower(), "").strip()
        if value:
            sections.append(f"## {label}\n{value}")
    if skill.prompt_template.strip():
        sections.append("## Template\n```\n" + skill.prompt_template.strip() + "\n```")
    if not sections:  # a bare skill with neither card nor template
        sections.append(skill.description.strip())
    return SkillMd(manifest=manifest, instructions="\n\n".join(sections))


def to_learned(
    skillmd: SkillMd, *, backend: object = None, model: str | None = None
) -> LearnedSkill:
    """Import a SkillMd into a LearnedSkill, recovering the card fields + template from the body."""
    from chimera.evolution.learned_skill import LearnedSkill

    fields, template = _split_sections(skillmd.instructions)
    m = skillmd.manifest
    # A tainted imported skill is held pending — never silently enters retrieval (Zombie Agents).
    status = "pending" if m.provenance == "tainted" else m.status
    # Round-trip the real status (incl. `provisional`, which is on-probation) and default an
    # unknown/mistyped status to `pending` — never silently promote it to full `active` retrieval.
    final_status = status if status in ("active", "pending", "retired", "provisional") else "pending"
    return LearnedSkill(
        name=m.name,
        description=m.description,
        version=m.version,
        prompt_template=template,
        trigger=fields.get("trigger", ""),
        do=fields.get("do", ""),
        avoid=fields.get("avoid", ""),
        check=fields.get("check", ""),
        risk=fields.get("risk", ""),
        triggers=list(m.triggers),
        kind="anti_pattern" if m.kind == "anti_pattern" else "pattern",
        status=final_status,  # type: ignore[arg-type]
        provenance="tainted" if m.provenance == "tainted" else "clean",
        backend=backend,  # type: ignore[arg-type]
        model=model,
    )


def _split_sections(body: str) -> tuple[dict[str, str], str]:
    """Split a card/template body into {field: text} + the template (from a ``## Template`` block)."""
    fields: dict[str, str] = {}
    template = ""
    current: str | None = None
    buf: list[str] = []
    in_template = False

    def _flush() -> None:
        nonlocal template
        if current is None:
            return
        text = "\n".join(buf).strip()
        if current == "template":
            template = _strip_code_fence(text)
        elif current in {s.lower() for s in _CARD_SECTIONS}:
            fields[current] = text

    for line in body.splitlines():
        header = line.strip()
        if header.startswith("## "):
            _flush()
            current = header[3:].strip().lower()
            in_template = current == "template"
            buf = []
            continue
        buf.append(line)
    _flush()
    _ = in_template
    return fields, template


def _strip_code_fence(text: str) -> str:
    lines = text.strip().splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()
