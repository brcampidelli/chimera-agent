"""`open_pull_request` and the Git panel's button: the push they make, and who has to say yes.

Study 29, P8.1. The measurement the plan asked for is a policy test — the tool is a REVIEW on every
surface, under the most open setting there is — and that is the spine of this file: under
``CHIMERA_APPROVAL_MODE=allow``, the posture that never stops, governance in ``observe``, a yes given
a moment earlier, and on each surface's own way of asking, the tool still asks and silence still
refuses.

Around it, the properties of the push itself, each against a real local bare repository and a fake
``gh`` that records what it was handed — no network, no GitHub:

* the default branch is never pushed, and nothing is ever forced;
* what is pushed is the commit the person was shown, by hash;
* the title, the branch and the body reach git and gh as arguments, never through a shell;
* a credential in origin's URL is never shown.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from chimera.config import Settings, get_settings
from chimera.core import pull_request as pr
from chimera.governance import pending
from chimera.governance.approval import ApprovalAnnouncer, always_ask
from chimera.governance.policy import Decision
from chimera.tools.base import is_refusal
from chimera.tools.pull_request import OpenPullRequestTool

FAKE_GH = '''
import json, os, sys
log = os.environ["FAKE_GH_LOG"]
entry = {"argv": sys.argv[1:], "stdin": "" if sys.stdin is None else sys.stdin.read()}
with open(log, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(entry) + "\\n")
if sys.argv[1:3] == ["auth", "status"]:
    sys.exit(int(os.environ.get("FAKE_GH_AUTH", "0")))
if sys.argv[1:3] == ["pr", "create"]:
    if os.environ.get("FAKE_GH_FAIL"):
        sys.stderr.write("a pull request for this branch already exists\\n")
        sys.exit(1)
    print("https://github.test/owner/repo/pull/7")
    sys.exit(0)
sys.exit(2)
'''


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=True
    )
    return out.stdout.strip()


def _commit(cwd: Path, name: str, text: str) -> str:
    (cwd / name).write_text(text, encoding="utf-8")
    _git(cwd, "add", "--", name)
    _git(cwd, "commit", "-q", "-m", f"change {name}")
    return _git(cwd, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A bare `origin` with `main`, a clone of it on branch `feature` one commit ahead, and a fake gh."""
    for key, value in {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.test",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.test",
        "FAKE_GH_LOG": str(tmp_path / "gh.jsonl"),
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("FAKE_GH_AUTH", raising=False)
    monkeypatch.delenv("FAKE_GH_FAIL", raising=False)
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    _commit(seed, "README.md", "hello\n")
    _git(seed, "push", "-q", str(origin), "main")
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    _git(work, "checkout", "-q", "-b", "feature")
    head = _commit(work, "feature.py", "print('hi')\n")
    fake = tmp_path / "fake_gh.py"
    fake.write_text(FAKE_GH, encoding="utf-8")
    return {
        "origin": origin,
        "work": work,
        "head": head,
        "gh": [sys.executable, str(fake)],
        "log": tmp_path / "gh.jsonl",
    }


def _gh_calls(repo: dict[str, Any]) -> list[dict[str, Any]]:
    log: Path = repo["log"]
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]


def _remote_ref(repo: dict[str, Any], branch: str) -> str:
    out = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo["origin"], capture_output=True, text=True, encoding="utf-8",
    )
    return out.stdout.strip()


# ------------------------------------------------------------------ readiness


def test_a_feature_branch_one_commit_ahead_is_ready_and_names_the_commit(repo: dict[str, Any]) -> None:
    state = pr.readiness(repo["work"], gh=repo["gh"])

    assert state.reason == "" and state.ready
    assert (state.branch, state.base, state.head, state.ahead) == ("feature", "main", repo["head"], 1)
    assert state.commits[0].endswith("change feature.py")
    assert "feature.py" in state.diffstat
    assert state.gh and state.gh_signed_in


def test_the_default_branch_is_never_ready(repo: dict[str, Any]) -> None:
    _git(repo["work"], "checkout", "-q", "main")
    _commit(repo["work"], "on_main.txt", "x\n")

    assert pr.readiness(repo["work"], gh=repo["gh"]).reason == "default_branch"
    # Naming another base does not make origin's default pushable either.
    assert pr.readiness(repo["work"], base="feature", gh=repo["gh"]).reason == "default_branch"


