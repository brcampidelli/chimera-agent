"""Where a Code conversation's commands run, part A of SSH: a host from the owner's ``~/.ssh/config``.

What these pin, in the order a mistake would cost most:

* **Only a named alias, only an allowed one.** An environment holds a ``Host`` alias and an absolute
  remote folder, never a user, an address, a password or a key. The alias must be in the owner's
  ``~/.ssh/config`` (no patterns) AND on the owner's allowed list, which only the app's own screen
  edits. Both are checked again at every turn.
* **The argv is a list with fixed security options.** No shell on this side, ``BatchMode`` and
  ``StrictHostKeyChecking=yes``, ``--`` before the alias, forwarding off, the folder quoted for the
  remote shell, and the command left exactly as the agent wrote it.
* **A command is never run twice.** The run id, not the connection, is the command's identity, so a
  dropped connection is reconnected and read on (``test_ssh_durable_commands.py``); a host key that is
  not trusted is a refusal that says what to do, and is not retried.
* **SSH is the host.** Not isolated, so the host-exec gate, Manual and the "only reads" classifier
  treat it as the machine they guard; an install that isolates its commands refuses it.
* **The wrong machine is never edited.** The local file tools refuse in an SSH conversation, and a
  turn that has read content from outside asks the owner before a command on the remote host.

Nothing here needs ssh: the remote is a fake that plays the launcher's part (``_Ssh``), and the
config is a file in a temporary folder.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

import pytest

from chimera.config import get_settings
from chimera.core import environments as envs
from chimera.sandbox import ssh as ssh_module
from chimera.sandbox.base import SandboxResult
from chimera.sandbox.ssh import (
    SshPathError,
    SshSandbox,
    clean_remote_path,
    config_aliases,
    remote_command,
    ssh_argv,
    valid_alias,
)
from tests.test_code_environments import _Rig

CONFIG = """\
# the owner's file
Host *
    ServerAliveInterval 30

Host web1 db.internal
    HostName 10.0.0.1
    User deploy

Host *.corp web? !blocked
    User nobody

host=box
    HostName box.example.com

Match host nothing
    User x
"""


@pytest.fixture(autouse=True)
def _journal_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The run journal goes under the test's folder, never the real CHIMERA_HOME."""
    monkeypatch.setattr(ssh_module, "_journal_dir", lambda: tmp_path / "ssh_runs")


