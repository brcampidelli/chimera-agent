"""M2: a pinned ``git-subdir`` on github.com installs that folder and nothing else; a pinned ``url`` at a
github.com repository root installs as ``github``; every other git source stays refused.

Field shapes follow https://code.claude.com/docs/en/plugins/marketplace-reference (read 2026-10-17):
``url`` takes "a full git URL"; ``git-subdir`` takes ``url`` ("a full git URL or GitHub owner/repo
shorthand"), ``path`` ("the subdirectory that holds the plugin"), ``ref`` and ``sha`` ("a full
40-character lowercase commit SHA").

Each test goes red if one promise breaks:

- a pinned github.com ``url`` root is classified ``github`` with its repo and sha; a deeper path, another
  host, ``http://``, credentials, a port, a query and the shorthand (not valid for ``url``) are not;
- a pinned github.com ``git-subdir`` keeps its kind and its normalised folder; another host is
  ``unsupported_source`` and an unpinned one is ``unpinned_source``;
- a ``path`` that is not a plain folder inside the repository is refused even when everything else is
  right;
- installing from a marketplace asks GitHub only for the tree at ``<sha>:<path>`` and only for files
  under ``<path>``, and what lands on disk is the folder's files and nothing from beside it;
- a refused source never reaches the network, and a stored git-subdir record that lost its folder is
  refused instead of widening into the whole repository.

Nothing is downloaded: the GitHub fetcher is replaced by a fake that serves one monorepo.
"""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

import pytest

from chimera.config import get_settings
from chimera.plugins import Code, SourceKind, classify_source, marketplaces, registry
from chimera.plugins.fetch import refusal_for
from chimera.plugins.model import Problem
from chimera.plugins.registry import PluginError
from chimera.skills import bundles
from tests.plugin_trees import text_plugin, write

SHA = "0123456789abcdef0123456789abcdef01234567"
FOLDER = "tools/notes"


def _home() -> Path:
    return Path(get_settings().home)


# --- classification ----------------------------------------------------------------------------------


@pytest.mark.parametrize("url", [
    "https://github.com/acme/notes",
    "https://github.com/acme/notes.git",
    "https://github.com/acme/notes/",
    "https://GitHub.com/acme/notes",
    "git@github.com:acme/notes.git",
])
def test_a_pinned_url_at_a_github_root_is_a_github_source(url: str) -> None:
    source = classify_source({"source": "url", "url": url, "ref": "main", "sha": SHA})

    assert source.installable
    assert (source.kind, source.repo, source.sha, source.ref, source.url) == (SourceKind.GITHUB, "acme/notes", SHA, "main", url)
    assert source.path is None


@pytest.mark.parametrize("url", [
    "https://github.com/acme/notes/tree/main/tools",  # a folder through a branch, not a repository root
    "https://github.com/acme",
    "https://gitlab.com/acme/notes.git",
    "https://github.com.evil.example/acme/notes",
    "https://evil.example/github.com/acme/notes",
    "http://github.com/acme/notes",
    "https://user:token@github.com/acme/notes",
    "https://github.com:8443/acme/notes",
    "https://github.com/acme/notes?x=1",
    "https://github.com/acme/notes#main",
    "https://github.com/acme/..",
    "acme/notes",  # the reference: `url` "doesn't take owner/repo shorthand"
    "git@gitlab.com:acme/notes.git",
    "file:///srv/notes",
])
def test_a_pinned_url_anywhere_else_is_unsupported(url: str) -> None:
    source = classify_source({"source": "url", "url": url, "sha": SHA})

    assert not source.installable
    assert (source.kind, source.refusal) == (SourceKind.GIT, Code.UNSUPPORTED_SOURCE)
    assert source.repo is None
    assert refusal_for(source) == "url sources are installable in v1 only from github.com"


@pytest.mark.parametrize("sha", [None, "main", SHA[:7], SHA.upper()])
def test_a_github_url_without_a_full_sha_is_unpinned(sha: str | None) -> None:
    spec: dict[str, object] = {"source": "url", "url": "https://github.com/acme/notes.git"}
    if sha is not None:
        spec["sha"] = sha

    assert classify_source(spec).refusal is Code.UNPINNED_SOURCE