def test_without_origin_without_gh_or_signed_out_it_says_which(
    repo: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_GH_AUTH", "1")
    assert pr.readiness(repo["work"], gh=repo["gh"]).reason == "gh_signed_out"

    monkeypatch.setattr(pr.shutil, "which", lambda _name: None)
    assert pr.readiness(repo["work"]).reason == "no_gh"

    _git(repo["work"], "remote", "remove", "origin")
    assert pr.readiness(repo["work"], gh=repo["gh"]).reason == "no_origin"


def test_a_gh_that_would_run_through_cmd_exe_is_not_used(monkeypatch: pytest.MonkeyPatch) -> None:
    """cmd.exe re-parses a batch file's arguments, and the title is text the agent wrote."""
    # An absolute directory on THIS platform: `C:\tools` is a relative name on POSIX, which
    # `gh_command` rightly treats as "found in the current directory" and searches PATH instead,
    # so the suffix rule under test was never reached there.
    tools = os.path.join(os.path.abspath(os.sep), "tools")
    monkeypatch.setattr(pr.shutil, "which", lambda _name: os.path.join(tools, "gh.CMD"))
    assert pr.gh_command() is None
    monkeypatch.setattr(pr.shutil, "which", lambda _name: os.path.join(tools, "gh.exe"))
    assert pr.gh_command() == [os.path.join(tools, "gh.exe")]


def test_nothing_ahead_and_a_detached_head_are_not_ready(repo: dict[str, Any]) -> None:
    _git(repo["work"], "checkout", "-q", "-b", "empty", "main")
    assert pr.readiness(repo["work"], gh=repo["gh"]).reason == "nothing_ahead"
    _git(repo["work"], "checkout", "-q", "--detach", repo["head"])
    assert pr.readiness(repo["work"], gh=repo["gh"]).reason == "detached"


def test_a_credential_in_origins_url_is_never_shown(repo: dict[str, Any]) -> None:
    token = "ghp_" + "a1B2" * 9
    _git(repo["work"], "remote", "set-url", "origin", f"https://bruno:{token}@example.invalid/o/r.git")

    state = pr.readiness(repo["work"], gh=repo["gh"])

    assert token not in json.dumps(state.as_dict())
    assert state.remote == "https://***@example.invalid/o/r.git"
    assert pr.redact(f"fatal: could not read from {token} at https://x:{token}@h/") == (
        "fatal: could not read from *** at https://***@h/"
    )
    assert pr.repo_slug("git@github.com:owner/repo.git") == "github.com/owner/repo"
    assert pr.repo_slug(str(repo["origin"])) == ""


# ------------------------------------------------------------------ the push


def test_a_yes_pushes_exactly_the_reviewed_commit_and_leaves_main_alone(repo: dict[str, Any]) -> None:
    main_before = _remote_ref(repo, "main")

    out = pr.open_pull_request(
        repo["work"], title="Add feature", body="Why.", expect_head=repo["head"], gh=repo["gh"]
    )

    assert out["ok"], out
    assert out["url"] == "https://github.test/owner/repo/pull/7"
    assert _remote_ref(repo, "feature") == repo["head"]
    assert _remote_ref(repo, "main") == main_before


def test_title_branch_and_body_reach_gh_as_arguments_never_through_a_shell(
    repo: dict[str, Any],
) -> None:
    title = "-x $(touch pwned) ; rm -rf . `id` | cat"
    body = "line one\n$(touch pwned2)\n--draft\n"

    out = pr.open_pull_request(repo["work"], title=title, body=body, expect_head=repo["head"], gh=repo["gh"])

    assert out["ok"], out
    create = [call for call in _gh_calls(repo) if call["argv"][:2] == ["pr", "create"]]
    assert len(create) == 1
    argv = create[0]["argv"]
    assert f"--title={title}" in argv, "the title is ONE argument, flag and value together"
    assert "--head=feature" in argv and "--base=main" in argv
    assert "--body-file=-" in argv and "--draft" not in argv
    assert create[0]["stdin"] == body, "the body went over stdin, byte for byte"
    assert not (repo["work"] / "pwned").exists() and not (repo["work"] / "pwned2").exists()


def test_a_remote_branch_that_moved_on_is_never_forced(repo: dict[str, Any], tmp_path: Path) -> None:
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(repo["origin"]), str(other))
    _git(other, "checkout", "-q", "-b", "feature")
    theirs = _commit(other, "theirs.txt", "someone else's work\n")
    _git(other, "push", "-q", "origin", "feature")

    out = pr.open_pull_request(repo["work"], title="Mine", body="", expect_head=repo["head"], gh=repo["gh"])

    assert not out["ok"]
    assert _remote_ref(repo, "feature") == theirs, "their commit is still the tip"
    assert not [c for c in _gh_calls(repo) if c["argv"][:2] == ["pr", "create"]]


def test_a_branch_that_moved_after_review_is_refused_not_pushed_at_its_new_tip(
    repo: dict[str, Any],
) -> None:
    _commit(repo["work"], "later.py", "x = 1\n")

    out = pr.open_pull_request(repo["work"], title="T", body="", expect_head=repo["head"], gh=repo["gh"])

    assert not out["ok"] and "moved after it was reviewed" in out["error"]
    assert _remote_ref(repo, "feature") == ""