@pytest.fixture
def ssh_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake ``~/.ssh`` with a config, ssh 'installed', and nothing real read."""
    folder = tmp_path / "dotssh"
    folder.mkdir()
    (folder / "config").write_text(CONFIG, encoding="utf-8")
    monkeypatch.setattr(ssh_module, "ssh_dir", lambda: folder)
    monkeypatch.setattr(ssh_module, "ssh_supported", lambda: True)
    return folder


EXPECTED_OPTIONS = [
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=yes",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=3",
    "-o", "ConnectTimeout=10",
    "-o", "ForwardAgent=no",
    "-o", "ClearAllForwardings=yes",
    "-o", "ForwardX11=no",
    "-o", "PermitLocalCommand=no",
    "-o", "RemoteCommand=none",
    "-o", "RequestTTY=no",
]


# -- ~/.ssh/config ----------------------------------------------------------------------------


def test_only_concrete_aliases_are_read_never_patterns(ssh_home: Path) -> None:
    assert config_aliases() == ["web1", "db.internal", "box"]


def test_include_is_followed_inside_dot_ssh_and_not_outside_it(ssh_home: Path, tmp_path: Path) -> None:
    (ssh_home / "conf.d").mkdir()
    (ssh_home / "conf.d" / "extra").write_text("Host inside1\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.write_text("Host outside1\n", encoding="utf-8")
    (ssh_home / "config").write_text(
        f"Include conf.d/*\nInclude ../outside\nInclude {outside}\nInclude config\nHost top\n",
        encoding="utf-8",
    )
    assert config_aliases() == ["inside1", "top"]


def test_a_quoted_host_pattern_is_one_word(ssh_home: Path) -> None:
    (ssh_home / "config").write_text(
        'Host "web 1" "star*" "quoted1" plain\nHost "unterminated\nHost after\n', encoding="utf-8"
    )
    assert config_aliases() == ["quoted1", "plain", "after"]


def test_a_missing_or_unreadable_config_offers_nothing(ssh_home: Path) -> None:
    (ssh_home / "config").unlink()
    assert config_aliases() == []
    assert config_aliases(ssh_home / "no-such-file") == []


@pytest.mark.parametrize(
    "name", ["-oProxyCommand=x", "a b", "", "*", "web1;rm", "user@web1", "web1\n", "a" * 65, "$(x)", "-web"]
)
def test_a_name_that_is_not_an_alias_is_refused(name: str) -> None:
    assert valid_alias(name) is False


# -- the remote folder ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["srv/app", "~/app", "/srv/../etc", "/srv/app/..", "..", "/srv/a\nb", "/srv/a\x00b", "/srv\\app", "", "  ",
     "/" + "a" * 1100, "C:\\x"],
)
def test_a_remote_folder_that_is_not_an_absolute_posix_path_is_refused(path: str) -> None:
    with pytest.raises(SshPathError):
        clean_remote_path(path)


def test_a_remote_folder_is_cleaned_and_keeps_its_spaces() -> None:
    assert clean_remote_path(" /srv//my app/ ") == "/srv/my app"
    assert clean_remote_path("/") == "/"
    assert clean_remote_path("/a/b...c/..d") == "/a/b...c/..d"  # dots inside a name are not `..`


# -- the argv ---------------------------------------------------------------------------------


def test_the_argv_is_a_list_with_the_fixed_options_the_alias_after_dashes_and_one_command() -> None:
    argv = ssh_argv("web1", remote_command("/srv/app", "ls -la && echo done"))
    assert argv == [
        "ssh", *EXPECTED_OPTIONS, "--", "web1", "cd /srv/app || exit 1; ls -la && echo done",
    ]


def test_a_folder_with_a_space_or_a_quote_is_quoted_for_the_remote_shell() -> None:
    folder = "/srv/my app/it's; rm -rf $HOME"
    remote = remote_command(folder, "ls")
    head, tail = remote.split(" || exit 1; ")
    assert tail == "ls"
    assert shlex.split(head) == ["cd", folder]  # the remote shell reads exactly the one folder
    assert head == f"cd {shlex.quote(folder)}"


def test_a_failed_cd_ends_the_command_instead_of_running_the_rest_in_the_home_folder() -> None:
    assert remote_command("/srv/app", "a; b") == "cd /srv/app || exit 1; a; b"


def test_the_agents_command_is_not_rewritten() -> None:
    command = "echo 'a b' | grep \"x\" > /tmp/out; true $(date)"
    assert remote_command("/srv", command).endswith(f"; {command}")


def test_a_bad_alias_never_reaches_the_argv() -> None:
    with pytest.raises(SshPathError):
        ssh_argv("-oProxyCommand=evil", "ls")


class _Proc:
    """What one command does on the fake remote: its exit code, stdout and stderr.

    ``hang`` never ends on its own (only a stop does); ``step`` reveals that many bytes of stdout per
    poll, so a test can drop the connection in the middle of the output.
    """

    def __init__(self, code: int, out: bytes, err: bytes, hang: bool = False, step: int | None = None) -> None:
        self.code, self.out, self.err, self.hang, self.step = code, out, err, hang, step


_REMOTE_OP = re.compile(r"chimera-run (launch|poll|stop|cleanup) ([0-9a-f]{16})((?: \d+)*)$")


class _Ssh:
    """A fake REMOTE behind ``SshSandbox._exec``: it answers the launcher's operations from a script.

    The detached protocol is the thing under test, so the fake plays the launcher's part (start a run
    under an id, hand out bytes from an offset, stop, clean up) and records every operation. ``calls``
    holds one entry per command that was STARTED, which is what "the command ran once" means. A fake
    clock replaces the real one so that waiting costs nothing.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, script: list[_Proc]) -> None:
        self.script = script
        self.calls: list[dict[str, Any]] = []
        self.ops: list[tuple[str, str, list[int]]] = []
        self.remotes: list[str] = []
        self.stopped: list[str] = []
        self.cleaned: list[str] = []
        self.waits: list[float] = []
        self.runs: dict[str, dict[str, Any]] = {}
        self.clock = 1000.0
        self.n_exec = 0
        #: Call numbers (1-based, over every ssh call) that fail at the ssh level, with the client's log.
        self.fail_at: dict[int, bytes] = {}
        #: Call numbers whose effect HAPPENS on the remote but whose reply is lost on the way back.
        self.lose_reply: set[int] = set()
        self.cancel_at: dict[int, BaseException] = {}
        #: Calls whose reply is replaced by these raw bytes (a forged or cut reply, shell noise).
        self.raw_at: dict[int, bytes] = {}
        self.stop_reply = b"chimera-stopped ok\n"
        monkeypatch.setattr(SshSandbox, "_exec", lambda _s, remote, stdin, timeout: self._exec(remote, stdin, timeout))
        monkeypatch.setattr(ssh_module, "_sleep", self._sleep)
        monkeypatch.setattr(ssh_module, "_now", lambda: self.clock)

    def _sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.clock += seconds

    def fail_calls(self, first: int, last: int, log: bytes = b"") -> None:
        for number in range(first, last + 1):
            self.fail_at[number] = log

    def _exec(self, remote: str, stdin: bytes | None, timeout: float) -> ssh_module._Call:
        self.n_exec += 1
        self.clock += 0.05
        self.remotes.append(remote)
        found = _REMOTE_OP.search(remote)
        assert found is not None, remote
        op, run_id = found.group(1), found.group(2)
        numbers = [int(n) for n in found.group(3).split()]
        self.ops.append((op, run_id, numbers))
        if self.n_exec in self.cancel_at:
            raise self.cancel_at[self.n_exec]
        if self.n_exec in self.raw_at:
            return ssh_module._Call(code=0, out=self.raw_at[self.n_exec])
        if self.n_exec in self.fail_at and self.n_exec not in self.lose_reply:
            return ssh_module._Call(code=255, client_log=self.fail_at[self.n_exec].decode())
        reply = self._apply(op, run_id, numbers, stdin)
        if self.n_exec in self.lose_reply:
            return ssh_module._Call(code=255, client_log=self.fail_at.get(self.n_exec, b"").decode())
        return reply

    def _apply(self, op: str, run_id: str, numbers: list[int], stdin: bytes | None) -> ssh_module._Call:
        run = self.runs.get(run_id)
        if op == "launch" and run is None:
            proc = self.script.pop(0) if len(self.script) > 1 else self.script[0]
            run = self.runs[run_id] = {"proc": proc, "shown": 0, "killed": False}
            self.calls.append({"id": run_id, "stdin": stdin, "remote": self.remotes[-1]})
        if op == "stop":
            self.stopped.append(run_id)
            if run is not None:
                run["killed"] = True
            return ssh_module._Call(code=0, out=self.stop_reply)
        if op == "cleanup":
            self.cleaned.append(run_id)
            self.runs.pop(run_id, None)
            return ssh_module._Call(code=0, out=b"chimera-cleaned\n")
        if run is None:
            return ssh_module._Call(code=0, out=b"chimera-poll gone 0 0 0\n")
        return ssh_module._Call(code=0, out=self._frame(run, numbers))

    def _frame(self, run: dict[str, Any], numbers: list[int]) -> bytes:
        proc: _Proc = run["proc"]
        wait, o, e = numbers[0], numbers[1], numbers[2]
        step = proc.step if proc.step is not None else len(proc.out)
        run["shown"] = min(len(proc.out), run["shown"] + step)
        everything = run["shown"] >= len(proc.out)
        finished = run["killed"] or (everything and not proc.hang)
        if not finished:
            self.clock += wait
        out_part, err_part = proc.out[o:run["shown"]], (proc.err[e:] if everything or run["killed"] else b"")
        code = (143 if run["killed"] else proc.code) if finished else 0
        head = f"chimera-poll {1 if finished else 0} {code} {len(out_part)} {len(err_part)}\n"
        return head.encode() + out_part + err_part


