"""Which external agents Chimera knows how to launch, and what each one needs to work.

A short, explicit catalogue rather than a discovery mechanism. Discovery would mean guessing at a
command line and reporting "not available" when the guess was wrong, which is the least useful
sentence a diagnostic can produce. Three entries, each verified against its own documentation, plus
``custom`` for everything else — including the adapters this file deliberately does not claim to
have checked.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from chimera.proc.stdio import resolve_program


@dataclass(frozen=True)
class AcpAgentSpec:
    """How to start one external agent, and what it needs from the environment."""

    key: str
    label: str
    argv: list[str]
    #: Environment variables this agent needs that :func:`chimera.sandbox.local._child_env` strips.
    #:
    #: That scrubber removes anything matching API_KEY / TOKEN / SECRET so a shell command cannot
    #: echo a provider key. An ACP agent is not a shell command — it is a program whose entire job
    #: needs a key — so the exception is named here, one variable at a time. Passing the whole
    #: environment would be easier and would hand every future adapter every key on the machine.
    passthrough_env: tuple[str, ...] = ()
    #: The command that installs this agent, and NOTHING else — no prose around it.
    #:
    #: The desktop splices this field into a translated sentence ("Not installed here — run {hint}"),
    #: so prose kept in here arrives untranslated: a Portuguese tooltip read "Não instalado aqui —
    #: npm i -g @google/gemini-cli (the ACP mode is flagged experimental upstream)". A command must
    #: not be translated and a sentence must, so one string holding both forces every UI to pick a
    #: single wrong answer for the pair. Anything explanatory belongs in ``notes``, which the
    #: desktop renders from its own dictionary.
    install_hint: str = ""
    #: True when the agent has file and shell tools of its own, so our handlers are an offer it can
    #: decline. Drives the sentence the posture note has to show; see the package docstring.
    writes_directly: bool = True
    notes: str = ""
    extra: dict[str, str] = field(default_factory=dict)


#: `@zed-industries/claude-code-acp` was RENAMED to `@agentclientprotocol/claude-agent-acp`; the old
#: name still resolves and no longer receives updates. Worth pinning the new one rather than the
#: name most search results still show.
CLAUDE = AcpAgentSpec(
    key="claude",
    label="Claude Code",
    argv=["npx", "-y", "@agentclientprotocol/claude-agent-acp"],
    passthrough_env=("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CONFIG_DIR"),
    install_hint="npm i -g @agentclientprotocol/claude-agent-acp",
    notes="Needs Node 22+ and npx on PATH. Uses the Claude Agent SDK, which has its own file and shell tools.",
)

GEMINI = AcpAgentSpec(
    key="gemini",
    label="Gemini CLI",
    argv=["gemini", "--experimental-acp"],
    passthrough_env=("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS"),
    install_hint="npm i -g @google/gemini-cli",
    notes="ACP mode is experimental upstream and its behaviour may change between releases.",
)

#: No built-in Codex entry. Codex reaches ACP through third-party adapters that this project has not
#: run, and listing an unverified command would turn "we did not check" into "supported". Point a
#: `custom` provider at whichever adapter you actually have.
CUSTOM = AcpAgentSpec(
    key="custom",
    label="Custom command",
    argv=[],
    install_hint="chimera.acp.command",
    notes="Whatever you point it at. Chimera makes no claim about what it does with your files.",
)

_CATALOGUE: dict[str, AcpAgentSpec] = {a.key: a for a in (CLAUDE, GEMINI, CUSTOM)}


def spec_for(key: str) -> AcpAgentSpec | None:
    """The spec for a provider key, or None when nothing is registered under it."""
    return _CATALOGUE.get(key.strip().lower())


def is_installed(spec: AcpAgentSpec) -> bool:
    """Whether the program this spec launches resolves on this machine right now.

    Resolution, not execution: starting an agent to find out whether it is there costs seconds and a
    model connection. It answers "is the launcher present", which is the question that separates
    "install this" from "this is installed and failing", and the failure message says which.
    """
    if not spec.argv:
        return False
    program = spec.argv[0]
    resolved = resolve_program(program)
    return resolved != program or os.path.exists(resolved)


def available_agents() -> list[dict[str, object]]:
    """Every known agent with its live availability — the shape ``chimera doctor`` reports.

    Capability by capability, ``available: true/false``, because a frozen sidecar built by CI on a
    machine nobody looked at is exactly where "it should work" stops being evidence.
    """
    out: list[dict[str, object]] = []
    for spec in (CLAUDE, GEMINI):
        out.append(
            {
                "key": spec.key,
                "label": spec.label,
                "available": is_installed(spec),
                "command": " ".join(spec.argv),
                "install_hint": spec.install_hint,
                "writes_directly": spec.writes_directly,
                "notes": spec.notes,
            }
        )
    return out


def child_env(spec: AcpAgentSpec) -> dict[str, str]:
    """The environment to launch this agent with.

    Starts from the scrubbed environment every other child gets, then puts back exactly the
    variables this agent declared — by name, from the parent's real environment.

    Without this the agent starts and fails in the least legible way available: it is a program
    whose whole purpose requires a provider key, launched by a scrubber built to make sure a child
    never sees one. The result is an authentication error from a provider the user is certain they
    configured.
    """
    from chimera.sandbox.local import _child_env

    env = _child_env()
    for name in spec.passthrough_env:
        value = os.environ.get(name)
        if value:
            env[name] = value
    return env