def test_a_failed_gh_says_the_branch_was_pushed_and_is_still_there(
    repo: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_GH_FAIL", "1")
    out = pr.open_pull_request(repo["work"], title="T", body="", expect_head=repo["head"], gh=repo["gh"])
    assert not out["ok"]
    assert "already exists" in out["error"] and "was pushed to origin and is still there" in out["error"]


@pytest.mark.parametrize(
    ("title", "body", "problem"),
    [("", "", "empty"), ("two\nlines", "", "one line"), ("x" * 257, "", "longer than"),
     ("ok", "b" * (pr.MAX_BODY + 1), "description")],
    ids=["empty-title", "two-line-title", "long-title", "long-body"],
)
def test_a_title_or_body_github_would_refuse_is_refused_first(
    repo: dict[str, Any], title: str, body: str, problem: str
) -> None:
    out = pr.open_pull_request(repo["work"], title=title, body=body, expect_head=repo["head"], gh=repo["gh"])
    assert not out["ok"] and problem in out["error"]
    assert _remote_ref(repo, "feature") == ""


# ------------------------------------------------------------------ the tool asks, every time


def _tool(repo: dict[str, Any], approve: Any) -> OpenPullRequestTool:
    return OpenPullRequestTool(repo["work"], approve=approve, gh=repo["gh"])


def test_the_card_is_a_review_that_shows_what_will_be_published(repo: dict[str, Any]) -> None:
    seen: list[tuple[Any, str]] = []

    def yes(verdict: Any, action: str) -> bool:
        seen.append((verdict, action))
        return True

    out = _tool(repo, yes).run(title="Add feature", body="Because.")

    assert out.startswith("Opened pull request https://github.test/owner/repo/pull/7")
    (verdict, action), = seen
    assert verdict.decision is Decision.REVIEW and verdict.rule == "open_pull_request"
    first = action.splitlines()[0]
    assert first.startswith("open_pull_request: feature -> main on ")
    assert f"commit {repo['head'][:12]}" in action
    assert "title: Add feature" in action and "Because." in action and "feature.py" in action


def _a_big_branch(repo: dict[str, Any]) -> None:
    """Five more commits over forty files, so the commit list and the diff summary alone run past
    one chat message — and a changed file left uncommitted."""
    work: Path = repo["work"]
    for n in range(5):
        for k in range(8):
            (work / f"module_with_a_rather_long_name_{n}_{k}.py").write_text(f"x = {n}\n", encoding="utf-8")
        _git(work, "add", "--", ".")
        _git(work, "commit", "-q", "-m", f"feat: a fairly descriptive commit subject line number {n} " + "x" * 40)
    (work / "README.md").write_text("edited, not committed\n", encoding="utf-8")


def test_the_owners_channel_is_sent_the_whole_card_in_messages_that_fit(
    repo: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The approver the bots, cron and `POST /api/runs` get, with its channel captured. The card used
    to reach the channel as its first 300 characters: it stopped inside the commit list, so the
    description, the diff summary and the NOT-included warning — what the yes is about — never
    arrived. Now every line of it arrives, each message short enough for Discord."""
    import chimera.sandbox.confirm as confirm
    from chimera.scheduler.delivery import MAX_CHARS

    monkeypatch.setattr(confirm, "_no_human_surface", "test")
    _a_big_branch(repo)
    seen: list[str] = []
    approve = always_ask(tmp_path / "home", deliver=seen.append, wait_seconds=0.0)
    body = "What changed and why. " * 24 + "END-OF-THE-DESCRIPTION"

    out = _tool(repo, approve).run(title="T" * 115, body=body)

    assert is_refusal(out) and _remote_ref(repo, "feature") == ""
    delivered = "\n".join(seen)
    assert len(seen) >= 2, "the card is longer than one message, so it arrives in several"
    assert all(len(part) <= MAX_CHARS for part in seen), [len(part) for part in seen]
    assert "END-OF-THE-DESCRIPTION" in delivered
    assert "changed file(s) in the workspace are NOT included" in delivered
    assert "module_with_a_rather_long_name_4_7.py" in delivered, "the diff summary, to its end"
    assert "chimera approve" in seen[-1], "the way to answer comes after what it answers"
    card = delivered[delivered.index("open_pull_request:") :]
    assert card.index("NOT included") < card.index("description:") < card.index("commit(s)"), (
        "what is published comes before the commit list"
    )


def test_a_person_at_the_terminal_reads_the_whole_card_and_no_escape_in_it_runs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The REPL's path: `always_ask` with a person at the console. The prompt printed 300
    characters; a card is the published text, so it prints all of it — with control characters
    written out, because the text is the agent's and a terminal obeys an ESC sequence."""
    import builtins

    import chimera.sandbox.confirm as confirm

    monkeypatch.setattr(confirm, "human_can_answer", lambda: True)
    monkeypatch.setattr(builtins, "input", lambda *_a: "n")
    action = "open_pull_request: feature -> main\n" + "d" * 400 + "\x1b[2A\x1b[2K LAST-LINE"

    assert always_ask(None)(pr_verdict(), action) is False

    err = capsys.readouterr().err
    assert "LAST-LINE" in err
    assert "\x1b" not in err and "\\x1b[2A" in err


def test_a_long_text_is_split_between_lines_and_nothing_is_lost() -> None:
    lines = "\n".join(f"line {n} " + "y" * 90 for n in range(60))
    parts = pending.split_for_channel(lines, limit=500)
    assert all(len(part) <= 500 for part in parts)
    assert "\n".join(parts) == lines, "broken between lines, every one kept"

    long_line = lines + "\n" + "z" * 4000 + "\nafter"
    parts = pending.split_for_channel(long_line, limit=500)
    assert all(len(part) <= 500 for part in parts)
    assert "".join(parts).replace("\n", "") == long_line.replace("\n", ""), "not one character lost"
    assert parts[-1] == "after"
    assert pending.split_for_channel("short") == ["short"]


def test_chimera_approve_show_prints_the_whole_question_and_answers_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--list` cuts the action at 120 characters; `--show` is how the whole of it is read from a
    shell, and it never doubles as the answer."""
    import time

    from typer.testing import CliRunner

    from chimera.cli.main import app

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    get_settings.cache_clear()
    directory = tmp_path / "approvals"
    directory.mkdir()
    action = "open_pull_request: feature -> main\ntitle: [x] T\n" + "w" * 300 + " TAIL\x1b]52;c;aGk=\x07"
    (directory / "abc123def456.ask.json").write_text(
        json.dumps(
            {"id": "abc123def456", "action": action, "reason": "asked every time",
             "asked_at": time.time(), "decision": "review"}
        ),
        encoding="utf-8",
    )
    runner = CliRunner()
    try:
        listed = runner.invoke(app, ["approve"])
        shown = runner.invoke(app, ["approve", "abc123def456", "--show"])
        both = runner.invoke(app, ["approve", "abc123def456", "--show", "--yes"])
        missing = runner.invoke(app, ["approve", "000000000000", "--show"])
    finally:
        get_settings.cache_clear()

    assert "chimera approve <id> --show" in listed.output
    assert shown.exit_code == 0, shown.output
    assert "title: [x] T" in shown.output and "TAIL" in shown.output
    assert "\x1b" not in shown.output and "\\x1b]52;c;aGk=\\x07" in shown.output
    assert both.exit_code == 1 and (directory / "abc123def456.ask.json").exists()
    assert not (directory / "abc123def456.answer.json").exists(), "--show answered nothing"
    assert missing.exit_code == 1


def test_a_no_pushes_nothing_and_says_not_to_report_it_done(repo: dict[str, Any]) -> None:
    out = _tool(repo, lambda *_a: False).run(title="T")

    assert is_refusal(out) and "Do not report it as done" in out
    assert _remote_ref(repo, "feature") == ""
    assert not [c for c in _gh_calls(repo) if c["argv"][:2] == ["pr", "create"]]


def test_an_unready_workspace_is_an_error_and_nobody_is_asked(repo: dict[str, Any]) -> None:
    _git(repo["work"], "checkout", "-q", "main")
    out = _tool(repo, lambda *_a: pytest.fail("asked about a push that cannot happen")).run(title="T")
    assert out.startswith("error: cannot open a pull request: the branch is the default branch")


@pytest.mark.parametrize("mode", ["allow", "ask", "", "nonsense"])
def test_no_approval_mode_turns_the_question_into_a_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """`allow` is the owner's standing yes to taint and policy reviews. It is read as `ask` here: a
    durable question is written, and with nobody answering it is refused."""
    import chimera.sandbox.confirm as confirm

    monkeypatch.setattr(confirm, "_no_human_surface", "test")
    approve = always_ask(tmp_path, mode=mode, wait_seconds=0.0)

    assert approve(pr_verdict(), "open_pull_request: feature -> main") is False
    (line,) = pending.history(tmp_path)
    assert line["decision"] == "review" and line["outcome"] == "timeout"
    assert line["rule"] == "open_pull_request" and line["tool"] == "open_pull_request"


def test_deny_refuses_without_asking(tmp_path: Path) -> None:
    assert always_ask(tmp_path, mode="deny")(pr_verdict(), "open_pull_request: x") is False
    assert pending.history(tmp_path) == []


def test_a_surfaces_own_modal_is_asked_and_its_answer_is_the_answer(tmp_path: Path) -> None:
    asked: list[tuple[str, str]] = []
    approve = always_ask(None, mode="allow", ask_with=lambda a, r: asked.append((a, r)) or False)
    assert approve(pr_verdict(), "open_pull_request: x") is False
    assert asked and asked[0][0] == "open_pull_request: x"


def test_with_no_way_to_ask_it_is_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    import chimera.sandbox.confirm as confirm

    monkeypatch.setattr(confirm, "_no_human_surface", "test")
    assert always_ask(None, mode="allow")(pr_verdict(), "open_pull_request: x") is False


def test_the_default_approver_cron_and_the_bots_get_is_the_durable_question_even_under_allow(
    repo: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chimera.sandbox.confirm as confirm

    monkeypatch.setattr(confirm, "_no_human_surface", "test")
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_APPROVAL_MODE", "allow")
    get_settings.cache_clear()
    asked: list[dict[str, Any]] = []

    def durable(home: Any, action: str, reason: str, **kw: Any) -> bool:
        asked.append({"action": action, "reason": reason, **kw})
        return False

    monkeypatch.setattr(pending, "ask_durably", durable)
    try:
        out = OpenPullRequestTool(repo["work"], gh=repo["gh"]).run(title="T")
    finally:
        get_settings.cache_clear()

    assert is_refusal(out)
    assert len(asked) == 1 and asked[0]["decision"] == "review"
    assert asked[0]["action"].startswith("open_pull_request: feature -> main")
    assert _remote_ref(repo, "feature") == ""


def pr_verdict() -> Any:
    from chimera.governance.policy import Verdict
    from chimera.tools.pull_request import REASON, RULE

    return Verdict(Decision.REVIEW, REASON, RULE)


# ------------------------------------------------------------------ the surfaces


def test_the_tool_exists_only_when_the_owner_switched_it_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.tools import default_registry

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    monkeypatch.delenv("CHIMERA_PULL_REQUESTS", raising=False)
    get_settings.cache_clear()
    try:
        assert "open_pull_request" not in default_registry(tmp_path).names()
        monkeypatch.setenv("CHIMERA_PULL_REQUESTS", "true")
        get_settings.cache_clear()
        assert isinstance(default_registry(tmp_path).get("open_pull_request"), OpenPullRequestTool)
    finally:
        get_settings.cache_clear()


def _code_registry(
    repo: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sink: Any, **env: str
) -> tuple[Any, Settings]:
    from chimera.api.code_api import CodeSeams, assemble_registry
    from chimera.api.posture import Posture
    from chimera.providers.gateway import LLMGateway

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_PULL_REQUESTS", "true")
    monkeypatch.setattr(pr, "gh_command", lambda: list(repo["gh"]))
    get_settings.cache_clear()
    settings = Settings(
        CHIMERA_HOME=str(tmp_path / "home"),  # type: ignore[arg-type]
        CHIMERA_APPROVAL_WAIT="5",  # type: ignore[arg-type]
        **env,  # type: ignore[arg-type]
    )
    # The most open posture the screen offers: shell in the workspace, and never stop to ask.
    seams = CodeSeams(posture=Posture(reach="workspace_shell", approval="never"))
    try:
        registry, _ = assemble_registry(
            seams, repo["work"], settings, LLMGateway(), steps=4, surface="api:turn",
            approval_sink=sink,
        )
    finally:
        get_settings.cache_clear()
    return registry, settings


@pytest.mark.parametrize("governance", ["off", "observe", "enforce"])
def test_on_the_code_screen_it_asks_with_a_card_under_the_most_open_settings(
    repo: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, governance: str
) -> None:
    sink = ApprovalAnnouncer()
    registry, settings = _code_registry(
        repo, tmp_path, monkeypatch, sink,
        CHIMERA_APPROVAL_MODE="allow", CHIMERA_GOVERNANCE=governance,
    )
    cards: list[Any] = []

    def no(question: Any) -> None:
        cards.append(question)
        assert pending.answer(settings.home, question.id, False)

    sink.emit = no
    out = str(registry.get("open_pull_request").run(title="T", body="b"))

    assert len(cards) == 1 and cards[0].decision == "review"
    assert cards[0].action.startswith("open_pull_request: feature -> main")
    assert is_refusal(out)
    assert _remote_ref(repo, "feature") == ""


def test_a_yes_on_the_card_is_not_remembered_for_the_next_pull_request(
    repo: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink = ApprovalAnnouncer()
    registry, settings = _code_registry(repo, tmp_path, monkeypatch, sink, CHIMERA_APPROVAL_MODE="allow")
    answers = iter([True, False])
    cards: list[Any] = []

    def answer(question: Any) -> None:
        cards.append(question)
        assert pending.answer(settings.home, question.id, next(answers))

    sink.emit = answer
    first = str(registry.get("open_pull_request").run(title="First", body=""))
    _commit(repo["work"], "second.py", "y = 2\n")
    second = str(registry.get("open_pull_request").run(title="Second", body=""))

    assert first.startswith("Opened pull request"), first
    assert len(cards) == 2, "the second pull request was asked about too"
    assert is_refusal(second)


# ------------------------------------------------------------------ the switch, the button, the bridge


def test_the_switch_is_off_by_default_readable_and_refuses_junk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.api.config_api import APPLIES_WHEN, is_editable, patch_config, read_config
    from chimera.api.schemas import AutonomyCfgOut

    monkeypatch.delenv("CHIMERA_PULL_REQUESTS", raising=False)
    assert Settings(CHIMERA_HOME=str(tmp_path)).pull_requests is False  # type: ignore[arg-type]
    assert read_config(Settings(CHIMERA_HOME=str(tmp_path)))["autonomy"]["pull_requests"] is False  # type: ignore[arg-type]
    assert AutonomyCfgOut().pull_requests is False, "a server without the field reads as off"
    assert is_editable("CHIMERA_PULL_REQUESTS")
    assert APPLIES_WHEN["CHIMERA_PULL_REQUESTS"] == "next_conversation"
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="CHIMERA_PULL_REQUESTS"):
        patch_config({"CHIMERA_PULL_REQUESTS": "maybe"}, env_path=env)
    assert env.read_text(encoding="utf-8") == ""


def test_only_the_owner_writes_the_switch_and_the_bridge_reaches_no_pull_request_route() -> None:
    from chimera.api.bridge_routes import OWNER_ONLY_SETTINGS, ROUTES

    assert "CHIMERA_PULL_REQUESTS" in OWNER_ONLY_SETTINGS
    assert not any("pull-request" in route.path for route in ROUTES.values())


def test_a_bridge_with_full_control_cannot_switch_it_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_every_way_into_this_machine_is_on_one_card import _build

    client = _build(
        tmp_path,
        monkeypatch,
        frozen=False,
        CHIMERA_DESKTOP_BRIDGE="true",
        CHIMERA_DESKTOP_BRIDGE_FULL="true",
        CHIMERA_PULL_REQUESTS="false",
    )
    app: Any = client.app
    bearer = {"Authorization": f"Bearer {app.state.desktop_bridge.token}"}
    refused = client.post(
        "/api/bridge/call",
        json={"route": "settings.edit", "body": {"CHIMERA_PULL_REQUESTS": "true"}},
        headers=bearer,
    )
    assert refused.status_code == 403
    get_settings.cache_clear()
    assert get_settings().pull_requests is False


def test_the_git_panel_sees_what_would_be_pushed_and_opens_only_the_commit_it_saw(
    repo: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_every_way_into_this_machine_is_on_one_card import _build

    monkeypatch.setattr(pr, "gh_command", lambda: list(repo["gh"]))
    client = _build(tmp_path, monkeypatch)
    ws = str(repo["work"])

    preview = client.get("/api/git/pull-request", params={"workspace": ws}).json()
    assert preview["ready"] is True and preview["head"] == repo["head"]
    assert (preview["branch"], preview["base"], preview["ahead"]) == ("feature", "main", 1)

    stale = client.post(
        "/api/git/pull-request",
        json={"workspace": ws, "title": "T", "head": "0" * 40},
    ).json()
    assert stale["ok"] is False and _remote_ref(repo, "feature") == ""
    assert client.post(
        "/api/git/pull-request", json={"workspace": ws, "title": "T", "head": "short"}
    ).status_code == 422

    opened = client.post(
        "/api/git/pull-request",
        json={"workspace": ws, "title": "Add feature", "body": "b", "head": repo["head"]},
    ).json()
    assert opened["ok"] is True and opened["url"].endswith("/pull/7")
    assert _remote_ref(repo, "feature") == repo["head"]

    elsewhere = tmp_path / "plain"
    elsewhere.mkdir()
    assert client.get("/api/git/pull-request", params={"workspace": str(elsewhere)}).json()[
        "reason"
    ] == "not_repo"


# ------------------------------------------------------------------ where it lands, and what runs


def _bare_copy(repo: dict[str, Any], tmp_path: Path, name: str) -> Path:
    """Another bare repository with origin's history: somewhere a push could be sent instead."""
    target = tmp_path / name
    _git(tmp_path, "clone", "-q", "--bare", str(repo["origin"]), str(target))
    return target


def _branch_in(bare: Path, branch: str) -> str:
    out = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=bare, capture_output=True, text=True, encoding="utf-8",
    )
    return out.stdout.strip()


@pytest.mark.parametrize("how", ["pushurl", "pushInsteadOf"])
def test_a_push_url_that_is_another_repository_is_refused_and_named(
    repo: dict[str, Any], tmp_path: Path, how: str
) -> None:
    """The card used to show origin's FETCH url while `git push origin` went to its PUSH url:
    measured, the card said `../legit.git` and the branch appeared only in `../evil.git`."""
    evil = _bare_copy(repo, tmp_path, "evil.git")
    if how == "pushurl":
        _git(repo["work"], "config", "remote.origin.pushurl", str(evil))
    else:
        _git(repo["work"], "config", f"url.{evil}.pushInsteadOf", str(repo["origin"]))

    state = pr.readiness(repo["work"], gh=repo["gh"])
    out = pr.open_pull_request(repo["work"], title="T", body="", expect_head=repo["head"], gh=repo["gh"])

    assert state.reason == "push_elsewhere"
    assert state.remote == str(evil), "what is shown is where the push would have gone"
    assert not out["ok"] and "different repository" in out["error"]
    assert _branch_in(evil, "feature") == "" and _remote_ref(repo, "feature") == ""
    assert not [c for c in _gh_calls(repo) if c["argv"][:2] == ["pr", "create"]]


def test_the_same_repository_written_two_ways_is_one_destination(repo: dict[str, Any]) -> None:
    _git(repo["work"], "remote", "set-url", "origin", "https://github.com/o/r.git")
    _git(repo["work"], "config", "remote.origin.pushurl", "git@github.com:o/r.git")
    assert pr._push_url(repo["work"], "https://github.com/o/r.git") == ("git@github.com:o/r.git", True)
    _git(repo["work"], "config", "--add", "remote.origin.pushurl", "git@github.com:o/r.git")
    assert pr._push_url(repo["work"], "https://github.com/o/r.git")[1] is False, (
        "two push URLs: `git push origin` would publish to both"
    )


def test_a_destination_that_changed_while_the_question_waited_is_refused(
    repo: dict[str, Any], tmp_path: Path
) -> None:
    """The push goes to the URL the card showed, compared again after the yes: a question waits up
    to the approval timeout, and a run with a shell can rewrite `.git/config` meanwhile."""
    other = _bare_copy(repo, tmp_path, "other.git")
    shown: list[str] = []

    def yes_after_a_change(_verdict: Any, action: str) -> bool:
        shown.append(action)
        _git(repo["work"], "remote", "set-url", "origin", str(other))
        return True

    out = _tool(repo, yes_after_a_change).run(title="T", body="b")

    assert out.startswith("error:") and "push URL changed" in out
    assert str(repo["origin"]) in shown[0].splitlines()[0]
    assert _branch_in(other, "feature") == "" and _remote_ref(repo, "feature") == ""


def test_a_branch_that_already_exists_on_origin_is_named_as_updated_and_pinned(
    repo: dict[str, Any], tmp_path: Path
) -> None:
    """A non-force push to an existing branch updates it before any pull request exists. The card
    says so, with the commit it is at; a branch that moved during the question is not updated."""
    main = _git(repo["work"], "rev-parse", "main")
    _git(repo["work"], "push", "-q", "origin", f"{main}:refs/heads/feature")
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", "-b", "feature", str(repo["origin"]), str(other))
    cards: list[str] = []

    def yes_after_someone_pushed(_verdict: Any, action: str) -> bool:
        cards.append(action)
        _commit(other, "theirs.txt", "x\n")
        _git(other, "push", "-q", "origin", "feature")
        return True

    state = pr.readiness(repo["work"], gh=repo["gh"])
    out = _tool(repo, yes_after_someone_pushed).run(title="T", body="b")

    assert state.ready and state.remote_head == main
    assert f"UPDATES the branch feature that already exists on origin: {main[:12]} -> " in cards[0]
    assert out.startswith("error:") and "on origin changed after it was reviewed" in out
    assert _remote_ref(repo, "feature") == _git(other, "rev-parse", "HEAD"), "theirs, untouched"


def test_a_new_branch_has_no_update_line_and_the_shown_head_is_empty(repo: dict[str, Any]) -> None:
    cards: list[str] = []
    _tool(repo, lambda _v, action: cards.append(action) or False).run(title="T", body="b")
    assert pr.readiness(repo["work"], gh=repo["gh"]).remote_head == ""
    assert "UPDATES" not in cards[0]


@pytest.mark.parametrize("name", ["main", "master"])
def test_main_and_master_are_never_pushed_whatever_the_default_is(
    repo: dict[str, Any], name: str
) -> None:
    """A git-flow repository's default is `develop` and its production branch is `main`: the agent
    on `main`, proposing into `develop`, would push straight to production."""
    _git(repo["work"], "push", "-q", "origin", "main:refs/heads/develop")
    _git(repo["origin"], "symbolic-ref", "HEAD", "refs/heads/develop")
    _git(repo["work"], "remote", "set-head", "origin", "develop")
    _git(repo["work"], "checkout", "-q", "-B", name)
    _commit(repo["work"], "prod.txt", "x\n")

    assert pr.readiness(repo["work"], gh=repo["gh"]).reason == "default_branch"


def _hook(path: Path, marker: Path) -> None:
    path.write_text(f"#!/bin/sh\necho ran > '{marker.as_posix()}'\n", encoding="utf-8")
    path.chmod(0o755)


def test_no_repository_hook_runs_when_the_pull_request_is_opened(
    repo: dict[str, Any], tmp_path: Path
) -> None:
    """`git push` runs `pre-push` and `reference-transaction`, and `git status` runs an fsmonitor:
    all three are files or settings a run with a shell in the workspace can write."""
    hooks = repo["work"] / ".git" / "hooks"
    _hook(hooks / "pre-push", tmp_path / "pre_push_ran")
    _hook(hooks / "reference-transaction", tmp_path / "ref_tx_ran")
    monitor = tmp_path / "monitor.sh"
    _hook(monitor, tmp_path / "fsmonitor_ran")
    _git(repo["work"], "config", "core.fsmonitor", monitor.as_posix())

    out = pr.open_pull_request(
        repo["work"], title="T", body="", expect_head=repo["head"], gh=repo["gh"]
    )

    assert out["ok"], out
    assert _remote_ref(repo, "feature") == repo["head"]
    assert not (tmp_path / "pre_push_ran").exists()
    assert not (tmp_path / "ref_tx_ran").exists()
    assert not (tmp_path / "fsmonitor_ran").exists()


def test_the_card_holds_the_whole_description(repo: dict[str, Any]) -> None:
    """The card used to hold the first 600 characters and a length: a description could be 600
    harmless characters and then whatever the run had read, approved unseen and published."""
    body = "Harmless summary. " * 40 + "\nAWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    cards: list[str] = []

    _tool(repo, lambda _v, action: cards.append(action) or False).run(title="T", body=body)

    assert len(body) > 600
    assert body in cards[0]


def _exe(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / ("gh.exe" if sys.platform == "win32" else "gh")
    target.write_bytes(b"#!/bin/sh\nexit 0\n")
    target.chmod(0o755)
    return target


@pytest.mark.parametrize("dot_on_path", [False, True])
def test_a_gh_in_the_current_directory_is_never_the_one_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dot_on_path: bool
) -> None:
    """Windows' lookup tries the current directory before PATH (Python 3.11 always, 3.12 unless
    NoDefaultCurrentDirectoryInExePath is set), and the chat and TUI run with the workspace as their
    current directory: a gh.exe written there would run in `gh auth status`, before any question.
    A relative `.` on PATH is the same door on every platform."""
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)
    planted = _exe(tmp_path / "workspace")
    real = _exe(tmp_path / "bin")
    entries = [str(real.parent)]
    if dot_on_path:
        entries.insert(0, ".")
    monkeypatch.setenv("PATH", os.pathsep.join(entries))
    monkeypatch.chdir(planted.parent)

    found = pr.gh_command()
    assert found is not None
    assert [os.path.normcase(p) for p in found] == [os.path.normcase(str(real))]


def test_a_gh_found_only_in_the_current_directory_is_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)
    planted = _exe(tmp_path / "workspace")
    (tmp_path / "empty").mkdir()
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.chdir(planted.parent)

    assert pr.gh_command() is None


ALIAS_GH = '''
import sys
argv = sys.argv[1:]
if argv[:2] == ["auth", "status"]:
    sys.exit(1 if any(arg.startswith("--hostname=") and arg != "--hostname=github.com" for arg in argv) else 0)
sys.exit(2)
'''


def test_an_ssh_alias_gh_does_not_know_is_refused_before_anything_is_pushed(
    repo: dict[str, Any], tmp_path: Path
) -> None:
    """`git@github-work:o/r.git` (a Host alias in ~/.ssh/config) pushes fine; `gh pr create
    --repo=github-work/o/r` then fails, leaving a published branch with no pull request."""
    fake = tmp_path / "alias_gh.py"
    fake.write_text(ALIAS_GH, encoding="utf-8")
    gh = [sys.executable, str(fake)]
    _git(repo["work"], "remote", "set-url", "origin", "git@github-work:o/r.git")

    state = pr.readiness(repo["work"], gh=gh)
    out = pr.open_pull_request(repo["work"], title="T", body="", expect_head=repo["head"], gh=gh)

    assert state.reason == "gh_unknown_host" and not state.gh_signed_in
    assert not out["ok"] and "SSH alias" in out["error"]
    assert pr._gh_signed_in(gh, repo["work"], "git@github.com:o/r.git") == ""


def test_the_taint_paragraph_of_security_md_names_every_outbound_channel() -> None:
    """SECURITY.md lists the outbound channels narrowing gates; a channel added in code and not in
    the list leaves an auditor reading an incomplete one."""
    from chimera.governance.ledger import WRITE_TOOLS
    from chimera.governance.ledger_tool import DANGEROUS_WHEN_TAINTED

    text = (Path(__file__).resolve().parents[1] / "SECURITY.md").read_text(encoding="utf-8")
    head = "**and every outbound channel** ("
    start = text.index(head) + len(head)
    listed = text[start : text.index(")", start)]
    # Execution and the workspace writes are the paragraph's other two kinds, not channels. The
    # writes are the ledger's own set, so a write tool added there (`create_document`) is not
    # mistaken for an unlisted channel; anything else still has to be named in SECURITY.md.
    local = {"run_shell", "execute_code", "code_interpreter"} | WRITE_TOOLS
    missing = sorted(t for t in DANGEROUS_WHEN_TAINTED - local if f"`{t}`" not in listed)
    assert not missing, missing
