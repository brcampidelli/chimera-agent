"""A marketplace reads entry by entry, and only a local, sha-pinned github or sha256-pinned archive source is installable.

The marketplace shape reproduces `anthropics/claude-plugins-official`'s `.claude-plugin/marketplace.json`
(name, owner, `$schema`, local `./plugins/<name>` entries with `category`, and external entries) and the
source examples in https://code.claude.com/docs/en/plugins/marketplace-reference. Nothing is downloaded.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.plugins import (
    Code,
    MarketplaceEntry,
    MarketplaceReport,
    Severity,
    SourceKind,
    classify_source,
    parse_marketplace,
    read_marketplace,
    read_plugin,
)

SHA = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"
SHA256 = "6BFA50E3D2E00C052B46ABE51FFF89346AC803E45771F76DCF6DF1AB74CCA5E1"


def _write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _marketplace(root: Path, data: object) -> None:
    _write(root, ".claude-plugin/marketplace.json", json.dumps(data))


def _official_shaped(root: Path, plugins: list[object], name: str = "claude-plugins-official") -> None:
    _marketplace(root, {
        "$schema": "https://anthropic.com/claude-code/marketplace.schema.json",
        "name": name,
        "description": "Directory of popular Claude Code extensions including development tools, productivity plugins, and MCP integrations",
        "owner": {"name": "Anthropic", "email": "support@anthropic.com"},
        "plugins": plugins,
    })


def _by_name(report: MarketplaceReport) -> dict[str, MarketplaceEntry]:
    return {entry.name: entry for entry in report.plugins}


def test_each_source_kind_is_classified_and_only_pinned_ones_are_installable(tmp_path: Path) -> None:
    root = tmp_path / "market"
    _write(root, "plugins/commit-commands/.claude-plugin/plugin.json", json.dumps({"name": "commit-commands"}))
    _write(root, "plugins/commit-commands/commands/commit.md", "Commit.")
    _official_shaped(root, [
        {"name": "commit-commands", "description": "git workflow", "category": "productivity",
         "author": {"name": "Anthropic", "email": "support@anthropic.com"}, "source": "./plugins/commit-commands"},
        {"name": "formatter", "source": {"source": "github", "repo": "your-org/formatter", "ref": "v2.0.0", "sha": SHA}},
        {"name": "floating", "source": {"source": "github", "repo": "your-org/floating"}},
        {"name": "zipped", "source": {"source": "archive", "url": "https://artifacts.example.com/formatter-2.0.0.zip", "sha256": SHA256}},
        {"name": "zipped-unpinned", "source": {"source": "archive", "url": "https://artifacts.example.com/x.zip"}},
        {"name": "from-npm", "source": {"source": "npm", "package": "@your-org/formatter", "version": "^2.0.0"}},
        {"name": "from-command", "source": {"source": "command", "command": "my-tool claude-plugin-path"}},
        {"name": "gitlab", "source": {"source": "url", "url": "https://gitlab.example.com/g/formatter.git", "ref": "main"}},
        {"name": "subdir-pinned", "source": {"source": "git-subdir", "url": "https://github.com/o/mono.git", "path": "tools/x", "sha": SHA}},
    ])

    report = read_marketplace(root, anthropic_origin=True)

    assert report.ok, report.problems
    entries = _by_name(report)
    expected = {
        "commit-commands": (SourceKind.LOCAL, None),
        "formatter": (SourceKind.GITHUB, None),
        "floating": (SourceKind.GITHUB, Code.UNPINNED_SOURCE),
        "zipped": (SourceKind.ARCHIVE, None),
        "zipped-unpinned": (SourceKind.ARCHIVE, Code.UNPINNED_SOURCE),
        "from-npm": (SourceKind.NPM, Code.UNSUPPORTED_SOURCE),
        "from-command": (SourceKind.COMMAND, Code.UNSUPPORTED_SOURCE),
        "gitlab": (SourceKind.GIT, Code.UNPINNED_SOURCE),
        "subdir-pinned": (SourceKind.GIT_SUBDIR, Code.UNSUPPORTED_SOURCE),
    }
    for name, (kind, refusal) in expected.items():
        entry = entries[name]
        assert (entry.source.kind, entry.source.refusal) == (kind, refusal), name
        assert entry.installable is (refusal is None), name
    local = report.plugins[0]
    assert local.source.path == "plugins/commit-commands" and local.category == "productivity"
    assert read_plugin(root / local.source.path).ok
    assert report.plugins[3].source.sha256 == SHA256.lower()
    # A refused source is a warning on its entry, never an error that would sink the marketplace.
    assert all(p.severity is Severity.WARNING for p in report.plugins[2].problems)


def test_an_official_name_is_reserved_unless_the_files_come_from_anthropics(tmp_path: Path) -> None:
    root = tmp_path / "market"
    _official_shaped(root, [{"name": "a", "source": {"source": "github", "repo": "x/a", "sha": SHA}}])

    report = read_marketplace(root)

    assert not report.ok
    assert any(p.code is Code.RESERVED_NAME for p in report.problems)
    assert read_marketplace(root, anthropic_origin=True).ok


@pytest.mark.parametrize("name", ["official-claude-plugins", "claude-plugins-v2", "github", "claude.code.plugins", "marché"])
def test_lookalike_marketplace_names_are_refused(name: str) -> None:
    report = parse_marketplace({"name": name, "owner": {"name": "x"}, "plugins": []})

    assert not report.ok


def test_one_bad_entry_does_not_sink_the_rest(tmp_path: Path) -> None:
    root = tmp_path / "market"
    _write(root, "plugins/good/skills/s/SKILL.md", "---\nname: s\ndescription: d\n---\n")
    _official_shaped(root, [
        {"name": "escape", "source": "./../outside"},
        {"name": "windows", "source": ".\\plugins\\good"},
        {"name": "backslash-inside", "source": "./plugins\\good"},
        {"name": "absolute", "source": "/etc"},
        {"name": "missing", "source": "./plugins/nope"},
        {"name": "no source"},
        {"name": "weird", "source": {"source": "svn", "url": "x"}},
        {"name": "good", "source": "./plugins/good"},
        {"name": "good", "source": "./plugins/good"},
    ], name="acme-tools")

    report = read_marketplace(root)

    assert report.ok, report.problems  # entry errors live on entries
    codes = [[p.code for p in entry.problems if p.severity is Severity.ERROR] for entry in report.plugins]
    assert Code.PATH_ESCAPES_PLUGIN in codes[0]
    assert Code.BACKSLASH_IN_PATH in codes[1]
    assert Code.BACKSLASH_IN_PATH in codes[2]
    assert Code.PATH_NOT_RELATIVE in codes[3]
    assert Code.PATH_NOT_FOUND in codes[4]
    assert Code.BAD_NAME in codes[5] and Code.BAD_SOURCE in codes[5]
    assert Code.BAD_SOURCE in codes[6]
    assert report.plugins[7].installable
    assert Code.DUPLICATE_PLUGIN in codes[8] and not report.plugins[8].installable
    assert [entry.installable for entry in report.plugins].count(True) == 1


def test_bare_names_resolve_under_plugin_root(tmp_path: Path) -> None:
    root = tmp_path / "market"
    _write(root, "plugins/formatter/commands/f.md", "f")
    _marketplace(root, {"name": "acme", "owner": {"name": "Acme"}, "description": "d",
                        "metadata": {"pluginRoot": "./plugins"},
                        "plugins": [{"name": "formatter", "source": "formatter"},
                                    {"name": "nested", "source": "team-a/formatter"}]})

    report = read_marketplace(root)

    assert report.plugins[0].installable and report.plugins[0].source.path == "plugins/formatter"
    assert Code.PATH_NOT_RELATIVE in [p.code for p in report.plugins[1].problems]


def test_a_local_source_through_a_symlink_out_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "market"
    outside = tmp_path / "outside"
    _write(outside, "commands/x.md", "x")
    (root / "plugins").mkdir(parents=True)
    try:
        (root / "plugins" / "evil").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available here")
    _marketplace(root, {"name": "acme", "owner": {"name": "Acme"}, "plugins": [{"name": "evil", "source": "./plugins/evil"}]})

    report = read_marketplace(root)

    assert not report.plugins[0].installable
    assert Code.SYMLINK_OUT in [p.code for p in report.plugins[0].problems]


@pytest.mark.parametrize(("source", "code"), [
    ({"source": "archive", "url": "http://example.com/x.zip", "sha256": SHA256}, Code.BAD_URL),
    ({"source": "archive", "url": "https://127.0.0.1/x.zip", "sha256": SHA256}, Code.BAD_URL),
    ({"source": "archive", "url": "https://169.254.169.254/latest", "sha256": SHA256}, Code.BAD_URL),
    ({"source": "archive", "url": "https://example.com/x.zip", "sha256": "abc"}, Code.BAD_SHA),
    ({"source": "github", "repo": "o/r", "sha": SHA.upper()}, Code.BAD_SHA),
    ({"source": "github", "repo": "o/r", "sha": SHA[:7]}, Code.BAD_SHA),
    ({"source": "github", "repo": "../etc"}, Code.BAD_SOURCE),
    ({"source": "github"}, Code.BAD_SOURCE),
    (42, Code.BAD_SOURCE),
])
def test_malformed_sources_are_refused(source: object, code: Code) -> None:
    result = classify_source(source)

    assert not result.installable
    assert result.refusal is code


def test_a_card_says_by_anthropic_only_for_an_anthropics_repository() -> None:
    assert classify_source({"source": "github", "repo": "anthropics/claude-plugins-official", "sha": SHA}).from_anthropic
    assert not classify_source({"source": "github", "repo": "anthropics-fan/plugins", "sha": SHA}).from_anthropic
    assert not classify_source({"source": "archive", "url": "https://github.com/anthropics/x.zip", "sha256": SHA256}).from_anthropic


def test_the_marketplace_file_itself_is_validated(tmp_path: Path) -> None:
    assert Code.MISSING_MARKETPLACE in [p.code for p in read_marketplace(tmp_path).problems]

    report = parse_marketplace({"name": "acme", "extra": 1})
    codes = {p.code: p.severity for p in report.problems}
    assert codes[Code.MISSING_OWNER] is Severity.ERROR
    assert codes[Code.MISSING_PLUGINS] is Severity.ERROR
    assert codes[Code.UNKNOWN_FIELD] is Severity.WARNING
    assert codes[Code.MISSING_DESCRIPTION] is Severity.WARNING

    root = tmp_path / "broken"
    _write(root, ".claude-plugin/marketplace.json", "{oops")
    assert Code.BAD_JSON in [p.code for p in read_marketplace(root).problems]
