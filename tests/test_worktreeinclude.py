"""`.worktreeinclude` copies only the ignored files it names into a new conversation worktree.

`chimera/core/worktree_include.py`, against a real git repository: the pattern language is checked on
its own, then the copy is checked end to end — what is copied, what is never copied (a tracked file, a
symbolic link, a file outside the project, a file over a cap), and that a broken rules file costs
only its own lines.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from chimera.core import worktree_include as wi


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=True
    )
    return done.stdout.strip()


@pytest.fixture(autouse=True)
def _identity(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    }.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A repository whose ``.gitignore`` hides ``.env*`` and ``secret/``, with one tracked ``.env``."""
    root = tmp_path / "project"
    root.mkdir()
    _git(tmp_path, "init", "-b", "main", str(root))
    (root / ".gitignore").write_text(".env*\nsecret/\nbuild/\n", encoding="utf-8")
    (root / "README.md").write_text("hi\n", encoding="utf-8")
    _git(root, "add", ".gitignore", "README.md")
    _git(root, "commit", "-m", "base")
    return root


def _yes(_digest: str, _preview: dict[str, object]) -> bool:
    return True


def _link_dir(link: Path, target: Path) -> None:
    """A directory link: a symbolic link, or on Windows without the privilege a junction, which needs
    none and is what `git` and `os.path` still have to refuse to follow."""
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        if sys.platform != "win32":
            pytest.skip("this platform cannot create symbolic links here")
    made = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True
    )
    if made.returncode != 0:
        pytest.skip("could not create a junction here")


def _worktree(root: Path, name: str = "wt") -> Path:
    path = root.parent / name
    _git(root, "worktree", "add", "-b", f"chimera/{name}", str(path), "HEAD")
    return path


def _rules(root: Path, text: str, name: str = ".worktreeinclude") -> None:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


# -- the pattern language ---------------------------------------------------------------------


def _selected(text: str, path: str) -> bool:
    rules, _ = wi.parse_rules(text)
    return wi.is_included(rules, path)


def test_a_plain_name_matches_that_file_at_any_depth() -> None:
    assert _selected(".env", ".env")
    assert _selected(".env", "apps/web/.env")
    assert not _selected(".env", ".env.local")


def test_a_slash_anchors_the_pattern_to_the_root() -> None:
    assert _selected("/config.json", "config.json")
    assert not _selected("/config.json", "nested/config.json")
    assert _selected("config/app.json", "config/app.json")
    assert not _selected("config/app.json", "x/config/app.json")


def test_a_directory_pattern_selects_everything_under_it() -> None:
    assert _selected("keys/", "keys/prod/server.pem")
    assert not _selected("keys/", "keys")  # a directory is not a file to copy


def test_a_negation_takes_one_file_back_out() -> None:
    text = "config/\n!config/public.json\n"
    assert _selected(text, "config/private.json")
    assert not _selected(text, "config/public.json")


def test_double_star_crosses_folders() -> None:
    assert _selected("**/*.pem", "a/b/c/cert.pem")
    assert _selected("certs/**", "certs/x/y.pem")
    assert not _selected("certs/**", "other/y.pem")


def test_comments_and_blank_lines_are_skipped() -> None:
    rules, invalid = wi.parse_rules("# the secrets\n\n   \n.env\n")
    assert [r.text for r in rules] == [".env"]
    assert invalid == []


@pytest.mark.parametrize(
    ("line", "reason_part"),
    [
        ("config/[abc", "unclosed"),
        ("trailing\\", "trailing backslash"),
        ("../outside.env", "climbs"),
        ("a/../../b", "climbs"),
        ("/", "empty"),
    ],
)
def test_a_malformed_line_is_reported_and_ignored(line: str, reason_part: str) -> None:
    rules, invalid = wi.parse_rules(f".env\n{line}\n", source=".worktreeinclude")
    assert [r.text for r in rules] == [".env"]
    assert len(invalid) == 1
    assert invalid[0].line == 2
    assert reason_part in invalid[0].reason


# -- the copy ---------------------------------------------------------------------------------


