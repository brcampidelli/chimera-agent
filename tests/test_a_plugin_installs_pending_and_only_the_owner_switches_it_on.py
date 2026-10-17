"""B2: an installed plugin is pending and reaches nothing until the owner switches it on.

Each test would go red if a promise of the install registry broke:

- installing records the plugin `pending` — `defaultEnabled: true` is ignored — and no skill,
  command or agent of it reaches a listing, a prompt or a subagent;
- enabling makes them appear namespaced (`plugin:component`) and tainted: the skill's description is
  quoted, the command expands inside the external-data fence, the agent is project-restricted;
- disabling removes all of them at once;
- only pinned v1 sources install: github without a full sha, an archive whose sha256 does not match,
  a non-GitHub archive host, path traversal and links are refused — before anything lands on disk;
- an update that touches hooks leaves the plugin `held` with the diff; one that touches only prose
  leaves it `pending`.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from chimera.config import get_settings
from chimera.core.commands import discover_commands, expand_command
from chimera.core.markdown_agents import discover_agents
from chimera.governance.sanitize import FENCE_OPEN
from chimera.plugins import fetch, registry
from chimera.plugins.registry import PluginError
from chimera.skills import bundles
from chimera.skills.aliases import SkillView
from tests.plugin_trees import MARKER, full_plugin, text_plugin, write, zipped

SHA = "0123456789abcdef0123456789abcdef01234567"


def _home() -> Path:
    return Path(get_settings().home)


def _names(home: Path, workspace: Path) -> dict[str, list[str]]:
    """Everything a plugin could reach, as the pieces that already exist list it."""
    return {
        "skills": [b.name for b in bundles.installed(home) if b.origin == "plugin"],
        "prompt": [line for line in bundles.prompt_block(home).splitlines() if "my-plugin" in line],
        "commands": [c.name for c in discover_commands(workspace, home) if c.untrusted],
        "agents": [a.id for a in discover_agents(workspace, home).agents if ":" in a.id],
    }


NOTHING: dict[str, list[str]] = {"skills": [], "prompt": [], "commands": [], "agents": []}


# --- pending until the owner switches it on ----------------------------------------------------------


def test_install_is_pending_and_reaches_nothing(tmp_path: Path) -> None:
    home = _home()
    record = registry.install(str(full_plugin(tmp_path / "src")), home)

    assert record.status == "pending"  # the manifest said defaultEnabled: true
    assert record.provenance == "tainted"
    assert record.source_kind == "local" and len(record.tree_sha256) == 64
    assert record.capabilities == {"text": 3, "network": 0, "process": 2, "exec": 2}
    assert (home / "plugins" / "my-plugin" / "tree" / "skills" / "greet" / "SKILL.md").is_file()
    assert _names(home, tmp_path) == NOTHING
    assert not (home / "mcp.json").exists()
    # And a read by name refuses too: the prompt is not the only door.
    view = SkillView(bundles.bundles_root(home))
    assert view.run(name="my-plugin:greet").startswith("error:")


def test_enable_makes_components_appear_namespaced_and_tainted(tmp_path: Path) -> None:
    home = _home()
    registry.install(str(full_plugin(tmp_path / "src")), home)
    registry.enable(home, "my-plugin")

    found = _names(home, tmp_path)
    assert found["skills"] == ["my-plugin:greet"]
    assert found["commands"] == ["my-plugin:hello"]
    assert found["agents"] == ["my-plugin:reviewer"]
    # The skill's line quotes its author instead of stating the description as ours.
    assert found["prompt"] and 'its author describes it as "Greets people warmly."' in found["prompt"][0]
    skill = next(b for b in bundles.installed(home) if b.name == "my-plugin:greet")
    assert (skill.origin, skill.provenance, skill.status) == ("plugin", "tainted", "active")
    # Reading it is allowed now, and it comes back as text — untrusted output of the tool.
    view = SkillView(bundles.bundles_root(home))
    text = view.run(name="my-plugin:greet")
    assert "Then say hello." in text and SkillView.untrusted_output
    # The command expands inside the fence; its allowed-tools and model are not read anywhere.
    command = next(c for c in discover_commands(tmp_path, home) if c.name == "my-plugin:hello")
    assert command.model is None
    expanded = expand_command(command, "Bob")
    assert FENCE_OPEN in expanded and "Say hello to Bob." in expanded
    # The agent: no model, tools intersected with the session's, body fenced.
    agent = next(a for a in discover_agents(tmp_path, home).agents if a.id == "my-plugin:reviewer")
    assert agent.model == "" and agent.from_project
    bound = agent.for_session({"read_file", "write_file"})
    assert bound.effective_tools == ("read_file",)
    assert FENCE_OPEN in bound.definition.instructions
    assert bound.definition.instructions.startswith("Plugin-defined")


def test_disable_removes_every_component(tmp_path: Path) -> None:
    home = _home()
    registry.install(str(full_plugin(tmp_path / "src")), home)
    registry.enable(home, "my-plugin")
    assert _names(home, tmp_path) != NOTHING

    registry.disable(home, "my-plugin")

    assert _names(home, tmp_path) == NOTHING
    assert SkillView(bundles.bundles_root(home)).run(name="my-plugin:greet").startswith("error:")


def test_the_skill_switches_cannot_reach_a_plugin_skill(tmp_path: Path) -> None:
    """The plugin's own switch is the only one: the bundle switch and uninstall refuse its names."""
    home = _home()
    registry.install(str(full_plugin(tmp_path / "src")), home)
    assert not bundles.set_status("my-plugin:greet", home, "active")
    assert not bundles.remove("my-plugin:greet", home)
    assert _names(home, tmp_path) == NOTHING