class _Popen:
    """A stand-in for ``subprocess.Popen`` for the one test of what a single ssh call looks like."""

    made: list[dict[str, Any]] = []

    def __init__(self, target: Any, **kwargs: Any) -> None:
        self.target, self.kwargs = target, kwargs
        self.returncode = 0
        self.pid = 4242
        self.input: bytes | None = None
        _Popen.made.append({"target": target, **kwargs})
        Path(target[target.index("-E") + 1]).write_bytes(b"")

    def communicate(self, input: bytes | None = None, timeout: float | None = None) -> tuple[bytes, bytes]:  # noqa: A002
        _Popen.made[-1]["input"] = input
        return b"chimera-poll 1 0 0 0\n", b""

    def poll(self) -> int:
        return 0


CONNECT_FAIL = b"ssh: connect to host web1 port 22: Connection timed out\r\n"


def test_one_ssh_call_is_an_argv_list_never_a_shell_with_a_scrubbed_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The argv, the fixed options, the scrubbed environment and the client's own log are unchanged;
    what is new is that the agent's command is on STDIN (a one-shot argv used to carry it)."""
    monkeypatch.setenv("MY_SERVICE_API_KEY", "super-secret-value")
    _Popen.made = []
    monkeypatch.setattr(subprocess, "Popen", _Popen)
    box = SshSandbox("web1", "/srv/my app")
    remote = ssh_module.remote_op("launch", "0123456789abcdef", 2, 0, 0)
    call = box._exec(remote, b"cd '/srv/my app' || exit 1; ls -la\n", 5)
    assert call is not None and call.code == 0
    (made,) = _Popen.made
    target = made["target"]
    at = target.index("-E")
    assert [*target[:at], *target[at + 2:]] == ["ssh", *EXPECTED_OPTIONS, "--", "web1", remote]
    assert made["shell"] is False and made["input"] == b"cd '/srv/my app' || exit 1; ls -la\n"
    assert "super-secret-value" not in json.dumps(made["env"])
    assert not Path(target[at + 1]).exists()  # the client's log file is removed after the call


def test_a_working_directory_below_the_workspace_becomes_a_folder_below_the_remote_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"", b"")])
    box = SshSandbox("web1", "/srv/app", tmp_path)
    box.run("ls", cwd=tmp_path / "src" / "lib")
    box.run("ls", cwd=tmp_path)
    box.run("ls")
    scripts = [c["stdin"].decode() for c in fake.calls]
    assert scripts == [
        "cd /srv/app/src/lib || exit 1; ls\n", "cd /srv/app || exit 1; ls\n", "cd /srv/app || exit 1; ls\n",
    ]


def test_a_working_directory_outside_the_workspace_is_not_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"", b"")])
    box = SshSandbox("web1", "/srv/app", tmp_path / "ws")
    for outside in (tmp_path, tmp_path / "other", tmp_path / "ws" / ".." / "x"):
        result = box.run("ls", cwd=outside)
        assert result.exit_code == 1 and "not run" in result.stderr
    assert fake.calls == [] and fake.ops == []


# -- refusals the link cannot fix, and what the agent is told ---------------------------------


def test_an_unknown_or_changed_host_key_is_a_clear_refusal_and_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for text in (b"Host key verification failed.\r\n", b"@@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@\n"):
        fake = _Ssh(monkeypatch, [_Proc(0, b"", b"")])
        fake.fail_calls(1, 9, text)
        result = SshSandbox("web1", "/srv").run("ls")
        assert fake.n_exec == 1 and fake.waits == []
        assert result.exit_code == 255
        assert "host key is not trusted" in result.stderr
        assert "ssh web1" in result.stderr and "never accepts a new host key" in result.stderr
        assert "keeps running" not in result.stderr  # it never ran, so there is no run to look for


def test_a_refused_credential_says_to_load_the_key_and_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"", b"")])
    fake.fail_calls(1, 9, b"deploy@web1: Permission denied (publickey).\n")
    result = SshSandbox("web1", "/srv").run("ls")
    assert fake.n_exec == 1 and "ssh-agent" in result.stderr


def test_the_clients_own_log_never_reaches_the_agent_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    log = (
        b"debug1: Reading configuration data /home/me/.ssh/config\n"
        b"Identity file /home/me/.ssh/id_ed25519_prod type -1\n"
        b"Bad owner or permissions on /home/me/.ssh/config\n"
        b"ssh: Could not resolve hostname secret.internal.example: Name or service not known\n"
        b"kex failed to 10.20.30.40 port 2222 and 2001:db8::1\n"
    )
    fake = _Ssh(monkeypatch, [_Proc(0, b"", b"")])
    fake.fail_calls(1, 99, log)
    result = SshSandbox("web1", "/srv").run("ls", timeout=600)
    for leaked in ("secret.internal", "/home/me", "id_ed25519", "10.20.30.40", "2222", "2001:db8", "Identity file"):
        assert leaked not in result.stderr, leaked
    assert "web1" in result.stderr


