"""The project's instructions in Claude Code's format, read beside AGENTS.md (``CHIMERA_CLAUDE_COMPAT``).

A repository's CLAUDE.md, CLAUDE.local.md and .claude/CLAUDE.md are read with the same fence, the same
sanitiser and the same budget as AGENTS.md. The owner's ~/.claude/CLAUDE.md is read as owner
instruction. ``@path`` imports are followed inside the project, two levels deep, and nowhere else.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.config import Settings
from chimera.core.agents_md import (
    MAX_FILE_CHARS,
    MAX_IMPORT_DEPTH,
    MAX_TOTAL_CHARS,
    load_agent_instructions,
    load_user_claude_instructions,
)


def _write(root: Path, rel: str, body: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_only_agents_md_is_read_when_the_switch_is_off(tmp_path: Path) -> None:
    """Off is the old behaviour: AGENTS.md alone, and a CLAUDE.md beside it is not read."""
    _write(tmp_path, "AGENTS.md", "AGENTS RULE")
    _write(tmp_path, "CLAUDE.md", "CLAUDE RULE")
    found = load_agent_instructions(tmp_path, claude_compat=False)
    assert found.sources == ("AGENTS.md",)
    assert "CLAUDE RULE" not in found.text


def test_only_claude_md_is_read_when_it_is_the_only_file(tmp_path: Path) -> None:
    _write(tmp_path, "CLAUDE.md", "CLAUDE RULE")
    found = load_agent_instructions(tmp_path, claude_compat=True)
    assert found.sources == ("CLAUDE.md",)
    assert "### CLAUDE.md\nCLAUDE RULE" in found.text


def test_with_both_files_each_is_read_and_labelled_with_its_name(tmp_path: Path) -> None:
    _write(tmp_path, "AGENTS.md", "AGENTS RULE")
    _write(tmp_path, "CLAUDE.md", "CLAUDE RULE")
    found = load_agent_instructions(tmp_path, claude_compat=True)
    assert found.sources == ("AGENTS.md", "CLAUDE.md")
    assert "### AGENTS.md\nAGENTS RULE" in found.text
    assert "### CLAUDE.md\nCLAUDE RULE" in found.text


def test_the_local_and_dot_claude_files_are_read_and_the_local_one_reads_last(tmp_path: Path) -> None:
    _write(tmp_path, "CLAUDE.local.md", "PERSONAL OVERRIDE")
    _write(tmp_path, ".claude/CLAUDE.md", "DOT CLAUDE RULE")
    _write(tmp_path, "CLAUDE.md", "SHARED RULE")
    found = load_agent_instructions(tmp_path, claude_compat=True)
    assert found.sources == ("CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md")
    assert found.text.index("SHARED RULE") < found.text.index("DOT CLAUDE RULE")
    assert found.text.index("DOT CLAUDE RULE") < found.text.index("PERSONAL OVERRIDE")


def test_a_nested_claude_file_follows_the_closest_one_wins_rule(tmp_path: Path) -> None:
    _write(tmp_path, "CLAUDE.md", "ROOT CLAUDE")
    _write(tmp_path, "pkg/CLAUDE.md", "PKG CLAUDE")
    found = load_agent_instructions(tmp_path, focus=["pkg/x.py"], claude_compat=True)
    assert found.sources == ("CLAUDE.md", "pkg/CLAUDE.md")
    assert found.text.index("ROOT CLAUDE") < found.text.index("PKG CLAUDE")


def test_an_import_inside_the_project_is_expanded(tmp_path: Path) -> None:
    _write(tmp_path, "docs/rules.md", "IMPORTED RULE")
    _write(tmp_path, "CLAUDE.md", "Intro\n@docs/rules.md\nOutro\n")
    found = load_agent_instructions(tmp_path, claude_compat=True)
    assert "IMPORTED RULE" in found.text
    assert "@docs/rules.md" not in found.text
    assert "Outro" in found.text


def test_an_import_that_leaves_the_project_is_not_followed(tmp_path: Path) -> None:
    """A path that climbs out of the root, or names an absolute file, is left as the literal line."""
    outside = tmp_path.parent / "claude-compat-outside-secret.md"
    outside.write_text("OUTSIDE SECRET", encoding="utf-8")
    project = tmp_path / "project"
    try:
        _write(project, "CLAUDE.md", "@../claude-compat-outside-secret.md\n@" + outside.as_posix() + "\n")
        found = load_agent_instructions(project, claude_compat=True)
    finally:
        outside.unlink(missing_ok=True)
    assert "OUTSIDE SECRET" not in found.text
    assert "@../claude-compat-outside-secret.md" in found.text
    assert "@" + outside.as_posix() in found.text


def test_an_import_is_followed_two_levels_deep_and_no_further(tmp_path: Path) -> None:
    assert MAX_IMPORT_DEPTH == 2
    _write(tmp_path, "CLAUDE.md", "@a.md\n")
    _write(tmp_path, "a.md", "LEVEL ONE\n@b.md\n")
    _write(tmp_path, "b.md", "LEVEL TWO\n@c.md\n")
    _write(tmp_path, "c.md", "LEVEL THREE")
    found = load_agent_instructions(tmp_path, claude_compat=True)
    assert "LEVEL ONE" in found.text and "LEVEL TWO" in found.text
    assert "LEVEL THREE" not in found.text
    assert "@c.md" in found.text


def test_an_import_cycle_terminates(tmp_path: Path) -> None:
    _write(tmp_path, "CLAUDE.md", "@a.md\n")
    _write(tmp_path, "a.md", "A TEXT\n@b.md\n")
    _write(tmp_path, "b.md", "B TEXT\n@a.md\n")
    found = load_agent_instructions(tmp_path, claude_compat=True)
    assert "A TEXT" in found.text and "B TEXT" in found.text
    assert found.text.count("A TEXT") == 1


def test_an_import_inside_a_code_fence_is_text_not_an_import(tmp_path: Path) -> None:
    _write(tmp_path, "docs/rules.md", "IMPORTED RULE")
    _write(tmp_path, "CLAUDE.md", "```\n@docs/rules.md\n```\n")
    found = load_agent_instructions(tmp_path, claude_compat=True)
    assert "IMPORTED RULE" not in found.text
    assert "@docs/rules.md" in found.text


def test_changing_an_imported_file_changes_the_fingerprint_of_the_block(tmp_path: Path) -> None:
    """The digest covers the bytes the prompt carries, and an imported file is part of them."""
    _write(tmp_path, "docs/rules.md", "version one")
    _write(tmp_path, "CLAUDE.md", "@docs/rules.md\n")
    first = load_agent_instructions(tmp_path, claude_compat=True).provenance
    _write(tmp_path, "docs/rules.md", "version two")
    second = load_agent_instructions(tmp_path, claude_compat=True).provenance
    assert first[0][0] == "CLAUDE.md" and first[0][1] != second[0][1]


def test_the_total_budget_holds_across_agents_and_claude_files(tmp_path: Path) -> None:
    """Both files together never exceed the one budget AGENTS.md already had; the cut is said aloud."""
    filler = "filler line of instructions\n"
    _write(tmp_path, "AGENTS.md", filler * 300)
    _write(tmp_path, "CLAUDE.md", filler * 300)
    found = load_agent_instructions(tmp_path, claude_compat=True)
    assert sum(len(body) for _, body in found.shown) <= MAX_TOTAL_CHARS
    assert found.truncated, "a file squeezed by the budget must be named as truncated"
    assert "was truncated to fit the prompt" in found.text
    assert all(len(body) <= MAX_FILE_CHARS for _, body in found.shown)


def test_the_user_claude_file_is_owner_instruction_not_project_text(tmp_path: Path) -> None:
    claude_home = tmp_path / "home" / ".claude"
    _write(claude_home.parent, ".claude/CLAUDE.md", "OWNER GLOBAL RULE")
    found = load_user_claude_instructions(claude_home)
    assert "Instructions from the person who runs you" in found.text
    assert "### ~/.claude/CLAUDE.md\nOWNER GLOBAL RULE" in found.text
    assert found.sources == ("~/.claude/CLAUDE.md",)
    assert found.provenance and found.provenance[0][0] == "~/.claude/CLAUDE.md"


def test_the_user_claude_file_is_empty_when_absent(tmp_path: Path) -> None:
    assert not load_user_claude_instructions(tmp_path / "no-such-home")


def test_an_import_in_the_user_file_stays_inside_its_own_directory(tmp_path: Path) -> None:
    claude_home = tmp_path / ".claude"
    _write(tmp_path, "secret-outside.md", "OUTSIDE HOME")
    _write(claude_home, "CLAUDE.md", "@../secret-outside.md\n@memory/lang.md\n")
    _write(claude_home, "memory/lang.md", "LANG RULE")
    found = load_user_claude_instructions(claude_home)
    assert "OUTSIDE HOME" not in found.text and "@../secret-outside.md" in found.text
    assert "LANG RULE" in found.text


def test_the_switch_is_on_by_default_and_off_only_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHIMERA_CLAUDE_COMPAT", raising=False)
    assert Settings(_env_file=None).claude_compat is True
    monkeypatch.setenv("CHIMERA_CLAUDE_COMPAT", "0")
    assert Settings(_env_file=None).claude_compat is False


class _NoBackend:
    def complete(self, *_: object, **__: object) -> object:  # pragma: no cover - never called
        raise AssertionError("composition must not call the model")


def test_the_switch_decides_whether_the_composed_prompt_carries_claude_files(tmp_path: Path) -> None:
    from chimera.core.agent import Agent, AgentConfig
    from chimera.tools import ToolRegistry

    _write(tmp_path, "AGENTS.md", "AGENTS PROJECT RULE")
    _write(tmp_path, "CLAUDE.md", "CLAUDE PROJECT RULE")
    on = Agent(_NoBackend(), ToolRegistry(), AgentConfig(project_root=tmp_path, claude_compat=True))
    off = Agent(_NoBackend(), ToolRegistry(), AgentConfig(project_root=tmp_path, claude_compat=False))
    assert "CLAUDE PROJECT RULE" in on.compose_system_prompt("do a thing")
    assert "AGENTS PROJECT RULE" in off.compose_system_prompt("do a thing")
    assert "CLAUDE PROJECT RULE" not in off.compose_system_prompt("do a thing")


def test_the_owner_claude_file_reaches_the_prompt_as_owner_instruction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.core.agent import Agent, AgentConfig
    from chimera.tools import ToolRegistry

    home = tmp_path / "userhome"
    _write(home, ".claude/CLAUDE.md", "OWNER GLOBAL RULE")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))
    monkeypatch.setenv("USERPROFILE", str(home))
    agent = Agent(_NoBackend(), ToolRegistry(), AgentConfig(claude_compat=True, user_claude_md=True))
    assert "OWNER GLOBAL RULE" in agent.compose_system_prompt("do a thing")

    agent_off = Agent(_NoBackend(), ToolRegistry(), AgentConfig(claude_compat=False, user_claude_md=True))
    assert "OWNER GLOBAL RULE" not in agent_off.compose_system_prompt("do a thing")


def test_the_receipt_lists_each_instruction_file_once_in_load_order() -> None:
    from chimera.api.code_api import _instruction_source_rows

    rows = _instruction_source_rows(
        [
            {"path": "AGENTS.md", "sha256": "a", "truncated": False},
            {"path": "CLAUDE.md", "sha256": "b", "truncated": True},
            {"path": "AGENTS.md", "sha256": "a", "truncated": False},
        ]
    )
    assert rows == [
        {"path": "AGENTS.md", "truncated": False},
        {"path": "CLAUDE.md", "truncated": True},
    ]
    assert _instruction_source_rows(None) == []


# ---- hardening: links, lexical imports, budget priority, bounded reads, fan-out, cache, the home ----

import os  # noqa: E402

from chimera.core import agents_md  # noqa: E402


def test_a_claude_file_that_is_a_link_out_of_the_project_is_skipped(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "CLAUDE.md").write_text("LINKED OUT", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    try:
        os.symlink(outside / "CLAUDE.md", project / "CLAUDE.md")
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create symlinks")
    found = load_agent_instructions(project, claude_compat=True)
    assert "LINKED OUT" not in found.text and "CLAUDE.md" not in found.sources


def test_a_dot_claude_directory_that_is_a_link_out_is_skipped(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    _write(outside, "CLAUDE.md", "LINKED DIR RULE")
    project = tmp_path / "project"
    project.mkdir()
    try:
        os.symlink(outside, project / ".claude", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create symlinks")
    found = load_agent_instructions(project, claude_compat=True)
    assert "LINKED DIR RULE" not in found.text


def test_an_import_link_out_of_the_project_is_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("LINKED IMPORT", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    try:
        os.symlink(outside, project / "link.md")
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create symlinks")
    _write(project, "CLAUDE.md", "@link.md\n")
    found = load_agent_instructions(project, claude_compat=True)
    assert "LINKED IMPORT" not in found.text and "@link.md" in found.text


@pytest.mark.parametrize(
    "target",
    [
        "/etc/passwd",
        "\\\\server\\share\\x.md",
        "//server/share/x.md",
        "~/x.md",
        "C:/x.md",
        "C:x.md",
        "../x.md",
        "a\\b.md",
        "",
    ],
)
def test_a_lexically_unsafe_import_is_refused_before_any_filesystem_call(target: str) -> None:
    assert not agents_md._lexically_followable(target)


def test_a_lexically_unsafe_import_never_reaches_the_filesystem(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    real = agents_md._inside

    def spy(path: Path, root: Path) -> Path | None:
        calls.append(str(path))
        return real(path, root)

    monkeypatch.setattr(agents_md, "_inside", spy)
    _write(tmp_path, "CLAUDE.md", "@//server/share/x.md\n@~/secret.md\n")
    found = load_agent_instructions(tmp_path, claude_compat=True)
    assert "@//server/share/x.md" in found.text and "@~/secret.md" in found.text
    assert not any("server" in c or "secret" in c for c in calls)


def test_agents_md_keeps_its_whole_body_before_the_claude_file_takes_budget(tmp_path: Path) -> None:
    """AGENTS.md is budgeted first: a 4,000-character AGENTS.md is shown whole beside a 5,000-character
    CLAUDE.md, which is the one that gets cut."""
    agents_body = "AGENTS HEAD\n" + "agents rule line\n" * 240
    agents_body = agents_body[:4000].rstrip() + "\nAGENTS TAIL"
    claude_body = "CLAUDE HEAD\n" + "claude rule line\n" * 300
    _write(tmp_path, "AGENTS.md", agents_body)
    _write(tmp_path, "CLAUDE.md", claude_body)
    found = load_agent_instructions(tmp_path, claude_compat=True)
    assert "AGENTS TAIL" in found.text
    assert "AGENTS.md" not in found.truncated
    assert "CLAUDE.md" in found.truncated


def test_a_huge_file_is_read_only_up_to_the_byte_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    body = "HEAD\n" + ("pad line\n" * 40_000) + "TAIL MARKER\n"
    _write(tmp_path, "docs/big.md", body)
    _write(tmp_path, "CLAUDE.md", "@docs/big.md\n")
    sizes: list[int] = []
    real = agents_md._read_bounded

    def spy(path: Path) -> str:
        text = real(path)
        sizes.append(len(text.encode("utf-8")))
        return text

    monkeypatch.setattr(agents_md, "_read_bounded", spy)
    agents_md._EXPANSION_CACHE.clear()
    load_agent_instructions(tmp_path, claude_compat=True)
    assert sizes and max(sizes) <= agents_md.READ_LIMIT_BYTES
    expanded = agents_md._source_text(tmp_path / "CLAUDE.md", tmp_path.resolve(), imports=True)
    assert "TAIL MARKER" not in expanded


def test_an_import_fan_out_of_200_by_200_stays_inside_every_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, "CLAUDE.md", "".join(f"@d/f{i}.md\n" for i in range(200)))
    for i in range(200):
        _write(tmp_path, f"d/f{i}.md", "".join(f"@g/x{i}_{j}.md\n" for j in range(200)) + f"BODY {i}\n")
        for j in range(200):
            _write(tmp_path, f"d/g/x{i}_{j}.md", f"DEEP {i} {j}\n" + "z" * 300)
    reads: list[Path] = []
    real = agents_md._read_bounded

    def spy(path: Path) -> str:
        reads.append(path)
        return real(path)

    monkeypatch.setattr(agents_md, "_read_bounded", spy)
    agents_md._EXPANSION_CACHE.clear()
    text = agents_md._source_text(tmp_path / "CLAUDE.md", tmp_path.resolve(), imports=True)
    assert len(reads) <= agents_md.MAX_IMPORTS + 1
    assert len(set(reads)) == len(reads), "a file is read once per expansion"
    assert len(text) <= agents_md.MAX_EXPANSION_CHARS + 4_000


def test_an_import_inlined_twice_appears_once(tmp_path: Path) -> None:
    _write(tmp_path, "shared.md", "SHARED ONCE")
    _write(tmp_path, "CLAUDE.md", "@shared.md\n@shared.md\n")
    agents_md._EXPANSION_CACHE.clear()
    text = agents_md._source_text(tmp_path / "CLAUDE.md", tmp_path.resolve(), imports=True)
    assert text.count("SHARED ONCE") == 1


def test_a_project_at_the_home_skips_the_dot_claude_file_that_is_the_owners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    _write(home, ".claude/CLAUDE.md", "OWNER ONLY")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))
    found = load_agent_instructions(home, claude_compat=True)
    assert ".claude/CLAUDE.md" not in found.sources and "OWNER ONLY" not in found.text
    owner = agents_md.load_user_claude_instructions()
    assert "OWNER ONLY" in owner.text


def test_the_expansion_is_cached_until_a_file_it_read_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, "docs/rules.md", "FIRST VERSION")
    _write(tmp_path, "CLAUDE.md", "@docs/rules.md\n")
    agents_md._EXPANSION_CACHE.clear()
    reads: list[Path] = []
    real = agents_md._read_bounded

    def spy(path: Path) -> str:
        reads.append(path)
        return real(path)

    monkeypatch.setattr(agents_md, "_read_bounded", spy)
    load_agent_instructions(tmp_path, claude_compat=True)
    first = len(reads)
    load_agent_instructions(tmp_path, claude_compat=True)
    assert len(reads) == first, "an unchanged source is not read again"
    _write(tmp_path, "docs/rules.md", "SECOND VERSION, LONGER")
    found = load_agent_instructions(tmp_path, claude_compat=True)
    assert "SECOND VERSION" in found.text
