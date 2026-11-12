"""Which surfaces may carry the owner's personal ~/.claude/CLAUDE.md, and what a reproducible run reads.

The owner's file is personal: a bot, a webhook, a job or a subagent that a stranger can talk to must not
hand it back. So it is opt-in per surface (``AgentConfig.user_claude_md``), and only the owner's own
surfaces set it. Reproducible runs (the prompt snapshots, the benches and the carrier eval) pin the whole
Claude switch off, so their output does not depend on the machine that produced it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from chimera.core import agents_md
from chimera.core.agent import Agent, AgentConfig
from chimera.core.agents_md import load_agent_instructions, load_user_claude_instructions
from chimera.tools import ToolRegistry

ROOT = Path(__file__).resolve().parent.parent
CANARY = "PERSONAL CANARY 7f3a"


class _NoBackend:
    def complete(self, *_: object, **__: object) -> object:  # pragma: no cover - never called
        raise AssertionError("composition must not call the model")


def _write(root: Path, rel: str, body: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture()
def personal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The owner's Claude folder, holding a personal file with a canary the tests look for."""
    folder = tmp_path / "owner-claude"
    _write(folder, "CLAUDE.md", f"{CANARY}\n")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(folder))
    return folder


def test_a_default_agent_does_not_carry_the_owners_personal_file(tmp_path: Path, personal: Path) -> None:
    assert AgentConfig().user_claude_md is False
    prompt = Agent(_NoBackend(), ToolRegistry(), AgentConfig(claude_compat=True)).compose_system_prompt("x")
    assert CANARY not in prompt


def test_an_owner_surface_agent_reads_the_personal_file(tmp_path: Path, personal: Path) -> None:
    prompt = Agent(
        _NoBackend(), ToolRegistry(), AgentConfig(claude_compat=True, user_claude_md=True)
    ).compose_system_prompt("x")
    assert CANARY in prompt


def test_the_switch_off_keeps_the_personal_file_out_even_for_an_owner_surface(tmp_path: Path, personal: Path) -> None:
    prompt = Agent(
        _NoBackend(), ToolRegistry(), AgentConfig(claude_compat=False, user_claude_md=True)
    ).compose_system_prompt("x")
    assert CANARY not in prompt


def test_the_owner_surfaces_opt_in_and_the_desktop_turn_excludes_guests() -> None:
    chat = (ROOT / "chimera" / "cli" / "commands" / "chat.py").read_text(encoding="utf-8")
    code_api = (ROOT / "chimera" / "api" / "code_api.py").read_text(encoding="utf-8")
    assert chat.count("user_claude_md=True") == chat.count("AgentConfig(") == 3
    assert "user_claude_md=not guest" in code_api


def test_the_prompt_snapshots_do_not_carry_the_owners_file(personal: Path) -> None:
    from chimera.prompts.snapshots import assembled_examples

    examples = assembled_examples()
    assert examples and all(CANARY not in text for text in examples.values())


@pytest.mark.parametrize(
    "rel",
    ["chimera/prompts/snapshots.py", "chimera/cli/commands/bench.py", "chimera/eval/agents_md_carrier.py"],
)
def test_reproducible_runs_pin_the_claude_switch_off(rel: str) -> None:
    source = (ROOT / rel).read_text(encoding="utf-8")
    assert "claude_compat=False" in source


def test_agents_md_and_a_claude_file_that_imports_it_show_the_rule_once(tmp_path: Path) -> None:
    _write(tmp_path, "AGENTS.md", "DUP RULE")
    _write(tmp_path, "CLAUDE.md", "@AGENTS.md\n")
    text = load_agent_instructions(tmp_path, claude_compat=True).text
    assert text.count("DUP RULE") == 1


def test_a_claude_file_that_is_a_link_to_agents_md_shows_the_rule_once(tmp_path: Path) -> None:
    _write(tmp_path, "AGENTS.md", "LINK DUP RULE")
    try:
        os.symlink(tmp_path / "AGENTS.md", tmp_path / "CLAUDE.md")
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create symlinks")
    found = load_agent_instructions(tmp_path, claude_compat=True)
    assert found.text.count("LINK DUP RULE") == 1
    assert found.sources == ("AGENTS.md",)


def test_agents_md_is_never_expanded_by_its_imports(tmp_path: Path) -> None:
    """The import rules are for Claude's files. AGENTS.md is read as it always was: an @line is text."""
    _write(tmp_path, "x.md", "IMPORTED BODY")
    _write(tmp_path, "AGENTS.md", "@x.md\n")
    text = load_agent_instructions(tmp_path, claude_compat=True).text
    assert "IMPORTED BODY" not in text and "@x.md" in text


def test_the_owners_own_file_is_accepted_even_as_a_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dotfiles = tmp_path / "dotfiles"
    _write(dotfiles, "CLAUDE.md", "DOTFILE OWNER RULE")
    config = tmp_path / "config"
    config.mkdir()
    try:
        os.symlink(dotfiles / "CLAUDE.md", config / "CLAUDE.md")
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create symlinks")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    assert "DOTFILE OWNER RULE" in load_user_claude_instructions().text


def test_the_owners_imports_stay_inside_the_owners_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "config"
    _write(config, "CLAUDE.md", "@sub/a.md\n@../escape.md\n")
    _write(config, "sub/a.md", "SUB A RULE")
    _write(tmp_path, "escape.md", "ESCAPE RULE")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    text = load_user_claude_instructions().text
    assert "SUB A RULE" in text
    assert "ESCAPE RULE" not in text and "@../escape.md" in text


def test_a_skipped_import_is_logged_at_debug_not_silently(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _write(tmp_path, "CLAUDE.md", "@../outside.md\n")
    with caplog.at_level("DEBUG", logger="chimera.core.agents_md"):
        load_agent_instructions(tmp_path, claude_compat=True)
    assert any("leaves the project" in r.getMessage() or "refused by its text" in r.getMessage() for r in caplog.records)
    assert agents_md.MAX_IMPORTS == 50