def test_a_hop_of_a_proxy_jump_writes_to_stderr_and_is_cleaned_the_same_way(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    err = (
        b"ssh: connect to host 10.0.0.5 port 2222: Connection refused\n"
        b"Identity file /home/x/.ssh/id_x not accessible: No such file or directory.\n"
        b"kex_exchange_identification: Connection closed by remote host\n"
    )
    _Ssh(monkeypatch, [_Proc(0, b"", b"")])
    box = SshSandbox("web1", "/srv")
    seen = box._failure(ssh_module._Call(code=255, err=err), "0123456789abcdef", started=False)
    for leaked in ("10.0.0.5", "2222", "/home/x", "id_x", "Identity file"):
        assert leaked not in seen.stderr, leaked
    assert "connect to web1" in seen.stderr


def test_a_failed_connection_after_the_reconnects_names_the_alias_and_not_the_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"", b"")])
    fake.fail_calls(1, 99, b"ssh: connect to host 203.0.113.9 port 2222: Connection refused\n")
    result = SshSandbox("web1", "/srv").run("ls", timeout=600)
    assert "203.0.113.9" not in result.stderr and "2222" not in result.stderr
    assert "connect to web1" in result.stderr


def test_a_log_file_that_cannot_be_created_is_a_not_run_result_and_not_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _Popen.made = []
    monkeypatch.setattr(subprocess, "Popen", _Popen)

    def broken(**_: Any) -> Any:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("chimera.sandbox.ssh.tempfile.mkstemp", broken)
    result = SshSandbox("web1", "/srv").run("ls")
    assert result.exit_code == 1 and result.stderr.startswith("error: not run:")
    assert _Popen.made == []


def test_the_host_is_checked_again_at_each_command(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"ok", b"")])
    box = SshSandbox("web1", "/srv")
    state = {"allowed": True}

    def recheck() -> None:
        if not state["allowed"]:
            raise ValueError("'web1' is not on your list of allowed SSH hosts")

    box.bind_recheck(recheck)
    assert box.run("a").exit_code == 0
    state["allowed"] = False  # removed from the list in the middle of the turn
    refused = box.run("b")
    assert refused.exit_code == 1 and "not run" in refused.stderr and "allowed" in refused.stderr
    assert len(fake.calls) == 1


def test_a_remote_shell_that_ran_and_failed_is_not_a_dropped_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exit 127 from the helper (no ``sh``) is the remote's answer: said once, never reconnected."""
    fake = _Ssh(monkeypatch, [_Proc(0, b"", b"")])
    monkeypatch.setattr(
        SshSandbox, "_exec", lambda _s, _r, _i, _t: ssh_module._Call(code=127, err=b"sh: not found\n"),
    )
    result = SshSandbox("web1", "/srv").run("ls")
    assert result.exit_code == 127 and "could not run the helper" in result.stderr
    assert fake.waits == []


# -- the saved environment --------------------------------------------------------------------


def _registry(tmp_path: Path) -> envs.EnvironmentRegistry:
    return envs.registry_for(tmp_path / "home")


def test_an_alias_that_is_not_in_the_config_or_not_allowed_cannot_be_saved(
    tmp_path: Path, ssh_home: Path
) -> None:
    registry = _registry(tmp_path)
    good = {"host": "web1", "remote_path": "/srv/app/"}
    with pytest.raises(ValueError, match="not on your list of allowed"):
        registry.add("prod", "ssh", good)  # in the config, not allowed yet
    with pytest.raises(ValueError, match="not a Host alias in your"):
        registry.allow_ssh_host("ghost")
    for pattern in ("*", "*.corp", "web?", "blocked", "!blocked"):
        with pytest.raises(ValueError):
            registry.allow_ssh_host(pattern)
    assert registry.ssh_hosts() == []
    registry.allow_ssh_host("web1")
    made = registry.add("prod", "ssh", good)
    assert made.params == {"host": "web1", "remote_path": "/srv/app"}  # cleaned
    with pytest.raises(ValueError, match="not on your list of allowed"):
        registry.add("other", "ssh", {"host": "box", "remote_path": "/x"})


@pytest.mark.parametrize(
    "params",
    [
        {}, {"host": "web1"}, {"remote_path": "/srv"},
        {"host": "web1", "remote_path": "relative"},
        {"host": "web1", "remote_path": "/srv/../etc"},
        {"host": "-oProxyCommand=x", "remote_path": "/srv"},
        {"host": "user@web1", "remote_path": "/srv"},
        {"host": "web1", "remote_path": "/srv", "user": "root"},
        {"host": "web1", "remote_path": "/srv", "password": "hunter2"},
        {"host": "web1", "remote_path": "/srv", "identity_file": "/home/me/.ssh/id_ed25519"},
        {"host": "web1", "remote_path": "/srv", "port": "2222"},
    ],
)
def test_an_ssh_environment_takes_an_alias_and_a_folder_and_nothing_else(
    params: dict[str, str], tmp_path: Path, ssh_home: Path
) -> None:
    registry = _registry(tmp_path)
    registry.allow_ssh_host("web1")
    with pytest.raises(ValueError):
        registry.add("x", "ssh", params)
    assert registry.entries() == []


