"""The security review of the Claude Code agents (memory scope, links, Manual, bounds, cache, warnings).

Companion of ``test_claude_code_agents_are_read_and_only_narrow.py``, whose helpers it reuses.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.core import claude_agent_format as cf
from chimera.core.markdown_agents import discover_agents
from chimera.core.subagent import SubAgentTool
from chimera.tools.builtin import default_registry
from tests.test_claude_code_agents_are_read_and_only_narrow import (
    _agent,
    _Backend,
    _found,
    _memory_agent,
)


def _link(target: Path, link: Path) -> None:
    """A symlink, or a junction on Windows when symlinks need privileges; skip if neither works."""
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError:
        pass
    try:
        import _winapi  # type: ignore[import-not-found,unused-ignore]

        _winapi.CreateJunction(str(target), str(link))
    except (ImportError, OSError, AttributeError):
        pytest.skip("neither symlinks nor junctions can be created on this machine")


def test_a_repository_file_cannot_use_the_users_memory_but_the_owners_can(tmp_path: Path) -> None:
    project, claude = tmp_path / "p", tmp_path / "c"
    _agent(project / ".claude" / "agents", "r.md", "name: repo\ndescription: d\nmemory: user")
    _agent(claude / "agents", "o.md", "name: owned\ndescription: d\nmemory: user")

    catalog = discover_agents(project, tmp_path / "h", owner_claude=claude)
    agents = {a.id: a for a in catalog.agents}

    assert agents["repo"].memory_scope == "" and agents["repo"].memory_path == ""
    assert any(
        "memória do usuário só para agentes do dono" in i.message
        for i in catalog.issues
        if i.agent_id == "repo"
    )
    assert agents["owned"].memory_scope == "user"
    assert agents["owned"].memory_path == str(claude / "agent-memory" / "owned")


@pytest.mark.parametrize("which", ["parent", "leaf"])
def test_a_memory_directory_that_is_a_link_is_refused(tmp_path: Path, which: str) -> None:
    project = tmp_path / "p"
    outside = tmp_path / "outside"
    outside.mkdir()
    base = project / ".claude"
    base.mkdir(parents=True)
    if which == "parent":
        _link(outside, base / "agent-memory")
    else:
        (base / "agent-memory").mkdir()
        _link(outside, base / "agent-memory" / "keeper")
    _agent(project / ".claude" / "agents", "k.md", "name: keeper\ndescription: d\nmemory: project")

    catalog = discover_agents(project, tmp_path / "h", owner_claude=tmp_path / "c")
    (agent,) = catalog.agents

    assert agent.memory_scope == "" and agent.memory_path == ""
    assert any("memory ignored" in i.message and "link" in i.message for i in catalog.issues)


def test_the_user_memory_directory_must_stay_inside_the_owners_claude_folder(tmp_path: Path) -> None:
    claude, outside = tmp_path / "c", tmp_path / "outside"
    outside.mkdir()
    claude.mkdir()
    _link(outside, claude / "agent-memory")
    _agent(claude / "agents", "o.md", "name: owned\ndescription: d\nmemory: user")

    (agent,) = discover_agents(tmp_path / "p", tmp_path / "h", owner_claude=claude).agents

    assert agent.memory_scope == ""


def test_a_link_that_appears_after_discovery_is_caught_when_the_agent_runs(tmp_path: Path) -> None:
    project, agent, memory = _memory_agent(tmp_path, "tools: Grep")
    outside = tmp_path / "outside"
    outside.mkdir()
    for child in memory.iterdir():
        child.unlink()
    memory.rmdir()
    _link(outside, memory)
    backend = _Backend()

    SubAgentTool(
        backend, default_registry(project), markdown_agents={"keeper": agent}, workspace=project
    ).run(task="t", agent="keeper")

    names = {entry["function"]["name"] for entry in backend.kwargs["tools"]}
    assert names == {"grep"}
    assert "Persistent memory unavailable" in backend.messages[0]["content"]


def test_in_manual_mode_a_memory_write_asks_for_a_card_and_the_answer_decides(tmp_path: Path) -> None:
    from tests.test_manual_mode_one_table_and_answers import _gate

    memory = tmp_path / ".claude" / "agent-memory" / "keeper"
    memory.mkdir(parents=True)
    asked: list[str] = []
    gate, stub = _gate(tmp_path, "write_file", asked, answer=False)
    scoped = cf.ScopedPathTool(gate, memory, tmp_path)  # type: ignore[arg-type]

    scoped.run(path=str(memory / "MEMORY.md"), content="note")
    assert len(asked) == 1 and stub.ran == []  # `memory` grants the directory; Manual still asks

    gate2, stub2 = _gate(tmp_path, "write_file", [], answer=True)
    cf.ScopedPathTool(gate2, memory, tmp_path).run(path=str(memory / "MEMORY.md"), content="note")  # type: ignore[arg-type]
    assert len(stub2.ran) == 1


def test_the_close_loop_never_stages_an_agents_memory() -> None:
    from chimera.core.close_loop import _is_agent_memory

    assert _is_agent_memory(".claude/agent-memory/x/MEMORY.md")
    assert _is_agent_memory(".Claude/Agent-Memory/x/MEMORY.md")
    assert _is_agent_memory("pkg/.CLAUDE/AGENT-MEMORY-LOCAL/x.md")
    assert _is_agent_memory("pkg\\.claude\\agent-memory-local\\x\\n.md")
    assert not _is_agent_memory(".claude/agents/x.md")
    assert not _is_agent_memory("src/agent-memory/x.py")


def test_discovery_of_a_claude_folder_is_bounded_in_files_and_depth(tmp_path: Path) -> None:
    folder = tmp_path / "p" / ".claude" / "agents"
    for i in range(205):
        _agent(folder, f"a{i:03}.md", f"name: a{i:03}\ndescription: d")
    _agent(folder / "x" / "y" / "z" / "too", "deep.md", "name: deep\ndescription: d")

    catalog = discover_agents(tmp_path / "p", tmp_path / "h", owner_claude=tmp_path / "c")

    assert len(catalog.agents) == 200 and "deep" not in {a.id for a in catalog.agents}
    assert any("more than 200 agent files" in i.message for i in catalog.issues)

    other = tmp_path / "q" / ".claude" / "agents"
    _agent(other / "x" / "y" / "z", "ok3.md", "name: depth3\ndescription: d")
    _agent(other / "x" / "y" / "z" / "w", "no4.md", "name: depth4\ndescription: d")
    found = {a.id for a in discover_agents(tmp_path / "q", tmp_path / "h", owner_claude=tmp_path / "c").agents}
    assert found == {"depth3"}


def test_an_unchanged_file_is_not_parsed_again_and_a_changed_one_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import time

    from chimera.core import markdown_agents as module

    path = _agent(tmp_path / "p" / ".claude" / "agents", "a.md", "name: a\ndescription: first")
    old = time.time_ns() - 60_000_000_000
    os.utime(path, ns=(old, old))  # a file written moments ago is deliberately never cached
    calls: list[str] = []
    real = module._read_agent

    def counted(*args: object, **kwargs: object) -> object:
        calls.append("x")
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(module, "_read_agent", counted)

    def listing() -> str:
        return discover_agents(tmp_path / "p", tmp_path / "h", owner_claude=tmp_path / "c").agents[0].description

    assert listing() == "first" and listing() == "first"
    assert len(calls) == 1
    path.write_text("---\nname: a\ndescription: second, longer\n---\nbody\n", encoding="utf-8")
    os.utime(path, ns=(old + 5, old + 5))
    assert listing() == "second, longer" and len(calls) == 2


def test_a_file_written_moments_ago_or_during_the_read_is_never_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import time

    from chimera.core import markdown_agents as module

    path = _agent(tmp_path / "p" / ".claude" / "agents", "a.md", "name: a\ndescription: first")
    calls: list[str] = []
    real = module._read_agent

    def counted(*args: object, **kwargs: object) -> object:
        calls.append("x")
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(module, "_read_agent", counted)

    def listing() -> Any:
        return discover_agents(tmp_path / "p", tmp_path / "h", owner_claude=tmp_path / "c")

    listing()
    listing()
    assert len(calls) == 2  # fresh mtime: read both times

    old = time.time_ns() - 60_000_000_000
    os.utime(path, ns=(old, old))

    def changes_while_reading(*args: object, **kwargs: object) -> object:
        result = real(*args, **kwargs)  # type: ignore[arg-type]
        path.write_text(
            "---\nname: a\ndescription: changed meanwhile!!\n---\nbody\n", encoding="utf-8"
        )
        os.utime(path, ns=(old, old))  # same mtime, new size: only the size gives it away
        return result

    monkeypatch.setattr(module, "_read_agent", changes_while_reading)
    assert listing().agents[0].description == "first"  # the parse of the old bytes was not stored
    monkeypatch.setattr(module, "_read_agent", real)
    assert listing().agents[0].description == "changed meanwhile!!"


def test_a_top_role_picked_by_a_repository_is_flagged_but_not_by_the_owner(tmp_path: Path) -> None:
    project, claude = tmp_path / "p", tmp_path / "c"
    _agent(project / ".claude" / "agents", "r.md", "name: repo\ndescription: d\nmodel: opus")
    _agent(project / ".claude" / "agents", "m.md", "name: mid\ndescription: d\nmodel: sonnet")
    _agent(claude / "agents", "o.md", "name: owned\ndescription: d\nmodel: opus")

    catalog = discover_agents(project, tmp_path / "h", owner_claude=claude)
    flagged = {i.agent_id for i in catalog.issues if "selects the top tier" in i.message}

    assert flagged == {"repo"}


def test_a_repository_agent_without_tools_inherits_the_session_but_never_beyond_it(tmp_path: Path) -> None:
    _agent(tmp_path / "p" / ".claude" / "agents", "a.md", "name: a\ndescription: d")
    (agent,) = _found(tmp_path / "p", tmp_path / "h", tmp_path / "c").values()

    assert agent.inherit_tools
    assert agent.for_session({"read_file"}).effective_tools == ("read_file",)
    assert agent.for_session(set()).effective_tools == ()