@pytest.mark.parametrize(("url", "path", "folder"), [
    ("https://github.com/acme/mono.git", "tools/notes", "tools/notes"),
    ("https://github.com/acme/mono", "./tools/notes/", "tools/notes"),
    ("acme/mono", "tools//notes", "tools/notes"),
    ("git@github.com:acme/mono.git", "tools/./notes", "tools/notes"),
])
def test_a_pinned_git_subdir_on_github_keeps_its_folder(url: str, path: str, folder: str) -> None:
    source = classify_source({"source": "git-subdir", "url": url, "path": path, "ref": "v2", "sha": SHA})

    assert source.installable
    assert (source.kind, source.repo, source.path, source.sha) == (SourceKind.GIT_SUBDIR, "acme/mono", folder, SHA)


def test_a_pinned_git_subdir_elsewhere_is_unsupported_and_an_unpinned_one_is_unpinned() -> None:
    elsewhere = classify_source({"source": "git-subdir", "url": "https://gitlab.com/acme/mono.git", "path": "x", "sha": SHA})
    floating = classify_source({"source": "git-subdir", "url": "https://github.com/acme/mono.git", "path": "x", "ref": "main"})

    assert (elsewhere.kind, elsewhere.refusal) == (SourceKind.GIT_SUBDIR, Code.UNSUPPORTED_SOURCE)
    assert refusal_for(elsewhere) == "git-subdir sources are installable in v1 only from github.com"
    assert (floating.kind, floating.refusal) == (SourceKind.GIT_SUBDIR, Code.UNPINNED_SOURCE)


@pytest.mark.parametrize("path", [
    "../outside", "tools/../../outside", "tools/..", "/etc", "tools\\notes", "C:/tools", "tools/no\x00tes",
    "", ".", "./", "/", ".github/hidden",
])
def test_a_git_subdir_path_that_is_not_a_plain_folder_is_refused(path: str) -> None:
    problems: list[Problem] = []
    source = classify_source(
        {"source": "git-subdir", "url": "https://github.com/acme/mono.git", "path": path, "sha": SHA},
        problems=problems,
    )

    assert not source.installable
    assert source.refusal is Code.PATH_ESCAPES_PLUGIN
    assert source.repo is None and source.path is None
    assert [p.code for p in problems] == [Code.PATH_ESCAPES_PLUGIN]


def test_a_git_subdir_by_anthropics_reads_as_anthropic_and_a_refused_one_does_not() -> None:
    ours = classify_source({"source": "git-subdir", "url": "https://github.com/anthropics/mono", "path": "p", "sha": SHA})
    lookalike = classify_source({"source": "git-subdir", "url": "https://gitlab.com/anthropics/mono", "path": "p", "sha": SHA})

    assert ours.from_anthropic
    assert not lookalike.from_anthropic


# --- installing from a marketplace -------------------------------------------------------------------


class _Monorepo:
    """One repository at one commit, served through the bundle fetcher's ``_get``.

    The tree endpoint honours ``<sha>:<path>`` the way GitHub does — paths come back relative to the
    folder — so a fetch that forgot the folder would list, and then download, the whole repository.
    """

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files, self.calls = files, []

    def __call__(self, url: str, *, accept: str = "", limit: int = 0) -> bytes:
        self.calls.append(url)
        if "/git/trees/" in url:
            treeish = urllib.parse.unquote(url.split("/git/trees/", 1)[1].split("?", 1)[0])
            sha, _, folder = treeish.partition(":")
            assert sha == SHA
            prefix = f"{folder.strip('/')}/" if folder else ""
            tree = [
                {"path": p[len(prefix):], "type": "blob", "mode": "100644", "size": len(b)}
                for p, b in self.files.items() if p.startswith(prefix)
            ]
            return json.dumps({"sha": "t", "tree": tree, "truncated": False}).encode()
        rel = urllib.parse.unquote(url.split(f"/{SHA}/", 1)[1])
        return self.files[rel]


def _monorepo(tmp_path: Path) -> dict[str, bytes]:
    plugin = text_plugin(tmp_path / "src")
    files = {f"{FOLDER}/{p.relative_to(plugin).as_posix()}": p.read_bytes() for p in plugin.rglob("*") if p.is_file()}
    files["README.md"] = b"the monorepo"
    files["tools/other/.claude-plugin/plugin.json"] = json.dumps({"name": "other"}).encode()
    files["secrets/deploy.env"] = b"TOKEN=not-for-the-plugin"
    return files