def test_the_files_the_owner_keeps_hold_an_alias_and_a_folder_and_never_a_secret(
    tmp_path: Path, ssh_home: Path
) -> None:
    registry = _registry(tmp_path)
    registry.allow_ssh_host("web1")
    registry.add("prod", "ssh", {"host": "web1", "remote_path": "/srv/app"})
    text = (tmp_path / "home" / "environments.json").read_text("utf-8")
    hosts = (tmp_path / "home" / "ssh_hosts.json").read_text("utf-8")
    assert json.loads(text)["environments"][0]["params"] == {"host": "web1", "remote_path": "/srv/app"}
    assert json.loads(hosts) == {"hosts": ["web1"]}
    for secret in ("HostName", "10.0.0.1", "deploy", "password", "IdentityFile"):
        assert secret not in text and secret not in hosts


def test_ssh_cannot_be_saved_where_there_is_no_ssh(
    tmp_path: Path, ssh_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry(tmp_path)
    registry.allow_ssh_host("web1")
    monkeypatch.setattr(ssh_module, "ssh_supported", lambda: False)
    assert envs.kind_available("ssh") is False
    with pytest.raises(ValueError, match="not available on this machine"):
        registry.add("prod", "ssh", {"host": "web1", "remote_path": "/srv"})


def test_a_hand_edited_row_or_host_list_that_the_form_would_refuse_is_not_read(
    tmp_path: Path, ssh_home: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "environments.json").write_text(json.dumps({"environments": [
        {"id": "a", "name": "ok", "kind": "ssh", "params": {"host": "web1", "remote_path": "/srv"}},
        {"id": "b", "name": "bad", "kind": "ssh", "params": {"host": "web1", "remote_path": "/srv/../x"}},
        {"id": "c", "name": "worse", "kind": "ssh", "params": {"host": "web1", "remote_path": "/s", "user": "r"}},
    ]}), encoding="utf-8")
    (home / "ssh_hosts.json").write_text(
        json.dumps({"hosts": ["web1", "-bad", "a b", 7, "web1", "*"]}), encoding="utf-8"
    )
    registry = envs.registry_for(home)
    assert [e.id for e in registry.entries()] == ["a"]
    assert registry.ssh_hosts() == ["web1"]
    (home / "ssh_hosts.json").write_text("not json", encoding="utf-8")
    assert registry.ssh_hosts() == []
    assert (home / "ssh_hosts.json.damaged").exists()


def test_removing_a_host_from_the_list_or_the_config_stops_the_environments_that_use_it(
    tmp_path: Path, ssh_home: Path
) -> None:
    from chimera.config import Settings

    registry = _registry(tmp_path)
    registry.allow_ssh_host("web1")
    made = registry.add("prod", "ssh", {"host": "web1", "remote_path": "/srv"})
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_SANDBOX="local")
    box = envs.sandbox_for(made, tmp_path, settings)
    assert isinstance(box, SshSandbox) and box.host == "web1" and box.remote_path == "/srv"
    assert registry.usable(made) is True

    assert registry.forget_ssh_host("web1") is True and registry.forget_ssh_host("web1") is False
    assert registry.usable(made) is False
    with pytest.raises(envs.EnvironmentRefused) as refused:
        envs.sandbox_for(made, tmp_path, settings)
    assert refused.value.status == 409 and "allowed" in refused.value.detail

    registry.allow_ssh_host("web1")
    (ssh_home / "config").write_text("Host other\n", encoding="utf-8")  # the alias left the config
    with pytest.raises(envs.EnvironmentRefused) as gone:
        envs.sandbox_for(made, tmp_path, settings)
    assert gone.value.status == 409 and "~/.ssh/config" in gone.value.detail


def test_a_host_removed_in_the_middle_of_a_turn_stops_the_next_command(
    tmp_path: Path, ssh_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.config import Settings

    registry = _registry(tmp_path)
    registry.allow_ssh_host("web1")
    made = registry.add("prod", "ssh", {"host": "web1", "remote_path": "/srv"})
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_SANDBOX="local")
    fake = _Ssh(monkeypatch, [_Proc(0, b"ok", b"")])
    box = envs.sandbox_for(made, tmp_path, settings)
    assert box is not None and box.run("first").exit_code == 0
    registry.forget_ssh_host("web1")
    refused = box.run("second")
    assert refused.exit_code == 1 and "allowed" in refused.stderr
    assert len(fake.calls) == 1


def test_an_install_that_isolates_its_commands_refuses_the_ssh_environment(
    tmp_path: Path, ssh_home: Path
) -> None:
    from chimera.config import Settings

    registry = _registry(tmp_path)
    registry.allow_ssh_host("web1")
    made = registry.add("prod", "ssh", {"host": "web1", "remote_path": "/srv"})
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_SANDBOX="docker")
    with pytest.raises(envs.EnvironmentRefused) as refused:
        envs.sandbox_for(made, tmp_path, settings)
    assert refused.value.status == 409 and "isolated" in refused.value.detail


# -- ssh is the host --------------------------------------------------------------------------


def test_ssh_is_not_isolated_and_is_not_a_local_sandbox() -> None:
    from chimera.sandbox import LocalSandbox
    from chimera.sandbox.confirm import sandbox_is_isolated

    box = SshSandbox("web1", "/srv")
    assert box.is_isolated() is False and sandbox_is_isolated(box) is False
    assert not isinstance(box, LocalSandbox)


def test_ssh_counts_as_the_host_for_the_host_exec_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.tools.shell import RunShellTool

    ran: list[str] = []

    def record(self: SshSandbox, command: str, **_: Any) -> SandboxResult:
        ran.append(command)
        return SandboxResult(exit_code=0, stdout="ok")

    monkeypatch.setattr(SshSandbox, "run", record)
    asked: list[str] = []

    def decline(command: str) -> bool:
        asked.append(command)
        return False

    tool = RunShellTool(Path.cwd(), SshSandbox("web1", "/srv"), confirm=decline)
    assert "declined" in tool.run(command="echo hi")
    assert asked == ["echo hi"] and ran == []
    allowed = RunShellTool(Path.cwd(), SshSandbox("web1", "/srv"), confirm=lambda _c: True)
    assert "ok" in allowed.run(command="echo hi") and ran == ["echo hi"]