def test_copies_only_ignored_files_that_match(project: Path) -> None:
    (project / ".env").write_text("TOKEN=1\n", encoding="utf-8")
    (project / ".env.local").write_text("LOCAL=1\n", encoding="utf-8")
    (project / "secret").mkdir()
    (project / "secret" / "key.pem").write_text("k\n", encoding="utf-8")
    _rules(project, ".env\nsecret/\n")
    wt = _worktree(project)

    report = wi.copy_included(project, wt, consent=_yes)

    assert report.copied == [".env", "secret/key.pem"]
    assert (wt / ".env").read_text(encoding="utf-8") == "TOKEN=1\n"
    assert (wt / "secret" / "key.pem").exists()
    assert not (wt / ".env.local").exists()  # ignored, but not named
    assert report.skipped == []


def test_a_tracked_file_is_never_copied_even_when_it_matches(project: Path) -> None:
    (project / "tracked.env").write_text("from-the-repo\n", encoding="utf-8")
    _git(project, "add", "-f", "tracked.env")
    _git(project, "commit", "-m", "track it")
    (project / "tracked.env").write_text("the owner's edit\n", encoding="utf-8")
    _rules(project, "tracked.env\n")
    wt = _worktree(project)

    report = wi.copy_included(project, wt, consent=_yes)

    assert report.copied == []
    assert (wt / "tracked.env").read_text(encoding="utf-8") == "from-the-repo\n"


def test_the_chimera_location_is_read_too(project: Path) -> None:
    (project / ".env").write_text("A=1\n", encoding="utf-8")
    _rules(project, ".env\n", name=".chimera/worktreeinclude")
    wt = _worktree(project)

    report = wi.copy_included(project, wt, consent=_yes)

    assert report.copied == [".env"]


