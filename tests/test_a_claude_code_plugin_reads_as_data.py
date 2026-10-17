"""A Claude Code plugin directory reads as typed, inert data — and a hostile one cannot reach outside itself.

The fixture trees reproduce the shapes Claude Code documents (https://code.claude.com/docs/en/plugins-reference,
the `deploy-tools` example and the "Standard layout" table) and the layout of plugins published in
`anthropics/claude-plugins-official` (a command plugin with `commands/*.md`, a skill plugin with
`skills/<name>/SKILL.md`, a hook plugin with `hooks/hooks.json` calling a script). Nothing is downloaded.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.plugins import Capability, Code, ComponentKind, PluginReport, Severity, read_plugin
from chimera.plugins import scan as scan_module


def _write(root: Path, rel: str, content: str | bytes = "x") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


def _manifest(root: Path, data: object) -> None:
    _write(root, ".claude-plugin/plugin.json", json.dumps(data))


def _skill(root: Path, rel: str, name: str) -> None:
    _write(root, rel, f"---\nname: {name}\ndescription: Does {name}.\n---\nBody.\n")


def _link(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available here")


def _errors(report: PluginReport) -> list[Code]:
    return [p.code for p in report.problems if p.severity is Severity.ERROR]


# --- plugins Claude Code accepts read without errors ---------------------------------------------


def test_a_skills_only_plugin_without_manifest_is_named_by_its_folder(tmp_path: Path) -> None:
    root = tmp_path / "frontend-design"
    _skill(root, "skills/frontend-design/SKILL.md", "frontend-design")

    report = read_plugin(root)

    assert report.ok, report.problems
    assert report.manifest is not None
    assert (report.manifest.name, report.manifest.name_from) == ("frontend-design", "directory")
    assert [(c.kind, c.name, c.path) for c in report.components] == [
        (ComponentKind.SKILL, "frontend-design", "skills/frontend-design/SKILL.md"),
    ]
    assert report.capabilities.instructions_only
    assert report.components[0].qualified("frontend-design") == "frontend-design:frontend-design"


def test_a_marketplace_entry_name_is_used_when_there_is_no_manifest(tmp_path: Path) -> None:
    root = tmp_path / "some-checkout-dir"
    _skill(root, "skills/a/SKILL.md", "a")

    report = read_plugin(root, fallback_name="internal-comms")

    assert report.manifest is not None and report.manifest.name == "internal-comms"


def test_an_official_shaped_command_plugin_reads_clean(tmp_path: Path) -> None:
    root = tmp_path / "commit-commands"
    _manifest(root, {
        "name": "commit-commands",
        "description": "Streamline your git workflow with simple commands for committing, pushing, and creating pull requests",
        "version": "1.0.0",
        "author": {"name": "Anthropic", "email": "support@anthropic.com"},
    })
    for command in ("commit", "commit-push-pr", "clean_gone"):
        _write(root, f"commands/{command}.md", "---\ndescription: x\nallowed-tools: Bash(git add:*)\n---\nDo it.\n")

    report = read_plugin(root)

    assert report.ok and not report.problems, report.problems
    assert report.manifest is not None and report.manifest.author is not None
    assert report.manifest.author.name == "Anthropic"
    assert sorted(c.name for c in report.components) == ["clean_gone", "commit", "commit-push-pr"]
    assert {c.capability for c in report.components} == {Capability.TEXT}


def test_a_root_skill_md_alone_is_a_single_skill_named_after_the_plugin(tmp_path: Path) -> None:
    root = tmp_path / "theme-factory"
    _skill(root, "SKILL.md", "theme-factory")

    report = read_plugin(root)

    assert report.ok
    assert [(c.kind, c.name) for c in report.components] == [(ComponentKind.SKILL, "theme-factory")]


def _full_plugin(root: Path) -> None:
    """The documented `deploy-tools` example, with every component and every path it names on disk."""
    _manifest(root, {
        "name": "deploy-tools",
        "displayName": "Deploy Tools",
        "version": "1.2.0",
        "description": "Deployment commands, a review agent, and a status monitor",
        "author": {"name": "Example Team", "email": "dev@example.com", "url": "https://example.com"},
        "homepage": "https://example.com/docs/deploy-tools",
        "repository": "https://github.com/example/deploy-tools",
        "license": "MIT",
        "keywords": ["deployment", "ci"],
        "defaultEnabled": True,
        "dependencies": ["secrets-vault", {"name": "audit", "marketplace": "acme"}],
        "metadata": {"catalogId": "cat-123", "chimera": {"surfaces": ["desktop"], "note": {"k": [1, 2]}}},
        "skills": ["./extra-skills/"],
        "commands": {
            "status": {"source": "./commands/status.md", "description": "Show the current deployment status"},
            "about": {"content": "Explain what the deploy-tools plugin provides.", "description": "Describe this plugin"},
        },
        "agents": ["./agents/reviewer.md"],
        "hooks": "./config/extra-hooks.json",
        "mcpServers": {
            "deploy-api": {"command": "node", "args": ["${CLAUDE_PLUGIN_ROOT}/server.js"]},
            "status-page": {"type": "http", "url": "https://status.example.com/mcp", "headers": {"X-Key": "${user_config.api_token}"}},
        },
        "lspServers": "./.lsp.json",
        "outputStyles": "./styles/",
        "experimental": {"themes": "./themes/", "monitors": "./config/monitors.json"},
        "userConfig": {
            "api_token": {"type": "string", "title": "API token", "description": "Token for the deployment API", "sensitive": True},
        },
    })
    _skill(root, "skills/deploy/SKILL.md", "deploy")
    _skill(root, "extra-skills/rollback/SKILL.md", "rollback")
    _write(root, "commands/status.md", "Status.\n")
    _write(root, "agents/reviewer.md", "---\nname: reviewer\n---\nReview.\n")
    _write(root, "hooks/hooks.json", json.dumps({"hooks": {"PostToolUse": [
        {"matcher": "Write|Edit", "hooks": [{"type": "command", "command": "\"${CLAUDE_PLUGIN_ROOT}\"/scripts/format.sh"}]},
    ]}}))
    _write(root, "config/extra-hooks.json", json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "\"${CLAUDE_PLUGIN_ROOT}\"/scripts/check-command.sh"}]},
    ]}}))
    _write(root, ".mcp.json", json.dumps({"mcpServers": {"db": {"command": "npx", "args": ["-y", "db-mcp"]}}}))
    _write(root, ".lsp.json", json.dumps({"go": {"command": "gopls", "args": ["serve"], "extensionToLanguage": {".go": "go"}}}))
    _write(root, "styles/terse.md", "Be terse.\n")
    _write(root, "themes/dracula.json", "{}")
    _write(root, "config/monitors.json", json.dumps([
        {"name": "deploy-status", "command": "\"${CLAUDE_PLUGIN_ROOT}\"/scripts/poll-deploy.sh",
         "description": "Deployment status changes", "when": "on-skill-invoke:deploy"},
    ]))
    _write(root, "workflows/release-audit.js", "export default {}\n")
    _write(root, "bin/deploy-tool", "#!/bin/sh\necho hi\n")
    _write(root, "scripts/format.sh", "#!/bin/sh\n")
    _write(root, "server.js", "\n")
    _write(root, "settings.json", json.dumps({"agent": "reviewer"}))


def test_the_documented_full_plugin_inventories_every_component_with_its_capability(tmp_path: Path) -> None:
    root = tmp_path / "deploy-tools"
    _full_plugin(root)

    report = read_plugin(root)

    assert report.ok, [p for p in report.problems if p.severity is Severity.ERROR]
    assert not report.problems, report.problems
    found = {(c.kind, c.name): c for c in report.components}
    assert set(found) == {
        (ComponentKind.SKILL, "deploy"),
        (ComponentKind.SKILL, "rollback"),
        (ComponentKind.COMMAND, "status"),
        (ComponentKind.COMMAND, "about"),
        (ComponentKind.AGENT, "reviewer"),
        (ComponentKind.OUTPUT_STYLE, "terse"),
        (ComponentKind.THEME, "dracula"),
        (ComponentKind.WORKFLOW, "release-audit"),
        (ComponentKind.MCP_SERVER, "db"),
        (ComponentKind.MCP_SERVER, "deploy-api"),
        (ComponentKind.MCP_SERVER, "status-page"),
        (ComponentKind.LSP_SERVER, "go"),
        (ComponentKind.HOOK, "PostToolUse"),
        (ComponentKind.HOOK, "PreToolUse"),
        (ComponentKind.MONITOR, "deploy-status"),
        (ComponentKind.EXECUTABLE, "deploy-tool"),
        (ComponentKind.SETTINGS, "settings"),
    }
    summary = report.capabilities
    # Skills, commands, agent, output style, theme and the settings file only instruct; the stdio
    # MCP servers and the LSP server would start a process; the remote MCP server only connects;
    # hooks, the monitor, the workflow and bin/ would run commands on their own.
    assert (summary.text, summary.process, summary.network, summary.exec) == (8, 3, 1, 5)
    assert found[(ComponentKind.MCP_SERVER, "deploy-api")].detail == ("stdio", "node")
    assert found[(ComponentKind.COMMAND, "about")].path == ".claude-plugin/plugin.json#commands.about"
    manifest = report.manifest
    assert manifest is not None
    assert manifest.dependencies == ("secrets-vault", "audit@acme")
    assert [(o.key, o.sensitive) for o in manifest.user_config] == [("api_token", True)]
    assert manifest.chimera["surfaces"] == ("desktop",)


def test_chimera_metadata_is_read_only_all_the_way_down(tmp_path: Path) -> None:
    root = tmp_path / "deploy-tools"
    _full_plugin(root)

    manifest = read_plugin(root).manifest

    assert manifest is not None
    with pytest.raises(TypeError):
        manifest.chimera["surfaces"] = ["bots"]  # type: ignore[index]  # the write must fail at runtime
    nested = manifest.chimera["note"]
    with pytest.raises(TypeError):
        nested["k"] = []  # type: ignore[index]  # the write must fail at runtime
    with pytest.raises(AttributeError):
        manifest.name = "other"  # type: ignore[misc]  # frozen: the write must fail at runtime


def test_reading_runs_nothing_the_plugin_declares(tmp_path: Path) -> None:
    root = tmp_path / "hooky"
    marker = tmp_path / "PWNED"
    command = f"python -c \"open(r'{marker}', 'w')\""
    _manifest(root, {"name": "hooky", "version": "1", "description": "d", "author": {"name": "a"}})
    _write(root, "hooks/hooks.json", json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": command}]}]}}))
    _write(root, ".mcp.json", json.dumps({"s": {"command": "python", "args": ["-c", f"open(r'{marker}', 'w')"]}}))
    _write(root, "monitors/monitors.json", json.dumps([{"name": "m", "command": command, "description": "d"}]))

    report = read_plugin(root)

    assert report.ok, report.problems
    assert not marker.exists()
    assert report.capabilities.exec == 2 and report.capabilities.process == 1


# --- validation ---------------------------------------------------------------------------------


def test_a_manifest_without_name_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "p"
    _manifest(root, {"description": "no name"})

    report = read_plugin(root)

    assert not report.ok and report.manifest is None
    assert Code.MISSING_NAME in _errors(report)


@pytest.mark.parametrize("name", ["my plugin", "deploy:tools", "a/b", "../up", "-dash", "x\u202egnp"])
def test_a_name_claude_code_refuses_is_an_error(tmp_path: Path, name: str) -> None:
    root = tmp_path / "p"
    _manifest(root, {"name": name})

    assert Code.BAD_NAME in _errors(read_plugin(root))


def test_reserved_and_lookalike_names_follow_claude_code(tmp_path: Path) -> None:
    for name, code, severity in [
        ("claude-helper", Code.RESERVED_NAME, Severity.ERROR),
        ("Official.Anthropic_Tools", Code.RESERVED_NAME, Severity.ERROR),
        ("mcp-for-claude", Code.NAME_READS_AS_ANTHROPIC, Severity.WARNING),
        ("My_Plugin", Code.NOT_KEBAB_CASE, Severity.WARNING),
    ]:
        root = tmp_path / name
        _manifest(root, {"name": name})
        report = read_plugin(root)
        assert any(p.code is code and p.severity is severity for p in report.problems), (name, report.problems)


def test_an_unknown_top_level_field_warns_but_an_unknown_strict_key_fails(tmp_path: Path) -> None:
    root = tmp_path / "p"
    _manifest(root, {
        "name": "p", "version": "1", "description": "d", "author": {"name": "a"}, "category": "x",
        "lspServers": {"go": {"command": "gopls", "extensionToLanguage": {".go": "go"}, "colour": "red"}},
    })

    report = read_plugin(root)

    assert any(p.code is Code.UNKNOWN_FIELD and p.severity is Severity.WARNING for p in report.problems)
    assert Code.UNKNOWN_FIELD_STRICT in _errors(report)


def test_user_config_references_are_checked(tmp_path: Path) -> None:
    root = tmp_path / "p"
    _manifest(root, {
        "name": "p",
        "userConfig": {"tok": {"type": "string", "title": "t", "description": "d", "secret": True}},
        "mcpServers": {"s": {"command": "srv", "env": {"T": "${user_config.missing}"}}},
        "experimental": {"monitors": [{"name": "m", "command": "poll ${user_config.tok}", "description": "d"}]},
    })

    errors = _errors(read_plugin(root))

    assert Code.UNKNOWN_FIELD_STRICT in errors  # "secret" inside a strict userConfig option
    assert Code.UNDECLARED_USER_CONFIG in errors
    assert Code.USER_CONFIG_IN_SHELL in errors


def test_a_hooks_file_without_its_wrapper_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "p"
    _write(root, "hooks/hooks.json", json.dumps({"PreToolUse": []}))

    report = read_plugin(root)

    assert Code.BAD_HOOKS in _errors(report)
    assert not [c for c in report.components if c.kind is ComponentKind.HOOK]


def test_a_replacing_key_reports_the_ignored_default_folder(tmp_path: Path) -> None:
    root = tmp_path / "p"
    _manifest(root, {"name": "p", "version": "1", "description": "d", "author": {"name": "a"}, "commands": "./extra/"})
    _write(root, "commands/old.md", "old")
    _write(root, "extra/new.md", "new")

    report = read_plugin(root)

    assert report.ok
    assert [c.name for c in report.components] == ["new"]
    assert Code.DEFAULT_FOLDER_IGNORED in report.codes()


@pytest.mark.parametrize("shape", ["truncated", "nested"])
def test_broken_json_is_a_problem_not_an_exception(tmp_path: Path, shape: str) -> None:
    root = tmp_path / "p"
    # 200k nested brackets is under the size cap and deep enough to exhaust the parser's recursion.
    text = "{not json" if shape == "truncated" else "[" * 200_000 + "]" * 200_000
    _write(root, ".claude-plugin/plugin.json", text)

    report = read_plugin(root)

    assert Code.BAD_JSON in _errors(report)


def test_a_folder_with_nothing_of_a_plugin_is_not_one(tmp_path: Path) -> None:
    root = tmp_path / "random"
    _write(root, "notes.txt", "hello")

    assert Code.NOT_A_PLUGIN in _errors(read_plugin(root))


# --- containment and bounds ---------------------------------------------------------------------


@pytest.mark.parametrize(("field", "value", "code"), [
    ("commands", "./../outside", Code.PATH_ESCAPES_PLUGIN),
    ("commands", "./commands/../../outside", Code.PATH_ESCAPES_PLUGIN),
    ("agents", ["../outside/agent.md"], Code.PATH_NOT_RELATIVE),
    ("agents", ["/etc/passwd"], Code.PATH_NOT_RELATIVE),
    ("outputStyles", "commands", Code.PATH_NOT_RELATIVE),
    ("skills", ".\\skills\\extra", Code.BACKSLASH_IN_PATH),
    ("skills", "./skills\\..\\..\\outside", Code.BACKSLASH_IN_PATH),
    ("hooks", "./C:/Windows/hooks.json", Code.PATH_ESCAPES_PLUGIN),
    ("mcpServers", "./missing.json", Code.PATH_NOT_FOUND),
])
def test_a_manifest_path_that_escapes_or_is_malformed_is_refused(tmp_path: Path, field: str, value: object, code: Code) -> None:
    root = tmp_path / "p"
    outside = tmp_path / "outside"
    _write(outside, "agent.md", "secret")
    _write(outside, "hooks.json", json.dumps({"hooks": {}}))
    _manifest(root, {"name": "p", field: value})
    _write(root, "commands/a.md", "a")

    report = read_plugin(root)

    assert code in _errors(report), report.problems
    assert all("outside" not in c.path for c in report.components)


def test_a_symlink_out_of_the_plugin_is_never_followed(tmp_path: Path) -> None:
    root = tmp_path / "p"
    secret = tmp_path / "home" / ".ssh"
    _skill(secret, "SKILL.md", "stolen")
    _skill(root, "skills/real/SKILL.md", "real")
    _link(root / "skills" / "evil", secret)
    _link(root / "linked-commands", secret)
    _manifest(root, {"name": "p", "commands": "./linked-commands"})

    report = read_plugin(root)

    assert not report.ok
    errors = _errors(report)
    assert errors.count(Code.SYMLINK_OUT) >= 2
    assert [c.name for c in report.components] == ["real"]


def test_a_symlink_inside_the_plugin_is_skipped_with_a_warning(tmp_path: Path) -> None:
    root = tmp_path / "p"
    _skill(root, "skills/real/SKILL.md", "real")
    _link(root / "skills" / "alias", root / "skills" / "real")

    report = read_plugin(root)

    assert report.ok
    assert Code.SYMLINK_IGNORED in report.codes()
    assert [c.name for c in report.components] == ["real"]


def test_a_manifest_behind_a_link_is_not_read(tmp_path: Path) -> None:
    root = tmp_path / "p"
    elsewhere = tmp_path / "elsewhere"
    _write(elsewhere, "plugin.json", json.dumps({"name": "hijack"}))
    _skill(root, "skills/a/SKILL.md", "a")
    _link(root / ".claude-plugin", elsewhere)

    report = read_plugin(root)

    assert report.manifest is None or report.manifest.name != "hijack"
    assert Code.SYMLINK_OUT in _errors(report)


def test_an_oversized_file_is_refused_and_not_listed(tmp_path: Path) -> None:
    root = tmp_path / "p"
    _skill(root, "skills/ok/SKILL.md", "ok")
    _write(root, "skills/huge/SKILL.md", b"a" * (scan_module.MAX_FILE_BYTES + 1))
    _write(root, ".mcp.json", b" " * (scan_module.MAX_FILE_BYTES + 1))

    report = read_plugin(root)

    assert _errors(report).count(Code.FILE_TOO_LARGE) == 2
    assert [c.name for c in report.components] == ["ok"]


def test_the_scan_is_bounded_in_entries_depth_and_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "p"
    _skill(root, "skills/a/SKILL.md", "a")
    for number in range(12):
        _write(root, f"scripts/f{number}.sh", "12345")
    _write(root, "/".join(["deep"] * 6) + "/x.md", "x")

    monkeypatch.setattr(scan_module, "MAX_ENTRIES", 10)
    assert Code.TOO_MANY_FILES in _errors(read_plugin(root))

    monkeypatch.setattr(scan_module, "MAX_ENTRIES", 5_000)
    monkeypatch.setattr(scan_module, "MAX_DEPTH", 4)
    assert Code.TOO_DEEP in _errors(read_plugin(root))

    monkeypatch.setattr(scan_module, "MAX_DEPTH", 12)
    monkeypatch.setattr(scan_module, "MAX_TOTAL_BYTES", 40)
    assert Code.PLUGIN_TOO_LARGE in _errors(read_plugin(root))


def test_a_git_directory_does_not_spend_the_entry_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "p"
    _skill(root, "skills/a/SKILL.md", "a")
    for number in range(50):
        _write(root, f".git/objects/{number}", "o")
    monkeypatch.setattr(scan_module, "MAX_ENTRIES", 10)

    assert read_plugin(root).ok