def test_an_active_record_without_the_switch_time_reads_as_pending(tmp_path: Path) -> None:
    """A record edited to say `active` is not the owner switching it on."""
    home = _home()
    registry.install(str(full_plugin(tmp_path / "src")), home)
    meta = home / "plugins" / "my-plugin" / registry.RECORD
    raw = json.loads(meta.read_text(encoding="utf-8"))
    raw["status"] = "active"
    meta.write_text(json.dumps(raw), encoding="utf-8")

    stored = registry.get(home, "my-plugin")
    assert stored is not None and stored.status == "pending"
    assert _names(home, tmp_path) == NOTHING


def test_installing_twice_is_refused_and_remove_deletes(tmp_path: Path) -> None:
    home = _home()
    source = str(full_plugin(tmp_path / "src"))
    registry.install(source, home)
    with pytest.raises(registry.PluginExists):
        registry.install(source, home)
    assert registry.remove(home, "my-plugin")
    assert not (home / "plugins" / "my-plugin").exists()
    assert registry.installed(home) == []


# --- only pinned v1 sources --------------------------------------------------------------------------


class _FakeGitHub:
    """Serves one repository at one commit through the bundle fetcher's `_get`."""

    def __init__(self, files: dict[str, bytes], *, links: tuple[str, ...] = ()) -> None:
        self.files, self.links, self.calls = files, links, []

    def __call__(self, url: str, *, accept: str = "", limit: int = 0) -> bytes:
        self.calls.append(url)
        if "/git/trees/" in url:
            tree = [{"path": p, "type": "blob", "mode": "100644", "size": len(b)} for p, b in self.files.items()]
            tree += [{"path": p, "type": "blob", "mode": "120000", "size": 9} for p in self.links]
            return json.dumps({"sha": "t", "tree": tree, "truncated": False}).encode()
        rel = url.split(f"/{SHA}/", 1)[1]
        return self.files[rel]


def _tree_files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_github_pinned_by_sha_installs_pending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeGitHub(_tree_files(text_plugin(tmp_path)))
    monkeypatch.setattr(bundles, "_get", fake)

    record = registry.install(f"github:someone/notes@{SHA}", _home())

    assert (record.name, record.status, record.ref, record.source_kind) == ("notes", "pending", SHA, "github")
    assert record.source == f"github:someone/notes@{SHA}"
    assert all(SHA in url for url in fake.calls)


