"""The registry of projects, and the two distinctions it exists to keep.

The first is **absent against empty**. Re-registering a project you have already named must not wipe
the name, and clearing a name must be possible — which are different requests that look identical in
JSON unless something keeps them apart. `register(path)` says nothing about the alias;
`register(path, "")` clears it. Collapsing those was how the client-side version would have lost every name the first
time a folder was re-added.

The second is **a bookmark against the work**. Forgetting a project here removes a row from a list.
It does not touch the conversations filed under that workspace and it does not touch the folder —
the sibling test `test_delete_code_project` covers the route that does remove transcripts, and the
whole reason these two live at different paths is that a person tidying a list must not lose work.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.core.code_projects import (
    MAX_PROJECTS,
    AppWorktreeRegistry,
    CodeProjectRegistry,
    app_worktree_registry_path,
)

LOJA = "C:\\Users\\alguem\\loja"
BLOG = "C:\\Users\\alguem\\blog"


def _registry(tmp_path: Path) -> CodeProjectRegistry:
    return CodeProjectRegistry(tmp_path / "code_projects.json")


def test_an_empty_registry_is_an_empty_list_not_an_error(tmp_path: Path) -> None:
    """A fresh install has no file, and that is the normal case rather than a failure."""
    assert _registry(tmp_path).entries() == []


def test_projects_come_back_in_the_order_they_were_added(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register(LOJA)
    registry.register(BLOG)
    assert [row.path for row in registry.entries()] == [LOJA, BLOG]


def test_registering_the_same_path_twice_does_not_list_it_twice(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register(LOJA)
    rows = registry.register(LOJA)
    assert [row.path for row in rows] == [LOJA]


def test_re_registering_a_named_project_keeps_the_name(tmp_path: Path) -> None:
    """The distinction this file exists for: no alias in the request means *say nothing about it*."""
    registry = _registry(tmp_path)
    registry.register(LOJA, "a loja")
    rows = registry.register(LOJA)
    assert rows[0].alias == "a loja"


def test_an_empty_alias_clears_the_name(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register(LOJA, "a loja")
    rows = registry.register(LOJA, "")
    assert rows[0].alias == ""


def test_forgetting_one_leaves_the_others(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register(LOJA)
    registry.register(BLOG)
    assert [row.path for row in registry.remove(LOJA)] == [BLOG]


def test_forgetting_something_never_registered_is_not_an_error(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register(LOJA)
    assert [row.path for row in registry.remove(BLOG)] == [LOJA]


def test_a_blank_path_is_not_a_project(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="path"):
        _registry(tmp_path).register("   ")


def test_the_number_of_projects_is_bounded(tmp_path: Path) -> None:
    """Not a judgement about how many checkouts anyone has — a bound on what a looping client can
    grow the file to, since nothing else here limits how often a client may POST."""
    registry = _registry(tmp_path)
    for index in range(MAX_PROJECTS):
        registry.register(f"C:\\p\\{index}")
    with pytest.raises(ValueError, match="at most"):
        registry.register("C:\\p\\one-too-many")


def test_an_unreadable_file_is_kept_rather_than_overwritten(tmp_path: Path) -> None:
    """A project list is the one thing here the machine cannot reconstruct — the person who typed it
    is the only copy. So a parse failure moves the bytes aside instead of reading as empty and
    letting the next write destroy them."""
    path = tmp_path / "code_projects.json"
    path.write_text("{not json at all", encoding="utf-8")
    registry = CodeProjectRegistry(path)

    assert registry.entries() == []
    assert path.with_suffix(".json.corrupt").read_text(encoding="utf-8") == "{not json at all"

    registry.register(LOJA)
    assert [row.path for row in registry.entries()] == [LOJA]


def test_a_shape_nobody_wrote_reads_as_empty(tmp_path: Path) -> None:
    """Valid JSON of the wrong shape is not corruption — it is a file someone edited by hand. Rows
    that are not rows are skipped, and the ones that are survive."""
    path = tmp_path / "code_projects.json"
    path.write_text(
        json.dumps({"projects": ["not a row", {"path": LOJA, "alias": 7}, {"alias": "x"}]}),
        encoding="utf-8",
    )
    rows = CodeProjectRegistry(path).entries()
    assert [(row.path, row.alias) for row in rows] == [(LOJA, "")]

def test_removing_project_invalidates_its_approved_hashes_and_prepared_setup(tmp_path: Path) -> None:
    from chimera.core.code_projects import prepared_setups_path

    project = tmp_path / "project"
    project.mkdir()
    registry = _registry(tmp_path / "home")
    registry.set_grant(str(project), True)
    digest = "f" * 64
    assert registry.approve_verify_hash(project, digest)
    assert registry.approve_setup_hash(project, digest)
    registry.mark_setup_prepared(project, digest)

    repo = tmp_path / "repo"
    repo.mkdir()
    import subprocess
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "--allow-empty", "-m", "initial"], check=True, capture_output=True)
    from chimera.core.worktree import GitWorktree
    child = GitWorktree.create(repo, branch="remove-registration", durable=True,
                               app_registry=app_worktree_registry_path(registry.path.parent))
    app_path = app_worktree_registry_path(registry.path.parent)
    child.remove()
    registry.remove(str(project))

    assert registry.project_for(project) is None
    assert json.loads(app_path.read_text(encoding="utf-8")) == {}
    assert not registry.setup_was_prepared(project, digest)
    assert registry.approved_verify_hash_for(project) == ""
    assert registry.approved_setup_hash_for(project) == ""
    assert json.loads(prepared_setups_path(registry.path).read_text(encoding="utf-8")) == {}


def test_the_write_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register(LOJA)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["code_projects.json"]


def test_the_file_is_readable_by_a_person(tmp_path: Path) -> None:
    """Seeding this list by hand is a supported thing to do — it is how six projects reached an
    installation that had never been used — so the file stays indented JSON with the paths
    unescaped."""
    registry = _registry(tmp_path)
    registry.register("C:\\Users\\alguém\\Área de Trabalho\\loja", "a loja")
    written = json.loads((tmp_path / "code_projects.json").read_text(encoding="utf-8"))
    assert written == {"projects": [{"path": "C:\\Users\\alguém\\Área de Trabalho\\loja",
                                     "alias": "a loja"}]}


# ------------------------------------------------- the grant, the pin and hiding (study 29, P4.3)


def test_a_file_from_an_older_build_grants_nothing(tmp_path: Path) -> None:
    """Every file written before the grant existed has rows with only a path and an alias. Reading
    one must not turn a bookmark into a permission."""
    path = tmp_path / "code_projects.json"
    path.write_text(json.dumps({"projects": [{"path": LOJA, "alias": "a loja"}]}), encoding="utf-8")
    row = CodeProjectRegistry(path).entries()[0]
    assert (row.shell_granted, row.pinned, row.hidden) == (False, False, False)


def test_only_a_literal_true_is_a_grant(tmp_path: Path) -> None:
    """A hand-edited `"shell_granted": "no"` is a non-empty string. Truthiness would read it as yes."""
    path = tmp_path / "code_projects.json"
    path.write_text(
        json.dumps({"projects": [{"path": LOJA, "shell_granted": "no"}, {"path": BLOG,
                                                                          "shell_granted": 1}]}),
        encoding="utf-8",
    )
    assert [row.shell_granted for row in CodeProjectRegistry(path).entries()] == [False, False]


def test_the_migration_preserves_every_grant_it_is_given(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register(LOJA, "a loja")
    migrated, recorded, rows = registry.migrate_grants([LOJA, BLOG, "  ", LOJA])
    assert migrated is True and recorded == 2
    assert {row.path: row.shell_granted for row in rows} == {LOJA: True, BLOG: True}
    # The name a migrated row already had survives the migration.
    assert next(row for row in rows if row.path == LOJA).alias == "a loja"
    assert registry.grants_migrated_at()


def test_the_migration_happens_once(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.migrate_grants([LOJA])
    migrated, recorded, rows = registry.migrate_grants([BLOG])
    assert (migrated, recorded) == (False, 0)
    assert [row.path for row in rows] == [LOJA]


def test_the_migration_stamp_survives_later_writes(tmp_path: Path) -> None:
    """Every other write rewrites the file. Losing the stamp on the next rename would reopen the
    one-time window for good."""
    registry = _registry(tmp_path)
    registry.migrate_grants([])
    registry.register(BLOG, "o blog")
    registry.set_flags(BLOG, pinned=True)
    assert registry.migrate_grants([LOJA])[0] is False


def test_setup_approval_is_project_scoped_and_revocation_clears_it(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    registry.register(str(project))
    registry.set_grant(str(project), True)
    digest = "a" * 64
    assert registry.approve_setup_hash(project, digest)
    assert registry.approved_setup_hash_for(project) == digest
    registry.set_grant(str(project), False)
    assert registry.approved_setup_hash_for(project) == ""

def test_setup_preparation_and_failure_cooldown_are_scoped_to_command_hash(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    digest = "a" * 64
    changed = "b" * 64
    assert registry.setup_retry_allowed(project, digest)
    registry.record_setup_failure(project, digest)
    assert not registry.setup_retry_allowed(project, digest)
    assert registry.setup_retry_allowed(project, changed)
    assert not registry.setup_was_prepared(project, digest)
    registry.mark_setup_prepared(project, digest)
    assert registry.setup_was_prepared(project, digest)
    registry.set_grant(str(project), False)
    assert not registry.setup_was_prepared(project, digest)


def test_prepared_setup_discards_missing_workspace_entries(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    missing = tmp_path / "gone"
    state = registry.path.with_name("prepared_setups.json")
    state.write_text(json.dumps({f"{project}|{'a' * 64}": True,
                                 f"{missing}|{'b' * 64}": True}), encoding="utf-8")
    assert registry.setup_was_prepared(project, "a" * 64)
    assert len(json.loads(state.read_text(encoding="utf-8"))) == 1

def test_only_registered_app_worktrees_inherit_grants_and_hashes(tmp_path: Path) -> None:
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    registry = _registry(tmp_path / "home")
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "--allow-empty", "-m", "initial"], check=True, capture_output=True)
    registry.set_grant(str(repo), True)
    verify_hash = "c" * 64
    assert registry.approve_verify_hash(repo, verify_hash)
    wt = repo.parent / "repo-worktree"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-b", "test-wt", str(wt)],
                   check=True, capture_output=True)
    assert registry.project_for(wt) is None
    unregistered_nested = repo.parent / ".chimera-worktrees" / "repo" / "manual"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-b", "manual", str(unregistered_nested)],
                   check=True, capture_output=True)
    assert registry.project_for(unregistered_nested) is None
    # The app creation path and project lookup must share one HOME-owned registry.
    from chimera.core.worktree import GitWorktree

    assert app_worktree_registry_path(registry.path.parent) == registry.path.parent / "app_worktrees.json"
    app_wt = GitWorktree.create(
        repo, branch="app-created", label="fixture", durable=True,
        app_registry=app_worktree_registry_path(registry.path.parent),
    )
    inherited = registry.project_for(app_wt.path)
    assert inherited is not None and inherited.shell_granted
    assert inherited.approved_verify_hash == verify_hash
    subprocess.run(
        ["git", "-C", str(app_wt.path), "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "commit", "--allow-empty", "-m", "nonce-survives-git-write"],
        check=True,
        capture_output=True,
    )
    inherited_after_commit = registry.project_for(app_wt.path)
    assert inherited_after_commit is not None and inherited_after_commit.shell_granted
    app_wt.remove()


def test_recreated_worktree_at_same_path_does_not_inherit_old_registration(tmp_path: Path) -> None:
    import shutil
    import subprocess

    from chimera.core.project_place import resolve_place
    from chimera.core.worktree import _admin_dir

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    registry = _registry(tmp_path / "home")
    registry.set_grant(str(repo), True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "--allow-empty", "-m", "initial"], check=True, capture_output=True)
    app_registry_path = app_worktree_registry_path(registry.path.parent)

    placed = resolve_place(str(repo), worktree="new", branch="original", home=registry.path.parent)
    wt = Path(placed.workspace)
    app_registry = AppWorktreeRegistry(app_registry_path)
    before = app_registry.path.read_bytes()
    old_admin = _admin_dir(wt)
    assert old_admin is not None and registry.project_for(wt) is not None
    shutil.rmtree(wt)
    subprocess.run(["git", "-C", str(repo), "worktree", "prune"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-b", "recreated", str(wt)],
                   check=True, capture_output=True)
    assert _admin_dir(wt) == old_admin  # Git reuses the basename; the admin directory is new.
    assert registry.project_for(wt) is None
    assert app_registry.path.read_bytes() == before


def test_project_for_discards_registration_with_missing_checkout(tmp_path: Path) -> None:
    import shutil
    import subprocess

    from chimera.core.worktree import GitWorktree

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "--allow-empty", "-m", "initial"], check=True, capture_output=True)
    registry = _registry(tmp_path / "home")

    made = GitWorktree.create(
        repo, branch="gone", durable=True,
        app_registry=app_worktree_registry_path(registry.path.parent),
    )
    app_registry = AppWorktreeRegistry(app_worktree_registry_path(registry.path.parent))
    shutil.rmtree(made.path)
    assert registry.project_for(made.path) is None
    assert str(made.path.resolve()) not in app_registry._read()

def test_app_worktree_registration_uses_the_same_explicit_home_as_code_registry(tmp_path: Path) -> None:
    import subprocess

    from chimera.core.project_place import resolve_place

    home = tmp_path / "same-home"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "--allow-empty", "-m", "initial"], check=True, capture_output=True)
    registry = _registry(home)
    registry.set_grant(str(repo), True)

    placed = resolve_place(str(repo), worktree="new", branch="home-check", home=home)

    inherited = CodeProjectRegistry(home / "code_projects.json").project_for(placed.workspace)
    assert inherited is not None and inherited.shell_granted
    assert app_worktree_registry_path(home).exists()


def test_genuine_git_worktree_outside_app_does_not_inherit(tmp_path: Path) -> None:
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    registry = _registry(tmp_path / "home")
    registry.set_grant(str(repo), True)
    wt = tmp_path / "genuine-external"
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "--allow-empty", "-m", "initial"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-b", "outside", str(wt)],
                   check=True, capture_output=True)
    assert registry.project_for(wt) is None


def test_forged_owner_metadata_does_not_grant_inheritance(tmp_path: Path) -> None:
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    forged = repo / ".chimera-worktrees" / "repo" / "forged"
    forged.mkdir(parents=True)
    fake_git = tmp_path / "fake.git"
    fake_admin = fake_git / "worktrees" / "forged"
    fake_admin.mkdir(parents=True)
    (fake_admin / "commondir").write_text(str(repo / ".git"), encoding="utf-8")
    (fake_admin / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
    (forged / ".git").write_text(f"gitdir: {fake_admin}\n", encoding="utf-8")
    (fake_admin / "chimera-owner.json").write_text(
        json.dumps({"durable": True, "pid": 1}), encoding="utf-8",
    )
    registry = _registry(tmp_path / "home")
    registry.set_grant(str(repo), True)
    assert registry.project_for(forged) is None

def test_model_inherits_from_registered_project_to_subfolder(tmp_path: Path) -> None:
    project = tmp_path / "X"
    child = project / "app"
    child.mkdir(parents=True)
    registry = _registry(tmp_path / "home")
    registry.set_model(str(project), "vendor/project")

    assert registry.model_for(child) == "vendor/project"


def test_nearest_registered_ancestor_model_wins(tmp_path: Path) -> None:
    project = tmp_path / "X"
    child = project / "app"
    child.mkdir(parents=True)
    registry = _registry(tmp_path / "home")
    registry.set_model(str(project), "vendor/project")
    registry.set_model(str(child), "vendor/app")

    assert registry.model_for(child / "src") == "vendor/app"


def test_hidden_registered_ancestor_does_not_supply_a_model(tmp_path: Path) -> None:
    project = tmp_path / "X"
    child = project / "app"
    child.mkdir(parents=True)
    registry = _registry(tmp_path / "home")
    registry.set_model(str(project), "vendor/project")
    registry.set_flags(str(project), hidden=True)

    assert registry.model_for(child) == ""


def test_project_for_does_not_inherit_a_parent_shell_grant(tmp_path: Path) -> None:
    project = tmp_path / "X"
    child = project / "app"
    child.mkdir(parents=True)
    registry = _registry(tmp_path / "home")
    registry.set_grant(str(project), True)

    assert registry.project_for(child) is None


def test_exact_registration_without_model_blocks_ancestor_model(tmp_path: Path) -> None:
    project = tmp_path / "X"
    child = project / "app"
    child.mkdir(parents=True)
    registry = _registry(tmp_path / "home")
    registry.set_model(str(project), "vendor/project")
    registry.register(str(child))

    assert registry.model_for(child) == ""


def test_a_forged_worktree_cannot_inherit_a_registered_parent_model(tmp_path: Path) -> None:
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "commit", "--allow-empty", "-m", "initial"],
        check=True, capture_output=True,
    )
    registry = _registry(tmp_path / "home")
    registry.set_model(str(repo), "vendor/parent")

    forged = tmp_path / "forged-worktree"
    admin = repo / ".git" / "worktrees" / "fake"
    forged.mkdir()
    admin.mkdir(parents=True)
    (admin / "commondir").write_text("../..", encoding="utf-8")
    (admin / "HEAD").write_text("ref: refs/heads/forged", encoding="utf-8")
    (forged / ".git").write_text(f"gitdir: {admin}\n", encoding="utf-8")
    (admin / "chimera-owner.json").write_text(
        json.dumps({"app_registry_nonce": "forged-nonce"}), encoding="utf-8",
    )
    assert registry.model_for(forged) == ""


def test_a_revoked_grant_keeps_no_date(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.set_grant(LOJA, True)
    first = registry.entries()[0].granted_at
    registry.set_grant(LOJA, True)
    assert registry.entries()[0].granted_at == first, "re-granting rewrote when it was granted"
    registry.set_grant(LOJA, False)
    assert registry.entries()[0].granted_at == ""

def test_hidden_parent_cannot_grant_its_child_worktree(tmp_path: Path) -> None:
    import subprocess

    from chimera.core.worktree import GitWorktree

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "--allow-empty", "-m", "initial"], check=True, capture_output=True)
    registry = _registry(tmp_path / "home")
    registry.set_grant(str(repo), True)
    child = GitWorktree.create(repo, branch="hidden-parent-child", durable=True,
                               app_registry=app_worktree_registry_path(registry.path.parent))
    registry.set_flags(str(repo), hidden=True)

    assert registry.project_for(child.path) is None
    child.remove()


def test_grant_revocation_invalidates_prepared_setup(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    registry.set_grant(str(project), True)
    digest = "d" * 64
    registry.mark_setup_prepared(project, digest)
    assert registry.setup_was_prepared(project, digest)
    registry.set_grant(str(project), False)
    assert not registry.setup_was_prepared(project, digest)


def test_hiding_parent_invalidates_child_prepared_marker(tmp_path: Path) -> None:
    import subprocess

    from chimera.core.worktree import GitWorktree

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    registry = _registry(tmp_path / "home")
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "--allow-empty", "-m", "initial"], check=True, capture_output=True)
    registry.set_grant(str(repo), True)
    child = GitWorktree.create(
        repo, branch="child", durable=True,
        app_registry=app_worktree_registry_path(registry.path.parent),
    )
    app_registry_path = app_worktree_registry_path(registry.path.parent)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "--allow-empty", "-m", "initial"], check=True, capture_output=True)
    digest = "e" * 64
    registry.mark_setup_prepared(child.path, digest)
    assert registry.setup_was_prepared(child.path, digest)
    registry.set_flags(str(repo), hidden=True)
    assert not registry.setup_was_prepared(child.path, digest)
    child.remove()
    app_state = json.loads(app_registry_path.read_text(encoding="utf-8"))
    assert str(child.path.resolve()) not in app_state


def test_granting_a_hidden_folder_unhides_it(tmp_path: Path) -> None:
    """A grant on a row the lists do not show would be a permission nobody can find."""
    registry = _registry(tmp_path)
    registry.set_flags(LOJA, hidden=True)
    registry.set_grant(LOJA, True)
    row = registry.entries()[0]
    assert row.shell_granted and not row.hidden


def test_unhiding_restores_neither_grant_nor_pin(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.set_grant(LOJA, True)
    registry.set_flags(LOJA, pinned=True)
    registry.set_flags(LOJA, hidden=True)
    registry.set_flags(LOJA, hidden=False)
    row = registry.entries()[0]
    assert (row.hidden, row.shell_granted, row.pinned) == (False, False, False)


def test_a_file_without_decisions_is_written_as_before(tmp_path: Path) -> None:
    """The fields appear in the file only when set, so a file seeded by hand stays two keys a row."""
    registry = _registry(tmp_path)
    registry.register(LOJA, "a loja")
    registry.set_flags(LOJA, pinned=True)
    registry.set_flags(LOJA, pinned=False)
    written = json.loads((tmp_path / "code_projects.json").read_text(encoding="utf-8"))
    assert written == {"projects": [{"path": LOJA, "alias": "a loja"}]}