def test_no_rules_file_means_no_copy_and_no_git_call(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("git must not run without a rules file")

    monkeypatch.setattr(wi, "ignored_untracked", boom)
    wt = _worktree(project)

    assert wi.copy_included(project, wt, consent=_yes).empty


def test_a_malformed_pattern_costs_only_its_own_line(project: Path) -> None:
    (project / ".env").write_text("A=1\n", encoding="utf-8")
    _rules(project, "config/[abc\n.env\n")
    wt = _worktree(project)

    report = wi.copy_included(project, wt, consent=_yes)

    assert report.copied == [".env"]
    assert [i.line for i in report.invalid] == [1]


def test_a_symbolic_link_is_not_followed(project: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("not yours\n", encoding="utf-8")
    link = project / ".env"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create symbolic links here")
    _rules(project, ".env\n")
    wt = _worktree(project)

    report = wi.copy_included(project, wt, consent=_yes)

    assert report.copied == []
    assert report.skipped == [(".env", "symbolic link: not followed")]
    assert not (wt / ".env").exists()


def test_a_file_under_a_linked_folder_is_not_followed(project: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / ".env").write_text("X=1\n", encoding="utf-8")
    _link_dir(project / "linked", elsewhere)
    _rules(project, ".env\n")
    wt = _worktree(project)

    report = wi.copy_included(project, wt, consent=_yes)

    assert "linked/.env" not in report.copied
    assert not (wt / "linked").exists()


def test_the_file_count_cap_skips_the_rest_and_says_so(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wi, "MAX_FILES", 1)
    for name in (".env", ".env.a", ".env.b"):
        (project / name).write_text("x\n", encoding="utf-8")
    _rules(project, ".env*\n")
    wt = _worktree(project)

    report = wi.copy_included(project, wt, consent=_yes)

    assert len(report.copied) == 1
    assert len(report.skipped) == 2
    assert all("more than 1 files" in reason for _path, reason in report.skipped)


def test_the_byte_cap_skips_a_file_that_would_pass_it(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wi, "MAX_BYTES", 10)
    (project / ".env").write_text("x" * 20, encoding="utf-8")
    (project / ".env.small").write_text("ok", encoding="utf-8")
    _rules(project, ".env*\n")
    wt = _worktree(project)

    report = wi.copy_included(project, wt, consent=_yes)

    assert report.copied == [".env.small"]
    assert report.skipped == [(".env", "over the 50 MB total")]
    assert report.bytes_copied == 2


def test_an_oversized_rules_file_is_not_read(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wi, "MAX_RULES_BYTES", 8)
    (project / ".env").write_text("A=1\n", encoding="utf-8")
    _rules(project, ".env\n" + "# padding padding\n")
    wt = _worktree(project)

    report = wi.copy_included(project, wt, consent=_yes)

    assert report.copied == []
    assert report.invalid and "not read" in report.invalid[0].reason


def test_the_payload_is_the_shape_the_turn_frame_carries(project: Path) -> None:
    (project / ".env").write_text("A=1\n", encoding="utf-8")
    _rules(project, ".env\n")
    wt = _worktree(project)

    payload = wi.copy_included(project, wt, consent=_yes).as_payload()

    assert payload["copied"] == [".env"]
    assert payload["skipped"] == []
    assert payload["bytes"] == (wt / ".env").stat().st_size


def test_a_junction_or_linked_folder_is_refused_as_a_parent(project: Path, tmp_path: Path) -> None:
    """The refusal itself, without going through git's listing: a directory link anywhere on the way
    to a file is never followed (on Windows that is a junction, which `is_symlink` does not report)."""
    elsewhere = tmp_path / "elsewhere2"
    elsewhere.mkdir()
    (elsewhere / ".env").write_text("X=1\n", encoding="utf-8")
    _link_dir(project / "jn", elsewhere)

    assert wi._has_symlink_parent(project, "jn/.env") is True
    assert wi._refusal(project, project.resolve(), "jn/.env") == "symbolic link: not followed"


# -- the copy is ignored IN the worktree ------------------------------------------------------


def test_a_copy_git_does_not_ignore_in_the_worktree_is_removed_again(project: Path) -> None:
    """`.gitignore` edited in the project and never committed hides ``.env.prod`` there only. The
    worktree starts from the base, where nothing ignores it: a `git add -A` would stage the secret."""
    (project / ".gitignore").write_text(".env*\nsecret/\nbuild/\n.env.prod\n.local-only/\n", encoding="utf-8")
    (project / ".local-only").mkdir()
    (project / ".local-only" / "db.sqlite").write_text("data\n", encoding="utf-8")
    (project / ".env").write_text("A=1\n", encoding="utf-8")
    _rules(project, ".env\n.local-only/\n")
    wt = _worktree(project)

    report = wi.copy_included(project, wt, consent=_yes)

    assert report.copied == [".env"]
    assert report.skipped == [
        (".local-only/db.sqlite", "not ignored in the worktree: the copy was removed")
    ]
    assert not (wt / ".local-only").exists()
    _git(wt, "add", "-A")
    assert _git(wt, "diff", "--cached", "--name-only") == ""


def test_the_copied_files_are_recorded_with_their_hash(project: Path) -> None:
    (project / ".env").write_text("A=1\n", encoding="utf-8")
    _rules(project, ".env\n")
    wt = _worktree(project)

    wi.copy_included(project, wt, consent=_yes)

    assert list(wi.read_manifest(wt)) == [".env"]
    assert wi.read_manifest(wt)[".env"] == wi._sha256(wt / ".env")


# -- the first time, and when the file changes, the owner is asked -----------------------------


def test_nothing_is_copied_until_the_owner_has_agreed(project: Path) -> None:
    (project / ".env").write_text("TOKEN=1\n", encoding="utf-8")
    _rules(project, ".env\n")
    wt = _worktree(project)
    asked: list[tuple[str, dict[str, object]]] = []

    def refuse(digest: str, preview: dict[str, object]) -> bool:
        asked.append((digest, preview))
        return False

    report = wi.copy_included(project, wt, consent=refuse)

    assert report.copied == [] and not (wt / ".env").exists()
    assert report.consent is not None and report.consent["files"] == [".env"]
    assert asked[0][0] == wi.plan_included(project).digest
    assert report.as_payload()["consent_required"]["files"] == [".env"]


def test_the_digest_changes_when_the_rules_file_does(project: Path) -> None:
    _rules(project, ".env\n")
    first = wi.rules_digest(project)
    _rules(project, ".env\n.env.local\n")
    assert wi.rules_digest(project) != first


def test_the_consent_store_agrees_only_to_a_version_a_turn_asked_about(tmp_path: Path) -> None:
    from chimera.core.worktree_include_consent import IncludeConsent

    store = IncludeConsent(tmp_path / "c.json")
    project = tmp_path / "p"
    project.mkdir()
    assert store.grant(project, "abc") is False  # never asked: cannot be pre-approved
    store.request(project, "abc", {"files": [".env"], "more": 0, "bytes": 3})
    assert [p["digest"] for p in store.pending()] == ["abc"]
    assert store.grant(project, "abc") is True
    assert store.granted(project, "abc") and not store.granted(project, "other")
    assert store.pending() == []


def test_a_new_worktree_asks_first_then_copies_once_agreed(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.core import project_place as pp
    from chimera.core.worktree_include_consent import IncludeConsent, consent_path

    (project / ".env").write_text("TOKEN=1\n", encoding="utf-8")
    _rules(project, ".env\n")
    home = tmp_path / "home"
    wt = _worktree(project, "first")

    first = pp._included_payload(project, wt, home=home)

    assert first is not None and "consent_required" in first and not (wt / ".env").exists()
    pending = IncludeConsent(consent_path(home)).pending()
    assert len(pending) == 1
    assert IncludeConsent(consent_path(home)).grant(project, pending[0]["digest"])

    second_wt = _worktree(project, "second")
    second = pp._included_payload(project, second_wt, home=home)

    assert second is not None and second["copied"] == [".env"]
    assert (second_wt / ".env").read_text(encoding="utf-8") == "TOKEN=1\n"
    # The file changes: asked again, nothing copied.
    _rules(project, ".env\n.env.local\n")
    third_wt = _worktree(project, "third")
    third = pp._included_payload(project, third_wt, home=home)
    assert third is not None and "consent_required" in third and not (third_wt / ".env").exists()


# -- the payload and the listing are bounded --------------------------------------------------


def test_the_payload_carries_a_count_and_the_first_names_only(project: Path) -> None:
    for i in range(wi.PREVIEW_FILES + 5):
        (project / f".env.{i:02d}").write_text("x\n", encoding="utf-8")
    _rules(project, ".env.*\n")
    wt = _worktree(project)
    # make every copy refused so they all land in `skipped`
    for i in range(wi.PREVIEW_FILES + 5):
        (wt / f".env.{i:02d}").write_text("there\n", encoding="utf-8")

    payload = wi.copy_included(project, wt, consent=_yes).as_payload()

    assert len(payload["skipped"]) == wi.PREVIEW_FILES
    assert payload["skipped_total"] == wi.PREVIEW_FILES + 5


def test_a_heavy_ignored_folder_is_not_walked(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (project / ".gitignore").write_text(".env*\nnode_modules/\n", encoding="utf-8")
    (project / "node_modules" / "pkg").mkdir(parents=True)
    (project / "node_modules" / "pkg" / "index.js").write_text("x\n", encoding="utf-8")
    (project / ".env").write_text("A=1\n", encoding="utf-8")
    rules, _ = wi.parse_rules(".env\n")
    seen: list[list[str]] = []
    real = wi._list_ignored

    def spy(root: Path, extra: object = (), *, timeout: float = 0.0) -> list[str] | None:
        seen.append(list(extra))  # type: ignore[call-overload]
        return real(root, extra, timeout=timeout)  # type: ignore[arg-type]

    monkeypatch.setattr(wi, "_list_ignored", spy)

    listed, truncated = wi.ignored_untracked(project, rules)

    assert listed == [".env"] and truncated is False
    assert seen == [["--directory"]]  # no second call into node_modules


def test_a_listing_that_times_out_copies_nothing_and_says_so(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (project / ".env").write_text("A=1\n", encoding="utf-8")
    _rules(project, ".env\n")
    monkeypatch.setattr(wi, "_list_ignored", lambda *_a, **_k: None)
    wt = _worktree(project)

    report = wi.copy_included(project, wt, consent=_yes)

    assert report.copied == [] and report.skipped and report.skipped[0][0] == "(listing)"


# -- the consent names the files too, the listing has one budget, links are rechecked ----------


def test_a_new_ignored_file_that_matches_asks_again(
    project: Path, tmp_path: Path
) -> None:
    from chimera.core import project_place as pp
    from chimera.core.worktree_include_consent import IncludeConsent, consent_path

    (project / ".env").write_text("A=1\n", encoding="utf-8")
    _rules(project, ".env*\n")
    home = tmp_path / "home"
    first_wt = _worktree(project, "one")
    assert pp._included_payload(project, first_wt, home=home) is not None
    store = IncludeConsent(consent_path(home))
    assert store.grant(project, store.pending()[0]["digest"])

    unchanged = pp._included_payload(project, _worktree(project, "two"), home=home)
    assert unchanged is not None and unchanged["copied"] == [".env"]

    (project / ".env.production").write_text("PROD=1\n", encoding="utf-8")  # same rules file
    third_wt = _worktree(project, "three")
    asked = pp._included_payload(project, third_wt, home=home)

    assert asked is not None and "consent_required" in asked
    assert asked["consent_required"]["files"] == [".env", ".env.production"]
    assert not (third_wt / ".env.production").exists() and not (third_wt / ".env").exists()


def test_the_preview_given_to_the_screen_is_the_whole_list(project: Path) -> None:
    for i in range(wi.PREVIEW_FILES + 7):
        (project / f".env.{i:02d}").write_text("x\n", encoding="utf-8")
    _rules(project, ".env.*\n")
    wt = _worktree(project)
    seen: list[dict[str, object]] = []

    def record(_d: str, preview: dict[str, object]) -> bool:
        seen.append(preview)
        return False

    report = wi.copy_included(project, wt, consent=record)

    assert len(seen[0]["files"]) == wi.PREVIEW_FILES + 7  # type: ignore[arg-type]
    cut = report.as_payload()["consent_required"]  # the turn frame stays short
    assert len(cut["files"]) == wi.PREVIEW_FILES and cut["more"] == 7


def test_the_listing_has_one_budget_for_all_its_git_calls(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (project / ".gitignore").write_text(".env*\nbuild/\nout/\n", encoding="utf-8")
    for folder in ("build", "out"):
        (project / folder).mkdir()
        (project / folder / "a.env").write_text("x\n", encoding="utf-8")
    rules, _ = wi.parse_rules("**/*.env\n")
    calls: list[list[str]] = []
    real = wi._list_ignored

    def spy(root: Path, extra: object = (), *, timeout: float = 0.0) -> list[str] | None:
        calls.append(list(extra))  # type: ignore[call-overload]
        return real(root, extra, timeout=timeout)  # type: ignore[arg-type]

    monkeypatch.setattr(wi, "_list_ignored", spy)
    ticks = iter([0.0, 0.0, 100.0, 100.0, 100.0, 100.0])  # the budget is gone after the first call

    listed, truncated = wi.ignored_untracked(project, rules, budget=20.0, clock=lambda: next(ticks))

    assert truncated is True
    assert calls == [["--directory"]]  # no call into build/ or out/ once the budget is spent
    assert listed == []


def test_a_listing_cut_by_the_budget_is_reported_as_limited(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (project / ".env").write_text("A=1\n", encoding="utf-8")
    _rules(project, ".env\n")
    monkeypatch.setattr(wi, "ignored_untracked", lambda *_a, **_k: ([".env"], True))

    plan = wi.plan_included(project)

    assert plan.skipped and plan.skipped[0][0] == "(listing)" and "limited" in plan.skipped[0][1]


def test_a_link_swapped_in_after_the_plan_is_not_followed(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (project / ".env").write_text("A=1\n", encoding="utf-8")
    _rules(project, ".env\n")
    wt = _worktree(project)
    source = project / ".env"
    calls = {"n": 0}
    real = wi._is_link

    def swapped(path: Path) -> bool:
        if path == source:
            calls["n"] += 1
            return calls["n"] >= 2  # clean while planning, a link right before the copy
        return real(path)

    monkeypatch.setattr(wi, "_is_link", swapped)

    report = wi.copy_included(project, wt, consent=_yes)

    assert report.copied == [] and not (wt / ".env").exists()
    assert report.skipped and "changed since the plan" in report.skipped[0][1]