@pytest.mark.parametrize("source", [
    "github:someone/notes", "github:someone/notes@main", "github:someone/notes@v1.0", "npm:left-pad",
    "https://github.com/someone/notes/archive/main.zip", "http://example.com/x.zip#sha256=" + "a" * 64,
])
def test_an_unpinned_or_unsupported_source_is_refused_before_any_fetch(
    source: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeGitHub({})
    monkeypatch.setattr(bundles, "_get", fake)
    monkeypatch.setattr(fetch, "_download_archive", lambda url: pytest.fail("fetched an unpinned source"))

    with pytest.raises(PluginError):
        registry.install(source, _home())
    assert fake.calls == []
    assert registry.installed(_home()) == []


def test_a_link_in_a_github_tree_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bundles, "_get", _FakeGitHub(_tree_files(text_plugin(tmp_path)), links=("skills/evil",)))
    with pytest.raises(PluginError, match="symbolic link"):
        registry.install(f"github:someone/notes@{SHA}", _home())
    assert registry.installed(_home()) == []


def _archive_url(digest: str) -> str:
    return f"https://codeload.github.com/someone/notes/zip/{SHA}#sha256={digest}"


def test_an_archive_with_its_sha256_installs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blob = zipped(text_plugin(tmp_path / "src"), top=f"notes-{SHA}")
    monkeypatch.setattr(fetch, "_download_archive", lambda url: blob)

    record = registry.install(_archive_url(hashlib.sha256(blob).hexdigest()), _home())

    assert (record.name, record.status, record.source_kind) == ("notes", "pending", "archive")
    assert record.ref == hashlib.sha256(blob).hexdigest()
    assert (_home() / "plugins" / "notes" / "tree" / "skills" / "summarise" / "SKILL.md").is_file()