def test_an_ssh_turn_offers_no_background_job() -> None:
    from chimera.core.jobs import jobs_for
    from chimera.tools.shell import RunShellTool

    tool = RunShellTool(
        Path.cwd(), SshSandbox("web1", "/srv"), confirm=lambda _c: True, jobs=jobs_for(Path.cwd() / ".j")
    )
    assert "not available in an SSH environment" in tool.run(command="sleep 1", background=True)


def test_the_shell_tool_says_where_it_runs_and_the_confirmation_names_the_host(tmp_path: Path) -> None:
    from chimera.tools import default_registry

    registry = default_registry(tmp_path, host_exec_confirm=None, sandbox=SshSandbox("web1", "/srv/app"))
    description = registry.get("run_shell").description
    assert "run on web1 over ssh, in /srv/app" in description


# -- the wrong machine is never edited --------------------------------------------------------


LOCAL_FILE_TOOLS = (
    "read_file", "write_file", "edit_file", "apply_patch", "list_dir", "grep", "glob", "execute_code",
)


def test_the_local_file_tools_refuse_in_an_ssh_conversation_and_touch_nothing(tmp_path: Path) -> None:
    from chimera.tools import default_registry

    (tmp_path / "keep.txt").write_text("local file", encoding="utf-8")
    registry = default_registry(tmp_path, host_exec_confirm=None, sandbox=SshSandbox("web1", "/srv/app", tmp_path))
    for name in LOCAL_FILE_TOOLS:
        tool = registry.get(name)
        args = {key: "keep.txt" for key in tool.parameters.get("required", [])}
        out = tool.run(**args)
        assert out == (
            "error: this conversation runs on web1; use run_shell to read or edit files there. "
            "This tool works on a folder of the machine running Chimera, not on the remote host, so it "
            "was not run."
        ), name
        assert "UNAVAILABLE" in tool.description, name
    assert sorted(p.name for p in tmp_path.iterdir()) == ["keep.txt"]
    assert (tmp_path / "keep.txt").read_text("utf-8") == "local file"
    # The shell is the way in, and it is not refused.
    assert "UNAVAILABLE" not in registry.get("run_shell").description


def test_execute_code_does_not_write_a_script_here_for_a_remote_host(tmp_path: Path) -> None:
    from chimera.tools.code import ExecuteCodeTool

    tool = ExecuteCodeTool(tmp_path, SshSandbox("web1", "/srv"), confirm=lambda _c: True)
    assert "runs on web1" in tool.run(code="print(1)")
    assert list(tmp_path.iterdir()) == []


def test_the_design_tool_registered_after_the_default_registry_is_blocked_too(tmp_path: Path) -> None:
    """`design export` writes into this machine's project folder, and is registered in `assemble_registry`."""
    from chimera.api.code_api import CodeSeams, assemble_registry
    from chimera.config import Settings
    from chimera.providers.gateway import LLMGateway
    from chimera.tools.design import DesignOffer
    from chimera.tools.ssh_files import RefusedLocalFileTool

    ws = tmp_path / "ws"
    ws.mkdir()
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    box = SshSandbox("web1", "/srv", ws)
    offer = DesignOffer()
    registry, _ = assemble_registry(
        CodeSeams(), ws, settings, LLMGateway(), steps=4, surface="api:turn", sandbox=box, design=offer
    )
    assert offer.offered is True
    tool = registry.get("design")
    while tool is not None and not isinstance(tool, RefusedLocalFileTool):
        tool = getattr(tool, "_inner", None) or getattr(tool, "inner", None)
    assert tool is not None, "the design tool is not refused in an SSH conversation"
    assert "runs on web1" in tool.run(action="export")
    assert not (ws / "design-exports").exists()
    # A local conversation keeps it.
    local, _ = assemble_registry(
        CodeSeams(), ws, settings, LLMGateway(), steps=4, surface="api:turn", design=DesignOffer()
    )
    inner = local.get("design")
    while inner is not None and not isinstance(inner, RefusedLocalFileTool) and (
        getattr(inner, "_inner", None) or getattr(inner, "inner", None)
    ):
        inner = getattr(inner, "_inner", None) or getattr(inner, "inner", None)
    assert not isinstance(inner, RefusedLocalFileTool)


def test_a_local_conversation_keeps_every_file_tool(tmp_path: Path) -> None:
    from chimera.tools import default_registry
    from chimera.tools.ssh_files import RefusedLocalFileTool

    registry = default_registry(tmp_path, host_exec_confirm=None)
    for name in LOCAL_FILE_TOOLS:
        assert not isinstance(registry.get(name), RefusedLocalFileTool), name
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    assert "hello" in registry.get("read_file").run(path="a.txt")


# -- a turn that read content from outside asks first -----------------------------------------


def _assembled(tmp_path: Path, sink: Any, box: SshSandbox, **extra: Any) -> Any:
    from chimera.api.code_api import CodeSeams, assemble_registry
    from chimera.config import Settings
    from chimera.providers.gateway import LLMGateway

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_APPROVAL_MODE="ask", CHIMERA_APPROVAL_WAIT="5")
    _, ledger = assemble_registry(
        CodeSeams(), ws, settings, LLMGateway(), steps=4, surface="api:turn", approval_sink=sink, sandbox=box,
        **extra,
    )
    return settings, ledger