def _market(tmp_path: Path, source: object) -> Path:
    root = tmp_path / "market"
    write(root, ".claude-plugin/marketplace.json", json.dumps({
        "name": "mono-tools", "owner": {"name": "Acme"}, "description": "From a monorepo.",
        "plugins": [{"name": "notes", "source": source}],
    }))
    return root


def test_a_marketplace_git_subdir_installs_only_its_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Monorepo(_monorepo(tmp_path))
    monkeypatch.setattr(bundles, "_get", fake)
    home = _home()
    marketplaces.add(home, str(_market(tmp_path, {
        "source": "git-subdir", "url": "https://github.com/acme/mono.git", "path": FOLDER, "ref": "main", "sha": SHA,
    })))

    spec = marketplaces.resolve(home, "notes", "mono-tools")
    record = registry.install(spec, home, marketplace="mono-tools")

    assert (spec.kind, spec.repo, spec.sha, spec.path) == (SourceKind.GITHUB, "acme/mono", SHA, FOLDER)
    assert (record.name, record.status, record.ref) == ("notes", "pending", SHA)
    tree_calls = [url for url in fake.calls if "/git/trees/" in url]
    file_calls = [url for url in fake.calls if "/git/trees/" not in url]
    assert tree_calls == [f"https://api.github.com/repos/acme/mono/git/trees/{urllib.parse.quote(f'{SHA}:{FOLDER}')}?recursive=1"]
    assert file_calls and all(url.startswith(f"https://raw.githubusercontent.com/acme/mono/{SHA}/{FOLDER}/") for url in file_calls)
    on_disk = sorted(p.relative_to(registry.tree_of(home, "notes")).as_posix()
                     for p in registry.tree_of(home, "notes").rglob("*") if p.is_file())
    assert on_disk == [".claude-plugin/plugin.json", "commands/brief.md", "skills/summarise/SKILL.md"]


def test_a_marketplace_url_at_a_github_root_installs_as_github(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = text_plugin(tmp_path / "src")
    fake = _Monorepo({p.relative_to(plugin).as_posix(): p.read_bytes() for p in plugin.rglob("*") if p.is_file()})
    monkeypatch.setattr(bundles, "_get", fake)
    home = _home()
    marketplaces.add(home, str(_market(tmp_path, {"source": "url", "url": "https://github.com/acme/notes.git", "sha": SHA})))

    record = registry.install(marketplaces.resolve(home, "notes", "mono-tools"), home, marketplace="mono-tools")

    assert (record.name, record.ref, record.source) == ("notes", SHA, f"github:acme/notes@{SHA}")
    assert all("acme/notes" in url and SHA in url for url in fake.calls)


@pytest.mark.parametrize("source", [
    {"source": "git-subdir", "url": "https://gitlab.com/acme/mono.git", "path": FOLDER, "sha": SHA},
    {"source": "git-subdir", "url": "https://github.com/acme/mono.git", "path": FOLDER, "ref": "main"},
    {"source": "git-subdir", "url": "https://github.com/acme/mono.git", "path": "../secrets", "sha": SHA},
    {"source": "url", "url": "https://github.com/acme/mono/tree/main/tools/notes", "sha": SHA},
])
def test_a_refused_git_source_never_reaches_the_network(
    source: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _Monorepo(_monorepo(tmp_path))
    monkeypatch.setattr(bundles, "_get", fake)
    home = _home()
    marketplaces.add(home, str(_market(tmp_path, source)))

    with pytest.raises(PluginError, match="not installable"):
        marketplaces.resolve(home, "notes", "mono-tools")
    assert fake.calls == []
    assert registry.installed(home) == []


def test_a_stored_git_subdir_without_its_folder_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Monorepo(_monorepo(tmp_path))
    monkeypatch.setattr(bundles, "_get", fake)
    home = _home()
    marketplaces.add(home, str(_market(tmp_path, {
        "source": "git-subdir", "url": "https://github.com/acme/mono.git", "path": FOLDER, "sha": SHA,
    })))
    store = registry.plugins_root(home) / marketplaces.STORE
    data = json.loads(store.read_text(encoding="utf-8"))
    data[0]["plugins"][0]["source"]["path"] = ""
    store.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(PluginError, match="no folder"):
        marketplaces.resolve(home, "notes", "mono-tools")
    assert fake.calls == []
