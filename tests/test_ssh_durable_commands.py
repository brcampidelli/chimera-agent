"""SSH part B, piece 2: a command that survives a dropped connection (``chimera/sandbox/ssh.py``).

What these pin:

* **The run id is the command's identity, so reconnecting is safe.** A connection that drops while the
  client follows a run is reconnected and read on from the saved offsets; the command is NOT started
  again, even when it was the launch's own reply that was lost.
* **The agent's command is never re-quoted.** It travels on stdin as a script file; every argv the
  remote login shell is given is the fixed launcher plus an operation, a hex id and integers.
* **A timeout or a cancellation kills the remote process GROUP** with the fixed stop operation, and
  when that cannot be sent the result names the run, because the command is still running.
* **Cleanup**: the run folder is removed after the result is read; a run nobody finished is in the
  journal, and the next start says where its output is.
* **The launcher itself**, when a POSIX ``sh`` is on this machine, run through the same code with
  ssh replaced by a local ``sh -c``: exit codes, stdout/stderr apart, a missing folder, the sweep.

The fake remote is ``_Ssh`` of ``test_ssh_environment.py``; no test here needs ssh.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from chimera.sandbox import ssh as ssh_module
from chimera.sandbox.ssh import SshSandbox
from tests.test_ssh_environment import CONNECT_FAIL, _Proc, _Ssh

TRICKY = (
    "echo \"it's $(whoami)\" && printf '%s\\n' 'a b' \"c d\" ; echo $HOME `date` | tr a-z A-Z\n"
    "if true; then echo 'multi\nline'; fi # trailing comment"
)


@pytest.fixture(autouse=True)
def _journal_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh_module, "_journal_dir", lambda: tmp_path / "ssh_runs")


def _journal(tmp_path: Path) -> list[Path]:
    return sorted((tmp_path / "ssh_runs").glob("*.json"))


# -- the command is never re-quoted -----------------------------------------------------------


def test_the_command_travels_on_stdin_and_never_on_the_remote_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"ok\n", b"")])
    result = SshSandbox("web1", "/srv/my app").run(TRICKY, timeout=30)
    assert result.exit_code == 0 and result.stdout == "ok\n"
    (call,) = fake.calls
    # byte for byte: the folder quoted, then the command exactly as the agent wrote it
    assert call["stdin"] == f"cd '/srv/my app' || exit 1; {TRICKY}\n".encode()
    for remote in fake.remotes:
        # what the login shell reads is ONE quoted word (the launcher) and plain words around it
        words = shlex.split(remote)
        assert words[:3] == ["sh", "-c", ssh_module.LAUNCHER] and words[3] == "chimera-run"
        assert remote == f"sh -c {shlex.quote(ssh_module.LAUNCHER)} " + " ".join(words[3:])
        for fragment in ("$(whoami)", "printf", "multi", "my app", "trailing"):
            assert fragment not in remote
        assert all(w.isdigit() or w.isalpha() or w == "chimera-run" or len(w) == 16 for w in words[3:])


def test_the_launcher_is_fixed_text_that_makes_a_private_folder_and_a_new_session() -> None:
    script = ssh_module.LAUNCHER
    assert "umask 077" in script and "chmod 700" in script
    assert "setsid nohup" in script and "nohup sh -c" in script  # the fallback without setsid
    assert "-mtime +3" in script  # the sweep of old runs
    assert "{" in script and "@" not in script  # every template field was filled
    assert ssh_module.remote_op("poll", "0123456789abcdef", 3, 10, 20).endswith("chimera-run poll 0123456789abcdef 3 10 20")
    with pytest.raises(ssh_module.SshPathError):
        ssh_module.remote_op("poll", "x; rm -rf ~", 1)  # an id that is not hex never reaches a shell


def test_an_oversized_command_is_not_run(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"", b"")])
    result = SshSandbox("web1", "/srv").run("echo " + "x" * ssh_module.MAX_COMMAND_BYTES)
    assert result.exit_code == 1 and "not run" in result.stderr and fake.ops == []


# -- reconnecting, and never starting twice ---------------------------------------------------


def test_a_connection_dropped_mid_follow_reconnects_and_resumes_from_the_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = b"line one\nline two\nline three\n"
    fake = _Ssh(monkeypatch, [_Proc(0, out, b"warn\n", step=9)])
    fake.fail_at[2] = CONNECT_FAIL  # the first poll after the launch loses its connection
    fake.fail_at[3] = CONNECT_FAIL  # and so does the reconnection
    result = SshSandbox("web1", "/srv").run("make", timeout=600)
    assert result.exit_code == 0 and result.stdout == out.decode() and result.stderr == "warn\n"
    assert len(fake.calls) == 1  # started ONCE
    assert [op for op, _, _ in fake.ops].count("launch") == 1
    polls = [numbers for op, _, numbers in fake.ops if op == "poll"]
    assert polls[0][1:] == [9, 0]  # after the launch's 9 bytes, the first poll asks from offset 9
    assert polls[1][1:] == [9, 0] and polls[2][1:] == [9, 0]  # the retries ask from the SAME offset
    assert fake.waits[:2] == list(ssh_module.RECONNECT_BACKOFF[:2])  # backoff, growing


def test_a_launch_whose_reply_was_lost_is_repeated_and_the_command_still_runs_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"deployed\n", b"")])
    fake.lose_reply.add(1)  # the remote started the run; the answer never came back
    result = SshSandbox("web1", "/srv").run("make deploy", timeout=60)
    assert result.exit_code == 0 and result.stdout == "deployed\n"
    launches = [(op, run_id) for op, run_id, _ in fake.ops if op == "launch"]
    assert len(launches) == 2 and launches[0][1] == launches[1][1]  # the same id, so one run
    assert len(fake.calls) == 1


def test_a_link_that_keeps_failing_is_given_up_on_and_the_run_is_named(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"long output", b"", hang=True)])
    fake.fail_calls(2, 99, CONNECT_FAIL)  # launched fine, then the link never comes back
    result = SshSandbox("web1", "/srv").run("make", timeout=10_000)
    run_id = fake.ops[0][1]
    assert result.exit_code == 255
    assert f"run {run_id}" in result.stderr and "keeps running" in result.stderr
    assert "chimera/runs" in result.stderr  # where to read it
    assert len(fake.waits) == len(ssh_module.RECONNECT_BACKOFF)  # bounded
    assert fake.stopped == [] or fake.stopped == [run_id]  # a kill is only ever for THIS run
    assert fake.cleaned == [] and [p.stem for p in _journal(tmp_path)] == [run_id]  # nothing removed


def test_failures_that_are_not_consecutive_never_exhaust_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"x" * 40, b"", step=4)])
    for number in range(2, 60, 2):
        fake.fail_at[number] = CONNECT_FAIL  # every other call fails, for the whole run
    result = SshSandbox("web1", "/srv").run("make", timeout=100_000)
    assert result.exit_code == 0 and result.stdout == "x" * 40 and len(fake.calls) == 1


# -- timeout and cancellation kill the remote group -------------------------------------------


def test_a_timeout_stops_the_remote_group_by_run_id_and_returns_what_it_had(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"partial\n", b"", hang=True)])
    result = SshSandbox("web1", "/srv").run("sleep 999", timeout=7)
    run_id = fake.ops[0][1]
    assert result.timed_out and result.exit_code == 124
    assert fake.stopped == [run_id]  # the fixed stop script, with the id
    assert [op for op, _, _ in fake.ops].count("launch") == 1
    assert result.stdout == "partial\n" and "timed out after 7s" in result.stderr
    assert fake.cleaned == [run_id] and _journal(tmp_path) == []


def test_a_timeout_that_cannot_reach_the_host_says_the_command_is_still_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"", b"", hang=True)])
    fake.fail_calls(2, 99, CONNECT_FAIL)
    box = SshSandbox("web1", "/srv")
    # the poll's reconnects run out of time (timeout 4s, backoff longer), then the stop cannot connect
    result = box.run("sleep 999", timeout=4)
    run_id = fake.ops[0][1]
    assert result.timed_out and result.exit_code == 124
    assert "could not be stopped" in result.stderr and f"run {run_id}" in result.stderr
    assert fake.cleaned == [] and [p.stem for p in _journal(tmp_path)] == [run_id]


def test_a_local_cancel_stops_the_remote_group_too(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"so far\n", b"", hang=True)])
    box = SshSandbox("web1", "/srv")
    polls = {"n": 0}

    def cancelled() -> bool:
        polls["n"] += 1
        return polls["n"] > 2

    box.bind_cancel(cancelled)
    result = box.run("tail -f log", timeout=600)
    assert result.exit_code == 130 and "cancelled" in result.stderr and result.stdout == "so far\n"
    assert fake.stopped == [fake.ops[0][1]]


def test_an_interrupt_while_following_stops_the_remote_group_and_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"", b"", hang=True)])
    fake.cancel_at[3] = KeyboardInterrupt()  # the turn is torn down in the middle of a poll
    with pytest.raises(KeyboardInterrupt):
        SshSandbox("web1", "/srv").run("sleep 999", timeout=600)
    assert fake.stopped == [fake.ops[0][1]]


# -- cleanup, the journal and the interrupted note --------------------------------------------


def test_the_run_folder_and_the_journal_entry_are_removed_once_the_result_is_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _Ssh(monkeypatch, [_Proc(3, b"out", b"err")])
    result = SshSandbox("web1", "/srv").run("false")
    run_id = fake.ops[0][1]
    assert (result.exit_code, result.stdout, result.stderr) == (3, "out", "err")
    assert fake.cleaned == [run_id] and fake.runs == {} and _journal(tmp_path) == []


def test_a_run_nobody_finished_is_announced_once_with_its_host_and_id(tmp_path: Path) -> None:
    ssh_module.journal_add("web1", "0123456789abcdef", "/srv")
    ssh_module.journal_add("db1", "fedcba9876543210", "/data")
    (tmp_path / "ssh_runs" / "junk.json").write_text("not json", encoding="utf-8")
    stale = {"host": "web1", "run_id": "aaaaaaaaaaaaaaaa", "started": time.time() - 9 * 86400}
    (tmp_path / "ssh_runs" / "aaaaaaaaaaaaaaaa.json").write_text(json.dumps(stale), encoding="utf-8")
    found = ssh_module.take_pending_runs()
    assert {e["run_id"] for e in found} == {"0123456789abcdef", "fedcba9876543210"}
    note = ssh_module.interrupted_note(found)
    assert "web1" in note and "0123456789abcdef" in note and "chimera/runs/0123456789abcdef/" in note
    assert ssh_module.take_pending_runs() == []  # each is announced once
    assert not (tmp_path / "ssh_runs" / "aaaaaaaaaaaaaaaa.json").exists()  # older than the sweep: dropped


def test_the_queue_notice_for_an_interrupted_message_carries_the_remote_run(tmp_path: Path) -> None:
    """The wiring in ``code_api``: the recovered-item notice appends the run note, only when needed."""
    source = Path(ssh_module.__file__).parents[1] / "api" / "code_api.py"
    text = source.read_text(encoding="utf-8")
    assert "ssh_runs.pop(interrupted.session_id" in text and "ssh_run_pending" in text


def test_read_run_reads_a_run_by_id_while_it_runs_and_after_it_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"abc", b"", hang=True)])
    box = SshSandbox("web1", "/srv")
    run_id = "0123456789abcdef"
    fake._exec(ssh_module.remote_op("launch", run_id, 0, 0, 0), b"cd /srv || exit 1; x\n", 1)
    live = box.read_run(run_id)
    assert live.exit_code == 0 and live.stdout == "abc" and "still running" in live.stderr
    fake.runs[run_id]["proc"].hang = False
    done = box.read_run(run_id, forget=True)
    assert done.exit_code == 0 and done.stdout == "abc" and "still running" not in done.stderr
    assert fake.cleaned == [run_id]
    assert "not on web1" in box.read_run(run_id).stderr
    assert box.read_run("not-an-id").exit_code == 1


# -- the launcher, for real (a local sh stands in for ssh) -------------------------------------

HAS_SH = shutil.which("sh") is not None


def _posix(path: Path) -> str:
    """``path`` as the local ``sh`` spells it: ``/c/Users/...`` under Git Bash on Windows."""
    text = path.as_posix()
    return f"/{text[0].lower()}{text[2:]}" if os.name == "nt" and text[1:3] == ":/" else text


def _local_sh(monkeypatch: pytest.MonkeyPatch, cache: Path) -> None:
    """Replace ssh with ``sh -c <remote string>`` on this machine: the real launcher, real files."""

    def run_here(_self: SshSandbox, remote: str, stdin: bytes | None, timeout: float) -> ssh_module._Call:
        done = subprocess.run(["sh", "-c", remote], input=stdin or b"", capture_output=True, timeout=timeout, check=False)
        return ssh_module._Call(code=done.returncode, out=done.stdout, err=done.stderr)

    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    monkeypatch.setattr(SshSandbox, "_exec", run_here)


@pytest.mark.skipif(not HAS_SH, reason="needs a POSIX sh")
def test_the_real_launcher_runs_the_command_as_typed_and_keeps_the_streams_apart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _local_sh(monkeypatch, tmp_path / "cache")
    workdir = _posix(tmp_path / "work dir")
    os.makedirs(workdir.replace("/c/", "C:/", 1) if os.name == "nt" else workdir)
    box = SshSandbox("web1", workdir)
    result = box.run("printf '%s|' \"a b\" $(echo c) && echo to-err >&2; pwd; exit 7", timeout=60)
    assert result.exit_code == 7
    assert result.stdout.startswith("a b|c|") and result.stdout.splitlines()[-1].endswith("work dir")
    assert result.stderr == "to-err\n"
    assert list((tmp_path / "cache" / "chimera" / "runs").iterdir()) == []  # removed after reading


@pytest.mark.skipif(not HAS_SH, reason="needs a POSIX sh")
def test_the_real_launcher_ends_a_command_whose_folder_is_missing_instead_of_running_it_elsewhere(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _local_sh(monkeypatch, tmp_path / "cache")
    marker = tmp_path / "ran-in-the-wrong-place"
    result = SshSandbox("web1", _posix(tmp_path / "missing")).run(f"touch {_posix(marker)}", timeout=60)
    assert result.exit_code == 1 and not marker.exists()


@pytest.mark.skipif(not HAS_SH, reason="needs a POSIX sh")
def test_the_real_launcher_survives_a_dropped_poll_without_starting_the_command_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _local_sh(monkeypatch, tmp_path / "cache")
    real = SshSandbox._exec
    counter = tmp_path / "starts"
    state = {"n": 0}

    def flaky(self: SshSandbox, remote: str, stdin: bytes | None, timeout: float) -> ssh_module._Call | None:
        state["n"] += 1
        if state["n"] == 2:  # the first poll after the launch
            return ssh_module._Call(code=255, client_log=CONNECT_FAIL.decode())
        return real(self, remote, stdin, timeout)

    monkeypatch.setattr(SshSandbox, "_exec", flaky)
    monkeypatch.setattr(ssh_module, "_sleep", lambda _s: None)
    monkeypatch.setattr(ssh_module, "LAUNCH_WAIT_SECONDS", 0)
    result = SshSandbox("web1", _posix(tmp_path)).run(f"echo x >> {_posix(counter)}; sleep 1; echo done", timeout=60)
    assert result.exit_code == 0 and result.stdout == "done\n"
    assert counter.read_text(encoding="utf-8") == "x\n"  # one start


@pytest.mark.skipif(not HAS_SH, reason="needs a POSIX sh")
def test_the_real_launcher_sweeps_old_finished_runs_when_the_next_one_starts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    probe = subprocess.run(["find", ".", "-maxdepth", "0", "-mtime", "+3"], capture_output=True, check=False)
    if probe.returncode != 0:
        pytest.skip("this find is not the POSIX one")
    cache = tmp_path / "cache"
    _local_sh(monkeypatch, cache)
    runs = cache / "chimera" / "runs"
    old, fresh = runs / "1111111111111111", runs / "2222222222222222"
    for folder in (old, fresh):
        folder.mkdir(parents=True)
        (folder / "out").write_text("x", encoding="utf-8")
        (folder / "code").write_text("0", encoding="utf-8")
    long_ago = time.time() - 10 * 86400
    os.utime(old / "code", (long_ago, long_ago))
    assert SshSandbox("web1", _posix(tmp_path)).run("true", timeout=60).exit_code == 0
    assert not old.exists() and fresh.exists()


def _dies_within(pid: int, seconds: float) -> bool:
    """Whether ``pid`` is gone (or a zombie nobody has reaped yet, which is dead) within ``seconds``."""
    end = time.time() + seconds
    while time.time() < end:
        try:
            state = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()[0]
        except (OSError, IndexError):
            return True
        if state in ("Z", "X"):
            return True
        time.sleep(0.1)
    return False


@pytest.mark.skipif(
    os.name != "posix" or shutil.which("setsid") is None or shutil.which("sh") is None,
    reason="the process-group kill needs setsid",
)
def test_the_real_stop_kills_the_whole_group_not_just_the_shell(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _local_sh(monkeypatch, tmp_path / "cache")
    monkeypatch.setattr(ssh_module, "LAUNCH_WAIT_SECONDS", 1)
    pidfile = tmp_path / "grandchild.pid"
    box = SshSandbox("web1", _posix(tmp_path))
    result = box.run(f"sleep 300 & echo $! > {_posix(pidfile)}; wait", timeout=3)
    assert result.timed_out
    pid = int(pidfile.read_text(encoding="utf-8").strip())
    assert _dies_within(pid, 5)  # the grandchild died with the group


# -- the second review: what each fix pins ------------------------------------------------------


def test_the_launcher_never_relaunches_a_run_that_has_its_marker_and_umask_is_not_leaked() -> None:
    script = ssh_module.LAUNCHER
    marker = script.index('if [ -f "$d/mode" ]; then frame; return 0; fi')
    assert marker < script.index('rm -rf "$d"') < script.index('(set -C; echo "$lmode" > "$d/mode")')
    assert '[ -O "$base" ]' in script  # the run folder must be this account's own
    assert 'umask "$3"' in script and "um=$(umask)" in script  # the user's command gets the caller's umask


@pytest.mark.skipif(not HAS_SH, reason="needs a POSIX sh")
def test_the_real_launcher_does_not_relaunch_a_run_whose_first_shell_has_not_written_its_pid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    _local_sh(monkeypatch, cache)
    run_id = "0123456789abcdef"
    folder = cache / "chimera" / "runs" / run_id
    folder.mkdir(parents=True)
    (folder / "cmd.sh").write_text("touch relaunched\n", encoding="utf-8")
    (folder / "out").write_text("first", encoding="utf-8")
    (folder / "mode").write_text("plain\n", encoding="utf-8")  # forked, but `pid` was never written
    call = SshSandbox("web1", "/srv")._exec(ssh_module.remote_op("launch", run_id, 0, 0, 0), b"echo second\n", 30)
    assert call is not None
    frame = ssh_module.parse_frame(call.out)
    assert frame is not None and frame.out == b"first"
    assert (folder / "cmd.sh").read_text(encoding="utf-8") == "touch relaunched\n"  # untouched


@pytest.mark.skipif(not HAS_SH, reason="needs a POSIX sh")
def test_the_real_launcher_recreates_a_folder_that_never_reached_its_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    _local_sh(monkeypatch, cache)
    run_id = "0123456789abcdef"
    folder = cache / "chimera" / "runs" / run_id
    folder.mkdir(parents=True)
    (folder / "cmd.sh").write_text("echo half-written", encoding="utf-8")
    (folder / "claim").write_bytes(f"old-1 {int(time.time()) - 1000}\n".encode())  # a dead attempt's
    call = SshSandbox("web1", "/srv")._exec(ssh_module.remote_op("launch", run_id, 5, 0, 0, 12), b"echo second\n", 30)
    assert call is not None
    frame = ssh_module.parse_frame(call.out)
    assert frame is not None and frame.out == b"second\n" and (folder / "mode").exists()


@pytest.mark.skipif(not HAS_SH, reason="needs a POSIX sh")
def test_the_users_command_runs_with_the_callers_umask_not_the_launchers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _local_sh(monkeypatch, tmp_path / "cache")
    wanted = subprocess.run(["sh", "-c", "umask"], capture_output=True, text=True, check=False).stdout
    result = SshSandbox("web1", _posix(tmp_path)).run("umask", timeout=60)
    assert result.stdout == wanted != "0077\n"


def test_a_stop_that_only_reached_the_wrapper_is_not_reported_as_a_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"partial\n", b"", hang=True)])
    fake.stop_reply = b"chimera-stopped partial\n"
    result = SshSandbox("web1", "/srv").run("sleep 999", timeout=5)
    run_id = fake.ops[0][1]
    assert result.timed_out and "could not be stopped" in result.stderr and f"run {run_id}" in result.stderr
    assert fake.cleaned == [] and [p.stem for p in _journal(tmp_path)] == [run_id]  # the folder stays


def test_a_host_removed_in_the_middle_of_a_run_stops_it_instead_of_following_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"so far\n", b"", hang=True)])
    box = SshSandbox("web1", "/srv")

    def recheck() -> None:
        if any(op == "launch" for op, _, _ in fake.ops):  # allowed at the start, removed after the launch
            raise ValueError("'web1' is not on your list of allowed SSH hosts")

    box.bind_recheck(recheck)
    result = box.run("sleep 999", timeout=600)
    run_id = fake.ops[0][1]
    assert result.exit_code == 1 and "stopped" in result.stderr and "allowed" in result.stderr
    assert fake.stopped == [run_id] and fake.cleaned == [run_id]  # stop and cleanup are not held back
    assert [op for op, _, _ in fake.ops].count("poll") == 0  # and no poll was sent for a removed host


def test_a_host_removed_while_reconnecting_is_not_reconnected_to(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"", b"", hang=True)])
    fake.fail_calls(1, 99, CONNECT_FAIL)
    box = SshSandbox("web1", "/srv")
    calls = {"n": 0}

    def recheck() -> None:
        calls["n"] += 1
        if calls["n"] > 1:  # allowed at the command's start, removed before the first retry
            raise ValueError("'web1' is not on your list of allowed SSH hosts")

    box.bind_recheck(recheck)
    result = box.run("make", timeout=600)
    assert result.exit_code == 1 and "allowed" in result.stderr
    assert [op for op, _, _ in fake.ops].count("launch") == 1  # no retry of the launch


def test_the_stderr_of_the_ssh_process_is_cleaned_even_with_a_client_log() -> None:
    hop = "ssh: connect to host 10.0.0.5 port 2222: Connection refused\nIdentity file /home/x/.ssh/id_x type -1\n"
    seen = ssh_module.explained(
        ssh_module.SandboxResult(exit_code=255, stderr=hop), "web1", "kex_exchange_identification: Connection closed"
    )
    for leaked in ("10.0.0.5", "2222", "/home/x", "id_x"):
        assert leaked not in seen.stderr


def test_a_reply_cut_inside_its_payload_cannot_forge_an_error_with_the_commands_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"fine\n", b"")])
    # the poll header announces 60 bytes; the connection ends after output that says "chimera-error ..."
    fake.raw_at[2] = b"chimera-poll 0 0 60 0\nchimera-error pwned: not run\n"
    result = SshSandbox("web1", "/srv").run("make", timeout=600)
    assert result.exit_code == 0 and result.stdout == "fine\n" and "pwned" not in result.stderr


def test_the_launchers_own_error_before_any_header_is_still_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _Ssh(monkeypatch, [_Proc(0, b"", b"")])
    monkeypatch.setattr(
        SshSandbox, "_exec",
        lambda _s, _r, _i, _t: ssh_module._Call(code=0, out=b"chimera-error cannot create the run folder\n"),
    )
    result = SshSandbox("web1", "/srv").run("make")
    assert result.exit_code == 1 and "cannot create the run folder" in result.stderr


def test_a_csh_login_shell_gets_a_message_that_names_the_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    _Ssh(monkeypatch, [_Proc(0, b"", b"")])
    monkeypatch.setattr(
        SshSandbox, "_exec", lambda _s, _r, _i, _t: ssh_module._Call(code=1, err=b"Unmatched '.\n"),
    )
    result = SshSandbox("web1", "/srv").run("make")
    assert result.exit_code == 1 and "csh" in result.stderr and "sh-family" in result.stderr


def test_read_run_reads_every_chunk_to_the_end(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"0123456789", b"e", step=3)])
    box = SshSandbox("web1", "/srv")
    run_id = "0123456789abcdef"
    fake._exec(ssh_module.remote_op("launch", run_id, 0, 0, 0), b"x\n", 1)
    done = box.read_run(run_id)
    assert done.stdout == "0123456789" and done.stderr == "e"


def test_the_journal_is_private_and_stale_entries_are_purged_on_every_run(tmp_path: Path) -> None:
    folder = tmp_path / "ssh_runs"
    folder.mkdir()
    old = {"host": "web1", "run_id": "aaaaaaaaaaaaaaaa", "started": time.time() - 9 * 86400}
    (folder / "aaaaaaaaaaaaaaaa.json").write_text(json.dumps(old), encoding="utf-8")
    ssh_module.journal_add("web1", "0123456789abcdef", "/srv")
    assert not (folder / "aaaaaaaaaaaaaaaa.json").exists()
    entry = folder / "0123456789abcdef.json"
    assert entry.exists()
    if os.name == "posix":
        assert entry.stat().st_mode & 0o077 == 0


def test_the_turns_stop_signal_is_bound_to_an_ssh_sandbox() -> None:
    text = (Path(ssh_module.__file__).parents[1] / "api" / "code_api.py").read_text(encoding="utf-8")
    assert "turn_sandbox.bind_cancel(stop_signal)" in text


# -- the third pass: claim marker, a truncated script, bounded reads, cancel, sessions ----------


def test_the_launcher_gives_each_attempt_its_own_token_and_file_and_sweeps_after_the_spawn() -> None:
    script = ssh_module.LAUNCHER
    assert script.index('mkdir "$d"') < script.index('echo "$tok $now" > "$d/claim"') < script.index('cat > "$tmp"')
    assert "cmd.$tok.tmp" in script  # a file no other attempt names
    # nothing is moved, truncated or started before the claim is re-read, and the take-over is exclusive
    assert script.index("if mine;") < script.index('ln "$tmp" "$d/cmd.sh"') < script.index("set -C")
    assert script.index("set -C") < script.index(': > "$d/out"')  # only the winner truncates the output
    assert script.index('echo $! > "$d/pid"') < script.index("  sweep\n  frame")  # sweep never delays the start
    assert "launch in progress" in script and "-ge 120" in script and "timeout 60 cat" in script
    release = script[script.index("release() {"):script.index("stop() {")]
    assert 'rm -rf "$d"' not in release  # an attempt that lost never removes the folder


def test_a_launch_sends_the_scripts_byte_count_so_a_prefix_can_be_told_from_the_whole(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"", b"")])
    SshSandbox("web1", "/srv").run("echo hi")
    (call,) = fake.calls
    launch = next(numbers for op, _, numbers in fake.ops if op == "launch")
    assert launch[-1] == len(call["stdin"])


def test_a_launch_that_is_still_settling_is_retried_later_not_reported_as_not_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"done\n", b"")])
    fake.raw_at[1] = b"chimera-error launch in progress\n"
    fake.raw_at[2] = b"chimera-error the command arrived incomplete and was not run\n"
    result = SshSandbox("web1", "/srv").run("make", timeout=600)
    assert result.exit_code == 0 and result.stdout == "done\n" and len(fake.calls) == 1
    assert fake.waits[:2] == list(ssh_module.RECONNECT_BACKOFF[:2])


def test_a_launch_that_stays_in_progress_ends_with_a_not_run_message(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"", b"")])
    for number in range(1, 30):
        fake.raw_at[number] = b"chimera-error launch in progress\n"
    result = SshSandbox("web1", "/srv").run("make", timeout=10_000)
    assert result.exit_code == 255 and "not run" in result.stderr and "launch in progress" in result.stderr
    assert fake.calls == []


@pytest.mark.skipif(not HAS_SH, reason="needs a POSIX sh")
def test_the_real_launcher_never_runs_a_script_that_arrived_short(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    _local_sh(monkeypatch, cache)
    marker = tmp_path / "ran"
    run_id = "0123456789abcdef"
    whole = f"touch {_posix(marker)}; echo rest-of-the-script\n".encode()
    prefix = whole[:12]  # the link dropped while the script streamed: cat saw EOF and exited 0
    call = SshSandbox("web1", "/srv")._exec(ssh_module.remote_op("launch", run_id, 0, 0, 0, len(whole)), prefix, 30)
    assert call is not None and b"arrived incomplete" in call.out
    assert not (cache / "chimera" / "runs" / run_id).exists() and not marker.exists()


@pytest.mark.skipif(not HAS_SH, reason="needs a POSIX sh")
def test_the_real_launcher_leaves_a_young_unmarked_folder_alone(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _local_sh(monkeypatch, cache)
    run_id = "0123456789abcdef"
    folder = cache / "chimera" / "runs" / run_id
    folder.mkdir(parents=True)
    (folder / "claim").write_bytes(f"other-1 {int(time.time())}\n".encode())  # another launcher, just now
    (folder / "cmd.tmp").write_text("partial", encoding="utf-8")
    call = SshSandbox("web1", "/srv")._exec(ssh_module.remote_op("launch", run_id, 0, 0, 0, 5), b"x\n", 30)
    assert call is not None and b"launch in progress" in call.out
    assert (folder / "cmd.tmp").exists()  # not removed, not relaunched


def test_read_run_stops_at_the_cap_and_at_its_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = "0123456789abcdef"
    fake = _Ssh(monkeypatch, [_Proc(0, b"x" * 100, b"", step=3)])
    fake._exec(ssh_module.remote_op("launch", run_id, 0, 0, 0, 1), b"x", 1)
    monkeypatch.setattr(ssh_module, "MAX_OUTPUT_BYTES", 10)
    capped = SshSandbox("web1", "/srv").read_run(run_id)
    assert len(capped.stdout) < 100 and "was cut" in capped.stderr
    monkeypatch.setattr(ssh_module, "MAX_OUTPUT_BYTES", 10**9)
    monkeypatch.setattr(ssh_module, "READ_RUN_SECONDS", 0)
    late = SshSandbox("web1", "/srv").read_run(run_id)
    assert "was cut" in late.stderr


def test_the_last_read_after_a_timeout_asks_the_allowed_list_first(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"so far\n", b"", hang=True)])
    box = SshSandbox("web1", "/srv")

    def recheck() -> None:
        if fake.stopped:  # removed from the list while the run was being stopped
            raise ValueError("'web1' is not on your list of allowed SSH hosts")

    box.bind_recheck(recheck)
    result = box.run("sleep 999", timeout=3)
    ops = [op for op, _, _ in fake.ops]
    assert result.timed_out and ops[ops.index("stop"):] == ["stop", "cleanup"]  # no poll after the stop


def test_a_stop_that_is_already_set_launches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"", b"")])
    box = SshSandbox("web1", "/srv")
    box.bind_cancel(lambda: True)
    result = box.run("make")
    assert result.exit_code == 130 and fake.ops == [] and fake.calls == []


def test_a_stop_during_the_backoff_wait_ends_the_run_without_waiting_it_out(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"", b"", hang=True)])
    fake.fail_calls(1, 1, CONNECT_FAIL)  # the launch's reply is lost; the retry would wait first
    box = SshSandbox("web1", "/srv")
    box.bind_cancel(lambda: bool(fake.ops))  # not cancelled at the start, cancelled once something was sent
    result = box.run("make", timeout=600)
    assert result.exit_code == 130 and fake.waits == []
    assert fake.stopped == [fake.ops[0][1]]  # the possibly-started run is stopped by id


def test_a_run_is_journaled_with_the_session_that_started_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"", b"", hang=True)])
    fake.fail_calls(2, 99, CONNECT_FAIL)
    box = SshSandbox("web1", "/srv")
    box.bind_session("sess-one")
    box.run("make", timeout=10_000)
    (entry,) = ssh_module.take_pending_runs()
    assert entry["session"] == "sess-one"
    text = (Path(ssh_module.__file__).parents[1] / "api" / "code_api.py").read_text(encoding="utf-8")
    assert "turn_sandbox.bind_session(session.session_id)" in text


def _wait_for(path: Path, seconds: float = 10.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if path.exists():
            return True
        time.sleep(0.05)
    return False


@pytest.mark.skipif(not HAS_SH or os.name == "nt", reason="needs a POSIX sh (Windows cannot remove a folder holding an open file)")
def test_a_stale_attempt_that_finishes_after_the_takeover_never_touches_the_new_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    _local_sh(monkeypatch, cache)
    run_id = "0123456789abcdef"
    folder = cache / "chimera" / "runs" / run_id
    counter = _posix(tmp_path / "starts")
    a_script = f"cd {_posix(tmp_path)} || exit 1; echo A >> {counter}; sleep 1\n".encode()
    b_script = f"cd {_posix(tmp_path)} || exit 1; echo B >> {counter}; echo B-out; sleep 1\n".encode()
    # Attempt A: the whole script is sent, but the link is dead, so its `cat` never sees EOF.
    a = subprocess.Popen(
        ["sh", "-c", ssh_module.remote_op("launch", run_id, 0, 0, 0, len(a_script))],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert a.stdin is not None
    a.stdin.write(a_script)
    a.stdin.flush()
    assert _wait_for(folder / "claim")
    token = (folder / "claim").read_bytes().decode().split()[0]
    (folder / "claim").write_bytes(f"{token} {int(time.time()) - 1000}\n".encode())  # A is now stale
    # Attempt B takes the folder over and runs the command.
    box = SshSandbox("web1", "/srv")
    b = box._exec(ssh_module.remote_op("launch", run_id, 5, 0, 0, len(b_script)), b_script, 30)
    assert b is not None
    first = ssh_module.parse_frame(b.out)
    assert first is not None
    new_claim = (folder / "claim").read_bytes().decode()
    assert new_claim.split()[0] != token
    # A wakes up: its link "recovers" and its `cat` finally ends.
    a.stdin.close()
    # Not communicate(): on CPython 3.11/3.12 it flushes a stdin that is already closed and raises
    # "flush of closed file". The output is a line or two, so waiting first cannot fill the pipe.
    a.wait(timeout=30)
    assert a.stdout is not None
    a_out = a.stdout.read()
    assert b"arrived incomplete" in a_out or a_out.strip() == b""
    # The new run is untouched, and the command ran exactly once, as B.
    done = box._exec(ssh_module.remote_op("poll", run_id, 5, 0, 0), None, 30)
    assert done is not None
    frame = ssh_module.parse_frame(done.out)
    assert frame is not None and frame.done and frame.code == 0
    assert (folder / "claim").read_bytes().decode() == new_claim
    assert (tmp_path / "starts").read_text(encoding="utf-8") == "B\n"


@pytest.mark.skipif(not HAS_SH, reason="needs a POSIX sh")
def test_the_real_launcher_sweeps_a_folder_that_only_ever_got_its_claim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    probe = subprocess.run(["find", ".", "-prune", "-mtime", "+3"], capture_output=True, check=False)
    if probe.returncode != 0:
        pytest.skip("this find is not the POSIX one")
    cache = tmp_path / "cache"
    _local_sh(monkeypatch, cache)
    leaked = cache / "chimera" / "runs" / "1111111111111111"
    bare = cache / "chimera" / "runs" / "2222222222222222"
    for folder in (leaked, bare):
        folder.mkdir(parents=True)
    (leaked / "claim").write_bytes(b"t 1\n")
    long_ago = time.time() - 10 * 86400
    os.utime(leaked / "claim", (long_ago, long_ago))
    os.utime(bare, (long_ago, long_ago))  # not even a claim
    assert SshSandbox("web1", _posix(tmp_path)).run("true", timeout=60).exit_code == 0
    assert not leaked.exists() and not bare.exists()


def test_the_group_kill_uses_the_form_dash_accepts() -> None:
    """dash rejects ``kill -TERM -- -<pgid>`` ("Illegal number: -") and the stop then did nothing while
    reporting success; ``kill -s TERM -- -<pgid>`` is the form every sh reads."""
    script = ssh_module.LAUNCHER
    assert 'kill -s TERM -- "-$pg"' in script and 'kill -s KILL -- "-$pg"' in script
    assert "kill -TERM --" not in script and "kill -KILL --" not in script
