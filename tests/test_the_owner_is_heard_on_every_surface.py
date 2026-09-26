"""The owner's identity reaches every agent a person talks to.

Study 25 (`bench/PLAN-study25-system-prompts.md` §4, defect 1) found the identity set in Settings
(name, language, standing instructions) missing from 13 surfaces. Among them was the bot that
`serve --discord` starts, which is the one the production deployment runs. The Settings screen said
the identity was applied on every surface, and a comment in the CLI said the Discord bot and the app
shared it. Both were true only of the app's own bot.

The cause was structural. Each command builds its own `AgentConfig`, and passing `instructions=` was
something each one had to remember. So this test is structural too: every `AgentConfig(...)` in the
package must be classified.
- A surface that answers a person must pass the owner's instructions.
- Anything else must say why it does not.

A new command cannot be added without choosing.
"""

from __future__ import annotations

import ast
from pathlib import Path

from chimera.core.instructions import AgentIdentity, for_home, render, save

ROOT = Path(__file__).resolve().parent.parent

#: (file, top-level definition) that build an agent a person talks to, or a run a person started.
ANSWERS_A_PERSON: frozenset[tuple[str, str]] = frozenset({
    ("chimera/api/app.py", "_build_solve_agent"),
    ("chimera/api/code_api.py", "register_code_api"),
    ("chimera/cli/main.py", "agent"),
    ("chimera/cli/main.py", "chat"),
    ("chimera/cli/main.py", "assist"),
    ("chimera/cli/main.py", "tui"),
    ("chimera/cli/main.py", "serve"),
    ("chimera/cli/main.py", "desktop_app"),
    ("chimera/cli/main.py", "_start_cron_daemon"),
    ("chimera/cli/main.py", "acp_server"),
    ("chimera/cli/main.py", "_serve_mcp"),
    ("chimera/cli/main.py", "_build_a2a"),
    ("chimera/cli/main.py", "_serve_platform"),
    ("chimera/cli/main.py", "solve"),
    ("chimera/cli/main.py", "solve_batch"),
    ("chimera/scheduler/job_runner.py", "make_run_job"),
    ("chimera/server/manager.py", "MessagingManager"),
})

#: Everything else, each with the reason it does not carry the owner's identity.
EXEMPT: dict[tuple[str, str], str] = {
    ("chimera/cli/main.py", "sandbox_bench"): "a bench: its prompt is part of the instrument",
    ("chimera/cli/main.py", "_right_hand_builder"): "the scenarios bench; the prompt is the instrument",
    ("chimera/core/agent.py", "Agent"): "the default config of the class itself",
    ("chimera/core/explorer.py", "ContextExplorer"): "answers the main agent with file locations",
    ("chimera/core/research.py", "WebResearcher"): "answers the main agent, which answers the person",
    ("chimera/core/subagent.py", "SubAgentTool"): "answers the main agent, which answers the person",
    ("chimera/kanban/lanes.py", "SolveLane"): "a board lane; its output is a card, not a reply",
    ("chimera/kanban/lanes.py", "AgentLane"): "passes the lane's role through `instructions`",
    ("chimera/orchestration/lifecycle.py", "lifecycle_crew"): "an internal pipeline of roles",
    ("chimera/orchestration/roles.py", "RoleAgent"): "receives the owner's identity from its caller",
    ("chimera/prompts/snapshots.py", "assembled_examples"): "fixed inputs for the prompt snapshots",
    ("chimera/workflow/executors.py", "build_executors"): "workflow steps, not a conversation",
}


def _agent_configs() -> list[tuple[str, str, ast.Call]]:
    found: list[tuple[str, str, ast.Call]] = []
    for path in sorted((ROOT / "chimera").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for top in tree.body:
            name = getattr(top, "name", None)
            if name is None:
                continue
            for node in ast.walk(top):
                func = getattr(node, "func", None)
                called = getattr(func, "id", None) or getattr(func, "attr", None)
                if isinstance(node, ast.Call) and called == "AgentConfig":
                    found.append((rel, name, node))
    return found


def test_every_agent_config_is_classified() -> None:
    unclassified = sorted(
        {(rel, name) for rel, name, _ in _agent_configs()} - ANSWERS_A_PERSON - set(EXEMPT)
    )
    assert not unclassified, (
        "AgentConfig built somewhere this test does not know about. If a person talks to it, pass "
        f"instructions=owner_identity(home) and add it to ANSWERS_A_PERSON; if not, say why in EXEMPT: {unclassified}"
    )


def test_every_surface_that_answers_a_person_passes_the_owner() -> None:
    missing = [
        f"{rel}:{node.lineno} ({name})"
        for rel, name, node in _agent_configs()
        if (rel, name) in ANSWERS_A_PERSON and "instructions" not in {k.arg for k in node.keywords}
    ]
    assert not missing, f"these agents answer a person without the owner's identity: {missing}"


def test_every_surface_that_answers_a_person_keeps_its_system_message_stable() -> None:
    """Study 25, wave 2: the same surfaces put what changes per turn in the turn context, so the
    system message a provider caches is the same bytes on every turn."""
    missing = [
        f"{rel}:{node.lineno} ({name})"
        for rel, name, node in _agent_configs()
        if (rel, name) in ANSWERS_A_PERSON
        and not any(k.arg == "turn_context" and getattr(k.value, "value", None) is True for k in node.keywords)
    ]
    assert not missing, f"these agents answer a person without turn_context=True: {missing}"


def test_the_lists_do_not_go_stale() -> None:
    built = {(rel, name) for rel, name, _ in _agent_configs()}
    assert built >= ANSWERS_A_PERSON, sorted(ANSWERS_A_PERSON - built)
    assert set(EXEMPT) <= built, sorted(set(EXEMPT) - built)
    assert not ANSWERS_A_PERSON & set(EXEMPT)


def test_for_home_is_the_rendered_identity_or_nothing(tmp_path: Path) -> None:
    assert for_home(tmp_path) == ""  # nothing configured: no block, no tokens spent on nothing
    identity = AgentIdentity(name="Lia", language="Portuguese (Brazil)", instructions="Seja breve.")
    save(tmp_path, identity)
    assert for_home(tmp_path) == render(identity)
    assert "Always answer in Portuguese (Brazil)" in for_home(tmp_path)