def test_a_clean_turn_runs_without_the_taint_question(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.governance.approval import ApprovalAnnouncer

    fake = _Ssh(monkeypatch, [_Proc(0, b"fine\n", b"")])
    sink = ApprovalAnnouncer()
    asked: list[Any] = []
    sink.emit = asked.append
    box = SshSandbox("web1", "/srv")
    _assembled(tmp_path, sink, box)
    assert box.run("uptime").stdout == "fine\n"
    assert asked == [] and len(fake.calls) == 1


def test_a_tainted_turn_puts_the_command_on_the_owners_card_and_a_no_stops_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.core.task_cards import seed_taint
    from chimera.governance import pending
    from chimera.governance.approval import ApprovalAnnouncer

    fake = _Ssh(monkeypatch, [_Proc(0, b"ran\n", b"")])
    sink = ApprovalAnnouncer()
    asked: list[Any] = []
    answers = iter([True, False])

    def on_screen(question: Any) -> None:
        asked.append(question)
        assert pending.answer(settings.home, question.id, next(answers))

    sink.emit = on_screen
    box = SshSandbox("web1", "/srv")
    settings, ledger = _assembled(tmp_path, sink, box)
    seed_taint(ledger)

    allowed = box.run("cat /etc/hosts")
    assert allowed.exit_code == 0 and len(fake.calls) == 1
    refused = box.run("uname -a")
    assert refused.exit_code == 1 and "needs the owner's approval" in refused.stderr
    assert len(fake.calls) == 1  # the declined command never started
    assert len(asked) == 2
    assert "run_shell: [ssh web1] cat /etc/hosts" in asked[0].action
    assert "web1" in asked[0].reason


def test_with_ignore_permissions_on_a_tainted_ssh_command_shows_no_card_exactly_as_the_local_host_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The owner's decision of 26/10: "Ignore permissions" releases what asks, tainted turns included.

    SSH is the host, so it follows the same rule and is not stricter: no card is shown, the release is
    announced as a notice (not a question), and the command runs.
    """
    from chimera.core.task_cards import seed_taint
    from chimera.governance.approval import ApprovalAnnouncer

    fake = _Ssh(monkeypatch, [_Proc(0, b"ran\n", b"")])
    sink = ApprovalAnnouncer()
    seen: list[Any] = []
    sink.emit = seen.append
    box = SshSandbox("web1", "/srv")
    _, ledger = _assembled(tmp_path, sink, box, bypass_permissions=True)
    seed_taint(ledger)
    assert box.run("uptime").exit_code == 0 and len(fake.calls) == 1
    cards = [e for e in seen if not isinstance(e, dict)]
    assert cards == [], "a card was shown although Ignore permissions is on"
    assert any(isinstance(e, dict) and "aprovado sozinho" in str(e.get("text")) for e in seen)


def test_with_ignore_permissions_off_the_same_tainted_command_does_show_the_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.core.task_cards import seed_taint
    from chimera.governance import pending
    from chimera.governance.approval import ApprovalAnnouncer

    fake = _Ssh(monkeypatch, [_Proc(0, b"ran\n", b"")])
    sink = ApprovalAnnouncer()
    cards: list[Any] = []

    def on_screen(question: Any) -> None:
        cards.append(question)
        assert pending.answer(settings.home, question.id, True)

    sink.emit = on_screen
    box = SshSandbox("web1", "/srv")
    settings, ledger = _assembled(tmp_path, sink, box, bypass_permissions=False)
    seed_taint(ledger)
    assert box.run("uptime").exit_code == 0 and len(cards) == 1 and len(fake.calls) == 1


@pytest.mark.parametrize("where", ["ssh", "local"])
def test_a_refusal_on_the_card_silences_the_turns_remaining_commands_and_the_next_turn_starts_clean(
    where: str,
) -> None:
    """`HostExecTurnLimiter`: one "no" on the command card stops every later `run_shell` of THAT turn.

    The same for SSH and for the local host (both are not isolated). The limiter is built with the
    turn's registry, so the next turn asks again. The taint question above is the ledger's approver,
    not this latch: it asks per command.
    """
    from chimera.sandbox import LocalSandbox
    from chimera.tools.shell import HOST_EXEC_ALREADY_DECLINED, HostExecTurnLimiter, RunShellTool

    box: Any = SshSandbox("web1", "/srv") if where == "ssh" else LocalSandbox()
    asked: list[str] = []

    def turn() -> RunShellTool:
        def no(command: str) -> bool:
            asked.append(command)
            return False

        return RunShellTool(Path.cwd(), box, confirm=HostExecTurnLimiter(no, lambda: True))

    first = turn()
    assert "declined" in first.run(command="echo one")
    assert first.run(command="echo two") == HOST_EXEC_ALREADY_DECLINED
    assert first.run(command="echo three") == HOST_EXEC_ALREADY_DECLINED
    assert asked == ["echo one"]
    second = turn()  # the next turn
    assert "declined" in second.run(command="echo four")
    assert asked == ["echo one", "echo four"]


def test_a_tainted_turn_with_no_screen_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.core.task_cards import seed_taint

    fake = _Ssh(monkeypatch, [_Proc(0, b"ran\n", b"")])
    box = SshSandbox("web1", "/srv")
    _, ledger = _assembled(tmp_path, None, box)
    seed_taint(ledger)
    result = box.run("uptime")
    assert result.exit_code == 1 and "not run" in result.stderr
    assert fake.calls == []


def test_binding_without_a_question_refuses_a_tainted_command_and_never_asks_a_clean_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _Ssh(monkeypatch, [_Proc(0, b"ok", b"")])
    box = SshSandbox("web1", "/srv")
    state = {"tainted": False}
    asked: list[str] = []
    box.bind_taint(lambda: state["tainted"], lambda c: asked.append(c) or True)
    assert box.run("a").exit_code == 0 and asked == []
    state["tainted"] = True
    assert box.run("b").exit_code == 0 and asked == ["b"]
    box.bind_taint(lambda: True, None)
    assert box.run("c").exit_code == 1
    assert len(fake.calls) == 2


# -- through the app --------------------------------------------------------------------------


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ssh_home: Path) -> Any:
    made = _Rig(tmp_path, monkeypatch)
    yield made
    made.screen.close()
    made.bare.close()
    get_settings.cache_clear()


def _allow(rig: Any, host: str = "web1") -> Any:
    return rig.screen.post("/api/code/environments/ssh-hosts", json={"host": host})


def _make_ssh_env(rig: Any, name: str = "prod") -> str:
    assert _allow(rig).status_code == 200
    response = rig.screen.post(
        "/api/code/environments",
        json={"name": name, "kind": "ssh", "params": {"host": "web1", "remote_path": "/srv/app"}},
    )
    assert response.status_code == 200, response.text
    return next(e["id"] for e in response.json()["environments"] if e["name"] == name)


def test_the_owners_screen_lists_allows_and_forgets_hosts(rig: Any) -> None:
    first = rig.screen.get("/api/code/environments/ssh-hosts").json()
    assert first == {"supported": True, "config_hosts": ["web1", "db.internal", "box"], "allowed": []}
    assert _allow(rig).json()["allowed"] == ["web1"]
    assert _allow(rig).json()["allowed"] == ["web1"]  # allowing twice is one entry
    assert _allow(rig, "ghost").status_code == 400
    assert _allow(rig, "*").status_code == 400
    assert rig.screen.delete("/api/code/environments/ssh-hosts/web1").json()["allowed"] == []
    assert rig.screen.delete("/api/code/environments/ssh-hosts/web1").status_code == 404
    assert {k["kind"] for k in rig.screen.get("/api/code/environments").json()["kinds"]} == {"wsl", "ssh"}


def test_the_bridge_a_paired_device_and_a_bare_caller_cannot_make_an_ssh_environment_or_edit_the_list(
    rig: Any,
) -> None:
    from chimera.api.bridge_routes import VIA_BRIDGE_SCOPE_KEY
    from chimera.api.devices import DEVICE_SCOPE_KEY

    env = {"name": "x", "kind": "ssh", "params": {"host": "web1", "remote_path": "/srv"}}
    assert _allow(rig).status_code == 200
    for client in (
        rig.marked({VIA_BRIDGE_SCOPE_KEY: True}, screen=True),
        rig.marked({DEVICE_SCOPE_KEY: "d1"}, screen=True),
        rig.bare,
    ):
        assert client.post("/api/code/environments", json=env).status_code == 403
        assert client.post("/api/code/environments/ssh-hosts", json={"host": "box"}).status_code == 403
        assert client.delete("/api/code/environments/ssh-hosts/web1").status_code == 403
    for client in (
        rig.marked({VIA_BRIDGE_SCOPE_KEY: True}, screen=True),
        rig.marked({DEVICE_SCOPE_KEY: "d1"}, screen=True),
    ):
        assert client.get("/api/code/environments/ssh-hosts").status_code == 403
    assert rig.screen.get("/api/code/environments/ssh-hosts").json()["allowed"] == ["web1"]
    assert rig.screen.get("/api/code/environments").json()["environments"][1:] == []


def test_the_first_turn_fixes_an_ssh_environment_and_the_turn_gets_its_sandbox(rig: Any) -> None:
    env_id = _make_ssh_env(rig)
    first = rig.turn(rig.screen, environment=env_id)
    assert first.status_code == 200, first.text
    sid = rig.sid(first)
    box = rig.sandboxes[-1]
    assert isinstance(box, SshSandbox) and (box.host, box.remote_path) == ("web1", "/srv/app")
    assert rig.stored(sid)["environment"] == {"id": env_id, "name": "prod", "kind": "ssh"}
    assert "web1" not in json.dumps(rig.stored(sid)["environment"]["name"])
    # The conversation file holds no parameter of the host.
    assert "/srv/app" not in json.dumps(rig.stored(sid).get("environment"))
    assert rig.turn(rig.screen, sid).status_code == 200
    assert isinstance(rig.sandboxes[-1], SshSandbox)
    changed = rig.turn(rig.screen, sid, environment="local")
    assert changed.status_code == 409 and "fixed" in changed.json()["detail"]


def test_a_bridge_turn_cannot_name_an_ssh_environment_and_a_removed_host_ends_the_commands(rig: Any) -> None:
    from chimera.api.bridge_routes import VIA_BRIDGE_SCOPE_KEY

    env_id = _make_ssh_env(rig)
    bridge = rig.marked({VIA_BRIDGE_SCOPE_KEY: True}, screen=True)
    assert rig.turn(bridge, environment=env_id).status_code == 403
    sid = rig.sid(rig.turn(rig.screen, environment=env_id))
    assert isinstance(rig.sandboxes[-1], SshSandbox)
    assert rig.turn(bridge, sid).status_code == 200  # the bridge keeps working in the one it has
    assert isinstance(rig.sandboxes[-1], SshSandbox)
    assert rig.screen.delete("/api/code/environments/ssh-hosts/web1").status_code == 200
    stopped = rig.turn(rig.screen, sid)
    assert stopped.status_code == 409 and "allowed" in stopped.json()["detail"]
    listed = rig.screen.get("/api/code/environments").json()["environments"]
    assert next(e for e in listed if e["id"] == env_id)["available"] is False