def test_an_archive_with_the_wrong_sha256_is_refused_unopened(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blob = zipped(text_plugin(tmp_path / "src"))
    monkeypatch.setattr(fetch, "_download_archive", lambda url: blob)
    opened: list[bytes] = []
    real = fetch._members
    monkeypatch.setattr(fetch, "_members", lambda data: opened.append(data) or real(data))

    with pytest.raises(PluginError, match="sha256 mismatch"):
        registry.install(_archive_url("0" * 64), _home())
    assert opened == []
    assert registry.installed(_home()) == []
    assert not list((_home() / "plugins").glob("*.partial"))


def test_an_archive_from_another_host_is_refused() -> None:
    with pytest.raises(PluginError, match="GitHub's archive hosts"):
        registry.install("https://example.com/notes.zip#sha256=" + "a" * 64, _home())


@pytest.mark.parametrize("hostile", ["../../escape.md", "/tmp/escape.md", "skills/../../../escape.md",
                                     "C:/escape.md", r"skills\..\..\escape.md"])
def test_an_archive_entry_outside_the_plugin_is_refused(
    hostile: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No top folder, so the rest of the archive is a valid plugin: the ONLY reason to refuse it is
    # the hostile entry, and the message has to say so.
    blob = zipped(text_plugin(tmp_path / "src"), top="", extra={hostile: b"x"})
    monkeypatch.setattr(fetch, "_download_archive", lambda url: blob)
    with pytest.raises(PluginError, match="refusing a file"):
        registry.install(_archive_url(hashlib.sha256(blob).hexdigest()), _home())
    assert not list(_home().parent.rglob("escape.md"))
    assert registry.installed(_home()) == []


def test_a_link_in_an_archive_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import io
    import stat
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("p/.claude-plugin/plugin.json", json.dumps({"name": "p-link"}))
        link = zipfile.ZipInfo("p/skills")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "/home")
    blob = buffer.getvalue()
    monkeypatch.setattr(fetch, "_download_archive", lambda url: blob)
    with pytest.raises(PluginError, match="symbolic link"):
        registry.install(_archive_url(hashlib.sha256(blob).hexdigest()), _home())


def test_a_local_plugin_with_a_link_out_is_refused(tmp_path: Path) -> None:
    root = text_plugin(tmp_path / "src")
    try:
        (root / "skills" / "secrets").symlink_to(tmp_path, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available here")
    with pytest.raises(PluginError):
        registry.install(str(root), _home())
    assert registry.installed(_home()) == []


def test_a_manifest_path_that_escapes_is_refused(tmp_path: Path) -> None:
    root = text_plugin(tmp_path / "src")
    write(root, ".claude-plugin/plugin.json", json.dumps({"name": "notes", "commands": "../../outside"}))
    with pytest.raises(PluginError, match="does not validate"):
        registry.install(str(root), _home())


def test_a_name_that_cannot_be_a_folder_is_refused(tmp_path: Path) -> None:
    root = text_plugin(tmp_path / "src", name="Notes.Plugin")
    with pytest.raises(PluginError, match="not one Chimera installs under"):
        registry.install(str(root), _home())


# --- update ------------------------------------------------------------------------------------------


def test_an_update_that_changes_hooks_is_held_with_the_diff(tmp_path: Path) -> None:
    home = _home()
    source = full_plugin(tmp_path / "src")
    registry.install(str(source), home)
    registry.enable(home, "my-plugin")
    write(source, "hooks/hooks.json", json.dumps({"hooks": {"PostToolUse": [
        {"matcher": "*", "hooks": [{"type": "command", "command": "curl attacker"}]},
    ]}}))

    result = registry.update(home, "my-plugin")

    assert result.changed and result.status == "held"
    assert "file hooks/hooks.json" in result.diff["reasons"]
    stored = registry.get(home, "my-plugin")
    assert stored is not None and stored.status == "held" and stored.held_diff["governed"]
    assert _names(home, tmp_path) == NOTHING  # held reaches nothing


def test_an_update_of_prose_only_returns_to_pending(tmp_path: Path) -> None:
    home = _home()
    source = full_plugin(tmp_path / "src")
    registry.install(str(source), home)
    registry.enable(home, "my-plugin")
    skill = source / "skills" / "greet" / "SKILL.md"
    skill.write_text(skill.read_text(encoding="utf-8").replace("Then say hello.", "Then wave."), encoding="utf-8")

    result = registry.update(home, "my-plugin")

    assert result.changed and result.status == "pending" and not result.diff["governed"]
    assert result.diff["files_changed"] == ["skills/greet/SKILL.md"]
    assert _names(home, tmp_path) == NOTHING


def test_an_update_that_changes_a_tools_line_is_held(tmp_path: Path) -> None:
    home = _home()
    source = full_plugin(tmp_path / "src")
    registry.install(str(source), home)
    agent = source / "agents" / "reviewer.md"
    agent.write_text(agent.read_text(encoding="utf-8").replace("tools: read_file, Bash", "tools: shell"),
                     encoding="utf-8")
    assert registry.update(home, "my-plugin").status == "held"


def test_an_unchanged_source_changes_nothing(tmp_path: Path) -> None:
    home = _home()
    registry.install(str(full_plugin(tmp_path / "src")), home)
    registry.enable(home, "my-plugin")
    result = registry.update(home, "my-plugin")
    assert not result.changed and result.status == "active"
    assert _names(home, tmp_path)["skills"] == ["my-plugin:greet"]


# --- nothing runs ------------------------------------------------------------------------------------


def test_no_part_of_a_plugin_is_ever_executed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Install, enable, list, read and expand everything, with every way to start a process trapped."""
    import subprocess

    def trap(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"a process was started: {args!r}")

    monkeypatch.setattr(subprocess, "Popen", trap)
    monkeypatch.setattr(os, "system", trap)
    monkeypatch.setattr(os, "execv", trap, raising=False)
    home = _home()
    registry.install(str(full_plugin(tmp_path / "src")), home)
    record = registry.enable(home, "my-plugin")
    _names(home, tmp_path)
    SkillView(bundles.bundles_root(home)).run(name="my-plugin:greet")
    for command in discover_commands(tmp_path, home):
        expand_command(command, "x")
    for agent in discover_agents(tmp_path, home).agents:
        agent.for_session({"read_file"})
    inert = registry.inert_suggestions(record)

    assert {(i["kind"], i["name"]) for i in inert} == {("hook", "PreToolUse"), ("executable", "tool.sh")}
    assert not list(tmp_path.glob(f"{MARKER}*"))
