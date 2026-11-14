"""The path from a commit to PyPI, to a user's installer and to a server image.

One of these actions publishes to PyPI with an OIDC identity PyPI trusts, and four `uses:` were a
mutable tag or branch the upstream author can move after we reviewed it. A diff that bumps nothing
cannot show that; only reading every `uses:` can, so this does.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
# `.yaml` runs just like `.yml`; globbing one spelling is how a workflow escapes every check here.
WORKFLOWS = sorted([*WORKFLOW_DIR.glob("*.yml"), *WORKFLOW_DIR.glob("*.yaml")])
SHA = re.compile(r"^[0-9a-f]{40}$")


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _wf(name: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))


def _uses(wf: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for job in wf["jobs"].values():
        for step in job.get("steps", []):
            if "uses" in step:
                out.append(step["uses"])
        if "uses" in job:  # a reusable workflow call
            out.append(job["uses"])
    return out


def test_there_are_workflows_to_check() -> None:
    assert len(WORKFLOWS) >= 5


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_action_is_pinned_to_a_40_hex_commit(path: Path) -> None:
    """`@v4`, `@stable` and `@release/v1` are pointers their owner moves. Local `./` actions are in
    this repository and need no pin."""
    loose = []
    for ref in _uses(yaml.safe_load(path.read_text(encoding="utf-8"))):
        if ref.startswith("./"):
            continue
        _, _, version = ref.partition("@")
        if not SHA.fullmatch(version):
            loose.append(ref)
    assert not loose, f"{path.name}: not pinned to a commit SHA: {loose}"


def test_the_pin_check_itself_rejects_a_tag() -> None:
    """A guard that was never shown to fail is not known to work."""
    assert not SHA.fullmatch("v4")
    assert not SHA.fullmatch("release/v1")
    assert not SHA.fullmatch("3d3c42e5")  # an abbreviated SHA is movable too, in principle
    assert SHA.fullmatch("3d3c42e5aac5ba805825da76410c181273ba90b1")


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_workflow_uses_pull_request_target_or_omits_permissions(path: Path) -> None:
    wf = yaml.safe_load(path.read_text(encoding="utf-8"))
    triggers = wf.get("on", wf.get(True))  # PyYAML reads the bare key `on` as the boolean True
    names = set(triggers) if isinstance(triggers, (dict, list)) else {triggers}
    assert "pull_request_target" not in names
    assert "permissions" in wf, f"{path.name} inherits the repository's default token"


# --- publish.yml -------------------------------------------------------------------------------


def test_publishing_waits_for_the_guard_and_runs_in_a_protected_environment() -> None:
    jobs = _wf("publish.yml")["jobs"]
    assert "guard" in jobs
    for name in ("build-ui", "pypi-publish"):
        needs = jobs[name]["needs"]
        needs = [needs] if isinstance(needs, str) else needs
        assert "guard" in needs, f"{name} can start before the ref is checked"
    # Only the job that holds the OIDC token is in the environment a reviewer approves.
    assert jobs["pypi-publish"]["environment"] == "pypi"
    assert "id-token" not in (jobs["build-ui"].get("permissions") or {})


# What a person other than the maintainer chooses. Pasted into code with `${{ }}`, each is code:
# `v0.89.0$(id>&2)` as a tag executed `id` in a job holding `contents: write`. They belong in `env:`
# and are read as a quoted variable.
_EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)  # non-greedy to `}}`: `format('{0}')` has `}'`
_BRACKET = re.compile(r"""\[\s*['"]([^'"\]]+)['"]\s*\]""")
_CONTROLLABLE = re.compile(
    r"""(?ix)
    \bgithub\.(?:head_ref|ref_name|base_ref)\b
  | \bgithub\.event\.release\.(?:tag_name|name|body)\b
  | \bgithub\.event\.inputs\b
  | \binputs\.
  | \bgithub\.event\.[\w.\-]*?(?:title|body|name|label|message|ref|email|login|description|text|default_branch)\b
    """
)
#: `with:` keys that hand a string to an interpreter even outside `run:`.
_CODE_KEYS = {"script", "run", "command", "args", "entrypoint"}


def _risky(expression: str) -> bool:
    # `github['event']['pull_request']['title']` is the same thing as the dotted spelling.
    dotted = _BRACKET.sub(r".\1", expression)
    return bool(_CONTROLLABLE.search(dotted))


def _code_of(step: dict[str, Any]) -> list[str]:
    """Every string in the step that a shell or a script engine will execute."""
    code = [step.get("run", "")]
    with_ = step.get("with") or {}
    runs_script = str(step.get("uses", "")).startswith("actions/github-script")
    code += [str(v) for k, v in with_.items() if runs_script or k in _CODE_KEYS]
    return [c for c in code if isinstance(c, str)]


def injections(wf: dict[str, Any]) -> list[str]:
    return [
        f"{job_name}: {step.get('name') or step.get('uses')}"
        for job_name, job in wf["jobs"].items()
        for step in job.get("steps", [])
        if any(_risky(e) for c in _code_of(step) for e in _EXPRESSION.findall(c))
    ]


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_code_step_interpolates_text_someone_else_chose(path: Path) -> None:
    bad = injections(yaml.safe_load(path.read_text(encoding="utf-8")))
    assert not bad, f"{path.name}: move into env: and read as a quoted variable: {bad}"


def _wf_with(run: str = "", **step: Any) -> dict[str, Any]:
    return {"jobs": {"j": {"steps": [{"name": "s", **({"run": run} if run else {}), **step}]}}}


PIN = "@" + "0" * 40


@pytest.mark.parametrize(
    "case",
    [
        _wf_with('ESPERADA="${{ github.event.release.tag_name }}"'),
        _wf_with('git fetch origin "${{ github.base_ref }}"'),
        _wf_with('echo "${{ github.event.pull_request.title }}"'),
        _wf_with("echo ${{ github.event.issue.body }}"),
        _wf_with("echo ${{ github.event.pull_request.head.ref }}"),
        _wf_with("echo ${{ inputs.notify }}"),
        # the `}` of the format string must not end the expression early
        _wf_with("echo ${{ format('{0}', github.event.pull_request.title) }}"),
        _wf_with("echo ${{ format('{0}-{1}', 'a', github.head_ref) }}"),
        # index spelling of the same context
        _wf_with("echo ${{ github['event']['pull_request']['title'] }}"),
        _wf_with('echo ${{ github["event"]["release"]["tag_name"] }}'),
        # code that is not `run:`
        _wf_with(uses="actions/github-script" + PIN, **{"with": {"script": "x = '${{ github.head_ref }}'"}}),
        _wf_with(uses="some/action" + PIN, **{"with": {"run": "echo ${{ github.event.pull_request.title }}"}}),
    ],
)
def test_the_injection_check_catches_each_spelling_it_once_missed(case: dict[str, Any]) -> None:
    assert injections(case), case


@pytest.mark.parametrize(
    "case",
    [
        _wf_with('test "${{ matrix.os }}" = windows-latest'),
        _wf_with("echo ${{ github.run_id }} ${{ runner.os }}"),
        _wf_with(uses="actions/checkout" + PIN, **{"with": {"ref": "${{ github.head_ref }}"}}),  # not code
        _wf_with('echo "$RELEASE_TAG"', env={"RELEASE_TAG": "${{ github.event.release.tag_name }}"}),
    ],
)
def test_the_injection_check_leaves_safe_expressions_alone(case: dict[str, Any]) -> None:
    assert not injections(case), case


def test_the_workflow_collection_includes_both_yaml_spellings() -> None:
    """A `.yaml` file the glob skipped is a file no check here ever read."""
    source = Path(__file__).read_text(encoding="utf-8")
    assert 'glob("*.yml")' in source and 'glob("*.yaml")' in source
    assert {p.suffix for p in WORKFLOWS} <= {".yml", ".yaml"}


def test_the_desktop_release_validates_the_tag_it_compares() -> None:
    step = next(
        s
        for s in _wf("desktop-release.yml")["jobs"]["build"]["steps"]
        if s.get("name") == "The frozen sidecar knows its own version"
    )
    assert "RELEASE_TAG" in step["env"] and "RELEASE_TAG" in step["run"]
    assert "grep -Eq" in step["run"], "the tag must match a version pattern before it is used"
    pattern = re.search(r"grep -Eq '([^']+)'", step["run"])
    assert pattern, "the version pattern must be readable"
    grammar = re.compile(pattern.group(1))
    # What RELEASING.md and cut_release.py produce, including the prerelease spellings.
    for ok in ("v0.89.0", "v0.51.0rc1", "v0.48.0rc10", "v1.2.3a1", "v1.2.3b2"):
        assert grammar.search(ok), ok
    # `v` alone left the version empty and skipped the comparison; a suffix is shell.
    for bad in ("", "v", "0.89.0", "v0.89.0$(id>&2)", "v0.89", "v0.89.0-rc.1", "v0.89.0 "):
        assert not grammar.search(bad), bad
    assert 'EVENT_NAME" = "release"' in step["run"], "an empty tag on a release event must be refused"


def test_the_built_files_are_checked_against_the_version_before_upload() -> None:
    steps = _wf("publish.yml")["jobs"]["pypi-publish"]["steps"]
    runs = [s.get("run", "") for s in steps]
    build = next(i for i, r in enumerate(runs) if r.strip() == "uv build")
    check = next(i for i, r in enumerate(runs) if "check_release_ref.py dist" in r)
    publish = next(i for i, s in enumerate(steps) if "gh-action-pypi-publish" in s.get("uses", ""))
    assert build < check < publish


GOOD = {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main"}


def test_a_manual_publish_from_main_is_allowed() -> None:
    assert _load("check_release_ref").check_ref(GOOD, "0.89.0") == []


@pytest.mark.parametrize("ref", ["refs/heads/audit/p0-05-release", "refs/tags/v0.89.0", ""])
def test_a_manual_publish_from_anywhere_else_is_refused(ref: str) -> None:
    assert _load("check_release_ref").check_ref({**GOOD, "GITHUB_REF": ref}, "0.89.0")


def _release(tag: str, ref: str | None = None) -> dict[str, str]:
    return {"GITHUB_EVENT_NAME": "release", "GITHUB_REF": ref or f"refs/tags/{tag}", "RELEASE_TAG": tag}


def test_a_release_whose_tag_is_the_package_version_is_allowed() -> None:
    mod = _load("check_release_ref")
    assert mod.check_ref(_release("v0.89.0"), "0.89.0") == []
    assert mod.check_ref(_release("v0.51.0rc1"), "0.51.0rc1") == []


def test_a_release_whose_tag_differs_from_the_package_version_is_refused() -> None:
    mod = _load("check_release_ref")
    assert mod.check_ref(_release("v0.90.0"), "0.89.0")
    assert mod.check_ref(_release("v0.89.0"), "0.89.1")


def test_a_release_with_a_ref_that_is_not_its_tag_is_refused() -> None:
    mod = _load("check_release_ref")
    assert mod.check_ref(_release("v0.89.0", ref="refs/heads/main"), "0.89.0")
    assert mod.check_ref(_release(""), "0.89.0")


@pytest.mark.parametrize("event", ["push", "pull_request", "schedule", ""])
def test_no_other_event_may_publish(event: str) -> None:
    mod = _load("check_release_ref")
    assert mod.check_ref({"GITHUB_EVENT_NAME": event, "GITHUB_REF": "refs/heads/main"}, "0.89.0")


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def _repo_with_main_and_a_feature_commit(tmp_path: Path) -> tuple[Path, str, str]:
    """`origin/main` is a remote-tracking ref in CI (`fetch-depth: 0`); recreate that shape."""
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a").write_text("1")
    _git(repo, "add", "a")
    _git(repo, "commit", "-q", "-m", "on main")
    on_main = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "refs/remotes/origin/main", on_main)
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "a").write_text("2")
    _git(repo, "commit", "-q", "-am", "only on a feature branch")
    return repo, on_main, _git(repo, "rev-parse", "HEAD")


def test_a_tag_on_a_commit_that_is_not_on_main_is_refused(tmp_path: Path) -> None:
    mod = _load("check_release_ref")
    repo, on_main, feature = _repo_with_main_and_a_feature_commit(tmp_path)
    assert mod.check_ancestry(on_main, repo) == []
    assert mod.check_ancestry(feature, repo), "a feature-branch commit must not publish"


def test_a_tag_named_like_the_main_branch_does_not_stand_in_for_it(tmp_path: Path) -> None:
    """git resolves `origin/main` to a TAG of that name before `refs/remotes/origin/main`, so a tag
    pushed on a feature commit became the "main" the ancestry was checked against."""
    mod = _load("check_release_ref")
    repo, on_main, feature = _repo_with_main_and_a_feature_commit(tmp_path)
    _git(repo, "tag", "origin/main", feature)
    assert _git(repo, "rev-parse", "origin/main^{commit}") == feature  # the trap is real
    assert mod.check_ancestry(feature, repo), "a feature commit passed against a fake origin/main"
    assert mod.check_ancestry(on_main, repo) == []
    assert mod.MAIN_TRACKING_REF == "refs/remotes/origin/main"


def test_a_malformed_sha_is_refused_before_git_sees_it(tmp_path: Path) -> None:
    mod = _load("check_release_ref")
    repo, _, _ = _repo_with_main_and_a_feature_commit(tmp_path)
    assert mod.check_ancestry("--all", repo)
    assert mod.check_ancestry("main", repo)


def test_the_ancestry_check_fails_closed(tmp_path: Path) -> None:
    mod = _load("check_release_ref")
    repo, on_main, _ = _repo_with_main_and_a_feature_commit(tmp_path)
    assert mod.check_ancestry("", repo)  # no sha
    assert mod.check_ancestry("0" * 40, repo)  # a commit this checkout does not have
    _git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    assert mod.check_ancestry(on_main, repo)  # a shallow/odd checkout without origin/main


def test_the_guard_checks_out_full_history() -> None:
    guard = _wf("publish.yml")["jobs"]["guard"]
    assert guard["steps"][0]["with"]["fetch-depth"] == 0


def test_the_dist_check_compares_each_file_to_pyproject(tmp_path: Path) -> None:
    mod = _load("check_release_ref")
    whl = tmp_path / "chimera_agent-0.89.0-py3-none-any.whl"
    sdist = tmp_path / "chimera_agent-0.89.0.tar.gz"
    assert mod.check_dist([whl, sdist], "0.89.0") == []
    stale = tmp_path / "chimera_agent-0.88.0-py3-none-any.whl"
    assert mod.check_dist([whl, stale], "0.89.0")
    assert mod.check_dist([], "0.89.0"), "an empty dist/ must fail, not pass"
    assert mod.check_dist([tmp_path / "other-0.89.0.tar.gz"], "0.89.0")


def test_the_checker_reads_the_real_pyproject_version() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+.*", _load("check_release_ref").project_version())


# --- desktop-release.yml ------------------------------------------------------------------------


def test_the_desktop_release_grants_nothing_by_default() -> None:
    wf = _wf("desktop-release.yml")
    assert wf["permissions"] == {}
    for name, job in wf["jobs"].items():
        assert job.get("permissions"), f"{name} must state what it needs"
        assert "id-token" not in job["permissions"]


# --- dependabot, Docker ------------------------------------------------------------------------


def test_dependabot_watches_the_rust_crates_of_the_desktop_shell() -> None:
    cfg = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    cargo = [u for u in cfg["updates"] if u["package-ecosystem"] == "cargo"]
    assert [u["directory"] for u in cargo] == ["/apps/desktop/src-tauri"]
    assert (ROOT / "apps/desktop/src-tauri/Cargo.lock").is_file()


def _dockerfile() -> list[str]:
    return [
        line.strip()
        for line in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_the_dockerfile_pins_its_base_and_uv_by_digest() -> None:
    sources = [
        re.split(r"\s+", line)
        for line in _dockerfile()
        if line.startswith(("FROM ", "COPY --from="))
    ]
    refs = [t.removeprefix("--from=") for toks in sources for t in toks if t.startswith(("python", "--from=ghcr"))]
    assert len(refs) == 2
    for ref in refs:
        assert re.search(r"@sha256:[0-9a-f]{64}$", ref), f"{ref} is a movable tag"


def test_the_dockerfile_does_not_run_as_root() -> None:
    lines = _dockerfile()
    users = [i for i, line in enumerate(lines) if line.startswith("USER ")]
    assert users, "no USER instruction: the container runs as root"
    assert lines[users[-1]].split()[1] not in {"root", "0"}
    # Whatever installs packages must run before USER, or it would need root it no longer has.
    last_run = max(i for i, line in enumerate(lines) if line.startswith("RUN "))
    assert last_run < users[-1]


def test_the_docker_context_leaves_out_the_benchmarks() -> None:
    lines = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "bench" in [line.strip() for line in lines]


def test_ci_builds_the_image_on_pull_requests_without_secrets_or_push() -> None:
    wf = _wf("ci.yml")
    job = wf["jobs"]["docker-build"]
    assert job["permissions"] == {"contents": "read"}
    text = yaml.dump(job)
    assert "secrets." not in text and "docker push" not in text and "push: true" not in text
    assert "docker-build" in wf["jobs"]["verdict"]["needs"]
    assert job.get("if", "") != "github.event_name == 'push'"


def test_the_image_stops_with_a_clear_message_on_a_state_dir_it_cannot_write() -> None:
    lines = _dockerfile()
    entry = next(line for line in lines if line.startswith("ENTRYPOINT"))
    assert "entrypoint.sh" in entry
    script = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    assert "-w" in script and "exit 78" in script
    assert "docker compose run" in script and "--user 0" in script and "10001:10001" in script
    assert script.rstrip().endswith('exec chimera "$@"'), "the agent must still replace the shell"


def test_the_root_volume_migration_is_documented_in_every_deploy_page() -> None:
    cmd = "--user 0 --entrypoint chown chimera -R 10001:10001 /data"
    pages = [ROOT / "docs" / "deploy.md", *sorted((ROOT / "docs" / "i18n").glob("*/deploy.md"))]
    assert len(pages) >= 8
    for page in pages:
        text = page.read_text(encoding="utf-8")
        assert cmd in text, page
        # Compose prefixes the volume with the project name; a bare `-v chimera-data` chowns nothing.
        assert "-v chimera-data:/data" in text and "docker volume ls" in text, page
        assert "sudo chown -R 10001:10001" in text, f"{page}: no bind-mount case"
